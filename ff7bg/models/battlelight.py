"""Painted battle scenes made to look better beyond their textures (user, 2026-11-07, four things approved): per
painted battle scene version (ai/models/scene/<id>/<version>/, paint.py's flat sheets), what FFNx-3d needs to

1. light the fight from the painting (light): the view all round from the middle of the action, rendered from the
   painted textures (render.py, paint.shade, times the scene's vertex colours as the game draws it), turned into
   FFNx's image-based light (a specular cube with mips and a cosine-blurred diffuse cube: ff3d.envmap's writer), plus
   a sun (the brightest part of the painted sky, kept 35-75 degrees up; how sunny the painting looks sets its share)
   and the light's strength and colour: the party and the enemies take the scene's light (a cool overcast marsh, a
   dim factory) instead of FFNx's one grey light for every battle;
2. fade and grade the characters (haze, grade): haze colour from the painted horizon, its density from how the
   painting's local contrast falls with distance from the battle camera, start and reach from where the fight is
   (none on the scene itself: the painting has its own); the characters' colour grade from the painting against the
   original (atmos.grade's bounded fit);
3. bring the scene to life (life): sky textures that drift (scenepaint.classify's far textures that are only sky,
   along the way they tile), grass that sways (ground textures that are grass: colour, and Qwen when allowed), and
   particles where the scene suits them (Qwen on the battle camera's view, else the Studio's description: embers,
   snow, rain, fireflies; no dust since 2026-11-09);
4. give the painted textures relief (normals): Marigold's surface normals of each painted sheet (wrap-padded, fine
   relief only), encoded for FFNx's cotangent frame as the fields' are, saved as <NAME>_00_nml.png.

Formats: ff7-3d/dev/battle-engine/FORMAT.md (the FFNx-3d battle work, 2026-11-07), in short:
- lighting/config.toml [battle_STAGE<nn>] (nn: the scene's location, battle.scene_location) and
  lighting/ibl/battle_STAGE<nn>_s.dds / _d.dds; the engine takes [bat_<formation>] and ibl/bat_<formation>_*.dds
  first, then those, then [battles] (game-wide: char_match, char_exposure... not written here), then the top level.
  FFNx before that work reads only the per-formation ones: install(..., formations=True) writes those too, for every
  formation fought there.
- Keys: light_rotation_vertical / _horizontal (toward the sun in battle space, y down: (cos v sin h, -sin v,
  cos v cos h), measured in game 2026-11-07: 0 lights from +z, 90 from +x; FORMAT.md's -cos v cos h mirrored every
  sun front to back; the shadows and, with the battle work, the shading), light_intensity, light_color (sRGB, the shader
  linearises), ambient_light_intensity, ambient_light_color, char_ambient (everything but the scene), material_*;
  haze_color / haze_density / haze_start / haze_strength / haze_scene; char_grade_full; normal_strength,
  normal_edge; uv_scroll = [{texture, u, v}] (texture widths / heights per second); sway = [{texture, amount 0..1,
  speed, dir [u, v]}].
- Normal maps <NAME>_<pal>_nml.png beside each installed painted texture (FFNx's own name for them); particles
  <external_mesh_path>/battle/STAGE<nn>_particles.toml (the fields' format, battle coordinates: x, y down, z; lift
  and rise are up).

The battle stage shares the light's direction and colour (checked in game, 2026-11-07: FFNx lights the scene's
polygons too, all but the sky textures in disable_lighting_textures; FFNx's defaults leave the painted floor about as
painted). So the light is set so that an up-facing surface (the floor) gets FLOOR x its colour, neutral but for TINT
of the light's cast, and only CONTRAST of the painting's light from other directions is kept: the stage stays as
painted while the characters' sides and undersides take the scene's light. char_ambient (the characters' fill, the
battle work) is written at the scene's ambient times CHAR_FILL.

Build outputs go to work/models/battlelight/<id>/<version>/ (settings.json, ibl/, textures/battle/, particles,
previews); install() copies them into the game with a manifest (work/models/battlelight/installed.json) and edits
config.toml under ff3d.ibl.config_lock(), only its own sections. AI steps (Qwen, Marigold) need ComfyUI and refuse
to run while the game is running unless told otherwise (the game and ComfyUI share the GPU).

  python -m ff7bg.models.battlelight build <id> [--version V] [--parts light,haze,grade,life,normals] [--no-ai]
  python -m ff7bg.models.battlelight install <id> [--version V] [--formations]
  python -m ff7bg.models.battlelight uninstall <id>
  python -m ff7bg.models.battlelight check [<id>]
  python -m ff7bg.models.battlelight status
"""
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import battle, paint, render, scenepaint

REPO = battle.REPO
SCENES = REPO / "ai" / "models" / "scene"
WORK = REPO / "work" / "models" / "battlelight"
MANIFEST = WORK / "installed.json"
GAME = battle.GAME_WORKDIR
LIGHTING = GAME / "lighting"
IBL_DIR = LIGHTING / "ibl"
FF7_3D = Path(os.environ.get("FF7_3D", Path(__file__).resolve().parents[2] / "ff7-3d"))  # the 3D pipeline (ff3d) in this repo

PARTS = ("light", "haze", "grade", "life", "normals")
AI_PARTS = ("life", "normals")  # these ask ComfyUI (Qwen, Marigold); life works without it, less well
PER_FORMATION = False  # install(): also the per-formation [bat_<f>] sections and IBL files FFNx reads today

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)  # linear light's luma
SRGB_LUMA = np.array([0.299, 0.587, 0.114], np.float32)  # sRGB luma (atmos.grade's)

# the fight, in battle units (y down): party members are ~950 tall (Cloud 955, Barret 920) and stand on y = 0;
# formations put enemies to z -3000 and the party to z +2500 or so around the cameras' look-at point
CHAR_H = 950.0
ACTION_R = 3000.0
# 1. the light
SPEC_N = 128        # the specular cube's top mip (envmap.make's)
SRC_N = 32          # the cube the diffuse light, the sun and the mips are worked out from
RENDER_SCALE = 2    # cube faces are rendered this many times finer, then filtered down
CUBE_TEX = 512      # ...from the painted textures shrunk to at most this (a 4096 px floor sampled sparsely is noise)
LEVEL = 0.45        # the brightest stored diffuse texel (envmap.LEVEL: the ambient strength keeps its meaning)
TINT = 0.25         # how much of the light's colour cast reaches an up-facing surface (the stage: 1 would tint the
                    # painted floor by the light it was painted under, twice)
CONTRAST = 0.85     # how much of the painting's difference in light between directions is kept (as a power of the
                    # ratio to the light from above: 1 all, 0 none; 0.6 left oo's party flat and grey, 2026-11-07)
FILL = (0.35, 1.25)  # ...and the darkest and brightest a direction may be against the light from above
FLOOR = 1.0         # what an up-facing surface gets, times its colour: the painting as painted (FFNx's default light
                    # gives oo's painted floor about 0.92, measured in game against our render, 2026-11-07)
SPEC = 0.1          # the reflections' cube against the diffuse one: rough as the material is, the split-sum's grazing
                    # term (~0.015, not times the colour) put the sky's blue on oo's dark painted floor at full
                    # strength (+35% blue in game, 2026-11-07)
SAT_MAX = 0.2       # how colourful the light may get (ibl.CHAR_SAT_MAX is 0.25: strongly coloured scenes don't dye
                    # them; less here, as the stage takes it too)
SUN_LOBE = 16       # the sun is looked for in the painting blurred by a cos^SUN_LOBE lobe (about 20 degrees)
SUN_SEARCH = 5.0    # ...at least this many degrees up
SUN_ELEV = (35.0, 60.0)  # and kept this high (long shadows across a painted floor look wrong; higher, it lit little
                          # but the tops of heads)
SUN_CLEAR = 0.85    # ...or higher, until this much of the floor round the fight sees it (clear_sun: walls and cliffs
SUN_CLEAR_MAX = 75.0  # shadow it), up to this high,
SUN_STEP = 5.0      # in these steps
SUN_FLOOR_R = 3000.0  # the floor looked at: this far round the fight's middle (battle units; formations stand within)
SUN_SHARE = (0.25, 0.5)   # the sun's share of the light on the floor, overcast (or indoors) to sunny...
SUNNY = (1.3, 3.0)  # ...as the sun's part of the sky is this many times brighter than the sky on average
MATERIAL = {"material_roughness": 0.95, "material_metallic": 0.0, "material_specular": 0.04}  # [fields]' if none
CHAR_FILL = 1.0     # char_ambient: the characters' fill against the scene's ambient
# 2. haze and grade
HAZE_MAX = 0.2      # atmos.HAZE_MAX: the most haze a character gets at the far side of the fight (times haze_strength)
HAZE_DARK_PCT = 2   # atmos.haze_dark_bound's
HAZE_STRENGTH = 0.15  # [fields]' haze_strength if none
HAZE_SCENE = 0.0    # haze_scene: the painting has its own distance haze
# 3. life
SKY_ELEV = 12.0     # a far texture all at least this high up (10th percentile, degrees) is sky; others ask Qwen
DRIFT = 0.0025      # the sky's drift, radians per second round the arena (about 0.4% of the battle picture a second)
GRASS_SHARE = 0.3   # a ground texture this much grass (colour) sways...
SWAY = 0.6          # ...this much when it's all grass
WIND = (1.0, 0.0, 1.0)  # the wind over the ground (battle x, y, z): the way the engine's gusts travel (+x+z)
PARTICLE_AMOUNT = 0.6
PARTICLE_KINDS = ("embers", "fireflies", "snow", "rain")  # no dust since 2026-11-09 (the user: it looked bad)
PARTICLE_NEAR = 0.35  # particles stop growing nearer the camera than this share of its distance to the fight
FLOOR_POINTS = 400
# 4. normal maps
NORMAL_STRENGTH = 0.5
NORMAL_EDGE = 0.0   # normal_edge: the maps tile (wrap-padded), no fade along texture edges needed
NORMAL_MAX = 2048   # the normal map's longest side (half the painted texture, at most this)
MG_TILE = 768       # Marigold sees the sheet (at the normal map's size) in tiles this big at most...
MG_PAD = 64         # ...wrap-padded this much, blended over the overlaps
MG_WEIGHT = 0.7     # Marigold's relief, times (it invents the odd ridge: oq's floor sheet)
RELIEF = 1 / 12     # only relief finer than this share of the sheet (Marigold reads a flat sheet as a receding floor)
BUMP = 4.0          # the painting's fine texture as height (surface.write_normals' bump; its BUMP_MAX is 10)...
BUMP_SCALE = 4.0    # ...detail finer than this many pixels of the map

