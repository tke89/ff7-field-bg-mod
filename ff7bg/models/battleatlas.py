"""Painted textures for battle models, untextured ones included (FFNx-3d's battle atlas, 2026-11-07): one atlas image
per model and a UV file per part, so every triangle can show a painting (ff7-3d/dev/battle-atlas/FORMAT.md; this is
its "Data" side).

Why: the user wants battles to look like a 2D anime (a repaint of formation 469's frame, work/models/battle/target2d:
drawn, flat fills, painted shadow shapes, thin coloured lines); shaders and screen filters were rejected. The drawing
has to live in the models' textures, drawn with flat light, but FF7's battle models are mostly vertex-coloured:
Midgar Zolom ("dd", the game's ENEMY081) has no texture at all.

What the engine reads (mesh/battle/atlas/ under the game's external_mesh_path, "mesh"):
- <MODEL>.png: one RGBA atlas per model (<MODEL> the game's name, battle.game_name: ENEMY081), every triangle's whole
  colour (what the game shows now, or a painting of it);
- <MODEL>.P<nn>.uv, one per part as the game names it ("FFUV", u32 version 1, u32 numverts, u32 numpolys, then per
  polygon of the .p file in its own order 3 x (f32 u, f32 v), v = 0 at the image's top). numverts and numpolys are the
  .p file's header counts: FFNx-3d ignores a file whose counts don't match.

Part names: the game loads a battle model's parts as <MODEL>.P<nn> with nn the bone index, i.e. battle.lgp's file
<id> + battle.part_role(nn) (role "am" + nn), and skips bones without a part. Checked 2026-11-07 against FFNx.log's
"reading p file: battle/..." lines: ENEMY019 (the Grunt, "at") loads P00 P01 P02 P05 P06 P08 P10 P12 P14 P17 P18 P20
P22 P24 P26 P28 P29 P30 P32 P33 P34, exactly the 21 bones whose header has a part (and whose atam.. file exists);
ENEMY081 ("dd") loads the 39 bones with a part, P00-P43 without P07 P09 P11 P36 P40, again exactly.

The atlas: atlas.build on the model in part space (each part's own coordinates: parts are rigid, so the atlas holds
for every pose and animation), size ATLAS_SIZE, filled with what the game draws now: vertex colours (Gouraud) for
untextured triangles, texture x vertex colour for textured ones. identity() checks it: the atlas-textured model
rendered from a battle camera against the original.

Painting (paint(); the Models "projected" method, as field models are painted): the model placed where a formation has
it (scene.bin: its position, facing the party on +z), seen from that formation's battle cameras (the battle scene
behind it), then all round and close up (ORBIT, CLOSE; a plain gradient of the battle's sky behind, their darks
lifted, a few words each on what they show), each view restyled with a Studio look and projected back into the atlas
(paint.Painter: later views repaint only what they see first or better, Text2Tex; the battle cameras' paint only
when seen KEEP_BATTLE times better). What's behind is only context for the AI (the 2D target was painted from a whole
battle frame); only the model's own pixels go back, less the ones the ground hides and those at its outline and
occlusion edges (projectable()). For Zolom the result is graded to the target's colours (target_grade).
Zolom's recipe and what each variant showed: docs/models-plan.md, "Painted textures for untextured battle models".

Builds: work/models/battle/atlas/<id>/<version>/ (<MODEL>.png, <MODEL>.P<nn>.uv, info.json, preview/).
install() copies a version's files into the game with a manifest (work/models/battle/atlas/installed.json), and
uninstall() removes exactly those (left alone where something else changed them since).

  python -m ff7bg.models.battleatlas build <id> [--version V]           the unpainted atlas (the game's own colours)
  python -m ff7bg.models.battleatlas paint <id> [--version V] [--look L] [--strength S] [--words W] [--lift L]
                                               [--grade G] [--allow-game]
  python -m ff7bg.models.battleatlas check <id> [--version V]
  python -m ff7bg.models.battleatlas install <id> [--version V]
  python -m ff7bg.models.battleatlas uninstall <id>
  python -m ff7bg.models.battleatlas status
"""
import hashlib
import json
import math
import struct
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import atlas as atlasmod
from . import battle, pfile, render
from . import paint as paintmod

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "work" / "models" / "battle" / "atlas"
MANIFEST = WORK / "installed.json"
GAME = battle.GAME_WORKDIR

ATLAS_SIZE = 2048     # Zolom's 53.8 M units^2 of surface at ~0.21 texels per unit: ~1.6x what a 720-line battle
                      # view shows of it (the game is 32-bit: kept modest)
UV_MAGIC = b"FFUV"
UV_VERSION = 1
ENEMY_YAW = 180.0     # enemies face the party (+z); models face -z (battle.load_model)

# The 2D anime target (work/models/battle/target2d/target2d_55.png): the Studio's painterly look at repaint 0.55
# plus these words (FORMAT.md). {what} is the model's own description.
LOOK = "painterly"
STRENGTH = 0.55
WORDS = ("A frame from a hand-drawn 2D anime: {what}, drawn flat in cel animation style, clean shapes, flat colours "
         "with simple painted shadow shapes, thin coloured line art, no 3D shading.")
# Zolom's: v1-v5 also said "red-brown stripes (plates) down its sides", and the AI put brown patches all over its
# sides and coils; the model's own side plates stay reddish without it (v6)
WHAT = {"dd": "Midgar Zolom, a giant green serpent monster with a cobra hood and a pale green segmented belly, rearing "
              "up from its coils, with a tail of dark green spikes"}

