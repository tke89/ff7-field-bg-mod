import sys, time
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow, life, build, gltfout
n = sys.argv[1]
f = common.Field(n)
t = time.time()
g = glow.through_glass(f)
print("through_glass", None if g is None else (g[1].mean().round(4), g[1].max()), f"{time.time()-t:.1f}s")
o = build.options(f)
out = __import__("pathlib").Path(f"scratch/{n}"); out.mkdir(parents=True, exist_ok=True)
sources, keys, light, summary = life.write(f, o, out)
print("summary", summary)
print("keys point_lights n", len(keys.get("point_lights", [])) // 8)
r = np.asarray(Image.open(f.render).convert("RGB"))
if g is not None:
    w = g[1][..., None]
    c = (r * (1 - w) + g[0] * w).astype(np.uint8)
    Image.fromarray(np.concatenate([r[0:450, 440:840], c[0:450, 440:840]], 1)).save(f"evidence/{n}_glass_colour.png")
