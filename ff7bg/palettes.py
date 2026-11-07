"""Palette effects: background colours that a field's scripts change while it runs.

FF7 animates some backgrounds without swapping tiles: a script copies a palette out of VRAM (STPAL),
changes the copy (MPPAL multiplies colours, ADPAL adds, RTPAL rotates, CPPAL copies), and loads it back
(LDPAL). Every tile drawn with that palette changes colour: Aerith's waterfall in eals_1 is two bright
bands moving through palette 8, one step every frame. A remastered page is a finished full-colour image,
so the effect is lost: the waterfall stands still.

Only LDPAL and LDPLS write to VRAM; the other opcodes work on the script's palette arrays. So a field
is affected when an LDPAL/LDPLS loads into a palette its background tiles use. When that happens:
  always  - in a script running from field entry (script 0's per-frame part, or a script it starts
            with REQ/REQSW/REQEW), inside a loop: an ambient animation (waterfall, flickering light).
  entry   - in script 0's init part: the colours are changed before the first frame and stay changed.
  event   - anywhere else: talking, touching, cutscenes, switches.
"""
import itertools
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .field import SECTION_PALETTE, _sections
from .scripts import _opcode_size

RET, REQ, REQSW, REQEW, JMPB, JMPBL, WAIT, RANDOM = 0x00, 0x01, 0x02, 0x03, 0x12, 0x13, 0x24, 0x99
LDPAL, LDPLS = 0xE6, 0xEC
NAMES = {0xDF: "MPPAL", 0xE5: "STPAL", 0xE6: "LDPAL", 0xE7: "CPPAL", 0xE8: "RTPAL", 0xE9: "ADPAL",
         0xEA: "MPPAL2", 0xEB: "STPLS", 0xEC: "LDPLS", 0xED: "CPPAL2", 0xEE: "RTPAL2", 0xEF: "ADPAL2"}


@dataclass
class Script:
    entity: int
    entity_name: str
    index: int
    start: int          # offset in the script section
    code: bytes
    init_end: int = -1  # script 0: offset (in code) of the RET ending the init part

    def ops(self):
        pos = 0
        while pos < len(self.code):
            n = _opcode_size(self.code, pos)
            yield pos, self.code[pos], self.code[pos : pos + n]
            pos += n


@dataclass
class Load:
    """One LDPAL/LDPLS: a palette loaded into VRAM."""
    script: Script
    pos: int
    palette: object     # VRAM palette id, or None when it comes from a variable
    colors: int
    kind: str = ""      # always / entry / event
    looped: bool = False
    random: bool = False
    ops: list = field(default_factory=list)  # palette opcodes in the same script


def scripts(raw_field):
    """Every distinct script of the field (script section 0)."""
    (off,) = struct.unpack_from("<I", raw_field, 6)
    (length,) = struct.unpack_from("<I", raw_field, off)
    sec = raw_field[off + 4 : off + 4 + length]
    entities = sec[2]
    (pos_texts,) = struct.unpack_from("<H", sec, 4)
    (n_akao,) = struct.unpack_from("<H", sec, 6)
    names = [sec[32 + 8 * e : 40 + 8 * e].split(b"\0")[0].decode("ascii", "replace") for e in range(entities)]
    pos_scripts = 32 + 8 * entities + 4 * n_akao
    pos_akao = struct.unpack_from("<I", sec, 32 + 8 * entities)[0] if n_akao else len(sec)
    end_all = min(pos_akao, pos_texts)
    table = [struct.unpack_from("<32H", sec, pos_scripts + 64 * e) for e in range(entities)]
    starts = sorted({p for row in table for p in row if p < end_all} | {end_all})
    out = []
    for e, row in enumerate(table):
        seen = set()
        for i, start in enumerate(row):
            if start >= end_all or (i and (start in seen or start == row[0])):
                continue
            seen.add(start)
            nxt = next(p for p in starts if p > start)
            s = Script(e, names[e], i, start, sec[start:nxt])
            if i == 0:
                s.init_end = next((p for p, op, _ in s.ops() if op == RET), len(s.code))
            out.append(s)
    return out