# views all round the placed model, after its battle cameras: (label, yaw from its front, pitch above the horizon)
ORBIT = [("front, turned left", 40, 10), ("front, turned right", -40, 10), ("left side", 90, 8),
         ("right side", -90, 8), ("back", 180, 12), ("from above", 0, 55)]
# close views: (label, region (the model's top share by height), yaw, pitch)
CLOSE = [("head, front", "head", 0, 5), ("head, turned left", "head", 50, 8), ("head, turned right", "head", -50, 8)]
# what each view shows, for the AI (paint() adds it to the words): the back view of v1 got a face painted on the back
# of the hood
VIEW_HINTS = {"left side": "Seen from its side.", "right side": "Seen from its side.",
              "back": "Seen from behind: the back of its hood, neck and coils, its face is hidden.",
              "from above": "Seen from above.", "head, turned left": "A close view of its head from the side.",
              "head, turned right": "A close view of its head from the side.",
              "head, front": "A close view of its head."}
# what's behind the views (paint(background=)): "scene" the battle scene, "sky" a plain gradient of the battle
# camera's sky, None black. The battle cameras always have the scene. v1-v2 (2026-11-07) had the scene behind every
# view: seen from round and above, the olive ground was behind the coils, and the AI painted them in its blotches
ORBIT_BACKGROUND = "sky"
VIEW_SIZE = (1280, 960)     # the battle cameras' views (4:3, as the game)
ORBIT_SIZE = (1024, 1024)
VIEW_FOV = 30.0
HEAD_SHARE = 0.25           # "head": the top quarter of the model's height


# --- parts and their files --------------------------------------------------------------------------------------------

@dataclass
class PartFile:
    file: str         # battle.lgp's file ("ddam")
    game: str         # the name the game loads it by ("ENEMY081.P00")
    bone: int
    numverts: int     # the .p header's counts (what FFNx-3d checks a .uv file against)
    numpolys: int


def game_part(id, bone):
    """The game's name for a model's part ("dd", 5 -> "ENEMY081.P05"): its bone index (see the module notes)."""
    g = battle.game_name(id)
    if not g:
        raise ValueError(f"no game name known for {id}")
    return f"{g}.P{bone:02d}"


def part_files(id, battle_lgp=battle.BATTLE_LGP):
    """The model's parts as the game loads them (bones with a part, in bone order; weapons left out: the party's are
    named apart and not handled here)."""
    id = id.lower()
    hd = battle.header(id, battle_lgp)
    if hd.kind == 1:
        raise ValueError(f"{id} is a battle scene, not a model")
    out = []
    for i, b in enumerate(hd.bones):
        name = id + battle.part_role(i)
        if not b.has_part or not battle.has_file(name, battle_lgp):
            continue
        data = battle.read_file(name, battle_lgp)
        h = struct.unpack_from("<16I", data, 0)
        out.append(PartFile(name, game_part(id, i), i, h[3], h[9]))
    return out


def poly_index(part):
    """Per triangle pfile.Part.triangles() gives (group order), its polygon's index in the .p file's polygon table."""
    out = []
    for g in part.groups:
        hi = min(g.offpoly + g.numpoly, len(part.polys))
        if hi > g.offpoly:
            out.append(np.arange(g.offpoly, hi))
    return np.concatenate(out).astype(np.int64) if out else np.zeros(0, np.int64)


# --- the atlas --------------------------------------------------------------------------------------------------------

@dataclass
class BattleAtlas:
    id: str
    name: str             # the game's model name (ENEMY081)
    atlas: object         # atlas.Atlas built on the model in part space (its image: the game's own colours)
    posed: object         # the posed model (battle.BattleMesh) with the atlas UVs: atlas.mesh's triangles, posed
    original: object      # the posed model as the game draws it (vertex colours, its own textures)
    parts: list           # [PartFile] per mesh part
    poly: np.ndarray      # (t,) per triangle its polygon index in its part's .p file

    @property
    def size(self):
        return self.atlas.size

    def uv(self, part):
        """(numpolys, 3, 2) float32 the atlas u, v of a part's polygons (.p order; polygons no group draws: 0)."""
        pf = self.parts[part]
        out = np.zeros((pf.numpolys, 3, 2), np.float32)
        sel = np.nonzero(self.posed.part == part)[0]
        out[self.poly[sel]] = self.posed.uv[sel]
        return out


def part_space(m):
    """A posed BattleMesh with every triangle back in its part's own coordinates (part_matrices inverted)."""
    pos = m.pos.astype(np.float64).copy()
    for pi, M in enumerate(m.part_matrices):
        sel = m.part == pi
        Mi = np.linalg.inv(np.asarray(M, np.float64))
        p = pos[sel].reshape(-1, 3)
        pos[sel] = (p @ Mi[:3, :3].T + Mi[:3, 3]).reshape(-1, 3, 3)
    return replace(m, pos=pos.astype(np.float32))


def build(id, size=ATLAS_SIZE, animation=0, battle_lgp=battle.BATTLE_LGP):
    """BattleAtlas of a battle model ("dd"): its parts in part space charted and packed into one size x size atlas,
    filled with the game's own colours; posed at frame 0 of `animation` for views."""
    id = id.lower()
    m = battle.load_model(id, animation, weapon=-1, battle_lgp=battle_lgp)
    files = {pf.file: pf for pf in part_files(id, battle_lgp)}
    parts, poly = [], []
    for pi, fname in enumerate(m.parts):
        pf = files.get(fname)
        if pf is None:
            raise ValueError(f"{fname} isn't one of {id}'s parts")
        idx = poly_index(pfile.read(battle.read_file(fname, battle_lgp)))
        n = int((m.part == pi).sum())
        if len(idx) != n:
            raise ValueError(f"{fname}: {n} triangles, {len(idx)} polygon indices")
        if len(np.unique(idx)) != len(idx):
            raise ValueError(f"{fname}: a polygon is in two groups")
        parts.append(pf)
        poly.append(idx)
    poly = np.concatenate(poly)
    a = atlasmod.build(part_space(m), size=size, max_size=size, name=battle.game_name(id))
    posed = replace(a.mesh, pos=m.pos)
    return BattleAtlas(id, battle.game_name(id), a, posed, m, parts, poly)


