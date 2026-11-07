"""Drive FF7 for the capture: find its window, screenshot it, press keys (only while it's in front).

  ctl.py launch | shot <file> | key <name> [times] | state | quit
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

from PIL import ImageGrab

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.SetProcessDPIAware()
EXE = os.environ.get("CTL_EXE", "FFVII.exe").lower()
SCAN = {"enter": 0x1C, "esc": 0x01, "up": 0xC8, "down": 0xD0, "left": 0xCB, "right": 0xCD, "space": 0x39,
        "ctrl": 0x1D, "x": 0x2D, "z": 0x2C, "a": 0x1E, "s": 0x1F, "d": 0x20, "w": 0x11, "c": 0x2E, "v": 0x2F}
EXTENDED = {"up", "down", "left", "right"}


def _proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    n = wt.DWORD(512)
    kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower()


def window():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _proc_name(pid.value) == EXE:
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 200:
                    found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def front(hwnd):
    if user32.GetForegroundWindow() != hwnd:
        user32.ShowWindow(hwnd, 9)
        # Windows only lets a process take the foreground in some cases: an Alt tap allows it
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _send(scan, up, ext):
    flags = 0x0008 | (0x0002 if up else 0) | (0x0001 if ext else 0)  # SCANCODE, KEYUP, EXTENDED
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(0, scan, flags, 0, None)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def key(name, times=1, hold=0.12):
    hwnd = window()
    if not hwnd:
        sys.exit("game window not found")
    for _ in range(times):
        if not front(hwnd):
            sys.exit("game window isn't in front: not sending keys")
        _send(SCAN[name], False, name in EXTENDED)
        time.sleep(hold)
        _send(SCAN[name], True, name in EXTENDED)
        time.sleep(0.35)


def click(x, y):
    """Left-click at (x, y) inside the window (window coordinates, as in shot)."""
    hwnd = window()
    if not hwnd or not front(hwnd):
        sys.exit("window not found or not in front: not clicking")
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    user32.SetCursorPos(r.left + x, r.top + y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def shot(path):
    hwnd = window()
    if not hwnd:
        sys.exit("game window not found")
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)
    print(path, r.right - r.left, r.bottom - r.top, "front" if user32.GetForegroundWindow() == hwnd else "behind")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "launch":
        os.startfile("steam://rungameid/3837340")
    elif cmd == "shot":
        shot(sys.argv[2])
    elif cmd == "key":
        key(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif cmd == "click":
        click(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "state":
        print("window", window())
