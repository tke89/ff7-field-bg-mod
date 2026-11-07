import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow, atmos, gltfout
n = sys.argv[1]
f = common.Field(n)
G = f"C:/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY VII Steam Edition/ff7/workingdir/mesh/field/{n}/"
r = np.asarray(Image.open(f.render).convert("RGB"))
sl = glow.static_light(f)
k = 0.8 - glow.light_kept(f)
add = np.clip(sl * k, 0, 255)
gl = cv2.resize(gltfout.read_dds(G + f"{n}_scene_glow.dds")[..., :3], (f.RW, f.RH))
sc = gltfout.read_dds(G + f"{n}_scene.dds")[..., :3]
print("static add max", add.reshape(-1, 3).max(0), "glow max", gl.reshape(-1, 3).max(0))
s = 0.5
tiles = [r, np.clip(sl, 0, 255).astype(np.uint8), np.clip(add * 3, 0, 255).astype(np.uint8), np.clip(gl.astype(int) * 3, 0, 255).astype(np.uint8), sc]
img = np.concatenate([cv2.resize(t, (int(f.RW * s), int(f.RH * s))) for t in tiles], 1)
Image.fromarray(img).save(sys.argv[2])
