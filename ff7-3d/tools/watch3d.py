"""Follow the Studio's 3D builds and capture each finished scene in the game for review: the 3D scene, the AI 2D image
and the original from the same spot, plus where the 3D scene's structure differs from the AI 2D image
-> report/review/<field>.jpg (sheet) and report/review/<field>/ (the shots), one line per field in log.jsonl.

  watch3d.py [--batch N] [--wait S] [field ...]

Exits once N fields are captured or failed (default 3), or after S seconds with nothing new, so whoever runs it can
look at them; the next run carries on (report/review/seen.json). With fields given, captures just those. Finished
builds are the fields whose report.json is newer than the rebuild's start, failed ones come from the Studio's queue
(GET only). Never drives the game while someone plays it (gamelock.playing).
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
os.environ.setdefault("CTL_EXE", "ff7_en.exe")
import ctl  # noqa: E402
import gamelock  # noqa: E402
import live  # noqa: E402
import winshot  # noqa: E402
from ff3d import common  # noqa: E402

STUDIO = "http://127.0.0.1:8765"
OUT = HERE / "report" / "review"
SEEN = OUT / "seen.json"
# the run being reviewed: {"since": its start (epoch), "first_job": the Studio's id of its first job} (the first
# run: 2026-11-01 20:55:56 from job 4; the second, with the fixes: run.json)
RUN = json.loads((OUT / "run.json").read_text()) if (OUT / "run.json").exists() else {"since": 1793566556, "first_job": 4}
FIRST_JOB, SINCE = RUN["first_job"], RUN["since"]


def queue():
    with urllib.request.urlopen(STUDIO + "/api/queue", timeout=10) as r:
        return json.load(r)


def memory():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "$p = Get-Process ff7_en -ErrorAction SilentlyContinue; if ($p) { \"$($p.VirtualMemorySize64) $($p.PrivateMemorySize64)\" }"],
                         capture_output=True, text=True).stdout.split()
    return [round(int(v) / 2**20) for v in out] if out else None


class MemoryLog:
    """The game's memory once a second while a capture runs -> report/review/memory.jsonl (F24: the two climbs on
    2026-11-03 happened during captures, and nothing recorded what ran). Each line: time, the field being captured,
    the game's field and view, its address space in use / committed / largest free block (MB, VirtualQueryEx: the
    32-bit game's 4 GB) and FFNx-3d's `textures` status (bgfx handles in use of 4096, the glow-frame cache)."""
    PATH = OUT / "memory.jsonl"

    def __init__(self, name):
        import threading
        self.name, self.stop_ = name, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def address_space():
        import ctypes.wintypes as wt

        class MBI(ctypes.Structure):
            _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                        ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t),
                        ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]
        k32 = ctypes.windll.kernel32
        pid = wt.DWORD()
        hwnd = ctl.window()
        if not hwnd:
            return None
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x0400, False, pid.value)  # PROCESS_QUERY_INFORMATION
        if not h:
            return None
        try:
            addr, used, commit, big, mbi = 0x10000, 0, 0, 0, MBI()
            while addr < 0xFFFF0000 and k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                if mbi.State == 0x10000:  # MEM_FREE
                    big = max(big, mbi.RegionSize)
                else:
                    used += mbi.RegionSize
                    commit += mbi.RegionSize if mbi.State == 0x1000 else 0
                addr = (mbi.BaseAddress or 0) + mbi.RegionSize
            return {"used_mb": used >> 20, "commit_mb": commit >> 20, "largest_free_mb": big >> 20}
        finally:
            k32.CloseHandle(h)

    def run(self):
        while not self.stop_.wait(1.0):
            try:
                st = live.status()
                line = {"t": time.strftime("%H:%M:%S"), "capture": self.name, "field": st.get("field"),
                        "view": st.get("view"), **(self.address_space() or {}), "textures": st.get("textures")}
            except Exception as e:  # never the capture's problem
                line = {"t": time.strftime("%H:%M:%S"), "capture": self.name, "error": f"{type(e).__name__}: {e}"}
            try:
                with open(self.PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line) + "\n")
            except OSError:
                pass

    def stop(self):
        self.stop_.set()
        self.thread.join(timeout=3)



# fields whose entry starts a battle: the game then waits for keys we can't send (clsin2_1, the coliseum)
BATTLE_ON_ENTRY = {"clsin2_1", "clsin2_2", "clsin2_3"}

# Sharing the game with another job (the fix agent): it creates LOCK, then waits for BUSY to go before driving the
# game, and deletes LOCK when done. We take the game only with no LOCK, holding BUSY for a whole capture.
LOCK = OUT / "game.lock"
BUSY = OUT / "watcher.busy"
MEMORY_LIMIT_MB = 2600  # the game's virtual size above which a capture restarts it first (fine fields run at 1.6-2.1 GB)


def free(mine=False):
    """The game runs, sits on a field, no other job holds it (LOCK; `mine`: we hold it) and nobody is playing it
    (gamelock.playing: in front and the PC used in the last few minutes)."""
    if LOCK.exists() and not mine:
        return "locked by another job"
    hwnd = ctl.window()
    if not hwnd:
        return "not running"
    if hung():
        return "hung"
    if gamelock.playing():
        return "in front"
    if not live.status().get("field"):
        return "not on a field"
    return None


def hung():
    """The game's window has stopped answering, or its frame counter hasn't moved for 20 s (2026-11-02: it froze in
    las4_3 on a view switch, and every capture after failed; a screenshot of a hung window blocks too)."""
    hwnd = ctl.window()
    if hwnd and ctypes.windll.user32.IsHungAppWindow(hwnd):
        return True
    frame = live.status().get("frame")
    now = time.time()
    if frame != _frame[0]:
        _frame[:] = [frame, now]
        return False
    return now - _frame[1] > 20


_frame = [None, 0.0]


def restart_game():
    """Close and start the game again (the user: "restart as much as you need"), back on a field. boot.py has the game
    in front for the title screen: whatever window was in front gets it back (no ShowWindow: it un-maximizes)."""
    import boot
    user32 = ctypes.windll.user32
    before = user32.GetForegroundWindow()
    # FFNx.log starts afresh with the game: keep the stopped game's last lines (a crash's exception and stack)
    log = boot.GAME / "FFNx.log"
    if log.exists():
        keep = OUT / "crash_logs"
        keep.mkdir(exist_ok=True)
        with open(log, "rb") as src:
            src.seek(max(0, log.stat().st_size - 400_000))
            (keep / f"FFNx_{time.strftime('%Y%m%d_%H%M%S')}.log").write_bytes(src.read())
    game_before = ctl.window()
    boot.boot()
    hand_back(before if before != game_before else None)
    print(json.dumps({"restarted": time.strftime("%H:%M:%S")}), flush=True)


def _user_window():
    """A window to give the focus to when the one that had it is gone: the Claude app's, else any other visible,
    titled top-level window that isn't the game's."""
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    game = ctl.window()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(h, _):
        if h != game and user32.IsWindowVisible(h) and not user32.IsIconic(h):
            t = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, t, 256)
            if t.value and t.value != "Program Manager":
                found.append((t.value, h))
        return True
    user32.EnumWindows(each, 0)
    for title, h in found:
        if title == "Claude":
            return h
    return found[0][1] if found else None


def hand_back(before):
    """Give the focus back after boot.py had the game in front. The Alt tap lets a background process switch windows;
    if the switch then fails, that tap has put the game in Windows' menu mode, its loop stopped until it loses the
    focus: twice on 2026-11-07, when the window in front before the restart was the old game's own (gone with it) and
    the game sat frozen in front. So: a live window that isn't the game's (_user_window when `before` is gone), and
    checked."""
    user32 = ctypes.windll.user32
    game = ctl.window()
    target = before if before and user32.IsWindow(before) and before != game else _user_window()
    for _ in range(3):
        if not target or user32.GetForegroundWindow() != game:
            return
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(target)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == game:
            target = _user_window()


def wait_free(limit=3600, mine=False):
    """Until the game is ours to drive. Stuck off a field for a minute (a battle on entry, a game over) or not
    running: restarted."""
    end = time.time() + limit
    off_since = None
    while True:
        why = free(mine)
        if why is None:
            return
        if why in ("not running", "not on a field", "hung"):
            off_since = off_since or time.time()
            if why in ("not running", "hung") or time.time() - off_since > 60:
                restart_game()
                off_since = None
                continue
        else:
            off_since = None
        if time.time() > end:
            raise RuntimeError(f"the game is {why}")
        time.sleep(2)


def grab(path):
    """The game's 4:3 picture, once two shots half a second apart agree and it isn't black (the fade is over: some
    fields fade in slowly, blin60_2's lift), or after 10 s."""
    prev = None
    end = time.time() + 10
    while True:
        winshot.shot(str(path))
        im = np.asarray(Image.open(path).convert("RGB"))
        h, w = im.shape[:2]
        pw = h * 4 // 3
        im = im[:, (w - pw) // 2:(w - pw) // 2 + pw]
        settled = prev is not None and np.abs(im.astype(int) - prev).mean() < 1.5 and im.max(2).mean() > 12
        if settled or time.time() > end:
            Image.fromarray(im).save(path, quality=95)
            return im
        prev = im.astype(int)
        time.sleep(0.5)


def switch(view, name):
    live.send(f"view {view}")
    time.sleep(1.0)
    end = time.time() + 20
    while live.status().get("view") != view or live.status().get("field") != name:
        if time.time() > end:
            raise RuntimeError(f"{view}: the game is in {live.status().get('field')}, view {live.status().get('view')}")
        time.sleep(0.3)
    time.sleep(2.0)


def mismatch(a, b, cell=24):
    """Per cell, how little the 3D shot's edges follow the AI 2D shot's (0 = same structure, 1 = unrelated), where the
    AI 2D shot has structure to follow; plus the share of the picture gone black or blown out that wasn't."""
    ga, gb = (cv2.GaussianBlur(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 1.0) for x in (a, b))
    ea, eb = (np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)) for g in (ga, gb))
    rows, cols = a.shape[0] // cell, a.shape[1] // cell
    heat = np.zeros((rows, cols), np.float32)
    for r in range(rows):
        for c in range(cols):
            pa = ea[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].ravel()
            pb = eb[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].ravel()
            if pb.std() < 4:
                continue
            heat[r, c] = 1 - max(0.0, float(np.corrcoef(pa, pb)[0, 1]) if pa.std() > 1e-3 else 0.0)
    dark = (a.max(2) < 12) & (b.max(2) > 40)
    blown = (a.min(2) > 245) & (b.min(2) < 230)
    return heat, float(dark.mean()), float(blown.mean())