_lock = threading.RLock()


# --- the private 3D pipeline ------------------------------------------------------------------------------------------

def _ff3d(name):
    """A module of ff3d (ff7-3d, not in this repo): envmap's cube writer, ibl's config lock, comfy, marigold, atmos."""
    if str(FF7_3D) not in sys.path:
        sys.path.insert(0, str(FF7_3D))
    return importlib.import_module(f"ff3d.{name}")


def game_running():
    """Is ff7_en.exe running? (ComfyUI jobs wait for it to close: they share the GPU.)"""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ff7_en.exe", "/NH"], capture_output=True, text=True,
                           timeout=20)
        return "ff7_en.exe" in r.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# --- a painted version ------------------------------------------------------------------------------------------------

@dataclass
class Scene:
    id: str
    version: str
    dir: Path
    meta: dict
    mesh: object        # battle.BattleMesh
    images: list        # per mesh texture RGBA uint8: the painted one where the version has it
    painted: list       # per mesh texture: painted?
    name: str           # the game's name: "STAGE08"
    location: int

    @property
    def section(self):
        return f"battle_{self.name}"

    @property
    def out(self):
        return WORK / self.id / self.version

    def originals(self):
        return [t.tex.rgba(t.palette) for t in self.mesh.textures]


def versions(id):
    d = SCENES / id.lower()
    return sorted(p.name for p in d.iterdir() if (p / "meta.json").exists()) if d.exists() else []


def current(id):
    try:
        cur = json.loads((SCENES / id.lower() / "model.json").read_text(encoding="utf-8")).get("current")
    except (OSError, ValueError):
        cur = None
    return cur if cur and (SCENES / id.lower() / cur / "meta.json").exists() else None


def load(id, version=None):
    """A painted battle scene version (the Studio's current one by default) with its mesh and textures."""
    id = id.lower()
    version = version or current(id) or (versions(id) or [None])[-1]
    if not version:
        raise FileNotFoundError(f"battle scene {id} has no painted version")
    vdir = SCENES / id / version
    meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
    mesh = battle.load_scene(id)
    images, painted = [], []
    for t in mesh.textures:
        p = vdir / "textures" / f"{t.name}.png"  # t.name: "battle/STAGE08_T00_00"
        if p.exists():
            images.append(np.asarray(Image.open(p).convert("RGBA")))
            painted.append(True)
        else:
            images.append(t.tex.rgba(t.palette))
            painted.append(False)
    return Scene(id, version, vdir, meta, mesh, images, painted, battle.game_name(id), battle.scene_location(id))


def tex_name(sc, k):
    """FFNx's override name of mesh texture k without its folder or palette: "STAGE08_T01"."""
    return sc.mesh.textures[k].key.split("/", 1)[1]


def _small(images, side):
    out = []
    for img in images:
        h, w = img.shape[:2]
        k = side / max(h, w)
        out.append(cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
                   if k < 1 else img)
    return out


def _shade(mesh, r, images):
    """(sRGB float 0..255, hit) of a view as the game draws the scene: the textures sampled smoothly (paint.shade) times
    the vertex colours (oq darkens its far walls with them; 255 is 1); black where nothing is."""
    img = paint.shade(mesh, r, images).astype(np.float32)
    hit = r.tri >= 0
    t = r.tri[hit]
    vc = np.einsum("nk,nkc->nc", r.bary[hit], mesh.col[t][..., :3].astype(np.float32)) / 255
    vc[mesh.tex[t] < 0] = 1.0  # untextured: paint.shade gave their vertex colour already
    img[hit] *= vc
    return img, hit


def _lin(srgb):
    return (np.clip(np.asarray(srgb, np.float32), 0, 255) / 255) ** 2.2


def _luma(lin):
    return lin @ LUMA


def _floor_y(mesh, x, z):
    """The scene's floor height (y down) at x, z: of the surfaces straight above and below, the one nearest y = 0
    (where formations stand everyone); 0 where there's none."""
    p = mesh.pos.astype(np.float64)
    a, b, c = p[:, 0], p[:, 1], p[:, 2]
    d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
    ok = np.abs(d) > 1e-9
    d = np.where(ok, d, 1.0)
    l1 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (z - c[:, 2])) / d
    l2 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (z - c[:, 2])) / d
    l3 = 1 - l1 - l2
    inside = ok & (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
    if not inside.any():
        return 0.0
    y = (l1 * a[:, 1] + l2 * b[:, 1] + l3 * c[:, 1])[inside]
    return float(y[np.argmin(np.abs(y))])


def centre(sc):
    """The middle of the action at a character's middle height: the battle cameras' look-at points averaged (x, z),
    half a character above the floor there."""
    cams = battle.cameras(sc.id)
    tgt = np.mean([c.target for c in cams], 0) if cams else np.zeros(3)
    return np.array([tgt[0], _floor_y(sc.mesh, tgt[0], tgt[2]) - CHAR_H / 2, tgt[2]])


# --- the view all round -----------------------------------------------------------------------------------------------

FACES = np.array([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], np.float64)


def _flat_sky(mesh):
    """Per triangle: (sky: on a far texture (scenepaint.classify), flat: all on about one texel). oo's dome is closed
    over the top by big triangles on one dark navy texel: what the cameras rarely see, but half the sky as light."""
    cls = scenepaint.classify(mesh)
    far = np.array([mesh.tex[t] >= 0 and cls[mesh.tex[t]]["far"] for t in range(len(mesh))], bool)
    uv = np.nan_to_num(mesh.uv.astype(np.float64))
    size = np.array([[t.tex.width, t.tex.height] if t is not None else [1, 1] for t in mesh.textures] or [[1, 1]], float)
    wh = size[np.maximum(mesh.tex, 0)]
    texels = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) -
                          (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1])) * wh[:, 0] * wh[:, 1]
    return far, far & (texels < FLAT_TEXELS)


FLAT_TEXELS = 2.0  # _flat_sky: a far triangle covering fewer texels than this is a flat cap
BOUNCE = 0.5       # cube: the ground as light at most this bright against the sky on average (it reflects the sky's
                   # light: a painted sky is as dark as the artist liked, oo's heavy clouds darker than its grass)


def cube(sc, n=SPEC_N, images=None, eye=None, plausible=True):
    """(radiance (6, n, n, 3) linear, covered (6, n, n) 0..1): the scene all round from `eye` (centre()), as FFNx samples
    an image-based light cube with battle-space directions (envmap.face_dirs' texel layout; checked in game
    2026-11-07 with six coloured faces: -y lights the tops). Black where nothing is (the game's clear colour).
    plausible: as light, the painting made physically likely: a flat cap over the sky (_flat_sky) takes the rest of the
    sky's average, and the ground below is at most BOUNCE times as bright as the sky above on average."""
    envmap = _ff3d("envmap")
    eye = centre(sc) if eye is None else np.asarray(eye, float)
    images = _small(sc.images if images is None else images, CUBE_TEX)
    dirs = envmap.face_dirs(n)
    R = int(math.ceil(n * RENDER_SCALE * 1.04))
    f = R / 2 / 1.04  # a little more than 90 degrees, so every texel's direction is inside the face's view
    out = np.zeros((6, n, n, 3), np.float32)
    cover = np.zeros((6, n, n), np.float32)
    skym = np.zeros((6, n, n), np.float32)
    flatm = np.zeros((6, n, n), np.float32)
    far, flat = _flat_sky(sc.mesh)
    for k, a in enumerate(FACES):
        up = np.array([0.0, 0.0, 1.0]) if abs(a[1]) > 0.5 else np.array([0.0, -1.0, 0.0])
        cam = render.Camera(eye=eye, target=eye + a * 1000.0, up=up, width=R, height=R, near=battle.NEAR, focal=f)
        r = render.rasterize(sc.mesh, cam)
        img, hit = _shade(sc.mesh, r, images)
        tri = np.maximum(r.tri, 0)
        c = dirs[k] @ cam.rotation().T
        mx = (R / 2 + f * c[..., 0] / c[..., 2] - 0.5).astype(np.float32)
        my = (R / 2 + f * c[..., 1] / c[..., 2] - 0.5).astype(np.float32)
        for dst, src in ((out, _lin(img)), (cover, hit), (skym, hit & far[tri] & ~flat[tri]), (flatm, hit & flat[tri])):
            src = cv2.blur(src.astype(np.float32), (RENDER_SCALE, RENDER_SCALE))
            dst[k] = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if plausible:
        w = np.broadcast_to(envmap.solid_angles(n), cover.shape)
        upper = dirs[..., 1] < -0.05
        sw = w * skym * upper
        if flatm.max() > 0 and sw.sum() > 0:
            sky = (out * sw[..., None]).sum((0, 1, 2)) / sw.sum()
            out = out * (1 - flatm[..., None]) + sky * flatm[..., None]
        # nothing above (the game's black over oq's factory floor): lit as the rest of what's above is, or as all of
        # it (its lamps are somewhere up there: the floor is lit in the painting)
        above_part = np.clip((0.05 - dirs[..., 1]) / 0.1, 0, 1) * (1 - cover)
        if above_part.max() > 0.01:
            cu = w * cover * upper
            ca = w * cover
            base = cu if cu.sum() > 0.1 * (w * upper).sum() else ca
            if base.sum() > 0:
                fill = (out * base[..., None]).sum((0, 1, 2)) / base.sum() / np.maximum(
                    (base * cover).sum() / base.sum(), 1e-6)
                out = out + above_part[..., None] * fill
        lum = _luma(out)
        above = float((lum * w)[upper].sum() / max(w[upper].sum(), 1e-9))
        below = dirs[..., 1] > 0.05
        under = float((lum * w)[below].sum() / max(w[below].sum(), 1e-9))
        if under > BOUNCE * above > 0:
            ramp = np.clip((dirs[..., 1] + 0.05) / 0.15, 0, 1)  # from 3 degrees above the horizon to 6 below
            out = out * (1 - ramp * (1 - BOUNCE * above / under))[..., None]
    return out, cover