def uv_bytes(ba, part):
    pf = ba.parts[part]
    uv = ba.uv(part)
    return UV_MAGIC + struct.pack("<3I", UV_VERSION, pf.numverts, pf.numpolys) + uv.astype("<f4").tobytes()


def read_uv(data):
    """(numverts, numpolys, uv (numpolys, 3, 2)) of a .uv file's bytes."""
    if data[:4] != UV_MAGIC:
        raise ValueError("not a .uv file")
    ver, nv, npoly = struct.unpack_from("<3I", data, 4)
    if ver != UV_VERSION:
        raise ValueError(f".uv version {ver}")
    if len(data) != 16 + npoly * 24:
        raise ValueError(".uv size doesn't match its polygon count")
    return nv, npoly, np.frombuffer(data, "<f4", offset=16).reshape(npoly, 3, 2)


def with_image(ba, image, mesh=None):
    """The posed atlas model (or `mesh`, the same triangles placed elsewhere) showing another atlas image."""
    m = ba.posed if mesh is None else mesh
    ref = battle.meshmod.TexRef(m.textures[ba.atlas.index].key, atlasmod.as_tex(np.ascontiguousarray(image)), 0)
    textures = list(m.textures)
    textures[ba.atlas.index] = ref
    return replace(m, textures=textures)


def atlas_only(m, index):
    """The mesh with the atlas as its only texture (what gets painted: paint.Painter paints every texture it has)."""
    return replace(m, textures=[m.textures[index]], tex=np.where(m.tex == index, 0, -1).astype(np.int32))


# --- placing a model in a battle --------------------------------------------------------------------------------------

def placed(m, position, yaw=ENEMY_YAW):
    """A model's mesh moved into battle space: turned `yaw` degrees round the vertical, at `position` (scene.bin's
    x, y, z for the formation's enemy)."""
    R = battle.rotation(0, yaw, 0)
    p = m.pos.reshape(-1, 3).astype(np.float64) @ R.T + np.asarray(position, np.float64)
    return replace(m, pos=p.reshape(m.pos.shape).astype(np.float32))


def formations(id, scene_bin=battle.SCENE_BIN):
    """[(formation, its enemy position for this model)] of every formation the model fights in (the first of it in
    each), from scene.bin."""
    from . import scenebin
    k = battle.id_index(id)
    out = []
    for f in scenebin.load(scene_bin).formations:
        pos = next((p for e, p in f.enemies if e == k), None)
        if pos is not None:
            out.append((f, pos))
    return out


def battle_views(id, formation=None, size=VIEW_SIZE, scene_bin=battle.SCENE_BIN):
    """[(label, BattleCamera, position, scene id)]: a formation's cameras (slot 0, the one battles rest on, first;
    slots 1-2 when they're real views, not scene.bin's placeholder), with where it puts the model. formation None:
    the first formation the model is in whose resting camera isn't the placeholder."""
    found = formations(id, scene_bin)
    if formation is not None:
        found = [x for x in found if x[0].id == formation]
    else:
        real = [x for x in found if x[0].cameras and
                (x[0].cameras[0].eye, x[0].cameras[0].target) != battle.PLACEHOLDER_CAMERA]
        found = real[:1] or found[:1]
    out = []
    for f, pos in found:
        sid = battle.location_id(f.location)
        for slot, c in enumerate(f.cameras):
            if (c.eye, c.target) == battle.PLACEHOLDER_CAMERA:
                continue
            cam = battle.game_camera(c.eye, c.target, *size, uses=[(f.id, slot)])
            out.append((f"formation {f.id} camera {slot}", cam, pos, sid))
    return out


def _fit(points, target, yaw, pitch, w, h, fov=VIEW_FOV, margin=0.9, facing=ENEMY_YAW):
    """(camera eye, distance) at (yaw, pitch) from the model's front round `target` that sees every point within
    `margin` of a w x h frame (studio/plugins/models.py's fit_distance; the model's front faces `facing`)."""
    a, p = math.radians(yaw + facing) + math.pi, math.radians(pitch)
    back = np.array([math.sin(a) * math.cos(p), -math.sin(p), math.cos(a) * math.cos(p)])  # target -> eye
    f = -back
    r = np.cross(f, [0.0, -1.0, 0.0])
    r /= max(np.linalg.norm(r), 1e-9)
    u = np.cross(f, r)
    rel = points - target
    ty = math.tan(math.radians(fov) / 2) * margin
    tx = ty * w / h
    need = float((np.maximum(np.abs(rel @ r) / tx, np.abs(rel @ u) / ty) - rel @ f).max())
    return np.asarray(target, float) + back * need, need


