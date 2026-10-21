"""Unswizzle a field background into editable sheets, and swizzle edited art back
into FFNx texture pages (field/<map>/<map>_<page>_00.png)."""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .field import page_rgba

BLEND_NAMES = {0: "blend-avg", 1: "blend-add", 2: "blend-sub", 3: "blend-quarter"}


class PageCache:
    def __init__(self, bg):
        self.bg = bg
        self._pages = {}

    def tile(self, t):
        key = (t.tex, t.palette if self.bg.pages[t.tex].depth < 2 else 0)
        if key not in self._pages:
            self._pages[key] = page_rgba(self.bg, *key)
        block = self._pages[key][t.src_y : t.src_y + t.size, t.src_x : t.src_x + t.size]
        if block.shape[:2] != (t.size, t.size):  # defensive: tile runs off the page
            padded = np.zeros((t.size, t.size, 4), np.uint8)
            padded[: block.shape[0], : block.shape[1]] = block
            block = padded
        return block


def canvas_rect(bg):
    min_x = min(t.dst_x for t in bg.tiles)
    min_y = min(t.dst_y for t in bg.tiles)
    max_x = max(t.dst_x + t.size for t in bg.tiles)
    max_y = max(t.dst_y + t.size for t in bg.tiles)
    return min_x, min_y, max_x - min_x, max_y - min_y


def base_sheet_name(t):
    name = f"L{t.layer}"
    if t.param:
        name += f"_p{t.param:02d}s{t.state:02d}"
    if t.blending:
        name += "_" + BLEND_NAMES.get(t.type_trans, f"blend{t.type_trans}")
    return name


def is_base(t):
    """Static, opaque-drawn scenery: what a flat repaint of the scene replaces."""
    return t.layer in (0, 1) and not t.param and not t.blending


def draw_order(tiles):
    return sorted(tiles, key=lambda t: -t.z)  # stable: back-to-front


def assign_sheets(bg, rect):
    """Map each tile to a sheet; tiles overlapping within a sheet go to _dupN sheets."""
    min_x, min_y, w, h = rect
    occupancy = {}
    assignment = []
    for t in bg.tiles:
        base = base_sheet_name(t)
        dup = 0
        while True:
            name = base if dup == 0 else f"{base}_dup{dup}"
            occ = occupancy.setdefault(name, np.zeros((h, w), bool))
            x, y = t.dst_x - min_x, t.dst_y - min_y
            if not occ[y : y + t.size, x : x + t.size].any():
                occ[y : y + t.size, x : x + t.size] = True
                break
            dup += 1
        assignment.append(name)
    return assignment


