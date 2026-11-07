"""Painted textures for field models (FFNx-3d's field atlas, 2026-11-07): the battle atlas (battleatlas.py,
ff7-3d/dev/battle-atlas/FORMAT.md) for the models the game draws on the fields, so they can carry the same 2D anime
painting as the battle models.

What the engine reads (mesh/field/atlas/ under the game's external_mesh_path, "mesh"), per part as the game loads it
from char.lgp (FFNx.log: "reading p file: char/AAAC.P", upper case with the extension):
- <P>.png: the part's own RGBA texture (the engine names it by the part file's stem: AAAC.P -> AAAC.png). Field
  parts don't share a model name the way battle parts do (ENEMY081.P05 -> ENEMY081.png), so each part gets its own
  texture: its charts cut out of the model's painted atlas texel for texel (field._part_texture, as field.export_part
  does for external meshes).
- <P>.P.uv: FORMAT.md's FFUV v1 against that part's own texture: numverts and numpolys of the .p file the game loads
  (direct/char first: field.char_read), then per polygon in the .p table's order 3 x (u, v), v = 0 at the top.
Light: the engine draws painted parts with flat light and rounds field models (char_round) with the painting
interpolated; nothing here writes lighting settings.

The model is the one the game draws: the user's chibi field models in workingdir/direct/char replace char.lgp's
(Cloud: AAAA.HRC, 16 parts AAAC.P .. AAFD.P, exactly the ones FFNx.log shows the game load), so field.py reads
direct/char first.

The atlas (build): atlas.build on the model in part space (battleatlas.part_space: parts are rigid, so one atlas holds
for every animation), ATLAS_SIZE, filled with what the game draws now (texture x vertex colour, bilinear). identity()
checks it, and files_identity() the exported per-part PNGs and .uv files themselves, against the original render.

Painting (paint(); the battle party's recipe, without a battle camera): the model in its standing pose (the first
animation most fields start it on: ACFE for Cloud) on a plain gradient, seen all round and from above, then close up
(its head from five sides, arms and hands, hands, the sword on its back), each view restyled with the Studio's
painterly look at 0.55 plus the 2D words and projected back (paint.Painter; later views repaint only what they see
first or better). Parts other parts hide in the standing pose (the torso's sides under the arms) are painted from
views with those parts left out, projected onto the hidden parts only (the "solo" views, battleatlas.MOVING's
mechanism). The face's close views repaint gently and the eye and mouth decals keep the game's drawing (the recipe's
strengths and keep: the chibi face is the user's look). Then the game's hue and saturation come back (colour_lock:
battleatlas' "hsv", the party's lock, with the game's greys kept grey too). A layer drawn twice (the head's untextured
and textured copies: twins()) is painted as the layer the game shows.
The recipes and what each variant showed: docs/models-plan.md, "Painted field models".

Builds: work/models/field/atlas/<hrc>/<version>/ (<P>.png, <P>.P.uv per part, info.json, preview/).
install() copies a build into the game's mesh/field/atlas/ with a manifest (work/models/field/atlas/installed.json);
uninstall() removes exactly those files.

  python -m ff7bg.models.fieldatlas build <hrc> [--version V]     the unpainted atlas (the game's own colours)
  python -m ff7bg.models.fieldatlas paint <hrc> [--version V] [--look L] [--strength S] [--allow-game]
  python -m ff7bg.models.fieldatlas check <hrc> [--version V]
  python -m ff7bg.models.fieldatlas install <hrc> [--version V]
  python -m ff7bg.models.fieldatlas uninstall <hrc>
  python -m ff7bg.models.fieldatlas status
"""
import json
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
from . import battleatlas as BA
from . import field, pfile, render
from . import mesh as meshmod
from . import paint as paintmod

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "work" / "models" / "field" / "atlas"
MANIFEST = WORK / "installed.json"

ATLAS_SIZE = 2048  # Cloud's chibi model: 2189 units^2 of surface whose own textures (CL 1024^2, the face's 512^2s)
                   # give it 1.7 M texels, 19-46 per unit (the face 46); at 2048 the atlas holds ~36 per unit for
                   # all of it. 1024 halves the face's detail (the eyes are texture).
FACING = 0.0       # field models face -z, as the party's battle models (battleatlas._fit's facing)
# paint(facing=): a view's pixels count from |cos| 0.3 (72 degrees off the surface's normal), fully from 0.55 (57).
# v1-v8 took every pixel (battleatlas'), and the hair's top, seen edge-on by the first view, came out as scattered
# samples with the original between them; the masked views painted from that blotchy state (brown patches)
FACING_RAMP = (0.3, 0.55)
VIEW_SIZE = (1024, 1024)
VIEW_FOV = BA.VIEW_FOV
BACKDROP = ((168, 182, 202), (206, 204, 198))  # the views' plain background: top and bottom of a vertical gradient

LOOK, STRENGTH, WORDS = BA.LOOK, BA.STRENGTH, BA.WORDS
# the model's words: shapes, not colours (the battle party's lesson: colours come from the model and colour_lock)
WHAT = {
    "AAAA": "Cloud as a small chibi character with a big head: a young swordsman with huge spiky hair, big eyes, a "
            "sleeveless ribbed knit top with a belt and a strap across his chest, one round pauldron on his left "
            "shoulder only and his right shoulder and arm bare, baggy trousers, gloves with wrist guards, big boots, "
            "a huge broadsword strapped on his back",
}

# Views (label, region, yaw from the front, pitch above the horizon). Regions: battleatlas.region_points' ("body",),
# ("bones", [..]), ("band", a, b), or ("parts", [.p names]).
BODY_ORBIT = BA.PARTY_ORBIT + [("from above, behind", 180, 50)]  # field cameras look down on the characters


@dataclass
class Recipe:
    head: list          # the head's bones
    arms: list          # arms with hands
    hands: list
    weapon: list        # .p names of a weapon carried on the body (framed on its own)
    hide_for_torso: list  # bones left out of the torso's own views (SOLO)
    torso: list         # the torso's .p names (what those views paint)
    first: str = "front, turned right"
    better: float = None
    keep: dict = None       # {.p name: [groups]} drawn as the game has them, never painted (the eye and mouth decals)
    strengths: dict = None  # {view label: repaint} for the later views (the face's close views)


