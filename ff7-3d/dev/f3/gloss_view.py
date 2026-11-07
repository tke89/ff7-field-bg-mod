import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, surface
rows = []
for n in sys.argv[1].split(","):
    f = common.Field(n)
    r = np.asarray(Image.open(f.render).convert("RGB"))
    g, m = surface.gloss_metal(f, (f.RW, f.RH))
    k = 360 / f.RH
    a = cv2.resize(r, (int(f.RW * k), 360)); b = cv2.resize((np.dstack([g] * 3) * 255).astype(np.uint8), (int(f.RW * k), 360))
    row = np.concatenate([a, b], 1); row = cv2.resize(row, (960, 360 * 960 // row.shape[1])) if row.shape[1] > 960 else row
    rows.append(np.pad(row, ((0, 4), (0, 960 - row.shape[1]), (0, 0))))
Image.fromarray(np.concatenate(rows, 0)).save(sys.argv[2])
