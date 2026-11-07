"""Stage-2 A/B: for each field, the base build (Studio render) against the depth-locked variants. Prints a table
(drift of the raw render from the original, walkmesh fit, cracks) and, with the game running, puts each version in
the game, re-enters the field and screenshots it -> report/ab_<field>.jpg and report/ab_table.txt.

  ab.py <field...> [--no-shots]
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
from ff3d import build, common  # noqa: E402

VARIANTS = ["base", "lock", "game"]


def stats(name, variant):
    work = common.WORK / name / ("" if variant == "base" else variant)
    if not (work / "report.json").exists():
        return None
    r = json.loads((work / "report.json").read_text())
    a = json.loads((work / "align.json").read_text())
    return {"drift": a["drift_before"], "fit": r["fit"]["median_err"], "p90": r["fit"]["p90_err"], "cracks": r["cracks"]}


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    shots = "--no-shots" not in sys.argv
    lines = [f"{'field':10s} {'version':6s} {'drift px':>8s} {'fit':>6s} {'fit p90':>8s} {'cracks':>7s}"]
    for name in names:
        for v in VARIANTS:
            s = stats(name, v)
            if s:
                lines.append(f"{name:10s} {v:6s} {s['drift']:8.2f} {s['fit']:6.1%} {s['p90']:8.1%} {s['cracks']:7.1%}")
    table = "\n".join(lines)
    print(table)
    (HERE / "report" / "ab_table.txt").write_text(table)
    if not shots:
        return
    import live
    for name in names:
        f = common.Field(name)
        tri = f.walk_triangle()
        tiles = []
        for v in VARIANTS:
            if not stats(name, v):
                continue
            build.use(name, None if v == "base" else v)
            live.jump(name, tri)  # re-entering loads the version's pages
            time.sleep(2.0)
            live.send("reload")   # and its scene mesh, for certain
            time.sleep(2.5)
            p = live.shot(common.WORK / name / f"ab_{v}.png")
            im = Image.open(p).convert("RGB")
            if im.size == (1280, 720):
                im = im.crop((160, 0, 1120, 720))
            im = im.resize((640, 480))
            ImageDraw.Draw(im).text((8, 6), f"{name}  {v}", fill=(255, 255, 0))
            tiles.append(im)
        sheet = Image.new("RGB", (640 * len(tiles), 480))
        for i, t in enumerate(tiles):
            sheet.paste(t, (640 * i, 0))
        sheet.save(HERE / "report" / f"ab_{name}.jpg", quality=86)
        build.use(name)  # back to the base build until the comparison is decided
        live.send("reload")
        print(name, "shots done", flush=True)


if __name__ == "__main__":
    main()
