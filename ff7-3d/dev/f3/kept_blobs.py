import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow
from ff7bg import ai
n = sys.argv[1]
f = common.Field(n)
sl = glow.static_light(f)
light = cv2.resize(sl, (f.w, f.h), interpolation=cv2.INTER_AREA)
shown = ai.default_view(f.bg, f.meta.get("rendered_defaults", {}))[0][..., :3].astype(np.float32)
without = np.clip(shown - light, 0, 255)
r = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (f.w, f.h), interpolation=cv2.INTER_AREA).astype(np.float32)
lum = light.mean(-1)
near = lum > 3
nlab, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(near.astype(np.uint8), np.ones((5, 5), np.uint8)), 8)
print("global kept", glow.light_kept(f))
for k in range(1, nlab):
    m = (lab == k) & near
    if m.sum() < 20: continue
    ring = (cv2.dilate((lab == k).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & ~near
    sel = m | ring
    W, L, R = without[sel], light[sel], r[sel]
    ks = [np.linalg.lstsq(np.column_stack([W, L[:, j], np.ones(len(W))]), R[:, j], rcond=None)[0][3] for j in range(3)]
    x, y, w, h, a = st[k]
    print(f"blob {k}: bg box x{x}-{x+w} y{y}-{y+h} px {m.sum()} mean light {L[m[sel]].mean(0).round(1) if False else light[m].mean(0).round(1)} kept per channel {np.round(ks, 2)} median {np.median(ks):.2f}")