def _started(all_scripts):
    """(entity, script) pairs running from field entry: script 0s, and what they start, transitively."""
    by_key = {(s.entity, s.index): s for s in all_scripts}
    running = {(s.entity, 0) for s in all_scripts if s.index == 0}
    todo = list(running)
    while todo:
        s = by_key.get(todo.pop())
        if not s:
            continue
        for _, op, b in s.ops():
            if op in (REQ, REQSW, REQEW) and len(b) >= 3:
                key = (b[1], b[2] & 0x1F)
                if key not in running:
                    running.add(key)
                    todo.append(key)
    return running


def _loops(s):
    """(start, end) code ranges inside a backward jump."""
    out = []
    for p, op, b in s.ops():
        if op == JMPB and len(b) >= 2:
            out.append((p - b[1], p))
        elif op == JMPBL and len(b) >= 3:
            out.append((p - struct.unpack_from("<H", b, 1)[0], p))
    return out


def loads(raw_field):
    """Every palette load into VRAM, classified (see the module docstring)."""
    all_scripts = scripts(raw_field)
    running = _started(all_scripts)
    out = []
    for s in all_scripts:
        ops = [(p, NAMES[op]) for p, op, _ in s.ops() if op in NAMES]
        if not ops:
            continue
        loops = _loops(s)
        rand = any(op == RANDOM for _, op, _ in s.ops())
        for p, op, b in s.ops():
            if op not in (LDPAL, LDPLS) or len(b) < 5:
                continue
            literal = (b[1] & 0x0F) == 0
            ld = Load(s, p, b[3] if literal else None, b[4] + 1, ops=ops, random=rand,
                      looped=any(a <= p <= e for a, e in loops))
            if (s.entity, s.index) not in running:
                ld.kind = "event"
            elif s.index == 0:
                ld.kind = "entry" if p < s.init_end else "always"  # after init, script 0 runs every frame
            else:
                ld.kind = "always" if ld.looped else "entry"
            out.append(ld)
    return out


def report(name, raw_field, bg, rect):
    """What a field's palette changes touch: one row per (palette, kind)."""
    _, _, cw, ch = rect
    rows = {}
    for ld in loads(raw_field):
        # a palette picked by a variable: which one isn't known without running the script
        tiles = [] if ld.palette is None else [t for t in bg.tiles if t.palette == ld.palette]
        if ld.palette is not None and not tiles:
            continue  # a palette no background tile uses
        key = ("var" if ld.palette is None else ld.palette, ld.kind)
        r = rows.setdefault(key, {
            "field": name, "palette": key[0], "kind": ld.kind, "looped": False, "random": False,
            "scripts": set(), "opcodes": set(), "tiles": len(tiles),
            "area": round(sum(t.size * t.size for t in tiles) / (cw * ch), 4),
            "pages": sorted({t.tex for t in tiles}), "layers": sorted({t.layer for t in tiles}),
            "blended": sum(1 for t in tiles if t.blending), "groups": sorted({t.param for t in tiles if t.param})})
        r["looped"] |= ld.looped
        r["random"] |= ld.random
        r["scripts"].add(f"{ld.script.entity_name}#{ld.script.index}")
        r["opcodes"].update(n for _, n in ld.ops)
    for r in rows.values():
        r["scripts"] = sorted(r["scripts"])
        r["opcodes"] = sorted(r["opcodes"])
    return list(rows.values())


# Replaying the scripts, for FFNx's animated textures.
#
# With enable_animated_textures, FFNx names each palette state of a page by a hash of the page as the game hands it
# over (32-bit BGRA, the current palette applied: page_bgra) and loads <map>_<page>_<palette>_<hash>.png when it
# exists. replay() runs a field's palette scripts and collects every palette they load, so those names can be
# worked out offline. Checked against FFNx's own logs on a tour of every affected field (2026-10-21).

_BANKS = {1: ("A", 1), 2: ("A", 2), 3: ("B", 1), 4: ("B", 2), 11: ("C", 1), 12: ("C", 2), 13: ("D", 1),
          14: ("D", 2), 15: ("E", 1), 7: ("E", 2), 5: ("T", 1), 6: ("T", 2)}  # memory, byte (1) or word (2)