# bone indices from the HRC (field.skeleton("AAAA")): 0 hip, 1 chest, 2 head, 5-7 l_uparm, l_foarm, l_hand, 10-12 the
# right ones; AAAE1.P on the chest bone is the Buster Sword on his back (renders in false colours:
# work/models/field/atlas/scratch/parts.jpg)
# v1-v3 (2026-11-07): at 0.55 the head's close views painted a different, older face (smaller eyes, more lines) whose
# eyes landed around the model's eye decals as smears. So the decals (AABA.P groups 1-3: the eyes and mouth, drawn on
# their own 32-512 px textures, 70-96% cut out) keep the game's drawing, the face's close views repaint gently and
# the words ask for big anime eyes: v4 0.40 left brown marks on the forehead, v6 0.30 the cleanest face. v9 (picked)
# is v6's recipe with paint(facing=) and compose()
FACE_STRENGTH = 0.30
RECIPES = {"AAAA": Recipe(head=[2], arms=[5, 6, 7, 10, 11, 12], hands=[7, 12], weapon=["AAAE1"],
                          hide_for_torso=[5, 6, 7, 10, 11, 12], torso=["AAAE", "AAAC"], better=2.0,
                          keep={"AABA": [1, 2, 3]},
                          strengths={"head, front": FACE_STRENGTH, "head, turned left": FACE_STRENGTH,
                                     "head, turned right": FACE_STRENGTH})}

HINTS = dict(BA.PARTY_HINTS)
HINTS.update({
    "head, front": "A close view of his chibi face: two big round anime eyes, a small closed mouth.",
    "head, turned left": "A close view of his chibi head from the side: one big round anime eye.",
    "head, turned right": "A close view of his chibi head from the side: one big round anime eye.",
    "from above, behind": "Seen from above and behind: the top and back of his head and hair, his back, no face.",
    "head, from above": "A close view of the top of his head: spiky hair, seen from above.",
    "hands": "A close view of his gloved hands.",
    "sword": "A close view of the huge broadsword strapped on his back: a wide flat steel blade and its hilt.",
    "sword, side": "A close view of the huge broadsword strapped on his back: a wide flat steel blade and its hilt.",
    "from below": "Seen from below: the soles of his boots, under his hands.",
    "from below, behind": "Seen from below and behind: the soles of his boots, his back, no face.",
    "torso, left side": "A close view of the side of his chest and waist (his arms are left out of this view).",
    "torso, right side": "A close view of the side of his chest and waist (his arms are left out of this view).",
})


def recipe(hrc):
    return RECIPES.get(field._base(hrc)) or Recipe(head=[], arms=[], hands=[], weapon=[], hide_for_torso=[], torso=[])


def default_views(hrc, solo=True):
    """[(label, region, yaw, pitch, solo)] a model's painting views: the body all round (BODY_ORBIT), the head from
    five sides, the arms and hands, the hands, its weapon, and (solo) its torso with the arms left out. solo: None, or
    (hidden bones, [.p names projected onto])."""
    rc = recipe(hrc)
    out = [(label, ("body",), yaw, pitch, None) for label, yaw, pitch in BODY_ORBIT]
    head = ("bones", rc.head) if rc.head else ("band", 0.0, 0.3)
    out += [("head, front", head, 0, 5, None), ("head, turned left", head, 50, 6, None),
            ("head, turned right", head, -50, 6, None), ("head, back", head, 180, 12, None),
            ("head, from above", head, 0, 60, None)]
    if rc.arms:
        out += [("arms and hands, front", ("bones", rc.arms), -20, 12, None),
                ("arms and hands, side", ("bones", rc.arms), -75, 8, None)]
    if rc.hands:
        out += [("hands", ("bones", rc.hands), 0, 15, None)]
    if rc.weapon:
        out += [("sword", ("parts", rc.weapon), 160, 10, None), ("sword, side", ("parts", rc.weapon), -120, 10, None)]
    if solo and rc.hide_for_torso and rc.torso:
        s = (rc.hide_for_torso, rc.torso)
        out += [("torso, left side", ("parts", rc.torso), 90, 5, s), ("torso, right side", ("parts", rc.torso), -90, 5, s)]
    # last, what's left: the undersides (soles, under the hands and the hip: 15-37% of those parts' texels unseen
    # without them, a dry run 2026-11-07); a walk shows the soles from behind
    out += [("from below", ("body",), 0, -35, None), ("from below, behind", ("body",), 180, -35, None)]
    return out


# --- parts and their files --------------------------------------------------------------------------------------------

@dataclass
class PartFile:
    file: str         # the .p name ("AAAC")
    game: str         # the name the game loads it by ("AAAC.P"; FFNx.log "reading p file: char/AAAC.P")
    bone: int
    numverts: int     # the .p header's counts (what FFNx-3d checks a .uv file against)
    numpolys: int

    @property
    def texture(self):
        return f"{self.file}.png"   # the engine's texture name: the part file's stem

    @property
    def uv_name(self):
        return f"{self.game}.uv"


def part_files(hrc):
    """The model's parts as the game loads them: per bone in order, each RSD the HRC lists whose .p exists (the files
    the game reads: direct/char first)."""
    m = field.model(hrc)
    out = []
    for p in m.parts:
        h = struct.unpack_from("<16I", field.char_read(p.name + ".P"), 0)
        out.append(PartFile(p.name, f"{p.name}.P", p.bone, h[3], h[9]))
    return out


@dataclass
class FieldAtlas:
    id: str               # "aaaa" (the HRC, lower case: the work folder)
    name: str             # "AAAA"
    atlas: object         # atlas.Atlas on the model in part space (its image: the game's own colours)
    posed: object         # the posed model (field.FieldMesh) with the atlas UVs
    original: object      # the posed model as the game draws it (its textures, vertex colours)
    parts: list           # [PartFile] per mesh part
    poly: np.ndarray      # (t,) per triangle its polygon index in its part's .p file
    twin: dict = None     # {shadowed triangle: (the later triangle at the same place, corner map)}: twins()

    @property
    def size(self):
        return self.atlas.size

    def seen(self, m):
        """The mesh (fa.posed, fa.original or one like them) as the game shows it: shadowed triangles (twins())
        collapsed onto their centres, so the rasterizer skips them (the triangles' order kept)."""
        if not self.twin:
            return m
        sel = np.array(sorted(self.twin), np.int64)
        pos = m.pos.copy()
        pos[sel] = pos[sel].mean(1, keepdims=True)
        return replace(m, pos=pos)

    def uv(self, part):
        """(numpolys, 3, 2) the atlas u, v of a part's polygons (.p order; polygons no group draws: 0)."""
        out = np.zeros((self.parts[part].numpolys, 3, 2), np.float32)
        sel = np.nonzero(self.posed.part == part)[0]
        out[self.poly[sel]] = self.posed.uv[sel]
        return out


