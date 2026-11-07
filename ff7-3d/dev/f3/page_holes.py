"""Tiles whose Studio page block is black or transparent where the original tile is opaque and not black."""
import sys
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np, cv2, json
from ff3d import common, glow
from ff7bg.sheets import PageCache, tile_alpha
n = sys.argv[1]
f = common.Field(n)
studio = glow.studio_pages(f)
S = f.scale
cache = PageCache(f.bg)
bad = {}
for t in f.bg.tiles:
    if t.tex not in studio: 
        bad.setdefault("no page", []).append(t.tex); continue
    o = cache.tile(t).astype(np.float32)
    oa = o[..., 3] > 0
    ol = o[..., :3].max(-1)
    blk = studio[t.tex][t.src_y * S:(t.src_y + t.size) * S, t.src_x * S:(t.src_x + t.size) * S].astype(np.float32)
    small = cv2.resize(blk, (t.size, t.size), interpolation=cv2.INTER_AREA)
    sa = small[..., 3] > 127
    sl = small[..., :3].max(-1)
    want = oa & (ol > 30)
    if want.sum() < 16: continue
    hole = want & (~sa | (sl < 12))
    if hole.mean() > 0.25 * want.mean():
        bad.setdefault((t.param, t.state, t.blending, t.layer), []).append((t.dst_x, t.dst_y, round(float(hole.sum() / want.sum()), 2)))
for k, v in bad.items():
    print(k, len(v), v[:12])
