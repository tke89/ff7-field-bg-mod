import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, life, depth
from ff3d.build import marigold_normals
n = sys.argv[1]
f = common.Field(n)
Z, step = life._depth(f)
bx, by = depth.grid_bg(f, step)
gh, gw = Z.shape
_, P = f.unproject(bx[:gh, :gw], by[:gh, :gw], np.where(np.isfinite(Z), Z, np.nan))
dx = np.gradient(P, axis=1); dy = np.gradient(P, axis=0)
N = np.cross(dx, dy); N /= np.linalg.norm(N, axis=-1, keepdims=True) + 1e-9
up = np.abs(N[..., 2])
mg = marigold_normals(f, (gh, gw))[..., 2]
r = np.asarray(Image.open(f.render).convert("RGB"))
y0, y1 = int(sys.argv[2]), int(sys.argv[3])
crop = r[y0:y1]
u = cv2.resize(np.nan_to_num(up), (f.RW, f.RH))[y0:y1]
m = cv2.resize(mg, (f.RW, f.RH))[y0:y1]
def ov(mask):
    o = crop.copy(); o[mask] = (0.5 * o[mask] + [0, 127, 0]).astype(np.uint8); return o
# walkmesh projection
floor = np.zeros((f.RH, f.RW), np.uint8)
s, z = f.project(f.tris.reshape(-1, 3))
tri = (((s - [f.mx, f.my]) * f.scale).reshape(-1, 3, 2)).round().astype(np.int32)
cv2.fillPoly(floor, list(tri[(z.reshape(-1, 3) > 1).all(1)]), 1)
fl = floor[y0:y1] > 0
img = np.concatenate([crop, ov(u > 0.75), ov(m > 0.75), ov(fl)], 1)
k = 1600 / img.shape[1]
Image.fromarray(cv2.resize(img, (1600, int(img.shape[0] * k)))).save(sys.argv[4])
