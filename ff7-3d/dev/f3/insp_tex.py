import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np
from PIL import Image
from ff3d import gltfout
G = r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir\mesh\field"
W = r"C:\Users\you\Desktop\ff7-3d\work"
name = sys.argv[1]
x1, y1, x2, y2 = [int(v) for v in sys.argv[2:6]]  # render px
out = []
al = np.asarray(Image.open(f"{W}/{name}/aligned.png").convert("RGB"))
print("aligned", al.shape)
out.append(al[y1:y2, x1:x2])
for suf in ["scene", "scene_glow", "scene_fx", "scene_sun", "scene_nml", "scene_pbr"]:
    try:
        d = gltfout.read_dds(f"{G}/{name}/{name}_{suf}.dds")
    except Exception as e:
        print(suf, e); continue
    k = d.shape[1] / al.shape[1]
    c = d[int(y1*k):int(y2*k), int(x1*k):int(x2*k)]
    print(suf, d.shape, "crop mean rgba", c.reshape(-1, 4).mean(0).round(1), "max", c.reshape(-1,4).max(0))
    import cv2
    c = cv2.resize(c, (x2-x1, y2-y1), interpolation=cv2.INTER_NEAREST)
    if suf in ("scene", "scene_glow", "scene_nml"):
        out.append(c[..., :3])
    else:
        for ch in range(4):
            out.append(np.repeat(c[..., ch:ch+1], 3, 2))
img = np.concatenate([np.pad(o, ((0, 0), (0, 6), (0, 0)), constant_values=255) for o in out], 1)
Image.fromarray(img).save(sys.argv[6])