_COND = {0: lambda a, b: a == b, 1: lambda a, b: a != b, 2: lambda a, b: a > b, 3: lambda a, b: a < b,
         4: lambda a, b: a >= b, 5: lambda a, b: a <= b, 6: lambda a, b: (a & b) != 0, 7: lambda a, b: (a ^ b) != 0,
         8: lambda a, b: (a | b) != 0, 9: lambda a, b: (a >> b) & 1 == 1, 10: lambda a, b: (a >> b) & 1 == 0}
_MATH = {0x80: lambda d, s: s, 0x76: lambda d, s: min(255, d + s), 0x78: lambda d, s: max(0, d - s),
         0x85: lambda d, s: d + s, 0x87: lambda d, s: d - s, 0x89: lambda d, s: d * s,
         0x8B: lambda d, s: d // s if s else 0, 0x8D: lambda d, s: d % s if s else 0,
         0x8F: lambda d, s: d & s, 0x91: lambda d, s: d | s, 0x93: lambda d, s: d ^ s}
_MATH2 = {0x81: lambda d, s: s, 0x77: lambda d, s: min(0xFFFF, d + s), 0x79: lambda d, s: max(0, d - s),
          0x86: lambda d, s: d + s, 0x88: lambda d, s: d - s, 0x8A: lambda d, s: d * s,
          0x8C: lambda d, s: d // s if s else 0, 0x8E: lambda d, s: d % s if s else 0}


def file_palettes(raw):
    """The field's palettes as stored: (count, 256) 15-bit colours plus the semi-transparency bit."""
    ps = _sections(raw)[SECTION_PALETTE]
    (count,) = struct.unpack_from("<H", ps, 10)
    return np.frombuffer(ps, dtype="<u2", count=count * 256, offset=12).reshape(count, 256).astype(np.int64)


def loaded_palettes(raw):
    """The palettes as the game holds them once the field is loaded: a 0x0000 colour past entry 0 already reads as
    the palette's entry 0, so STPAL copies that and the script's changes start from it. Taken as 0x0000 until
    2026-11-03 (page_bgra then showed the entry as the state's entry 0): wherever a script animates entry 0 and
    leaves such an entry alone, the hash was not the game's and FFNx never found the frame (mtnvl4's mist, page 16:
    entry 255 is 0x0000 in the file, ADPAL changes 0-254; FFNx's log showed it load the frames named this way)."""
    v = file_palettes(raw).copy()
    zero = v == 0
    zero[:, 0] = False
    v[zero] = np.broadcast_to(v[:, :1], v.shape)[zero]
    return v


def multiply(v, mr, mg, mb):
    """MPPAL/MPPAL2 on 15-bit colours, as the PC game does it: each channel times m/64, clamped, except that green
    and blue are masked one bit too wide, so the top bit of the channel below adds half a step (fitted to FFNx's
    dumps of blin1 and eals_1)."""
    r = np.minimum(((v & 0x001F) * mr) >> 6, 31)
    g = np.minimum((((v & 0x03F0) * mg) >> 6) >> 5, 31)
    b = np.minimum((((v & 0x7E00) * mb) >> 6) >> 10, 31)
    return (v & 0x8000) | b << 10 | g << 5 | r


def add(v, ar, ag, ab):
    """ADPAL/ADPAL2: a signed amount added to each 5-bit channel, clamped."""
    signed = lambda a: a - 256 if a > 127 else a
    ch = lambda shift, a: np.clip(((v >> shift) & 31) + signed(a), 0, 31) << shift
    return (v & 0x8000) | ch(10, ab) | ch(5, ag) | ch(0, ar)


class Machine:
    """The field's palette scripts, run frame by frame: script 0 of every entity that reaches a palette opcode
    (directly or through the scripts it requests), with REQ/REQSW/REQEW, WAIT, variables and the palette
    opcodes. Palette arrays are in units of 16 colours. loads collects every (palette, colours) LDPAL sets."""

    def __init__(self, raw, seed=0):
        self.vram = loaded_palettes(raw)
        self.arr = np.zeros(256 * 16, dtype=np.int64)
        self.mem = {g: bytearray(256) for g in "ABCDET"}
        self.rng = random.Random(seed)
        self.loads, self.unknown = [], set()
        everything = scripts(raw)
        self.by_key = {(s.entity, s.index): s for s in everything}

        def reaches(key, seen):
            s = self.by_key.get(key)
            if not s or key in seen:
                return False
            seen.add(key)
            for _, op, b in s.ops():
                if op in NAMES:
                    return True
                if op in (REQ, REQSW, REQEW) and len(b) >= 3 and reaches((b[1], b[2] & 0x1F), seen):
                    return True
            return False

        self.threads = [self._thread(s) for s in everything if s.index == 0 and reaches((s.entity, 0), set())]

    @staticmethod
    def _thread(s):
        return {"s": s, "pc": 0, "wait": 0, "done": False, "join": None}

    def get(self, bank, v):
        if bank == 0:
            return v
        g, size = _BANKS.get(bank, ("T", 1))
        m = self.mem[g]
        return m[v % 256] | (m[(v + 1) % 256] << 8) if size == 2 else m[v % 256]

    def put(self, bank, v, value):
        g, size = _BANKS.get(bank, ("T", 1))
        m = self.mem[g]
        if size == 2:
            m[v % 256], m[(v + 1) % 256] = value & 255, (value >> 8) & 255
        else:
            m[v % 256] = value & 255

    def _step(self, t, cap=2000):
        s, code = t["s"], t["s"].code
        for _ in range(cap):
            if t["pc"] >= len(code):
                t["done"] = True
                return
            pos = t["pc"]
            op = code[pos]
            b = code[pos : pos + _opcode_size(code, pos)]
            nxt = pos + len(b)
            hi, lo = (lambda k: b[k] >> 4), (lambda k: b[k] & 15)
            if op == RET:
                if s.index == 0:  # script 0: init, then its per-frame part, restarted each time it returns
                    t["pc"] = s.init_end + 1
                else:
                    t["done"] = True
                return
            elif op == 0x10:
                nxt = pos + 1 + b[1]
            elif op == 0x11:
                nxt = pos + 1 + struct.unpack_from("<H", b, 1)[0]
            elif op in (JMPB, JMPBL):  # a backward jump ends the script's turn this frame
                t["pc"] = pos - (b[1] if op == JMPB else struct.unpack_from("<H", b, 1)[0])
                return
            elif op in (0x14, 0x15):  # IFUB(L)
                if not _COND.get(b[4], lambda a, c: False)(self.get(hi(1), b[2]), self.get(lo(1), b[3])):
                    nxt = pos + 5 + (b[5] if op == 0x14 else struct.unpack_from("<H", b, 5)[0])
            elif op in (0x16, 0x17, 0x18, 0x19):  # IFSW(L) / IFUW(L)
                a, c = struct.unpack_from("<hh" if op < 0x18 else "<HH", b, 2)
                if not _COND.get(b[6], lambda a, c: False)(self.get(hi(1), a), self.get(lo(1), c)):
                    nxt = pos + 7 + (b[7] if op in (0x16, 0x18) else struct.unpack_from("<H", b, 7)[0])
            elif op == WAIT:
                t["wait"], t["pc"] = struct.unpack_from("<H", b, 1)[0], nxt
                return
            elif op in _MATH:
                self.put(hi(1), b[2], _MATH[op](self.get(hi(1), b[2]), self.get(lo(1), b[3])))
            elif op in _MATH2:
                self.put(hi(1), b[2], _MATH2[op](self.get(hi(1), b[2]),
                                                 self.get(lo(1), struct.unpack_from("<H", b, 3)[0])))
            elif op in (0x95, 0x96, 0x97, 0x98, RANDOM, 0x7A, 0x7B, 0x7C, 0x7D):  # INC!/DEC!/RANDOM/INC/DEC
                cur = self.get(lo(1), b[2])
                self.put(lo(1), b[2], {0x95: cur + 1, 0x96: cur + 1, 0x97: cur - 1, 0x98: cur - 1,
                                       RANDOM: self.rng.randrange(256), 0x7A: min(255, cur + 1),
                                       0x7B: min(0xFFFF, cur + 1), 0x7C: max(0, cur - 1), 0x7D: max(0, cur - 1)}[op])
            elif op == 0xE5:  # STPAL: palette -> array
                pal, p, n = self.get(hi(1), b[2]), self.get(lo(1), b[3]), b[4] + 1
                self.arr[p * 16 : p * 16 + n] = self.vram[pal % len(self.vram)][:n]
            elif op == LDPAL:  # array -> palette
                p, pal, n = self.get(hi(1), b[2]), self.get(lo(1), b[3]), b[4] + 1
                if pal < len(self.vram):
                    self.vram[pal][:n] = self.arr[p * 16 : p * 16 + n]
                    self.loads.append((pal, tuple(int(x) for x in self.vram[pal])))
            elif op == 0xE7:  # CPPAL
                src, dst, n = self.get(hi(1), b[2]), self.get(lo(1), b[3]), b[4] + 1
                self.arr[dst * 16 : dst * 16 + n] = self.arr[src * 16 : src * 16 + n].copy()
            elif op in (0xDF, 0xEA, 0xE9, 0xEF):  # MPPAL, MPPAL2, ADPAL, ADPAL2
                if op in (0xDF, 0xEF):  # literal arrays and a start colour
                    src, dst, start = b[4], b[5], self.get(hi(1), b[6])
                    kb, kg, kr = self.get(lo(1), b[7]), self.get(hi(2), b[8]), self.get(lo(2), b[9])
                    n = self.get(lo(3), b[10]) + 1
                else:
                    src, dst, start = self.get(hi(1), b[4]), self.get(lo(1), b[5]), 0
                    kb, kg, kr = self.get(hi(2), b[6]), self.get(lo(2), b[7]), self.get(hi(3), b[8])
                    n = b[9] + 1
                seg = self.arr[src * 16 + start : src * 16 + start + n].copy()
                self.arr[dst * 16 + start : dst * 16 + start + len(seg)] = (
                    multiply(seg, kr, kg, kb) if op in (0xDF, 0xEA) else add(seg, kr, kg, kb))
            elif op in NAMES:
                self.unknown.add(NAMES[op])
            elif op in (REQ, REQSW, REQEW) and len(b) >= 3:
                target = self.by_key.get((b[1], b[2] & 0x1F))
                if target is not None:
                    child = next((x for x in self.threads if x["s"] is target and not x["done"]), None)
                    if child is None:
                        child = self._thread(target)
                        self.threads.append(child)
                    if op == REQEW:  # wait until it ends
                        t["join"], t["pc"] = child, nxt
                        return
            t["pc"] = nxt
        t["wait"] = 1  # a loop with no WAIT: yield

    def run(self, frames):
        for _ in range(frames):
            self.threads = [t for t in self.threads if not t["done"]]
            for t in list(self.threads):
                if t["join"] is not None:
                    if not t["join"]["done"]:
                        continue
                    t["join"] = None
                if t["wait"] > 0:
                    t["wait"] -= 1
                    continue
                self._step(t)


def switches(raw):
    """Variables the palette scripts test whose values other scripts set (a light switch, a story flag), with
    every value written to them in the field: {(bank, address): values}."""
    m = Machine(raw)
    seen, todo, tested = set(), [t["s"] for t in m.threads], set()
    while todo:
        s = todo.pop()
        if (s.entity, s.index) in seen:
            continue
        seen.add((s.entity, s.index))
        for _, op, b in s.ops():
            if op in (0x14, 0x15) and len(b) > 3:
                tested |= {(b[1] >> 4, b[2])} if b[1] >> 4 else set()
                tested |= {(b[1] & 15, b[3])} if b[1] & 15 else set()
            elif op in (REQ, REQSW, REQEW) and len(b) >= 3 and (b[1], b[2] & 0x1F) in m.by_key:
                todo.append(m.by_key[(b[1], b[2] & 0x1F)])
    values = {k: {0} for k in tested}
    for s in scripts(raw):
        for _, op, b in s.ops():
            if op == 0x80 and len(b) > 3 and not b[1] & 15 and (b[1] >> 4, b[2]) in values:
                values[(b[1] >> 4, b[2])].add(b[3])
    return {k: v for k, v in values.items() if len(v) > 1}


def replay(raw, frames=12000, seeds=range(6)):
    """({palette: set of 256-colour tuples}, {opcodes not emulated}): every palette state the field's scripts
    load, trying each switch value (up to 64 combinations) and several random seeds."""
    sw = switches(raw)
    combos = list(itertools.product(*[sorted(v) for v in sw.values()]))[:64] or [()]
    states, unknown = {}, set()
    for combo in combos:
        for seed in seeds:
            m = Machine(raw, seed)
            for (bank, address), value in zip(sw, combo):
                m.put(bank, address, value)
            m.run(frames)
            unknown |= m.unknown
            for pal, colors in m.loads:
                states.setdefault(pal, set()).add(colors)
    return states, unknown


def page_bgra(bg, page, colors, transparent):
    """A page as FFNx hashes it: 256x256 BGRA, colours expanded as c*255/31, 0x8000 as (1, 1, 1), a 0x0000
    entry showing entry 0's colour, index 0 clear when the palette is transparent."""
    p = np.asarray(colors, dtype=np.int64)
    pal = np.stack([((p >> 10) & 31) * 255 // 31, ((p >> 5) & 31) * 255 // 31, (p & 31) * 255 // 31,
                    np.full(256, 255)], -1)
    pal[p == 0x8000] = (1, 1, 1, 255)
    pal[1:][p[1:] == 0] = pal[0]
    if transparent:
        pal[0] = 0
    pg = bg.pages[page]
    if pg.depth == 1:
        idx = np.frombuffer(pg.data, dtype=np.uint8).reshape(256, 256)
    else:
        packed = np.frombuffer(pg.data, dtype=np.uint8).reshape(256, 128)
        idx = np.empty((256, 256), dtype=np.uint8)
        idx[:, 0::2], idx[:, 1::2] = packed & 0xF, packed >> 4
    return pal.astype(np.uint8)[idx]


def animated_textures_cached(raw, bg, path):
    """animated_textures, kept in `path` (JSON): replaying the scripts takes seconds and a field never changes."""
    import json
    path = Path(path)
    if path.exists():
        data = json.loads(path.read_text())
        return {tuple(map(int, k.split("_"))): {int(h, 16): tuple(c) for h, c in v.items()} for k, v in data.items()}
    anim = animated_textures(raw, bg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({f"{p}_{q}": {f"{h:x}": list(map(int, c)) for h, c in v.items()}
                                for (p, q), v in anim.items()}))
    return anim


def animated_textures(raw, bg, frames=12000):
    """{(page, palette): {hash: colours}} for every palette state the scripts set, on every page drawn with it:
    FFNx loads field/<map>/<map>_<page>_<palette>_<hash:x>.png for each."""
    from .xxh3 import xxh3_64
    states, _ = replay(raw, frames)
    pages_of = {}
    for t in bg.tiles:
        if bg.pages[t.tex].depth < 2:  # 16-bit pages are direct colour: palettes don't touch them
            pages_of.setdefault(t.palette, set()).add(t.tex)
    out = {}
    for pal, ss in states.items():
        transparent = bg.transparency[pal] if pal < len(bg.transparency) else True
        for page in pages_of.get(pal, ()):
            out[(page, pal)] = {xxh3_64(page_bgra(bg, page, s, transparent).tobytes()): s for s in ss}
    return out


def palette_rgba(colours):
    """256 colours decoded like field._decode_palettes (the Studio's views): RGBA, a 0x0000 entry showing entry 0."""
    c = np.asarray(colours, dtype=np.uint16)
    rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1)
    out = np.empty((256, 4), np.uint8)
    out[:, :3] = (rgb << 3) | (rgb >> 2)
    out[:, 3] = 255
    out[c == 0] = out[0]
    return out


