"""FF8 field scripts (.jsm): which background switches are on when you enter a field, and which animate by themselves.

Format (Deling, myst6re's FF8 field editor: src/files/JsmFile.cpp, src/JsmOpcode.cpp):
- Header: entity counts (doors, lines, backgrounds, others: a byte each), then the offsets (u16) of the script-position
  table and of the code. The entity table (u16 each, from byte 8) lists lines first, then doors, backgrounds, others;
  an entry is (first script's index << 7) | (script count - 1).
- Script positions (u16): bit 15 a flag, the low 15 bits the instruction index of the script's first instruction.
- Code: 32-bit instructions. A word with a zero top byte is a plain opcode (the word itself); otherwise the top byte is
  the opcode and the low 24 bits a signed parameter. JMP/JPF/GJMP jump relative, in instructions.
- An entity's script 0 runs when the field loads (init), script 1 is its default script, run over and over.
- Background entity k drives the background tiles of parameter k (Deling: searchDefaultBGStates, bgParam = the
  entity's index among background entities). BGDRAW n shows state n, BGOFF hides the switch, BGANIME / RBGANIME a b
  play states a..b once, RBGANIMELOOP a b loop them; BGANIMESPEED sets the pace.
"""
import struct

NOP, CAL, JMP, JPF, GJMP, LBL, RET, PSHN_L = 0, 1, 2, 3, 4, 5, 6, 7
REQ, REQSW, REQEW, PREQ, PREQSW, PREQEW = 20, 21, 22, 23, 24, 25
HALT = 28
BGANIME, RBGANIME, RBGANIMELOOP, BGANIMESYNC, BGDRAW, BGOFF, BGANIMESPEED = 149, 150, 151, 152, 153, 154, 155
BGANIMEFLAG, BGSHADE, BGSHADESTOP, RBGSHADELOOP, BGSHADEOFF, BGCLEAR = 206, 208, 209, 210, 279, 284
SETPLACE = 307
NAMES = {NOP: "NOP", CAL: "CAL", JMP: "JMP", JPF: "JPF", GJMP: "GJMP", LBL: "LBL", RET: "RET", PSHN_L: "PSHN_L",
         REQ: "REQ", REQSW: "REQSW", REQEW: "REQEW", PREQ: "PREQ", PREQSW: "PREQSW", PREQEW: "PREQEW", HALT: "HALT",
         BGANIME: "BGANIME", RBGANIME: "RBGANIME", RBGANIMELOOP: "RBGANIMELOOP", BGANIMESYNC: "BGANIMESYNC",
         BGDRAW: "BGDRAW", BGOFF: "BGOFF", BGANIMESPEED: "BGANIMESPEED", BGANIMEFLAG: "BGANIMEFLAG",
         BGSHADE: "BGSHADE", BGSHADESTOP: "BGSHADESTOP", RBGSHADELOOP: "RBGSHADELOOP", BGSHADEOFF: "BGSHADEOFF",
         BGCLEAR: "BGCLEAR", SETPLACE: "SETPLACE", 8: "PSHI_L", 9: "POPI_L", 10: "PSHM_B", 11: "POPM_B",
         12: "PSHM_W", 13: "POPM_W", 14: "PSHM_L", 15: "POPM_L", 16: "PSHSM_B", 17: "PSHSM_W", 18: "PSHSM_L",
         19: "PSHAC"}


def decode(word):
    """(opcode, parameter or None)."""
    if word & 0xFF000000:
        p = word & 0x00FFFFFF
        return word >> 24, p - 0x1000000 if p & 0x800000 else p
    return word, None