def twins(m):
    """{earlier triangle: (later triangle, corner map (3,): the later one's corner at each of its corners)} for
    triangles drawn twice at exactly the same place in one part, the earlier one shadowed. The chibi Cloud's head
    (AABA.P) is two layers of the same 257 polygons: group 0 untextured (peach vertex colours) and group 4 textured
    (CL: the hair and face), with the eye and mouth decals (groups 1-3) between them. The game draws a part's groups
    in order with depth test LEQUAL (FFNx renderer.cpp), so the later layer is what shows (the earlier one only
    through its cut-outs, 0.05% of its texels); render.rasterize's strict z test speckled the two together."""
    out = {}
    for p in np.unique(m.part):
        ts = np.nonzero(m.part == p)[0]
        seen = {}
        for t in ts:
            k = tuple(sorted(map(tuple, np.asarray(m.pos[t], np.float64).round(5))))
            if k in seen and len(set(k)) == 3:
                u = seen[k]
                # D[i, j]: u's corner i against t's corner j; cm[i] = t's corner at u's corner i
                D = np.abs(m.pos[u][:, None, :].astype(np.float64) - m.pos[t][None, :, :]).sum(-1)
                out[int(u)] = (int(t), D.argmin(1))
            seen[k] = t
    # chains (three layers): each shadowed triangle points at the last one
    for u in list(out):
        t, cm = out[u]
        while t in out:
            t2, cm2 = out[t]
            cm, t = cm2[cm], t2
        out[u] = (t, cm)
    return out


def copy_twins(fa, image):
    """The atlas with each shadowed triangle's texels (twins()) painted as its later twin at the same place: whichever
    layer the engine shows, it shows the painting."""
    if not fa.twin:
        return image
    a, size = fa.atlas, fa.atlas.size
    out = image.copy()
    for u, (t, cm) in fa.twin.items():
        ys, xs = np.nonzero(a.tri == u)
        if not len(ys):
            continue
        P = fa.posed.uv[u].astype(np.float64) * size
        Q = fa.posed.uv[t].astype(np.float64)[cm]  # t's corners in u's corner order
        d = (P[1] - P[0], P[2] - P[0])
        den = d[0][0] * d[1][1] - d[0][1] * d[1][0]
        if abs(den) < 1e-12:
            continue
        px, py = xs + 0.5 - P[0, 0], ys + 0.5 - P[0, 1]
        w1 = (px * d[1][1] - py * d[1][0]) / den
        w2 = (py * d[0][0] - px * d[0][1]) / den
        b = np.clip(np.stack([1 - w1 - w2, w1, w2], -1), 0, None)
        b /= np.maximum(b.sum(1, keepdims=True), 1e-12)
        col = paintmod.sample(image[..., :3], b @ Q)
        out[ys, xs, :3] = np.clip(np.round(col), 0, 255).astype(np.uint8)
    return atlasmod.fill(out, a.used())


def build(hrc, size=ATLAS_SIZE, animation=None):
    """FieldAtlas of a field model ("AAAA"): its parts in part space charted into one size x size atlas, filled with
    the game's own colours; posed with `animation` (None: field.load_model's default, the one most fields start it
    on) at frame 0 for the views."""
    m = field.load_model(hrc, animation)
    files = part_files(hrc)
    if [p.file for p in files] != list(m.parts):
        raise ValueError(f"{hrc}: the mesh's parts {m.parts} aren't the model's files {[p.file for p in files]}")
    poly = []
    for pi, pf in enumerate(files):
        idx = BA.poly_index(pfile.read(field.char_read(pf.file + ".P")))
        n = int((m.part == pi).sum())
        if len(idx) != n:
            raise ValueError(f"{pf.file}: {n} triangles, {len(idx)} polygon indices")
        if len(np.unique(idx)) != len(idx):
            raise ValueError(f"{pf.file}: a polygon is in two groups")
        poly.append(idx)
    a = atlasmod.build(BA.part_space(m), size=size, max_size=size, name=m.hrc)
    posed = replace(a.mesh, pos=m.pos)
    return FieldAtlas(m.hrc.lower(), m.hrc, a, posed, m, files, np.concatenate(poly), twins(m))


# --- per-part textures and UV files ----------------------------------------------------------------------------------

def part_textures(fa, image):
    """Per mesh part (texture RGBA, uv (numpolys, 3, 2) against it): the part's charts cut out of the atlas image
    texel for texel (field._part_texture) and its polygons' u, v moved with them. Degenerate triangles (no area) all
    share one spot charted with the first one's part: the others point at their own part's first texel row's middle
    (they draw nothing)."""
    a = fa.atlas
    out = []
    for pi, pf in enumerate(fa.parts):
        ptex, moves, k = field._part_texture(a, pi, image)
        H, W = ptex.shape[:2]
        chart_of = {int(t): ci for ci, c in enumerate(a.charts) if c.part == pi for t in c.tris}
        tris = np.nonzero(fa.posed.part == pi)[0]
        px = fa.posed.uv[tris].astype(np.float64) * a.size * k
        fallback = None
        for j, t in enumerate(tris):
            ci = chart_of.get(int(t))
            if ci is None:
                if fallback is None:  # the middle of the part's first chart
                    x, y, w, h = a.charts[min(chart_of.values())].rect
                    dx, dy = moves[min(chart_of.values())]
                    fallback = np.array([(x + w / 2) * k + dx, (y + h / 2) * k + dy])
                px[j] = fallback
            else:
                px[j] += moves[ci]
        uv = np.zeros((pf.numpolys, 3, 2), np.float32)
        uv[fa.poly[tris]] = (px / (W, H)).astype(np.float32)
        out.append((ptex, uv))
    return out


def uv_bytes(pf, uv):
    return BA.UV_MAGIC + struct.pack("<3I", BA.UV_VERSION, pf.numverts, pf.numpolys) + uv.astype("<f4").tobytes()