def orbit_views(m, orbit=ORBIT, close=CLOSE, size=ORBIT_SIZE, facing=ENEMY_YAW):
    """[(label, render.Camera)] round a placed model (m: its mesh in battle space, facing `facing`): ORBIT round
    all of it, CLOSE round a region of it (the head: its top HEAD_SHARE)."""
    pts = np.unique(m.pos.reshape(-1, 3).astype(np.float64), axis=0)
    out = []
    w, h = size
    lo, hi = pts.min(0), pts.max(0)
    for label, yaw, pitch in orbit:
        t = (lo + hi) / 2
        eye, d = _fit(pts, t, yaw, pitch, w, h, facing=facing)
        out.append((label, render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV,
                                         width=w, height=h, near=max(d * 0.01, 1.0))))
    for label, region, yaw, pitch in close:
        if region == "head":
            top = lo[1] + (hi[1] - lo[1]) * HEAD_SHARE  # y down: the top is the smallest y
            sel = pts[pts[:, 1] <= top]
        else:
            raise ValueError(f"unknown region {region}")
        if not len(sel):
            continue
        t = (sel.min(0) + sel.max(0)) / 2
        eye, d = _fit(sel, t, yaw, pitch, w, h, margin=0.85, facing=facing)
        out.append((label, render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV,
                                         width=w, height=h, near=max(d * 0.01, 1.0))))
    return out


_scenes = {}


def scene_behind(sid, cam):
    """(RGB, depth) of a battle scene from a camera (as the game draws it: its vertex colours times its textures,
    culled): the context a model's view is painted in."""
    if sid not in _scenes:
        _scenes[sid] = battle.load_scene(sid)
    s = _scenes[sid]
    r = render.rasterize(s, cam)
    return render.shade(s, r, modulate=True), r.depth


def sky_backdrop(sid, cam, size, top=0.5):
    """(RGB, depth inf) a plain vertical gradient, w x h = size, of the sky a battle camera sees above the horizon
    (the scene's rows from the top to `top` of the picture, each row averaged): behind the views from round and above,
    where the scene's ground would be behind the model."""
    img, _ = scene_behind(sid, cam)
    rows = img[:max(2, int(img.shape[0] * top))].astype(np.float32).mean(1)
    rows = cv2.GaussianBlur(rows[:, None, :], (1, 0), sigmaX=0.1, sigmaY=len(rows) / 6)[:, 0]  # no bands
    w, h = size
    y = np.linspace(0, len(rows) - 1, h)
    col = np.stack([np.interp(y, np.arange(len(rows)), rows[:, c]) for c in range(3)], -1)
    out = np.broadcast_to(col[:, None, :], (h, w, 3)).astype(np.uint8).copy()
    return out, np.full((h, w), np.inf, np.float32)


def lift(img, vis, floor, k=0.7):
    """The model's pixels (vis) of a view with their darks raised toward `floor` (mean RGB level), hue kept: k of the
    way, only brighter. For the views from round and behind: the model's vertex colours are lit from its front, and
    its back near black-green (paint(lift=))."""
    out = img.copy()
    f = img[vis].astype(np.float32)
    l = f.mean(-1, keepdims=True)
    want = np.where(l < floor, l + (floor - l) * k, l)
    out[vis] = np.clip(f * want / np.maximum(l, 1.0), 0, 255).astype(np.uint8)
    return out


def composite(fg, r, bg):
    """The model's pixels (fg, its raster r) over a background (RGB, depth) where it's nearer."""
    if bg is None:
        return fg
    img, depth = bg
    out = img.copy()
    hit = (r.tri >= 0) & (r.depth < depth)
    out[hit] = fg[hit]
    return out


# --- checks -----------------------------------------------------------------------------------------------------------

def identity(ba, cam, position=(0, 0, 0), yaw=ENEMY_YAW, image=None):
    """The atlas against the game's own colours from a camera: (original render, atlas render (bilinear, as FFNx
    filters), stats). Both are the model alone; stats over the pixels it covers: mean, p99 and max of the largest
    channel difference, and the share within 3 and 8 levels."""
    orig = placed(ba.original, position, yaw)
    am = placed(ba.posed, position, yaw)
    r = render.rasterize(orig, cam)
    a = render.rasterize(am, cam)
    o_img = render.shade(orig, r, modulate=True)
    imgs = [None] * len(am.textures)
    imgs[ba.atlas.index] = ba.atlas.image if image is None else image
    a_img = paintmod.shade(am, a, [im if im is not None else t.tex.rgba(t.palette) for im, t in zip(imgs, am.textures)])
    hit = (r.tri >= 0) & (a.tri >= 0)
    d = np.abs(o_img.astype(int) - a_img.astype(int)).max(-1)[hit]
    stats = {"pixels": int(hit.sum()), "mean": round(float(d.mean()), 3), "p99": float(np.percentile(d, 99)),
             "max": int(d.max()), "within3": round(float((d <= 3).mean()), 4), "within8": round(float((d <= 8).mean()), 4)}
    return o_img, a_img, stats


def check(ba, files):
    """Problems with a build's files ({name: path}) against the model's .p files: [] when all is well."""
    probs = []
    png = files.get(f"{ba.name}.png")
    if png is None or not Path(png).exists():
        probs.append(f"{ba.name}.png missing")
    else:
        im = Image.open(png)
        if im.size[0] != im.size[1] or im.size[0] > 4096 or im.mode != "RGBA":
            probs.append(f"{ba.name}.png is {im.size} {im.mode}")
    for i, pf in enumerate(ba.parts):
        p = files.get(f"{pf.game}.uv")
        if p is None or not Path(p).exists():
            probs.append(f"{pf.game}.uv missing")
            continue
        nv, npoly, uv = read_uv(Path(p).read_bytes())
        if (nv, npoly) != (pf.numverts, pf.numpolys):
            probs.append(f"{pf.game}.uv counts {nv}/{npoly}, the .p file's {pf.numverts}/{pf.numpolys}")
        if not np.isfinite(uv).all() or uv.min() < 0 or uv.max() > 1:
            probs.append(f"{pf.game}.uv has u, v outside 0..1")
        if not np.allclose(uv, ba.uv(i)):
            probs.append(f"{pf.game}.uv isn't this atlas' layout")
    return probs


