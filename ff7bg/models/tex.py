"""FF7 PC TEX textures (the models' textures in char.lgp, battle.lgp, magic.lgp, direct/): read to RGBA per palette.

Header (0xEC bytes of uint32): 0x08 colour key flag, 0x30 palettes, 0x34 colours per palette, 0x3C width, 0x40 height,
0x4C palette flag, 0x58 palette size (all palettes' colours), 0x68 bytes per pixel, 0x6C-0x98 the truecolour
format's bit counts, masks and shifts, 0xBC colour key array flag. Then the palettes (BGRA), the pixels (indices, or
truecolour), and with the key array flag one byte per palette."""
import struct
from dataclasses import dataclass, field

import numpy as np

HEADER = 0xEC


@dataclass
class Tex:
    width: int
    height: int
    palettes: list = field(default_factory=list)  # [(colours, 4) uint8 RGBA], empty for truecolour
    indices: np.ndarray = None                     # (h, w) uint8 palette indices, or None
    pixels: np.ndarray = None                      # (h, w, 4) uint8 RGBA for truecolour, or None
    color_key: bool = False                        # black is transparent (FFNx: colour key)
    key_per_palette: list = None                   # the key array: per palette, keyed or not

    @property
    def count(self):
        """How many palettes (FFNx's `_%02i` overrides): 1 for truecolour."""
        return max(1, len(self.palettes))

    def rgba(self, palette=0):
        """(h, w, 4) uint8 in one palette, colour-keyed black made transparent."""
        if self.indices is None:
            out = self.pixels.copy()
            keyed = self.color_key
        else:
            pal = self.palettes[min(palette, len(self.palettes) - 1)]
            out = pal[np.minimum(self.indices, len(pal) - 1)].copy()
            out[..., 3] = 255
            keyed = self.color_key or bool(self.key_per_palette and self.key_per_palette[min(palette, len(self.key_per_palette) - 1)])
        if keyed:
            out[(out[..., :3] == 0).all(-1), 3] = 0
        return out


def read(data):
    """Tex from a .tex file's bytes; ValueError for anything else (battle.lgp's files have no extensions)."""
    if len(data) < HEADER or struct.unpack_from("<I", data, 0)[0] != 1:
        raise ValueError("not a TEX file")
    v = struct.unpack_from("<59I", data, 0)
    npal, cpp, w, h, palflag, palsize, bypp = v[12], v[13], v[15], v[16], v[19], v[22], v[26]
    if not (0 < w <= 4096 and 0 < h <= 4096 and bypp in (1, 2, 3, 4)):
        raise ValueError("not a TEX file")
    off = HEADER
    t = Tex(w, h, color_key=bool(v[2]))
    if palflag:
        pal = np.frombuffer(data, np.uint8, palsize * 4, off).reshape(-1, 4)[:, [2, 1, 0, 3]]
        off += palsize * 4
        cpp = cpp or palsize
        # the palettes the file holds: battle textures give their model's whole PSX palette bank as the count
        # (2-8) but store only their own row's colours
        t.palettes = [p for p in (pal[i * cpp:(i + 1) * cpp] for i in range(max(1, npal))) if len(p)] or [pal]
    n = w * h * bypp
    if len(data) < off + n:
        raise ValueError("TEX file cut short")
    px = np.frombuffer(data, np.uint8, n, off)
    off += n
    if palflag:
        t.indices = px.reshape(h, w) if bypp == 1 else px.view("<u2").reshape(h, w).astype(np.uint8)
    else:
        word = {1: px, 2: px.view("<u2"), 3: None, 4: px.view("<u4")}[bypp]
        if word is None:  # 24-bit: BGR
            rgb = px.reshape(h, w, 3)[..., ::-1]
            t.pixels = np.concatenate([rgb, np.full((h, w, 1), 255, np.uint8)], -1)
        else:
            word = word.astype(np.uint32).reshape(h, w)
            chans = []
            for i in range(4):  # red, green, blue, alpha
                bits, mask, shift = v[27 + i], v[31 + i], v[35 + i]
                if not bits or not mask:
                    chans.append(np.full((h, w), 255, np.uint8))
                    continue
                c = (word & mask) >> shift
                chans.append(np.round(c * (255.0 / ((1 << bits) - 1))).astype(np.uint8))
            t.pixels = np.stack(chans, -1)
    if v[47] and palflag and len(data) >= off + max(1, npal):
        t.key_per_palette = list(data[off:off + max(1, npal)])
    return t


def is_tex(data):
    """Is it a whole TEX file (the header and the sizes agree)?"""
    if len(data) < HEADER or struct.unpack_from("<I", data, 0)[0] != 1:
        return False
    v = struct.unpack_from("<59I", data, 0)
    w, h, palflag, palsize, bypp = v[15], v[16], v[19], v[22], v[26]
    if not (0 < w <= 4096 and 0 < h <= 4096 and bypp in (1, 2, 3, 4)):
        return False
    body = HEADER + (palsize * 4 if palflag else 0) + w * h * bypp
    return body <= len(data) <= body + max(1, v[12]) + 4
