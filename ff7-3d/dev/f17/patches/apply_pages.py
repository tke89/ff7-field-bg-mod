"""apply_pages.py field ...: after the F17 merge (glow.seamless), rewrite a built field's 2D pages (overlays and glow
frames, as the build's last step does) with the LIVE code, backing up its current pages to
ff7-3d-dev-f17/backup/pages/<field>/ first. Only the overlay pages change with F17 (the mesh, the look and
entry_off.npz don't), so this stands in for a rebuild. Skips a field the Studio hasn't marked built or is building.
Seen on the field's next entry."""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

LIVE = Path(r"C:\Users\you\Desktop\ff7-3d")
BACK = Path(r"C:\Users\you\Desktop\ff7-3d-dev-f17\backup\pages")
sys.path.insert(0, str(LIVE))
from ff3d import common, glow  # noqa: E402

get = lambda p: json.load(urllib.request.urlopen("http://127.0.0.1:8765" + p, timeout=10))
assert hasattr(glow, "seamless"), "the live glow.py hasn't got the F17 change"
for name in sys.argv[1:]:
    if get("/api/3d/built").get(name) is not True:
        print(name, "skipped: not built from its current version")
        continue
    if (get("/api/queue").get("current") or {}).get("name") == name:
        print(name, "skipped: the Studio is building it")
        continue
    src = common.PAGES / name
    b = BACK / name
    if not b.exists():  # the first state is the one to restore
        shutil.copytree(src, b)
    f = common.Field(name)
    anim = glow.layers(f)
    plan = glow.overlay_plan(f, set(anim["glowing"]))
    print(name, "pages written, glow frames", glow.write_pages(f, anim, plan), flush=True)
    if glow.scrolling_layers(f):  # write_pages empties the folder; the look step writes the scrolling layers' pages
        from ff3d import build
        build.write_look(f, (f.work / "look.txt").read_text().strip())
        print(name, "look re-applied (scrolling layers)", flush=True)