def render_composite(bg, rect, tile_source, scale=1, include=lambda t: not t.param):
    """Composite the scene back-to-front with blending, like the game's default view."""
    min_x, min_y, w, h = rect
    out = np.zeros((h * scale, w * scale, 3), np.int32)
    for t in draw_order([t for t in bg.tiles if include(t)]):
        block = tile_source(t).astype(np.int32)
        x, y = (t.dst_x - min_x) * scale, (t.dst_y - min_y) * scale
        s = t.size * scale
        dst = out[y : y + s, x : x + s]
        a = block[..., 3:4]
        src = block[..., :3]
        if t.blending:
            mixed = {
                0: (dst + src) // 2,
                1: dst + src,
                2: dst - src,
                3: dst + src // 4,
            }.get(t.type_trans, (dst + src) // 2)
            src = np.clip(mixed, 0, 255)
        dst[:] = (dst * (255 - a) + src * a) // 255
    return Image.fromarray(out.astype(np.uint8), "RGB")


def sheet_canvases(bg, rect, names, cache):
    """Unswizzle: draw every tile at its screen position on its sheet (1x RGBA)."""
    min_x, min_y, w, h = rect
    canvases = {}
    for t, name in zip(bg.tiles, names):
        c = canvases.setdefault(name, np.zeros((h, w, 4), np.uint8))
        x, y = t.dst_x - min_x, t.dst_y - min_y
        c[y : y + t.size, x : x + t.size] = cache.tile(t)
    return canvases


def upscale_rgba(px, scale):
    """Lanczos upscale with premultiplied alpha, so transparent pixels don't bleed dark fringes."""
    if scale == 1:
        return px
    h, w = px.shape[:2]
    img = Image.fromarray(px, "RGBA").convert("RGBa").resize((w * scale, h * scale), Image.LANCZOS)
    return np.asarray(img.convert("RGBA"))


def tile_alpha(orig_tile, scale):
    """Upscale a tile's original transparency mask with soft (bilinear) edges inside the tile."""
    a = orig_tile[..., 3]
    if scale == 1 or a.min() == a.max():
        return np.full((a.shape[0] * scale, a.shape[1] * scale), a.flat[0], np.uint8) if scale != 1 else a
    up = Image.fromarray(a, "L").resize((a.shape[1] * scale, a.shape[0] * scale), Image.BILINEAR)
    return np.asarray(up)


def swizzle(bg, scale, block_for):
    """Pack per-tile blocks (block_for(t) -> size*scale square RGBA) into texture pages."""
    pages = {}
    for t in bg.tiles:
        s = t.size * scale
        page = pages.setdefault(t.tex, np.zeros((256 * scale, 256 * scale, 4), np.uint8))
        page[t.src_y * scale : t.src_y * scale + s, t.src_x * scale : t.src_x * scale + s] = block_for(t)
    return pages


def page_tile_source(pages, scale):
    def tile(t):
        s = t.size * scale
        return pages[t.tex][t.src_y * scale : t.src_y * scale + s, t.src_x * scale : t.src_x * scale + s]
    return tile


def write_pages(name, pages, mod_root):
    out_dir = Path(mod_root) / "field" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for tex, px in sorted(pages.items()):
        p = out_dir / f"{name}_{tex:02d}_00.png"
        Image.fromarray(px, "RGBA").save(p, compress_level=6)
        written.append(p)
    return written


def export_field(bg, out_dir, scale=1):
    out_dir = Path(out_dir)
    sheets_dir = out_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    rect = canvas_rect(bg)
    min_x, min_y, w, h = rect
    cache = PageCache(bg)
    names = assign_sheets(bg, rect)
    canvases = sheet_canvases(bg, rect, names, cache)

    for name, c in canvases.items():
        img = Image.fromarray(c, "RGBA")
        if scale != 1:
            img = img.resize((w * scale, h * scale), Image.NEAREST)
        img.save(sheets_dir / f"{name}.png")

    preview = render_composite(bg, rect, cache.tile)
    if scale != 1:
        preview = preview.resize((w * scale, h * scale), Image.NEAREST)
    preview.save(out_dir / "preview.png")

    manifest = {
        "field": bg.name,
        "canvas": {"x": min_x, "y": min_y, "width": w, "height": h},
        "export_scale": scale,
        "sheets": sorted(canvases),
        "tiles": [
            {
                "layer": t.layer, "index": t.index, "sheet": name,
                "dst": [t.dst_x, t.dst_y], "size": t.size,
                "page": t.tex, "src": [t.src_x, t.src_y], "palette": t.palette,
                "param": t.param, "state": t.state,
                "blending": t.blending, "type_trans": t.type_trans,
            }
            for t, name in zip(bg.tiles, names)
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _load_rgba(path, size):
    img = Image.open(path).convert("RGBA")
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return np.asarray(img)


def _infer_scale(path, w, h):
    iw, ih = Image.open(path).size
    s = iw / w
    if abs(ih / h - s) > 0.02 * s:
        raise ValueError(f"{path.name} is {iw}x{ih}, which doesn't match the field's {w}x{h} aspect ratio")
    return s


def build_field(bg, work_dir, mod_root, flat=None, scale=None, mask="original"):
    """Swizzle sheets (and/or one flat image) into FFNx pages under mod_root/field/<map>/.

    Returns (scale, written_paths, notes)."""
    work_dir = Path(work_dir)
    sheets_dir = work_dir / "sheets"
    manifest = json.loads((work_dir / "manifest.json").read_text())
    rect = canvas_rect(bg)
    min_x, min_y, w, h = rect
    if (manifest["canvas"]["x"], manifest["canvas"]["y"]) != (min_x, min_y):
        raise ValueError("manifest doesn't match this field; re-export it")
    names = assign_sheets(bg, rect)
    notes = []

    if scale is None:
        probe = Path(flat) if flat else None
        if probe is None:
            edited = [sheets_dir / f"{n}.png" for n in sorted(set(names))]
            probe = max((p for p in edited if p.exists()), key=lambda p: Image.open(p).size[0])
        s = _infer_scale(probe, w, h)
        scale = max(1, round(s))
        if abs(s - scale) > 0.01:
            notes.append(f"{probe.name} is {s:.2f}x the original size; resampling to {scale}x")
    size = (w * scale, h * scale)

    flat_px = _load_rgba(flat, size) if flat else None
    cache = PageCache(bg)
    sheet_px = {}

    def sheet(name):
        if name not in sheet_px:
            p = sheets_dir / f"{name}.png"
            if p.exists():
                sheet_px[name] = _load_rgba(p, size)
            else:
                notes.append(f"{name}.png missing; using original art")
                sheet_px[name] = None
        return sheet_px[name]

    originals = {}

    def original_up(name):
        if name not in originals:
            canvases = sheet_canvases(bg, rect, names, cache)
            originals.update({n: upscale_rgba(c, scale) for n, c in canvases.items()})
        return originals[name]

    tile_sheet = dict(zip(map(id, bg.tiles), names))

    def block_for(t):
        name = tile_sheet[id(t)]
        s = t.size * scale
        x, y = (t.dst_x - min_x) * scale, (t.dst_y - min_y) * scale
        src = flat_px if (flat_px is not None and is_base(t)) else sheet(name)
        if src is None:
            return original_up(name)[y : y + s, x : x + s]
        block = src[y : y + s, x : x + s].copy()
        if mask == "original":
            block[..., 3] = np.minimum(block[..., 3], tile_alpha(cache.tile(t), scale))
        return block

    pages = swizzle(bg, scale, block_for)
    written = write_pages(bg.name, pages, mod_root)
    # Preview of the result, composited exactly the way the pages will be sampled in game.
    render_composite(bg, rect, page_tile_source(pages, scale), scale).save(work_dir / "preview_modded.png")
    return scale, written, notes
