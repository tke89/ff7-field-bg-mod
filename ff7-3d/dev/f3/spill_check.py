import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, life, gltfout
n = sys.argv[1]
f = common.Field(n)
G = f"C:/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY VII Steam Edition/ff7/workingdir/mesh/field/{n}/"
r = np.asarray(Image.open(f.render).convert("RGB"))
gl = gltfout.read_dds(G + f"{n}_scene_glow.dds")[..., :3]
hw, hh = gl.shape[1], gl.shape[0]
small = cv2.resize(r, (hw, hh), interpolation=cv2.INTER_AREA)
luma = small.astype(np.float32) @ [0.2126, 0.7152, 0.0722]
dark = luma < 16
added = gl.max(-1) > 3
print("glow pixels over near-black painting:", (dark & added).sum(), "of", added.sum(), " max glow there", gl[dark].max(0) if dark.any() else None)
x1, y1, x2, y2 = [int(v) for v in sys.argv[2:6]]  # render px region (the shaft)
reg = (slice(y1 // 2, y2 // 2), slice(x1 // 2, x2 // 2))
print("shaft luma pctl 50/90/99:", np.percentile(luma[reg], [50, 90, 99]).round(1), " glow mean/max:", gl[reg].reshape(-1, 3).mean(0).round(2), gl[reg].reshape(-1, 3).max(0))
img = np.concatenate([small, np.clip(gl.astype(int) * 4, 0, 255).astype(np.uint8), np.dstack([dark * 255] * 3).astype(np.uint8)], 1)
Image.fromarray(img).save(sys.argv[6])
