"""The review's game lock, for jobs that drive the game besides watch3d.py (the fix agents, A/B tests): exclusive
(created atomically, with an owner tag), the watcher's capture in progress (watcher.busy) waited out, and never while
someone plays the game (`playing`).

    with gamelock.held("main: char light A/B"):
        ...drive the game...
"""
import contextlib
import ctypes
import ctypes.wintypes as wt
import os
import time
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "report" / "review"
LOCK, BUSY = R / "game.lock", R / "watcher.busy"
os.environ.setdefault("CTL_EXE", "ff7_en.exe")  # ctl finds the game's window by its exe
PLAYING_IDLE = 180  # the game in front counts as someone playing it unless the PC has been idle this many seconds


def idle():
    """Seconds since the last keyboard or mouse input anywhere."""
    class Last(ctypes.Structure):
        _fields_ = [("size", wt.UINT), ("time", wt.DWORD)]
    info = Last(ctypes.sizeof(Last), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.time) / 1000


def playing():
    """The game's window is in front and the PC was used in the last PLAYING_IDLE seconds (a game left in front by
    someone who walked away doesn't stop the work)."""
    import ctl
    hwnd = ctl.window()
    return bool(hwnd) and ctypes.windll.user32.GetForegroundWindow() == hwnd and idle() < PLAYING_IDLE


@contextlib.contextmanager
def held(tag):
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(2)
    os.write(fd, tag.encode())
    os.close(fd)
    try:
        while BUSY.exists() or playing():
            time.sleep(1)
        yield
    finally:
        try:
            if LOCK.read_text() == tag:
                LOCK.unlink()
        except FileNotFoundError:
            pass