def sheet(name, shots, heat, info):
    w, h = 560, 420
    panels = []
    for label, im in (("3D scene", shots["3d"]), ("AI 2D", shots["ai2d"]), ("original", shots["original"])):
        panels.append((label, cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)))
    over = panels[0][1].astype(np.float32) * 0.6
    hm = cv2.resize(heat, (w, h), interpolation=cv2.INTER_NEAREST)
    over[..., 0] += 255 * 0.4 * np.clip((hm - 0.35) / 0.4, 0, 1)
    panels.append(("3D edges unlike AI 2D (red)", np.clip(over, 0, 255).astype(np.uint8)))
    top = 28
    out = Image.new("RGB", (2 * w, 2 * h + top), (24, 24, 24))
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    d.text((8, 6), f"{name}   " + "   ".join(f"{k} {v}" for k, v in info.items()), fill=(240, 240, 240), font=font)
    for i, (label, im) in enumerate(panels):
        x, y = (i % 2) * w, top + (i // 2) * h
        out.paste(Image.fromarray(im), (x, y))
        d.rectangle((x, y, x + len(label) * 8 + 10, y + 20), fill=(0, 0, 0))
        d.text((x + 5, y + 2), label, fill=(255, 255, 0), font=font)
    path = OUT / f"{name}.jpg"
    out.save(path, quality=90)
    return path


def take_game():
    while True:
        wait_free()
        BUSY.write_text(str(os.getpid()))
        if not LOCK.exists():
            return
        BUSY.unlink(missing_ok=True)  # the other job got there first
        time.sleep(2)


def capture(name):
    take_game()
    memlog = MemoryLog(name)
    try:
        return capture_held(name)
    finally:
        memlog.stop()
        BUSY.unlink(missing_ok=True)


def capture_held(name):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    fid, tri = common.spot(name)
    wait_free(mine=True)
    # the game is a 32-bit process: near its 4 GB of address space its textures stop loading and scenes draw untextured
    # (2026-11-03: 1.9 -> 3.9 GB over four fields, ancnt3-anfrst_2 shot flat grey, then it hung): start it afresh first
    mem = memory()
    if mem and mem[0] > MEMORY_LIMIT_MB:
        print(json.dumps({"memory_mb": mem, "restart": "address space nearly full"}), flush=True)
        restart_game()
        wait_free(mine=True)
    before = live.status().get("view", "3d")
    if before != "3d":
        switch("3d", live.status().get("field"))
    origin = live.status().get("field")
    try:
        live.jump(name, tri)
    except TimeoutError:
        # stopped altogether (FFNx's "Game crashed" dialog: the window still answers, the frame counter doesn't
        # move; 2026-11-03 12:23, chrin_1a): start it again rather than retrying into it
        f0 = live.status().get("frame")
        time.sleep(3)
        if live.status().get("frame") == f0:
            restart_game()
            raise RuntimeError("the game is stopped (crashed or hung): restarted, try again")
        if hung() or live.status().get("field") == origin:  # the game never left: hung, or the jump was refused
            raise RuntimeError("the game is hung or didn't jump (try again)")
        # its entry event moves the game straight on (blin60_1 -> blin60_2). Events that hold the player don't stop
        # jumps since FFNx-3d's 2026-11-01 fix, so this isn't retried
        raise RuntimeError(f"entering it doesn't stay (now in {live.status().get('field')}): can't be looked at")
    time.sleep(2.5)
    shots = {}
    try:
        shots["3d"] = grab(folder / "3d.jpg")
        if live.status().get("field") != name:  # its entry event moved the game on after it arrived (blin67_4)
            raise RuntimeError(f"entering it doesn't stay (now in {live.status().get('field')}): can't be looked at")
        for view in ("ai2d", "original"):
            wait_free(mine=True)
            try:
                switch(view, name)
                shots[view] = grab(folder / f"{view}.jpg")
            except RuntimeError:  # an event on entry holds the field: the rest another time
                shots[view] = np.zeros_like(shots["3d"])
        # a view switch enters the field again, and that entry can set another camera than the jump's (nvmin1_1:
        # 45 px apart): the 3D shot compared is one after a switch too, the jump's kept if that fails
        wait_free(mine=True)
        try:
            switch("3d", name)
            shots["3d"] = grab(folder / "3d.jpg")
        except RuntimeError:
            Image.fromarray(shots["3d"]).save(folder / "3d.jpg")
    finally:
        if live.status().get("view") != "3d":
            live.send("view 3d")
            time.sleep(3)
    heat, dark, blown = mismatch(shots["3d"], shots["ai2d"])
    np.save(folder / "heat.npy", heat)
    rep = common.WORK / name / "report.json"
    r = json.loads(rep.read_text()) if rep.exists() else {}
    info = {"tri": tri, "unlike": f"{(heat > 0.5).mean():.0%}", "black": f"{dark:.1%}", "blown": f"{blown:.1%}",
            "fit": f"{r.get('fit', {}).get('median_err', float('nan')):.1%}", "cracks": f"{r.get('cracks', float('nan')):.1%}",
            "build": f"{r.get('seconds', 0):.0f}s"}
    return sheet(name, shots, heat, info), info


def main():
    args = sys.argv[1:]
    batch = int(args[args.index("--batch") + 1]) if "--batch" in args else 3
    wait = float(args[args.index("--wait") + 1]) if "--wait" in args else 1800
    names = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or not args[i - 1].startswith("--"))]
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    todo = [{"id": None, "name": n, "ok": True, "message": ""} for n in names]
    done, idle_since = 0, time.time()
    while done < batch:
        if not names:
            q = queue()
            report = lambda n: common.WORK / n / "report.json"
            # a failure built again since (crcin_1: cancelled, then queued again) is no failure any more
            failed = [j for j in q["recent"] if j["kind"] == "3d" and j["id"] >= FIRST_JOB and not j["ok"]
                      and j["name"] not in seen
                      and not (report(j["name"]).exists() and report(j["name"]).stat().st_mtime > j.get("finished", 0))]
            built = sorted((r.stat().st_mtime, r.parent.name) for r in common.WORK.glob("*/report.json")
                           if r.stat().st_mtime > SINCE and r.parent.name not in seen)
            current = (q.get("current") or {}).get("name")
            todo = failed + [{"id": None, "name": n, "ok": True, "message": ""} for _, n in built if n != current]
            if not todo:
                if time.time() - idle_since > wait:
                    break
                time.sleep(5)
                continue
        elif not todo:
            break
        job = todo.pop(0)
        line = {"field": job["name"], "job": job["id"], "time": time.strftime("%H:%M:%S")}
        if job["name"].startswith("blackbg"):
            line.update(result="skipped", message="black background")
        elif job["name"] in BATTLE_ON_ENTRY:
            line.update(result="skipped", message="entering it starts a battle (needs keys to get out)")
        elif not job["ok"]:
            err = common.WORK / job["name"] / "error.txt"
            line.update(result="build failed", message=job.get("message", ""),
                        error=err.read_text(errors="replace").strip().splitlines()[-1] if err.exists() else "")
        else:
            try:
                path, info = capture(job["name"])
                line.update(result="captured", sheet=str(path), **info)
            except Exception as e:
                line.update(result="not captured", message=f"{type(e).__name__}: {e}")
            line["memory_mb"] = memory()
        print(json.dumps(line), flush=True)
        if line["result"] == "not captured" and "the game is" in line.get("message", ""):
            # try it again next time, up to 3 times (a field the game never jumps to mustn't hold the watcher)
            tries_p = OUT / "retries.json"
            tries = json.loads(tries_p.read_text()) if tries_p.exists() else {}
            tries[job["name"]] = tries.get(job["name"], 0) + 1
            tries_p.write_text(json.dumps(tries))
            if tries[job["name"]] < 3:
                break
        with open(OUT / "log.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")
        if not names:
            seen.add(job["name"])
            SEEN.write_text(json.dumps(sorted(seen)))
        done += 1
        idle_since = time.time()


if __name__ == "__main__":
    main()
