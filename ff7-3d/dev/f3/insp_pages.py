import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np
from PIL import Image
from ff3d import common, glow
from ff7bg.sheets import render_composite, page_tile_source, PageCache
name = sys.argv[1]
f = common.Field(name)
studio = glow.studio_pages(f)
S = f.scale
print("pages", sorted(studio), "frame", f.frame)
rect = (f.mx, f.my, f.w, f.h)
ai2d = np.asarray(render_composite(f.bg, rect, page_tile_source(studio, S), S))
ai2d_noglass = np.asarray(render_composite(f.bg, rect, page_tile_source(studio, S), S, include=lambda t: not t.param and not (t.blending and t.type_trans == 0)))
orig = np.asarray(render_composite(f.bg, rect, PageCache(f.bg).tile, 1))
orig = np.asarray(Image.fromarray(orig).resize((f.w*S, f.h*S), Image.NEAREST))
al = np.asarray(Image.open(f.render).convert("RGB"))
x1, y1, x2, y2 = [int(v) for v in sys.argv[2:6]]
crops = [a[y1:y2, x1:x2] for a in (al, ai2d, ai2d_noglass, orig)]
for n, c in zip(("render", "pages", "pages-no-avg-layer", "orig"), crops):
    print(n, c.reshape(-1, 3).mean(0).round(1))
Image.fromarray(np.concatenate([np.pad(c, ((0,0),(0,6),(0,0)), constant_values=255) for c in crops], 1)).save(sys.argv[6])
# the glass layer page itself
for t in f.bg.tiles:
    pass