def _down(c, n):
    k = c.shape[1] // n
    return c.reshape(6, n, k, n, k, -1).mean((2, 4)) if k > 1 else c


def _cap_sat(rgb, most=SAT_MAX):
    """Linear colours moved toward their own grey until (max - min) / max <= most, brightness kept (ibl.cap_saturation
    for arrays)."""
    rgb = np.asarray(rgb, np.float64)
    hi, lo = rgb.max(-1, keepdims=True), rgb.min(-1, keepdims=True)
    grey = (rgb @ LUMA.astype(np.float64))[..., None]
    sat = (hi - lo) / np.maximum(hi, 1e-9)
    t = np.clip(most * grey / np.maximum((hi - lo) - most * (hi - grey), 1e-9), 0, 1)
    return np.where(sat > most, grey + t * (rgb - grey), rgb)


def sun_angles(direction):
    """(light_rotation_vertical, light_rotation_horizontal) for a direction toward the sun in battle space (y down):
    the battle's sun lights from (cos v sin h, -sin v, cos v cos h). Measured in game (oq, formation 310, 2026-11-07):
    at 0 the characters' lit sides, their shadows and the normal-mapped floor's bright sides all face +z, at 90 +x.
    (FFNx's own rotation gives -cos v cos h for z; the battle view it goes through mirrors z.)"""
    d = np.asarray(direction, float) / np.linalg.norm(direction)
    v = math.degrees(math.asin(float(np.clip(-d[1], -1, 1))))
    h = math.degrees(math.atan2(float(d[0]), float(d[2]))) % 360.0
    return v, h


def sun_direction(vertical, horizontal):
    v, h = math.radians(vertical), math.radians(horizontal)
    return np.array([math.cos(v) * math.sin(h), -math.sin(v), math.cos(v) * math.cos(h)])


def floor_points(sc, radius=None, n=11):
    """Points just above the floor round the middle of the fight (battle space, y down)."""
    radius = SUN_FLOOR_R if radius is None else radius
    c = centre(sc)
    g = np.linspace(-radius, radius, n)
    return np.array([(c[0] + x, _floor_y(sc.mesh, c[0] + x, c[2] + z) - 5.0, c[2] + z) for x in g for z in g])


def sunlit(sc, vertical, horizontal, points=None, skip=None):
    """The share of the floor round the fight that sees the sun (rays toward it against the scene's own polygons, the
    unlit sky domes left out: they cast nothing). Checked against the game: oq's floor is all in its walls' shadow up
    to 35 degrees, a sixth lit at 40, half at 45, all at 60, in game and here."""
    pts = floor_points(sc) if points is None else points
    if skip is None:
        unlit = unlit_textures()
        names = [tex_name(sc, k).upper() for k in range(len(sc.mesh.textures))]
        skip = np.array([k >= 0 and names[k] in unlit for k in sc.mesh.tex])
    p = sc.mesh.pos.astype(np.float64)[~skip]
    d = sun_direction(vertical, horizontal)
    a, e1, e2 = p[:, 0], p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    pv = np.cross(d, e2)
    det = (e1 * pv).sum(1)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
    hit = np.zeros(len(pts), bool)
    for i in range(0, len(pts), 32):  # Moller-Trumbore, a few points at a time
        s = pts[i:i + 32, None, :] - a[None]
        u = (s * pv[None]).sum(2) * inv
        q = np.cross(s, e1[None])
        w = (q * d).sum(2) * inv
        t = (q * e2[None]).sum(2) * inv
        hit[i:i + 32] = (ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1.0)).any(1)
    return float(1 - hit.mean())


def clear_sun(sc, vertical, horizontal):
    """(height, share of the floor lit): the sun raised in SUN_STEP steps until SUN_CLEAR of the floor round the fight
    sees it, up to SUN_CLEAR_MAX (the highest with the most lit when none reaches it). A sun the walls hide leaves the
    painted floor at the image-based light alone (1 - share of its painted brightness) and its relief flat: oq's 35
    degrees did, 2026-11-07."""
    pts = floor_points(sc)
    best = (vertical, sunlit(sc, vertical, horizontal, pts))
    v = vertical
    while best[1] < SUN_CLEAR and v < SUN_CLEAR_MAX:
        v = min(SUN_CLEAR_MAX, v + SUN_STEP)
        lit = sunlit(sc, v, horizontal, pts)
        if lit > best[1] + 1e-6:
            best = (v, lit)
    return best


def unlit_textures():
    """The textures FFNx draws unlit (lighting/config.toml's top-level disable_lighting_textures: the stock list holds
    the sky domes), upper case: no normal maps, sway or haze on them."""
    try:
        cfg = tomllib.loads((LIGHTING / "config.toml").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(t).upper() for t in cfg.get("disable_lighting_textures", [])}


