"""FF7's field models (data/field/char.lgp) and the fields that use them (flevel.lgp's model loader sections): read,
pose and turn into a mesh.Mesh (docs/models-plan.md, "field models").

char.lgp holds, per model:
- An HRC skeleton (text): ":HEADER_BLOCK 2", ":SKELETON <name>", ":BONES <n>", then per bone four lines: its name,
  its parent's name ("root" for the model's root), its length, and "<count> <RSD names...>". Lines starting with #
  are comments (ERHA.HRC has one). A model without bones (105 objects: doors, the materia) says ":BONES 0" and still
  has one bone, "null", holding its single part.
- RSD files (text): PLY=<name>.PLY (the part is <name>.P), MAT=, GRP=, NTEX=<n>, TEX[i]=<name>.TIM (the texture is
  <name>.TEX). A .p group's texid indexes the RSD's TEX list.
- .a animations: a 36-byte header (u32 version 1, frames, bones; 4 bytes of rotation order, always 1 0 2: Y, X, Z;
  then 5 u32 the game fills at run time), then per frame 6 floats (the root's rotation x y z in degrees and its
  translation x y z) and 3 floats per bone (its rotation x y z in degrees). Bones past the animation's count keep no
  rotation (FFNx's draw_3d_model gives them a zero rotation too).

Posing, as Kimera draws field skeletons (and the game's frame_animation walks them):
- The root's frame is T(tx, -ty, tz) R(root rotation): the .a's root height is up-positive while model space is y
  down. Checked on the 190 field models with 15+ bones: their first animation's frame 0 puts the soles at y = 0
  (median 0.09, 80% within -1.5..0.6) with -ty, and at y = 2 ty (15 to 29) without the sign.
- A bone's frame is its parent's frame, moved along the parent's -z by the parent's length (nothing for the root),
  then rotated by the bone's own rotation: F(bone) = F(parent) T(0, 0, -length(parent)) R(bone).
- R(x, y, z) = Ry(y) Rx(x) Rz(z) (column vectors): the header's order Y X Z, applied to a point z first.
- A bone's parts (its RSDs' .p files) are drawn in its frame (no offset). Model space: x, y down, z; the models face
  -z (Cloud's face looks at a camera on the -z side), their feet at y = 0 and the origin between them.

flevel.lgp: every field's section 2 (the model loader) lists the models its entities use: u16 0, u16 count, u16 scale
(the field's model scale, 512 in most: FFNx's external meshes are drawn at scale / 128), then per model: u16 length +
name ("<field><model>.char", e.g. md1stinmain_n_cloud.char), u16 (0/1), 8 chars HRC ("AAAA.HRC"), 4 chars scale
text ("512\\0"), u16 animation count, 3 lights (r g b bytes, direction 3 x s16 of length 4096), the global light
colour (r g b), then per animation u16 length + name ("ACFE.aki": the .a file ACFE.A; the extension names the
animator) and a u16. An entity starts on its model's first animation: the model's standing pose.

Use: catalogue() (every model, what the fields call it, where and how it's animated), load_model(hrc) (a posed
FieldMesh), atlas(hrc or mesh) (atlas.build: a texture space for every triangle), export_part / export_model (FFNx
external meshes from an atlas; gltf.py says what FFNx reads)."""
import json
import re
import struct
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import lzs
from ..lgp import LgpArchive
from . import mesh as meshmod
from . import pfile, tex

GAME_WORKDIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
CHAR_LGP = GAME_WORKDIR / "data" / "field" / "char.lgp"
FLEVEL_LGP = GAME_WORKDIR / "data" / "field" / "flevel.lgp"
REPO = Path(__file__).resolve().parents[2]
# catalogue() scans every field (about 35 s): its result is kept here, redone when an LGP changes
CACHE = REPO / "work" / "models" / "field" / "catalogue.json"
CACHE_VERSION = 3

# model name prefixes left out of a model's short name: main_n_cloud -> cloud, fieldbg_saveicn -> saveicn
SHORT_PREFIXES = ("main_n_", "main_", "sub_n_", "sub_", "fieldbg_", "n_")


# --- char.lgp formats ---------------------------------------------------------------------------------------------

@dataclass
class Bone:
    name: str
    parent: int       # index of the parent bone, -1 for the root
    length: float
    rsds: list        # RSD names ("AAAB")


@dataclass
class Skeleton:
    name: str         # ":SKELETON" (n_cloud_sk)
    bones: list       # [Bone], parents before children


@dataclass
class Rsd:
    name: str
    ply: str          # the .p part's name ("AAAC")
    textures: list    # the .tex names ("AABB"), in TEX[i] order