class Jsm:
    def __init__(self, data):
        self.data = data
        self.doors, self.lines, self.backgrounds, self.others = data[0], data[1], data[2], data[3]
        pos_off, code_off = struct.unpack_from("<HH", data, 4)
        groups = struct.unpack_from(f"<{(pos_off - 8) // 2}H", data, 8)
        if len(groups) != self.doors + self.lines + self.backgrounds + self.others \
                and len(groups) == self.lines + self.backgrounds + self.others:
            self.doors = 0  # a handful of fields (bg2f_1a, bgroom_2...) have 0x37 in the door count's place
        positions = struct.unpack_from(f"<{(code_off - pos_off) // 2}H", data, pos_off)
        self.positions = [p & 0x7FFF for p in positions]
        n = (len(data) - code_off) // 4
        self.code = [decode(w) for w in struct.unpack_from(f"<{n}I", data, code_off)]
        # (first script index, script count) per entity, in file order: lines, doors, backgrounds, others
        self.entities = [(g >> 7, (g & 0x7F) + 1) for g in groups]
        if len(self.entities) != self.doors + self.lines + self.backgrounds + self.others:
            raise ValueError("entity counts don't match the entity table")

    def kind(self, e):
        if e < self.lines:
            return "line"
        if e < self.lines + self.doors:
            return "door"
        if e < self.lines + self.doors + self.backgrounds:
            return "background"
        return "other"

    def background_entities(self):
        """[(entity index, parameter)] for the background entities."""
        first = self.lines + self.doors
        return [(first + k, k) for k in range(self.backgrounds)]

    def script(self, e, m):
        """The instructions of entity e's script m ([] when it has none)."""
        first, count = self.entities[e]
        if m >= count or first + m + 1 >= len(self.positions):
            return []
        start, end = self.positions[first + m], self.positions[first + m + 1]
        return self.code[start:end]


def listing(code):
    return "  ".join(f"{NAMES.get(op, op)}" + ("" if p is None else f" {p}") for op, p in code)


WAIT = 60  # pops a frame count ("PSHN_L 16  60" in bgbook1a's page-turning loop)
PUSHES = {8, 10, 12, 14, 16, 17, 18, 19}  # push a value we don't know (variables, memory)
POPS = {9, 11, 13, 15}
CALLS = {REQ, REQSW, REQEW, PREQ, PREQSW, PREQEW}
POP_COUNT = {BGSHADE: 7, RBGSHADELOOP: 10, WAIT: 1}
CAL_OPS = {0: lambda a, b: a + b, 1: lambda a, b: a - b, 2: lambda a, b: a * b, 6: lambda a, b: int(a == b),
           7: lambda a, b: int(a > b), 8: lambda a, b: int(a >= b), 9: lambda a, b: int(a < b),
           10: lambda a, b: int(a <= b), 11: lambda a, b: int(a != b), 12: lambda a, b: a & b, 13: lambda a, b: a | b}
ALL_STATES = "all"  # in an animation: every state of the switch, in order (frames the script picks at run time)
MAX_STEPS = 600   # instructions run per script call
MAX_DEPTH = 4     # nested REQ calls followed
DEFAULT_SPEED = 1  # game frames per animation step before any BGANIMESPEED