def page_rgba(bg, page, pal, colours):
    """A page as the Studio shows it (field.page_rgba), with palette `pal` set to `colours`."""
    pg = bg.pages[page]
    if pg.depth == 1:
        idx = np.frombuffer(pg.data, dtype=np.uint8).reshape(256, 256)
    else:
        packed = np.frombuffer(pg.data, dtype=np.uint8).reshape(256, 128)
        idx = np.empty((256, 256), dtype=np.uint8)
        idx[:, 0::2], idx[:, 1::2] = packed & 0xF, packed >> 4
    out = palette_rgba(colours)[idx]
    if pal < len(bg.transparency) and bg.transparency[pal]:
        out[idx == 0, 3] = 0
    return out


PREVIEW_FRAMES = 3600  # two minutes of play
PREVIEW_STATES = 160   # most states one preview group plays; rarer ones show as the closest kept state


def timeline(raw, frames=PREVIEW_FRAMES):
    """The palettes as the field plays with nothing switched (seed 0, as replay's first run): (palettes, states,
    seq), seq[f, i] being which of states[i] palette palettes[i] has at frame f."""
    m = Machine(raw)
    stored = file_palettes(raw)
    current, snaps = {}, []
    for _ in range(frames):
        n = len(m.loads)
        m.run(1)
        for pal, colours in m.loads[n:]:
            current[pal] = colours
        snaps.append(dict(current))
    pals = sorted(current)
    if not pals:
        return [], [], np.zeros((0, 0), np.int32)
    states = [{} for _ in pals]
    seq = np.array([[states[i].setdefault(s.get(p) or tuple(int(x) for x in stored[p]), len(states[i]))
                     for i, p in enumerate(pals)] for s in snaps], np.int32)
    return pals, [list(st) for st in states], seq


