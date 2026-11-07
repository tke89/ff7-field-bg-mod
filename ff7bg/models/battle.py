"""FF7's battle models and battle scenes (data/battle/battle.lgp): read, pose and turn into a mesh.Mesh, name them
from scene.bin, and find the cameras the game looks at each battle scene from (docs/models-plan.md).

battle.lgp has 481 model ids. Each is two letters, an index in base 26 ("aa" = 0, "rt" = 461), and its files are
the id plus a two-letter role, the same way: `aa` header and skeleton, `ab` the model's battle data (0.4-1.5 KB for
models, not needed here; 8 bytes for scenes: 01 05 00.. in 74 of 90, nothing to do with palettes), `ac`... its
textures (TEX), `am`... its parts (.p), one per bone (bone i is role am + i; bones without a part have no file), `ck`...
a party member's weapons (16) and `da` the animations.
- ids 0-369 (aa-of): enemy models. scene.bin's enemy id is the model index (Goblin 338 = "na"). Many are untextured
  (vertex colours only) and a few have no parts of their own.
- ids 370-459 (og-rr): the 90 battle scenes: battle location L is id 370 + L (location 2, the grassland most
  formations use, is "oi").
- ids 460-480 (rs-sm): party members and the forms they take (the header's first word is 2).

`aa` header (FFNx battle_hrc_header): u32 kind (0 enemy, 1 scene, 2 party), u32 1, u32 1 (0 for scenes), u32 bones,
u32 0, u32 parts (scenes: their part count; models 0), u32 textures, u32 body animations, u32 index of the first
weapon animation's slot, u32 weapons, u32 weapon animations, u32 0, then 4 bytes (a pointer at run time) and per bone
12 bytes: s32 parent (-1 for the root), float length, u32 has a part (FFNx battle_hrc_bone: parent, bone_length,
num_rsd). A scene's parts all sit at the origin in battle space; its textures are ac.. in order (a group's texid
indexes them, for scenes and models alike).

`da`: u32 animation count (body animations, then weapon animations); per animation u32 bones (the skeleton's + 1 for
the root's rotation), u32 frames, u32 block size, then the block: u16 frames, u16 data size, u8 key, then a bit
stream read most significant bit first. Frame 0 is stored whole: s16 root x, y, z, then per bone (root first) three
(12 - key)-bit angles shifted left by key (4096 = 360 degrees: x, y, z). Later frames are deltas (not decoded here:
load_model poses frame 0). Weapon animation k belongs to body animation k and has one bone: where the weapon is.

Posing (as Kimera draws battle skeletons): the root's frame is T(x, y, z) R(root); a bone's frame is its parent's
end (the root's frame for bones whose parent is -1) times R(its rotation), and its end is that moved by its length
along z: F(bone) = End(parent) R(bone), End(bone) = F(bone) T(0, 0, length). R(x, y, z) = Ry(y) Rx(x) Rz(z) as for
field models. A weapon's frame is T(weapon anim position) R(weapon anim rotation) in model space. Models stand with
their soles at y = 0 (y down) and face -z (the party faces the enemies on -z).

Palettes: every battle TEX stores one palette (palette size = colours per palette), but its header's palette count is
the size of the model's CLUT bank on the PSX (mostly its number of textures; 1 fewer to 2 more in 23 of the 198
textured ids), and tex.read leaves the bank's other palettes empty. The .p hundreds keep the PSX render state: offset
0x1C the texture page, 0x20 the CLUT (x / 16 in bits 0-5, row in bits 6-14). A group's palette row is the CLUT row
minus the TEX header's CLUT base row (u32 at 0xE0; 0xDC is the base x). In all of battle.lgp each texture is drawn
with exactly one row (787 of 787), and the colours the file stores are that row's: the colour-key flag is set exactly
where the file's own palette has black at a used index (786 of 787; the row's file, the other reading, shows no such
link), and some rows have no file at all (Aeris's rvac uses row 2; she has 2 files). So every group draws its texture
with the file's own palette: mesh.TexRef palette 0. The game asks FFNx for _00 (seen in game 2026-11-06), under the
name the exe loads the model by, not battle.lgp's: mods/textures/battle/STAGE10_T00_00.png for oq's first texture
(game_name, texture_key). The rows are kept in the catalogue ("clut_row")."""
import json
import struct
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..lgp import LgpArchive
from . import mesh as meshmod
from . import pfile, render, scenebin, tex

GAME_WORKDIR = scenebin.GAME_WORKDIR
BATTLE_LGP = GAME_WORKDIR / "data" / "battle" / "battle.lgp"
DIRECT = GAME_WORKDIR / "direct" / "battle"  # the game's own replacements for battle.lgp's files (read_file)
SCENE_BIN = scenebin.SCENE_BIN
REPO = Path(__file__).resolve().parents[2]
# catalogue() reads every file of battle.lgp (a few seconds): its result is kept here, redone when the data changes
CACHE = REPO / "work" / "models" / "battle" / "catalogue.json"
CACHE_VERSION = 5  # 4: texture keys are the game's names (texture_key); 5: cameras are the resting views, culled

