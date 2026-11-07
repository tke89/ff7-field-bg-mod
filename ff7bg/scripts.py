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

    A loop that waits with its group all off shows the art underneath as a frame (Kalm's wall fans: off,
    frame 1, frame 2): 0 is in the order there.

    Returns (defaults {param: bitmask}, animations {param: {"order": [state masks], "wait": frames}})."""
    (off,) = struct.unpack_from("<I", raw_field, 6)
    (length,) = struct.unpack_from("<I", raw_field, off)
    sec = raw_field[off + 4 : off + 4 + length]
    on, anims = {}, {}
    for code in _script0s(sec):
        in_loop = False
        waited = 0
        last_on = {}
        # the groups this loop switches on, and their state as it runs
        loop_params, after_ret = set(), False
        for pos, op in _ops(code):
            if op == RET and not after_ret:
                after_ret = True
            elif after_ret and op == BGON and pos + 3 < len(code) and code[pos + 1] == 0 and code[pos + 3] < 8:
                loop_params.add(code[pos + 2])
        cur = {}
        for pos, op in _ops(code):
            if op == RET and not in_loop:
                in_loop = True
                cur = {p: on.get(p, 0) for p in loop_params}
                continue
            bank0 = pos + 1 < len(code) and code[pos + 1] == 0
            if op == WAIT and pos + 2 < len(code):
                n = struct.unpack_from("<H", code, pos + 1)[0]
                waited += n
                for p in loop_params if in_loop and n else ():
                    if not cur.get(p, 0):  # a step with the group all off
                        a = anims.setdefault(p, {"order": [], "waits": []})
                        if 0 not in a["order"]:
                            a["order"].append(0)
            elif op in (BGON, BGOFF) and bank0 and pos + 3 < len(code) and code[pos + 3] < 8:
                param, bit = code[pos + 2], 1 << code[pos + 3]
                if in_loop and param in cur:
                    cur[param] = (cur[param] | bit) if op == BGON else (cur[param] & ~bit)
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
    for p, a in _loops(sec, _tile_states(raw_field)).items():  # loops script 0's frame loop doesn't hold
        animations.setdefault(p, a)
    return defaults, animations


JMPB, JMPBL = 0x12, 0x13


def _scripts(sec):
    """Yield the bytes of every script of every entity (each from its start to the next script's)."""
    entities = sec[2]
    (pos_texts,) = struct.unpack_from("<H", sec, 4)
    (n_akao,) = struct.unpack_from("<H", sec, 6)
    pos_scripts = 32 + 8 * entities + 4 * n_akao
    end_all = min(struct.unpack_from("<I", sec, 32 + 8 * entities)[0] if n_akao else len(sec), pos_texts)
    table = [struct.unpack_from("<32H", sec, pos_scripts + 64 * e) for e in range(entities)]
    starts = sorted({p for row in table for p in row} | {end_all})
    for start in starts[:-1]:
        yield sec[start:next((p for p in starts if p > start), end_all)]


def _tile_states(raw_field):
    """{param: set of state masks its tiles have}, or None when the background can't be read."""
    try:
        from .field import parse_background
        bg = parse_background("field", raw_field)
    except Exception:
        return None
    out = {}
    for t in bg.tiles:
        if t.param:
            out.setdefault(t.param, set()).add(t.state)
    return out


def _loops(sec, tile_states=None):
    """{param: {"order", "wait"}} for groups a field script keeps switching (bank 0) in a loop that waits, in
    whichever script it runs: a backward jump (JMPB/JMPBL) over a WAIT and over two or more states of a group switched
    on, or one switched on and off (a blink). analyze's frame loop only reads script 0's part after its init, so it
    missed Junon's sea (jundoc1a/1b, junone7: the wave entity's script 1 cycles its group through 7 states). A BGON
    whose state comes from a variable (its banks byte's low nibble set) switches whichever state the variable holds:
    in a loop, that's every state the group's tiles have, in order (colne_6's spinning ball and rkt_w's fire count
    one up each turn, mrkt3's dancing dolls on the Honey Bee sign); analyze skipped those BGONs (bank 0 only), so
    these fields showed no animation. The wait: the loop's WAIT frames per state switched on."""
    out = {}
    for code in _scripts(sec):
        ops = list(_ops(code))
        for pos, op in ops:
            if op == JMPB and pos + 1 < len(code):
                target = pos - code[pos + 1]
            elif op == JMPBL and pos + 2 < len(code):
                target = pos - struct.unpack_from("<H", code, pos + 1)[0]
            else:
                continue
            body = [(q, o) for q, o in ops if target <= q < pos]
            waits = sum(struct.unpack_from("<H", code, q + 1)[0] for q, o in body if o == WAIT and q + 2 < len(code))
            if not waits:
                continue
            switched = {}
            for q, o in body:
                if o not in (BGON, BGOFF) or q + 3 >= len(code) or code[q + 1] >> 4:  # the group from a variable
                    continue
                p = code[q + 2]
                if code[q + 1] & 0xF:  # the state from a variable: any of the group's
                    if o == BGON:
                        mine = sorted((tile_states or {}).get(p, ())) or [1 << i for i in range(8)]
                        switched.setdefault(p, []).extend((BGON, b, 1) for b in mine)
                elif code[q + 3] < 8:
                    switched.setdefault(p, []).append((o, 1 << code[q + 3], 0))
            for p, events in switched.items():
                ons = [b for o, b, _ in events if o == BGON]
                if len(set(ons)) < 2 and not set(ons) & {b for o, b, _ in events if o == BGOFF}:
                    continue
                a = out.setdefault(p, {"order": [], "wait": 4})
                a["order"] += [b for b in ons if b not in a["order"]]
                steps = sum(1 for o, _, v in events if o == BGON and not v) + any(v for _, _, v in events)
                a["wait"] = max(1, round(waits / max(steps, 1)))
    return out


def default_states(raw_field):
    """{param: bitmask of states switched on at field entry}."""
    return analyze(raw_field)[0]
