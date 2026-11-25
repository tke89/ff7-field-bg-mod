"""Texture pages for Demaster (MaKiPL/FF8_demaster), which loads the Remaster's field art from
DEMASTER_EXP/textures/field_bg/<first 2 letters>/<field>/<field>_<page>[_<slot>].png.

What we learned getting them in game (2026-11-13):
- A page file is the 256x256-texel window the game samples for that VRAM page (an 8-bit window spans two VRAM pages),
  at any scale (4x = 1024x1024).
- The game asks for <field>_<page>_<slot>, slot = 0, 2, 4... in drawing order, which can't be predicted from the
  files. Without one Demaster falls back to <field>_<page>, so only those are written, each holding every palette's
  tiles in their own spots. <field>_0 must exist or the field isn't replaced at all.
- The game cuts by alpha (drawn or not), so alpha is made on/off here; the engine's soft tile edges become a smooth
  outline rather than 4-pixel steps.

Inside a version (and dist) the pages sit at field/<field>/<field>_<page>.png, as FF7's do; install_target maps them
into Demaster's folders.
"""
from pathlib import Path

import numpy as np
from PIL import Image

ALPHA_CUT = 128


def write_pages(name, pages, mod_root):
    """pages: {engine page id (VRAM page | depth << 4): RGBA at the build's scale} -> one PNG per VRAM page. A page
    used at two colour depths holds both depths' tiles (written 4-bit first, 8-bit over it where it has art)."""
    out = Path(mod_root) / "field" / name
    out.mkdir(parents=True, exist_ok=True)
    merged = {}
    for tex in sorted(pages):
        page, img = tex & 0xF, pages[tex]
        if page not in merged:
            merged[page] = img.copy()
        else:
            m = img[..., 3] > 0
            merged[page][m] = img[m]
    if 0 not in merged:
        merged[0] = np.zeros_like(next(iter(merged.values())))
    written = []
    for page, img in sorted(merged.items()):
        img[..., 3] = np.where(img[..., 3] >= ALPHA_CUT, 255, 0).astype(np.uint8)
        p = out / f"{name}_{page}.png"
        Image.fromarray(img, "RGBA").save(p, compress_level=6)
        written.append(p)
    return written


def load_pages(pages_dir, name, bg):
    """A version's pages back as the engine's {page id: RGBA}, for the viewer's in-game view."""
    out = {}
    for tex in bg.pages:
        p = Path(pages_dir) / f"{name}_{tex & 0xF}.png"
        if p.exists():
            out[tex] = np.asarray(Image.open(p).convert("RGBA"))
    return out


def demaster_dir(game_dir):
    return Path(game_dir) / "DEMASTER_EXP" / "textures" / "field_bg"


def install_target(game_dir, rel):
    """field/<field>/<file> (as in dist) -> where Demaster reads it."""
    rel = Path(rel)
    field = rel.parts[1]
    return demaster_dir(game_dir) / field[:2] / field / rel.name
