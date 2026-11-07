"""How many fields show static layer-2/3 art (the Studio keeps the original's there) and static blended tiles over it."""
import sys, json
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np
from ff3d import common
from ff7bg.field import parse_background
from ff7bg.lgp import LgpArchive
from ff7bg.lzs import decompress
from ff7bg.sheets import canvas_rect, draw_order, PageCache
names = common.maplist()
out = []
with LgpArchive(str(common.LGP)) as lgp:
    for n in names:
        try:
            raw = decompress(lgp.read(n)); bg = parse_background(n, raw)
        except Exception:
            continue
        if not bg.tiles: continue
        mx, my, w, h = canvas_rect(bg)
        top = np.full((h, w), -1, np.int16)   # layer of the top-most opaque static tile
        cache = PageCache(bg)
        static_solid = [t for t in bg.tiles if not t.param and not t.blending]
        for t in draw_order(static_solid):
            x, y = t.dst_x - mx, t.dst_y - my
            a = cache.tile(t)[..., 3][:h - y, :w - x] > 0
            top[y:y + t.size, x:x + t.size][a] = t.layer
        back = top >= 2
        glass = np.zeros((h, w), bool)
        for t in bg.tiles:
            if not t.param and t.blending:
                x, y = t.dst_x - mx, t.dst_y - my
                a = cache.tile(t)[..., 3][:h - y, :w - x] > 0
                glass[y:y + t.size, x:x + t.size] |= a
        out.append((n, round(float(back.mean()), 3), round(float((back & glass).mean()), 4),
                    sorted({t.type_trans for t in bg.tiles if not t.param and t.blending})))
json.dump(out, open("scan_backdrop.json", "w", encoding="utf-8"))
print(len(out), "fields")
print("layer>=2 visible >2%:", sum(1 for o in out if o[1] > 0.02))
print("blended over layer>=2 >0.5%:", [(o[0], o[1], o[2], o[3]) for o in out if o[2] > 0.005])
