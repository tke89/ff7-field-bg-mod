"""SYW Unified Field Textures as the original art's enlargement (input upscaler "syw").

The SYW Unified pack (Tsunamods, for 7th Heaven) is a ready-made, faithful 4x of every field's background pages:
cleaner than an upscaling model on 1x art, with readable signs. With "syw" as the input upscaler the AI starts from
the field's view put together from SYW's pages (the same tiles the original input shows, at 4x) instead of the
original art enlarged by a model. Fields it doesn't have use the default upscaler.

The pack stays where 7th Heaven keeps it: its .iro archive (or an unpacked copy) is read in place, nothing is
copied out but each field's assembled view (ai/input_syw, a cache). Its textures are Square Enix's art upscaled by
the SYW team: for use on this PC; nothing made from them is the Studio's to share.
"""
import functools
import io
import json
import re
import struct
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .sheets import canvas_rect, render_composite

NAME = "syw"
SCALE = 4
FALLBACK = "4x-UltraSharp.safetensors"  # fields the pack doesn't have
# its folders, as 7th Heaven layers them with Advanced Texture Animations on (mod.xml): the pages (V5), their
# palette animation frames named by the palette state's hash as FFNx looks them up (V5AA), and pages redone to go
# with those animations, which replace V5's (V5AAF: blin68_2, datiao_8, jtempl, lastmap, trnad_1)
PAGE = re.compile(r"(?:.*/)?FF7 SYW (V5|V5AAF)/field/([^/]+)/\2_(\d+)_(\d+)\.dds$", re.I)
FRAME = re.compile(r"(?:.*/)?FF7 SYW V5AA/field/([^/]+)/\1_(\d+)_(\d+)_([0-9a-f]+)\.dds$", re.I)
_lock = threading.Lock()
_index = {}  # pack path -> (stamp, pages {field: {(page, palette): where}}, frames {field: {(page, palette): {hash: where}}})