@dataclass
class Animation:
    name: str                 # "ACFE"
    root_rotation: np.ndarray     # (frames, 3) degrees x y z
    root_translation: np.ndarray  # (frames, 3)
    rotations: np.ndarray     # (frames, bones, 3) degrees x y z
    order: tuple = (1, 0, 2)  # the header's rotation order (always Y X Z in char.lgp)

    @property
    def frames(self):
        return len(self.rotations)


def read_hrc(text, name=""):
    """Skeleton from an HRC file's text."""
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    skel = ""
    body = []
    for l in lines:
        if l.startswith(":"):
            if l.upper().startswith(":SKELETON"):
                skel = l.split(None, 1)[1] if len(l.split()) > 1 else ""
            continue
        body.append(l)
    bones, index = [], {}
    for i in range(0, len(body) - 3, 4):
        bname, pname, length, rsd = body[i:i + 4]
        words = rsd.split()
        count = int(words[0]) if words and words[0].isdigit() else 0
        bones.append(Bone(bname, index.get(pname, -1), float(length), [w.upper() for w in words[1:1 + count]]))
        index[bname] = len(bones) - 1  # a later bone of the same name is the one children mean (as the game's stack)
    return Skeleton(skel or name, bones)


def read_rsd(text, name=""):
    """Rsd from an RSD file's text."""
    ply, texs = "", {}
    for l in text.splitlines():
        l = l.strip()
        if l.upper().startswith("PLY="):
            ply = l[4:].split(".")[0].upper()
        m = re.match(r"TEX\[(\d+)\]=(.+)", l, re.I)
        if m:
            texs[int(m.group(1))] = m.group(2).split(".")[0].upper()
    return Rsd(name, ply, [texs[i] for i in sorted(texs)])


def read_a(data, name=""):
    """Animation from an .a file's bytes."""
    version, frames, bones = struct.unpack_from("<3I", data, 0)
    order = tuple(data[12:15])
    if version != 1:
        raise ValueError(f"{name}: not a field animation")
    per = 6 + 3 * bones
    f = np.frombuffer(data, np.float32, frames * per, 36).reshape(frames, per).astype(np.float64)
    return Animation(name, f[:, 0:3], f[:, 3:6], f[:, 6:].reshape(frames, bones, 3), order)


def rotation(x, y, z):
    """3x3 rotation of a bone (degrees): Ry(y) Rx(x) Rz(z), for column vectors (the .a order Y X Z)."""
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


def local_transforms(skel, anim=None, frame=0):
    """Per bone its (translation (3,), rotation 3x3) in its parent's frame, and the root's: ([(t, r)], (t, r)).
    t is the parent's length along -z (0 under the root); r the bone's rotation in that frame."""
    f = min(max(int(frame), 0), anim.frames - 1) if anim is not None and anim.frames else 0
    if anim is not None and anim.frames:
        root = (anim.root_translation[f] * (1, -1, 1), rotation(*anim.root_rotation[f]))  # the height is up-positive
    else:
        root = (np.zeros(3), np.eye(3))
    out = []
    for i, b in enumerate(skel.bones):
        rot = anim.rotations[f, i] if anim is not None and anim.frames and i < anim.rotations.shape[1] else (0, 0, 0)
        plen = skel.bones[b.parent].length if b.parent >= 0 else 0.0
        out.append((np.array([0.0, 0.0, -plen]), rotation(*rot)))
    return out, root


def pose(skel, anim=None, frame=0):
    """Per bone its 4x4 frame in model space (column vectors: model = M @ bone-local) at the animation's frame
    (no animation: every rotation zero, the bind pose)."""
    local, (rt, rr) = local_transforms(skel, anim, frame)
    root = _rt(rr, rt)
    out = []
    for b, (t, r) in zip(skel.bones, local):
        parent = out[b.parent] if b.parent >= 0 else root
        out.append(parent @ _rt(r, t))
    return out


def rotations(angles):
    """rotation() for many: angles (..., 3) degrees x y z -> (..., 3, 3)."""
    a = np.radians(np.asarray(angles, np.float64))
    c, s = np.cos(a), np.sin(a)
    one, zero = np.ones(a.shape[:-1]), np.zeros(a.shape[:-1])
    shape = a.shape[:-1] + (3, 3)
    rx = np.stack([one, zero, zero, zero, c[..., 0], -s[..., 0], zero, s[..., 0], c[..., 0]], -1).reshape(shape)
    ry = np.stack([c[..., 1], zero, s[..., 1], zero, one, zero, -s[..., 1], zero, c[..., 1]], -1).reshape(shape)
    rz = np.stack([c[..., 2], -s[..., 2], zero, s[..., 2], c[..., 2], zero, zero, zero, one], -1).reshape(shape)
    return ry @ rx @ rz