def _fields_settings():
    """The game-wide 3D settings the user chose for fields ([fields] in lighting/config.toml): battles follow its
    finish, haze strength and colour match."""
    try:
        cfg = tomllib.loads((LIGHTING / "config.toml").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg.get("fields", {})


def light(sc, E=None, cover=None):
    """The scene's light for the characters: ({config keys}, specular mips [(6, n, n, 3) stored 0..1], diffuse (6, 32, 32,
    3) stored 0..1, {info}). See the module's notes on the stage."""
    envmap = _ff3d("envmap")
    if E is None:
        E, cover = cube(sc, SPEC_N)
    fs = _fields_settings()
    mat = {k: float(fs.get(k, v)) for k, v in MATERIAL.items()}
    metal = min(mat["material_metallic"], 0.9)
    d32 = envmap.face_dirs(SRC_N)
    src = _down(E, SRC_N)
    D = envmap.convolve(src, d32, d32)  # per surface direction: its light (cosine-weighted radiance), per albedo
    up = np.array([[[[0.0, -1.0, 0.0]]]])
    D_up = envmap.convolve(src, d32, up)[0, 0, 0]
    lobe = envmap.convolve(src, d32, d32, power=SUN_LOBE)
    # the sun: the brightest part of the sky (blurred), and how much brighter than the sky around it is
    elev = np.degrees(np.arcsin(np.clip(-d32[..., 1], -1, 1)))
    L = _luma(lobe)
    i = int(np.argmax(np.where(elev >= SUN_SEARCH, L, -1.0)))
    peak_dir = d32.reshape(-1, 3)[i]
    v0, h0 = sun_angles(peak_dir)
    v = float(np.clip(v0, *SUN_ELEV))
    w = np.broadcast_to(envmap.solid_angles(SRC_N), d32.shape[:3])
    upper = d32[..., 1] < 0
    sky = float((_luma(src) * w)[upper].sum() / max(w[upper].sum(), 1e-9))
    ratio = float(L.reshape(-1)[i] / max(sky, 1e-6))
    share = SUN_SHARE[0] + (SUN_SHARE[1] - SUN_SHARE[0]) * float(np.clip((ratio - SUNNY[0]) / (SUNNY[1] - SUNNY[0]), 0, 1))
    if ratio < SUNNY[0]:  # overcast, no one bright place (oo: its brightest is the flat cap, straight up): the side
        band = (elev >= SUN_SEARCH) & (elev <= SUN_ELEV[0])  # the sky is brightest on, low down
        if band.any():
            j = int(np.argmax(np.where(band, L, -1.0)))
            h0 = sun_angles(d32.reshape(-1, 3)[j])[1]
            i = j
    v, floor_lit = clear_sun(sc, v, h0)
    # colours against the light from above: TINT of its cast kept
    c_up = D_up / max(float(_luma(D_up)), 1e-9)
    wb = np.clip(c_up, 1e-3, None) ** (TINT - 1)
    norm = float(_luma(D_up * wb))
    Dn = D * wb / max(norm, 1e-9)
    En = E * wb / max(norm, 1e-9)

    def squeeze(x, lo, hi):  # each direction's brightness against the light from above, to the power CONTRAST
        lx = np.maximum(_luma(x), 1e-6)
        return x * (np.clip(lx ** CONTRAST, lo, hi) / lx)[..., None]

    Dc = _cap_sat(squeeze(Dn, *FILL))
    Ec = _cap_sat(squeeze(En, 0.0, 4.0))
    sun_rgb = lobe.reshape(-1, 3)[i] * wb
    sun_rgb = _cap_sat(sun_rgb / max(float(_luma(sun_rgb)), 1e-9))
    sun_rgb = sun_rgb / max(float(_luma(sun_rgb)), 1e-9)
    # an up-facing surface: (1 - share) from the image-based light, share from the sun (n.l = sin of its height)
    ibl_light = FLOOR * (1 - share) * Dc
    amb = float(ibl_light.max()) / LEVEL / (1 - metal)
    stored_d = ibl_light / (amb * (1 - metal))
    stored_e = SPEC * (1 - share) * Ec / (amb * (1 - metal))
    src_e = _down(stored_e, SRC_N)
    mips, n, level = [], SPEC_N, 0
    while n >= 1:  # envmap.make's chain: blurred with a widening lobe (roughness grows with the level)
        mips.append(np.clip(stored_e if level == 0 else
                            envmap.convolve(src_e, d32, envmap.face_dirs(n), power=max(2.0, 256 / 4 ** level)), 0, 1))
        n //= 2
        level += 1
    intensity = FLOOR * share * math.pi / (math.sin(math.radians(v)) * (1 - metal))
    keys = {"light_rotation_vertical": round(v, 1), "light_rotation_horizontal": round(h0, 1),
            "light_intensity": round(intensity, 3),
            "light_color": [round(float(c), 4) for c in np.clip(sun_rgb, 0, None) ** (1 / 2.2)],
            "ambient_light_intensity": round(amb, 4), "ambient_light_color": [1.0, 1.0, 1.0],
            "char_ambient": round(amb * CHAR_FILL, 4),
            **{k: round(val, 3) for k, val in mat.items()}}
    lo_dir = _luma(Dc)
    info = {"centre": [round(float(x), 1) for x in centre(sc)], "covered": round(float(cover.mean()), 3) if cover is not None else None,
            "sun_found": [round(v0, 1), round(h0, 1)], "sun_ratio": round(ratio, 2), "sun_share": round(share, 3),
            "floor_sunlit": round(floor_lit, 2),
            "light_from_above": [round(float(x), 4) for x in D_up],
            "brightness": round(float(_luma(D_up)), 4),
            "tint": [round(float(x), 3) for x in c_up],
            "contrast_painted": [round(float(_luma(Dn).min()), 3), round(float(_luma(Dn).max()), 3)],
            "contrast_kept": [round(float(lo_dir.min()), 3), round(float(lo_dir.max()), 3)]}
    # what the floor gets in game (diffuse; the sun at its angles), per channel: FLOOR, tinted
    up_i = np.unravel_index(int(np.argmin(d32[..., 1])), d32.shape[:3])
    floor = (amb * stored_d[up_i] * (1 - metal) + intensity / math.pi * math.sin(math.radians(v)) * (1 - metal) * sun_rgb)
    info["floor_expected"] = [round(float(x), 3) for x in floor]
    return keys, mips, np.clip(stored_d, 0, 1), info


def write_ibl(path_s, path_d, mips, diffuse):
    """FFNx's image-based light cubes (envmap.write_cube: linear 8-bit, faces +X -X +Y -Y +Z -Z with mips)."""
    envmap = _ff3d("envmap")
    Path(path_s).parent.mkdir(parents=True, exist_ok=True)
    envmap.write_cube(path_d, [envmap.to_lin8(diffuse)])
    envmap.write_cube(path_s, [envmap.to_lin8(m) for m in mips])


def equirect(c, w=512, h=256):
    """A cube (6, n, n, C) as a picture all round (battle space: up is -y; across: angle round from -z)."""
    n = c.shape[1]
    lon = (np.arange(w) + 0.5) / w * 2 * np.pi - np.pi
    lat = np.pi / 2 - (np.arange(h) + 0.5) / h * np.pi
    lon, lat = np.meshgrid(lon, lat)
    d = np.stack([np.cos(lat) * np.sin(lon), -np.sin(lat), -np.cos(lat) * np.cos(lon)], -1)
    a = np.abs(d)
    face = np.where((a[..., 0] >= a[..., 1]) & (a[..., 0] >= a[..., 2]), np.where(d[..., 0] > 0, 0, 1),
                    np.where(a[..., 1] >= a[..., 2], np.where(d[..., 1] > 0, 2, 3), np.where(d[..., 2] > 0, 4, 5)))
    x, y, z = d[..., 0], d[..., 1], d[..., 2]
    m = np.maximum(a.max(-1), 1e-9)
    sc = np.select([face == 0, face == 1, face == 2, face == 3, face == 4], [-z, z, x, x, x], -x) / m
    tc = np.select([face == 0, face == 1, face == 2, face == 3, face == 4], [-y, -y, z, -z, -y], -y) / m
    col = np.clip(((sc + 1) / 2 * n).astype(int), 0, n - 1)
    row = np.clip(((tc + 1) / 2 * n).astype(int), 0, n - 1)
    return c[face, row, col]


# --- haze and grade ---------------------------------------------------------------------------------------------------

def _view(sc, images, width=640, height=480, cam=None):
    cam = cam or battle.cameras(sc.id, width, height)[0]
    r = render.rasterize(sc.mesh, cam)
    img, hit = _shade(sc.mesh, r, images)
    return cam, r, img, hit


def haze(sc, E=None, cover=None):
    """The characters' haze in this scene ({haze_color (sRGB 0..1), haze_density per battle unit, haze_start, haze_strength},
    info): the colour of the painted horizon all round; the density how fast the painting's local contrast falls with
    distance from the main battle camera (atmos.haze), bounded by the painting's darks across the fight
    (atmos.haze_dark_bound) and capped at HAZE_MAX at its far side; from the fight's near side."""
    if E is None:
        E, cover = cube(sc, 64)
    n = E.shape[1]
    envmap = _ff3d("envmap")
    d = envmap.face_dirs(n)
    el = np.degrees(np.arcsin(np.clip(-d[..., 1], -1, 1)))
    ring = (np.abs(el - 2.0) <= 6.0) & (cover > 0.5)
    colour = (E[ring].mean(0) if ring.any() else E.reshape(-1, 3).mean(0)) ** (1 / 2.2)
    cams = battle.cameras(sc.id, 640, 480)
    mid = centre(sc)
    dist = float(np.median([np.linalg.norm(np.asarray(c.eye) - mid) for c in cams])) if cams else 9000.0
    start, reach = max(dist - ACTION_R * 0.5, 1.0), dist + ACTION_R
    cam, r, img, hit = _view(sc, _small(sc.images, 1024), cam=cams[0] if cams else None)
    H, W = r.tri.shape
    fpx = cam.f_px()
    ys, xs = np.mgrid[0:H, 0:W] + 0.5
    D = r.depth * np.sqrt(1 + ((xs - W / 2) ** 2 + (ys - H / 2) ** 2) / fpx ** 2)
    ok = hit & np.isfinite(D)
    lin = _lin(img)
    lum = _luma(lin)
    mean = cv2.blur(lum, (9, 9))
    contrast = np.sqrt(np.maximum(cv2.blur(lum * lum, (9, 9)) - mean * mean, 0)) / np.maximum(mean, 0.02)
    density = 0.0
    if ok.sum() > 2000:
        edges = np.percentile(D[ok], np.linspace(5, 98, 12))
        pts = [(np.median(D[ok & (D >= a) & (D < b)]), np.median(contrast[ok & (D >= a) & (D < b)]))
               for a, b in zip(edges[:-1], edges[1:]) if (ok & (D >= a) & (D < b)).sum() > 200]
        if len(pts) >= 4:
            dd, cc = np.array(pts, np.float64).T
            density = max(0.0, -float(np.polyfit(dd, np.log(np.maximum(cc, 1e-3)), 1)[0]))
    bound = None
    if density > 0:
        A = np.maximum(_lin(colour * 255), 1e-3)
        dark = cv2.erode((lin / A).min(2), np.ones((3, 3), np.uint8))
        near = ok & (D > start) & (D <= reach * 1.15)
        bound = np.inf
        if near.sum() >= 1600:
            e = np.percentile(D[near], np.linspace(0, 100, 9))
            for a, b in zip(e[:-1], e[1:]):
                m = near & (D >= a) & (D < b)
                dm = float(np.median(D[m])) if m.sum() > 200 else start
                if dm <= start * 1.05:
                    continue
                lifted = min(float(np.percentile(dark[m], HAZE_DARK_PCT)), 0.95)
                bound = min(bound, -float(np.log1p(-lifted)) / (dm - start))
        density = min(density, bound)
        cap = HAZE_MAX * min(1.0, (reach - start) / start)
        density = min(density, -math.log(1 - cap) / (reach - start))
    strength = float(_fields_settings().get("haze_strength", HAZE_STRENGTH))
    keys = {"haze_color": [round(float(c), 3) for c in np.clip(colour, 0, 1)], "haze_density": round(float(density), 8),
            "haze_start": round(start, 1), "haze_strength": round(strength, 3), "haze_scene": HAZE_SCENE}
    return keys, {"camera_distance": round(dist, 1), "reach": round(reach, 1),
                  "dark_bound": None if bound is None or not np.isfinite(bound) else round(float(bound), 8)}


def grade_from(o, n, strength=1.0):
    """atmos.grade's bounded colour grade of new art `n` against the original `o` (sRGB 0..255, (k, 3)): 12 numbers,
    rows R, G, B of x, y, z, offset on sRGB 0..1, and what went into it."""
    keep = (o.sum(1) > 6) & (n.sum(1) > 6)
    o, n = o[keep].astype(np.float32), n[keep].astype(np.float32)
    if len(o) < 200:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0], {}
    Lo, Ln = o @ SRGB_LUMA, n @ SRGB_LUMA
    p = lambda x, q: float(np.percentile(x, q))
    contrast = float(np.clip((p(Ln, 90) - p(Ln, 10)) / max(p(Lo, 90) - p(Lo, 10), 1.0), 0.7, 1.3))
    brightness = float(np.clip(np.median(Ln) - contrast * np.median(Lo), -40, 40))
    chroma = lambda x: x.max(1) - x.min(1)
    sat = float(np.clip(np.median(chroma(n)) / max(float(np.median(chroma(o))), 1.0), 0.7, 1.4))
    neutral = (chroma(o) < 0.12 * np.maximum(o.max(1), 1)) & (Lo > 25) & (Lo < 235)
    if neutral.sum() < 200:
        neutral = np.ones(len(o), bool)
    balance = lambda x: x.mean(0) / max(float(x.mean(0) @ SRGB_LUMA), 1.0)
    g = np.clip(balance(n[neutral]) / balance(o[neutral]), 0.9, 1.1)
    g = g / float(g @ SRGB_LUMA)
    T = np.diag(g) @ (sat * np.eye(3) + (1 - sat) * np.outer(np.ones(3), SRGB_LUMA))
    lin, off = T * contrast, T @ np.full(3, brightness) / 255
    lin = (1 - strength) * np.eye(3) + strength * lin
    off = strength * off
    return ([round(float(v), 4) for row, b in zip(lin, off) for v in (*row, b)],
            {"contrast": round(contrast, 3), "brightness": round(brightness, 1), "saturation": round(sat, 3),
             "balance": [round(float(v), 3) for v in g]})


def grade(sc):
    """The characters' colour grade for this scene: the painting against the original from the battle cameras (each
    formation's resting view, up to four), as atmos.grade does for a field ({char_grade_full}, info). How much of it
    applies (char_match) and char_exposure are game-wide ([battles], FORMAT.md A; the engine's default: all of it)."""
    cams = battle.cameras(sc.id, 480, 360, min_angle=10.0)[:4]
    o, n = [], []
    small = _small(sc.images, 1024)
    orig = sc.originals()
    for cam in cams:
        r = render.rasterize(sc.mesh, cam)
        a, hit = _shade(sc.mesh, r, orig)
        b, _ = _shade(sc.mesh, r, small)
        o.append(a[hit])
        n.append(b[hit])
    g, info = grade_from(np.concatenate(o), np.concatenate(n)) if o else grade_from(np.zeros((0, 3)), np.zeros((0, 3)))
    return {"char_grade_full": g}, info


# --- life -------------------------------------------------------------------------------------------------------------

