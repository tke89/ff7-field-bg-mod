import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from pathlib import Path
from ff3d import common, life, build, atmos, gltfout
n = sys.argv[1]
f = common.Field(n)
o = build.options(f)
out = Path(f"scratch/{n}"); out.mkdir(parents=True, exist_ok=True)
colour = np.asarray(Image.open(f.render).convert("RGB")).astype(np.float32)
colour = build.saturate(colour, build.look_light(o, "relit")["saturation"])
sources, keys, light, summary = life.write(f, o, out)
atmos.glow(f, colour, float(o["glow"]), out, lights=light["still"] if light else None)
G = f"C:/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY VII Steam Edition/ff7/workingdir/mesh/field/{n}/"
old = gltfout.read_dds(G + f"{n}_scene_glow.dds")[..., :3].astype(int)
new = gltfout.read_dds(out / f"{n}_scene_glow.dds")[..., :3].astype(int)
small = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (old.shape[1], old.shape[0]), interpolation=cv2.INTER_AREA)
dark = small.astype(np.float32) @ [0.2126, 0.7152, 0.0722] < 16
print("glow over near-black: old mean/max", old[dark].mean(0).round(2), old[dark].max(0), " new", new[dark].mean(0).round(2), new[dark].max(0))
print("glow elsewhere: old mean", old[~dark].mean(0).round(2), "new", new[~dark].mean(0).round(2))
Image.fromarray(np.concatenate([np.clip(old * 4, 0, 255), np.clip(new * 4, 0, 255)], 1).astype(np.uint8)).save(f"evidence/{n}_glow_old_new_x4.png")