def pose_frames(skel, anim):
    """pose() for every frame of an animation: (frames, bones, 4, 4)."""
    F, B = anim.frames, len(skel.bones)
    rot = np.zeros((F, B, 3))
    k = min(B, anim.rotations.shape[1])
    rot[:, :k] = anim.rotations[:, :k]
    local = np.tile(np.eye(4), (F, B, 1, 1))
    local[:, :, :3, :3] = rotations(rot)
    for i, b in enumerate(skel.bones):
        local[:, i, 2, 3] = -(skel.bones[b.parent].length if b.parent >= 0 else 0.0)
    root = np.tile(np.eye(4), (F, 1, 1))
    root[:, :3, :3] = rotations(anim.root_rotation)
    root[:, :3, 3] = anim.root_translation * (1, -1, 1)
    out = np.zeros((F, B, 4, 4))
    for i, b in enumerate(skel.bones):
        out[:, i] = (out[:, b.parent] if b.parent >= 0 else root) @ local[:, i]
    return out


# --- the archives ---------------------------------------------------------------------------------------------------

_lock = threading.RLock()
_archives = {}


def _archive(path):
    path = str(path)
    with _lock:
        if path not in _archives:
            _archives[path] = LgpArchive(path)
        return _archives[path]


def char_read(name, char_lgp=CHAR_LGP):
    """A file of char.lgp (any case); KeyError when it isn't there."""
    with _lock:
        return _archive(char_lgp).read(name)


def char_has(name, char_lgp=CHAR_LGP):
    return name.lower() in _archive(char_lgp).entries


def char_names(ext, char_lgp=CHAR_LGP):
    """char.lgp's names with an extension (".hrc"), upper case without it."""
    ext = ext.lower()
    return sorted(n[:-len(ext)].upper() for n in _archive(char_lgp).names() if n.lower().endswith(ext))


def _base(name):
    """"AAAA.HRC", "aaaa", "ACFE.aki" -> "AAAA", "ACFE"."""
    return Path(str(name)).stem.upper() if "." in str(name) else str(name).upper()


def skeleton(hrc, char_lgp=CHAR_LGP):
    hrc = _base(hrc)
    return read_hrc(char_read(hrc + ".HRC", char_lgp).decode("ascii", "replace"), hrc)


def rsd(name, char_lgp=CHAR_LGP):
    name = _base(name)
    return read_rsd(char_read(name + ".RSD", char_lgp).decode("ascii", "replace"), name)


def load_animation(name, char_lgp=CHAR_LGP):
    name = _base(name)
    return read_a(char_read(name + ".A", char_lgp), name)


# --- flevel.lgp: who uses which model -------------------------------------------------------------------------------

@dataclass
class FieldModel:
    field: str        # the field (md1stin)
    name: str         # its name there without the field and ".char" (main_n_cloud)
    hrc: str          # "AAAA"
    scale: int        # the model's own scale text (512)
    field_scale: int  # the field's model scale (the section header's; FFNx draws external meshes at this / 128)
    animations: list  # ["ACFE", ...] (.a names), the first is what the entity starts on
    lights: list      # 3 x [r, g, b, dx, dy, dz] (direction of length 4096)
    global_light: list  # [r, g, b]


def _sections(raw):
    (count,) = struct.unpack_from("<I", raw, 2)
    offs = struct.unpack_from(f"<{count}I", raw, 6)
    out = []
    for off in offs:
        (length,) = struct.unpack_from("<I", raw, off)
        out.append(raw[off + 4:off + 4 + length])
    return out


def model_loader(field_name, sec):
    """[FieldModel] from a field's section 2."""
    _, count, scale = struct.unpack_from("<HHH", sec, 0)
    pos, out = 6, []
    for _ in range(count):
        (n,) = struct.unpack_from("<H", sec, pos)
        raw_name = sec[pos + 2:pos + 2 + n].split(b"\0")[0].decode("ascii", "replace")
        pos += 2 + n
        hrc = sec[pos + 2:pos + 10].split(b"\0")[0].decode("ascii", "replace")
        sc = sec[pos + 10:pos + 14].split(b"\0")[0].decode("ascii", "replace")
        (nanim,) = struct.unpack_from("<H", sec, pos + 14)
        lights = []
        for k in range(3):
            o = pos + 16 + 9 * k
            lights.append(list(sec[o:o + 3]) + list(struct.unpack_from("<3h", sec, o + 3)))
        glob = list(sec[pos + 43:pos + 46])
        pos += 46
        anims = []
        for _ in range(nanim):
            (n,) = struct.unpack_from("<H", sec, pos)
            anims.append(_base(sec[pos + 2:pos + 2 + n].split(b"\0")[0].decode("ascii", "replace")))
            pos += 4 + n
        name = raw_name[:-5] if raw_name.lower().endswith(".char") else raw_name
        if name.lower().startswith(field_name.lower()):
            name = name[len(field_name):]
        out.append(FieldModel(field_name, name, _base(hrc), int(sc) if sc.isdigit() else scale, scale, anims, lights,
                              glob))
    return out


