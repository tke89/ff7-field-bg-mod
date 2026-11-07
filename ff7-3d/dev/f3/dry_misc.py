import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow, life, build
from pathlib import Path
for n in sys.argv[1].split(","):
    f = common.Field(n)
    o = build.options(f)
    r = np.asarray(Image.open(f.render).convert("RGB"))
    a = glow.light_added(f, float(o["static_glow"]))
    old = glow.static_light(f) * max(0.0, float(o["static_glow"]) - glow.light_kept(f))
    print(n, "static add old max/mean", old.max().round(1), old.mean().round(3), " new", None if a is None else (a.max().round(1), a.mean().round(3)))
    feats = life.cached(f)
    Z, step = life._depth(f)
    print("  reflections", life.reflections(f, feats, Z, step, r, float(o["reflections"])))
