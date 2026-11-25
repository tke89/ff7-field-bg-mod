"""An FF8 field background (.map tiles + .mim texture pages), and the same field in the tile model the Studio's
painting engine uses (ff7bg.field.Background), so renders, the states pass, signs and touch-ups work unchanged.

Format (checked against Deling, myst6re's FF8 field editor, src/files/BackgroundFile.cpp):
- .mim, 438272 bytes: 16 palettes of 256 colours at 4096, then 256 rows of 1664 bytes (13 VRAM pages of 128 bytes)
  at 12288. A tile's texture window starts at its page and is 256 texels wide in its own colour depth, so a 4-bit
  window is one VRAM page, an 8-bit one two. Colour 0x0000 is see-through.
- .map: 16-byte tiles until x = 0x7FFF: x, y, z (int16, int16, uint16), a PSX tpage word (bits 0-3 page, 5-6 the
  semi-transparency mode, 7-8 colour depth), a CLUT word (bits 6-9 the palette), srcX, srcY, layer (7 bits),
  blendType, parameter, state.
- Blending is blendType alone: 0 average, 1 add, 2 subtract, 3 add a quarter, 4 none (Deling never reads the tpage's
  mode bits for it). Tiles draw far to near (descending z).
- Switches: parameter 255 is always drawn; any other parameter shows the tiles of its current state, one at a time.

Not read yet: the older layout (a 401408-byte .mim, a different tile layout; also signalled by blendType >= 60),
used only by test rooms and the logo screen (bccent12, bccent15, bvtr_107, logo, test10-12).
"""
import hashlib
import struct
from dataclasses import dataclass

import numpy as np

from ff7bg.field import Background, TexturePage, Tile as StudioTile

from . import scripts

MIM_SIZE = 438272
MIM_PAL = 4096
MIM_TEX = 12288
MIM_STRIDE = 1664
ROWS = 256
TILE = 16
NO_PARAM = 255
# The Studio's page tests a state with 32-bit bit maths ("mask & state"), so a switch with more states than this is
# shown as several groups of this many (bg2f_1's 2F hall has 40)
STATES_PER_GROUP = 30
GROUP_BLOCK = 256  # group ids: parameter + 1 + GROUP_BLOCK * (state // STATES_PER_GROUP)

# Hand-set entry states where the scripts' answer is wrong: {field: {parameter: state or None (off)}}. feroad1 needed
# one before the scripts were read (2026-11-13); they now give its door close-up by themselves.
ENTRY_OVERRIDES = {}
MAX_ANIMATION_STEPS = 240  # longest animation order handed to the Studio


class Unsupported(Exception):
    """A field in a layout this reader doesn't handle yet."""


@dataclass
class Tile:
    x: int
    y: int
    z: int
    tex: int
    pal_word: int
    sx: int
    sy: int
    layer: int
    blend_type: int
    param: int
    state: int

    @property
    def page(self):
        return self.tex & 0xF

    @property
    def depth(self):
        return (self.tex >> 7) & 3

    @property
    def pal(self):
        return (self.pal_word >> 6) & 0xF

    @property
    def blended(self):
        return self.blend_type < 4


def read_tiles(mapdata):
    out = []
    for o in range(0, len(mapdata) - 15, 16):
        x, y, z, tex, pal, sx, sy, layer, bt, param, state = struct.unpack_from("<hhHHHBBBBBB", mapdata, o)
        if x == 0x7FFF:
            break
        out.append(Tile(x, y, z, tex, pal, sx, sy, layer & 0x7F, bt, param, state))
    if any(t.blend_type >= 60 for t in out):
        raise Unsupported("older tile layout")
    return out


def psx_rgba(c):
    """uint16 PSX colours -> RGBA uint8 (5 bits per channel, scaled like the game: x * 255 // 31); 0 is see-through."""
    c = c.astype(np.uint32)
    r = (c & 31) * 255 // 31
    g = ((c >> 5) & 31) * 255 // 31
    b = ((c >> 10) & 31) * 255 // 31
    a = np.where(c == 0, 0, 255)
    return np.stack([r, g, b, a], -1).astype(np.uint8)