# --- painting ---------------------------------------------------------------------------------------------------------

def _model_words(id, words):
    what = WHAT.get(id.lower())
    if what is None:
        from . import scenebin
        names = scenebin.load(battle.SCENE_BIN).names
        what = names.get(battle.id_index(id), id)
    return words.replace("{what}", what)


LIFT_SHARE = 1.0   # paint(): the views from round and behind show the model's darks raised toward its median level
                   # in the battle view (its vertex colours are lit from the front; its back is near black)
KEEP_BATTLE = 2.0  # paint(): what the battle cameras painted, other views need to see this much better to repaint
MIN_NEW = 0.01   # a later view runs when it's the first to see (or sees better) this share of the model it shows
EDGE_PX = 3.0    # a view's pixels this close to the model's outline or an occlusion edge in it count for less...
EDGE_MIN = 0.34  # ...and nothing under this weight (paint.Painter.add marks every texel it takes as seen)
DEPTH_JUMP = 0.01  # an occlusion edge: neighbouring pixels' depths differ by more than this share


def projectable(r, bg_depth=None):
    """(visible, weight) for a view's raster of the model: the pixels where the model shows (in front of the scene
    behind it: the ground hides the coils' undersides, and the AI paints ground there), and per pixel a weight rising
    from 0 at the model's outline and at occlusion edges (a near part over a far one) to 1 EDGE_PX inside. Projected
    as they were, the views' edge pixels put the background's colour, and the near part's colour, on the far side."""
    vis = r.tri >= 0
    if bg_depth is not None:
        vis &= r.depth < bg_depth
    z = np.where(vis, r.depth, np.inf).astype(np.float64)
    cut = ~vis
    for axis in (0, 1):
        a = np.swapaxes(z, 0, axis)
        with np.errstate(invalid="ignore"):
            d = np.abs(a[1:] - a[:-1])
        jump = ~(d <= DEPTH_JUMP * np.minimum(a[1:], a[:-1]))  # inf - inf (both off the model) is nan: a jump too
        c = np.swapaxes(cut, 0, axis)
        c[1:] |= jump
        c[:-1] |= jump
    dist = cv2.distanceTransform((~cut).astype(np.uint8), cv2.DIST_L2, 3)
    return vis, np.clip(dist / EDGE_PX, 0, 1).astype(np.float32) * vis


def paint(ba, look=LOOK, strength=STRENGTH, words=WORDS, formation=None, orbit=ORBIT, close=CLOSE,
          url="http://127.0.0.1:8000", describe=None, background=ORBIT_BACKGROUND, keep_battle=KEEP_BATTLE,
          hints=VIEW_HINTS, lift_share=LIFT_SHARE, progress=None, log_dir=None):
    """The atlas painted from restyled views (the Models projected method): the battle cameras of a formation first,
    then all round (orbit) and close up (close), the model placed where that formation has it. Behind it (context for
    the AI only): the battle scene for the battle cameras, and for the rest `background` (ORBIT_BACKGROUND: "scene",
    "sky" or None for black). hints: per view label, words added for that view (VIEW_HINTS). lift_share: the views
    after the battle cameras show the model's darks raised toward this share of its median level in the first battle
    view (lift()), None not. Each view after
    the first repaints only what it's the first to see, or sees BETTER than before (paint.Painter.view_mask), around
    the paint so far; what the battle cameras painted is kept unless a view sees it keep_battle x BETTER (the player
    sees the model from there). Returns (atlas RGBA at the atlas' size, [per view dict(label, new share, image, orig)], the
    restyle.Restyler)."""
    from . import restyle
    bviews = battle_views(ba.id, formation)
    if not bviews:
        raise ValueError(f"{ba.id} is in no formation with a battle camera")
    pos, sid = bviews[0][2], bviews[0][3]
    pm_battle = atlas_only(placed(ba.posed, pos), ba.atlas.index)
    views = [(label, cam, sid) for label, cam, _, _ in bviews]
    views += [(label, cam, sid) for label, cam in orbit_views(pm_battle, orbit, close)]
    s = restyle.look_settings(look)
    R = restyle.Restyler(s, url=url, kind="battle_model", name=f"battleatlas_{ba.id}",
                         prompt=_model_words(ba.id, words), strength=strength, describe=describe)
    P = paintmod.Painter(pm_battle, 1, [ba.atlas.image])
    log = []
    scene_words, floor = None, None
    for i, (label, cam, sid) in enumerate(views):
        if i == len(bviews) and keep_battle != 1:  # the battle cameras' paint: other views must see it this much better
            P.best = [b * keep_battle for b in P.best]
        r = render.rasterize(pm_battle, cam)
        if not (r.tri >= 0).any():
            continue
        how = "scene" if i < len(bviews) else background
        bg = (scene_behind(sid, cam) if how == "scene" else
              sky_backdrop(sid, bviews[0][1], (cam.width, cam.height)) if how == "sky" else None)
        vis, edge = projectable(r, None if bg is None else bg[1])
        if scene_words is not None:  # this view's own words after the model's (prepare set them on the first view)
            R.s["scene"] = " ".join(x for x in (scene_words, (hints or {}).get(label, "")) if x)
        shown = P.images()
        fg = paintmod.shade(pm_battle, r, [ba.atlas.image])
        if i >= len(bviews) and floor is not None:
            fg = lift(fg, vis, floor)
        orig = composite(fg, r, bg)
        if not any((w > 0).any() for w in P.wsum):
            mask = vis
            out = R(orig, None, None)
            scene_words = R.s.get("scene", "")
            if lift_share:
                floor = lift_share * float(np.median(fg[vis].astype(np.float32).mean(-1)))
            weight = edge
        else:
            mask = P.view_mask(cam, r) & vis
            new = float(mask.sum() / max(vis.sum(), 1))
            if new < MIN_NEW:
                log.append(dict(label=label, new=new, image=None))
                continue
            cur = composite(paintmod.shade(pm_battle, r, shown), r, bg)
            grown = cv2.dilate(mask.astype(np.uint8), np.ones((2 * paintmod.MASK_GROW + 1,) * 2, np.uint8)) > 0
            out = R(orig, cur, grown & vis)
            weight = paintmod.feather(mask) * edge
        P.add(cam, r, out, np.where(weight >= EDGE_MIN, weight, 0).astype(np.float32))
        log.append(dict(label=label, new=float(mask.sum() / max(vis.sum(), 1)), image=out, orig=orig))
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.hstack([orig, out])).save(Path(log_dir) / f"view_{i:02d}.jpg", quality=88)
        if progress:
            progress(i + 1, len(views), label)
    if scene_words is not None:
        R.s["scene"] = scene_words
    P.fill_holes()
    img = P.images()[0]
    img = atlasmod.fill(img, ba.atlas.used())
    return img, log, R


