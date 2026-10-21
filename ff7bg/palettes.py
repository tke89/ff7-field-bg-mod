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
import struct
from dataclasses import dataclass, field

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