def field_models(flevel_lgp=FLEVEL_LGP, progress=None):
    """Every field's [FieldModel]: {field: [FieldModel]} (decompresses all of flevel.lgp: about 35 s)."""
    arc = LgpArchive(str(flevel_lgp))
    out = {}
    try:
        names = [n for n in arc.names() if "." not in n and n.lower() != "maplist"]
        for i, n in enumerate(names):
            if progress:
                progress(i, len(names), n)
            try:
                sec = _sections(lzs.decompress(arc.read(n)))[2]
                out[n] = model_loader(n, sec)
            except (struct.error, IndexError, ValueError):
                continue  # not a field
    finally:
        arc.close()
    return out


def short_name(name):
    """A model's short name: its field name without the cast prefix (main_n_cloud -> cloud, shinra_hei stays)."""
    for p in SHORT_PREFIXES:
        if name.startswith(p) and len(name) > len(p):
            return name[len(p):]
    return name


def _stamp(path):
    st = Path(path).stat()
    return [st.st_size, int(st.st_mtime)]


_catalogue = {}


def catalogue(cache=CACHE, flevel_lgp=FLEVEL_LGP, char_lgp=CHAR_LGP, progress=None):
    """Every field model (HRC): {hrc: {
        "hrc", "skeleton" (its name), "name" (short: "cloud"), "names" ({name the fields call it: uses}),
        "bones" (count), "parts" ([.p names]), "textures" ([.tex names]),
        "fields" ({field: {"names", "animations", "scale", "field_scale", "lights", "global_light"}}),
        "animations" ([.a names], the most used first), "first_animation" (what most fields start it on)}}.
    Kept in `cache` (a JSON file, None for none) and redone when either LGP changes."""
    stamp = {"version": CACHE_VERSION, "flevel": _stamp(flevel_lgp), "char": _stamp(char_lgp)}
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
    uses = field_models(flevel_lgp, progress)
    models = {}
    for hrc in char_names(".hrc", char_lgp):
        try:
            sk = skeleton(hrc, char_lgp)
        except (KeyError, ValueError):
            continue
        parts, texs = [], []
        for b in sk.bones:
            for r in b.rsds:
                if not char_has(r + ".RSD", char_lgp):
                    continue
                rs = rsd(r, char_lgp)
                parts.append(rs.ply)
                texs += [t for t in rs.textures if t not in texs]
        models[hrc] = {"hrc": hrc, "skeleton": sk.name, "name": hrc.lower(), "names": {}, "bones": len(sk.bones),
                       "parts": parts, "textures": texs, "fields": {}, "animations": [], "first_animation": None}
    # names: a few fields' model names don't start with the field's name (q_1main_n_cloud in qa): the name other
    # fields give the same model, when this one ends with it
    plain = {}
    for f, ms in uses.items():
        for m in ms:
            if m.hrc in models and not re.match(r"^\d", m.name):
                plain.setdefault(m.hrc, Counter())[m.name] += 1
    for f, ms in sorted(uses.items()):
        for m in ms:
            e = models.get(m.hrc)
            if e is None:
                continue
            name = m.name
            for known, _ in (plain.get(m.hrc) or Counter()).most_common():
                if name != known and name.endswith(known):
                    name = known
                    break
            e["names"][name] = e["names"].get(name, 0) + 1
            fe = e["fields"].setdefault(f, {"names": [], "animations": [], "scale": m.scale,
                                            "field_scale": m.field_scale, "lights": m.lights,
                                            "global_light": m.global_light})
            if name not in fe["names"]:
                fe["names"].append(name)
            fe["animations"] += [a for a in m.animations if a not in fe["animations"]]
    for e in models.values():
        if e["names"]:
            top = max(e["names"].items(), key=lambda kv: kv[1])[0]
            e["name"] = short_name(top)
        count, first = Counter(), Counter()
        for fe in e["fields"].values():
            count.update(fe["animations"])
            if fe["animations"]:
                first[fe["animations"][0]] += 1
        e["animations"] = [a for a, _ in count.most_common() if char_has(a + ".A", char_lgp)]
        firsts = [a for a, _ in first.most_common() if char_has(a + ".A", char_lgp)]
        e["first_animation"] = firsts[0] if firsts else (e["animations"][0] if e["animations"] else None)
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps({"stamp": stamp, "models": models}), encoding="utf-8")
    _catalogue[key] = models
    return models


