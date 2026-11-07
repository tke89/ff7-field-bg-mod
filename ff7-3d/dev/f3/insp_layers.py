import sys, collections
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
from ff3d import common
name = sys.argv[1]
f = common.Field(name)
print(name, "canvas", f.mx, f.my, f.w, f.h, "render", f.RW, f.RH, "scale", f.scale, "version", f.version)
t0 = f.bg.tiles[0]
print([a for a in dir(t0) if not a.startswith("_")])
groups = collections.defaultdict(list)
for t in f.bg.tiles:
    groups[(t.layer, t.blending, t.type_trans, t.param, t.state, t.tex, t.palette)].append(t)
for k, ts in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][3])):
    xs = [t.dst_x for t in ts]; ys = [t.dst_y for t in ts]
    print("layer %s blend %s trans %s param %s state %s page %s pal %s: %d tiles x %d..%d y %d..%d z %s" % (*k, len(ts), min(xs), max(xs)+ts[0].size, min(ys), max(ys)+ts[0].size, sorted(set(t.z for t in ts))[:4]))
