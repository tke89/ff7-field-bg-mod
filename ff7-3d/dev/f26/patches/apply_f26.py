"""apply_f26.py [@list | field ...]: after the F26 merge (glow.py: FF3D_EVENT_STATES "auto", event_states), bring a
built "look" field's game files up to it with the LIVE code, as a rebuild would leave them: its 2D pages (overlays,
glow frames: the build's last step), work/<f>/entry_off.npz (the off art beneath the new start-on tiles) and its look
re-applied (the look step paints entry_off into the scene's colour and writes the scrolling layers' pages, which
write_pages clears). The mesh's depth doesn't change for these fields (fields_look.txt); the 10 in fields_rebuild.txt
need a real rebuild (their scene cover, parallax order or pushes change): queue those in the Studio.

The light layers drawn over the scene (glow_over_scene) are the last build's, from report.json, as write_look takes
them; report.json's "overlays" counts are brought up to date.

Backs up first, once per field (the first state is the one to restore), to dev/f26/backup_rollout/<field>/: the pages
folder, mesh/field/<field>, work/<f>/entry_off.npz and report.json, and the field's lighting section text. Skips a
field the Studio hasn't marked built or is building. Seen on the field's next entry.

    python apply_f26.py @fields_look.txt
"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

import os  # noqa: E402

LIVE = Path(os.environ.get("F26_CODE_ROOT", r"C:\Users\you\Desktop\ff7-3d"))  # (the F26 test ran it on its dev copy)
HERE = Path(__file__).resolve().parent
BACK = HERE.parent / "backup_rollout"
sys.path.insert(0, str(LIVE))
from ff3d import build, common, glow  # noqa: E402

assert hasattr(glow, "event_states") and glow.EVENT_STATES == "auto", "the live glow.py hasn't got the F26 change (or FF3D_EVENT_STATES is set)"
get = lambda p: json.load(urllib.request.urlopen("http://127.0.0.1:8765" + p, timeout=10))
names = []
for a in sys.argv[1:]:
    names += [l.strip() for l in open(HERE / a[1:] if not Path(a[1:]).is_absolute() else a[1:], encoding="utf-8")
              if l.strip()] if a.startswith("@") else [a]
built = get("/api/3d/built")
for name in names:
    if built.get(name) is not True:
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
        for fn in ("entry_off.npz", "report.json"):
            if (common.WORK / name / fn).exists():
                shutil.copy2(common.WORK / name / fn, b / "work" / fn)
        cfg = (common.LIGHTING / "config.toml").read_text(encoding="utf-8")
        start = cfg.find(f"[field_{name}]")
        if start >= 0:
            end = cfg.find("\n[", start + 1)
            (b / "lighting_section.toml").write_text(cfg[start:end if end > 0 else None], encoding="utf-8")
    f = common.Field(name)
    rep_p = f.work / "report.json"
    rep = json.loads(rep_p.read_text())
    anim = glow.layers(f)
    plan = glow.overlay_plan(f, set(anim["glowing"]))
    plan["over"] = {tuple(int(v) for v in k.split("_")): s for k, s in (rep.get("glow_over_scene") or {}).items()}
    n = glow.write_pages(f, anim, plan)
    off = glow.entry_off_art(f, plan)
    if off is None:
        (f.work / "entry_off.npz").unlink(missing_ok=True)
    else:
        np.savez_compressed(f.work / "entry_off.npz", rgb=off[0], weight=off[1].astype(np.float16))
    rep["overlays"] = {"tiles": len(plan["tiles"]), "params": sorted({t.param for t in plan["tiles"]}),
                       "start_on": len(plan["entry_on"]), "backdrop": len(plan.get("backdrop", []))}
    rep_p.write_text(json.dumps(rep, indent=1))
    build.write_look(f, (f.work / "look.txt").read_text().strip())
    build.snapshot(f)
    print(name, f"pages written, glow frames {n}, overlays {len(plan['tiles'])}, start on {len(plan['entry_on'])}, "
          "look re-applied", flush=True)