def export(fa, image, out_dir, info=None):
    """<P>.png and <P>.P.uv per part into out_dir (the atlas image cut per part), plus info.json. Returns {file name:
    path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = np.ascontiguousarray(image)
    if image.shape[-1] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    files, sizes = {}, {}
    for pf, (ptex, uv) in zip(fa.parts, part_textures(fa, image)):
        p = out_dir / pf.texture
        Image.fromarray(ptex, "RGBA").save(p)
        files[p.name] = p
        sizes[pf.file] = [ptex.shape[1], ptex.shape[0]]
        p = out_dir / pf.uv_name
        p.write_bytes(uv_bytes(pf, uv))
        files[p.name] = p
    meta = {"id": fa.id, "model": fa.name, "animation": fa.original.animation,
            "atlas": {"size": fa.size, "density": round(fa.atlas.density, 4), "charts": len(fa.atlas.charts)},
            "parts": [{"file": pf.file, "game": pf.game, "bone": pf.bone, "numverts": pf.numverts,
                       "numpolys": pf.numpolys, "texture": pf.texture, "texture_size": sizes[pf.file]}
                      for pf in fa.parts],
            "files": sorted(files), "at": time.strftime("%Y-%m-%d %H:%M:%S"), **(info or {})}
    (out_dir / "info.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out_dir / "preview").mkdir(exist_ok=True)
    Image.fromarray(image).save(out_dir / "preview" / "atlas.png")  # what the parts were cut from (check)
    return files


def build_files(out_dir):
    """{file name: path} of a build folder's game files (the PNG and .uv files)."""
    out_dir = Path(out_dir)
    return {p.name: p for p in sorted(out_dir.iterdir()) if p.suffix.lower() in (".png", ".uv") and p.is_file()}


def check(fa, files):
    """Problems with a build's files ({name: path}) against the .p files the game loads (direct/char first): []
    when all is well. Per part: its PNG (RGBA, at most 4096), its .uv (counts as the .p header's, u, v in 0..1), and
    each drawn polygon's middle landing on the same colour in its part's PNG as in the atlas (the cut is texel for
    texel)."""
    probs = []
    for pi, pf in enumerate(fa.parts):
        h = struct.unpack_from("<16I", field.char_read(pf.file + ".P"), 0)
        if (h[3], h[9]) != (pf.numverts, pf.numpolys):
            probs.append(f"{pf.file}.P has changed: {h[3]}/{h[9]}, built for {pf.numverts}/{pf.numpolys}")
        png, uvp = files.get(pf.texture), files.get(pf.uv_name)
        if png is None or not Path(png).exists():
            probs.append(f"{pf.texture} missing")
            continue
        im = Image.open(png)
        if im.mode != "RGBA" or max(im.size) > 4096:
            probs.append(f"{pf.texture} is {im.size} {im.mode}")
        if uvp is None or not Path(uvp).exists():
            probs.append(f"{pf.uv_name} missing")
            continue
        nv, npoly, uv = BA.read_uv(Path(uvp).read_bytes())
        if (nv, npoly) != (h[3], h[9]):
            probs.append(f"{pf.uv_name} counts {nv}/{npoly}, the game's {pf.file}.P {h[3]}/{h[9]}")
            continue
        if not np.isfinite(uv).all() or uv.min() < 0 or uv.max() > 1:
            probs.append(f"{pf.uv_name} has u, v outside 0..1")
        # the drawn polygons' middles: the same texel colour in the part's PNG as in the atlas
        tris = np.nonzero((fa.posed.part == pi) & fa.atlas.selected)[0]
        _, area = atlasmod._normals_areas(fa.posed.pos[tris])
        tris = tris[area > atlasmod.EPS]
        img = np.asarray(im.convert("RGBA"))
        c_part = paintmod.sample(img[..., :3], uv[fa.poly[tris]].mean(1))
        c_atlas = paintmod.sample(fa.atlas.image[..., :3], fa.posed.uv[tris].mean(1))
        bad = np.abs(c_part - c_atlas).max(1) > 2
        if bad.mean() > 0.002:
            probs.append(f"{pf.uv_name}: {int(bad.sum())} of {len(tris)} polygons land on other colours")
    return probs


def check_image(fa, files, image):
    """check() with the atlas image the build was cut from (a painted one) in place of the built atlas'."""
    a = replace(fa.atlas, image=np.ascontiguousarray(image))
    return check(replace(fa, atlas=a), files)


# --- renders ---------------------------------------------------------------------------------------------------------

def original_shade(mesh, r):
    """The model as the game draws it (FFNx filters model textures): textured pixels bilinear x vertex colour,
    untextured ones their vertex colours (Gouraud). render.shade samples the nearest texel."""
    H, W = r.tri.shape
    out = np.zeros((H, W, 3), np.float32)
    hit = r.tri >= 0
    t = r.tri[hit]
    vc = np.einsum("nk,nkc->nc", r.bary[hit], mesh.col[t].astype(np.float32))[:, :3]
    col = vc.copy()
    ti, uv = render.texels(mesh, r)
    tih, uvh = ti[hit], uv[hit]
    for k in np.unique(tih[tih >= 0]):
        sel = tih == k
        ref = mesh.textures[k]
        col[sel] = paintmod.sample(ref.tex.rgba(ref.palette)[..., :3], uvh[sel]) * vc[sel] / 255.0
    out[hit] = col
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def views(mesh, specs, size=VIEW_SIZE, margin=BA.CLOSE_MARGIN, body_margin=0.9):
    """[(label, render.Camera)] for (label, region, yaw, pitch, ...) specs round a posed model (facing FACING)."""
    return [(spec[0], cam) for spec, cam in _cameras(mesh, specs, size, margin, body_margin)]


def _cameras(mesh, specs, size=VIEW_SIZE, margin=BA.CLOSE_MARGIN, body_margin=0.9):
    """[(spec, render.Camera)]: views(), each with its spec (a spec whose region is empty is left out)."""
    pts = np.unique(mesh.pos.reshape(-1, 3).astype(np.float64), axis=0)
    w, h = size
    out = []
    for spec in specs:
        label, region, yaw, pitch = spec[:4]
        if isinstance(region, tuple) and region and region[0] == "parts":
            idx = [i for i, n in enumerate(mesh.parts) if n in set(region[1])]
            sel = mesh.pos[np.isin(mesh.part, idx)].reshape(-1, 3).astype(np.float64)
        else:
            sel = BA.region_points(mesh, region, pts)
        if not len(sel):
            continue
        t = (sel.min(0) + sel.max(0)) / 2
        mg = body_margin if region == ("body",) else margin
        eye, d = BA._fit(sel, t, yaw, pitch, w, h, margin=mg, facing=FACING)
        out.append((spec, render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV,
                                        width=w, height=h, near=max(d * 0.01, 0.05))))
    return out