def find_pack(pref=None):
    """The pack: `pref` (an .iro file or an unpacked folder) if it exists, else the newest SYW Unified Fields
    archive in 7th Heaven's library (its default place, ~/7th Heaven), or None."""
    if pref and Path(pref).exists():
        return Path(pref)
    lib = Path.home() / "7th Heaven"
    if not lib.is_dir():
        return None
    found = sorted(lib.glob("*SYW_Unified_Fields_Textures*.iro"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def _iro_entries(path):
    """A 7th Heaven .iro archive's directory (version 0x10002): [(name, offset, length)]."""
    with open(path, "rb") as f:
        magic, _, _, _, count = struct.unpack("<4sIIII", f.read(20))
        if magic != b"IROS":
            raise ValueError(f"{path} isn't a 7th Heaven .iro archive")
        out = []
        for _ in range(count):
            size, nlen = struct.unpack("<HH", f.read(4))
            name = f.read(nlen).decode("utf-16-le")
            fflags, offset, length = struct.unpack_from("<IqI", f.read(size - 4 - nlen))
            if fflags & 0x2:  # compressed: SYW's 1.03 stores everything plainly; anything else is left out
                continue
            out.append((name.replace("\\", "/"), offset, length))
        return out


def _read(pack):
    """(pages, frames) of the pack, read once (an .iro's directory, or a folder's files)."""
    pack = Path(pack)
    stamp = pack.stat().st_mtime
    with _lock:
        hit = _index.get(str(pack))
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
        pages, fixed, frames = {}, {}, {}
        if pack.is_dir():
            items = [(p.relative_to(pack).as_posix(), p) for p in pack.rglob("*.dds")]
        else:
            items = [(n, (o, ln)) for n, o, ln in _iro_entries(pack)]
        for name, where in items:
            m = PAGE.match(name)
            if m:
                (fixed if m[1].upper() == "V5AAF" else pages).setdefault(m[2].lower(), {})[(int(m[3]), int(m[4]))] = where
                continue
            m = FRAME.match(name)
            if m:
                frames.setdefault(m[1].lower(), {}).setdefault((int(m[2]), int(m[3])), {})[int(m[4], 16)] = where
        for field, fix in fixed.items():
            pages.setdefault(field, {}).update(fix)
        _index[str(pack)] = (stamp, pages, frames)
        return pages, frames


def index(pack):
    """{field: {(page, palette): where}}: the pack's pages."""
    return _read(pack)[0]


def frames(pack):
    """{field: {(page, palette): {palette state hash: where}}}: the pack's palette animation frames."""
    return _read(pack)[1]


def status(pack):
    """For the page: where the pack is and how many fields it has, or None."""
    if pack is None:
        return None
    try:
        return {"path": str(pack), "fields": len(index(pack))}
    except (OSError, ValueError, struct.error):
        return None


@functools.lru_cache(maxsize=48)  # pages decoded lately (4 MB each): the viewer and a render ask again
def _decoded(pack, where):
    if isinstance(where, Path):
        data = where.read_bytes()
    else:
        offset, length = where
        with open(pack, "rb") as f:
            f.seek(offset)
            data = f.read(length)
    px = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    px.flags.writeable = False  # shared
    return px


def page(pack, where, scale=SCALE):
    """A page (RGBA, 256 x 256 original pixels) at `scale`."""
    px = _decoded(str(pack), where)
    if scale == SCALE:
        return px
    return np.asarray(Image.fromarray(px, "RGBA").resize((256 * scale,) * 2, Image.BOX))


def tile_source(pack, name, bg, scale=SCALE, swap=None):
    """render_composite's tile source from SYW's pages at `scale` (4: as they are), or None when the pack doesn't
    have the field. swap(t): another page for a tile (a palette animation frame), or None. A tile whose page SYW
    lacks (or lacks in that palette) comes from the original, enlarged."""
    pages = index(pack).get(name.lower())
    if not pages:
        return None
    loaded = {}
    from .sheets import PageCache
    original = PageCache(bg)

    def tile(t):
        s = t.size * scale
        px = swap(t) if swap else None
        if px is None:
            k = (t.tex, t.palette) if (t.tex, t.palette) in pages else (t.tex, 0)
            if k in pages:
                if k not in loaded:
                    loaded[k] = page(pack, pages[k], scale)
                px = loaded[k]
        if px is not None:
            px = px[t.src_y * scale:t.src_y * scale + s, t.src_x * scale:t.src_x * scale + s]
            if px.shape[:2] == (s, s):
                return px
        return cv2.resize(np.asarray(original.tile(t)), (s, s), interpolation=cv2.INTER_CUBIC)
    return tile


def view(pack, name, bg, shows, pad=(0, 0, 0, 0), cache_dir=None, stamp=""):
    """The field's view at 4x from SYW's pages: the tiles `shows` lets through (the original input's), padded as the
    input is (pad: 1x pixels, a blurred mirror), RGB; None when the pack doesn't have the field. Cached in cache_dir
    by `stamp`."""
    if not index(pack).get(name.lower()):
        return None
    key = json.dumps([2, str(pack), Path(pack).stat().st_mtime, list(pad), stamp])  # 2: V5AAF pages over V5
    if cache_dir:
        cache_dir = Path(cache_dir)
        img_path, key_path = cache_dir / f"{name}.png", cache_dir / f"{name}.json"
        if img_path.exists() and key_path.exists() and key_path.read_text() == key:
            return np.asarray(Image.open(img_path).convert("RGB"))
    tile = tile_source(pack, name, bg)
    img = np.asarray(render_composite(bg, canvas_rect(bg), tile, SCALE, shows).convert("RGB"))
    if any(pad):
        from .ai import _pad
        img = _pad(img, *(SCALE * p for p in pad))
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(img_path, compress_level=1)
        key_path.write_text(key)
    return img
