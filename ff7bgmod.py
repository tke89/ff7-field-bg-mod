"""FF7 field background modding helper for FFNx.

  list   [filter]                       list field map names
  export <map...> [--scale N]           unswizzle into work/<map>/ (preview + editable sheets)
  build  <map...> [--flat img.png]      swizzle edited art into dist/mods/Textures/field/<map>/
  verify [map...]                       round-trip check (export -> build without edits == original)

  ai-prep  [map...] [--aspect 1:1,3:2]  every map's default view -> ai/input/<map>.png
  ai-build [map...]                     ai/output/<map>.png -> aligned FFNx pages + ai/review/<map>.jpg
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from ff7bg.field import parse_background
from ff7bg.lgp import LgpArchive
from ff7bg.lzs import decompress
from ff7bg.sheets import PageCache, build_field, export_field

DEFAULT_LGP = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition"
    r"\ff7\workingdir\data\field\flevel.lgp"
)
HERE = Path(__file__).resolve().parent


def field_names(lgp):
    return [n for n in lgp.names() if "." not in n and n != "maplist"]


def load(lgp, name):
    return parse_background(name.lower(), decompress(lgp.read(name)))


def cmd_list(lgp, args):
    names = [n for n in field_names(lgp) if not args.filter or args.filter.lower() in n.lower()]
    for n in names:
        print(n)
    print(f"({len(names)} fields)", file=sys.stderr)


def cmd_export(lgp, args):
    for name in args.maps:
        bg = load(lgp, name)
        out = Path(args.work) / bg.name
        m = export_field(bg, out, scale=args.scale)
        c = m["canvas"]
        print(f"{bg.name}: {c['width']}x{c['height']} canvas, {len(m['tiles'])} tiles, "
              f"{len(m['sheets'])} sheets -> {out}")
        for s in m["sheets"]:
            print(f"    sheets/{s}.png")


def cmd_build(lgp, args):
    if args.flat and len(args.maps) != 1:
        sys.exit("--flat takes one image, so build one map at a time")
    for name in args.maps:
        bg = load(lgp, name)
        work = Path(args.work) / bg.name
        if not (work / "manifest.json").exists():
            sys.exit(f"{work} not found; run export {bg.name} first")
        scale, written, notes = build_field(
            bg, work, Path(args.out), flat=args.flat, scale=args.scale, mask=args.mask
        )
        for n in notes:
            print("  note:", n)
        print(f"{bg.name}: {len(written)} pages at {scale}x -> {written[0].parent}")
        print(f"    check {work / 'preview_modded.png'}")


def cmd_ai_prep(lgp, args):
    from ff7bg import ai

    names = args.maps or field_names(lgp)
    aspects = [ai.ASPECTS[a] for a in args.aspect.split(",")] if args.aspect else None
    jobs = ai.prep(((n, decompress(lgp.read(n))) for n in names), args.dir, aspects, args.prescale, args.margin)
    copies = sum(len(j["copies"]) for j in jobs.values())
    print(f"{len(jobs)} images -> {Path(args.dir) / 'input'}  ({copies} maps reuse one of them)")
    print(f"Put results in {Path(args.dir) / 'output'} with the same file names, then run ai-build.")


def cmd_ai_build(lgp, args):
    import csv
    from ff7bg import ai

    root = Path(args.dir)
    jobs = json.loads((root / "jobs.json").read_text())
    todo = [m.lower() for m in args.maps] if args.maps else list(jobs)
    rows = []
    for name in todo:
        if name not in jobs:
            print(f"{name}: not in jobs.json (it may be a copy of another map; build that one)")
            continue
        found = [p for ext in ("png", "webp", "jpg", "jpeg") if (p := root / "output" / f"{name}.{ext}").exists()]
        if not found:
            continue
        bg = load(lgp, name)
        try:
            r = ai.assemble(bg, found[0], jobs[name], args.out, root / "review",
                            scale=args.scale, do_align=not args.no_align)
        except ValueError as e:
            print(f"{name}: SKIPPED, {e}")
            continue
        b, a = r["drift_before"], r["drift_after"]
        flag = ", ".join(ai.flags_for(r))
        print(f"{name}: {r['pages']} pages at {r['scale']}x"
              f"{' (+' + ', '.join(r['copies']) + ')' if r['copies'] else ''}; "
              f"outlines kept {r['edges_kept']:.0%}"
              f"{f'; aligned {b.median:.1f}px -> {a.median:.1f}px' if r['aligned'] else ''}; "
              f"{r['missing']:.1%} filled from original{('  <- check: ' + flag) if flag else ''}")
        rows.append([name, " ".join(r["copies"]), r["scale"], r["pages"], f"{r['edges_kept']:.3f}",
                     f"{b.median:.2f}", f"{a.median:.2f}", f"{r['missing']:.4f}", flag])
    if rows:
        with open(root / "report.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["map", "copies", "scale", "pages", "outlines_kept", "drift_before_median",
                        "drift_after_median", "missing_fraction", "flag"])
            w.writerows(rows)
        print(f"review images: {root / 'review'}  |  report: {root / 'report.csv'}")
    else:
        print(f"no AI outputs found in {root / 'output'}")


def cmd_verify(lgp, args):
    names = args.maps or field_names(lgp)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(names):
            bg = load(lgp, name)
            work = Path(tmp) / "work" / bg.name
            export_field(bg, work, scale=1)
            build_field(bg, work, Path(tmp) / "mod", scale=1)
            cache = PageCache(bg)
            from PIL import Image

            bad = 0
            pages = {}
            for t in bg.tiles:
                if t.tex not in pages:
                    p = Path(tmp) / "mod" / "field" / bg.name / f"{bg.name}_{t.tex:02d}_00.png"
                    pages[t.tex] = np.asarray(Image.open(p).convert("RGBA"))
                got = pages[t.tex][t.src_y : t.src_y + t.size, t.src_x : t.src_x + t.size]
                want = cache.tile(t)
                opaque = want[..., 3] > 0
                if not (np.array_equal(got[..., 3] > 0, opaque) and np.array_equal(got[opaque], want[opaque])):
                    bad += 1
            if bad:
                failures += 1
                print(f"FAIL {bg.name}: {bad} tiles differ")
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(names)} checked", file=sys.stderr)
    print(f"verified {len(names)} fields, {failures} failures")
    sys.exit(1 if failures else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lgp", default=str(DEFAULT_LGP), help="path to flevel.lgp")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("filter", nargs="?")

    p = sub.add_parser("export")
    p.add_argument("maps", nargs="+")
    p.add_argument("--scale", type=int, default=4, help="upscale exported sheets (nearest) to paint at higher res")
    p.add_argument("--work", default=str(HERE / "work"))

    p = sub.add_parser("build")
    p.add_argument("maps", nargs="+")
    p.add_argument("--flat", help="one full-scene image replacing the static scenery (layers 0-1)")
    p.add_argument("--scale", type=int, help="output scale; default is inferred from your images")
    p.add_argument("--mask", choices=["original", "sheet"], default="original",
                   help="original: keep the game's transparent pixels; sheet: trust your PNG's alpha")
    p.add_argument("--work", default=str(HERE / "work"))
    p.add_argument("--out", default=str(HERE / "dist" / "mods" / "Textures"))

    p = sub.add_parser("ai-prep", help="write every map's default view to ai/input for an image generator")
    p.add_argument("maps", nargs="*", help="default: all maps")
    p.add_argument("--aspect", help="pad to the closest of these ratios for fixed-size generators, e.g. 1:1,3:2,2:3")
    p.add_argument("--prescale", type=int, default=1, help="Lanczos-upscale the inputs by N")
    p.add_argument("--margin", type=float, default=0.0,
                   help="extra blurred border on each side, as a fraction (0.06 = 6%%), so a generator that "
                        "zooms in doesn't crop real content")
    p.add_argument("--dir", default=str(HERE / "ai"))

    p = sub.add_parser("ai-build", help="align ai/output images to the game geometry and build FFNx pages")
    p.add_argument("maps", nargs="*", help="default: every map with an output image")
    p.add_argument("--scale", type=int, help="output scale; default follows the AI image size")
    p.add_argument("--no-align", action="store_true", help="use the AI image exactly as returned")
    p.add_argument("--dir", default=str(HERE / "ai"))
    p.add_argument("--out", default=str(HERE / "dist" / "mods" / "Textures"))

    p = sub.add_parser("verify")
    p.add_argument("maps", nargs="*")

    args = ap.parse_args()
    commands = {"list": cmd_list, "export": cmd_export, "build": cmd_build, "verify": cmd_verify,
                "ai-prep": cmd_ai_prep, "ai-build": cmd_ai_build}
    with LgpArchive(args.lgp) as lgp:
        commands[args.cmd](lgp, args)


if __name__ == "__main__":
    main()
