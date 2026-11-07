"""Talk to a running FFNx-3d (src/ffnx3d_live.cpp): commands through workingdir/ffnx3d/cmd.txt, results from log.txt,
where the game is from status.txt.

  live.py status
  live.py jump <field name or id> [triangle]     waits until the field has loaded
  live.py shot <out.png>                          waits until the file is written
  live.py reload
"""
import struct
import sys
import time
from pathlib import Path

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
ROOT = GAME / "ffnx3d"


def maplist():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the Studio repo (ff7bg)
    from ff7bg.lgp import LgpArchive
    with LgpArchive(str(GAME / "data" / "field" / "flevel.lgp")) as lgp:
        d = lgp.read("maplist")
    n = struct.unpack_from("<H", d, 0)[0]
    return [d[2 + 32 * i:2 + 32 * (i + 1)].split(b"\0")[0].decode() for i in range(n)]


def status():
    try:
        return dict(line.split(" ", 1) for line in (ROOT / "status.txt").read_text().splitlines() if " " in line)
    except FileNotFoundError:
        return {}


def send(*lines, timeout=10):
    """Queue commands, wait for the game to pick them up, return the new log lines."""
    log = ROOT / "log.txt"
    before = log.stat().st_size if log.exists() else 0
    with open(ROOT / "cmd.txt", "a") as f:
        f.write("\n".join(lines) + "\n")
    end = time.time() + timeout
    while (ROOT / "cmd.txt").exists():
        if time.time() > end:
            raise TimeoutError("the game didn't pick up the command (not running, or not FFNx-3d?)")
        time.sleep(0.1)
    with open(log, "rb") as f:
        f.seek(before)
        return f.read().decode(errors="replace").splitlines()


def jump(field, triangle=0, timeout=30):
    fid = int(field) if str(field).isdigit() else maplist().index(field)
    name = maplist()[fid]
    print(*send(f"jump {fid} {triangle}"), sep="\n")
    end = time.time() + timeout
    while status().get("field") != name:
        if time.time() > end:
            raise TimeoutError(f"still in {status().get('field')}")
        time.sleep(0.2)
    time.sleep(1.5)  # fade in
    return name


def shot(path, timeout=10):
    path = Path(path).resolve()
    path.unlink(missing_ok=True)
    send(f"shot {path}")
    end = time.time() + timeout
    while not path.exists() or path.stat().st_size == 0:
        if time.time() > end:
            raise TimeoutError("no screenshot written")
        time.sleep(0.1)
    time.sleep(0.2)
    return path


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "status":
        print(status())
    elif cmd == "jump":
        print(jump(args[0], int(args[1]) if len(args) > 1 else 0))
    elif cmd == "shot":
        print(shot(args[0]))
    else:
        print(*send(" ".join([cmd] + args)), sep="\n")
