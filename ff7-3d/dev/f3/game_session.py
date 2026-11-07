"""f3-agent: before/after shots of my fields in game, the look re-applied with the dev code in between.

    python game_session.py blin66_2:276 church:86 ...      (field:triangle)
    python game_session.py --ai2d-timing blin60_2:10        (AI 2D shots right after entering)
"""
import os
import sys
import time
from pathlib import Path

os.environ["CTL_EXE"] = "ff7_en.exe"
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d\tools")  # the live tools: the real game lock
import gamelock  # noqa: E402
import live  # noqa: E402
import winshot  # noqa: E402
from PIL import Image  # noqa: E402

DEV = Path(r"C:\Users\you\Desktop\ff7-3d-dev-f3")
sys.path.insert(0, str(DEV))
from ff3d import build, common  # noqa: E402

OUT = DEV / "evidence" / "game"
OUT.mkdir(parents=True, exist_ok=True)


def shot(path):
    full = path.with_suffix(".full.png")
    winshot.shot(str(full))
    Image.open(full).crop((160, 0, 1120, 720)).save(path)
    full.unlink()
    return path


def view(v):
    live.send(f"view {v}")
    end = time.time() + 15
    while live.status().get("view") != v:
        if time.time() > end:
            raise RuntimeError(f"view {v}: still {live.status().get('view')}")
        time.sleep(0.3)
    time.sleep(2.0)


def enter(name, tri):
    if live.status().get("view") != "3d":
        view("3d")
    fid = common.maplist().index(name)
    live.jump(fid, tri)
    time.sleep(2.5)
    if live.status().get("field") != name:
        raise RuntimeError(f"{name}: the game is in {live.status().get('field')}")


def before_after(name, tri, apply=True):
    with gamelock.held(f"f3-agent: {name} before/after"):
        enter(name, tri)
        shot(OUT / f"{name}_3d_before.png")
        if apply:
            t = time.time()
            f = common.Field(name)
            build.write_look(f, (f.work / "look.txt").read_text().strip())
            print(name, f"look applied in {time.time() - t:.0f} s", flush=True)
            time.sleep(4)
        if live.status().get("field") != name:
            raise RuntimeError(f"{name}: left for {live.status().get('field')}")
        shot(OUT / f"{name}_3d_after.png")
        view("ai2d")
        shot(OUT / f"{name}_ai2d.png")
        view("original")
        shot(OUT / f"{name}_original.png")
        view("3d")


def ai2d_timing(name, tri):
    with gamelock.held(f"f3-agent: {name} AI 2D on entry"):
        if live.status().get("view") != "3d":
            view("3d")
        view("ai2d")
        fid = common.maplist().index(name)
        live.send(f"jump {fid} {tri}")
        end = time.time() + 30
        while live.status().get("field") != name:
            if time.time() > end:
                raise RuntimeError("jump timed out")
            time.sleep(0.05)
        t0 = time.time()
        for k, wait in enumerate((0.3, 1.0, 2.0, 4.0, 8.0, 12.0, 16.0, 22.0)):
            time.sleep(max(0.0, t0 + wait - time.time()))
            shot(OUT / f"{name}_ai2d_t{wait:.1f}.png")
        view("3d")


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = "ba"
    if args and args[0].startswith("--"):
        mode, args = args[0], args[1:]
    for a in args:
        name, tri = a.split(":")
        try:
            if mode == "--ai2d-timing":
                ai2d_timing(name, int(tri))
            elif mode == "--shots":
                before_after(name, int(tri), apply=False)
            else:
                before_after(name, int(tri))
        except Exception as e:
            print(f"{name}: FAILED {e}", flush=True)
    print("status", live.status(), flush=True)
