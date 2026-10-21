"""Which background states a field shows when you enter it.

Follows Makou Reactor (Section1File / Script::backgroundParams): every entity's first script (its
init script) is scanned for BGON opcodes on bank 0; each one switches on bit `state` of `param`.
"""
import struct

# Opcode sizes, Makou Reactor Opcode::length[257]
LENGTH = [
    1, 3, 3, 3, 3, 3, 3, 2, 2, 15, 6, 6, 1, 1, 2, 2, 2, 3, 2, 3, 6, 7, 8, 9, 8, 9, 10, 3, 6, 1, 1, 1,
    11, 2, 5, 3, 3, 9, 2, 2, 3, 1, 2, 2, 5, 7, 2, 10, 4, 4, 4, 2, 2, 4, 5, 8, 6, 6, 6, 4, 1, 1, 1, 1,
    3, 5, 6, 2, 1, 5, 1, 5, 7, 4, 2, 2, 1, 5, 1, 5, 10, 6, 4, 2, 2, 3, 7, 7, 5, 5, 5, 7, 8, 10, 8, 1,
    10, 2, 5, 6, 6, 1, 9, 1, 9, 2, 7, 9, 1, 4, 3, 6, 4, 2, 3, 4, 4, 8, 4, 5, 4, 5, 3, 3, 3, 3, 2, 3,
    4, 5, 4, 4, 4, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 3, 3, 3, 3, 3, 4, 5, 6, 7, 7, 11,
    2, 2, 3, 3, 2, 11, 9, 9, 6, 6, 2, 4, 1, 6, 3, 3, 5, 5, 4, 3, 6, 6, 2, 4, 5, 4, 3, 5, 5, 4, 1, 2,
    11, 8, 15, 12, 1, 3, 3, 2, 2, 2, 4, 3, 3, 3, 2, 2, 13, 2, 2, 16, 10, 10, 4, 4, 3, 1, 15, 2, 4, 1, 1, 11,
    4, 4, 3, 3, 3, 5, 5, 5, 7, 10, 10, 5, 5, 8, 8, 11, 2, 5, 14, 2, 2, 2, 2, 4, 2, 1, 3, 2, 2, 8, 3, 1,
    0,
]
SPECIAL, UNUSED1C, KAWAI, BGON = 0x0F, 0x1C, 0x28, 0xE0
SPECIAL_PLUS1 = {0xF5, 0xF6, 0xF7, 0xFB, 0xFC}
SPECIAL_PLUS2 = {0xF8, 0xFD}


def _opcode_size(code, pos):
    op = code[pos]
    size = LENGTH[op]
    if op == SPECIAL and pos + 1 < len(code):
        sub = code[pos + 1]
        size += 1 if sub in SPECIAL_PLUS1 else 2 if sub in SPECIAL_PLUS2 else 0
    elif op == UNUSED1C and pos + 5 < len(code):
        size += min(code[pos + 5], 128)
    elif op == KAWAI and pos + 1 < len(code):
        size = max(code[pos + 1], 1)
    return max(size, 1)


def _script0s(sec):
    """Yield the bytes of each entity's script 0 (init up to the first RET, then its frame loop)."""
    entities = sec[2]
    (pos_texts,) = struct.unpack_from("<H", sec, 4)
    (n_akao,) = struct.unpack_from("<H", sec, 6)
    cur = 32
    pos_scripts = cur + 8 * entities + 4 * n_akao
    if n_akao:
        (pos_akao,) = struct.unpack_from("<I", sec, cur + 8 * entities)
    else:
        pos_akao = len(sec)
    end_all = min(pos_akao, pos_texts)
    table = [struct.unpack_from("<32H", sec, pos_scripts + 64 * e) for e in range(entities)]
    starts = sorted({p for row in table for p in row} | {end_all})
    for row in table:
        start = row[0]
        nxt = next((p for p in starts if p > start), end_all)
        yield sec[start:nxt]


BGOFF, BGROL, BGROL2, BGCLR, RET, WAIT = 0xE1, 0xE2, 0xE3, 0xE4, 0x00, 0x24


def _ops(code):
    pos = 0
    while pos < len(code):
        yield pos, code[pos]
        pos += _opcode_size(code, pos)


def analyze(raw_field):
    """Background states at field entry, and which groups animate on their own.

    Script 0 of an entity is its init code up to the first RET, followed by its per-frame loop.
    BGON/BGOFF/BGCLR in the init part give the state on entry (Makou Reactor scans the whole
    script and ignores BGOFF, which turns every animation frame on at once). A group switched
    in the loop part animates by itself; frame order and timing come from the BGON order and the
    WAITs (frames at 30 fps) between them. Groups only switched by event scripts (talking,
    touching: doors, switches) are not animations.

    Returns (defaults {param: bitmask}, animations {param: {"order": [state masks], "wait": frames}})."""
    (off,) = struct.unpack_from("<I", raw_field, 6)
    (length,) = struct.unpack_from("<I", raw_field, off)
    sec = raw_field[off + 4 : off + 4 + length]
    on, anims = {}, {}
    for code in _script0s(sec):
        in_loop = False
        waited = 0
        last_on = {}
        for pos, op in _ops(code):
            if op == RET and not in_loop:
                in_loop = True
                continue
            bank0 = pos + 1 < len(code) and code[pos + 1] == 0
            if op == WAIT and pos + 2 < len(code):
                waited += struct.unpack_from("<H", code, pos + 1)[0]
            elif op in (BGON, BGOFF) and bank0 and pos + 3 < len(code) and code[pos + 3] < 8:
                param, bit = code[pos + 2], 1 << code[pos + 3]
                if not in_loop:
                    on[param] = (on.get(param, 0) | bit) if op == BGON else (on.get(param, 0) & ~bit)
                elif op == BGON:
                    a = anims.setdefault(param, {"order": [], "waits": []})
                    if bit not in a["order"]:
                        a["order"].append(bit)
                    if param in last_on and waited:
                        a["waits"].append(waited)
                    last_on[param], waited = bit, 0
            elif op in (BGROL, BGROL2) and bank0 and pos + 2 < len(code) and in_loop:
                anims.setdefault(code[pos + 2], {"order": [], "waits": []})
            elif op == BGCLR and bank0 and pos + 2 < len(code) and not in_loop:
                on[code[pos + 2]] = 0
    defaults = {p: m for p, m in on.items() if m}
    animations = {}
    for p, a in anims.items():
        waits = sorted(a["waits"])
        animations[p] = {"order": a["order"], "wait": waits[len(waits) // 2] if waits else 4}
    return defaults, animations


def default_states(raw_field):
    """{param: bitmask of states switched on at field entry}."""
    return analyze(raw_field)[0]
