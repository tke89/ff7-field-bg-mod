"""F19, after the merge: rewrite a built field's 2D pages (overlays and glow frames) with the LIVE code, backing up its
current pages to ff7-3d-dev-f19/backup/pages_merge/<field>/ first (the first state is kept); --restore puts them back.
Only the glow frames change with F19's glow.py (moves_apart, painted_out), so this stands in for a rebuild for those
fields. Skips a field the Studio hasn't marked built or is building. Seen on the field's next entry."""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

LIVE = Path(r"C:\Users\you\Desktop\ff7-3d")
BACK = Path(r"C:\Users\you\Desktop\ff7-3d-dev-f19\backup\pages_merge")
sys.path.insert(0, str(LIVE))
from ff3d import common, glow  # noqa: E402

assert hasattr(glow, "moves_apart") and hasattr(glow, "painted_out"), "the live glow.py hasn't got the F19 change"
get = lambda p: json.load(urllib.request.urlopen("http://127.0.0.1:8765" + p, timeout=10))
args = sys.argv[1:]
restore = "--restore" in args
for name in [a for a in args if not a.startswith("--")]:
    src, b = common.PAGES / name, BACK / name
    if restore:
        if b.exists():
            shutil.rmtree(src, ignore_errors=True)
            shutil.copytree(b, src)
            print(name, "restored")
        continue
    if get("/api/3d/built").get(name) is not True:
        print(name, "skipped: not built from its current version")
        continue
    if (get("/api/queue").get("current") or {}).get("name") == name:
        print(name, "skipped: the Studio is building it")
        continue
    if not b.exists():
        shutil.copytree(src, b)
    f = common.Field(name)
    anim = glow.layers(f)
    plan = glow.overlay_plan(f, set(anim["glowing"]))
    print(name, "pages written, glow frames", glow.write_pages(f, anim, plan), flush=True)
    if glow.scrolling_layers(f):  # write_pages empties the folder; the look step writes the scrolling layers' pages
        from ff3d import build
        build.write_look(f, (f.work / "look.txt").read_text().strip())
        print(name, "look re-applied (scrolling layers)", flush=True)
