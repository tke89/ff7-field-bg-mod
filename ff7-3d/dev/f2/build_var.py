"""Experiment: build fields with some build.DEFAULTS overridden: build_var.py key=value[,key=value] field..."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from ff3d import build
over = {}
for kv in sys.argv[1].split(","):
    k, v = kv.split("=")
    over[k] = json.loads(v)
build.DEFAULTS.update(over)
print("overrides", over, flush=True)
for name in sys.argv[2:]:
    t0 = time.time()
    build.build(name, log=lambda s: print(s, f"({time.time() - t0:.0f} s)", flush=True))