# --- the target's colours ----------------------------------------------------------------------------------------------

# The 2D target (target2d_55.png, 1040 x 405) is the game's 1280 x 720 frame of formation 469 (its 4:3 picture at x
# 160-1120) cropped to TARGET_CROP and repainted. Zolom in it: TARGET_BOX of the crop (above Barret's head), not sky.
TARGET = REPO / "work" / "models" / "battle" / "target2d" / "target2d_55.png"
TARGET_MODEL = "dd"
TARGET_FORMATION = 469
TARGET_CROP = (120, 0, 1160, 405)   # x0, y0, x1, y1 in the game's 1280 x 720 frame
TARGET_BOX = (330, 0, 700, 270)     # ...and Zolom's part of the crop


def game_frame(ba, image=None, formation=TARGET_FORMATION):
    """(RGB, model mask) of a formation's battle view as the game frames it at 1280 x 720 (the 4:3 picture in the
    middle, black bars), the scene behind: the original model (image None) or the atlas showing `image`."""
    label, cam, pos, sid = battle_views(ba.id, formation, (960, 720))[0]
    if image is None:
        m = placed(ba.original, pos)
        r = render.rasterize(m, cam)
        fg = render.shade(m, r, modulate=True)
    else:
        m = atlas_only(placed(ba.posed, pos), ba.atlas.index)
        r = render.rasterize(m, cam)
        fg = paintmod.shade(m, r, [image])
    bg = scene_behind(sid, cam)
    vis = (r.tri >= 0) & (r.depth < bg[1])
    img = np.zeros((720, 1280, 3), np.uint8)
    mask = np.zeros((720, 1280), bool)
    img[:, 160:1120] = composite(fg, r, bg)
    mask[:, 160:1120] = vis
    return img, mask


