"""Experiment: build without the placed models (A/B for placed_layers)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from ff3d import build
build.placed_layers = lambda *a, **k: []
for name in sys.argv[1:]:
    t0 = time.time()
    build.build(name, log=lambda s: print(s, f"({time.time() - t0:.0f} s)", flush=True))