class _Run:
    """One pass over a field's start-up: every entity's init script, then the first pass of every default script,
    following REQ calls by their label. Branches on a known constant are taken as the game would; on anything else
    (story flags, where you came from) the jump is taken, which lands on the default ("otherwise") branch.

    For each background parameter it keeps the state as it stands (`state`: a number, or None when switched off) and
    a timeline of what its own default script shows ([(state or None, frames)]), with the instruction index where a
    backward jump makes it a loop."""

    def __init__(self, jsm):
        self.jsm = jsm
        self.label_of = {}
        for e, (first, count) in enumerate(jsm.entities):
            for m in range(count):
                self.label_of[first + m] = (e, m)
        self.param_of = dict(jsm.background_entities())
        self.state = {}       # param -> state number, or None (off)
        self.speed = {}       # param -> frames per animation step
        self.looping = {}     # param -> [(state, frames)] of an RBGANIMELOOP

    def anim_steps(self, param, a, b):
        sp = self.speed.get(param, DEFAULT_SPEED)
        step = 1 if b >= a else -1
        return [(s, sp) for s in range(a, b + step, step)]

    def run(self, e, m, depth=0, timeline=None):
        """Run entity e's script m. With timeline (a list), the background param's states are appended to it as
        (state or None, frames, instruction index); returns the instruction index a backward jump went to, if any."""
        code = self.jsm.script(e, m)
        param = self.param_of.get(e)
        stack = []
        pop = lambda: stack.pop() if stack else None
        i = steps = 0
        while 0 <= i < len(code) and steps < MAX_STEPS:
            steps += 1
            op, p = code[i]
            if op == PSHN_L:
                stack.append(p)
            elif op in PUSHES:
                stack.append(None)
            elif op in POPS:
                pop()
            elif op == CAL:
                b = pop() if p not in (5, 15) else None
                a = pop()
                stack.append(CAL_OPS[p](a, b) if p in CAL_OPS and a is not None and b is not None else None)
            elif op == JPF:
                v = pop()
                if v is None or v == 0:  # false, or a flag we can't know: the default branch
                    i += p
                    continue
            elif op in (JMP, GJMP):
                if p <= 0:  # back: a loop (the default script's main loop); one pass is enough
                    return i + p
                i += p
                continue
            elif op in CALLS:
                label, _priority = pop(), pop()
                target = self.label_of.get(label)
                if target and depth < MAX_DEPTH:
                    self.run(*target, depth + 1)
            elif op in (HALT, RET):
                return None
            elif param is not None and op == BGDRAW:
                n = pop()
                if n is not None:
                    self.state[param] = n
                    self.looping.pop(param, None)
                    if timeline is not None:
                        timeline.append((n, 0, i))
            elif param is not None and op == BGOFF:
                self.state[param] = None
                self.looping.pop(param, None)
                if timeline is not None:
                    timeline.append((None, 0, i))
            elif param is not None and op == BGANIMESPEED:
                n = pop()
                if n:
                    self.speed[param] = n
            elif param is not None and op in (BGANIME, RBGANIME, RBGANIMELOOP):
                b, a = pop(), pop()
                if a is not None and b is not None:
                    steps_ = self.anim_steps(param, a, b)
                    if op == RBGANIMELOOP:
                        self.looping[param] = steps_
                        self.state[param] = a
                    else:
                        self.state[param] = b  # played once: it rests on the last
                        self.looping.pop(param, None)
                    if timeline is not None:
                        timeline.extend((s, f, i) for s, f in steps_)
                elif timeline is not None:  # frames picked at run time (bghoke_1: at random): all of its states
                    timeline.append((ALL_STATES, self.speed.get(param, DEFAULT_SPEED), i))
            elif op == WAIT:
                n = pop()
                if timeline is not None and timeline and n:
                    s, f, at = timeline[-1]
                    timeline[-1] = (s, f + n, at)
            else:
                for _ in range(POP_COUNT.get(op, 0)):
                    pop()
            i += 1
        return None


def place(jsm):
    """The field's place-name number (SETPLACE n, an index into menu/areames.dc1): the first set by an init script,
    else the first anywhere; None if it never sets one."""
    found = None
    for m in (0, None):
        for e, (first, count) in enumerate(jsm.entities):
            for mm in ([m] if m is not None else range(count)):
                code = jsm.script(e, mm)
                for i, (op, _) in enumerate(code):
                    if op == SETPLACE and i and code[i - 1][0] == PSHN_L:
                        return code[i - 1][1]
    return found


def analyze(jsm):
    """What a field shows on entry, from its scripts.

    Returns (entry, animations, touched):
    - entry {parameter: state, or None when off} for the switches the start-up sets;
    - animations {parameter: [(state or None, frames)]}: switches whose default script keeps changing them (an
      RBGANIMELOOP, or a loop of BGANIME / BGDRAW / BGOFF / WAIT), one cycle of what they show;
    - touched: the parameters any start-up script set."""
    run = _Run(jsm)
    n = len(jsm.entities)
    for e in range(n):  # init scripts
        run.run(e, 0)
    loops = {}
    for e in range(n):  # the first pass of each default script
        tl = []
        back = run.run(e, 1, timeline=tl)
        param = run.param_of.get(e)
        if param is not None and back is not None:
            cycle = [(s, f) for s, f, at in tl if at >= back]
            if len({s for s, _ in cycle}) > 1 or any(s == ALL_STATES for s, _ in cycle):
                loops[param] = cycle
    animations = {}
    for param, steps_ in {**run.looping, **loops}.items():
        if len({s for s, _ in steps_}) > 1 or any(s == ALL_STATES for s, _ in steps_):
            animations[param] = [(s, max(f, 1)) for s, f in steps_]
    return dict(run.state), animations, set(run.state)