def target_pixels(path=TARGET, box=TARGET_BOX):
    """The target's model pixels (n, 3) RGB: TARGET_BOX, less the sky (bluer than green, or grey)."""
    tgt = np.asarray(Image.open(path).convert("RGB"))
    t = tgt.astype(int)
    m = np.zeros(tgt.shape[:2], bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    sat = cv2.cvtColor(tgt, cv2.COLOR_RGB2HSV)[..., 1]
    m &= ~((t[..., 2] > t[..., 1] + 5) | (sat < 40))
    return tgt[m]


def lab_grade(image, src, ref, amount=1.0):
    """image (RGB or RGBA) graded so the colours `src` (n, 3 RGB: the model as painted, in some view) take the mean and
    spread of `ref` (the target's) in Lab, channel by channel (Reinhard); amount blends it in. Returns (image,
    {src, ref: Lab means and spreads})."""
    def lab(x):
        return cv2.cvtColor(np.ascontiguousarray(x, np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    ls, lr = lab(src), lab(ref)
    ms, ss, mr, sr = ls.mean(0), ls.std(0) + 1e-3, lr.mean(0), lr.std(0) + 1e-3
    rgb = image[..., :3]
    li = lab(rgb)
    out = (li - ms) / ss * sr + mr
    out = li + (out - li) * amount
    back = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8).reshape(-1, 1, 3), cv2.COLOR_LAB2RGB).reshape(rgb.shape)
    res = image.copy()
    res[..., :3] = back
    return res, {"src": [ms.round(2).tolist(), ss.round(2).tolist()], "ref": [mr.round(2).tolist(), sr.round(2).tolist()]}


def target_grade(ba, image, amount=1.0, path=TARGET):
    """The painted atlas graded to the target's colours: the model's pixels in the target's frame (game_frame cropped
    to TARGET_CROP, inside TARGET_BOX) against the target's (target_pixels). Returns (atlas, stats)."""
    img, mask = game_frame(ba, image)
    x0, y0, x1, y1 = TARGET_CROP
    img, mask = img[y0:y1, x0:x1], mask[y0:y1, x0:x1]
    bx0, by0, bx1, by1 = TARGET_BOX
    box = np.zeros(mask.shape, bool)
    box[by0:by1, bx0:bx1] = True
    out, st = lab_grade(image, img[mask & box], target_pixels(path), amount)
    return atlasmod.fill(out, ba.atlas.used()), st


# --- builds -----------------------------------------------------------------------------------------------------------

def _next_version(id):
    d = WORK / id.lower()
    n = [int(p.name[1:]) for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()] if d.exists() else []
    return f"v{max(n, default=0) + 1}"


def export(ba, image, out_dir, info=None):
    """<MODEL>.png (the atlas image, RGBA) and <MODEL>.P<nn>.uv per part into out_dir, plus info.json. Returns
    {file name: path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = np.ascontiguousarray(image)
    if image.shape[-1] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    files = {}
    p = out_dir / f"{ba.name}.png"
    Image.fromarray(image, "RGBA").save(p)
    files[p.name] = p
    for i, pf in enumerate(ba.parts):
        p = out_dir / f"{pf.game}.uv"
        p.write_bytes(uv_bytes(ba, i))
        files[p.name] = p
    meta = {"id": ba.id, "model": ba.name, "atlas": {"size": ba.size, "density": round(ba.atlas.density, 4),
                                                     "charts": len(ba.atlas.charts)},
            "parts": [{"file": pf.file, "game": pf.game, "bone": pf.bone, "numverts": pf.numverts,
                       "numpolys": pf.numpolys} for pf in ba.parts],
            "files": sorted(files), "at": time.strftime("%Y-%m-%d %H:%M:%S"), **(info or {})}
    (out_dir / "info.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return files


def build_files(out_dir):
    """{file name: path} of a build folder's game files (the PNG and .uv files)."""
    out_dir = Path(out_dir)
    return {p.name: p for p in sorted(out_dir.iterdir()) if p.suffix.lower() in (".png", ".uv") and p.is_file()}


def version_dir(id, version=None):
    """A build's folder (the newest when version is None)."""
    d = WORK / id.lower()
    if version:
        return d / version
    vs = sorted((p for p in d.glob("*") if (p / "info.json").exists()), key=lambda p: p.stat().st_mtime) if d.exists() else []
    if not vs:
        raise FileNotFoundError(f"no build of {id} in {d}")
    return vs[-1]


# --- install ----------------------------------------------------------------------------------------------------------

_lock = threading.RLock()


def atlas_dir():
    """The game's mesh/battle/atlas (under FFNx's external_mesh_path)."""
    from . import battlelight
    return battlelight.mesh_dir() / "battle" / "atlas"


def _hash(p):
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


def installed():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(data):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def install(id, version=None):
    """Copy a build's <MODEL>.png and .uv files into the game's mesh/battle/atlas/, recorded in the manifest. Replaces
    this model's earlier install; refuses (writing nothing) where a file it would write is there and isn't ours.
    Returns the manifest entry."""
    id = id.lower()
    src_dir = version_dir(id, version)
    files = build_files(src_dir)
    if not files:
        raise FileNotFoundError(f"nothing to install in {src_dir}")
    dest = atlas_dir()
    with _lock:
        data = installed()
        kept = []  # folders the earlier install made that are still there (something else is in them): still ours
        if id in data:
            before = data[id].get("folders", [])
            uninstall(id)
            data = installed()
            kept = [d for d in before if Path(d).exists()]
        ours = {f["path"].lower() for e in data.values() for f in e["files"]}
        clash = [str(dest / n) for n in files if (dest / n).exists() and str(dest / n).lower() not in ours]
        if clash:
            raise FileExistsError("not ours, left alone: " + ", ".join(clash))
        made, done = list(kept), []
        try:
            for d in reversed([dest, *dest.parents]):
                if not d.exists():
                    d.mkdir()
                    made.append(str(d))
            for n, src in files.items():
                dst = dest / n
                dst.write_bytes(Path(src).read_bytes())
                done.append({"path": str(dst), "sha1": _hash(dst)})
        except Exception:
            for f in done:
                Path(f["path"]).unlink(missing_ok=True)
            for d in reversed(made[len(kept):]):
                try:
                    Path(d).rmdir()
                except OSError:
                    pass
            raise
        entry = {"version": src_dir.name, "from": str(src_dir), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "files": done, "folders": made}
        data[id] = entry
        _save_manifest(data)
        return entry


def uninstall(id):
    """Take a model's install out of the game: exactly the files it wrote (left where something changed them since),
    and the folders it made if they're empty. Returns (files removed, files left)."""
    id = id.lower()
    with _lock:
        data = installed()
        entry = data.get(id)
        if not entry:
            return 0, []
        removed, left = 0, []
        for f in entry["files"]:
            p = Path(f["path"])
            if not p.exists():
                continue
            if _hash(p) != f.get("sha1"):
                left.append(str(p))
                continue
            p.unlink()
            removed += 1
        for d in reversed(entry.get("folders", [])):
            try:
                Path(d).rmdir()
            except OSError:  # another model's files are in it: that one takes it away later
                heir = next((e for k, e in data.items() if k != id and any(
                    Path(f["path"]).is_relative_to(d) for f in e["files"])), None)
                if heir is not None and d not in heir.setdefault("folders", []):
                    heir["folders"].insert(0, d)
        data.pop(id)
        _save_manifest(data)
        return removed, left


def status():
    out = {}
    if WORK.exists():
        for d in sorted(p for p in WORK.iterdir() if p.is_dir()):
            vs = sorted(v.name for v in d.iterdir() if (v / "info.json").exists())
            if vs:
                out[d.name] = {"built": vs}
    for k, e in installed().items():
        out.setdefault(k, {})["installed"] = {"version": e["version"], "files": len(e["files"])}
    return out


# --- previews ---------------------------------------------------------------------------------------------------------

def battle_frame(ba, image=None, formation=None, size=VIEW_SIZE, background=True):
    """The model as a formation's resting battle camera shows it, with the battle scene behind: the original (vertex
    colours) when image is None, else the atlas showing `image` with flat light (what FFNx-3d draws with toon_shadow
    white). RGB."""
    label, cam, pos, sid = battle_views(ba.id, formation, size)[0]
    if image is None:
        m = placed(ba.original, pos)
        r = render.rasterize(m, cam)
        fg = render.shade(m, r, modulate=True)
    else:
        m = atlas_only(placed(ba.posed, pos), ba.atlas.index)
        r = render.rasterize(m, cam)
        fg = paintmod.shade(m, r, [image])
    return composite(fg, r, scene_behind(sid, cam) if background else None)


def turntable(ba, image, path, formation=None):
    """A sheet of the painted model all round (8 views, 12 degrees up) and two of its head, with the battle scene
    behind: what the battle cameras show when they swing round."""
    _, cam0, pos, sid = battle_views(ba.id, formation)[0]
    pm = atlas_only(placed(ba.posed, pos), ba.atlas.index)
    orbit = [(f"yaw {y}", y, 12) for y in (0, 45, 90, 135, 180, 225, 270, 315)]
    cells = []
    for _, cam in orbit_views(pm, orbit, [("head", "head", 20, 10), ("head", "head", -60, 10)], size=(480, 480)):
        r = render.rasterize(pm, cam)
        cells.append(composite(paintmod.shade(pm, r, [image]), r, scene_behind(sid, cam)))
    Image.fromarray(np.vstack([np.hstack(cells[i:i + 5]) for i in (0, 5)])).save(path, quality=88)


def vs_target(ba, image, path):
    """The original and the painted model in the target's framing (game_frame cropped to TARGET_CROP), above the
    target."""
    x0, y0, x1, y1 = TARGET_CROP
    tgt = np.asarray(Image.open(TARGET).convert("RGB"))
    rows = [game_frame(ba, None)[0][y0:y1, x0:x1], game_frame(ba, image)[0][y0:y1, x0:x1], tgt]
    Image.fromarray(np.vstack(rows)).save(path, quality=92)


def previews(ba, image, out_dir, formation=None):
    """preview/battle.jpg (original | painted from the resting battle camera), turntable.jpg, and for the target's
    model vs_target.jpg."""
    d = Path(out_dir) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.hstack([battle_frame(ba, None, formation), battle_frame(ba, image, formation)])).save(
        d / "battle.jpg", quality=90)
    turntable(ba, image, d / "turntable.jpg", formation)
    if ba.id == TARGET_MODEL and TARGET.exists():
        vs_target(ba, image, d / "vs_target.jpg")


