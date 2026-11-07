import sys, json, urllib.request
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d-dev-f3")
import numpy as np
from ff3d import common, glow, build
built = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/3d/built"))
for n in sys.argv[1].split(","):
    if not built.get(n):
        print(n, "not built (skipped)"); continue
    f = common.Field(n)
    o = build.options(f)
    a = glow.light_added(f, float(o["static_glow"]))
    old = glow.static_light(f) * max(0.0, float(o["static_glow"]) - glow.light_kept(f))
    print(f"{n:9s} kept {glow.light_kept(f):.2f}  old add mean {old.mean():.3f} max {old.max():.1f}  new mean {0 if a is None else a.mean():.3f} max {0 if a is None else a.max():.1f}")
