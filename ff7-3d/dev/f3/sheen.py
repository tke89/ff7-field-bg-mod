import sys, json, urllib.request
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, surface
from ff3d.build import marigold_normals
built = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/3d/built"))
for n in sys.argv[1].split(","):
    if not built.get(n): continue
    f = common.Field(n)
    r = np.asarray(Image.open(f.render).convert("RGB")).astype(np.float32) / 255
    floor = np.zeros((f.RH, f.RW), np.uint8)
    s, z = f.project(f.tris.reshape(-1, 3))
    tri = (((s - [f.mx, f.my]) * f.scale).reshape(-1, 3, 2)).round().astype(np.int32)
    cv2.fillPoly(floor, list(tri[(z.reshape(-1, 3) > 1).all(1)]), 1)
    up = marigold_normals(f, (f.RW, f.RH))[..., 2] if False else cv2.resize(marigold_normals(f, (f.RW // 4, f.RH // 4))[..., 2], (f.RW, f.RH))
    fl = (cv2.erode(floor, np.ones((15, 15), np.uint8)) > 0) & (up > 0.75)
    L = r @ surface.LUMA
    edges = cv2.dilate(cv2.Canny((L * 255).astype(np.uint8), 40, 120), np.ones((9, 9), np.uint8)) > 0
    sat = (r.max(-1) - r.min(-1)) / np.maximum(r.max(-1), 1e-3)
    med = cv2.medianBlur((L * 255).astype(np.uint8), 31).astype(np.float32) / 255
    inner = fl & ~edges
    bright = (L - med) > 0.06
    # how varied the floor's own shading is at a middle scale (reflections: soft streaks), and its colour
    mid = cv2.GaussianBlur(L, (0, 0), 4) - cv2.GaussianBlur(L, (0, 0), 16)
    print(f"{n:10s} floorpx {fl.mean():.3f} inner {inner.mean():.3f} sheen {bright[inner].mean() if inner.any() else 0:.3f} midvar {np.abs(mid[inner]).mean() if inner.any() else 0:.4f} sat {np.median(sat[fl]) if fl.any() else 0:.2f} lum {np.median(L[fl]) if fl.any() else 0:.2f}")
