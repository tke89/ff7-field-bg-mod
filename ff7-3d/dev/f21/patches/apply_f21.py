"""apply_f21.py [@list | field ...]: after the F21 merge (glow.py: script_loops' variable states, entry_unknown,
swaps, write_scrolling's stacked states), bring a built field's game files up to it with the LIVE code: its 2D pages
(overlays, glow frames: as the build's last step writes them) and work/<f>/entry_off.npz, and its look re-applied
when the entry_off art changed or it has scrolling layers (the look step paints entry_off into the scene's texture
and writes the scrolling layers' pages). The mesh's depth doesn't change (no field needs a rebuild with the default
FF3D_EVENT_STATES=0), so this stands in for one.

Backs up first, once per field (the first state is the one to restore), to ff7-3d-dev-f21/backup/<field>/: the
pages folder, mesh/field/<field>, work/<f>/entry_off.npz and the field's lighting section text. The nine fields F21
already changed (blin69_1, colne_6, ghotel, gidun_3, hideway2, junonl2, junonr2, mrkt3, rkt_w) keep their pre-F21
backups there. Skips a field the Studio hasn't marked built or is building. Seen on the field's next entry.

    python apply_f21.py @fields_pages.txt @fields_look.txt
"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

LIVE = Path(r"C:\Users\you\Desktop\ff7-3d")
HERE = Path(__file__).resolve().parent
BACK = HERE.parent / "backup"
sys.path.insert(0, str(LIVE))
from ff3d import build, common, glow  # noqa: E402

assert hasattr(glow, "swaps") and hasattr(glow, "entry_unknown"), "the live glow.py hasn't got the F21 change"
get = lambda p: json.load(urllib.request.urlopen("http://127.0.0.1:8765" + p, timeout=10))
names = []
for a in sys.argv[1:]:
    names += [l.strip() for l in open(HERE / a[1:] if not Path(a[1:]).is_absolute() else a[1:], encoding="utf-8")
              if l.strip()] if a.startswith("@") else [a]
for name in names:
    if get("/api/3d/built").get(name) is not True:
        print(name, "skipped: not built from its current version")
        continue
    if (get("/api/queue").get("current") or {}).get("name") == name:
        print(name, "skipped: the Studio is building it")
        continue
    b = BACK / name
    if not b.exists():
        shutil.copytree(common.PAGES / name, b / "pages")
        shutil.copytree(common.MESH / name, b / "mesh")
        (b / "work").mkdir(parents=True)
        if (common.WORK / name / "entry_off.npz").exists():
            shutil.copy2(common.WORK / name / "entry_off.npz", b / "work" / "entry_off.npz")
        cfg = (common.LIGHTING / "config.toml").read_text(encoding="utf-8")
        start = cfg.find(f"[field_{name}]")
        if start >= 0:
            end = cfg.find("\n[", start + 1)
            (b / "lighting_section.toml").write_text(cfg[start:end if end > 0 else None], encoding="utf-8")
    f = common.Field(name)
    old = common.WORK / name / "entry_off.npz"
    before = dict(np.load(old)) if old.exists() else None
    anim = glow.layers(f)
    plan = glow.overlay_plan(f, set(anim["glowing"]))
    n = glow.write_pages(f, anim, plan)
    off = glow.entry_off_art(f, plan)
    if off is None:
        old.unlink(missing_ok=True)
    else:
        np.savez_compressed(old, rgb=off[0], weight=off[1].astype(np.float16))
    same = (before is None and off is None) or (before is not None and off is not None and
                                                np.array_equal(before["rgb"], off[0]) and
                                                np.array_equal(before["weight"], off[1].astype(np.float16)))
    print(name, f"pages written, glow frames {n}, overlays {len(plan['tiles'])}, entry_on {len(plan['entry_on'])}, "
          f"entry_off {'same' if same else 'changed'}", flush=True)
    if not same or glow.scrolling_layers(f):
        build.write_look(f, (f.work / "look.txt").read_text().strip())
        print(name, "look re-applied", flush=True)
