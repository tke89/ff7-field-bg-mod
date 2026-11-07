import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2
from PIL import Image
from ff3d import common, glow
from ff7bg.sheets import render_composite, PageCache, page_tile_source
n, ox, oy, lx, ly = sys.argv[1], *[int(v) for v in sys.argv[2:6]]
f = common.Field(n)
studio = glow.studio_pages(f)
S = f.scale
cache = PageCache(f.bg)
rect = (f.mx, f.my, f.w, f.h)
l3t = [t for t in f.bg.tiles if t.layer == 3]
carried = [t for t in l3t if studio[t.tex][t.src_y*S:(t.src_y+t.size)*S, t.src_x*S:(t.src_x+t.size)*S][..., :3].max() > 0]
print("layer-3 tiles", len(l3t), "carrying art in the Studio pages", len(carried))
# what the game shows in AI 2D: base (layers<3, default states) at the view offset + layer 3 (Studio pages) at its parallax offset, added
from ff7bg.ai import shows
d = f.meta.get("rendered_defaults", {})
base = np.asarray(render_composite(f.bg, rect, page_tile_source(studio, S), S, lambda t: shows(d)(t) and t.layer < 3)).astype(np.float32)
l3 = np.asarray(render_composite(f.bg, rect, page_tile_source(studio, S), S, lambda t: t.layer == 3)).astype(np.float32)
l3o = np.asarray(render_composite(f.bg, rect, cache.tile, 1, lambda t: t.layer == 3)).astype(np.float32)
l3o = cv2.resize(l3o, (f.w * S, f.h * S), interpolation=cv2.INTER_NEAREST)
view = lambda a, x, y: a[y*S:(y+240)*S, x*S:(x+320)*S]
game = np.clip(view(base, ox, oy) + view(l3, lx, ly), 0, 255).astype(np.uint8)
orig_like = np.clip(view(base, ox, oy) + view(l3o, lx, ly), 0, 255).astype(np.uint8)
shot = cv2.resize(np.asarray(Image.open(f"C:/Users/you/Desktop/ff7-3d/report/review/{n}/ai2d.jpg").convert("RGB")), (320 * S, 240 * S))
img = np.concatenate([shot, game, orig_like], 1)
Image.fromarray(cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))).save(sys.argv[6])