def _save_identity(ba, out_dir, formation=None):
    label, cam, pos, sid = battle_views(ba.id, formation)[0]
    o, a, st = identity(ba, cam, pos)
    d = np.abs(o.astype(int) - a.astype(int)).max(-1)
    heat = np.clip(d * 16, 0, 255).astype(np.uint8)
    Image.fromarray(np.hstack([o, a, np.dstack([heat] * 3)])).save(Path(out_dir) / "identity.png")
    return st


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.battleatlas")
    ap.add_argument("cmd", choices=("build", "paint", "check", "install", "uninstall", "status"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--version")
    ap.add_argument("--size", type=int, default=ATLAS_SIZE)
    ap.add_argument("--look", default=LOOK)
    ap.add_argument("--strength", type=float, default=STRENGTH)
    ap.add_argument("--words", default=WORDS)
    ap.add_argument("--formation", type=int)
    ap.add_argument("--lift", type=float, default=LIFT_SHARE)
    ap.add_argument("--grade", type=float, default=None,
                    help="grade to the 2D target's colours, 0-1 (default: 1 for the target's model, dd; else 0)")
    ap.add_argument("--allow-game", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd in ("build", "paint"):
        ba = build(a.id, a.size)
        out = WORK / a.id.lower() / (a.version or _next_version(a.id))
        out.mkdir(parents=True, exist_ok=True)
        (out / "preview").mkdir(exist_ok=True)
        st = _save_identity(ba, out / "preview", a.formation)
        info = {"identity": st}
        if a.cmd == "paint":
            from . import battlelight
            if battlelight.game_running() and not a.allow_game:
                raise SystemExit("the game is running (it shares the GPU with ComfyUI): close it first")
            t = time.time()
            img, log, R = paint(ba, a.look, a.strength, a.words, a.formation, lift_share=a.lift,
                                log_dir=out / "preview",
                                progress=lambda i, n, label: print(f"view {i}/{n}: {label}", flush=True))
            info.update(painted={"look": a.look, "strength": a.strength, "words": _model_words(a.id, a.words),
                                 "describer": R.words, "prompt": R.s.get("scene"), "lift_share": a.lift,
                                 "background": ORBIT_BACKGROUND, "keep_battle": KEEP_BATTLE,
                                 "seconds": round(time.time() - t),
                                 "views": [dict(label=x["label"], new=round(x["new"], 3)) for x in log]})
            grade = (1.0 if a.id.lower() == TARGET_MODEL else 0.0) if a.grade is None else a.grade
            if grade > 0:
                Image.fromarray(img).save(out / "preview" / "ungraded.png")
                img, gst = target_grade(ba, img, grade)
                info["graded"] = dict(amount=grade, **gst)
        else:
            img = ba.atlas.image
        files = export(ba, img, out, info)
        previews(ba, img, out, a.formation)
        print(json.dumps({"out": str(out), "identity": st, "files": len(files)}, indent=1))
    elif a.cmd == "check":
        ba = build(a.id, a.size)
        p = check(ba, build_files(version_dir(a.id, a.version)))
        print("\n".join(p) if p else "ok")
        return 1 if p else 0
    elif a.cmd == "install":
        print(json.dumps(install(a.id, a.version), indent=1))
    elif a.cmd == "uninstall":
        print(uninstall(a.id))
    else:
        print(json.dumps(status(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
