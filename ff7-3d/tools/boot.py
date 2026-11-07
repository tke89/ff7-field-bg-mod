"""(Re)start the game and get to a field fast: launch ff7_en.exe, press confirm only until FFNx-3d's status file says
we're on a field (the save in slot 1 starts at nmkin_4, which jumps straight on to eals_1), then optionally jump.

  boot.py [field name or id] [triangle]
"""
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["CTL_EXE"] = "ff7_en.exe"
import ctl  # noqa: E402
import live  # noqa: E402

GAME = live.GAME


def close_game():
    h = ctl.window()
    if h:
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        for _ in range(30):
            if not ctl.window():
                break
            time.sleep(1)
    subprocess.run(["taskkill", "/im", "ff7_en.exe", "/f"], capture_output=True)
    time.sleep(1)


def boot(field=None, triangle=0):
    t0 = time.time()
    close_game()
    (live.ROOT / "status.txt").unlink(missing_ok=True)
    subprocess.Popen([str(GAME / "ff7_en.exe")], cwd=str(GAME))
    while not ctl.window():
        time.sleep(0.3)
    last_key = 0
    while True:
        s = live.status()
        if s.get("mode") == "0" and s.get("field") and s.get("field") != "nmkin_4":
            break
        if time.time() - last_key > 1.2 and s.get("mode") != "0":
            try:
                ctl.key("x")
            except SystemExit:
                pass
            last_key = time.time()
        if time.time() - t0 > 120:
            sys.exit(f"didn't reach a field: {s}")
        time.sleep(0.2)
    print(f"in {live.status().get('field')} after {time.time() - t0:.0f} s")
    if field:
        print(live.jump(field, triangle), f"after {time.time() - t0:.0f} s")


if __name__ == "__main__":
    boot(sys.argv[1] if len(sys.argv) > 1 else None, int(sys.argv[2]) if len(sys.argv) > 2 else 0)
