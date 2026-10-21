"""Parse the background of a PC FF7 field file (palette + tiles + texture pages).

Format notes follow Makou Reactor (myst6re/makoureactor, src/core/field/Background*).
"""
import struct
from dataclasses import dataclass, field as dc_field

import numpy as np

SECTION_PALETTE = 3
SECTION_BACKGROUND = 8
TILE_STRIDE = 52
TEXTURE_SLOTS = 42
# Draw order key per layer (higher = further back), matching Makou Reactor.
LAYER_Z = {0: 4095, 2: 4096, 3: 0}


@dataclass
class Tile:
    index: int          # position within its layer
    layer: int
    dst_x: int
    dst_y: int
    src_x: int
    src_y: int
    tex: int            # texture page actually sampled
    palette: int
    z: int              # depth ID; layer 1 uses per-tile IDs for sprite occlusion
    param: int
    state: int
    blending: int
    type_trans: int
    size: int


@dataclass
class TexturePage:
    tex: int
    depth: int          # 0 = 4bpp, 1 = 8bpp paletted, 2 = 16bpp direct color
    big_tile: int
    data: bytes


@dataclass
class Background:
    name: str
    palettes: list                      # list of (256, 4) uint8 RGBA arrays
    transparency: list                  # per palette: index 0 is transparent
    tiles: list
    pages: dict = dc_field(default_factory=dict)


def _sections(data):
    (count,) = struct.unpack_from("<I", data, 2)
    offsets = struct.unpack_from(f"<{count}I", data, 6)
    out = []
    for off in offsets:
        (length,) = struct.unpack_from("<I", data, off)
        out.append(data[off + 4 : off + 4 + length])
    return out


def _decode_palettes(sec):
    (_size, _x, _y, _w, count) = struct.unpack_from("<IHHHH", sec, 0)
    raw = np.frombuffer(sec, dtype="<u2", count=count * 256, offset=12).reshape(count, 256)
    r = (raw & 31).astype(np.uint16)
    g = ((raw >> 5) & 31).astype(np.uint16)
    b = ((raw >> 10) & 31).astype(np.uint16)
    rgba = np.zeros((count, 256, 4), dtype=np.uint8)
    rgba[..., 0] = (r << 3) | (r >> 2)
    rgba[..., 1] = (g << 3) | (g >> 2)
    rgba[..., 2] = (b << 3) | (b >> 2)
    rgba[..., 3] = 255
    # Makou: a raw 0x0000 entry renders as palette color 0
    zero = raw == 0
    for p in range(count):
        rgba[p][zero[p]] = rgba[p][0]
    return [rgba[p] for p in range(count)]


def _read_tiles(sec, offset, count, layer):
    tiles = []
    for i in range(count):
        base = offset + i * TILE_STRIDE
        dst_x, dst_y = struct.unpack_from("<hh", sec, base)
        src_x, src_y, src_x2, src_y2 = sec[base + 8], sec[base + 10], sec[base + 12], sec[base + 14]
        palette = sec[base + 20]
        (tile_id,) = struct.unpack_from("<H", sec, base + 22)
        param, state, blending = sec[base + 24], sec[base + 25], sec[base + 26]
        type_trans = sec[base + 28]
        tex, tex2 = sec[base + 30], sec[base + 32]
        if abs(dst_x) >= 1024 or abs(dst_y) >= 1024:
            continue  # unused tiles parked off-screen (e.g. DestX = 10000)
        use2 = layer > 0 and blending
        tiles.append(Tile(
            index=i,
            layer=layer,
            dst_x=dst_x,
            dst_y=dst_y,
            src_x=src_x2 if use2 else src_x,
            src_y=src_y2 if use2 else src_y,
            tex=tex2 if use2 else tex,
            palette=palette,
            z=LAYER_Z.get(layer, tile_id),
            param=param if layer > 0 else 0,
            state=state if layer > 0 else 0,
            blending=blending if layer > 0 else 0,
            type_trans=type_trans,
            size=32 if layer > 1 else 16,
        ))
    return tiles


def parse_background(name, data):
    secs = _sections(data)
    pal_sec = secs[SECTION_PALETTE]
    bg = secs[SECTION_BACKGROUND]

    palettes = _decode_palettes(pal_sec)
    transparency = [bool(b) for b in bg[12 : 12 + 24]]

    tiles = []
    (n,) = struct.unpack_from("<H", bg, 44)
    tiles += _read_tiles(bg, 52, n, 0)
    pos = 52 + n * TILE_STRIDE

    for layer, tile_start in ((1, 27), (2, 21), (3, 21)):
        exists = bg[pos]
        if exists:
            (n,) = struct.unpack_from("<H", bg, pos + 5)
            tiles += _read_tiles(bg, pos + tile_start, n, layer)
            pos = pos + tile_start + n * TILE_STRIDE
        else:
            pos += 1

    if bg[pos : pos + 7] != b"TEXTURE":
        raise ValueError(f"{name}: TEXTURE marker not found at {pos}")
    pos += 7

    pages = {}
    for tex in range(TEXTURE_SLOTS):
        (exists,) = struct.unpack_from("<H", bg, pos)
        pos += 2
        if not exists:
            continue
        big, depth = struct.unpack_from("<HH", bg, pos)
        pos += 4
        size = 32768 if depth == 0 else depth * 65536
        pages[tex] = TexturePage(tex, depth, big, bg[pos : pos + size])
        pos += size

    return Background(name, palettes, transparency, tiles, pages)


def page_rgba(bg, tex, palette):
    """Decode a 256x256 texture page to RGBA using the given palette."""
    page = bg.pages[tex]
    if page.depth == 2:
        raw = np.frombuffer(page.data, dtype="<u2").reshape(256, 256)
        b = raw & 31
        g = (raw >> 6) & 31
        r = raw >> 11
        out = np.zeros((256, 256, 4), dtype=np.uint8)
        out[..., 0] = (r << 3) | (r >> 2)
        out[..., 1] = (g << 3) | (g >> 2)
        out[..., 2] = (b << 3) | (b >> 2)
        out[..., 3] = np.where(raw == 0, 0, 255)
        out[raw == 0x821, :3] = 0
        return out
    if page.depth == 1:
        idx = np.frombuffer(page.data, dtype=np.uint8).reshape(256, 256)
    else:
        packed = np.frombuffer(page.data, dtype=np.uint8).reshape(256, 128)
        idx = np.empty((256, 256), dtype=np.uint8)
        idx[:, 0::2] = packed & 0xF
        idx[:, 1::2] = packed >> 4
    pal = bg.palettes[palette] if palette < len(bg.palettes) else bg.palettes[0]
    out = pal[idx].copy()
    if palette < len(bg.transparency) and bg.transparency[palette]:
        out[idx == 0, 3] = 0
    return out
