"""Resize: finished versions enlarged by an upscaler of the user's choice, with no AI repaint (2026-11-19, the user:
"double the resolution using the upscaler of my choice on every existing final field").

A version is its render plus the state panels its build cuts in (doors, lights, signs: states.json in its states
folder, saved at the render's scale). Resizing makes a new version from it: the render enlarged to the new scale,
and every state panel enlarged by the same upscaler so a door that opens in the game matches the picture around it.
The panels go through the upscaler packed onto a few sheets (hyou5_2 has 280 of them), each with its own edges
spread into the gaps around it so the upscaler sees no black seams, and are cut out again at the new scale. The
Studio then builds the new version (studio.py run_resize).
"""
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MAX_SCALE = 10   # the Studio's own biggest output scale
MAX_MP = 120     # no render bigger than this (megapixels): the build and the game's textures get slow past it
GUTTER = 16      # pixels (at the old scale) between packed state panels, filled from each panel's own edges
SHEET_W = 2048   # state panels are packed onto sheets this wide (old-scale pixels)...
SHEET_MP = 4.0   # ...and up to about this big, each enlarged in one go

HOWS = {"x2": ("by", 2), "x3": ("by", 3), "x4": ("by", 4),
        "to4": ("to", 4), "to6": ("to", 6), "to8": ("to", 8), "to10": ("to", 10)}


def new_scale(old, how):
    """The scale a version at `old`x goes to (capped at MAX_SCALE)."""
    kind, n = HOWS[how]
    return min(MAX_SCALE, old * n if kind == "by" else n)


def fit(fw, fh, scale):
    """The biggest scale up to `scale` whose render (fw x fh at 1x, margins included) stays within MAX_MP."""
    return min(scale, int(math.sqrt(MAX_MP * 1e6 / max(1, fw * fh))))


def _pack(sizes, align):
    """Shelf packing of panels (w, h) with their gutters: [(sheet, x, y)] of each panel's own top left, and the
    sheets' sizes. Positions are multiples of `align` (the old scale), so they land on whole pixels at the new one."""
    def up(v):
        return -(-v // align) * align

    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    places, sheets = [None] * len(sizes), []
    x = y = row_h = 0
    for i in order:
        w, h = sizes[i]
        cw, chh = up(w + 2 * GUTTER), up(h + 2 * GUTTER)
        if not sheets:
            sheets.append([0, 0])
        if x and x + cw > max(SHEET_W, cw):  # next row
            x, y, row_h = 0, y + row_h, 0
        if y and (max(sheets[-1][0], x + cw) * (y + chh) > SHEET_MP * 1e6):  # next sheet
            sheets.append([0, 0])
            x = y = row_h = 0
        places[i] = (len(sheets) - 1, x + up(GUTTER), y + up(GUTTER))
        x += cw + up(GUTTER)
        row_h = max(row_h, chh + up(GUTTER))
        sheets[-1][0] = max(sheets[-1][0], x)
        sheets[-1][1] = max(sheets[-1][1], y + row_h)
    return places, [tuple(s) for s in sheets]


def enlarge_states(src_dir, dst_dir, old, new, enlarge, progress=lambda i, n: None):
    """Copy a version's saved state panels (states.json and its images) from scale old to new: each panel enlarged
    by enlarge(img, w, h) -> img, through a few packed sheets. Returns how many panels."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    meta = json.loads((src_dir / "states.json").read_text())
    if meta.get("scale") != old:
        raise ValueError(f"its state panels are at {meta.get('scale')}x, its render at {old}x: Redo states first")
    entries = meta["panels"]
    imgs = [np.asarray(Image.open(src_dir / e["file"]).convert("RGB")) for e in entries]
    places, sheets = _pack([(im.shape[1], im.shape[0]) for im in imgs], old)
    f = new / old
    dst_dir.mkdir(parents=True, exist_ok=True)
    for si, (sw, sh) in enumerate(sheets):
        progress(si, len(sheets))
        sheet = np.zeros((sh, sw, 3), np.uint8)
        mine = [i for i, p in enumerate(places) if p[0] == si]
        for i in mine:  # each panel with its edges spread into the gutter around it
            _, x, y = places[i]
            im = imgs[i]
            padded = cv2.copyMakeBorder(im, GUTTER, GUTTER, GUTTER, GUTTER, cv2.BORDER_REPLICATE)
            sheet[y - GUTTER:y + im.shape[0] + GUTTER, x - GUTTER:x + im.shape[1] + GUTTER] = padded
        big = enlarge(sheet, round(sw * f), round(sh * f))
        for i in mine:
            _, x, y = places[i]
            w, h = round(imgs[i].shape[1] * f), round(imgs[i].shape[0] * f)  # (a panel at the picture's edge is cut short)
            X, Y = round(x * f), round(y * f)
            Image.fromarray(np.ascontiguousarray(big[Y:Y + h, X:X + w])).save(dst_dir / entries[i]["file"])
    for p in src_dir.iterdir():  # anything else the folder keeps comes along as it is
        if p.is_file() and p.name != "states.json" and not (dst_dir / p.name).exists():
            shutil.copy2(p, dst_dir / p.name)
    (dst_dir / "states.json").write_text(json.dumps(dict(meta, scale=new)))
    return len(entries)


def workflow(image_name, upscaler, w, h, prefix):
    """A ComfyUI graph: the uploaded image enlarged by an upscale model, then resized exactly to w x h."""
    return {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "upm": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscaler}},
        "up": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["upm", 0], "image": ["img", 0]}},
        "fit": {"class_type": "ImageScale", "inputs": {"image": ["up", 0], "upscale_method": "lanczos", "width": w,
                                                       "height": h, "crop": "disabled"}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["fit", 0], "filename_prefix": prefix}},
    }
