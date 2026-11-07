"""Loads the 3D plug-in (3D tab, 3D scene jobs, the game) from ff7-3d/ in this repo. It needs the FFNx-3d fork in
the game to show anything there. Remove this file to turn the plug-in off."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ff7-3d"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from studio_plugin import register  # noqa: E402,F401