def backdrop(w, h, colours=BACKDROP):
    """(RGB, depth inf): the views' plain vertical gradient."""
    top, bottom = (np.asarray(c, np.float32) for c in colours)
    k = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    col = top * (1 - k) + bottom * k
    return np.broadcast_to(col[:, None, :], (h, w, 3)).astype(np.uint8).copy(), np.full((h, w), np.inf, np.float32)


def atlas_view(fa, image, cam, mesh=None):
    """(RGB, raster) of the atlas model showing `image`."""
    m = BA.atlas_only(fa.seen(fa.posed) if mesh is None else mesh, fa.atlas.index)
    r = render.rasterize(m, cam)
    return paintmod.shade(m, r, [image]), r


def identity(fa, cam, image=None):
    """The atlas against the game's colours from a camera: (original render (bilinear x vertex colours), atlas
    render (bilinear), stats over the pixels both cover: mean, p99 and max of the largest channel difference, the
    share within 3 and 8 levels)."""
    orig = fa.seen(fa.original)
    r = render.rasterize(orig, cam)
    o_img = original_shade(orig, r)
    a_img, a = atlas_view(fa, fa.atlas.image if image is None else image, cam)
    return o_img, a_img, _stats(o_img, a_img, (r.tri >= 0) & (a.tri >= 0))


def _stats(o, a, hit):
    d = np.abs(o.astype(int) - a.astype(int)).max(-1)[hit]
    return {"pixels": int(hit.sum()), "mean": round(float(d.mean()), 3), "p99": float(np.percentile(d, 99)),
            "max": int(d.max()), "within3": round(float((d <= 3).mean()), 4), "within8": round(float((d <= 8).mean()), 4)}


def files_mesh(fa, files):
    """The posed model drawn from a build's own files, as the engine draws it: each part's triangles with its PNG and
    the u, v its .uv file gives their polygons."""
    m = fa.posed
    texs, uv = [], np.zeros(m.uv.shape, np.float32)
    for pi, pf in enumerate(fa.parts):
        img = np.asarray(Image.open(files[pf.texture]).convert("RGBA"))
        texs.append(meshmod.TexRef(f"files/{pf.file}", atlasmod.as_tex(img), 0))
        _, _, puv = BA.read_uv(Path(files[pf.uv_name]).read_bytes())
        sel = np.nonzero(m.part == pi)[0]
        uv[sel] = puv[fa.poly[sel]]
    return replace(m, uv=uv, tex=m.part.astype(np.int32).copy(), textures=texs,
                   col=np.full(m.col.shape, 255, np.uint8))


def files_identity(fa, files, cam, image=None):
    """The build's files (files_mesh) against the atlas they were cut from (image: the painted one; default the
    built atlas): (atlas render, files render, stats). For the unpainted build it's the original against the files."""
    fm = fa.seen(files_mesh(fa, files))
    r = render.rasterize(fm, cam)
    f_img = paintmod.shade(fm, r, [np.asarray(t.tex.pixels) for t in fm.textures])
    a_img, a = atlas_view(fa, fa.atlas.image if image is None else image, cam)
    return a_img, f_img, _stats(a_img, f_img, (r.tri >= 0) & (a.tri >= 0))


# --- painting --------------------------------------------------------------------------------------------------------

def _words(fa, words):
    return words.replace("{what}", WHAT.get(fa.name, f"a small chibi JRPG character"))


