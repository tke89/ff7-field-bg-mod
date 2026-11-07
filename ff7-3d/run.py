"""FF7 fields as 3D scenes for FFNx-3d.

  run.py build  <field...>   depth, materials, mesh, 2D pages, lighting -> the game folder (work/<field>/ keeps the steps)
  run.py review <field...>   with the game running FFNx-3d: jump to each field, screenshot -> work/<field>/ingame.png
  run.py sheet  [field...]   contact sheets and report/index.html (default: every built field)
  run.py look   relit|painted <field...>   switch a built field's look in seconds (shows live in game)
  run.py regen  lock|game <field...>      stage-2 render (Studio workflow + depth lock), then build it
  run.py use    base|lock|game <field...>  put that build in the game (base = from the Studio's render)
"""
import html
import json
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
from ff3d import build, common  # noqa: E402

PILOT = ["mds7pb_1", "md1stin", "mds7st1", "mkt_w", "nivl", "niv_w", "cos_top", "anfrst_3", "ancnt1", "del1"]


def do_build(fields, progress=False):
    """progress: also print '@ <share done> <step>' lines (the Studio plug-in shows them). Exit code 1 if any failed."""
    failed = 0
    for name in fields:
        t0 = time.time()
        stage = (lambda share, what: print(f"@ {share:.2f} {what}", flush=True)) if progress else (lambda *a: None)
        try:
            build.build(name, log=lambda s: print(s, f"({time.time() - t0:.0f} s)", flush=True), stage=stage)
            (common.WORK / name / "error.txt").unlink(missing_ok=True)
        except Exception as e:
            failed += 1
            print(f"{name}: FAILED {e}", flush=True)
            (common.WORK / name).mkdir(parents=True, exist_ok=True)
            (common.WORK / name / "error.txt").write_text(traceback.format_exc())
    if failed:
        sys.exit(1)


def do_review(fields):
    import live
    for name in fields:
        f = common.Field(name)
        tri = f.walk_triangle()
        try:
            live.jump(name, tri)
            time.sleep(2.0)
            live.shot(f.work / "ingame.png")
            print(f"{name}: in-game shot (triangle {tri})", flush=True)
        except Exception as e:
            print(f"{name}: review failed: {e}", flush=True)


def panel(img, w=480):
    h = int(img.shape[0] * w / img.shape[1])
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def sheet(name):
    work = common.WORK / name
    f = common.Field(name)
    tiles = [np.asarray(Image.open(f.render).convert("RGB"))]
    d = np.load(work / "da3_depth.npy")
    d = (255 * (d - d.min()) / max(np.ptp(d), 1e-9)).astype(np.uint8)
    tiles.append(cv2.applyColorMap(d, cv2.COLORMAP_TURBO)[..., ::-1])
    for p in ("colour.jpg", "mg_normals.png"):
        if (work / p).exists():
            tiles.append(np.asarray(Image.open(work / p).convert("RGB")))
    if (work / "ao.npy").exists():
        tiles.append(np.dstack([np.clip(np.load(work / "ao.npy") * 255, 0, 255).astype(np.uint8)] * 3))
    if (work / "ingame.png").exists():
        tiles.append(np.asarray(Image.open(work / "ingame.png").convert("RGB")))
    panels = [panel(t) for t in tiles]
    h = max(p.shape[0] for p in panels)
    panels = [np.pad(p, ((0, h - p.shape[0]), (0, 0), (0, 0))) for p in panels]
    rows = [np.hstack(panels[i:i + 3] + [np.zeros_like(panels[0])] * (3 - len(panels[i:i + 3]))) for i in range(0, len(panels), 3)]
    Image.fromarray(np.vstack(rows)).save(work / "sheet.jpg", quality=85)


def do_sheet(fields):
    fields = fields or sorted(p.name for p in common.WORK.iterdir() if (p / "report.json").exists() or (p / "error.txt").exists())
    rows = []
    for name in fields:
        work = common.WORK / name
        if (work / "error.txt").exists() and not (work / "report.json").exists():
            rows.append(f"<tr><td>{name}</td><td colspan=5><pre>{html.escape((work / 'error.txt').read_text()[-1500:])}</pre></td></tr>")
            continue
        r = json.loads((work / "report.json").read_text())
        sheet(name)
        fit = r["fit"]
        rows.append(f"<tr><td><b>{name}</b><br>id {r['id']}</td><td>{fit['median_err']:.1%}<br>p90 {fit['p90_err']:.1%}</td>"
                    f"<td>{fit['walk_coverage']:.0%}</td><td>{r['cracks']:.1%}</td><td>{r['triangles'] / 1e6:.2f}M</td>"
                    f"<td>{html.escape(r['glow_layers'])}</td></tr><tr><td colspan=6><img src='../work/{name}/sheet.jpg' "
                    f"style='width:100%'></td></tr>")
    out = HERE / "report"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>FF7 3D pilot</title><style>body{font:14px system-ui;margin:16px;"
        "background:#111;color:#ddd}td{padding:6px;vertical-align:top;border-bottom:1px solid #333}pre{white-space:"
        "pre-wrap;color:#e88}</style><h1>FF7 3D pilot</h1><p>Panels: AI render, depth, colour (de-lit, restored, "
        "contact shadows), normals, ambient occlusion, in-game.</p><table><tr><th>field</th><th>walkmesh fit error"
        "</th><th>walkmesh in view</th><th>cracks</th><th>triangles</th><th>animated glow</th></tr>"
        + "".join(rows) + "</table>")
    print(out / "index.html")


if __name__ == "__main__":
    cmd, fields = sys.argv[1], sys.argv[2:]
    if fields == ["pilot"]:
        fields = PILOT
    if cmd in ("regen", "use"):
        variant, fields = fields[0], fields[1:]
        fields = PILOT if fields == ["pilot"] else fields
        for name in fields:
            try:
                if cmd == "regen":
                    from ff3d import regen
                    regen.run(name, variant)
                    t0 = time.time()
                    build.build(name, log=lambda s: print(s, f"({time.time() - t0:.0f} s)", flush=True), variant=variant)
                else:
                    build.use(name, None if variant == "base" else variant)
                    print(name, "using", variant, flush=True)
            except Exception as e:
                print(f"{name}: FAILED {e}", flush=True)
                traceback.print_exc()
    elif cmd == "look":
        look, fields = fields[0], fields[1:]
        fields = PILOT if fields == ["pilot"] else fields
        from ff3d import life
        for name in fields:
            build.set_options(name, look=look)  # later builds keep it
            f = common.Field(name)
            if life.stale(life.cached(f)):  # built before chimneys, fires, windows, water were looked for: once
                try:
                    life.find(f)
                except Exception as e:
                    print(f"{name}: finding chimneys, fires and flags failed: {e}", flush=True)
            build.write_look(f, look)
            print(name, look)
    elif cmd == "build":
        do_build([f for f in fields if f != "--progress"], progress="--progress" in fields)
    else:
        {"review": do_review, "sheet": do_sheet}[cmd](fields)
