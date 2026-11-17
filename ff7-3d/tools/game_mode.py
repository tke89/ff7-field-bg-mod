"""Switch the game between Full 3D and 2D (the new backgrounds only).

  game_mode.py backgrounds   2D: the new field backgrounds, flat as in the 2D game: no 3D scenes, no lighting or
                             character shading, none of FFNx-3d's own pages (hidden pages, glow frames). The
                             characters drawn as the plain FFNx 1.24.3 draws them (FFNx-3d's renderer, lighting off)
  game_mode.py 3d            Full 3D: the 3D scenes, lighting and shaders, as before
  game_mode.py status        which one is set up

Both run FFNx-3d, so the Studio can still start the game in a field, take it to another one, re-enter it, flip to the
original backgrounds and take screenshots. What changes is three lines of FFNx.toml: enable_lighting,
enable_external_mesh and override_mod_path (FFNx-3d's own pages in mods/3dtest). Full 3D's lines are kept in
workingdir/_game_mode/full3d.json, so switching back gives exactly what was there. Refuses while the game is running
(FFNx reads its settings when it starts). Run for the mode that's already set, it sets it up again.

For a while (2026-11-16/17) 2D swapped in the plain FFNx 1.24.3 instead, which the Studio can't steer (FFNx-3d then
drew characters wrong with lighting off; fixed in its renderer). The FFNx-3d it set aside in _game_mode/full3d/ is put
back by either switch.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

W = Path(os.environ.get("FF7_WORKINGDIR",  # (another folder, for a test run)
                        r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir"))
ORIGINAL = W / "_backup_ffnx_1.24.3"   # the user's FFNx 1.24.3, from before the newer FFNx (manifest.json)
STORE = W / "_game_mode"
MODE = STORE / "current.txt"
SAVED = STORE / "full3d.json"          # Full 3D's lines of FFNx.toml while 2D is set
OLD = STORE / "full3d"                 # the FFNx-3d set aside when 2D swapped in the plain FFNx
ENGINE = ("AF3DN.P", "AF4DN.P", "FFNx.pdb")
# FFNx.toml's lines (values as written in the file); Full 3D gets back whatever was there before (SAVED)
SETTINGS = {"3d": {"enable_lighting": "true", "enable_external_mesh": "true", "override_mod_path": '"mods/3dtest"'},
            "backgrounds": {"enable_lighting": "false", "enable_external_mesh": "false", "override_mod_path": '""'}}
NAMES = {"3d": "Full 3D (the 3D scenes, lighting and shaders)",
         "backgrounds": "2D (the new backgrounds, flat as in the 2D game)"}


def game_running():
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return any(exe in out for exe in ("ff7_en.exe", "ff7.exe", "ff7_launcher.exe", "7th heaven.exe"))


def current():
    return MODE.read_text().strip() if MODE.exists() else "3d"  # before the first switch: the 3D setup


def get_toml(key):
    """A line's value in FFNx.toml as written (true, "mods/3dtest", ...), None if it isn't there."""
    m = re.search(rf"(?m)^{key}\s*=\s*([^\r\n]*?)\s*$", (W / "FFNx.toml").read_text(encoding="utf-8"))
    return m.group(1) if m else None


def set_toml(values):
    """Those lines of FFNx.toml, nothing else changed (line endings kept)."""
    p = W / "FFNx.toml"
    raw = p.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    for key, value in values.items():
        text, n = re.subn(rf"(?m)^{key}\s*=[^\r\n]*", lambda _: f"{key} = {value}", text)
        if n == 0:  # not in the file: add it
            text = text.rstrip("\r\n") + f"{nl}{key} = {value}{nl}"
    p.write_bytes(text.encode("utf-8"))


def save_toml(fresh):
    """Keep Full 3D's lines of FFNx.toml for switching back. fresh=False (2D already set, nothing kept): the override
    line if it still has a folder, the two switches as Full 3D has them."""
    now = {k: get_toml(k) for k in SETTINGS["3d"]}
    if not fresh:
        over = now["override_mod_path"]
        now = {**SETTINGS["3d"], "override_mod_path": over if over and over != '""' else SETTINGS["3d"]["override_mod_path"]}
    SAVED.write_text(json.dumps({k: v for k, v in now.items() if v is not None}, indent=1))


def move_set(src_root, dst_root, files, shaders=True):
    """Move the engine files (and the shaders folder) from src_root to dst_root."""
    dst_root.mkdir(parents=True, exist_ok=True)
    for name in files:
        s = src_root / name
        if s.exists():
            d = dst_root / name
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists():
                d.unlink()
            shutil.move(str(s), str(d))
    if shaders and (src_root / "shaders").exists():
        if (dst_root / "shaders").exists():
            shutil.rmtree(dst_root / "shaders")
        shutil.move(str(src_root / "shaders"), str(dst_root / "shaders"))


def restore_engine():
    """The plain-FFNx swap undone: FFNx-3d, its shaders and the newer FFNx's files back exactly as they were (the
    plain FFNx's copies go; its backup stays), and Full 3D's lines it kept moved to SAVED."""
    if (OLD / "toml.json").exists() and not SAVED.exists():
        shutil.move(str(OLD / "toml.json"), str(SAVED))
    if not (OLD / "AF3DN.P").exists():
        return
    for name in ENGINE:
        if (W / name).exists():
            (W / name).unlink()
    if (W / "shaders").exists():
        shutil.rmtree(W / "shaders")
    move_set(OLD, W, ENGINE)
    if (OLD / "added").exists():
        added = json.loads((ORIGINAL / "manifest.json").read_text())["added"]
        move_set(OLD / "added", W, added, shaders=False)
    shutil.rmtree(OLD)
    print("FFNx-3d is back in place of the plain FFNx")


def to_backgrounds(have):
    restore_engine()
    if not SAVED.exists():
        save_toml(fresh=have == "3d")
    set_toml(SETTINGS["backgrounds"])


def to_3d(have):
    restore_engine()
    values = {**SETTINGS["3d"], **(json.loads(SAVED.read_text()) if SAVED.exists() else {})}
    set_toml(values)
    SAVED.unlink(missing_ok=True)


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "status"
    have = current()
    if want == "status":
        print("Set up now:", NAMES[have])
        return
    if want not in NAMES:
        sys.exit("use: game_mode.py backgrounds | 3d | status")
    if want == have == "3d" and not (OLD / "AF3DN.P").exists():
        print("Already set up:", NAMES[have])
        return
    if game_running():
        sys.exit("Close the game first (and 7th Heaven, if it's open), then run this again.")
    STORE.mkdir(exist_ok=True)
    (to_backgrounds if want == "backgrounds" else to_3d)(have)
    MODE.write_text(want)
    print("Set up:" if want == have else "Switched to:", NAMES[want])


if __name__ == "__main__":
    main()
