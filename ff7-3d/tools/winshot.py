"""Capture the game window even when it's behind other windows (PrintWindow with PW_RENDERFULLCONTENT)."""
import ctypes, ctypes.wintypes as wt, os, sys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("CTL_EXE", "ff7_en.exe")
import ctl

def shot(path):
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = ctl.window()
    r = wt.RECT(); u.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    hdc = u.GetDC(hwnd); mdc = g.CreateCompatibleDC(hdc); bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 3)  # client area + full content
    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("a", wt.LONG), ("b", wt.LONG), ("c", wt.DWORD), ("d", wt.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd, hdc)
    print(path, w, h)

if __name__ == "__main__":
    shot(sys.argv[1])