def paint(fa, look=LOOK, strength=STRENGTH, words=WORDS, specs=None, url=BA.DEFAULT_URL, describe=None,
          first=None, better=None, extra_words="", progress=None, log_dir=None, on_progress=None, cancelled=None,
          solo=True, size=VIEW_SIZE, strengths=None, keep=None, facing=FACING_RAMP, fill_each=True):
    """The atlas painted from restyled views (battleatlas.paint's loop, without battle cameras or scene): specs
    (default_views: (label, region, yaw, pitch, solo)) with the model on a plain gradient (backdrop); the first view
    (`first`, the recipe's: Cloud's front turned right) restyled whole, each later one repainting only what it's the
    first to see or sees `better` times better (paint.BETTER 1.3 when None) around the paint so far. A solo view
    (hidden bones, [parts]) shows the model with those bones left out and projects onto those parts only.
    strengths: {view label: repaint} for later views (the recipe's: the face's close views); keep: {.p name: [groups]}
    never painted, the game's own texels kept (the recipe's: the eye and mouth decals; keep_mask()).
    facing: (lo, hi) a view's pixels count for nothing where the surface faces it less than lo (|cos| between the
    normal and the ray), fully from hi (None: all, as battleatlas); fill_each: the next views see the paint so far as
    compose() makes it (each chart's unpainted texels from its nearest paint), not paint.Painter.images().

    Returns (atlas RGBA, [per view dict(label, kind, new)], restyle.Restyler, coverage: the share of the atlas'
    texels some view painted)."""
    from . import restyle
    rc = recipe(fa.name)
    specs = default_views(fa.name, solo) if specs is None else specs
    first = rc.first if first is None else first
    better = rc.better if better is None else better
    strengths = (rc.strengths if strengths is None else strengths) or {}
    kept = np.nonzero(keep_mask(fa, keep))[0]
    stop = None if cancelled is None else BA._stop_check(cancelled)
    pm = BA.atlas_only(fa.seen(fa.posed), fa.atlas.index)
    jobs = []
    for spec, cam in _cameras(pm, specs, size):
        label = spec[0]
        sv = spec[4] if len(spec) > 4 else None
        if sv:
            mesh = BA.hidden(pm, sv[0])
            only = [i for i, n in enumerate(mesh.parts) if n in set(sv[1])]
            jobs.append((label, cam, (mesh, only)))
        else:
            jobs.append((label, cam, None))
    if first:
        k = next((j for j, v in enumerate(jobs) if v[0] == first), None)
        if k is not None:
            jobs = [jobs[k]] + jobs[:k] + jobs[k + 1:]
    s = restyle.look_settings(look)
    prompt = " ".join(x for x in (_words(fa, words), (extra_words or "").strip()) if x)
    R = restyle.Restyler(s, url=url, kind="field_model", name=f"fieldatlas_{fa.id}", prompt=prompt, strength=strength,
                         describe=describe, on_progress=on_progress)
    if stop is not None:
        run, run_text = R.comfy.run, R.comfy.run_text
        R.comfy.run = lambda wf, on_progress=None, cancelled=None: run(wf, on_progress, stop)
        R.comfy.run_text = lambda wf, cancelled=None: run_text(wf, stop)
    P = BA._Painter(pm, 1, [fa.atlas.image])
    P.better = paintmod.BETTER if better is None else float(better)
    log, scene_words = [], None
    bg = backdrop(*size)
    base_denoise = R.denoise
    for i, (label, cam, extra) in enumerate(jobs):
        if stop is not None and stop():
            raise InterruptedError("cancelled")
        mesh = pm if extra is None else extra[0]
        P.mesh = mesh
        r = render.rasterize(mesh, cam)
        if not (r.tri >= 0).any():
            continue
        vis, edge = BA.projectable(r)
        if len(kept):  # the kept triangles take no paint (what shows through their cut-outs does)
            vis &= ~np.isin(r.tri, kept)
            edge = edge * vis
        hint = HINTS.get(label, "")
        if scene_words is not None:
            R.s["scene"] = " ".join(x for x in (scene_words, hint) if x)
        shown = [compose(fa, P)] if fill_each else P.images()
        orig = BA.composite(paintmod.shade(mesh, r, [fa.atlas.image]), r, bg)
        on_model = r.tri >= 0
        if extra is not None:  # only the solo parts' own pixels go back
            vis = vis & np.isin(mesh.part[np.maximum(r.tri, 0)], extra[1]) & on_model
            edge = edge * vis
        if not any((w > 0).any() for w in P.wsum):
            mask = vis
            R.prompt = " ".join(x for x in (prompt, hint) if x)
            out = BA._styled(R, stop, orig, None, None)
            R.prompt = prompt
            scene_words = " ".join(x for x in (restyle.HINTS.get(R.kind, ""), prompt, R.words) if x)
            weight = edge
        else:
            mask = P.view_mask(cam, r) & vis
            new = float(mask.sum() / max(vis.sum(), 1))
            if new < BA.MIN_NEW:
                log.append(dict(label=label, kind="solo" if extra else "round", new=new))
                continue
            cur = BA.composite(paintmod.shade(mesh, r, shown), r, bg)
            grown = cv2.dilate(mask.astype(np.uint8), np.ones((2 * paintmod.MASK_GROW + 1,) * 2, np.uint8)) > 0
            R.denoise = strengths.get(label, base_denoise)
            out = BA._styled(R, stop, orig, cur, grown & (on_model if extra is not None else vis))
            R.denoise = base_denoise
            weight = paintmod.feather(mask) * edge
        if facing:  # surfaces seen edge-on: left to the views that see them squarely
            lo, hi = facing
            weight = weight * np.clip((render.facing(mesh, r, cam) - lo) / (hi - lo), 0, 1)
        P.add(cam, r, out, np.where(weight >= BA.EDGE_MIN, weight, 0).astype(np.float32))
        log.append(dict(label=label, kind="solo" if extra else "round", new=round(float(mask.sum() / max(vis.sum(), 1)), 3)))
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.hstack([orig, out])).save(Path(log_dir) / f"view_{i:02d}.jpg", quality=88)
        if progress:
            progress(i + 1, len(jobs), label)
    P.mesh = pm
    if scene_words is not None:
        R.s["scene"] = scene_words
    used = fa.atlas.used()
    shown_tex = used & ~np.isin(fa.atlas.tri, list(fa.twin or {}))  # (shadowed twins are never seen)
    coverage = float(((P.wsum[0] > 0) & shown_tex).sum() / max(shown_tex.sum(), 1))
    # (no Painter.fill_holes: it grows paint across the atlas, into neighbouring charts' unpainted rims; compose()
    # fills each chart from its own paint)
    img = restore_kept(fa, compose(fa, P), keep)
    return img, log, R, coverage


def compose(fa, P):
    """The painted atlas from the painter: the paint where views put some; the rest of each chart from its nearest
    painted texel (the rims along chart edges the views' samples miss; whole charts no view saw keep the game's
    colours). paint.Painter.images() colours unpainted texels with one linear fit from the original to the paint over
    the whole atlas, which turned the skin by a seam on Cloud's arm into a dotted black line (v1-v8)."""
    w = P.wsum[0]
    have = w > 0
    out = fa.atlas.image.copy()
    out[have, :3] = np.clip(P.acc[0][have] / w[have][:, None], 0, 255).astype(np.uint8)
    used = fa.atlas.used()
    for c in fa.atlas.charts:
        x, y, cw, ch = c.rect
        h = have[y:y + ch, x:x + cw] & used[y:y + ch, x:x + cw]
        if h.any() and not h.all():
            out[y:y + ch, x:x + cw] = atlasmod.fill(out[y:y + ch, x:x + cw], h)
    return atlasmod.fill(out, used)


def keep_mask(fa, keep=None):
    """(t,) bool the triangles kept as the game draws them (keep: {.p name: [groups]}, None the recipe's)."""
    keep = recipe(fa.name).keep if keep is None else keep
    out = np.zeros(len(fa.posed), bool)
    for name, groups in (keep or {}).items():
        if name in fa.posed.parts:
            out |= (fa.posed.part == fa.posed.parts.index(name)) & np.isin(fa.posed.group, groups)
    return out


def restore_kept(fa, image, keep=None):
    """The atlas with the kept triangles' texels (keep_mask) the game's own again (after painting, hole filling and
    colour_lock), the margins refilled."""
    k = np.nonzero(keep_mask(fa, keep))[0]
    out = image.copy()
    if len(k):
        sel = np.isin(fa.atlas.tri, k)
        out[sel] = fa.atlas.image[sel]
    return atlasmod.fill(out, fa.atlas.used())


LOCK_MIN_SAT = BA.LOCK_MIN_SAT


