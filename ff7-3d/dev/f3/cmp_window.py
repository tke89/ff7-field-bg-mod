import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import gltfout
name, ox, oy = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
x1, y1, x2, y2 = [int(v) for v in sys.argv[4:8]]   # shot px (960x720)
base = f"C:/Users/you/Desktop/ff7-3d/report/review/{name}/"
G = f"C:/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY VII Steam Edition/ff7/workingdir/mesh/field/{name}/"
shots = {n: np.asarray(Image.open(base + n + ".jpg").convert("RGB")).astype(np.float32) for n in ("3d", "ai2d", "original")}
sc = gltfout.read_dds(G + f"{name}_scene.dds")[..., :3].astype(np.float32)
gl = cv2.resize(gltfout.read_dds(G + f"{name}_scene_glow.dds")[..., :3], (sc.shape[1], sc.shape[0])).astype(np.float32)
# render px for the shot box: render = (shot/3 + offset) * 4
def tor(a): 
    S = sc.shape[1] // 320
    return cv2.resize(a[(oy*S):(oy*S)+240*S, (ox*S):(ox*S)+320*S], (960, 720), interpolation=cv2.INTER_AREA)
scene, glow = tor(sc), tor(gl)
lin = lambda c: (c / 255) ** 2.2
gam = lambda c: np.clip(c, 0, 1) ** (1 / 2.2) * 255
pred = {"scene": scene, "scene*0.75": gam(lin(scene) * 0.75), "scene*0.75+glow": np.clip(gam(lin(scene) * 0.75) + glow, 0, 255)}
box = (slice(y1, y2), slice(x1, x2))
for n, a in {**shots, **pred}.items():
    c = a[box].reshape(-1, 3)
    hsv = cv2.cvtColor(a[box].astype(np.uint8), cv2.COLOR_RGB2HSV)
    print(f"{n:18s} mean {c.mean(0).round(1)}  sat {hsv[...,1].mean():.1f}")
out = np.concatenate([np.pad(a[box].astype(np.uint8), ((0,0),(0,4),(0,0)), constant_values=255) for a in (shots["3d"], pred["scene*0.75+glow"], pred["scene"], shots["ai2d"], shots["original"])], 1)
Image.fromarray(out).save(sys.argv[8])