def _loop(rows):
    """The part of a run to play over and over: its repeating cycle once the first third (the scripts' setup,
    fades in) is past, or the rest of the run when nothing repeats within it."""
    start = len(rows) // 3
    length = next((n for n in range(1, (len(rows) - start) // 2 + 1)
                   if np.array_equal(rows[start + n :], rows[start : -n])), len(rows) - start)
    return rows[start : start + length]


def preview(raw, bg):
    """Palette effects as the viewer plays them: groups of palettes whose tiles overlap (they have to change
    together), each [{"pals", "states": [{palette: colours}], "times": [[frame, state]], "length"}], `times`
    being when the group's state changes within a loop `length` frames long (30 a second)."""
    pals, states, seq = timeline(raw)
    used = {}
    for t in bg.tiles:
        if bg.pages[t.tex].depth < 2:
            used.setdefault(t.palette, []).append(t)
    cols = [i for i, p in enumerate(pals) if p in used and len(set(seq[len(seq) // 3 :, i])) > 1]
    if not cols:
        return []
    x0, y0 = min(t.dst_x for t in bg.tiles), min(t.dst_y for t in bg.tiles)
    w = max(t.dst_x + t.size for t in bg.tiles) - x0
    h = max(t.dst_y + t.size for t in bg.tiles) - y0
    masks = {}
    for i in cols:
        mk = masks[i] = np.zeros((h, w), bool)
        for t in used[pals[i]]:
            mk[t.dst_y - y0 : t.dst_y - y0 + t.size, t.dst_x - x0 : t.dst_x - x0 + t.size] = True
    groups = []  # palettes whose footprints overlap, joined
    for i in cols:
        hit = [g for g in groups if any((masks[i] & masks[j]).any() for j in g)]
        for g in hit:
            groups.remove(g)
        groups.append(sorted({i, *(j for g in hit for j in g)}))
    out = []
    for g in groups:
        rows, combos = np.unique(seq[:, g], axis=0, return_inverse=True)[::-1]
        idx = _loop(rows.ravel())
        combos, idx = combos[np.unique(idx)], np.unique(idx, return_inverse=True)[1].ravel()  # the loop's own states
        if len(combos) > PREVIEW_STATES:  # keep the longest-shown states, show the others as the nearest of those
            kept = np.argsort(-np.bincount(idx, minlength=len(combos)), kind="stable")[:PREVIEW_STATES]
            vec = lambda c: np.concatenate([palette_rgba(states[j][s])[:, :3].ravel() for j, s in zip(g, c)])
            kv = np.stack([vec(combos[k]) for k in kept]).astype(np.int32)
            near = {k: kept[np.abs(kv - vec(combos[k])).sum(1).argmin()] for k in range(len(combos))}
            remap = {k: n for n, k in enumerate(kept)}
            idx = np.array([remap[near[k]] for k in idx])
            combos = combos[kept]
        times = [[int(f), int(idx[f])] for f in range(len(idx)) if f == 0 or idx[f] != idx[f - 1]]
        out.append({"pals": [pals[j] for j in g], "times": times, "length": len(idx),
                    "states": [{pals[j]: states[j][s] for j, s in zip(g, c)} for c in combos]})
    return out
