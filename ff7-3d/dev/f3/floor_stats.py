import sys, json, urllib.request
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, surface
built = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/3d/built"))
names = sys.argv[1].split(",")
thumbs = []
for n in names:
    if not built.get(n):
        print(n, "not built: skipped"); continue
    f = common.Field(n)
    render = np.asarray(Image.open(f.render).convert("RGB"))
    floor = np.zeros((f.RH, f.RW), np.uint8)
    s, z = f.project(f.tris.reshape(-1, 3))
    tri = (((s - [f.mx, f.my]) * f.scale).reshape(-1, 3, 2)).round().astype(np.int32)
    ok = (z.reshape(-1, 3) > 1).all(1)
    cv2.fillPoly(floor, list(tri[ok]), 1)
    floor = cv2.erode(floor, np.ones((9, 9), np.uint8)) > 0
    g, m = surface.gloss_metal(f, (f.RW, f.RH))
    L = render.astype(np.float32) @ surface.LUMA / 255
    hp = L - cv2.GaussianBlur(L, (0, 0), 3)
    tex = np.sqrt(cv2.GaussianBlur(hp * hp, (0, 0), 6))
    print(f"{n:10s} floor {floor.mean():.3f} gloss {g[floor].mean():.3f} p90 {np.percentile(g[floor],90):.3f} tex {tex[floor].mean():.4f} lum {L[floor].mean():.3f}")
    ys, xs = np.nonzero(floor)
    if len(xs):
        c = render.copy(); c[~floor] = (c[~floor] * 0.25).astype(np.uint8)
        c = c[ys.min():ys.max(), xs.min():xs.max()]
        k = 300 / max(c.shape[:2]); c = cv2.resize(c, (int(c.shape[1]*k), int(c.shape[0]*k)))
        cv2.putText(c, n, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        canvas = np.zeros((300, 300, 3), np.uint8); canvas[:c.shape[0], :c.shape[1]] = c
        thumbs.append(canvas)
rows = [np.concatenate(thumbs[i:i+6] + [np.zeros((300, 300, 3), np.uint8)] * (6 - len(thumbs[i:i+6])), 1) for i in range(0, len(thumbs), 6)]
Image.fromarray(np.concatenate(rows, 0)).save(sys.argv[2])