def find(name, cache=CACHE):
    """HRC names whose model is called `name` (short or field name, any case: "cloud", "main_n_cloud") or that are
    `name` ("AAAA")."""
    name = name.lower()
    cat = catalogue(cache)
    if _base(name) in cat:
        return [_base(name)]
    hits = [h for h, e in cat.items() if e["name"] == name or name in e["names"]]
    return sorted(hits, key=lambda h: -sum(cat[h]["names"].values()))


# --- models and posed meshes ----------------------------------------------------------------------------------------

@dataclass
class PartRef:
    name: str         # the .p part ("AAAC")
    bone: int         # the bone it's drawn in
    rsd: str          # the RSD naming it
    part: object      # pfile.Part
    textures: list    # per texid: mesh.TexRef or None (missing)


@dataclass
class Model:
    hrc: str
    skeleton: Skeleton
    parts: list       # [PartRef] in the order the bones list them


_models = {}


def model(hrc, char_lgp=CHAR_LGP):
    """A field model's skeleton, parts and textures (cached)."""
    hrc = _base(hrc)
    key = (hrc, str(char_lgp))
    with _lock:
        if key in _models:
            return _models[key]
    sk = skeleton(hrc, char_lgp)
    texcache, parts = {}, []
    for bi, b in enumerate(sk.bones):
        for r in b.rsds:
            if not char_has(r + ".RSD", char_lgp):
                continue
            rs = rsd(r, char_lgp)
            if not char_has(rs.ply + ".P", char_lgp):
                continue
            refs = []
            for t in rs.textures:
                if t not in texcache:
                    try:
                        texcache[t] = meshmod.TexRef(f"char/{t}", tex.read(char_read(t + ".TEX", char_lgp)), 0)
                    except (KeyError, ValueError):
                        texcache[t] = None
                refs.append(texcache[t])
            parts.append(PartRef(rs.ply, bi, r, pfile.read(char_read(rs.ply + ".P", char_lgp)), refs))
    m = Model(hrc, sk, parts)
    with _lock:
        _models[key] = m
    return m


@dataclass
class FieldMesh(meshmod.Mesh):
    """A posed field model: mesh.Mesh plus what made it (for the atlas and the glTF export)."""
    hrc: str = ""
    animation: str = ""   # the .a it's posed with ("" for the bind pose)
    frame: int = 0
    part_bones: list = field(default_factory=list)     # per mesh part: its bone index
    part_matrices: list = field(default_factory=list)  # per mesh part: its 4x4 frame (model = M @ part-local)


def default_animation(hrc, cache=CACHE):
    """The animation most fields start the model on (its standing pose), or None."""
    e = catalogue(cache).get(_base(hrc))
    return e and e["first_animation"]


def load_model(hrc, animation=None, frame=0, cache=CACHE, char_lgp=CHAR_LGP):
    """A field model posed as a FieldMesh (a mesh.Mesh) in model space: x, y down, z (pass up=(0, -1, 0) to
    render.Camera). animation: an .a name ("ACFE", "ACFE.aki", "acfe.a"), an Animation, "" for the bind pose (no
    rotations), or None for the model's first animation in the fields (catalogue: what most fields start it on).
    Mesh parts are named after their .p files ("AAAC"), textures "char/<TEX>" at palette 0."""
    m = model(hrc, char_lgp)
    if animation is None:
        animation = default_animation(m.hrc, cache) or ""
    if isinstance(animation, str):
        anim = load_animation(animation, char_lgp) if animation else None
    else:
        anim = animation
    frames = pose(m.skeleton, anim, frame)
    b = meshmod.Builder()
    bones, mats = [], []
    for p in m.parts:
        texmap = {i: b.add_texture(r) for i, r in enumerate(p.textures) if r is not None}
        before = len(b.parts)
        b.add_part(p.part, frames[p.bone], texmap, p.name)
        if len(b.parts) > before:
            bones.append(p.bone)
            mats.append(frames[p.bone])
    base = b.mesh()
    return FieldMesh(**vars(base), hrc=m.hrc, animation=anim.name if anim is not None else "",
                     frame=min(int(frame), anim.frames - 1) if anim is not None and anim.frames else 0,
                     part_bones=bones, part_matrices=mats)


# --- the atlas and FFNx external meshes -----------------------------------------------------------------------------

def atlas(mesh_or_model, **kw):
    """atlas.build of a field model: a FieldMesh, or an HRC name (posed with its first animation). kw: atlas.build's
    (size, max_size, density, pad, textured, modulate). The Atlas' mesh is a FieldMesh with the atlas UVs."""
    from . import atlas as atlasmod
    m = mesh_or_model if isinstance(mesh_or_model, meshmod.Mesh) else load_model(mesh_or_model)
    kw.setdefault("name", getattr(m, "hrc", "") or "model")
    return atlasmod.build(m, **kw)