def colour_lock(fa, image, mode="hsv", grey=True):
    """battleatlas.colour_lock (the game's hue, and with "hsv" its saturation, where the game's colour is coloured at
    all), and with `grey` the game's greys kept grey too: texels whose original is greyer than LOCK_MIN_SAT take its
    saturation (v1-v3: the AI's purple cast stayed on Cloud's near-black gloves, his steel pauldron and the sword)."""
    out = BA.colour_lock(fa, image, mode)
    if grey and mode:
        p = cv2.cvtColor(np.ascontiguousarray(out[..., :3]), cv2.COLOR_RGB2HSV_FULL)
        o = cv2.cvtColor(np.ascontiguousarray(fa.atlas.image[..., :3]), cv2.COLOR_RGB2HSV_FULL)
        g = o[..., 1] < LOCK_MIN_SAT * 255
        p[..., 1] = np.where(g, np.minimum(p[..., 1], o[..., 1]), p[..., 1])
        out = out.copy()
        out[..., :3] = cv2.cvtColor(p, cv2.COLOR_HSV2RGB_FULL)
    return atlasmod.fill(out, fa.atlas.used())


# --- previews --------------------------------------------------------------------------------------------------------

def _cell(fa, image, cam, mesh=None):
    """A view of the model (the original when image is None) on the backdrop."""
    m = fa.seen(fa.original) if mesh is None else mesh
    if image is None:
        r = render.rasterize(m, cam)
        fg = original_shade(m, r)
    else:
        fg, r = atlas_view(fa, image, cam)
    return BA.composite(fg, r, backdrop(cam.width, cam.height))


def turntable(fa, image, path, pitch=20, size=(400, 400)):
    """The model all round (8 views, `pitch` degrees up) and its head twice: the original's row above the painted
    one's, per block."""
    rc = recipe(fa.name)
    head = ("bones", rc.head) if rc.head else ("band", 0.0, 0.3)
    specs = [(f"yaw {y}", ("body",), y, pitch) for y in (0, 45, 90, 135, 180, 225, 270, 315)]
    specs += [("head", head, 25, 10), ("head", head, -60, 10)]
    cams = views(fa.posed, specs, size)
    rows = []
    for half in (cams[:5], cams[5:]):
        rows.append(np.hstack([_cell(fa, None, c) for _, c in half]))
        rows.append(np.hstack([_cell(fa, image, c) for _, c in half]))
    Image.fromarray(np.vstack(rows)).save(path, quality=90)


def head_closeup(fa, image, path, size=(640, 640)):
    """The head close up from the front and both sides: original above, painted below."""
    rc = recipe(fa.name)
    head = ("bones", rc.head) if rc.head else ("band", 0.0, 0.3)
    cams = views(fa.posed, [("f", head, 0, 8), ("l", head, 40, 10), ("r", head, -40, 10)], size, margin=0.95)
    Image.fromarray(np.vstack([np.hstack([_cell(fa, None, c) for _, c in cams]),
                               np.hstack([_cell(fa, image, c) for _, c in cams])])).save(path, quality=92)


FIELD_CAMERAS = [("field, front", 20, 35), ("field, back", 160, 35)]  # (label, yaw, pitch): a field's view of him


def field_frame(fa, image, path, height=180, size=(320, 240)):
    """The model about as big as a field shows it (`height` pixels tall at 320 x 240 x 3: the game's frame enlarged
    3 times, a high field camera): original | painted, front and back."""
    cells = []
    for label, yaw, pitch in FIELD_CAMERAS:
        W, H = size[0] * 3, size[1] * 3
        pts = np.unique(fa.posed.pos.reshape(-1, 3).astype(np.float64), axis=0)
        t = (pts.min(0) + pts.max(0)) / 2
        span = pts[:, 1].max() - pts[:, 1].min()
        f = 3 * 240 / 2 / np.tan(np.radians(VIEW_FOV) / 2)
        d = f * span / (height * 3) * 1.0
        a, p = np.radians(yaw + FACING) + np.pi, np.radians(pitch)
        eye = t + d * np.array([np.sin(a) * np.cos(p), -np.sin(p), np.cos(a) * np.cos(p)])
        cam = render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV, width=W, height=H,
                            near=0.1)
        cells += [_cell(fa, None, cam), _cell(fa, image, cam)]
    Image.fromarray(np.vstack([np.hstack(cells[:2]), np.hstack(cells[2:])])).save(path, quality=92)


def identity_cameras(fa):
    """Two views for the identity checks: the body from the front a little turned, the head close up."""
    rc = recipe(fa.name)
    head = ("bones", rc.head) if rc.head else ("band", 0.0, 0.3)
    return views(fa.posed, [("body", ("body",), 30, 15), ("head", head, -30, 10)], (1024, 1024))


def save_identity(fa, out_dir, files=None, image=None):
    """identity.png (original | atlas | difference x16, body and head) and with `files` files_identity.png (the
    atlas the files were cut from | the files | difference x16). Returns the stats."""
    out = {}
    rows = []
    for label, cam in identity_cameras(fa):
        o, a, st = identity(fa, cam)
        out[f"atlas_{label}"] = st
        d = np.clip(np.abs(o.astype(int) - a.astype(int)).max(-1) * 16, 0, 255).astype(np.uint8)
        rows.append(np.hstack([o, a, np.dstack([d] * 3)]))
    Image.fromarray(np.vstack(rows)).resize((1536, 1024), Image.LANCZOS).save(Path(out_dir) / "identity.jpg", quality=92)
    if files is not None:
        rows = []
        for label, cam in identity_cameras(fa):
            a, f, st = files_identity(fa, files, cam, image)
            out[f"files_{label}"] = st
            d = np.clip(np.abs(a.astype(int) - f.astype(int)).max(-1) * 16, 0, 255).astype(np.uint8)
            rows.append(np.hstack([a, f, np.dstack([d] * 3)]))
        Image.fromarray(np.vstack(rows)).resize((1536, 1024), Image.LANCZOS).save(Path(out_dir) / "files_identity.jpg",
                                                                                   quality=92)
    return out


def previews(fa, image, out_dir):
    d = Path(out_dir) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    turntable(fa, image, d / "turntable.jpg")
    head_closeup(fa, image, d / "head.jpg")
    field_frame(fa, image, d / "field.jpg")


# --- builds ----------------------------------------------------------------------------------------------------------

def _next_version(id):
    d = WORK / id.lower()
    n = [int(p.name[1:]) for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()] if d.exists() else []
    return f"v{max(n, default=0) + 1}"


def version_dir(id, version=None):
    """A build's folder (the newest when version is None)."""
    d = WORK / id.lower()
    if version:
        return d / version
    vs = sorted((p for p in d.glob("*") if (p / "info.json").exists()), key=lambda p: p.stat().st_mtime) if d.exists() else []
    if not vs:
        raise FileNotFoundError(f"no build of {id} in {d}")
    return vs[-1]