SCENE_BASE = 370    # battle location 0 ("og")
SCENE_COUNT = 90
PART_BASE = 12      # role "am": bone 0's part
WEAPON_BASE = 62    # role "ck": weapon 0

# The party's battle models (battle.lgp doesn't name them: told apart by rendering them, check_battle.py's
# party sheet). Barret and Vincent have several models with the same skeleton but their own parts and textures
# (one per kind of weapon, it seems); "si" is a second, much finer Cloud (3324 triangles to rt's 1104).
PARTY = {
    "rs": "Frog", "rt": "Cloud", "ru": "Tifa", "rv": "Aeris", "rw": "Red XIII", "rx": "Yuffie",
    "ry": "Cait Sith", "rz": "Cid", "sa": "Sephiroth", "sb": "Barret", "sc": "Barret (2)", "sd": "Barret (3)",
    "se": "Barret (4)", "sf": "Vincent", "sg": "Vincent (2)", "sh": "Vincent (3)", "si": "Cloud (fine)",
    "sj": "Galian Beast", "sk": "Death Gigas", "sl": "Hellmasker", "sm": "Chaos",
}

# Battle projection, measured in game (2026-11-06, work/models/battle/camera: 12 battles in 9 scenes screenshotted
# at rest; in 11 of them ~45000 points matched between the game's picture and our render of the scene, the camera
# solved from them). The PSX one scaled up: a 320 x 240 screen with the projection plane (the GTE's H) BATTLE_H =
# 512 pixels from the eye, 1536 at 720 lines (fitted 1536.3 over all shots, 1534-1540 per battle), so a vertical
# field of view of 26.4 degrees (horizontal 34.7 at 4:3); the principal point at the picture's centre (fitted within
# 0.3 px at 960 x 720: the battle menu covers the bottom third, but the 3D view behind it is the whole picture); no
# roll (fitted 0.00-0.02 degrees). Wider screens keep the vertical field of view, as FFNx's widescreen does (not
# measured).
BATTLE_H = 512.0
PSX_HEIGHT = 240.0
NEAR = 100.0        # battle units; cameras sit 5000-20000 from the action, inside the sky dome
# Where the game's camera really is: scene.bin's eye and look-at both moved by this (battle units, y down: 14 up).
# Measured, not understood: every one of the 11 battles' resting shots fits best with the whole camera 12-15 units
# above the stored numbers (each battle on its own: -12.4 to -15.2, mean -13.9), the look direction unchanged;
# with it the game's picture and our render agree to a median 0.8 px at 960 x 720 (1.8 px without). A raised eye
# alone (look-at kept), a taller focal or a lower principal point explain the shots worse (median 1.2, 1.35, 0.9 px).
# Rendering the scene, it's the same as the game drawing the scene 14 units lower; which of the two it is matters
# only when enemies or the party are drawn in too (not checked).
CAMERA_OFFSET = (0.0, -14.0, 0.0)
# scene.bin's stock camera placement (eye, look-at), a steep view from high up: 98 of its 996 formations rest on it
# (their slot 0), and 732 more fill their unused slots 1 and 2 with it.
PLACEHOLDER_CAMERA = ((10000, -5000, 6000), (0, 400, 300))


# The names the game asks for (the 2026 re-release's ff7_en.exe: PSX file names, STAGE%02d.DAT, ENEMY%03d.DAT and
# its table of the party's, which it maps to battle.lgp's two-letter files itself). FFNx names a texture after them:
# its override is mods/textures/battle/<NAME>_T<texid 00>_<palette 00>.png. Seen in game (FFNx.log, 2026-11-06):
# STAGE10_T00-T04 for "oq" (location 10), ENEMY019_T00-T02 for the Grunt ("at", 19), CLOUD_T00-T01, BARRETT2_T00-T02;
# battle 310 with oq's painted textures as STAGE10_T0n_00.png showed them. The party's other names follow the exe's
# table in battle.lgp's order (Barret's and Vincent's alternates in turn): not each one checked in game.
GAME_PARTY = {
    "rs": "FROG", "rt": "CLOUD", "ru": "TIFA", "rv": "EARITH", "rw": "RED13", "rx": "YUFI", "ry": "KETCY",
    "rz": "CID1", "sa": "SEFIROS", "sb": "BARRETT", "sc": "BARRETT2", "sd": "BARRETT3", "se": "BARRETT4",
    "sf": "VINSENT", "sg": "VINSENT2", "sh": "VINSENT3", "si": "HICLOUD",
}


def game_name(id):
    """The name the game loads a battle model or scene by ("oq" -> "STAGE10", "at" -> "ENEMY019", "rt" -> "CLOUD"),
    or None for one it has no name for here (the Galian Beast's forms)."""
    id = id.lower()
    loc = scene_location(id)
    if loc is not None:
        return f"STAGE{loc:02d}"
    k = id_index(id)
    if k < SCENE_BASE:
        return f"ENEMY{k:03d}"
    return GAME_PARTY.get(id)