CREASE = 60.0  # degrees: faces meeting at more than this keep their own normals in an export


def smooth_normals(pos, crease=CREASE):
    """Per corner normals (t, 3, 3) of triangles (t, 3, 3): the area-weighted face normals around each corner's
    (welded) position whose faces turn less than `crease` from its own. Faces point outward the way FF7 winds them:
    (p2 - p0) x (p1 - p0) (the other cross product points inward on nearly every part checked)."""
    p = pos.astype(np.float64)
    n = np.cross(p[:, 2] - p[:, 0], p[:, 1] - p[:, 0])
    unit = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    scale = max(1e-6, float(np.abs(p).max()))
    _, vid = np.unique(np.round(p.reshape(-1, 3) / (scale * 1e-5)).astype(np.int64), axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    cos = np.cos(np.radians(crease))
    out = np.repeat(unit[:, None], 3, 1)
    faces = {}
    for t in range(len(p)):
        for c in range(3):
            faces.setdefault(vid[t, c], []).append(t)
    for t in range(len(p)):
        for c in range(3):
            near = [g for g in faces[vid[t, c]] if unit[g] @ unit[t] >= cos]
            s = n[near].sum(0)
            if np.linalg.norm(s) > 1e-12:
                out[t, c] = s / np.linalg.norm(s)
    return out


def _part_texture(a, part, image):
    """Cut a part's charts out of the atlas image (any whole multiple of the atlas' size) into a texture of their
    own, texel for texel: (texture RGBA, {chart index in a.charts: (dx, dy) to add to its texels}, scale)."""
    from . import atlas as atlasmod
    k = image.shape[0] / a.size
    mine = [i for i, c in enumerate(a.charts) if c.part == part]
    rects = [tuple(int(round(v * k)) for v in a.charts[i].rect) for i in mine]
    sizes = [(w, h) for _, _, w, h in rects]
    area = sum(w * h for w, h in sizes)
    width = int(-(-max(max(w for w, _ in sizes), int(np.sqrt(area) * 1.1)) // 4) * 4)
    pos, top = atlasmod.skyline(sizes, width)
    height = int(-(-top // 4) * 4)
    out = np.zeros((height, width, 4), np.uint8)
    used = np.zeros((height, width), bool)
    moves = {}
    for i, (x, y, w, h), (nx, ny) in zip(mine, rects, pos):
        out[ny:ny + h, nx:nx + w] = image[y:y + h, x:x + w]
        used[ny:ny + h, nx:nx + w] = True
        moves[i] = (nx - x, ny - y)
    # free space: the nearest chart's colour (nothing samples it; keeps a DDS compressor's blocks clean)
    return atlasmod.fill(out, used), moves, k


BLEND_SHARE = {1: 1.0, 3: 0.25}  # FF7's blend modes as a share of the colour added: additive, quarter additive


def additive_alpha(a, image):
    """The atlas image with its blended (effect) triangles' texels made to add their colour under FFNx's alpha
    blending (src x alpha + dst x (1 - alpha), the only blending an external mesh gets): with c the colour the game
    adds (mode 1: all of it, 3: a quarter), alpha = max(c) / 255 and rgb = c / alpha, which draws c + dst x (1 - alpha):
    additive where the background is dark, a little darker over bright ones. Black adds nothing and is discarded
    (alpha 0), as additive black is invisible. Mode 2 (subtractive) has no such form (no field model uses it)."""
    from . import atlas as atlasmod
    k = image.shape[0] / a.size
    rows = np.minimum((np.arange(image.shape[0]) / k).astype(int), a.size - 1)
    cols = np.minimum((np.arange(image.shape[1]) / k).astype(int), a.size - 1)
    tri = a.tri[rows][:, cols]
    mode = np.where(tri >= 0, a.mesh.blend[np.maximum(tri, 0)], -1)
    out = image.copy()
    for md, share in BLEND_SHARE.items():
        sel = mode == md
        if not sel.any():
            continue
        px = out[sel].astype(np.float32)
        c = px[:, :3] * share
        alpha = c.max(1) / 255.0
        rgb = c / np.maximum(alpha, 1 / 255.0)[:, None]
        alpha *= px[:, 3] / 255.0  # cut-outs stay cut out
        out[sel] = np.concatenate([np.clip(np.round(rgb), 0, 255), np.round(alpha * 255)[:, None]], 1)
    return atlasmod.fill(out, tri >= 0)


def _export(a, parts, out_dir, animations=None, image=None, texture_names=None, blended="alpha", cache=CACHE,
            char_lgp=CHAR_LGP):
    """export_part for several parts at once (each animation is posed once for all of them)."""
    from . import gltf
    m = a.mesh
    if not isinstance(m, FieldMesh):
        raise ValueError("export needs an atlas of a field model (field.atlas)")
    skel = model(m.hrc, char_lgp).skeleton
    out_dir = Path(out_dir)
    image = a.image if image is None else np.asarray(image)
    if blended == "alpha" and (m.blend[a.selected] >= 0).any():
        image = additive_alpha(a, image)
    if animations is None:
        animations = list((catalogue(cache).get(m.hrc) or {}).get("animations") or [])
    animations = [_base(n) for n in animations]
    if m.animation:
        animations.insert(0, m.animation)
    animations = [n for n in dict.fromkeys(animations) if char_has(n + ".A", char_lgp)]
    rest = pose(skel, load_animation(animations[0], char_lgp) if animations else None, 0)
    jobs = []
    for pi in parts:
        name, bone = m.parts[pi], m.part_bones[pi]
        M = np.asarray(m.part_matrices[pi], np.float64)
        chart_of = {int(t): ci for ci, c in enumerate(a.charts) if c.part == pi for t in c.tris}
        tris = np.nonzero(m.part == pi)[0]
        # degenerate triangles share a spot charted with another part: left out (they have no area)
        tris = np.array([t for t in tris if not a.selected[t] or int(t) in chart_of], np.int64)
        # blended (effect) triangles: kept with additive_alpha's texels, left out, or drawn as they are ("opaque");
        # either way after the part's solid triangles
        drop = (m.blend[tris] >= 0) if blended == "skip" else (m.blend[tris] == 2) if blended == "alpha" else None
        if drop is not None:
            tris = tris[~drop]
        tris = tris[np.argsort(m.blend[tris] >= 0, kind="stable")]
        local = (m.pos[tris].astype(np.float64) - M[:3, 3]) @ M[:3, :3]  # rigid: R^T (p - t)
        normals = smooth_normals(local)
        sel = a.selected[tris]
        w = gltf.Writer()
        prims = []
        if sel.any():
            tex_name = (texture_names or {}).get(name) or f"{name}_atlas"
            ptex, moves, k = _part_texture(a, pi, image)
            gltf.write_texture(out_dir / "textures", tex_name, ptex)
            uv_px = m.uv[tris].astype(np.float64) * a.size * k
            for j, t in enumerate(tris):
                if sel[j]:
                    uv_px[j] += moves[chart_of[int(t)]]
            uv = uv_px / (ptex.shape[1], ptex.shape[0])
            mat = w.material(w.image(f"textures/{tex_name}.png", tex_name), name=tex_name)
            prims.append((np.nonzero(sel)[0], uv, mat))
        for ti in np.unique(m.tex[tris][~sel]):  # triangles kept on their own textures (atlas textured=False)
            if ti < 0:
                continue
            ref = m.textures[ti]
            tname = ref.name.replace("/", "_")
            gltf.write_texture(out_dir / "textures", tname, ref.tex.rgba(ref.palette))
            js = np.nonzero(~sel & (m.tex[tris] == ti))[0]
            mat = w.material(w.image(f"textures/{tname}.png", tname), name=tname)
            prims.append((js, np.nan_to_num(m.uv[tris].astype(np.float64)), mat))
        # the skin: one joint, the part's bone, keyed with the bone's whole model-space frame; above it a node that
        # turns FF7's y down to glTF's y up for viewers (FFNx reads no nodes above the joints)
        R = rest[bone]
        flip = w.node(name="y_up (viewers only)", rotation=[1.0, 0.0, 0.0, 0.0])
        joint = w.node(name=skel.bones[bone].name, translation=R[:3, 3].tolist(),
                       rotation=gltf.quaternion(R[:3, :3]).tolist())
        w.doc["nodes"][flip]["children"] = [joint]
        ibm = w.accessor(np.eye(4, dtype=np.float32).reshape(1, 16), gltf.FLOAT, "MAT4")
        w.doc["skins"].append({"joints": [joint], "inverseBindMatrices": ibm, "name": m.hrc})
        primitives = []
        for js, uv, mat in prims:
            corners = local[js].reshape(-1, 3)
            nrm = normals[js].reshape(-1, 3)
            uvc = uv[js].reshape(-1, 2)
            key = np.round(np.concatenate([corners * 1e3, nrm * 1e3, uvc * 1e5], 1))
            _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
            idx = inv.reshape(-1, 3)[:, [0, 2, 1]]  # FF7's winding reversed: glTF's front faces point out
            nv = len(first)
            primitives.append(w.primitive(corners[first], nrm[first], uvc[first], idx, mat,
                                          np.zeros((nv, 4), np.uint8), np.tile([1.0, 0.0, 0.0, 0.0], (nv, 1))))
        w.doc["meshes"].append({"name": name, "primitives": primitives})
        mesh_node = w.node(name=name, mesh=0, skin=0)
        w.doc["scenes"][0]["nodes"] = [mesh_node, flip]
        jobs.append((name, bone, w, joint))
    for an in animations:
        frames = pose_frames(skel, load_animation(an, char_lgp))
        times = np.arange(len(frames), dtype=np.float32) / 30.0
        for name, bone, w, joint in jobs:
            F = frames[:, bone]
            t_in = w.accessor(times, gltf.FLOAT, "SCALAR", minmax=True, share=("t", len(times)))
            tr = w.accessor(F[:, :3, 3].astype(np.float32), gltf.FLOAT, "VEC3")
            q = w.accessor(gltf.continuous(gltf.quaternions(F[:, :3, :3])).astype(np.float32), gltf.FLOAT, "VEC4")
            w.doc["animations"].append({"name": an, "samplers": [
                {"input": t_in, "output": tr, "interpolation": "LINEAR"},
                {"input": t_in, "output": q, "interpolation": "LINEAR"}], "channels": [
                {"sampler": 0, "target": {"node": joint, "path": "translation"}},
                {"sampler": 1, "target": {"node": joint, "path": "rotation"}}]})
    return {name: w.write(out_dir / f"{name}.gltf") for name, bone, w, joint in jobs}


def export_part(a, part, out_dir, animations=None, image=None, texture_name=None, blended="alpha", cache=CACHE,
                char_lgp=CHAR_LGP):
    """One .p part of a field model as an FFNx external mesh: <out_dir>/<P>.gltf + <P>.bin, and its texture
    <out_dir>/textures/<texture_name>.dds (+ .png for viewers; texture_name defaults to "<P>_atlas"). out_dir plays
    <external_mesh_path>/field (mesh/field in the game folder). Returns the .gltf path.

    a: field.atlas(...) of the model; part: the .p name ("AAAC") or mesh part index; image: the atlas image to ship
    (a painted one, any whole multiple of the atlas' size; default the atlas as built); animations: the .a names to
    include (default: every animation the fields play on the model, which FFNx needs: a part whose glTF lacks the
    animation playing is drawn unposed at the model's origin); blended: the effect triangles (the save point's glow),
    "alpha" (additive_alpha), "skip" or "opaque".

    The glTF: the part's triangles in its bone's space (the .p's own coordinates, FF7's y down, as FFNx's field
    loader takes them: isZUp, no axis swap), with smooth normals and its atlas UVs (its charts repacked into a texture
    of its own); a skin of one joint named after the bone, every vertex on it, identity inverse bind matrix; per
    animation a translation and rotation key per frame: the bone's model-space frame (root and parents included), so
    FFNx draws the part at scale / 128 x that frame, under the entity's own position and turn. Vertex colours aren't
    written (FFNx ignores them): the atlas holds the colour, the material's colour is white."""
    m = a.mesh
    pi = m.parts.index(_base(part)) if isinstance(part, str) else int(part)
    names = {m.parts[pi]: texture_name} if texture_name else None
    return _export(a, [pi], out_dir, animations, image, names, blended, cache, char_lgp)[m.parts[pi]]


def export_model(a, out_dir, animations=None, image=None, blended="alpha", cache=CACHE, char_lgp=CHAR_LGP):
    """export_part for every part of the model: {part name: .gltf path}. FFNx needs them all: once one part of a
    model has an external mesh, it draws only the parts that have one.

    Installing is exporting into <game>/ff7/workingdir/mesh/field (FFNx.toml: enable_external_mesh = true,
    external_mesh_path = "mesh"); removing it is deleting those <P>.gltf/.bin and textures/<P>_atlas.dds files.
    Caveats, from FFNx's code (the user's fork):
    - A glTF replaces every .p of that name the game loads, whichever folder it comes from: the chibi mod's
      direct/char parts of the same names (and its extra parts of that model are then not drawn at all), and also
      battle.lgp parts that happen to share a name: 245 field parts of 126 models do (Aerith's AVBC and AVBE among
      them), because the fork's load_p_file looks up mesh/field/<name>.gltf for every .p it loads.
    - FFNx draws external meshes at the field's model scale (the model loader's header), not the model's own scale
      entry: 261 of 4891 model uses in the fields differ (Cloud listed at 2048 in some, the party at 768 in a few).
    - Two HRCs share 15 .p parts (AODD and MMMO): their glTFs carry the animations of the model exported last."""
    return _export(a, list(range(len(a.mesh.parts))), out_dir, animations, image, None, blended, cache, char_lgp)