def _ask(items, tag):
    """Qwen3.5 (the Studio's describer, through ComfyUI) on [(RGB image, question)]: short answers, lower case."""
    comfy = _ff3d("comfy")
    from ..comfy import CAPTION_CLIP
    clip, clip_type = CAPTION_CLIP
    tmp = WORK / "_ask"
    tmp.mkdir(parents=True, exist_ok=True)
    wf = {"clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": clip_type}}}
    for i, (img, ask) in enumerate(items):
        p = tmp / f"{tag}_{i}.png"
        Image.fromarray(np.ascontiguousarray(img[..., :3])).save(p)
        wf[f"img_{i}"] = {"class_type": "LoadImage", "inputs": {"image": comfy.upload(p, f"battlelight_{tag}_{i}.png")}}
        wf[f"gen_{i}"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": ask, "max_length": 8, "sampling_mode": "off", "image": [f"img_{i}", 0],
            "thinking": False}}
        wf[f"show_{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"gen_{i}", 0]}}
    if not items:
        return []
    outs = comfy.run(wf)
    return ["".join(outs.get(f"show_{i}", {}).get("text", [])).strip().lower() for i in range(len(items))]


SKY_ASK = ("This is a texture from a JRPG battle arena's sky dome. Does it show only sky and clouds, with no mountains, "
           "hills, trees, buildings, walls or ground at all? Answer with one word, yes or no.")
GRASS_ASK = ("This is a ground texture from a JRPG battle arena. Is it mostly grass, a meadow, reeds or crops: plants "
             "that would sway in the wind? Answer with one word, yes or no.")
PARTICLE_ASK = ("This is a battle arena in a Japanese role-playing game. Which floating particles would suit it best: "
                "embers (fires, lava, burning places), snow "
                "(snowy or icy places outdoors), rain (rain falling outdoors), fireflies (forests, swamps or grass at "
                "night), or none (clear open air, clean rooms, water, space)? Answer with one word.")


def _uv_jacobians(mesh, sel):
    """Per triangle of `sel`: (dP/du, dP/dv as (n, 3, 2), area, centre, valid)."""
    P = mesh.pos[sel].astype(np.float64)
    UV = np.nan_to_num(mesh.uv[sel].astype(np.float64))
    e = np.stack([P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]], -1)          # (n, 3, 2)
    M = np.stack([UV[:, 1] - UV[:, 0], UV[:, 2] - UV[:, 0]], -1)       # (n, 2, 2): columns d1, d2
    det = M[:, 0, 0] * M[:, 1, 1] - M[:, 0, 1] * M[:, 1, 0]
    valid = np.abs(det) > 1e-7
    Minv = np.zeros_like(M)
    Minv[valid] = np.linalg.inv(M[valid])
    J = e @ Minv
    area = 0.5 * np.linalg.norm(np.cross(e[..., 0], e[..., 1]), axis=1)
    return J, area, P.mean(1), valid


def drift(sc, k, mid, wrap=(True, True)):
    """{texture, u, v}: texture k moved so its clouds go round the arena at DRIFT radians a second (its UV direction
    for that, the area-weighted median over its triangles; along u where the layout has no one direction), only along
    the ways it tiles in the scene (wrap: paint.wraps'; the engine moves the whole texture, so an edge that doesn't
    meet its other side would show); None when it tiles neither way."""
    if not any(wrap):
        return None
    J, area, cen, valid = _uv_jacobians(sc.mesh, sc.mesh.tex == k)
    rel = cen - mid
    rad = np.hypot(rel[:, 0], rel[:, 2])
    t = np.stack([rel[:, 2], np.zeros(len(rel)), -rel[:, 0]], -1) / np.maximum(rad, 1e-6)[:, None]
    JtJ = np.einsum("nki,nkj->nij", J, J)
    ok = valid & (np.abs(np.linalg.det(JtJ)) > 1e-12) & (rad > 1)
    rate = np.zeros((len(J), 2))
    rate[ok] = np.einsum("nij,nj->ni", np.linalg.inv(JtJ[ok]), np.einsum("nki,nk->ni", J[ok], t[ok] * (DRIFT * rad[ok])[:, None]))
    if not ok.any():
        return {"texture": tex_name(sc, k), "u": 0.0, "v": 0.0}
    w = area[ok]

    def wmedian(x):
        o = np.argsort(x)
        c = np.cumsum(w[o])
        return float(x[o][np.searchsorted(c, c[-1] / 2)])

    u, v = wmedian(rate[ok, 0]), wmedian(rate[ok, 1])
    typical = wmedian(np.hypot(rate[ok, 0], rate[ok, 1]))
    if math.hypot(u, v) < 0.3 * typical:  # no one direction (a cap mapped from above, mirrored halves): along u
        u, v = (typical, 0.0) if wrap[0] else (0.0, typical)
    if not wrap[0] or not wrap[1]:  # only along the way it tiles, as fast
        speed = math.hypot(u, v)
        u, v = (math.copysign(speed, u) if u else speed, 0.0) if wrap[0] else (0.0, math.copysign(speed, v) if v else speed)
    return {"texture": tex_name(sc, k), "u": round(u, 6), "v": round(v, 6)}


def sway_dir(sc, k):
    """[u, v]: the way the engine's gusts travel over the ground (WIND), in texture k's space (area-weighted median of
    its triangles; unit length), so the grass leans with them."""
    J, area, cen, valid = _uv_jacobians(sc.mesh, sc.mesh.tex == k)
    JtJ = np.einsum("nki,nkj->nij", J, J)
    ok = valid & (np.abs(np.linalg.det(JtJ)) > 1e-12)
    if not ok.any():
        return [1.0, 0.0]
    wind = np.asarray(WIND, float) / np.linalg.norm(WIND)
    d = np.einsum("nij,nj->ni", np.linalg.inv(JtJ[ok]), np.einsum("nki,k->ni", J[ok], wind))
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    w = area[ok]
    m = (d * w[:, None]).sum(0)
    if np.linalg.norm(m) < 0.3 * w.sum():  # mirrored tiles disagree: across the texture
        return [1.0, 0.0]
    m /= np.linalg.norm(m)
    return [round(float(m[0]), 3), round(float(m[1]), 3)]


def _grass_share(rgb):
    """The share of a texture that's grass by colour (surface.grass_mask's test: green, saturated, textured)."""
    rgb = _small([rgb], 512)[0][..., :3]
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    green = (np.clip((sat - 0.22) / 0.18, 0, 1) * np.clip((val - 0.1) / 0.12, 0, 1)
             * np.clip((hue - 60) / 8, 0, 1) * np.clip((175 - hue) / 12, 0, 1))
    L = rgb.astype(np.float32) @ LUMA / 255
    texture = np.sqrt(np.maximum(cv2.GaussianBlur(L * L, (0, 0), 3) - cv2.GaussianBlur(L, (0, 0), 3) ** 2, 0))
    mask = cv2.GaussianBlur(green * np.clip((texture - 0.015) / 0.03, 0, 1), (0, 0), 1.5)
    return float((mask > 0.5).mean())


def _kind_from_words(words):
    """Particles from the Studio's description of the scene (atmos._particle_kind's words), when Qwen can't be asked."""
    c = (words or "").lower()
    inside = re.search(r"\b(room|interior|inside|indoors?|hallway|corridor|cave|tunnel|underground|enclosed|factory|"
                       r"warehouse|reactor|basement)\b", c)
    if re.search(r"\b(burning|blaze|blazing|flames|inferno|bonfire|campfire|lava|magma|embers)\b", c):
        return "embers"
    if not inside and re.search(r"\b(snow|snowy|snowfall|blizzard|snow-covered|glacier|icy)\b", c):
        return "snow"
    if not inside and re.search(r"\b(rain|rainy|raining|rainfall|downpour)\b", c):
        return "rain"
    if re.search(r"\b(forest|jungle|woods|swamp|grove)\b", c) and re.search(r"\b(night|nighttime|moonlit)\b", c):
        return "fireflies"
    return "none"


def particles(sc, kind, amount=PARTICLE_AMOUNT):
    """The text of <external_mesh_path>/battle/STAGE<nn>_particles.toml for `kind` (atmos.PARTICLES' look, sized and
    moving as in a field where a background pixel is a PSX battle pixel at the cameras' distance), or None for none.
    Battle coordinates (x, y down, z): the box and floor points as the scene has them; lift is up from the floor."""
    if kind not in PARTICLE_KINDS or amount <= 0:
        return None
    atmos = _ff3d("atmos")
    p = atmos.PARTICLES[kind]
    cams = battle.cameras(sc.id)
    mid = centre(sc)
    floor = mid[1] + CHAR_H / 2
    dist = float(np.median([np.linalg.norm(np.asarray(c.eye) - np.asarray(c.target)) for c in cams])) if cams else 9000.0
    ppu = battle.BATTLE_H / dist  # PSX battle pixels per battle unit at the fight
    H = p["height"] / ppu
    lines = [f"# FFNx-3d battle particles for {sc.name} (battlelight, {sc.id} {sc.version}): battle coordinates x, y down, "
             "z; lift is up from the floor (-y)",
             f'kind = "{kind}"']
    if kind in atmos.FLOOR_KINDS:
        R = ACTION_R * 1.3
        rng = np.random.default_rng(0)
        a = rng.random(FLOOR_POINTS) * 2 * np.pi
        rr = R * np.sqrt(rng.random(FLOOR_POINTS))
        xs, zs = mid[0] + rr * np.sin(a), mid[2] + rr * np.cos(a)
        ys = np.array([_floor_y(sc.mesh, x, z) for x, z in zip(xs, zs)])
        pts = np.stack([xs, ys, zs], -1)
        box = [mid[0] - R, floor - H, mid[2] - R, mid[0] + R, floor, mid[2] + R]
        count = p["per_screen"] * amount
        extra = [f"floor = [{', '.join(f'{v:.1f}' for v in pts.reshape(-1))}]",
                 f"lift = [{p['lift'][0] * H:.1f}, {p['lift'][1] * H:.1f}]",
                 f"spread = {atmos.FLOOR_SPREAD / ppu:.1f}"]
    else:  # falling all round what the cameras see as they swing
        R = dist * 1.2
        box = [mid[0] - R, floor - max(H, 2 * CHAR_H) * 3, mid[2] - R, mid[0] + R, floor, mid[2] + R]
        count = p["per_screen"] * amount * 1.5
        extra = []
    lines += [f"count = {int(np.clip(round(count), 8, 4000))}",
              f"size = {p['size'] / ppu:.2f}",
              f"alpha = {p['alpha']}",
              f"speed = {float(np.clip(atmos.PARTICLE_PX / ppu, 0.05, 20)):.3f}",
              f"color = [{', '.join(str(c) for c in p['color'])}]",
              f"box = [{', '.join(f'{float(v):.1f}' for v in box)}]",
              f"near = {PARTICLE_NEAR * dist:.1f}"] + extra
    return "\n".join(lines) + "\n"