def build_of(id, version=None):
    """The FieldAtlas a build was made with (its atlas size and animation from its info.json)."""
    d = version_dir(id, version)
    try:
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    return build(id, (info.get("atlas") or {}).get("size") or ATLAS_SIZE, info.get("animation") or None)


def finish(fa, image, out, info):
    """Previews, the files, check() and the files' identity: into out. Raises ValueError when check() finds a problem."""
    out = Path(out)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    previews(fa, image, out)
    files = export(fa, image, out, info)
    probs = check_image(fa, files, image)
    if probs:
        raise ValueError(f"{out.name}: " + "; ".join(probs))
    info["identity"] = save_identity(fa, out / "preview", files, image)
    export(fa, image, out, info)  # (info.json again, with the files' identity)
    return files


def make(hrc, size=ATLAS_SIZE, look=None, strength=None, extra_words="", url=None, progress=None, cancelled=None,
         out_dir=None, words=WORDS, lock="hsv", **paint_kw):
    """A painted build end to end: build, paint (the model's recipe), colour_lock (the game's hue and saturation,
    the party's "hsv"; False none), previews, export, check. Returns (version folder, info)."""
    def stop():
        if cancelled is not None and BA._stop_check(cancelled)():
            raise InterruptedError("cancelled")
    look = LOOK if look is None else look
    strength = STRENGTH if strength is None else strength
    fa = build(hrc, size)
    stop()
    out = Path(out_dir) if out_dir else WORK / fa.id / _next_version(fa.id)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    t = time.time()
    img, log, R, coverage = paint(fa, look, strength, words, url=url or BA.DEFAULT_URL, log_dir=out / "preview",
                                  progress=progress, extra_words=extra_words, cancelled=cancelled, **paint_kw)
    info = {"painted": {"look": look, "strength": strength, "words": _words(fa, words), "describer": R.words,
                        "prompt": R.s.get("scene"), "extra_words": extra_words or "", "seconds": round(time.time() - t),
                        "coverage": round(coverage, 4), "views": log,
                        # the recipe as run (paint()'s defaults: the model's RECIPES entry)
                        "better": paint_kw.get("better", recipe(fa.name).better),
                        "strengths": paint_kw.get("strengths", recipe(fa.name).strengths),
                        "keep": paint_kw.get("keep", recipe(fa.name).keep),
                        "facing": paint_kw.get("facing", FACING_RAMP),
                        **{k: v for k, v in paint_kw.items() if isinstance(v, (int, float, str, bool, type(None)))}}}
    stop()
    if lock:
        Image.fromarray(img).save(out / "preview" / "unlocked.png")
        img = colour_lock(fa, img, lock)
        info["locked"] = lock
    img = copy_twins(fa, restore_kept(fa, img))
    finish(fa, img, out, info)
    return out, info


# --- install ---------------------------------------------------------------------------------------------------------

_lock = threading.RLock()


def atlas_dir():
    """The game's mesh/field/atlas (under FFNx's external_mesh_path)."""
    from . import battlelight
    return battlelight.mesh_dir() / "field" / "atlas"


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
    """Copy a build's <P>.png and <P>.P.uv files into the game's mesh/field/atlas/, recorded in the manifest
    (battleatlas.install's rules: replaces this model's earlier install; writes nothing where a file it would write is
    there and isn't ours). Returns the manifest entry."""
    id = id.lower()
    src_dir = version_dir(id, version)
    files = build_files(src_dir)
    if not files:
        raise FileNotFoundError(f"nothing to install in {src_dir}")
    dest = atlas_dir()
    with _lock:
        data = installed()
        kept = []
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
                done.append({"path": str(dst), "sha1": BA._hash(dst)})
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
    and the folders it made if they're empty (battleatlas.uninstall's rules). Returns (files removed, files left)."""
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
            if BA._hash(p) != f.get("sha1"):
                left.append(str(p))
                continue
            p.unlink()
            removed += 1
        kept = []
        for d in reversed(entry.get("folders", [])):
            if not Path(d).exists():
                continue
            try:
                Path(d).rmdir()
            except OSError:
                heir = next((e for k, e in data.items() if k != id and any(
                    Path(f["path"]).is_relative_to(d) for f in e["files"])), None)
                if heir is not None:
                    if d not in heir.setdefault("folders", []):
                        heir["folders"].insert(0, d)
                else:
                    kept.insert(0, d)
        if kept or left:
            data[id] = {"version": entry.get("version"), "from": entry.get("from"), "at": entry.get("at"),
                        "files": [], "folders": kept, "left": left + entry.get("left", [])}
        else:
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
        out.setdefault(k, {})["installed"] = {"version": e["version"], "files": len(e["files"]),
                                              **({"left": e["left"]} if e.get("left") else {})}
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.fieldatlas")
    ap.add_argument("cmd", choices=("build", "paint", "check", "install", "uninstall", "status"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--version")
    ap.add_argument("--size", type=int, default=ATLAS_SIZE)
    ap.add_argument("--look", default=LOOK)
    ap.add_argument("--strength", type=float, default=STRENGTH)
    ap.add_argument("--words", default=WORDS)
    ap.add_argument("--allow-game", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd in ("build", "paint"):
        out = WORK / a.id.lower() / (a.version or _next_version(a.id))
        if a.cmd == "paint":
            from . import battlelight
            if battlelight.game_running() and not a.allow_game:
                raise SystemExit("the game is running (it shares the GPU with ComfyUI): close it first")
            out, info = make(a.id, a.size, a.look, a.strength, out_dir=out, words=a.words,
                             progress=lambda i, n, label: print(f"view {i}/{n}: {label}", flush=True))
            print(json.dumps({"out": str(out), "identity": info["identity"]}, indent=1))
            return 0
        fa = build(a.id, a.size)
        info = {}
        finish(fa, fa.atlas.image, out, info)
        print(json.dumps({"out": str(out), "identity": info["identity"]}, indent=1))
    elif a.cmd == "check":
        fa = build_of(a.id, a.version)
        d = version_dir(a.id, a.version)
        atlas_png = d / "preview" / "atlas.png"
        files = build_files(d)
        p = (check_image(fa, files, np.asarray(Image.open(atlas_png).convert("RGBA"))) if atlas_png.exists()
             else check(fa, files))
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
