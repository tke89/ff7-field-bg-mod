"""FF7's battle setups (data/lang-en/battle/scene.bin): which battle scene each formation is fought in, where its
cameras sit, and which enemies it holds (with their names). battle.py names battle.lgp's models and scenes from it
and takes its battle cameras from here.

scene.bin is 0x2000-byte blocks. Each block starts with 16 u32 pointers (times 4: byte offsets in the block, 0xFFFFFFFF
ends the list) to gzip streams, 256 scenes in all. A scene unpacks to 7808 bytes:
- 0x0000: 3 x u16 enemy ids (0xFFFF none), 2 bytes padding. An enemy id is also its battle.lgp model: id n is the
  model whose two-letter name is n in base 26 ("aa" = 0, Goblin 338 = "na").
- 0x0008: 4 formations' setups, 20 bytes each: u16 battle location (0-89; battle.lgp scene "og" + location, see
  battle.location_id), u16 next formation, u16 escape counter, u16 unused, 4 x u16 arena candidates, u16 flags,
  u8 layout (0 normal, 1 preemptive, 2 back attack, 3 side attack, 4 pincer, 5-8 special), u8 the intro camera.
- 0x0058: 4 formations' camera placements, 48 bytes each: 3 cameras of s16 position x, y, z and s16 focal point
  (where it looks) x, y, z, then 12 unused bytes. The game starts a battle on one of them (FFNx: formation_camera,
  curr_formation_camera_idx) and returns to it between attacks. Unused cameras are all -1.
- 0x0118: 4 formations x 6 enemies, 16 bytes each: u16 enemy id (0xFFFF none), s16 x, y, z (battle space: y down,
  the party on +z, enemies mostly on -z), u16 row, u16 cover flags, u32 initial status flags.
- 0x0298: the 3 enemies' data, 184 bytes each, starting with the name (32 bytes of FF7 text, 0xFF ends it).
- 0x04C0 on: attacks, attack names and the AI scripts (not read here).
A formation's id is scene * 4 + its index (0-1023)."""
import struct
import threading
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

GAME_WORKDIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
SCENE_BIN = GAME_WORKDIR / "data" / "lang-en" / "battle" / "scene.bin"

BLOCK = 0x2000
SCENE_SIZE = 7808
# formation layouts (the Qhimm wiki's list; 5-8 are rare variants)
LAYOUTS = {0: "normal", 1: "preemptive", 2: "back attack", 3: "side attack", 4: "pincer attack",
           5: "pincer attack 2", 6: "side attack 2", 7: "side attack 3", 8: "normal, front row locked"}


# FF7's text (menus, names): byte b is Mac Roman's b + 0x20 (0x00 the space, 0x21 "A", 0xC1 the middle dot of
# "Jenova·BIRTH"); 0xE0 and up are control codes, and 0xFF ends a string
def text(raw):
    """FF7 text bytes -> str (stops at 0xFF, leaves control codes out)."""
    out = bytearray()
    for c in raw:
        if c == 0xFF:
            break
        if c < 0xE0:
            out.append(c + 0x20)
    return out.decode("mac_roman").strip()


def scenes(data):
    """scene.bin's bytes -> the unpacked scenes ([bytes] of SCENE_SIZE, in order)."""
    out = []
    for b in range(0, len(data), BLOCK):
        blk = data[b:b + BLOCK]
        for p in struct.unpack_from("<16I", blk):
            if p == 0xFFFFFFFF:
                break
            # each stream runs up to the next one (or the block's 0xFF padding): decompressobj stops at its end
            out.append(zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(blk[p * 4:]))
    return out


@dataclass
class Camera:
    eye: tuple        # (x, y, z) battle space (y down)
    target: tuple     # the focal point it looks at


@dataclass
class Formation:
    id: int           # scene * 4 + index
    scene: int
    index: int
    location: int     # battle location 0-89 (battle.lgp scene battle.location_id(location))
    layout: int       # LAYOUTS
    cameras: list     # [Camera], the formation's (up to 3) idle cameras, unused ones left out
    enemies: list     # [(enemy id, (x, y, z))]
    next_formation: int = 0xFFFF
    flags: int = 0
    intro_camera: int = 0


@dataclass
class SceneBin:
    formations: list = field(default_factory=list)   # [Formation] that have a location
    names: dict = field(default_factory=dict)         # enemy id -> name (the most common spelling)
    enemy_scenes: dict = field(default_factory=dict)  # enemy id -> [scene numbers]


def read(data):
    """SceneBin from scene.bin's bytes."""
    out = SceneBin()
    spellings = {}
    for si, s in enumerate(scenes(data)):
        if len(s) < SCENE_SIZE:
            continue
        ids = struct.unpack_from("<3H", s, 0)
        for k, eid in enumerate(ids):
            if eid == 0xFFFF:
                continue
            name = text(s[0x298 + k * 184:0x298 + k * 184 + 32])
            if name:
                spellings.setdefault(eid, Counter())[name] += 1
            out.enemy_scenes.setdefault(eid, []).append(si)
        for k in range(4):
            loc, nxt, _esc, _unused, _a0, _a1, _a2, _a3, flags, layout, intro = struct.unpack_from(
                "<9HBB", s, 0x08 + k * 20)
            if loc == 0xFFFF:
                continue
            cams = []
            for c in range(3):
                v = struct.unpack_from("<6h", s, 0x58 + k * 48 + c * 12)
                if all(x == -1 for x in v):
                    continue
                cams.append(Camera(tuple(v[:3]), tuple(v[3:])))
            ens = []
            for e in range(6):
                eid, x, y, z = struct.unpack_from("<H3h", s, 0x118 + k * 96 + e * 16)
                if eid != 0xFFFF:
                    ens.append((eid, (x, y, z)))
            out.formations.append(Formation(si * 4 + k, si, k, loc, layout, cams, ens, nxt, flags, intro))
    out.names = {eid: c.most_common(1)[0][0] for eid, c in spellings.items()}
    return out


_lock = threading.Lock()
_cache = {}


def load(path=SCENE_BIN):
    """SceneBin of the game's scene.bin (read once per path and file version)."""
    path = Path(path)
    st = path.stat()
    key = (str(path), st.st_size, int(st.st_mtime))
    with _lock:
        if key not in _cache:
            _cache[key] = read(path.read_bytes())
        return _cache[key]