def life(sc, ai=True):
    """({uv_scroll, sway} config keys, particle kind, particle file text or None, info). ai: ask Qwen (ComfyUI) whether
    far textures that dip to the horizon are only sky, whether grassy-looking ground is grass, and which particles
    suit the scene; without it, only sure sky drifts, grass goes by colour, particles by the Studio's description."""
    mesh = sc.mesh
    cls = scenepaint.classify(mesh)
    cams = battle.cameras(sc.id)
    tgt = np.mean([c.target for c in cams], 0) if cams else np.zeros(3)
    eye_y = float(np.median([c.eye[1] for c in cams])) if cams else -1500.0
    mid = np.array([tgt[0], eye_y, tgt[2]])  # where the cameras stand: the sky's height as they see it
    info = {"far": [], "ground": []}
    sky, ask_sky = [], []
    for k, c in enumerate(cls):
        if not c["far"] or not sc.painted[k]:
            continue
        p = mesh.pos[mesh.tex == k].reshape(-1, 3).astype(np.float64)
        el = np.degrees(np.arctan2(mid[1] - p[:, 1], np.hypot(p[:, 0] - mid[0], p[:, 2] - mid[2])))
        e10 = float(np.percentile(el, 10))
        info["far"].append({"texture": tex_name(sc, k), "elevation_p10": round(e10, 1)})
        if e10 >= SKY_ELEV:
            sky.append(k)
        else:
            ask_sky.append(k)
    grass, ask_grass = {}, []
    for k, c in enumerate(cls):
        if not c["ground"] or not sc.painted[k]:
            continue
        share = _grass_share(sc.images[k])
        info["ground"].append({"texture": tex_name(sc, k), "grass": round(share, 3)})
        if ai:  # Qwen decides (the colour test misses olive and yellow grass: oo's marsh is 0%), the colour how much
            grass[k] = max(share, 0.3)
            ask_grass.append(k)
        elif share >= GRASS_SHARE:
            grass[k] = share
    view = None
    kind = _kind_from_words(sc.meta.get("words") or sc.meta.get("scene"))
    if ai:
        if cams:
            cam = battle.cameras(sc.id, 1024, 768)[0]
            view = np.clip(_view(sc, _small(sc.images, 2048), cam=cam)[2], 0, 255).astype(np.uint8)
        items = [(_small([sc.images[k]], 768)[0], SKY_ASK) for k in ask_sky]
        items += [(_small([sc.images[k]], 768)[0], GRASS_ASK) for k in ask_grass]
        items += [(view, PARTICLE_ASK)] if view is not None else []
        said = _ask(items, sc.id)
        for k, a in zip(ask_sky, said[:len(ask_sky)]):
            info.setdefault("asked", {})[f"sky {tex_name(sc, k)}"] = a
            if a.startswith("yes"):
                sky.append(k)
        for k, a in zip(ask_grass, said[len(ask_sky):len(ask_sky) + len(ask_grass)]):
            info.setdefault("asked", {})[f"grass {tex_name(sc, k)}"] = a
            if not a.startswith("yes"):
                grass.pop(k, None)
        if view is not None:
            a = said[-1]
            info.setdefault("asked", {})["particles"] = a
            word = re.findall(r"[a-z]+", a)
            kind = word[0] if word and word[0] in (*PARTICLE_KINDS, "none") else kind
    keys = {}
    wr = paint.wraps(mesh)
    scroll = [drift(sc, k, mid, wr[k]) for k in sorted(sky)]
    info["no_drift"] = [tex_name(sc, k) for k, s in zip(sorted(sky), scroll) if s is None]
    scroll = [s for s in scroll if s and abs(s["u"]) + abs(s["v"]) > 0]
    if scroll:
        keys["uv_scroll"] = scroll
    unlit = unlit_textures()
    grass = {k: s for k, s in grass.items() if tex_name(sc, k).upper() not in unlit}  # the engine sways lit draws only
    if grass:
        keys["sway"] = [{"texture": tex_name(sc, k), "amount": round(float(min(1.0, SWAY * s / 0.6)), 2), "speed": 1.0,
                         "dir": sway_dir(sc, k)} for k, s in sorted(grass.items())]
    info["particles"] = kind
    return keys, kind, particles(sc, kind), info, view


# --- normal maps ------------------------------------------------------------------------------------------------------

def encode_normals(t):
    """Tangent-space normals (h, w, 3: x along u, y along v (down the texture), z out) as RGBA bytes FFNx's
    perturb_normal turns back into them (simplify.encode's undoing of its sqrt(1 - x^2 + y^2))."""
    t = t / np.maximum(np.linalg.norm(t, axis=-1, keepdims=True), 1e-9)
    t[..., 2] = np.maximum(t[..., 2], 1e-3)
    k = 1 / np.sqrt(np.maximum(t[..., 2] ** 2 + t[..., 0] ** 2 - t[..., 1] ** 2, 1e-6))
    xy = np.clip(t[..., :2] * k[..., None], -1, 1)
    out = np.full(t.shape[:2] + (4,), 255, np.uint8)  # B and A unused (FORMAT.md C: 255)
    out[..., :2] = np.round(128 + 127 * xy)
    return out


def decode_normals(rgba):
    """encode_normals' inverse (as the shader reads them)."""
    xy = (rgba[..., :2].astype(np.float32) - 128) / 127  # the shader's byte/255 * 255/127 - 128/127
    z = np.sqrt(np.clip(1 - xy[..., 0] ** 2 + xy[..., 1] ** 2, 0, None))
    t = np.dstack([xy, z])
    return t / np.maximum(np.linalg.norm(t, axis=-1, keepdims=True), 1e-9)


