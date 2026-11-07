import sys, collections
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow
from ff7bg.sheets import render_composite, PageCache, page_tile_source
from ff7bg import ai
n = sys.argv[1]; x0, y0, x1, y1 = [int(v) for v in sys.argv[2:6]]
f = common.Field(n)
c = collections.Counter()
for t in f.bg.tiles:
    if t.dst_x < x1 and t.dst_x + t.size > x0 and t.dst_y < y1 and t.dst_y + t.size > y0:
        c[(t.layer, t.blending, t.type_trans, t.param, t.state, t.tex, t.palette)] += 1
for k, v in sorted(c.items()): print("layer %s blend %s trans %s param %s state %s page %s pal %s" % k, v)
defaults = f.meta.get("rendered_defaults", {})
orig = ai.default_view(f.bg, defaults)[0][..., :3]
studio = glow.studio_pages(f)
S = f.scale
from ff7bg.ai import shows
pages = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), page_tile_source(studio, S), S, shows(defaults)))
r = np.asarray(Image.open(f.render).convert("RGB"))
X0, Y0, X1, Y1 = x0 - f.mx, y0 - f.my, x1 - f.mx, y1 - f.my
crops = [cv2.resize(orig[Y0:Y1, X0:X1], ((X1 - X0) * S, (Y1 - Y0) * S), interpolation=cv2.INTER_NEAREST), r[Y0*S:Y1*S, X0*S:X1*S], pages[Y0*S:Y1*S, X0*S:X1*S]]
img = np.concatenate([np.pad(c, ((0, 0), (0, 6), (0, 0)), constant_values=255) for c in crops], 1)
k = min(1.0, 1500 / img.shape[1])
Image.fromarray(cv2.resize(img, (int(img.shape[1] * k), int(img.shape[0] * k)))).save(sys.argv[6])