def texture_key(id, texid):
    """FFNx's override name (without the palette) for a model's texture: "battle/STAGE10_T00"; the battle.lgp file's
    own name ("battle/OQAC") where the game's name isn't known."""
    g = game_name(id)
    return f"battle/{g}_T{texid:02d}" if g else f"battle/{(id + tex_role(texid)).upper()}"


def id_name(index):
    """Model index -> its two-letter id (461 -> "rt")."""
    return chr(97 + index // 26) + chr(97 + index % 26)


def id_index(name):
    """Two-letter id -> model index ("rt" -> 461)."""
    name = name.lower()
    return (ord(name[0]) - 97) * 26 + ord(name[1]) - 97


def location_id(location):
    """Battle location (scene.bin) -> its battle scene's id (2 -> "oi")."""
    return id_name(SCENE_BASE + location)


def scene_location(scene_id):
    """Battle scene id -> its battle location number, or None for a model id."""
    k = id_index(scene_id) - SCENE_BASE
    return k if 0 <= k < SCENE_COUNT else None


def part_role(bone):
    return id_name(PART_BASE + bone)


def weapon_role(weapon):
    return id_name(WEAPON_BASE + weapon)


def tex_role(texid):
    return id_name(2 + texid)  # "ac" = texid 0


# --- the archive ----------------------------------------------------------------------------------------------------

_lock = threading.RLock()
_archives = {}


def _archive(path):
    path = str(path)
    with _lock:
        if path not in _archives:
            _archives[path] = LgpArchive(path)
        return _archives[path]


def _direct(name, battle_lgp):
    """The game's own replacement for a battle.lgp file, when there is one: FFNx's direct mode reads
    workingdir/direct/battle/<file> in place of the archive's (7th Heaven mods put files there: the user's setup has a
    whole new, denser Barret, `sc*`, and Cloud's `rtad`). Only for the game's own battle.lgp."""
    if Path(battle_lgp) != BATTLE_LGP:
        return None
    p = DIRECT / name.lower()
    return p if p.is_file() else None


def read_file(name, battle_lgp=BATTLE_LGP):
    """A file of battle.lgp ("rtaa", any case), as the game reads it (its direct/ replacement first); KeyError when
    it isn't there. Painting the archive's own model when the game draws a mod's made textures for parts the game
    doesn't have: FFNx-3d refused Barret's painted texture (its parts' counts didn't match), 2026-11-07."""
    p = _direct(name, battle_lgp)
    if p is not None:
        return p.read_bytes()
    with _lock:
        return _archive(battle_lgp).read(name)


def has_file(name, battle_lgp=BATTLE_LGP):
    return _direct(name, battle_lgp) is not None or name.lower() in _archive(battle_lgp).entries


def ids(battle_lgp=BATTLE_LGP):
    """Every model id in battle.lgp (the ones with an `aa` header), sorted (with any the game's direct/ adds)."""
    names = set(_archive(battle_lgp).names())
    if Path(battle_lgp) == BATTLE_LGP and DIRECT.is_dir():
        names |= {p.name for p in DIRECT.iterdir() if p.is_file()}
    return sorted({n[:2].lower() for n in names if n.lower().endswith("aa") and len(n) == 4})


# --- formats --------------------------------------------------------------------------------------------------------

@dataclass
class Bone:
    parent: int       # -1 for the root
    length: float
    has_part: bool


@dataclass
class Header:
    kind: int         # 0 enemy, 1 scene, 2 party
    bones: list       # [Bone]
    parts: int        # scenes: how many parts (am..); models: 0
    textures: int     # TEX files ac..
    animations: int   # body animations (the da file's first ones)
    weapon_slot: int  # the header's first weapon animation slot (the da file has weapon animations right after
                      # the body ones, whatever this says)
    weapons: int      # weapon parts ck..
    weapon_animations: int


def read_header(data):
    """Header from an `aa` file."""
    if len(data) < 0x34:
        raise ValueError("not a battle header")
    h = struct.unpack_from("<12I", data)
    nb = h[3]
    if len(data) < 0x34 + 12 * nb:
        raise ValueError("battle header cut short")
    bones = []
    for i in range(nb):
        parent, length, rsd = struct.unpack_from("<ifI", data, 0x34 + 12 * i)
        bones.append(Bone(parent, float(length), bool(rsd)))
    return Header(h[0], bones, h[5], h[6], h[7], h[8], h[9], h[10])


@dataclass
class Frame:
    """One posed frame: the root's position and every rotation in degrees (rows: the root's, then bone 0, 1...)."""
    translation: np.ndarray   # (3,)
    rotations: np.ndarray     # (bones + 1, 3) x, y, z


@dataclass
class AnimationBlock:
    bones: int        # rotations per frame (skeleton bones + 1)
    frames: int
    data: bytes       # the block after the 12-byte header

    @property
    def empty(self):
        return self.frames < 1 or len(self.data) < 11


def animation_blocks(data):
    """A `da` file -> [AnimationBlock] (body animations, then weapon animations)."""
    count = struct.unpack_from("<I", data)[0]
    out, off = [], 4
    for _ in range(count):
        if off + 12 > len(data):
            break
        bones, frames, size = struct.unpack_from("<3I", data, off)
        out.append(AnimationBlock(bones, frames, bytes(data[off + 12:off + 12 + size])))
        off += 12 + size
    return out


class _Bits:
    """A bit stream read most significant bit first (battle animations)."""

    def __init__(self, data):
        self.data, self.pos = data, 0

    def get(self, n, signed=False):
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        if signed and n and v & (1 << (n - 1)):
            v -= 1 << n
        return v


def first_frame(block):
    """Frame 0 of an animation (stored whole: s16 x, y, z, then (12 - key)-bit angles << key), or None if empty."""
    if block.empty:
        return None
    _frames, _size, key = struct.unpack_from("<HHB", block.data)
    bits = _Bits(block.data[5:])
    try:
        t = [bits.get(16, True) for _ in range(3)]
        rots = [[bits.get(12 - key) << key for _ in range(3)] for _ in range(block.bones)]
    except IndexError:  # shorter than a whole frame
        return None
    return Frame(np.array(t, float), np.array(rots, float) * (360.0 / 4096.0))


def rotation(x, y, z):
    """3x3 rotation (degrees): Ry(y) Rx(x) Rz(z), for column vectors (the field models' order too)."""
    x, y, z = np.radians([x, y, z])
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return ry @ rx @ rz


def _rt(r, t=(0, 0, 0)):
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = t
    return m


def pose(header, frame=None):
    """Per bone its 4x4 frame in model space (column vectors: model = M @ part-local); frame None: the bind pose."""
    if frame is None:
        root = np.eye(4)
        rots = np.zeros((len(header.bones) + 1, 3))
    else:
        root = _rt(rotation(*frame.rotations[0]), frame.translation)
        rots = frame.rotations
    frames, ends = [], []
    for i, b in enumerate(header.bones):
        base = ends[b.parent] if 0 <= b.parent < i else root
        r = rots[i + 1] if i + 1 < len(rots) else (0, 0, 0)
        f = base @ _rt(rotation(*r))
        frames.append(f)
        ends.append(f @ _rt(np.eye(3), (0, 0, b.length)))
    return frames


def _hundreds(data):
    """A .p file's hundreds (25 u32 each) and groups (14 u32 each), raw: the PSX texture page and CLUT that
    pfile.Part leaves out. Same layout walk as pfile.read (call that first: it checks the file)."""
    h = struct.unpack_from("<16I", data, 0)
    nv, nn, nunk, nt, nc, ne, npoly, nunk2, nunk3, nh, ng = h[3:14]
    off = (pfile.HEADER + nunk * 12 + nv * 12 + nn * 12 + nt * 8 + nc * 4 + npoly * 4 + ne * 4 + npoly * 24
           + nunk2 * 24 + nunk3 * 3)
    hs = [struct.unpack_from("<25I", data, off + i * pfile.HUNDRED) for i in range(nh)]
    gs = [struct.unpack_from("<14I", data, off + nh * pfile.HUNDRED + i * pfile.GROUP) for i in range(ng)]
    return hs, gs


def clut_rows(id, battle_lgp=BATTLE_LGP):
    """{texid: palette row} as the model's parts draw them: the hundred's CLUT row minus the TEX's CLUT base row
    (one row per texture everywhere in battle.lgp; see the module notes). A texture whose groups record no CLUT (0:
    three textures, in fa, ff and kq) has no row."""
    id = id.lower()
    hd = header(id, battle_lgp)
    base = {}
    for t in range(hd.textures):
        name = id + tex_role(t)
        if has_file(name, battle_lgp):
            v = struct.unpack_from("<59I", read_file(name, battle_lgp))
            base[t] = (v[55], v[56])
    rows = {}
    for name in _part_files(id, hd, battle_lgp) + _weapon_files(id, hd, battle_lgp):
        data = read_file(name, battle_lgp)
        for hu, g in zip(*_hundreds(data)):
            if not g[12] or g[13] not in base:
                continue
            clut = hu[8]
            if not clut:
                continue
            rows.setdefault(g[13], Counter())[(clut >> 6) - base[g[13]][1]] += 1
    return {t: c.most_common(1)[0][0] for t, c in rows.items()}


# --- models ---------------------------------------------------------------------------------------------------------

_headers = {}


def header(id, battle_lgp=BATTLE_LGP):
    key = (id.lower(), str(battle_lgp))
    with _lock:
        if key not in _headers:
            _headers[key] = read_header(read_file(id.lower() + "aa", battle_lgp))
        return _headers[key]


def _part_files(id, hd, battle_lgp):
    """The id's .p files (not weapons): a scene's am.. (its part count), a model's per bone that has one."""
    if hd.kind == 1:
        names = [id + part_role(i) for i in range(hd.parts)]
    else:
        names = [id + part_role(i) for i, b in enumerate(hd.bones) if b.has_part]
    return [n for n in names if has_file(n, battle_lgp)]


def _weapon_files(id, hd, battle_lgp):
    return [id + weapon_role(w) for w in range(hd.weapons) if has_file(id + weapon_role(w), battle_lgp)]


def _tex(data):
    """tex.Tex of a battle TEX: only the palette the file stores (its header counts the PSX CLUT bank, and
    tex.read leaves the bank's other palettes empty, which rgba() can't use)."""
    t = tex.read(data)
    t.palettes = [p for p in t.palettes if len(p)] or t.palettes[:1]
    if t.key_per_palette:
        t.key_per_palette = t.key_per_palette[:len(t.palettes)]
    return t


def textures(id, battle_lgp=BATTLE_LGP):
    """Per texid a mesh.TexRef (FFNx's name: "battle/CLOUD_T00", texture_key; palette 0), None where the file is
    missing."""
    id = id.lower()
    hd = header(id, battle_lgp)
    out = []
    for t in range(hd.textures):
        name = id + tex_role(t)
        out.append(meshmod.TexRef(texture_key(id, t), _tex(read_file(name, battle_lgp)), 0)
                   if has_file(name, battle_lgp) else None)
    return out


@dataclass
class BattleMesh(meshmod.Mesh):
    """A battle scene or posed battle model: mesh.Mesh plus what made it."""
    id: str = ""
    kind: str = ""            # "scene", "party", "enemy", "other"
    animation: int = -1       # the da animation it's posed with (-1: none, the bind pose or a scene)
    frame: int = 0
    weapon: int = -1          # the weapon part drawn (-1 none)
    part_bones: list = field(default_factory=list)     # per mesh part: its bone (-1 scene part, -2 weapon)
    part_matrices: list = field(default_factory=list)  # per mesh part: its 4x4 frame (model = M @ part-local)
    cull: bool = False        # drawn one-sided by the game: render.rasterize drops back faces (scenes; see load_scene)


def _kind(id, hd):
    if hd.kind == 1:
        return "scene"
    if hd.kind == 2:
        return "party"
    sb = scenebin.load(SCENE_BIN) if Path(SCENE_BIN).exists() else None
    return "enemy" if sb is not None and id_index(id) in sb.enemy_scenes else "other"


def load_scene(id, battle_lgp=BATTLE_LGP):
    """A battle scene ("op", or a battle location number) as a BattleMesh in battle space (x, y down, z: pass
    up=(0, -1, 0) to render.Camera): all its parts at the origin, each group with its texture (palette 0, the one
    the file stores and the group draws with).
    cull is set: the game draws battle scenes one-sided (their .p hundreds leave the cull state to the game, which
    culls), and render.rasterize then drops back faces too. Checked on the resting shots of 12 battles in 9 scenes
    (work/models/battle/camera/sheets/cull_*): wherever culling changes our render, the game's picture is the culled
    one: oq's railing (part oqau) right in front of formation 310's eye, facing away (40% of the picture); the walls
    between pc's cameras and the fight (all of it); os's railings, whose back faces fill the gaps between the posts."""
    if isinstance(id, int):
        id = location_id(id)
    id = id.lower()
    hd = header(id, battle_lgp)
    if hd.kind != 1:
        raise ValueError(f"{id} is not a battle scene")
    b = meshmod.Builder()
    texmap = {i: b.add_texture(r) for i, r in enumerate(textures(id, battle_lgp)) if r is not None}
    bones, mats = [], []
    for name in _part_files(id, hd, battle_lgp):
        before = len(b.parts)
        b.add_part(pfile.read(read_file(name, battle_lgp)), None, texmap, name)
        if len(b.parts) > before:
            bones.append(-1)
            mats.append(np.eye(4))
    base = b.mesh()
    return BattleMesh(**vars(base), id=id, kind="scene", part_bones=bones, part_matrices=mats, cull=True)


def animation_frame(id, animation=0, frame=0, battle_lgp=BATTLE_LGP):
    """(Frame, the animation used) of a model: frame 0 of `animation` (the first animation with frames when that
    one is empty). Only frame 0 is decoded."""
    if frame:
        raise ValueError("only frame 0 of battle animations is decoded")
    id = id.lower()
    if not has_file(id + "da", battle_lgp):
        return None, -1
    hd = header(id, battle_lgp)
    blocks = animation_blocks(read_file(id + "da", battle_lgp))
    body = blocks[:hd.animations] or blocks
    order = [animation] + [k for k in range(len(body)) if k != animation]
    for k in order:
        if 0 <= k < len(body) and body[k].bones == len(hd.bones) + 1:
            f = first_frame(body[k])
            if f is not None:
                return f, k
    return None, -1


def load_model(id, animation=0, frame=0, weapon=0, battle_lgp=BATTLE_LGP):
    """A party member or enemy ("rt", or an enemy id number) posed as a BattleMesh in model space (x, y down, z;
    soles at y = 0, facing -z): its skeleton's parts at frame 0 of `animation` (the first animation with frames
    when that one is empty), plus weapon `weapon` (party members: their 16 weapons; -1 for none) where that
    animation's weapon animation holds it. Mesh parts are named after their .p files ("rtam"), textures
    "battle/<FILE>" at palette 0."""
    if isinstance(id, int):
        id = id_name(id)
    id = id.lower()
    hd = header(id, battle_lgp)
    if hd.kind == 1:
        raise ValueError(f"{id} is a battle scene: use load_scene")
    fr, used = animation_frame(id, animation, frame, battle_lgp)
    frames = pose(hd, fr)
    b = meshmod.Builder()
    texmap = {i: b.add_texture(r) for i, r in enumerate(textures(id, battle_lgp)) if r is not None}
    bones, mats = [], []
    for i, bone in enumerate(hd.bones):
        name = id + part_role(i)
        if not bone.has_part or not has_file(name, battle_lgp):
            continue
        before = len(b.parts)
        b.add_part(pfile.read(read_file(name, battle_lgp)), frames[i], texmap, name)
        if len(b.parts) > before:
            bones.append(i)
            mats.append(frames[i])
    drawn = -1
    if weapon is not None and 0 <= weapon < hd.weapons and used >= 0 and has_file(id + weapon_role(weapon), battle_lgp):
        blocks = animation_blocks(read_file(id + "da", battle_lgp))
        k = hd.animations + used
        wf = first_frame(blocks[k]) if k < len(blocks) and blocks[k].bones == 1 else None
        if wf is not None:
            m = _rt(rotation(*wf.rotations[0]), wf.translation)
            name = id + weapon_role(weapon)
            before = len(b.parts)
            b.add_part(pfile.read(read_file(name, battle_lgp)), m, texmap, name)
            if len(b.parts) > before:
                bones.append(-2)
                mats.append(m)
                drawn = weapon
    base = b.mesh()
    return BattleMesh(**vars(base), id=id, kind=_kind(id, hd), animation=used, frame=0, weapon=drawn,
                      part_bones=bones, part_matrices=mats)


# --- cameras --------------------------------------------------------------------------------------------------------

@dataclass
class BattleCamera(render.Camera):
    """A render.Camera the game uses for battles in a scene (eye and target already moved by CAMERA_OFFSET), with
    the scene.bin placement it comes from and the formations that use it."""
    formations: list = field(default_factory=list)  # formation ids (scene.bin scene * 4 + index), one per use
    slots: list = field(default_factory=list)       # per use (aligned with formations): which of the formation's 3
                                                    # camera slots it is (0: the one battles rest on)
    scene_eye: tuple = None                         # scene.bin's numbers, before CAMERA_OFFSET
    scene_target: tuple = None


def focal(height):
    """The battle projection's focal length in pixels for a render `height` pixels tall (measured: see BATTLE_H)."""
    return BATTLE_H * height / PSX_HEIGHT


def game_camera(eye, target, width=640, height=480, near=NEAR, uses=()):
    """The view the game shows for a scene.bin camera placement (eye, look-at as stored): both moved by
    CAMERA_OFFSET, up -y, the battle focal, principal point at the centre (render.Camera's)."""
    off = np.array(CAMERA_OFFSET, float)
    uses = sorted(uses)
    fov_y = float(np.degrees(2 * np.arctan(PSX_HEIGHT / 2 / BATTLE_H)))
    return BattleCamera(eye=np.array(eye, float) + off, target=np.array(target, float) + off,
                        up=np.array([0.0, -1.0, 0.0]), fov_y=fov_y, width=width, height=height, near=near,
                        focal=focal(height),
                        formations=[u[0] for u in uses], slots=[u[1] for u in uses],
                        scene_eye=tuple(eye), scene_target=tuple(target))


def _slot_set(slots):
    if isinstance(slots, str):
        if slots != "all":
            raise ValueError(f"slots: 0, a list of slots or 'all', not {slots!r}")
        return {0, 1, 2}
    if isinstance(slots, int):
        return {slots}
    return set(slots)


def _camera_uses(scene_bin, slots=(0,)):
    """{location: {(eye, target): [(formation id, slot)]}} over all of scene.bin, for the camera slots asked for."""
    want = _slot_set(slots)
    out = {}
    for f in scenebin.load(scene_bin).formations:
        for slot, c in enumerate(f.cameras):
            if slot in want and c.eye != c.target:
                out.setdefault(f.location, {}).setdefault((c.eye, c.target), []).append((f.id, slot))
    return out


def _too_close(a, b, min_angle):
    """Do two cameras see nearly the same view: eye and target within min_angle degrees of each other, as seen
    from the target and the eye?"""
    da = np.subtract(a.target, a.eye)
    db = np.subtract(b.target, b.eye)
    look = np.degrees(np.arccos(np.clip(np.dot(da, db) / np.linalg.norm(da) / np.linalg.norm(db), -1, 1)))
    reach = max(np.linalg.norm(da), np.linalg.norm(db))
    moved = np.degrees(max(np.linalg.norm(np.subtract(a.eye, b.eye)), np.linalg.norm(np.subtract(a.target, b.target)))
                       / reach)
    return look < min_angle and moved < min_angle


def cameras(scene_id, width=640, height=480, near=NEAR, min_angle=0.0, fallback=True, scene_bin=SCENE_BIN, slots=0):
    """The views battles in this scene ("op" or a location number) rest on, as the game shows them: every formation
    fought there (scene.bin), its resting camera, deduplicated, the one most formations rest on first (BattleCamera:
    render.Camera plus the formations using it). Render with the battle mesh's own culling (load_scene sets it).

    How they map to the game (measured 2026-11-06, work/models/battle/camera; sure of it): a formation has three
    camera placements (slots), and its battle comes to rest on slot 0 after the opening camera move (3-4 s) and
    returns to it after every attack. 12 battles in 9 scenes (op, os, pe, oi, pj, pc, ot, oq, ok; normal, back and
    side attacks; three formations whose slots 1-2 are real views) were screenshotted every 0.75 s for 27 s: every
    resting shot is slot 0, none slot 1 or 2. The view is scene.bin's eye and look-at as stored (x, y, z in battle
    space, no axis flips or reordering), moved by CAMERA_OFFSET (14 units up), up -y with no roll, focal 512 per 240
    lines, principal point at the centre (BATTLE_H): points matched between the game's picture and our render of the
    scene from this camera land within a median 0.6-1.0 px of each other per battle (0.8 overall, 90% within 1.8)
    at 960 x 720. Formations rest on slot 0 even where slots 1-2 differ, so what slots 1-2 are for isn't known
    (FFNx's formation_camera[curr_formation_camera_idx]; perhaps the Camera angle "Auto" setting, or scripted
    battles); 732 formations hold PLACEHOLDER_CAMERA there. The old order, by use in any slot, put that placeholder
    first in most scenes (the steep view down at oq's floor that didn't match formation 310's battle).

    slots: which placements to give: 0 (the resting view; default), "all" (slots 1-2 too), or a list of slots.
    min_angle: also leave out a camera within that many degrees of a more used one (its look direction, and its eye
    and target as seen from the other's distance), 0 keeps every distinct one (the grassland has 79 resting views,
    86 with slots="all").
    fallback: a scene no formation uses (location 82) gets the resting view most formations use, with no
    formations; otherwise it gets none."""
    loc = scene_id if isinstance(scene_id, int) else scene_location(scene_id)
    if loc is None:
        raise ValueError(f"{scene_id} is not a battle scene")
    uses = _camera_uses(scene_bin, slots)

    def order(kv):
        u = kv[1]
        return (-len({x[0] for x in u if x[1] == 0}), -len({x[0] for x in u}), min(u))

    out = []
    for (eye, target), u in sorted(uses.get(loc, {}).items(), key=order):
        cam = game_camera(eye, target, width, height, near, u)
        if min_angle > 0 and any(_too_close(cam, o, min_angle) for o in out):
            continue
        out.append(cam)
    if not out and fallback:
        count = Counter()
        for views in _camera_uses(scene_bin, 0).values():
            for view, u in views.items():
                count[view] += len({x[0] for x in u})
        if count:
            eye, target = count.most_common(1)[0][0]
            out.append(game_camera(eye, target, width, height, near))
    return out


RING_FOV = 60.0  # ring_cameras: each view's vertical field of view (degrees)


def ring_cameras(scene_id, n=6, width=1280, height=960, near=NEAR, pitch=8.0):
    """Views all round from the middle of the action, looking out at the horizon (a little up, `pitch` degrees): what
    a battle's cameras see of a scene's sky and far backdrop whichever way they swing (they all stand near the
    middle, and those are far away). For painting the textures a scene uses once (paint.paint projects them; the
    ones it lays many times over are painted as sheets): views from the battle cameras alone painted only what they
    saw, and every other angle showed the original there (user, 2026-11-07: "bad at any angle other than the fixed
    camera"). The middle: the battle cameras' look-at points, averaged; the height: their eyes'."""
    cams = cameras(scene_id, width, height, near)
    if cams:
        tgt = np.mean([c.target for c in cams], 0)
        eye_y = float(np.median([c.eye[1] for c in cams]))
    else:
        tgt, eye_y = np.zeros(3), -1500.0
    centre = np.array([tgt[0], eye_y, tgt[2]])
    out = []
    for i in range(n):
        a = 2 * np.pi * i / n
        d = np.array([np.sin(a), -np.tan(np.radians(pitch)), np.cos(a)])  # y down: up is -y
        out.append(render.Camera(eye=centre, target=centre + d * 1000.0, up=np.array([0.0, -1.0, 0.0]),
                                 fov_y=RING_FOV, width=width, height=height, near=near))
    return out


# --- catalogue ------------------------------------------------------------------------------------------------------

def _stamp(path):
    st = Path(path).stat()
    return [st.st_size, int(st.st_mtime)]


_catalogue = {}


def catalogue(cache=CACHE, battle_lgp=BATTLE_LGP, scene_bin=SCENE_BIN):
    """Every battle.lgp id that has a model (parts): {id: {
        "id", "index", "kind" ("scene", "party", "enemy", "other"), "name" (enemies: their scene.bin name; party
        members: PARTY; scenes: "<location n>: <its most common enemies>"),
        "textures" ([{"file", "key" (FFNx override name without _NN), "width", "height", "colors", "palettes"
        (stored: 1), "bank" (the header's palette count), "clut_row", "color_key"}]), "parts" ([.p files]),
        "weapons" ([.p files]), "bones", "animations" (body), "weapon_animations",
        enemies and others: "names" ({name: scenes}), "enemy_id", "formations" ([ids]), "scenes" ([battle scene ids]),
        scenes: "location", "formations", "enemies" ({name: formations}), "cameras" (distinct idle cameras),
        "coverage" (share of the main camera's view the scene fills: the rest shows the game's black)}}.
    Kept in `cache` (a JSON file, None for none) and redone when battle.lgp or scene.bin changes."""
    stamp = {"version": CACHE_VERSION, "battle": _stamp(battle_lgp), "scene": _stamp(scene_bin)}
    if Path(battle_lgp) == BATTLE_LGP and DIRECT.is_dir():  # the game's direct/ replacements (read_file) count too
        files = [p.stat() for p in DIRECT.iterdir() if p.is_file()]
        stamp["direct"] = [len(files), sum(s.st_size for s in files), int(max((s.st_mtime for s in files), default=0))]
    key = (str(cache), json.dumps(stamp))
    if key in _catalogue:
        return _catalogue[key]
    if cache and Path(cache).exists():
        try:
            data = json.loads(Path(cache).read_text(encoding="utf-8"))
            if data.get("stamp") == stamp:
                _catalogue[key] = data["models"]
                return data["models"]
        except (ValueError, KeyError):
            pass
    sb = scenebin.load(scene_bin)
    # who fights where: enemy id -> formations, battle scene -> formations and enemies
    enemy_forms, loc_forms, loc_enemies = {}, {}, {}
    for f in sb.formations:
        loc_forms.setdefault(f.location, []).append(f.id)
        for eid, _pos in f.enemies:
            enemy_forms.setdefault(eid, set()).add(f.id)
            loc_enemies.setdefault(f.location, Counter())[eid] += 1
    form_loc = {f.id: f.location for f in sb.formations}
    models = {}
    for id in ids(battle_lgp):
        try:
            hd = header(id, battle_lgp)
        except (KeyError, ValueError):
            continue
        parts = _part_files(id, hd, battle_lgp)
        if not parts:
            continue
        idx = id_index(id)
        rows = clut_rows(id, battle_lgp)
        texs = []
        for t in range(hd.textures):
            name = id + tex_role(t)
            if not has_file(name, battle_lgp):
                continue
            data = read_file(name, battle_lgp)
            tx = _tex(data)
            v = struct.unpack_from("<59I", data)
            texs.append({"file": name, "key": texture_key(id, t), "width": tx.width, "height": tx.height,
                         "colors": len(tx.palettes[0]) if tx.palettes else 0, "palettes": tx.count, "bank": v[12],
                         "clut_row": rows.get(t), "color_key": tx.color_key})
        e = {"id": id, "index": idx, "kind": _kind(id, hd), "name": id, "textures": texs, "parts": parts,
             "weapons": _weapon_files(id, hd, battle_lgp), "bones": len(hd.bones), "animations": hd.animations,
             "weapon_animations": hd.weapon_animations}
        if e["kind"] == "scene":
            loc = idx - SCENE_BASE
            ens = loc_enemies.get(loc, Counter())
            named = Counter()
            for eid, n in ens.items():
                named[sb.names.get(eid, f"enemy {eid}")] += n
            cams = cameras(id, 160, 120, fallback=False, scene_bin=scene_bin)
            # how much of its main camera's view the scene fills: some leave the rest to the black the game clears
            # to (the Sector 5 bridge's drop, Bizarro Sephiroth's void); og, qo and rk are blank (a 2x1 texture)
            cover = (float((render.rasterize(load_scene(id, battle_lgp), cams[0]).tri >= 0).mean())
                     if cams else 0.0)
            e.update(location=loc, formations=loc_forms.get(loc, []), enemies=dict(named.most_common()),
                     cameras=len(cams), coverage=round(cover, 3))
            # the test formations' dummy enemies have placeholder names in other letters: left out of the name
            top = [n for n, _ in named.most_common() if n and all(c.isascii() or c == "·" for c in n)][:3]
            e["name"] = f"location {loc}" + (": " + ", ".join(top) if top else "")
        elif e["kind"] == "party":
            e["name"] = PARTY.get(id, id)
        else:
            forms = sorted(enemy_forms.get(idx, []))
            e.update(enemy_id=idx, formations=forms,
                     scenes=sorted({location_id(form_loc[f]) for f in forms if f in form_loc}),
                     names={sb.names[idx]: len(sb.enemy_scenes.get(idx, []))} if idx in sb.names else {})
            if idx in sb.names and sb.names[idx]:
                e["name"] = sb.names[idx]
        models[id] = e
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps({"stamp": stamp, "models": models}, indent=1), encoding="utf-8")
    _catalogue[key] = models
    return models