def _marigold(img, tag):
    """Marigold v2's normals of an RGB picture (x right, y up, z to the viewer), at its size."""
    comfy, mg = _ff3d("comfy"), _ff3d("marigold")
    tmp = WORK / "_marigold"
    tmp.mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    pw, ph = -(-w // 16) * 16, -(-h // 16) * 16
    a = np.pad(img, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    p = tmp / f"{tag}.png"
    Image.fromarray(np.ascontiguousarray(a)).save(p)
    out = comfy.run(mg.workflow(comfy.upload(p, f"battlelight_{tag}.png"), "normals", f"ff7_3d/battlelight_{tag}"))
    res = np.asarray(Image.open(__import__("io").BytesIO(comfy.fetch(comfy.files(out, "save")[0]))).convert("RGB"))
    p.unlink(missing_ok=True)
    return res[:h, :w].astype(np.float32) / 127.5 - 1


def _fold(a, P, H, W):
    """An (H + 2P, W + 2P, ...) sum with its borders wrapped back onto the (H, W) middle (a tiling texture's)."""
    a = a.copy()
    a[P:2 * P] += a[H + P:H + 2 * P]
    a[H:H + P] += a[:P]
    a = a[P:P + H]
    a[:, P:2 * P] += a[:, W + P:W + 2 * P]
    a[:, W:W + P] += a[:, :P]
    return a[:, P:P + W]


def _wrap_blur(a, sigma):
    m = int(math.ceil(3 * sigma))
    my, mx = min(m, a.shape[0] - 1), min(m, a.shape[1] - 1)
    p = np.pad(a, ((my, my), (mx, mx)) + ((0, 0),) * (a.ndim - 2), mode="wrap")
    return cv2.GaussianBlur(p, (0, 0), sigma)[my:my + a.shape[0], mx:mx + a.shape[1]]


def marigold_tiled(img, tag):
    """Marigold's normals (texture space: x along u, y down the sheet, z out) of a tiling sheet (RGB uint8), in tiles
    of at most MG_TILE wrap-padded by MG_PAD and blended over the overlaps: a whole 1536 px sheet came back flat
    (oo's ground, 2026-11-07; tilt under 1 degree), the model sees relief at a closer look."""
    H, W = img.shape[:2]
    P = int(min(MG_PAD, H // 2, W // 2))
    ny, nx = -(-H // MG_TILE), -(-W // MG_TILE)
    th, tw = -(-H // ny), -(-W // nx)
    padded = np.pad(img, ((P, P), (P, P), (0, 0)), mode="wrap")
    acc = np.zeros((H + 2 * P, W + 2 * P, 3), np.float32)
    wsum = np.zeros((H + 2 * P, W + 2 * P), np.float32)
    for iy in range(ny):
        for ix in range(nx):
            y0, x0 = iy * th, ix * tw
            y1, x1 = min(H, y0 + th), min(W, x0 + tw)
            crop = np.ascontiguousarray(padded[y0:y1 + 2 * P, x0:x1 + 2 * P])
            n = _marigold(crop, f"{tag}_{iy}{ix}")
            t = np.dstack([n[..., 0], -n[..., 1], n[..., 2]]).astype(np.float32)
            ch, cw = crop.shape[:2]
            # each tile's own idea of the sheet's slant out before they meet (oo's ground: a different flat tilt per
            # tile, blocks with edges)
            low = cv2.GaussianBlur(t, (0, 0), min(RELIEF * max(H, W) / 2, max(ch, cw) / 6))
            t = t - low + np.array([0.0, 0.0, 1.0], np.float32) * np.linalg.norm(low, axis=-1, keepdims=True)
            ry = np.minimum(np.arange(ch) + 0.5, ch - np.arange(ch) - 0.5) / max(P, 1)
            rx = np.minimum(np.arange(cw) + 0.5, cw - np.arange(cw) - 0.5) / max(P, 1)
            wgt = np.outer(np.clip(ry, 0.02, 1), np.clip(rx, 0.02, 1)).astype(np.float32)
            acc[y0:y1 + 2 * P, x0:x1 + 2 * P] += t * wgt[..., None]
            wsum[y0:y1 + 2 * P, x0:x1 + 2 * P] += wgt
    t = _fold(acc, P, H, W) / np.maximum(_fold(wsum, P, H, W), 1e-6)[..., None]
    return t / np.maximum(np.linalg.norm(t, axis=-1, keepdims=True), 1e-9)


def normal_map(rgb, tag, raw=None):
    """A painted sheet's normal map (RGBA, encode_normals) at half its size (NORMAL_MAX at most): Marigold's normals
    (marigold_tiled; only relief finer than RELIEF of the sheet: it reads a flat sheet as a slanted floor) plus the
    painting's own fine texture as height (surface.write_normals' bump, BUMP), wrapping round as the tiles do.
    raw: a folder to keep Marigold's own answer in (a picture), for a look."""
    h, w = rgb.shape[:2]
    ow = min(NORMAL_MAX, max(64, w // 2)) if w >= h else round(min(NORMAL_MAX, max(64, h // 2)) * w / h)
    oh = max(1, round(ow * h / w))
    img = cv2.resize(np.ascontiguousarray(rgb[..., :3]), (ow, oh), interpolation=cv2.INTER_AREA)
    t = marigold_tiled(img, tag)
    if raw is not None:
        Path(raw).mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.round(np.clip(t * 0.5 + 0.5, 0, 1) * 255).astype(np.uint8)).save(Path(raw) / f"{tag}.png")
    low = _wrap_blur(t, RELIEF * max(oh, ow) / 2)
    t = MG_WEIGHT * (t - low) + np.array([0.0, 0.0, 1.0], np.float32)
    L = img.astype(np.float32) @ LUMA / 255
    height = _wrap_blur(L - _wrap_blur(L, BUMP_SCALE), 0.7)
    gx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) / 2
    gy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) / 2
    t[..., 0] -= BUMP * gx
    t[..., 1] -= BUMP * gy
    t /= np.maximum(np.linalg.norm(t, axis=-1, keepdims=True), 1e-9)
    return encode_normals(t)


NML = "_nml.png"  # FFNx's own name for a texture's normal map: <NAME>_<pal>_nml.png beside it (FORMAT.md C)


def normals(sc):
    """{texture name ("STAGE08_T00_00"): RGBA normal map} for the painted textures that are lit: not the sky
    (scenepaint.classify's far ones) nor any in disable_lighting_textures. Marigold's own answers are kept in the
    build's marigold/ folder."""
    cls = scenepaint.classify(sc.mesh)
    unlit = unlit_textures()
    out = {}
    for k, t in enumerate(sc.mesh.textures):
        if not sc.painted[k] or cls[k]["far"] or tex_name(sc, k).upper() in unlit:
            continue
        out[t.name.split("/", 1)[1]] = normal_map(sc.images[k], f"{sc.id}_{tex_name(sc, k)}", raw=sc.out / "marigold")
    return out


# --- build ------------------------------------------------------------------------------------------------------------

def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def built(id, version=None):
    """The build's settings.json for a version (the current one by default), or None."""
    version = version or current(id)
    return _read_json(WORK / id.lower() / str(version) / "settings.json") if version else None


def build(id, version=None, parts=PARTS, ai=True, allow_game=False, progress=print, step=None):
    """Work out a painted version's battle settings into work/models/battlelight/<id>/<version>/: settings.json (the
    config keys, what files go where, info), ibl/, textures/battle/<NAME>_00_nml.png, <STAGE>_particles.toml and preview
    pictures. parts: any of PARTS (the rest keep what an earlier build made). ai: Qwen and Marigold through ComfyUI
    (normals need it); refused while the game runs (allow_game to override). step: called with each part as it starts
    ("view" for the view all round, then the part's name); an InterruptedError from it stops the build there, the parts
    done so far kept in settings.json (the Studio's progress and Cancel)."""
    sc = load(id, version)
    out = sc.out
    out.mkdir(parents=True, exist_ok=True)
    parts = [p for p in PARTS if p in parts]
    if ai and any(p in AI_PARTS for p in parts) and not allow_game and game_running():
        raise RuntimeError("the game is running: close it before ComfyUI jobs (or build with ai=False)")
    prev = _read_json(out / "settings.json", {}) or {}
    values = dict(prev.get("values", {}))
    info = dict(prev.get("info", {}))
    files = dict(prev.get("files", {}))
    say = progress or (lambda *a: None)
    done = []

    def save(made):
        settings = {"id": sc.id, "version": sc.version, "name": sc.name, "location": sc.location,
                    "section": sc.section, "formations": battle.catalogue().get(sc.id, {}).get("formations", []),
                    "values": values, "files": files, "info": info, "built": time.time(),
                    "parts": sorted(set(prev.get("parts", [])) | set(made)),
                    "source": {sc.mesh.textures[k].name: (sc.dir / "textures" / f"{sc.mesh.textures[k].name}.png").stat().st_mtime
                               for k in range(len(sc.mesh.textures)) if sc.painted[k]}}
        (out / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        return settings

    def starting(part):
        if step is None:
            return
        try:
            step(part)
        except InterruptedError:
            if done:
                save(done)
            raise

    E = cover = None
    if "light" in parts or "haze" in parts:
        starting("view")
        say(f"{sc.id}: the view all round from the fight")
        E, cover = cube(sc, SPEC_N)
    if "light" in parts:
        starting("light")
        keys, mips, diffuse, inf = light(sc, E, cover)
        for k in [k for k in values if k.startswith(("light_", "ambient_", "material_", "char_ambient"))]:
            values.pop(k)
        values.update(keys)
        info["light"] = inf
        s, d = out / "ibl" / f"{sc.section}_s.dds", out / "ibl" / f"{sc.section}_d.dds"
        write_ibl(s, d, mips, diffuse)
        files["ibl"] = [s.relative_to(out).as_posix(), d.relative_to(out).as_posix()]
        pano = np.clip(np.concatenate([equirect(E), equirect(diffuse / LEVEL)], 0), 0, 1) ** (1 / 2.2)
        Image.fromarray(np.round(pano * 255).astype(np.uint8)).save(out / "preview_light.png")
        say(f"{sc.id}: sun {keys['light_rotation_vertical']} up, {keys['light_rotation_horizontal']} round, "
            f"intensity {keys['light_intensity']}, ambient {keys['ambient_light_intensity']}")
        done.append("light")
    if "haze" in parts:
        starting("haze")
        keys, inf = haze(sc, _down(E, 64), _down(cover[..., None], 64)[..., 0])
        values.update(keys)
        info["haze"] = inf
        done.append("haze")
    if "grade" in parts:
        starting("grade")
        say(f"{sc.id}: the characters' grade")
        keys, inf = grade(sc)
        for k in ("char_match", "char_exposure"):  # game-wide ([battles]), written per scene by builds before FORMAT.md
            values.pop(k, None)
        values.update(keys)
        info["grade"] = inf
        done.append("grade")
    if "life" in parts:
        starting("life")
        say(f"{sc.id}: life (sky, grass, particles){' with Qwen' if ai else ''}")
        keys, kind, text, inf, view = life(sc, ai=ai)
        values.pop("uv_scroll", None)
        values.pop("sway", None)
        values.update(keys)
        info["life"] = inf
        pfile = out / f"{sc.name}_particles.toml"
        if text:
            tomllib.loads(text)
            pfile.write_text(text, encoding="utf-8")
            files["particles"] = pfile.name
        else:
            pfile.unlink(missing_ok=True)
            files.pop("particles", None)
        if view is not None:
            Image.fromarray(view).save(out / "preview_view.jpg", quality=90)
        done.append("life")
    if "normals" in parts:
        starting("normals")
        if ai:
            say(f"{sc.id}: normal maps (Marigold)")
            maps = normals(sc)
            ndir = out / "textures" / "battle"
            if ndir.exists():
                shutil.rmtree(ndir)
            ndir.mkdir(parents=True)
            for name, rgba in maps.items():
                Image.fromarray(rgba).save(ndir / f"{name}{NML}")
            files["normals"] = sorted(f"textures/battle/{name}{NML}" for name in maps)
            values["normal_strength"] = NORMAL_STRENGTH if maps else 0.0
            values["normal_edge"] = NORMAL_EDGE
        else:
            say(f"{sc.id}: normal maps need Marigold (ai): skipped")
    return save(parts)


# --- the game's files -------------------------------------------------------------------------------------------------

def _ffnx_setting(key, default):
    try:
        text = (GAME / "FFNx.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1) if m else default


def texture_dirs():
    """Where FFNx looks for texture overrides, the one it tries first first: [override_mod_path, mod_path]."""
    out = []
    o = _ffnx_setting("override_mod_path", "")
    if o:
        out.append(GAME / o)
    out.append(GAME / _ffnx_setting("mod_path", "mods/textures"))
    return out


def texture_installed(name):
    """The folder a painted texture ("STAGE08_T00_00") is installed in for the game (as FFNx looks), or None."""
    for d in texture_dirs():
        if (d / "battle" / f"{name}.png").exists():
            return d / "battle"
    return None


def mesh_dir():
    return GAME / _ffnx_setting("external_mesh_path", "mesh")


def toml_value(v):
    """A value as TOML text (floats, ints, strings, lists, inline tables)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(f"not a finite number: {v}")
        return repr(round(f, 8))
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple, np.ndarray)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(f"can't write {type(v)} to TOML")


def _section_re(table):
    # ff3d.ibl.write_section's: the header and every line up to the next one starting with "["
    return re.compile(rf"^\[{re.escape(table)}\][^\r\n]*(?:\r?\n(?!\[)[^\r\n]*)*\r?\n?", re.M)


def config_sections():
    """The table names in lighting/config.toml."""
    text = (LIGHTING / "config.toml").read_bytes().decode("utf-8")
    return set(re.findall(r"^\[([^\]\r\n]+)\]", text, re.M))


def edit_config(sections=None, remove=()):
    """Put these {table: {key: value}} in lighting/config.toml in place of what's there, and take the `remove` tables out,
    in one write under ff3d.ibl.config_lock() (every writer of it takes that lock); never writes a file that doesn't
    parse."""
    ibl = _ff3d("ibl")
    cfg = LIGHTING / "config.toml"
    with ibl.config_lock():
        text = cfg.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        new = text
        for table in remove:
            new = _section_re(table).sub("", new)
        for table, values in (sections or {}).items():
            body = nl.join([f"[{table}]"] + [f"{k} = {toml_value(v)}" for k, v in values.items()])
            pat = _section_re(table)
            if pat.search(new):
                new = pat.sub(lambda m: body + nl + nl, new, count=1)
            else:
                new = new.rstrip() + nl + nl + body + nl
        if new != text:
            tomllib.loads(new)
            cfg.write_bytes(new.encode("utf-8"))


def _hash(p):
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


def installed():
    return _read_json(MANIFEST, {}) or {}


def _save_manifest(data):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def install(id, version=None, formations=None, parts=None):
    """Put a built version in the game: ibl/<section>_s/_d.dds, the [battle_STAGE<nn>] section, the normal maps beside
    each installed painted texture (as FFNx looks for them: the override folder first), the particles file. formations:
    True (or PER_FORMATION when None) also writes [bat_<f>] and ibl/bat_<f>_*.dds for every formation fought there (what
    FFNx reads today); a list: those. parts: the settings' keys to take (default all). Replaces this scene's earlier
    install; refuses (writing nothing) where a file or section it would write is someone else's. Returns the manifest
    entry."""
    id = id.lower()
    version = version or current(id)
    st = built(id, version)
    if not st:
        raise FileNotFoundError(f"{id} {version} isn't built (build() first)")
    out = WORK / id / version
    formations = PER_FORMATION if formations is None else formations
    forms = st["formations"] if formations is True else list(formations or [])
    values = dict(st["values"])
    if parts is not None:
        keep = {"light": ("light_", "ambient_", "material_", "char_ambient"), "haze": ("haze_",),
                "grade": ("char_grade",), "life": ("uv_scroll", "sway"), "normals": ("normal_",)}
        prefixes = tuple(p for part in parts for p in keep.get(part, ()))
        values = {k: v for k, v in values.items() if k.startswith(prefixes)}
    copies = []  # (source, target)
    sec = st["section"]
    ibls = [out / f for f in st["files"].get("ibl", [])]
    if ibls and (parts is None or "light" in parts):
        for src in ibls:
            copies.append((src, IBL_DIR / src.name))
            for f in forms:
                copies.append((src, IBL_DIR / src.name.replace(sec, f"bat_{f}")))
    skipped = []
    if parts is None or "normals" in parts:
        for rel in st["files"].get("normals", []):
            src = out / rel
            tex = src.name[:-len(NML)]
            dest = texture_installed(tex)  # FFNx looks for it only beside the colour it loaded
            if dest is None:
                skipped.append(src.name)
            else:
                copies.append((src, dest / src.name))
    if st["files"].get("particles") and (parts is None or "life" in parts):
        src = out / st["files"]["particles"]
        try:  # a build from before a kind was dropped (dust, 2026-11-09) keeps its file: not put in
            offered = tomllib.loads(src.read_text(encoding="utf-8")).get("kind") in PARTICLE_KINDS
        except (OSError, ValueError):
            offered = False
        if offered:
            copies.append((src, mesh_dir() / "battle" / src.name))
    tables = {sec: values, **{f"bat_{f}": values for f in forms}}
    with _lock:
        data = installed()
        if id in data:
            uninstall(id)
            data = installed()
        ours = {f["path"].lower() for e in data.values() for f in e["files"]}
        our_tables = {t for e in data.values() for t in e["sections"]}
        clash = [str(dst) for _, dst in copies if dst.exists() and str(dst).lower() not in ours]
        have = config_sections()
        clash += [f"[{t}]" for t in tables if t in have and t not in our_tables]
        if clash:
            raise FileExistsError("not ours, left alone: " + ", ".join(clash))
        files, made = [], []
        try:
            for src, dst in copies:
                if not dst.parent.exists():  # a folder of ours (mesh/battle): taken away again when empty
                    for d in reversed([dst.parent, *dst.parent.parents]):
                        if not d.exists():
                            d.mkdir()
                            made.append(str(d))
                shutil.copy2(src, dst)
                files.append({"path": str(dst), "sha1": _hash(dst)})
            edit_config(tables)
        except Exception:
            for f in files:
                Path(f["path"]).unlink(missing_ok=True)
            for d in reversed(made):
                try:
                    Path(d).rmdir()
                except OSError:
                    pass
            raise
        entry = {"version": version, "at": time.time(), "name": st["name"], "files": files, "folders": made,
                 "sections": sorted(tables), "formations": forms, "normals_skipped": skipped,
                 "parts": [p for p in PARTS if p in st.get("parts", PARTS) and (parts is None or p in parts)]}
        data[id] = entry
        _save_manifest(data)
        return entry


def uninstall(id):
    """Take a scene's install out of the game: exactly the files it wrote (left where something else has changed them
    since) and its sections. Returns (files removed, files left, sections removed)."""
    id = id.lower()
    with _lock:
        data = installed()
        entry = data.get(id)
        if not entry:
            return 0, [], []
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
        for d in reversed(entry.get("folders", [])):  # the folders install made, if nothing else is in them
            try:
                Path(d).rmdir()
            except OSError:  # another scene's files are in it: that one takes it away later
                heir = next((e for k, e in data.items() if k != id and any(
                    Path(f["path"]).is_relative_to(d) for f in e["files"])), None)
                if heir is not None and d not in heir.setdefault("folders", []):
                    heir["folders"].insert(0, d)
        have = config_sections()
        tables = [t for t in entry["sections"] if t in have]
        if tables:
            edit_config(remove=tables)
        data.pop(id)
        _save_manifest(data)
        return removed, left, tables


# --- checks -----------------------------------------------------------------------------------------------------------

def _dds_info(p):
    b = Path(p).read_bytes()[:128]
    if len(b) < 128 or b[:4] != b"DDS ":
        return None
    h, w, mips = int.from_bytes(b[12:16], "little"), int.from_bytes(b[16:20], "little"), int.from_bytes(b[28:32], "little")
    caps2 = int.from_bytes(b[112:116], "little")
    return {"w": w, "h": h, "mips": mips, "cube": bool(caps2 & 0x200)}


def check(id=None, version=None):
    """Problems (a list of strings, empty when all is well) with a scene's build and install, or with every install:
    the files, the cubes, config.toml parsing and holding the sections, the normal maps against their textures, the
    particles file."""
    problems = []
    try:
        cfg = tomllib.loads((LIGHTING / "config.toml").read_bytes().decode("utf-8"))
    except (OSError, ValueError) as e:
        return [f"lighting/config.toml doesn't parse: {e}"]
    data = installed()
    ids = [id.lower()] if id else sorted(set(data) | {p.name for p in WORK.iterdir() if p.is_dir() and not p.name.startswith("_")}
                                         if WORK.exists() else set(data))
    for sid in ids:
        e = data.get(sid)
        ver = version or (e["version"] if e else current(sid))
        st = built(sid, ver)
        if st is None:
            if e:
                problems.append(f"{sid}: installed {e['version']} has no build")
            continue
        out = WORK / sid / ver
        for rel in st["files"].get("ibl", []):
            i = _dds_info(out / rel)
            if not i or not i["cube"] or i["w"] != i["h"]:
                problems.append(f"{sid}: {rel} isn't a cube DDS")
        for rel in st["files"].get("normals", []):
            p = out / rel
            tex = load_texture_size(sid, ver, p.name[:-len(NML)])
            if not p.exists():
                problems.append(f"{sid}: {rel} missing")
            elif tex:
                w, h = Image.open(p).size
                if abs(w / h - tex[0] / tex[1]) > 0.02:
                    problems.append(f"{sid}: {rel} is {w}x{h}, its texture {tex[0]}x{tex[1]}")
        if st["files"].get("particles"):
            try:
                pt = tomllib.loads((out / st["files"]["particles"]).read_text(encoding="utf-8"))
                if pt.get("kind") not in PARTICLE_KINDS or len(pt.get("box", [])) != 6:
                    problems.append(f"{sid}: particles file has kind {pt.get('kind')}, box {pt.get('box')}")
            except (OSError, ValueError) as ex:
                problems.append(f"{sid}: particles file: {ex}")
        for k, v in st["values"].items():
            try:
                tomllib.loads(f"{k} = {toml_value(v)}")
            except (ValueError, TypeError) as ex:
                problems.append(f"{sid}: {k} doesn't make TOML: {ex}")
        if e:
            for f in e["files"]:
                p = Path(f["path"])
                if not p.exists():
                    problems.append(f"{sid}: installed file gone: {p}")
                elif _hash(p) != f.get("sha1"):
                    problems.append(f"{sid}: installed file changed since: {p}")
                if p.name.endswith(NML) and not (p.parent / (p.name[:-len(NML)] + ".png")).exists():
                    problems.append(f"{sid}: {p.name} is beside no installed painted texture")
            for t in e["sections"]:
                if t not in cfg:
                    problems.append(f"{sid}: [{t}] missing from config.toml")
                elif any(cfg[t].get(k) is None for k in st["values"] if k in ("light_intensity", "ambient_light_intensity")):
                    problems.append(f"{sid}: [{t}] lacks the light")
    return problems


def load_texture_size(id, version, name):
    p = SCENES / id / version / "textures" / "battle" / f"{name}.png"
    try:
        return Image.open(p).size
    except OSError:
        return None


def status():
    """What's built and installed, per scene."""
    data = installed()
    out = {}
    if WORK.exists():
        for d in sorted(p for p in WORK.iterdir() if p.is_dir() and not p.name.startswith("_")):
            vers = sorted(v.name for v in d.iterdir() if (v / "settings.json").exists())
            if vers:
                out[d.name] = {"built": vers, "current": current(d.name)}
    for sid, e in data.items():
        out.setdefault(sid, {})["installed"] = {"version": e["version"], "files": len(e["files"]),
                                               "sections": e["sections"]}
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.battlelight")
    ap.add_argument("cmd", choices=("build", "install", "uninstall", "check", "status"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--version")
    ap.add_argument("--parts", default=",".join(PARTS))
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--allow-game", action="store_true")
    ap.add_argument("--formations", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        st = build(a.id, a.version, a.parts.split(","), ai=not a.no_ai, allow_game=a.allow_game)
        print(json.dumps({"values": st["values"], "files": st["files"]}, indent=1))
    elif a.cmd == "install":
        print(json.dumps(install(a.id, a.version, formations=a.formations or None), indent=1))
    elif a.cmd == "uninstall":
        print(uninstall(a.id))
    elif a.cmd == "check":
        p = check(a.id, a.version)
        print("\n".join(p) if p else "ok")
        return 1 if p else 0
    else:
        print(json.dumps(status(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