class Field:
    """One field's background as the game stores it."""

    def __init__(self, name, files):
        self.name = name
        mims = files.find(f"/{name}.mim") or files.find(f"/{name}_en.mim")
        if not mims:
            raise Unsupported("no background")
        base = mims[0][:-4]
        self.lang = "_en" if base.endswith("_en") else ""  # art with writing in it: the English one
        self.mim = files.read(base + ".mim")
        if len(self.mim) != MIM_SIZE:
            raise Unsupported(f"older background layout ({len(self.mim)}-byte .mim)")
        self.map = files.read(base + ".map")
        self.tiles = read_tiles(self.map)
        if not self.tiles:
            raise Unsupported("no tiles")
        self._rows = np.frombuffer(self.mim, np.uint8, ROWS * MIM_STRIDE, MIM_TEX).reshape(ROWS, MIM_STRIDE)
        self._windows = {}
        self.jsm = None  # the field's scripts (the English ones), when they can be read
        jsms = files.find(f"/{name}_en.jsm") or files.find(f"/{name}.jsm")
        if jsms:
            try:
                self.jsm = scripts.Jsm(files.read(jsms[0]))
            except (ValueError, struct.error):
                pass
        self._script = None

    def key(self):
        """Equal keys draw identically: fields sharing a background become one job with copies."""
        return hashlib.sha1(self.mim + self.map).hexdigest()

    def palette(self, pal):
        return np.frombuffer(self.mim, np.uint16, 256, MIM_PAL + pal * 512)

    def window(self, page, depth, pal):
        """The 256x256-texel texture window a tile of this page and depth samples, as RGBA with palette pal."""
        key = (page, depth, pal if depth < 2 else 0)
        if key not in self._windows:
            width = 128 << depth  # bytes: 4-bit 128, 8-bit 256, 16-bit 512
            slab = np.zeros((ROWS, width), np.uint8)
            part = self._rows[:, page * 128 : page * 128 + width]
            slab[:, : part.shape[1]] = part
            if depth == 0:
                idx = np.empty((ROWS, 256), np.uint8)
                idx[:, 0::2] = slab & 15  # low nibble first
                idx[:, 1::2] = slab >> 4
                rgba = psx_rgba(self.palette(pal)[idx])
            elif depth == 1:
                rgba = psx_rgba(self.palette(pal)[slab])
            else:
                rgba = psx_rgba(slab.view("<u2"))
            self._windows[key] = rgba
        return self._windows[key]

    def tile_pixels(self, t):
        return self.window(t.page, t.depth, t.pal)[t.sy : t.sy + TILE, t.sx : t.sx + TILE]

    def groups(self):
        """{parameter: sorted states} of the switchable tiles."""
        g = {}
        for t in self.tiles:
            if t.param != NO_PARAM:
                g.setdefault(t.param, set()).add(t.state)
        return {k: sorted(v) for k, v in sorted(g.items())}

    def script_states(self):
        """(entry {param: state or None}, animations {param: [(state or None, frames)]}) from the field's scripts
        (scripts.analyze), or empty ones without readable scripts."""
        if self._script is None:
            self._script = ({}, {})
            if self.jsm is not None:
                try:
                    entry, anims, _ = scripts.analyze(self.jsm)
                    self._script = (entry, anims)
                except (IndexError, KeyError, TypeError):
                    pass
        return self._script

    def entry_states(self):
        """{parameter: state} shown on entering the field (switches that are off are left out): what the field's
        start-up scripts set (scripts.analyze), an animation's first frame, each untouched switch's lowest state,
        and ENTRY_OVERRIDES on top."""
        groups = self.groups()
        entry, anims = self.script_states()
        out = {p: s[0] for p, s in groups.items()}
        for p, s in entry.items():
            if p in out:
                out[p] = s if s in groups[p] else None  # a state with no tiles draws nothing
        for p, cycle in anims.items():
            if p in out:
                out[p] = next((s for s, _ in self.animation_cycle(p, cycle) if s is not None), out[p])
        out.update(ENTRY_OVERRIDES.get(self.name, {}))
        return {p: s for p, s in out.items() if s is not None}

    def animation_cycle(self, param, cycle):
        """An animation's [(state or None, frames)] with "every state" steps spelled out."""
        states = self.groups().get(param, [])
        out = []
        for s, f in cycle:
            if s == scripts.ALL_STATES:
                out.extend((x, f) for x in states)
            else:
                out.append((s if s in states else None, f))
        return out

    def animations(self):
        """{parameter: [(state or None, frames at 30 fps)]}: switches that keep changing on their own."""
        _, anims = self.script_states()
        groups = self.groups()
        return {p: self.animation_cycle(p, c) for p, c in anims.items() if p in groups}

    def moving_layers(self):
        """Layers other than 0 made only of see-through tiles that add or take away light: glows and clouds the game
        can slide about (feroad1's layer 4 is drifting cloud). They stay their own art over the painting: baked into
        it, the clouds showed as a hard-edged block that stopped suddenly (2026-11-13)."""
        by = {}
        for t in self.tiles:
            if t.layer:
                by.setdefault(t.layer, []).append(t.blend_type in (1, 2, 3))
        return {layer for layer, v in by.items() if all(v)}


