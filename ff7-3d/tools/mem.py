"""Watch ff7_en.exe's memory (a 32-bit process: it dies when its address space runs out) across repeated live commands,
to find what leaks.

  mem.py reload [n]              n x `reload` on the current field
  mem.py jump <a> <b> [n]        n round trips between two fields
  mem.py swap <field> [n]        n x (switch the field's build variant, re-enter, reload), as ab.py does
  mem.py touch <path> [n]        n x touch a watched file (scene mesh folder, lighting/, lighting/ibl, glow.toml)
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import live  # noqa: E402


def sample():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "$p = Get-Process ff7_en; \"$($p.VirtualMemorySize64) $($p.PrivateMemorySize64)\""],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) / 2**20, int(out[1]) / 2**20


def show(label):
    v, p = sample()
    print(f"{label:28s} virtual {v:7.0f} MB   private {p:7.0f} MB", flush=True)


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    show("start")
    if cmd == "reload":
        for i in range(int(args[0]) if args else 10):
            live.send("reload")
            time.sleep(2.0)
            show(f"reload {i + 1}")
    elif cmd == "jump":
        a, b = args[0], args[1]
        for i in range(int(args[2]) if len(args) > 2 else 5):
            for name in (b, a):
                live.jump(name)
                time.sleep(1.5)
                show(f"{i + 1}: {name}")
    elif cmd == "touch":
        for i in range(int(args[1]) if len(args) > 1 else 5):
            os.utime(args[0])
            time.sleep(2.5)
            show(f"touch {i + 1}")
    elif cmd == "swap":
        from ff3d import build
        name = args[0]
        for i in range(int(args[1]) if len(args) > 1 else 5):
            for v in (None, "lock", "game"):
                build.use(name, v)
                live.jump(name, 0)
                time.sleep(2.0)
                live.send("reload")
                time.sleep(2.5)
                show(f"{i + 1}: {name} {v or 'base'}")


if __name__ == "__main__":
    main()
