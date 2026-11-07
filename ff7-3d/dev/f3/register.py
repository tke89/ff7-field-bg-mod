"""Register review shots (960x720 = 3x the game's 320x240 view) onto render coordinates: find the view's offset
by matching the original shot against the original composite."""
import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common
from ff7bg.sheets import render_composite, PageCache

def offset(f, shot_orig):
    orig = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), PageCache(f.bg).tile, 1)).astype(np.float32)
    s = cv2.resize(np.asarray(shot_orig).astype(np.float32), (320, 240), interpolation=cv2.INTER_AREA)
    best = None
    for oy in range(0, max(1, f.h - 240 + 1)):
        for ox in range(0, max(1, f.w - 320 + 1)):
            d = np.abs(orig[oy:oy + 240, ox:ox + 320] - s)[20:200].mean()
            if best is None or d < best[0]:
                best = (d, ox, oy)
    return best

if __name__ == "__main__":
    name = sys.argv[1]
    f = common.Field(name)
    base = f"C:/Users/you/Desktop/ff7-3d/report/review/{name}/"
    print(offset(f, Image.open(base + "original.jpg").convert("RGB")))
