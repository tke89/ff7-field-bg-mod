import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common
from ff7bg.sheets import render_composite, PageCache
from ff7bg import ai
n, ox, oy = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
f = common.Field(n)
cache = PageCache(f.bg)
rect = (f.mx, f.my, f.w, f.h)
d = f.meta.get("rendered_defaults", {})
base = np.asarray(render_composite(f.bg, rect, cache.tile, 1, lambda t: (not t.param or ai.shows(d)(t)) and t.layer < 3)).astype(np.float32)
l3 = np.asarray(render_composite(f.bg, rect, cache.tile, 1, lambda t: t.layer == 3)).astype(np.float32)
shot = cv2.resize(np.asarray(Image.open(f"C:/Users/you/Desktop/ff7-3d/report/review/{n}/original.jpg").convert("RGB")).astype(np.float32), (320, 240), interpolation=cv2.INTER_AREA)
resid = np.clip(shot - base[oy:oy + 240, ox:ox + 320], 0, None).mean(-1)
resid[:40, :80] = 0  # the debug text box
best = []
L = l3.mean(-1)
for dx in range(0, f.w - 320 + 1, 2):
    for dy in range(0, f.h - 240 + 1, 2):
        a = L[dy:dy + 240, dx:dx + 320]
        if a.std() < 1: continue
        c = np.corrcoef(a.ravel(), resid.ravel())[0, 1]
        best.append((c, dx, dy))
best.sort(reverse=True)
print("view offset", ox, oy, "layer-3 best offsets (corr, x, y):", [(round(c, 3), x, y) for c, x, y in best[:5]])
print("layer-3 at the view offset corr:", [round(c, 3) for c, x, y in best if x == ox - ox % 2 and y == oy - oy % 2][:1])