def group_of(param, state):
    """The Studio's (group, state bit) for an FF8 switch state."""
    return param + 1 + GROUP_BLOCK * (state // STATES_PER_GROUP), 1 << (state % STATES_PER_GROUP)


def ff8_state(group, bit):
    """Back from the Studio's (group, state bit) to FF8's (parameter, state)."""
    block, param = divmod(group - 1, GROUP_BLOCK)
    return param, block * STATES_PER_GROUP + bit.bit_length() - 1


def studio_defaults(field):
    """The entry view as the Studio's {group: state mask}."""
    out = {}
    for p, s in field.entry_states().items():
        g, bit = group_of(p, s)
        out[g] = out.get(g, 0) | bit
    return out


def studio_animations(field):
    """Animations as the Studio's jobs hold them (FF7's scripts.analyze): {group: {"order": [state masks, 0 for a
    step with the switch off], "wait": frames per step}}. A step held longer is repeated; a switch whose states run
    past STATES_PER_GROUP animates in the group of its first state."""
    out = {}
    for p, cycle in field.animations().items():
        cycle = [(s, f) for s, f in cycle if f > 0]
        if not cycle:
            continue
        wait = max(1, min(f for _, f in cycle))
        group = group_of(p, next((s for s, _ in cycle if s is not None), 0))[0]
        order = []
        for s, f in cycle:
            g, bit = group_of(p, s) if s is not None else (group, 0)
            order += [bit if g == group else 0] * max(1, round(f / wait))
        if len(set(order)) > 1:
            out[str(group)] = {"order": order[:MAX_ANIMATION_STEPS], "wait": wait}
    return out


@dataclass
class Ff8Background(Background):
    """The engine's Background, with FF8's own texture decoding (ff7bg.field.page_rgba asks it)."""

    source: object = None
    # FF8 stacks switches as layers: the states pass only paints a spot without another group drawn over it when that
    # group moves (states.run)
    occluders: str = "animated"
    # FF8 states mix scenery and their own shading: every state's light and shadow tiles come out of the art they're
    # painted into (ai.build_pages), not only states made of nothing else
    lights: str = "any state"
    # whole scenes sit in switched groups: the states on at entry take the main render (where sign fixes and touch-ups
    # land), not their panels (ai.build_pages)
    entry_from_main: bool = True

    def decode_page(self, tex, palette):
        return self.source.window(tex & 0xF, tex >> 4, palette)


def virtual_tex(t):
    """The engine's page id for a tile: its VRAM page and colour depth (114 fields use one page at two depths)."""
    return t.page | (t.depth << 4)


def studio_background(field):
    """The field in the engine's tile model:
    - every tile is a 16-pixel tile on layer 1 (the walk-behind layer: in FF8 every tile hides characters by its own
      depth) with its own z, except the moving see-through layers, which go on layer 2 (the scrolling layers: kept out
      of the painting and carried as their own art, like FF7's scrolling glows);
    - parameter 255 is static (group 0); a switch state is a group and a state bit (group_of);
    - pages are the (VRAM page, depth) windows, decoded by Ff8Background.decode_page.
    Tiles are listed with the ones hidden at entry first, then the shown ones far to near (file order among equal z):
    where tiles share texture space the one on screen is written last, and wins."""
    moving = field.moving_layers()
    entry = field.entry_states()
    shown = lambda t: t.param == NO_PARAM or entry.get(t.param) == t.state
    order = sorted(range(len(field.tiles)), key=lambda i: (shown(field.tiles[i]), -field.tiles[i].z))
    tiles, pages = [], {}
    for i in order:
        t = field.tiles[i]
        group, bit = group_of(t.param, t.state) if t.param != NO_PARAM else (0, 0)
        tex = virtual_tex(t)
        pages.setdefault(tex, TexturePage(tex, t.depth, 0, b""))
        tiles.append(StudioTile(
            index=i, layer=2 if t.layer in moving else 1, dst_x=t.x, dst_y=t.y, src_x=t.sx, src_y=t.sy, tex=tex,
            palette=t.pal, z=t.z, param=group, state=bit, blending=1 if t.blended else 0,
            type_trans=t.blend_type if t.blended else 0, size=TILE))
    return Ff8Background(field.name, [], [], tiles, pages, source=field)
