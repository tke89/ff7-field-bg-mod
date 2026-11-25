"""Drive FF8 Remastered's window: find it, bring it to the front, press keys (scan codes, so the game's DirectInput sees
them; only sent while the game is in front), and take its picture (PrintWindow, which works while it's covered).
Exclusive fullscreen captures black, so testing uses a window (the game's config.txt, screenmode 2)."""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

EXE = "ffviii.exe"
STEAM_URL = "steam://rungameid/1026680"
SCAN = {"enter": 0x1C, "esc": 0x01, "x": 0x2D, "c": 0x2E,
        "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True)}


def _proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    n = wt.DWORD(260)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return buf.value.rsplit("\\", 1)[-1].lower() if ok else ""


def window():
    """The game's main window handle, or None."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _proc_name(pid.value) == EXE:
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 100:
                    found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return max(found)[1] if found else None


def running():
    return window() is not None


def launch():
    """Start the game through Steam, from explorer (so it outlives this process's job object)."""
    subprocess.Popen(["explorer.exe", STEAM_URL])


def close():
    subprocess.run(["taskkill", "/IM", "FFVIII.exe", "/F"], capture_output=True)


def in_front(hwnd):
    return user32.GetForegroundWindow() == hwnd


def focus(hwnd):
    """Bring the game in front. Windows only lets the process the user last used do that, which the Studio isn't
    once you click anywhere else (its presses then went nowhere and the jump stopped at the load screen,
    2026-11-21): so this thread borrows the foreground window's input queue (AttachThreadInput) for the switch,
    with an Alt tap as before."""
    if in_front(hwnd):
        return True
    me = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.4)
    return in_front(hwnd)


def _client_rect(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def grab(hwnd):
    """The window's picture as a PIL image."""
    from PIL import Image

    w, h = _client_rect(hwnd)
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    buf = ctypes.create_string_buffer(w * h * 4)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _send(scan, up, ext=False):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(0, scan, 0x0008 | (0x0002 if up else 0) | (0x0001 if ext else 0), 0, None)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def press(hwnd, name, hold=0.08):
    """One key press, only while the game is in front (False if it isn't)."""
    if not focus(hwnd):
        return False
    s = SCAN[name]
    scan, ext = s if isinstance(s, tuple) else (s, False)
    _send(scan, False, ext)
    time.sleep(hold)
    _send(scan, True, ext)
    return True


def screen(img):
    """What the game shows, from a few broad features (measured on 2026-11-21's screenshots): "title" (the red
    crest), "slots" with the selected slot (1 or 2), "menu" (another grey menu: the save list, a message), or
    "other" (logos, a field)."""
    import numpy as np

    a = np.asarray(img.resize((192, 108))).astype(float)
    top = a[5:60, 30:162]
    r, g, b = (top[..., k].mean() for k in range(3))
    if r > 80 and r > 1.6 * g:
        return ("title", None)
    # every menu has the grey INFO panel across the top: exactly neutral, about 102-107 (the save list's portraits
    # put colour in the rest of the screen, so only the panel is looked at)
    panel = a[9:15, 40:120]
    pr, pg, pb = (panel[..., k].mean() for k in range(3))
    if abs(pr - pg) < 1.5 and abs(pg - pb) < 1.5 and 90 < pr < 120:
        g1, g2 = a[48:54, 66:76].mean(), a[72:78, 66:76].mean()
        if max(g1, g2) > 90 and min(g1, g2) < 40 and r < 50:
            return ("slots", 1 if g1 > g2 else 2)
        return ("menu", None)
    return ("other", None)
