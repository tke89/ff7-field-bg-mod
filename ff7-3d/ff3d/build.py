"""Build a field's 3D scene for FFNx-3d from its Studio render:

  depth (DA3 fitted to the walkmesh) -> walkable ground snapped onto the walkmesh -> geometry pushed back under the
  field's animated glow layers -> triangles, with depth-break cracks filled -> de-lit colour (Marigold) with its
  colour restored and contact shadows baked in -> mesh/field/<field>/scene.gltf (+ .bin, .dds)

plus the field's 2D pages hidden (mods/3dtest), its animated glow layers as FFNx animated textures, image-based
light, and a lighting section. Options: DEFAULTS, the set every field follows (work/options_all.json) and a field's
own changes (work/<field>/options.json).
"""
import json
import shutil
import struct
import time

import cv2
import numpy as np
from PIL import Image

from . import atmos, common, depth, gltfout, glow, ibl, life, marigold, relight, simplify, surface, sway
from ff7bg import palettes  # noqa: E402  (common puts the Studio on the path)
from ff7bg.sheets import PageCache  # noqa: E402

DEFAULTS = {
    "simplify": True,     # the mesh only as fine as it needs to be (see simplify.py), shading detail in a normal map
    "depth_tol": 0.005,   # simplified: how far (relative) a point's depth may move
    "uv_tol": 0.25,       # simplified: how far (background pixels) a point's texture may slide
    "max_level": 5,       # simplified: cells up to 2**5 grid steps
    "sink": 15.0,         # walkable ground this far (world units) under the walkmesh: the chibi boots reach below it
    "sink_deep": 15.0,    # ...the walkable ground itself (not what stands at its edge) this much further: kneeling and
                          # lying models reach further below (ancnt3)
    "sink_slope": 35.0,   # ...plus this x the slope (tan) where the walkmesh slopes: a foot's half length, its toe or
                          # heel below the slope's height under the model's middle (all three x the field's model scale)
    "snap_tol": 110.0,    # within this camera depth of the walkmesh, ground snaps onto it...
    "snap_rel": 0.03,     # ...or within this share of its depth, whichever is more (far ground: 3% of 5000 = 150)
    "anchor": 0.04,       # the depth bent onto the walkmesh by a smooth correction this wide (share of the grid; 0: off)
    "crack": 0.025,       # neighbouring points further apart in depth than this (relative) are a break, not a surface
    "sway": False,        # flowers/foliage sway in the wind (needs a flower bed: see sway.py)
    "reveal": True,       # characters hidden where the original game hid them (match_original_hiding); off: the
                          # scene's own depth decides (the AI's depth puts things a little off: characters walk
                          # through objects or vanish into openings, but the original's rough spots go)
    "static_glow": 0.8,   # how strongly the field's non-animated glow layers (light shafts, haze) show
    "look": "painted",    # painted (the user's pick, 2026-10-24): the painting's own colour and light, with gentle 3D
                          # shading and real shadows on top; relit: the painting under the game's light (relight.py)
    # the look's light and colour (None: the look's own default, LOOK_LIGHT); a look switch applies them in seconds
    "shine": None,        # how much surfaces reflect (the material's specular); water keeps its own
    "light_tint": None,   # 0: white light; 1: light coloured like the scene
    "shading": 0.5,       # how strongly the sun shapes the scene, overall brightness kept
    "brightness": 1.0,    # both lights together
    "saturation": 1.0,    # colour strength of the scene's texture
    "sun_turn": 0.0,      # relit: the sun turned this many degrees around from the painting's
    "sun_height": 0.0,    # relit: the sun this many degrees higher (or lower) than the painting's
    # around the characters (atmos.py), also applied by a look switch
    "sun": "painting",    # painting: the sun's direction fitted to the painting's shading; default: FFNx's
    "char_shadow": 0.5,   # how dark the characters' shadows are on the ground they walk on (0 none, 1 black)
    "char_haze": 1.0,     # how much of the painting's haze the characters take on with distance (1: matched)
    "glow": 0.5,          # how strongly the painting's lights glow, over the characters too (0: none)
    "char_match": 0.0,    # how far the characters' colours follow the new art's change from the original (atmos.grade;
                          # 0.75 until 2026-11-12: its contrast and darkening turned yellow chocobos to gold)
    "char_light": 0.6,    # how much the characters' surrounding light takes the painting's colours (0: neutral)
    "char_local": 0.6,    # how much characters take the light of the floor where they stand (atmos.floor_light)
    "char_outline": 0.0,  # characters outlined in the painting's line colour (for outlined art styles; 0: none)
    "char_soft": 0.35,    # how much softer the characters' textures are, like the painted scene
    "contact_shadow": 0.5,  # a soft dark patch on the floor right under the characters, whatever the sun
    "char_finish": 0.2,   # how shiny the characters look under the lights (1: FFNx's usual plastic-like sheen and sky
                          # reflections; 0: matte, none)
    "char_sss": 1.0,      # light soaking a little into skin, feathers and cloth (FFNx-3d char_sss; 0: as upstream)
    "char_highlight": 1.0,  # the characters' brightest parts roll off softly instead of washing out (FFNx-3d; fields)
    "char_volume": 1.0,   # the characters' parts shaded as if round, not as flat facets (FFNx-3d char_volume; 0: upstream)
    "char_ao": 1.0,       # the light from all around shaded where their parts come close (FFNx-3d char_ao; 0: none)
    "char_fill": 1.0,     # the characters' light from all around, whatever the scene's sun/shade split (0: the scene's
                          # ambient, as before 2026-11-01; 1: CHAR_FILL x brightness; up to 2)
    # life in the scene (FFNx-3d)
    "particles": "auto",  # embers, fireflies, snow, rain or none in the air (auto: atmos.particle_kind)
    "particle_amount": 1.0,  # how many of them (1: the usual number)
    "water_motion": 0.5,  # how much the scene's water ripples (0: still)
    "clouds": 0.0,        # cloud shadows drifting over the field, characters too (sunny outdoors; atmos.clouds)
    "flicker": 0.0,       # warm lights (fires, torches, lanterns) flicker (atmos.flicker)
    "grass": 0.0,         # grass and leaves move in the wind (surface.grass_mask, atmos.grass)
    # found in the painting (life.py: Qwen3.5 points out chimneys, fires, flags and the sky; windows by their light)
    "smoke": 0.5,         # smoke rising from chimneys
    "fire": 0.5,          # sparks rising from fires and furnaces, the air shimmering over them
    "windows": 0.5,       # lit windows and lanterns glow (whatever the glow above), their light on the ground
    "sky": 0.5,           # the sky slowly drifts
    "flags": 0.5,         # flags and banners wave
    "birds": 0.3,         # now and then a flock of birds crosses the sky
    "blink": 0.5,         # screens shimmer, indicator lights blink (tech scenes)
    "motes": 0.5,         # dust glinting in the light shafts
    "mist": 0.5,          # mist rolls slowly through misty areas
    "bubbles": 0.5,       # bubbles rise in liquid tanks
    "char_lamps": 0.5,    # characters are lit by the lamps, fires and screens they pass
    "reflections": 0.5,   # characters are reflected in water and polished floors
    # the scene's surfaces for the game's light (surface.py); off: as built before them
    "occlusion": 0.0,     # ambient occlusion: corners and the floor under things get less surrounding light
    "gloss": 0.0,         # shine where the painting has highlights (polished wood, metal, wet floors)
    "metal": 0.0,         # of the glossy surfaces, the grey ones reflect like metal
    "bump": 0.0,          # fine relief from the painting's texture for the game's light to catch
}
# per look: sun and ambient strength at shading 0.5, and the defaults of the settings above. Painted: an unshadowed
# surface ~ the painting, matte under white light (the painting already has its highlights and its light's colour:
# reflections and tinted light on top of it greyed out dark fields such as md1_1). Relit: FFNx's own light.
LOOK_LIGHT = {"painted": {"sun": 1.4, "ambient": 2.3, "shine": 0.0, "light_tint": 0.0},
              "relit": {"sun": 4.0, "ambient": 1.5, "shine": 0.35, "light_tint": 0.5}}
SUN_AS_AMBIENT = 0.45  # how much ambient strength one unit of sun gives an average surface (keeps brightness)
# the characters' ambient at char_fill 1, times brightness. With shading 1 the scene's ambient is a quarter of the
# look's (relit at brightness 0.75: 0.28) and characters went nearly black wherever the sun missed them; 3.0 (at
# brightness 0.75) made anfrst_1's Cloud readable without changing its relit scene (2026-11-01, user: "characters
# very dark and dull, they blend in too much")
CHAR_FILL = 4.0
WATER_SHINE = 0.35
WIND_BONES, WIND_FRAMES = 10, 360


# The settings the Studio's 3D tab sets (a look switch applies these in seconds; the rest need a build)
LOOK_KEYS = ("look", "shine", "light_tint", "shading", "brightness", "saturation", "sun_turn", "sun_height", "sun",
             "char_shadow", "char_haze",
             "glow", "char_match", "char_light", "char_local", "char_outline", "char_soft", "contact_shadow", "char_finish",
             "char_fill", "char_sss", "char_highlight", "char_volume", "char_ao",
             "particles", "particle_amount", "water_motion", "clouds", "flicker", "grass", "occlusion", "gloss", "metal", "bump",
             "smoke", "fire", "windows", "sky", "flags", "birds", "blink", "motes", "mist", "bubbles", "char_lamps",
             "reflections")
# The characters' settings that are the same in every field (2026-11-02, user: "some settings would be better as
# global settings rather than per scene, e.g. character outlines"): FFNx-3d reads them from config.toml's [fields]
# (atmos.fields_settings), so a change shows at once in every field without re-applying a look, and a field can't
# have its own. Each field's section keeps only its own part (its outline colour and scale, its whole colour grade,
# its floor light map).
GAMEWIDE = ("char_shadow", "char_haze", "char_match", "char_local", "char_outline", "char_soft", "contact_shadow",
            "char_finish", "char_fill", "char_sss", "char_highlight", "char_volume", "char_ao")


def write_fields_section(o=None):
    """config.toml's [fields] from the shared settings (`o`: those, if already at hand)."""
    o = o or shared_options()
    look, ll = o["look"], look_light(o, o["look"])
    ibl.write_section("fields", {
        **atmos.fields_settings(o, char_ambient(look, ll["shading"], ll["brightness"], float(o["char_fill"]))),
        "char_round": 0.0})  # FFNx-3d's rounder models hid eyes drawn as separate pieces (mds7pb_1): off
    # every battle's characters the same way (a battle look's own [battle_STAGE<nn>] comes first; its material is
    # matte too): before 2026-11-12 battles took the file's top level, FFNx's half-metallic stock
    ibl.write_section("battles", atmos.battles_settings(o))
    ibl.write_section("worldmap", atmos.worldmap_settings(o))  # ...and the world map's (FFNx-3d [worldmap])


# One set every field follows (SHARED, the 3D tab's "All fields"), and a field's own changes on top
# (work/<field>/options.json, "This field only"). Until 2026-11-01 each field kept a copy of every look setting from
# whenever it was last built and new fields started from the last ones used (OLD_REMEMBERED), so the scenes drifted
# apart (39 different sets over 248 fields) and couldn't be given one set again.
SHARED = common.WORK / "options_all.json"
OLD_REMEMBERED = common.WORK / "options_last.json"


def shared():
    """What every field follows, where it differs from DEFAULTS."""
    return json.loads(SHARED.read_text()) if SHARED.exists() else {}


def shared_options():
    return {**DEFAULTS, **shared()}


def set_shared(**changes):
    """Change the settings every field follows (a value equal to the built-in default is dropped)."""
    s = {k: v for k, v in {**shared(), **changes}.items() if k in DEFAULTS and DEFAULTS[k] != v}
    SHARED.parent.mkdir(parents=True, exist_ok=True)
    SHARED.write_text(json.dumps(s))
    write_fields_section({**DEFAULTS, **s})  # the game-wide ones show in the game at once
    return {**DEFAULTS, **s}


def own(name):
    """A field's own changes: what it has different from every other field."""
    p = common.WORK / name / "options.json"
    return json.loads(p.read_text()) if p.exists() else {}


def field_options(name):
    return {**shared_options(), **{k: v for k, v in own(name).items() if k not in GAMEWIDE}}


def options(f):
    return field_options(f.name)


def _write_own(name, o):
    p = common.WORK / name / "options.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o))


def set_options(name, **changes):
    """Change a field's own settings; a value equal to what all fields have is dropped (the field follows them)."""
    below = shared_options()
    o = {k: v for k, v in {**own(name), **changes}.items() if k in DEFAULTS and k not in GAMEWIDE and below[k] != v}
    _write_own(name, o)
    return {**below, **o}


def follow(name, keys=None):
    """Drop a field's own changes (`keys`, or all of them): it follows all fields there again."""
    o = {k: v for k, v in own(name).items() if keys is not None and k not in keys}
    _write_own(name, o)
    return {**shared_options(), **o}


def adopt_shared():
    """Once: the last settings used become the set all fields follow, and each field's copy keeps only where it
    differs from that (every field looks exactly as before)."""
    if SHARED.exists():
        return False
    set_shared(**(json.loads(OLD_REMEMBERED.read_text()) if OLD_REMEMBERED.exists() else {}))
    for p in common.WORK.glob("*/options.json"):
        set_options(p.parent.name)  # prunes against the shared set
    return True


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def grid_step(f):
    """One vertex every 2 render pixels, coarser for very large fields (keep under ~1.2M vertices); every pixel for
    renders at the original's size (an output scale of 1x)."""
    if f.scale < 2:
        return 1
    return 2 if f.RW * f.RH / 4 <= 1.2e6 else (3 if f.RW * f.RH / 9 <= 1.2e6 else 4)


def marigold_normals(f, shape, blur=0.5):
    """Marigold's camera normals (x right, y up, z to viewer) -> world, at `shape` (the grid, or a texture)."""
    mg = np.asarray(Image.open(f.work / "mg_normals.png").convert("RGB")).astype(np.float32) / 127.5 - 1
    nc = unit(mg) * np.array([1, -1, -1], np.float32)  # -> game camera: x right, y down, z forward
    small = cv2.resize(nc @ f.R, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)
    return unit(np.stack([cv2.GaussianBlur(small[..., k], (0, 0), blur) for k in range(3)], -1))


def walk_slope(f, W, step):
    """tan of the walkmesh's slope (0 flat, capped at 1) at the walkmesh surface W picks over each grid point; 0 off
    the walkmesh."""
    bx, by = depth.grid_bg(f, step)
    bx, by = bx[:W.shape[0], :W.shape[1]], by[:W.shape[0], :W.shape[1]]
    t = f.tris
    nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    tan = np.minimum(np.hypot(nrm[:, 0], nrm[:, 1]) / np.maximum(np.abs(nrm[:, 2]), 1e-9), 1.0)
    s, z = f.project(t.reshape(-1, 3))
    s, z = s.reshape(-1, 3, 2), z.reshape(-1, 3)
    out = np.zeros(W.shape, np.float32)
    for (a, b, c), (za, zb, zc), tn in zip(s, z, tan):
        if min(za, zb, zc) <= 0 or tn <= 0:
            continue
        m = (bx >= min(a[0], b[0], c[0])) & (bx <= max(a[0], b[0], c[0])) & (by >= min(a[1], b[1], c[1])) & \
            (by <= max(a[1], b[1], c[1]))
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if not m.any() or abs(d) < 1e-9:
            continue
        px, py = bx[m], by[m]
        l1 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        l2 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        zv = 1 / (l1 / za + l2 / zb + l3 / zc)
        here = inside & (np.abs(zv - W[m]) < 0.01 * zv)  # the surface W picked there, not another under/over it
        out[m] = np.where(here, np.maximum(out[m], tn), out[m])
    return out


def snap_to_walkmesh(f, Z, W, Pc, o, reach=12, up=None, step=2):
    """Walkable ground onto the walkmesh (sunk by o['sink'], more where models dip further below it), moving points
    along their camera rays; only surfaces facing up (`up`, 0..1), so posts and fronts standing just before the
    walkmesh keep their depth. Which ground is walkable is judged against the walkmesh itself, not the sunk one: at a
    grazing view a few units of sink are a long way along the ray (ancnt3's altar: 15 units were 60)."""
    on = ~np.isnan(W)
    target = edge = W
    if o["sink"]:
        # the floor goes under the models' lowest parts: chibi boots reach below the walkmesh, more on a slope (the
        # feet are set at the slope's height under their middle: ancnt2's ramp hid Cloud's boot), kneeling and lying
        # ones further (ancnt3: Cloud kneeling, Aerith lying, cut by the altar's top), all with the field's model
        # scale (ancnt3's 1024). What stands at the floor's edge follows only the usual sink (`spread` below)
        scale = model_scale(f) / 512
        u = Pc / np.linalg.norm(Pc, axis=-1, keepdims=True)
        along = u[..., 2] / np.maximum(-(u @ f.R)[..., 2], 0.15)  # camera depth per world unit down, along the ray
        target = W + (o["sink"] + o["sink_deep"] + o["sink_slope"] * walk_slope(f, W, step)) * scale * along
        edge = W + o["sink"] * scale * along
    tol = np.maximum(o["snap_tol"], o["snap_rel"] * np.nan_to_num(W, nan=0.0))
    band = tol * 0.55
    d = np.where(on, target - Z, 0)
    w = np.where(on, np.clip((tol + band - np.abs(np.where(on, W - Z, 0))) / band, 0, 1), 0).astype(np.float32)
    if up is not None:
        w *= up.astype(np.float32)
    c = (d * w).astype(np.float32)
    dist = cv2.distanceTransform((w == 0).astype(np.uint8), cv2.DIST_L2, 3)
    blur_w = cv2.GaussianBlur(w, (0, 0), reach / 2)
    spread = cv2.GaussianBlur((np.where(on, edge - Z, 0) * w).astype(np.float32), (0, 0), reach / 2) / np.maximum(blur_w, 1e-3)
    fade = np.clip(1 - dist / reach, 0, 1) * (blur_w > 1e-3)
    c = np.where(w > 0, c, spread * fade)
    return Z + c, {"snapped": float((w == 1).mean()), "snap_median_shift": float(np.median(np.abs(d[w == 1]))) if (w == 1).any() else 0.0}, w


TILE_DEPTH = 4.0  # a background tile's depth value is about camera depth / 4 (the PlayStation's ordering table)


def push_back(Z, regions, foreground=0.6, behind=1.08, ramp=32):
    """Under each animated glow layer, push the scene that should be behind it back along the camera rays to just
    behind the layer's tiles (camera depth ~ TILE_DEPTH x tile depth), so the game's additive tiles draw over it.
    What's nearer than `foreground` of the tiles' depth (a house in front of a waterfall) stays put."""
    GH, GW = Z.shape
    ys, xs = np.mgrid[0:GH, 0:GW]
    out = Z.copy()
    for c0, r0, c1, r1, z in regions:
        zt = TILE_DEPTH * z
        if zt <= 0:
            continue
        d = np.maximum(np.maximum(c0 - xs, xs - (c1 - 1)), np.maximum(r0 - ys, ys - (r1 - 1))).clip(0)
        g = np.clip(1 - d / ramp, 0, 1) * np.clip((Z - foreground * zt) / (0.1 * zt), 0, 1)
        out = np.maximum(out, Z + g * np.clip(behind * zt - Z, 0, None))
    return out


CHAR_HEIGHT = 90.0  # a standing character, world units at the usual model scale (512): Cloud ~65 (mds7st1, from
                    # FFNx-3d's contact parts), a little more for the taller ones; too tall only clears more air.
                    # Fields with close cameras scale their models up (ancnt3: 1024, Sephiroth twice as tall)


def model_scale(f):
    """The scale the field draws its characters at (the model loader section's; usually 512)."""
    from .common import _sections
    return struct.unpack_from("<H", _sections(f.raw)[2], 4)[0] or 512
CLEAR_GAP = 20.0     # the scene this far (world units) in front of a character's feet hides them
CHAR_DEPTH = 25.0    # half a character's thickness toward the camera, world units at model scale 512 (an arm or a
                     # shoulder pad in front of the body: elm, Cloud's arm through a pipe)
FRINGE_PX = 3        # match_original_hiding: this close (original pixels) outside a covering tile's shape, the new art
                     # may still show the same object (its outlines differ a little: elm's pipes)
FLOOR_PATCH_PX = 2   # ...floor narrower than about twice this (original pixels) is a ledge's top, not floor
SAME_SURFACE = 0.02  # ...and it does where its depth continues from the covered pixels beside it within this share
CLEAR_ROWS = 64      # match_original_hiding tries every point up a character this tall on screen (rows), every q-th beyond
REACH_SIDE = 1.1     # ...where the original has nothing that covers characters, what a character holds reaches this far
REACH_DOWN = 0.4     # to its sides and this far below its feet (x its height): the chibi Cloud's sword, out to his side
                     # and toward the camera (bwhlin: hidden by the bench beside him, which the original never draws over him)
FRONT_TILE = 0.5     # an original tile nearer than this share of the scene it covers is an "always in front" one (z 1-2:
                     # sango1's hollow log, bigwheel's gondola roof, sbwy4_3's black shaft; elm's door lintels, z 112 on
                     # walls ~1500 deep): its outline is the artists' cut, not an object's. Up-facing ground under one
                     # isn't floor (whoever stands there is hidden in 2D), and no fringe (FRINGE_PX) goes round it


LADER = 0xC2  # field opcode: climb a ladder to (x, y, z) on a walkmesh triangle (banks, x, y, z, triangle, ...)


def ladders(f):
    """The field's ladders: [(end, end)] in field world coordinates, from its scripts' LADER commands (one at each
    end, in the same entity: mds7st1's four up the train cars)."""
    by_entity = {}
    for sc in palettes.scripts(f.raw):
        for _, op, b in sc.ops():
            if op == LADER and len(b) >= 15 and b[1] == 0 and b[2] == 0:  # coordinates given, not from variables
                x, y, z = struct.unpack_from("<hhh", b, 3)
                by_entity.setdefault(sc.entity, []).append(np.array([x, y, z], float))
    out = []
    for pts in by_entity.values():
        for i, a in enumerate(pts):  # each end with the nearest other end at another height
            others = [(np.linalg.norm(b - a), j) for j, b in enumerate(pts) if j != i and abs(b[2] - a[2]) > 30]
            if others:
                j = min(others)[1]
                if (min(i, j), max(i, j)) not in {(min(u, v), max(u, v)) for u, v in [(k[2], k[3]) for k in out]}:
                    out.append((a, pts[j], i, j))
    return [(a, b) for a, b, _, _ in out]


def ladder_layers(f, step, GH, GW):
    """Each ladder as a walkway for match_original_hiding: (gh, gw) arrays, the camera depth of a climber's feet on the
    grid points along it, NaN elsewhere."""
    bx, by = depth.grid_bg(f, step)
    x0, y0 = bx[0, 0], by[0, 0]
    dx, dy = bx[0, 1] - x0, by[1, 0] - y0
    out = []
    for a, b in ladders(f):
        n = 200
        P = a[None] + np.linspace(0, 1, n)[:, None] * (b - a)[None]
        s, z = f.project(P)
        col = np.round((s[:, 0] - x0) / dx).astype(int)
        row = np.round((s[:, 1] - y0) / dy).astype(int)
        ok = (z > 1) & (col >= 0) & (col < GW) & (row >= 0) & (row < GH)
        L = np.full((GH, GW), np.nan, np.float32)
        L[row[ok], col[ok]] = z[ok]
        out.append(L)
    return out


CHAR, XYZI, XYI, XYZ, OFST = 0xA1, 0xA5, 0xA6, 0xA7, 0xC3  # field opcodes: the entity's model; place it (banks, x, y,
# z, triangle / x, y, triangle / x, y, z); offset its model from there (banks, type, x, y, z, speed)
PROP_UNIT = 4.0  # a prop's model units -> world units at model scale 512 (bigwheel's gondola, a flat picture of one
                 # at scale 768: ~4 by its size on screen); too big only applies the original's layering wider


def field_models(f):
    """The field's models, in its model list's order: (scale / 512, None for a character (it has bones: CHAR_HEIGHT
    tall) or a prop's (horizontal, vertical) half size in model units, from its mesh)."""
    import re
    from ff7bg.lgp import LgpArchive
    from .common import _sections
    s = _sections(f.raw)[2]
    n = struct.unpack_from("<H", s, 2)[0]
    p, out = 6, []
    with LgpArchive(str(common.GAME / "data" / "field" / "char.lgp")) as lgp:
        names = set(lgp.names())
        for _ in range(n):
            p += 2 + struct.unpack_from("<H", s, p)[0] + 2   # name, unknown
            hrc = s[p:p + 8].decode("latin1").lower()
            scale = int(s[p + 8:p + 12].split(b"\0")[0] or b"512") / 512
            anims = struct.unpack_from("<H", s, p + 12)[0]
            p += 12 + 2 + 30  # hrc, scale, animation count, light
            for _ in range(anims):
                p += 2 + struct.unpack_from("<H", s, p)[0] + 2
            size = None
            if hrc in names:
                text = lgp.read(hrc).decode("latin1")
                bones = re.search(r":BONES\s+(\d+)", text)
                if bones and int(bones.group(1)) == 0:  # a prop: one mesh at its root
                    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
                    for rsd in re.findall(r"\b([A-Za-z]{4})\b", text.split("root", 1)[-1]):
                        rn = rsd.lower() + ".rsd"
                        ply = re.search(r"PLY=(\w+)\.", lgp.read(rn).decode("latin1")) if rn in names else None
                        pn = ply.group(1).lower() + ".p" if ply else ""
                        if pn in names:
                            d = lgp.read(pn)
                            nv = struct.unpack_from("<I", d, 12)[0]
                            v = np.frombuffer(d, np.float32, nv * 3, 128).reshape(-1, 3)
                            lo, hi = np.minimum(lo, v.min(0)), np.maximum(hi, v.max(0))
                    if np.isfinite(lo).all():
                        ext = np.maximum(np.abs(lo), np.abs(hi))
                        size = (float(max(ext[0], ext[2])), float(ext[1]))  # model y is up/down
            out.append((scale, size))
    return out


def placed_models(f):
    """Where the field's scripts put its models with coordinates given (XYZI, XYZ, XYI; also on the way to the
    entity's model offsets, OFST), as [(feet xyz, height, half width, kind)] in world units, kind "char", "prop" or
    "flat". A character stands there, CHAR_HEIGHT tall; a prop (bigwheel's gondola: a picture of one, far bigger than a
    character, its lower half was hidden behind the platform) is its mesh around the spot. A prop lying flat that
    never moves (placed, or offset at once: kuro_4's clock hands, under the clock) is "flat" there; one that does
    (md8_52's debris flying off) stays a "prop" along its way. A prop no script places stands at the field's origin
    (kuro_4's hands turn there)."""
    models = field_models(f)
    ents = {}
    for sc in palettes.scripts(f.raw):
        e = ents.setdefault(sc.entity, {"model": None, "at": [], "off": [], "placed": False})
        for _, op, b in sc.ops():
            if op in (XYZI, XYZ, XYI):
                e["placed"] = True
            if len(b) < 3 or b[1] or b[2]:  # coordinates from variables
                if op == CHAR and len(b) >= 2:
                    e["model"] = b[1]
                continue
            if op in (XYZI, XYZ) and len(b) >= 9:
                e["at"].append(np.array(struct.unpack_from("<hhh", b, 3), float))
            elif op == XYI and len(b) >= 9:
                x, y, t = struct.unpack_from("<hhH", b, 3)
                if t < len(f.tris):
                    a, b_, c = f.tris[t]
                    n = np.cross(b_ - a, c - a)
                    if abs(n[2]) > 1e-6:
                        e["at"].append(np.array([x, y, a[2] - (n[0] * (x - a[0]) + n[1] * (y - a[1])) / n[2]]))
            elif op == OFST and len(b) >= 12:
                e["off"].append((np.array(struct.unpack_from("<hhh", b, 4), float), b[3]))  # (offset, 0: at once)
    out = []
    for e in ents.values():
        if e["model"] is None or e["model"] >= len(models):
            continue
        scale, size = models[e["model"]]
        if not e["at"] and size is not None and not e["placed"]:
            e["at"] = [np.zeros(3)]
        if not e["at"]:
            continue
        kind = "char" if size is None else "prop"
        offs = [(np.zeros(3), kind)] + [(o * t, kind) for o, _ in e["off"] for t in (0.25, 0.5, 0.75, 1.0)]
        if size is None:
            height, half, below = CHAR_HEIGHT * scale, 0.17 * CHAR_HEIGHT * scale, 0.0
        else:
            height, half, below = 2 * PROP_UNIT * scale * size[1], PROP_UNIT * scale * size[0], PROP_UNIT * scale * size[1]
            if height < FLAT_PROP * half and all(how == 0 for _, how in e["off"]):
                offs = [(np.zeros(3), "flat")] + [(o, "flat") for o, _ in e["off"]]
        for p in e["at"]:
            for o, kind in offs:
                out.append((p + o - np.array([0, 0, below]), height, half, kind))
    return out


FLAT_PROP = 0.5   # a prop less tall than this x its half width lies flat (kuro_4's clock hands: no height at all)
PROP_THICK = 4.0  # world units: ...and is taken as at least this thick


def prop_depths(f, bx, by, feet, height, radius):
    """Where a prop lying flat covers the grid (bx, by: its background coords) and how near and far it is there
    (camera depth; NaN where it doesn't): a flat cylinder around it (it may be turned any way), from its feet up
    `height`, `radius` wide, each grid point's camera ray against it. Drawn as standing up (placed_layers' walkways),
    a flat prop covered a line on screen: kuro_4's clock hands, which turn under the clock, were cut by the black of
    the hole."""
    O = -f.Rinv @ np.asarray(f.T, float)  # the camera in the world
    D = np.stack([bx / f.zoom, by / f.zoom, np.ones_like(bx)], -1) @ f.Rinv.T  # world step per unit of camera depth
    lo, hi = feet[2] - 0.5 * PROP_THICK, feet[2] + max(height, 0.0) + 0.5 * PROP_THICK
    ox, oy = O[0] - feet[0], O[1] - feet[1]
    dx, dy, dz = D[..., 0], D[..., 1], D[..., 2]
    a = dx * dx + dy * dy
    b = 2 * (dx * ox + dy * oy)
    c = ox * ox + oy * oy - radius * radius
    disc = b * b - 4 * a * c
    flat = a < 1e-12  # a ray straight down the axis
    sq = np.sqrt(np.maximum(disc, 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t0 = np.where(flat, np.where(c <= 0, -np.inf, np.inf), (-b - sq) / (2 * a))
        t1 = np.where(flat, np.where(c <= 0, np.inf, -np.inf), (-b + sq) / (2 * a))
        t0 = np.where(~flat & (disc < 0), np.inf, t0)
        t1 = np.where(~flat & (disc < 0), -np.inf, t1)
        level = np.abs(dz) < 1e-12
        s0 = np.where(level, np.where((O[2] >= lo) & (O[2] <= hi), -np.inf, np.inf), (lo - O[2]) / dz)
        s1 = np.where(level, np.where((O[2] >= lo) & (O[2] <= hi), np.inf, -np.inf), (hi - O[2]) / dz)
    s0, s1 = np.minimum(s0, s1), np.maximum(s0, s1)
    near, far = np.maximum(np.maximum(t0, s0), 1.0), np.minimum(t1, s1)
    hit = far >= near
    return np.where(hit, near, np.nan).astype(np.float32), np.where(hit, far, np.nan).astype(np.float32)


def placed_layers(f, step, GH, GW):
    """placed_models for match_original_hiding: characters and props as walkways ((gh, gw) feet depth array, height,
    half width, whether it's a character), one array per size (a second where two share a grid point); each prop lying
    flat where it's set down as ("prop", nearest, farthest), its depth where it covers the grid (prop_depths)."""
    bx, by = depth.grid_bg(f, step)
    bx, by = bx[:GH, :GW].astype(np.float64), by[:GH, :GW].astype(np.float64)
    x0, y0 = bx[0, 0], by[0, 0]
    dx, dy = bx[0, 1] - x0, by[1, 0] - y0
    groups, props = {}, {}
    for feet, height, half, kind in placed_models(f):
        if kind == "flat":
            key = tuple(np.round(feet, 1)) + (round(height, 1), round(half, 1))
            if key not in props:
                near, far = prop_depths(f, bx, by, feet, height, half)
                if np.isfinite(near).any():
                    props[key] = ("prop", near, far)
            continue
        s, z = f.project(feet[None])
        col, row, z = int(round((s[0, 0] - x0) / dx)), int(round((s[0, 1] - y0) / dy)), float(z[0])
        if z <= 1 or not (0 <= col < GW and 0 <= row < GH):
            continue
        layers = groups.setdefault((round(height, 1), round(half, 1)), [])
        for L in layers:
            if np.isnan(L[row, col]) or abs(L[row, col] - z) < 1:
                break
        else:
            layers.append(np.full((GH, GW), np.nan, np.float32))
            L = layers[-1]
        L[row, col] = z
    # (characters, not props, also reach out: match_original_hiding's REACH_*)
    return [(L, h, w, abs(w - 0.17 * h) < 0.01 * h) for (h, w), layers in groups.items() for L in layers] + \
        list(props.values())


def match_original_hiding(f, Z, layers, step, up=None, floor=None, bodies=None, floor_at=None):
    """Characters are hidden where the original game hid them, wherever one can be (`layers`: the walkways from
    depth.walkmesh_layers and the ladders, a character on every point, and the models the scripts place with their own
    sizes, placed_layers): the scene moves along its camera rays
    (the picture stays the same) so that, at each grid point, it's in front of every character the original's
    layer-1 tile there covers and behind every one it shows. Square's artists gave every tile that covers
    characters a depth for exactly this; the AI's depth puts things a little off (elm: Cloud walked through the
    machine's pipes, mds7st1: hidden in the open train car, ancnt3: Sephiroth cut by the balustrade). Layer 0 never
    covers anyone; the original's rough spots come along too (the farm's near fence is layer 0). Around the bodies, as
    far as what they hold reaches (REACH_*), the scene goes behind them too where the original has nothing in front of
    them. `floor`: the ground snap_to_walkmesh put under the walkmesh (sunk, it's further back than the walkway's
    depth), `floor_at` the walkway depth it was snapped onto. `bodies` (a dict) gets the nearest and farthest body
    depth that can cover each grid point ("near", "far": inf / -inf where none can) and their thickness ("thick").
    Returns (Z, stats)."""
    GH, GW = Z.shape
    g = f.scale / step  # grid points per background pixel
    # the original's covering layer: layer 1, the only one with a depth per tile (layer 0 is always behind
    # characters; toggled tiles are drawn by the game, with their own depth)
    front = np.full((f.h, f.w), np.inf, np.float32)
    cache = PageCache(f.bg)
    for t in f.bg.tiles:
        if t.layer != 1 or t.blending or t.param:
            continue
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        if x < 0 or y < 0 or x + s > f.w or y + s > f.h:
            continue
        a = cache.tile(t)[..., 3] > 0
        front[y:y + s, x:x + s][a] = np.minimum(front[y:y + s, x:x + s][a], TILE_DEPTH * t.z)
    # one-pixel cracks between the tiles' pixels (elm: a diagonal seam along the machine's panel) cover too: drawn
    # big, a character would show through them as a line
    big = np.float32(1e9)
    covered_bg = np.isfinite(front)
    sq = np.ones((3, 3), np.uint8)
    crack = (cv2.morphologyEx(covered_bg.astype(np.uint8), cv2.MORPH_CLOSE, sq) > 0) & ~covered_bg
    front[crack] = cv2.erode(np.where(covered_bg, front, big).astype(np.float32), sq)[crack]
    bx, by = depth.grid_bg(f, step)
    bx, by = bx[:GH, :GW], by[:GH, :GW]
    front = front[np.clip(np.round(by - f.my).astype(int), 0, f.h - 1), np.clip(np.round(bx - f.mx).astype(int), 0, f.w - 1)]
    # just outside a covering shape the new art's object may go on (elm: its pipes and elbows are a little bigger):
    # where the scene's depth continues from the covered points beside it, that's the same object and it covers as
    # the tile does; where it jumps, it's what's behind (Cloud's hair shows above the pipe). Not round an "always in
    # front" tile (FRONT_TILE): its edge is where the original cuts (elm: the walls over its doorways went on into the
    # door frames and cut the hair of whoever stood in a doorway, which AI 2D shows)
    r = max(1, int(round(FRINGE_PX * g)))
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    covered = np.isfinite(front)
    tile_by = cv2.erode(np.where(covered, front, big).astype(np.float32), kern)
    scene_by = cv2.erode(np.where(covered, Z, big).astype(np.float32), kern)
    # where the scene is the walkable floor itself (facing up, `up` 0..1, and at the walkway's depth), it never
    # covers anyone walking there (the original's tiles take in a little floor around an object: elm's floor above a
    # pipe cut Cloud's head). The foot of an upright thing is at the floor's depth too, but faces the camera. Only
    # proper patches of floor: a thin up-facing strip is a ledge's top (elm: the panel under the pipe, Cloud's boots
    # showed through it)
    floor = np.zeros((GH, GW), bool) if floor is None else floor[:GH, :GW].copy()
    # the walkway each piece of floor is the ground of (its depth, unsunk)
    fw = np.full((GH, GW), np.nan, np.float32)
    if floor_at is not None:
        fw[floor] = floor_at[:GH, :GW][floor]
    for W in layers:
        if isinstance(W, tuple):  # a placed model, not a walkway
            continue
        tol = np.maximum(CLEAR_GAP, 0.005 * W)
        at = np.isfinite(W) & (Z > W - tol) & (Z < W + 4 * tol)
        fw = np.where(at & np.isnan(fw), W, fw)
        floor |= at
    if up is not None:
        floor &= up[:GH, :GW] > 0.5
    fr = max(1, int(round(FLOOR_PATCH_PX * g)))
    floor = cv2.morphologyEx(floor.astype(np.uint8), cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * fr + 1, 2 * fr + 1))) > 0
    # up-facing ground that the original covers with an "always in front" tile (far nearer than its walkway) isn't
    # the floor anyone there stands on, it's what hides them (sango1: the hollow log's top over the path inside it,
    # bigwheel: the gondola's roof, sbwy4_3: the black of the shaft)
    floor &= ~(front < FRONT_TILE * np.nan_to_num(fw, nan=0.0))
    same = ~covered & ~floor & (tile_by < big) & (tile_by >= FRONT_TILE * scene_by) & \
        (np.abs(Z - scene_by) < SAME_SURFACE * Z)
    front = np.where(same, tile_by, front)
    # the nearest and the farthest character body that can cover each grid point (standing anywhere their body covers
    # it), at the depth of the part of them there: higher up is nearer under a camera looking down (elm: a shoulder
    # 50 units up is 26 nearer than the feet), farther under one looking up (ancnt3)
    near = np.full((GH, GW), np.inf, np.float32)
    far = np.full((GH, GW), -np.inf, np.float32)
    far_feet = np.full((GH, GW), -np.inf, np.float32)  # the farthest feet: the scene stepping back clears whole bodies
    far_reach = np.full((GH, GW), -1e9, np.float32)  # the farthest feet of any character whose reach (REACH_*) is there
    scale = model_scale(f) / 512
    up_dz = float(np.asarray(f.R, float)[2, 2])  # camera depth per world unit up
    thick = CHAR_DEPTH * scale
    # the floor never covers anyone standing on it or in front of it, but it does cover, where the original's tile
    # does, whoever is clearly behind its walkway (onna_52: the round bed's top, sunk under its walkway at a grazing
    # view, went behind Cloud standing behind the bed): the nearest such body, per grid point
    behind = np.where(floor & np.isfinite(fw), fw + CLEAR_GAP + thick, -np.inf).astype(np.float32)
    near_b = np.full((GH, GW), np.inf, np.float32)
    for L in layers:
        if isinstance(L, tuple) and isinstance(L[0], str):  # a prop lying flat: where it covers, its own depth
            pn, pf = L[1], L[2]
            on = np.isfinite(pn)
            near[on], far[on] = np.minimum(near[on], pn[on]), np.maximum(far[on], pf[on])
            far_feet[on] = np.maximum(far_feet[on], pf[on])
            nb = on & (pn > behind)
            near_b[nb] = np.minimum(near_b[nb], pn[nb])
            continue
        # a walkway or ladder (a character on each point: CHAR_HEIGHT tall, about a third as wide), or a model the
        # scripts place (placed_layers: its own height and half width), world units
        W, tall, wide, reach = L if isinstance(L, tuple) else (L, CHAR_HEIGHT * scale, 0.17 * CHAR_HEIGHT * scale, True)
        walk = np.isfinite(W)
        if not walk.any():
            continue
        body = np.zeros((GH, GW), np.float32)
        body[walk] = tall * f.zoom * g / W[walk]  # its height on screen, rows
        half = int(round(float(np.median(wide * f.zoom * g / W[walk]))))
        # characters on every q-th point up and across (neighbours are nearly the same): close cameras make them
        # hundreds of rows tall
        q = max(1, int(np.ceil(float(body.max()) / CLEAR_ROWS)))
        # only the part of the grid they can reach (a placed model is one point)
        rows, cols = np.nonzero(walk)
        r0, r1 = max(0, rows.min() - int(np.ceil(body.max())) - q), rows.max() + 1
        c0, c1 = max(0, cols.min() - half - q), min(GW, cols.max() + half + q + 1)
        feet = np.where(walk, W, np.nan).astype(np.float32)[r0:r1, c0:c1]
        body = body[r0:r1, c0:c1]
        h = r1 - r0
        ln = np.full((h, c1 - c0), np.inf, np.float32)
        lnb = np.full((h, c1 - c0), np.inf, np.float32)
        lf = np.full((h, c1 - c0), -np.inf, np.float32)
        lff = np.full((h, c1 - c0), -np.inf, np.float32)
        bh = behind[r0:r1, c0:c1]
        for dx in range(-(half // q) * q, half + 1, q):
            fx = np.roll(feet, dx, axis=1)
            bxs = np.roll(body, dx, axis=1)
            if dx > 0:
                fx[:, :dx] = np.nan
            elif dx < 0:
                fx[:, dx:] = np.nan
            for k in range(0, min(int(np.ceil(body.max())), h - 1) + 1, q):
                src = fx[k:] if k else fx
                ok = ((bxs[k:] if k else bxs) >= k) & np.isfinite(src)
                part = src * (1 + up_dz * k / (f.zoom * g))  # the depth of the body k rows above the feet
                dst = slice(0, h - k)
                ln[dst] = np.where(ok, np.minimum(ln[dst], part), ln[dst])
                lnb[dst] = np.where(ok & (part > bh[dst]), np.minimum(lnb[dst], part), lnb[dst])
                lf[dst] = np.where(ok, np.maximum(lf[dst], part), lf[dst])
                lff[dst] = np.where(ok, np.maximum(lff[dst], src), lff[dst])
        if q > 1:  # the in-between points: each found character covers the q x q block it stands for
            k = np.ones((q, q), np.uint8)
            ln = -cv2.dilate(np.where(np.isfinite(ln), -ln, -1e9).astype(np.float32), k)
            ln[ln > 1e8] = np.inf
            lnb = -cv2.dilate(np.where(np.isfinite(lnb), -lnb, -1e9).astype(np.float32), k)
            lnb[lnb > 1e8] = np.inf
            lf = cv2.dilate(np.where(np.isfinite(lf), lf, -1e9).astype(np.float32), k)
            lf[lf < -1e8] = -np.inf
            lff = cv2.dilate(np.where(np.isfinite(lff), lff, -1e9).astype(np.float32), k)
            lff[lff < -1e8] = -np.inf
        win = (slice(r0, r1), slice(c0, c1))
        near[win], far[win], far_feet[win] = np.minimum(near[win], ln), np.maximum(far[win], lf), np.maximum(far_feet[win], lff)
        near_b[win] = np.minimum(near_b[win], lnb)
        if reach:  # a box around each body, by bands of depth, each as big as its nearest one's (only ever too big)
            edges = np.unique(np.quantile(W[walk], np.linspace(0, 1, 7)))
            for lo, hi in zip(edges[:-1], edges[1:]) if len(edges) > 1 else [(edges[0], edges[0])]:
                band = walk & (W >= lo) & (W <= hi)
                rows = tall * f.zoom * g / lo
                up_r, down_r, side = int(np.ceil(rows)), int(np.ceil(REACH_DOWN * rows)), int(np.ceil(REACH_SIDE * rows))
                box = np.ones((up_r + down_r + 1, 2 * side + 1), np.uint8)
                far_reach = np.maximum(far_reach, cv2.dilate(np.where(band, W, -1e9).astype(np.float32), box,
                                                             anchor=(side, down_r)))
    has = np.isfinite(near)
    out = Z.copy()
    # every character there in front of the original's tile (or no tile): the scene behind them all (behind the
    # farthest feet, so all of them; where it's already about there, they show anyway)
    shown = has & (far <= front) & (Z < far_feet - CLEAR_GAP)
    out[shown] = far_feet[shown] + 1.5 * CLEAR_GAP
    # every one behind the tile: the scene just in front of them all (no nearer than the tile: tiles of depth 1 mean
    # "always in front", not a place). On the floor, only the bodies behind its walkway count (near_b)
    has_b = np.isfinite(near_b)
    hidden = has_b & (near_b > front) & (Z > near_b - thick)
    out[hidden] = np.maximum(front, near_b - thick - 0.5 * CLEAR_GAP)[hidden]
    # some in front of the tile, some behind: exactly at the tile's depth
    split = has_b & (near_b <= front) & (far > front) & np.isfinite(front)
    out[split] = front[split]
    # around the bodies, within what they hold, where the original has nothing to cover them (or only behind them):
    # the scene behind them too, but not the floor (it's under them: snap_to_walkmesh)
    reached = ~(shown | hidden | split) & ~floor & (far_reach > -1e8) & (front > far_reach + CLEAR_GAP) & \
        (Z < far_reach - CLEAR_GAP)
    out[reached] = far_reach[reached] + 1.5 * CLEAR_GAP
    if bodies is not None:
        bodies.update(near=near, far=far, thick=thick)
    return out, {"shown": float(shown.mean()), "hidden": float(hidden.mean()), "split": float(split.mean()),
                 "reached": float(reached.mean())}


# The game draws field models, and FFNx-3d the scene mesh, with the field's projection, read from the game's memory
# (2026-11-02): near plane 512 camera units in some fields (hyou10-12, icedun_1/2, junbin3, mds7pb_1), 50 in others
# (no field data found that tells which), far plane about 32000 (31195-32386 across 20 fields). Scenery past either was
# cut away and the clear colour showed: black shapes (hyou11's rock, junbin3's ceiling fixture: nearer than 512;
# junone7's "fish" on the horizon: farther than 32000).
NEAR_PLANE = 512.0
NEAR_CLEAR = 1.04 * NEAR_PLANE  # clear_near_plane moves scenery at least this far
FAR_CLEAR = 28000.0             # clear_far_plane: nothing farther (10% inside the nearest far plane seen)
FAR_KNEE = 18000.0              # ...and depths from here on brought in smoothly, in order


def clear_near_plane(Z, bodies=None):
    """Scenery nearer than the game's near plane (NEAR_PLANE) moved back along its camera rays to NEAR_CLEAR: the
    picture stays the same. Where a character can stand behind it (bodies: match_original_hiding's nearest body that
    can cover each grid point), only as far as just in front of them, so it still hides them; where they can only be
    in front of it, any distance. Fields whose near plane is 50 look the same. Returns (Z, stats: share of the grid
    moved, share still nearer)."""
    near = bodies["near"] if bodies else np.full(Z.shape, np.inf, np.float32)
    far = bodies["far"] if bodies else np.full(Z.shape, -np.inf, np.float32)
    thick = bodies["thick"] if bodies else 0.0
    to = np.full(Z.shape, NEAR_CLEAR, np.float32)
    ahead = Z < near  # in front of every character there (or none can be there)
    to[ahead] = np.minimum(NEAR_CLEAR, near[ahead] - thick - 0.5 * CLEAR_GAP)
    to[~ahead & ~(Z > far)] = -np.inf  # characters on both sides of it: it stays
    out = np.where(Z < NEAR_CLEAR, np.maximum(Z, to), Z)
    return out, {"moved": float((out > Z).mean()), "left": float((out < NEAR_PLANE).mean())}


def clear_far_plane(Z):
    """Depths past FAR_KNEE brought in along their camera rays, smoothly and in order, to under FAR_CLEAR (inside the
    game's far plane): the far sea and sky, nothing anyone walks on. Returns (Z, share of the grid moved)."""
    span = FAR_CLEAR - FAR_KNEE
    out = np.where(Z > FAR_KNEE, FAR_KNEE + span * (1 - np.exp(-(Z - FAR_KNEE) / span)), Z)
    return out, float((Z > FAR_KNEE).mean())


PARALLAX_MARGIN = 0.08  # parallax_order: the scene this much (of the depth) in front of or behind layer 3


def parallax_order(f, Z, step):
    """Where a layer 3 the game draws at a depth a script set (glow.layer3_depth) can come over the scene, the scene's
    depth in front of it or behind it as the original's tiles are in 2D, along the camera rays (the picture is the
    same): a layer-1 tile nearer than the layer hides it (crater_1's cliff, at 1, in front of its wall at 3840),
    layer 0, a farther layer-1 tile and layer 2 don't (hill's ground, layer 0 and 4094, under its Highwind at 512:
    46% of it within the Highwind's reach was estimated nearer). The layer's tiles the game draws: solid ones when
    the layer is drawn by the game (glow.drawn_layers), and blended ones it scrolls or switches (trnad_3's mist at
    4080, scrolled: the far cliffs, estimated past it, showed it over them as a pale rectangle; in 2D the cliff's
    tiles, at 1072 and up, hide it). Only within the layer's reach: its canvas footprint grown by how far the camera
    moves it (glow.layer_reach), everywhere for an effect a script keeps scrolling (not hill's Highwind, which a
    cutscene moves). For the mesh's depth (Zm). Returns
    (Z, {"behind", "front": share of the grid moved}) or (Z, None)."""
    d3 = glow.layer3_depth(f)
    if d3 is None or f.variant:
        return Z, None
    drawn = glow.drawn_layers(f) if glow.backdrop_alpha(f) is not None else set()
    moving = glow.scrolling_layers(f)
    by_game = [t for t in f.bg.tiles if t.layer == 3 and ((not t.blending and 3 in drawn)
                                                           or (t.blending and (t.param or glow.scrolling(f, t, moving))))]
    if not by_game:
        return Z, None
    mine = {id(t) for t in by_game}
    zt = TILE_DEPTH * d3
    in_render, animated = glow.entry_states(f)
    cache = PageCache(f.bg)
    key = np.full((f.h, f.w), np.inf, np.float32)  # the front scene tile's place in the game's order (tile depths)
    reach = np.zeros((f.h, f.w), np.uint8)
    for t in f.bg.tiles:
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        a = cache.tile(t)[..., 3][:f.h - y, :f.w - x] > 0
        if id(t) in mine:
            reach[y:y + s, x:x + s][a] = 1
        elif t.blending or t.layer == 3:
            continue
        elif t.layer not in drawn and (not t.param or (in_render(t) and t.param not in animated)):
            k = {0: 5000.0, 2: 6000.0}.get(t.layer, float(t.z))  # layer 0 (z 0.9997) and 2 (0.9998) behind any layer 1
            blk = key[y:y + s, x:x + s]
            blk[a] = np.minimum(blk[a], k)
    rx, ry = glow.layer_reach(f, 3)
    if any(glow.scrolling(f, t, moving) for t in by_game):  # an effect a script keeps scrolling over all of it
        reach[:] = 1
    reach = cv2.dilate(reach, np.ones((2 * int(np.ceil(ry)) + 1, 2 * int(np.ceil(rx)) + 1), np.uint8)) > 0
    GH, GW = Z.shape
    g = lambda m: cv2.resize(m.astype(np.uint8), (GW, GH), interpolation=cv2.INTER_NEAREST) > 0
    held = g(np.isfinite(key) & reach)
    front = g(key < d3) & held
    behind = held & ~front
    out = Z.copy()
    far, near = zt * (1 + PARALLAX_MARGIN), zt * (1 - PARALLAX_MARGIN)
    push, pull = behind & (out < far), front & (out > near)
    out[push], out[pull] = far, near
    return out, {"behind": float(push.mean()), "front": float(pull.mean())}


OVERLAY_PUSH_CAP = 3.0  # no further back than this (a tile whose z isn't a real depth would fold the scene up)
LIT_PUSH_CAP = 8.5      # ...behind a toggled glow layer's tiles (plan["lit"]): blin3_1's message board is at a
                        # quarter of its tiles' depth (z 3810 x TILE_DEPTH: x4.4), and with no painted message left in
                        # the render, the board hid every message the game drew. zz4's crystal glow (z 4094 over the
                        # crystal at a seventh of that: x6.9) lost 68% of its light at 6, and fship_4, junonr1, kuro_5,
                        # tunnel_5, hyou5_2, ghotin_2, gon_i, crater_2 (x6.3-8.3) all of it. blin65_2's room lights
                        # (z 4087: x11-16, most of the room) and the 20-odd fields like it: FFNx-3d draws such layers
                        # over the scene (glow.over_scene, F20)


def push_under(f, Z, plan, step, over=()):
    """Behind each blended overlay tile (glow.overlay_plan), wherever the original shows it over the scene, move the
    scene back along its camera rays just behind the tile's depth, so the game's depth test lets the tile through as
    in 2D. Front-most tiles (smoke, haze, light shafts at depth ~0) need nothing. Solid tiles need nothing either:
    FFNx-3d draws them over the scene before the characters (deferred.cpp draw_scene_overlays), so the scene keeps
    its true depth for when they're off. Their depth is often only for sorting (del1's ship: 4094, five times the
    pier's in front of it), which the cap had left hidden behind the scene. The toggled glow layers' tiles too
    (plan["lit"]: palette-animated and switched by a script, the game draws them with all their light), with a
    cap of their own (LIT_PUSH_CAP); push_back's box with its foreground rule left blin3_1's message board in front.
    Not the tiles of the layers FFNx-3d draws over the scene (`over`: (page, palette) pairs, glow.over_scene)."""
    g = f.scale / step
    out = Z.copy()
    lit = {id(t) for t in plan.get("lit", [])}
    for t in plan["tiles"] + plan.get("lit", []):
        behind = TILE_DEPTH * t.z * 1.08
        if behind <= 0 or not t.blending or (t.tex, glow.page_palette(f, t)) in over:
            continue
        cap = LIT_PUSH_CAP if id(t) in lit else OVERLAY_PUSH_CAP
        n = int(round(t.size * g))
        r0, c0 = int(round((t.dst_y - f.my + f.frame[1]) * g)), int(round((t.dst_x - f.mx + f.frame[0]) * g))
        block = out[r0:r0 + n, c0:c0 + n]
        if not block.size:
            continue
        v = cv2.resize(plan["vis"][id(t)].astype(np.float32), (n, n), interpolation=cv2.INTER_LINEAR)
        v = v[:block.shape[0], :block.shape[1]]
        move = (v > 0.5) & (block < behind) & (behind <= cap * block)
        block[move] = behind
    return out


def ambient_occlusion(f, Pw, Z, n, step, radius=(40.0, 120.0, 300.0), samples=24, seed=0):
    """Screen-space AO on the depth grid: hemisphere samples at a few radii (world units), occluded when the depth
    map has something in front of the sample point."""
    GH, GW = Z.shape
    rng = np.random.default_rng(seed)
    occl = np.zeros((GH, GW), np.float32)
    total = 0
    for r in radius:
        for _ in range(samples):
            v = rng.normal(size=3)
            v /= np.linalg.norm(v)
            dirs = np.where((n @ v)[..., None] < 0, -v, v)
            p = Pw + dirs * rng.uniform(0.2, 1.0) * r
            s, pz = f.project(p.reshape(-1, 3))
            sx, sy = s[:, 0], s[:, 1]
            gu = (sx + f.frame[0] - f.mx) * f.scale / step
            gv = (sy + f.frame[1] - f.my) * f.scale / step
            ui = np.clip(np.round(gu).astype(int), 0, GW - 1)
            vi = np.clip(np.round(gv).astype(int), 0, GH - 1)
            inside = (gu >= 0) & (gu < GW) & (gv >= 0) & (gv < GH)
            zmap = Z[vi, ui].reshape(-1)
            blocked = inside & (zmap < pz - 4.0) & (pz - zmap < r * 2.5)
            occl += blocked.reshape(GH, GW)
            total += 1
    return 1.0 - occl / total


def water_mask(render):
    """Bright cyan/blue water, plus the pale foam next to it (de-lighting turns both dull grey)."""
    hsv = cv2.cvtColor(render, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    h, s, v = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    core = ((h > 165) & (h < 220) & (s > 0.35) & (v > 0.42)).astype(np.uint8)
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    near = cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    foam = ((v > 0.7) & (s < 0.4) & (near > 0)).astype(np.uint8)
    m = cv2.morphologyEx(core | foam, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), 2)


def look_light(o, look):
    """The look's light settings with their defaults filled in."""
    base = LOOK_LIGHT[look]
    return {k: base[k] if o.get(k) is None else float(o[k]) for k in ("shine", "light_tint")} |         {k: float(o[k]) for k in ("shading", "brightness", "saturation", "sun_turn", "sun_height")}


def lights(look, shading, brightness):
    """FFNx's sun and ambient strengths: shading moves light between them, brightness scales both."""
    base = LOOK_LIGHT[look]
    sun = base["sun"] * shading / 0.5
    ambient = max(base["ambient"] + (base["sun"] - sun) * SUN_AS_AMBIENT, 0.25 * base["ambient"])
    return {"light_intensity": round(sun * brightness, 3), "ambient_light_intensity": round(ambient * brightness, 3)}


def char_ambient(look, shading, brightness, fill):
    """The characters' ambient light (FFNx-3d's char_ambient): the scene's at fill 0, CHAR_FILL x brightness at 1."""
    scene = lights(look, shading, brightness)["ambient_light_intensity"]
    return round(max(0.0, scene + fill * (CHAR_FILL * brightness - scene)), 3)


def saturate(colour, k):
    lab = cv2.cvtColor(np.clip(colour, 0, 255).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    lab[..., 1:] *= k
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB) * 255


FILL_REACH = 8  # texels: how far the scene's colour is carried out over where it's transparent (fill_out)


def fill_out(colour, seen):
    """The scene's colour carried out over where it's transparent (`seen` 0): at its edges the texture is filtered
    with the texels beyond, and the shader's sky drift and ripples fetch from a few texels off, so those take the
    scene's own colour, not the render's painting of the backdrop the game draws there instead."""
    out = np.asarray(colour, np.float32).copy()
    hole = seen < 0.5
    if not hole.any():
        return out
    w = (~hole).astype(np.float32)
    num = cv2.GaussianBlur(out * w[..., None], (0, 0), FILL_REACH / 2)
    den = cv2.GaussianBlur(w, (0, 0), FILL_REACH / 2)
    near = hole & (den > 1e-4)
    out[near] = num[near] / den[near][..., None]
    return out


def write_look(f, look, o=None, out=None, over=None):
    """The scene's colour texture and material map, image-based light and lighting section for a look and its light
    settings; uses only cached work files, so it takes seconds (hot reload shows it in game). out: the scene's
    folder (default: the game's)."""
    o = o or options(f)
    ll = look_light(o, look)
    out = out or common.MESH / f.name
    tex = f"{f.name}_scene"
    render = np.asarray(Image.open(f.render).convert("RGB"))
    extent = float((f.work / "extent.txt").read_text())
    # material: rough non-metal (R roughness, G metallic, B specular, A occlusion), water smoother and shinier. It
    # hardly varies: a quarter of the size does
    wet = life.water(f, render)  # the colour test, only where Qwen found water (teal floors, blue walls passed it)
    wm = cv2.resize(wet, (f.RW // 4, f.RH // 4), interpolation=cv2.INTER_AREA)
    gltfout.write_dds(out / f"{tex}_pbr.dds", surface.material(
        f, wm, ll["shine"], WATER_SHINE, float(o["occlusion"]), float(o["gloss"]), float(o["metal"])))
    surface.write_normals(f, out / f"{tex}_nml.dds", float(o["bump"]), grass=float(o["grass"]) > 0)
    # both looks show the painting; relit, the game relights it (painting x its light now / the painting's light).
    # (Relit used to be Marigold's de-lit colour: its painted shadows only half went and the colour step put the old
    # sunlight back as orange patches.) The painting in AI 2D's colours: the Studio's colour pass (keep_colors), which
    # its pages have and the render doesn't (glow.scene_colours)
    scene = glow.scene_colours(f, render)
    colour = scene.copy()
    off = f.work / "entry_off.npz"
    if off.exists():  # animations that start on: the game draws them, so the mesh shows what's beneath
        d = np.load(off)
        w = d["weight"].astype(np.float32)[..., None]
        colour = colour * (1 - w) + d["rgb"].astype(np.float32) * w
    glass = glow.through_glass(f)  # a backdrop AI 2D shows without the glass the render has painted over it
    if glass is not None:
        colour = colour * (1 - glass[1][..., None]) + glass[0].astype(np.float32) * glass[1][..., None]
    if ll["saturation"] != 1:
        colour = saturate(colour, ll["saturation"])
    # light shafts, haze, lamp halos: only what the render lacks of them (the AI saw them and mostly painted them)
    added = glow.light_added(f, float(o["static_glow"]))
    if added is not None:
        colour = colour + added
    # where the game draws a parallax layer (it moves apart from the scene: glow.drawn_layers), the scene is
    # transparent: FFNx-3d skips it there and the game's tiles show wherever nothing nearer is in front
    seen = glow.scene_alpha(f)
    alpha = np.full((f.RH, f.RW), 255, np.uint8)
    if seen is not None:
        colour = fill_out(colour, seen)
        alpha = np.where(seen >= 0.5, 255, 0).astype(np.uint8)
    rgba = np.dstack([np.clip(colour, 0, 255).astype(np.uint8), alpha])
    gltfout.write_dds(out / f"{tex}.dds", rgba)
    Image.fromarray(rgba[..., :3]).save(f.work / "colour.jpg", quality=88)
    sources, fx, light, _ = life.write(f, o, out)  # what the painting has: chimneys, fires, windows, sky, flags
    base = atmos.glow(f, colour, float(o["glow"]), out, lights=light["still"] if light else None, seen=seen)
    flickering = atmos.flicker(f, float(o["flicker"]), out, colour, base=base, lights=light, seen=seen)
    if flickering:  # its frames loop in order (blinking lights keep time)
        fx["glow_fps"] = life.GLOW_FPS
    vertical, horizontal, _ = atmos.sun(f) if o["sun"] == "painting" else (*ibl.DEFAULT_SUN, 0)
    relit = {}
    if look == "relit":  # its sun map, and the game's sun from the painting's (fitted with its cast shadows), moved
        relit, now, _ = relight.write(f, o, ll, out, tex)
        if now:
            vertical, horizontal = now
    else:
        relight.clear(out, tex)
    walk_depth = np.load(f.work / "walk_depth.npy")
    ibl.write(f, scene, walk_depth, ll["light_tint"], look, sun=(vertical, horizontal))
    # the characters' own surrounding light, from the painting's colours (the scene keeps its own, above)
    char_ibl = [ibl.envmap.IBL / f"field_{f.id}_char_{k}.dds" for k in "sd"]
    if float(o["char_light"]) > 0:
        ibl.write(f, scene, walk_depth, float(o["char_light"]), "painted", sun=(vertical, horizontal), suffix="_char")
    else:
        for p in char_ibl:
            p.unlink(missing_ok=True)
    # the characters' game-wide settings (GAMEWIDE) are in [fields], not here: only what this field has of its own
    char_grade, _ = atmos.grade(f, 1.0)  # the whole grade: [fields]' char_match takes some of it
    floor = atmos.floor_light(f, 1.0, out)  # the map; [fields]' floor_light_strength how much of it
    if floor.get("floor_light_strength", 0.0) > 0:
        floor.pop("floor_light_strength")
    haze_colour, haze_density, haze_start = atmos.haze(f)
    # the light layers the game draws over the scene (glow.over_scene, `over`: (page, palette) pairs): the build's
    # list, its pages cut to match; else the last build's (report.json: a build without the list leaves none)
    if over is None:
        rep = f.work / "report.json"
        over = [tuple(int(v) for v in k.split("_")) for k in
                (json.loads(rep.read_text()).get("glow_over_scene") or {} if rep.exists() else {})]
    over = {"glow_over_scene": [n for pq in sorted(over) for n in pq]} if over else {}
    ibl.lighting_section(f, extent, {
        **lights(look, ll["shading"], ll["brightness"]),
        "light_rotation_vertical": vertical, "light_rotation_horizontal": horizontal,
        "scene_shadow_strength": 0.0,  # not on the scene's ground: off from the walkmesh, the shadows slide away
        "haze_color": haze_colour, "haze_density": haze_density, "haze_start": haze_start,
        "char_grade_full": char_grade, **floor, **atmos.char_style(f), **atmos.game_light(f),
        "water_motion": round(2 * float(o["water_motion"]), 3),
        "walkmesh_extrude": round(atmos.BORDER_PX / atmos.floor_px(f), 1),
        **atmos.shadow_reach(f, atmos.BORDER_PX / atmos.floor_px(f)),
        **atmos.clouds(f, float(o["clouds"]), extent), **atmos.grass(float(o["grass"])), **fx, **relit, **over})
    write_fields_section()
    kind = atmos.particles(f, o["particles"], float(o["particle_amount"]), out, sources)
    if not f.variant:  # the game draws the original's scrolling layers (glow.write_scrolling); with the blizzard's
        glow.write_scrolling(f, grain=kind != "blizzard")  # particles, only its gusts of white
    (f.work / "look.txt").write_text(look)


def swap_in(tmp, dst, tries=10):
    """Put a whole folder in place of another at once: the game's watcher never sees half a scene. Retries while the
    game still has a file open."""
    for i in range(tries):
        shutil.rmtree(dst, ignore_errors=True)
        try:
            tmp.rename(dst)
            return
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(0.3)


def snapshot(f):
    """Keep a copy of what the build put in the game (scene mesh, override pages) in work/.../snapshot/."""
    snap = f.work / "snapshot"
    shutil.rmtree(snap, ignore_errors=True)
    shutil.copytree(common.MESH / f.name, snap / "mesh")
    shutil.copytree(common.PAGES / f.name, snap / "pages")
    (snap / "look.txt").write_text((f.work / "look.txt").read_text())


def use(name, variant=None):
    """Put a built variant's snapshot back into the game (the mesh reloads by itself; pages on field entry)."""
    f = common.Field(name, variant)
    snap = f.work / "snapshot"
    for src, dst in ((snap / "mesh", common.MESH / name), (snap / "pages", common.PAGES / name)):
        # copied whole into a side folder with fresh timestamps, then swapped in: the game's watcher never
        # sees a half-copied scene, and always sees the change
        tmp = dst.with_name(dst.name + ".incoming")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp, copy_function=shutil.copyfile)
        swap_in(tmp, dst)
    write_look(f, (snap / "look.txt").read_text())


def build(name, log=print, variant=None, stage=lambda share, what: None):
    """stage(share done 0-1, what's next) reports progress."""
    t0 = time.time()
    stage(0.0, "lining the render up with the original")
    f = common.Field(name, variant)
    if f.render is None:
        raise ValueError(f"{name}: no Studio render")
    o = options(f)
    step = grid_step(f)
    report = {"field": name, "id": f.id, "render": str(f.render), "size": [f.RW, f.RH], "step": step,
              "source": {"version": f.version, "render": str(f.raw_render), "mtime": f.raw_render.stat().st_mtime}}

    stage(0.1, "materials (Marigold)")
    marigold.run(f)
    stage(0.3, "depth (Depth Anything)")
    up = np.clip((marigold_normals(f, f.grid_shape(step))[..., 2] - 0.55) / 0.3, 0, 1)  # floor-like: faces up
    Z, W, fit = depth.camera_depth(f, step, smooth=o["anchor"], up=up)
    stage(0.45, "shaping the scene")
    report["fit"] = fit
    GH, GW = Z.shape
    bx, by = depth.grid_bg(f, step)

    Pc, _ = f.unproject(bx, by, Z)
    Z, snap, snapped = snap_to_walkmesh(f, Z, W, Pc, o, up=up, step=step)
    report["snap"] = snap
    Z0 = Z.copy()  # depth breaks are judged before the glow push: that keeps every point on its camera ray

    anim = glow.layers(f)
    regions = [glow.grid_rect(f, r, step) for r in anim["rects"]]
    Zb = push_back(Z, regions)
    plan = glow.overlay_plan(f, set(anim["glowing"]))
    # characters hidden as in the original (`reveal`); climbers on ladders always (the painting's wall is often
    # estimated in front of its ladder: mds7st1's train cars)
    walkers = ladder_layers(f, step, GH, GW) + (list(depth.walkmesh_layers(f, step)) + placed_layers(f, step, GH, GW)
                                                 if o["reveal"] else [])

    def shaped(over):
        """The scene's depth Z and the mesh's Zm, the scene moved back under the blended tiles but those of the layers
        drawn over it (`over`), and where the glow pushes (push_back, push_under) moved it."""
        Z = push_under(f, Zb, plan, step, over)
        pushed = Z != Z0
        bodies = {}
        if walkers:
            Z, report["walk_clearance"] = match_original_hiding(f, Z, walkers, step, up=up, floor=snapped > 0.5,
                                                                bodies=bodies, floor_at=W)
        # the mesh's depth: inside the game's near and far planes; the rest (contact shadows, the extent,
        # simplify_in.npz for the haze, the sun, life and surface) keeps the scene's own
        Zm, report["near_plane"] = clear_near_plane(Z, bodies)
        Zm, report["far_plane"] = clear_far_plane(Zm)
        Zm, report["parallax_order"] = parallax_order(f, Zm, step)  # a layer 3 the game draws among the scene
        return Z, Zm, pushed

    Z, Zm, pushed = shaped(())
    # light layers the mesh still hides where the original shows them (tiles far behind it): the game draws them over
    # the scene (glow.over_scene; the look step lists them in the lighting section, from report.json), so the scene
    # keeps its own depth under all of their tiles: moved back under the ones within the cap, junbin4's scene came out
    # 5.5 times as wide (its extent sizes the shadow map)
    plan["over"] = glow.over_scene(f, anim, plan, Zm, step, lambda t: TILE_DEPTH * t.z)
    if plan["over"]:
        Z, Zm, pushed = shaped(set(plan["over"]))
    report["glow_over_scene"] = {f"{p}_{q}": share for (p, q), share in sorted(plan["over"].items())}
    report["glow_layers"] = anim["summary"]
    report["overlays"] = {"tiles": len(plan["tiles"]), "params": sorted({t.param for t in plan["tiles"]}),
                          "start_on": len(plan["entry_on"]), "backdrop": len(plan.get("backdrop", []))}
    # the scene where it stands, not where the glow pushes (push_back, push_under) moved it back behind tiles: what's
    # worked out from its shape (contact shadows, the extent, simplify_in.npz for the haze, the sun, life and surface).
    # psdun_4's sky, pushed 8.5 times as deep under its light layers, took the mist in front of it along: the puffs'
    # size, set for their median depth, made those in front of the rocks 7 times as big (pale cut-off patches) (F25)
    Zs = np.where(pushed, Z0, Z)
    Pc, Pw = f.unproject(bx, by, Z)
    Pcm, Pm = f.unproject(bx, by, Zm)
    _, Ps = f.unproject(bx, by, Zs)

    n = marigold_normals(f, (GH, GW))
    stage(0.55, "contact shadows")
    ao = ambient_occlusion(f, Ps, Zs, n, step)
    np.save(f.work / "ao.npy", ao)

    render = np.asarray(Image.open(f.render).convert("RGB"))
    jw = np.zeros((GH * GW, 4), np.uint8)
    ww = np.tile(np.array([1, 0, 0, 0], np.float32), (GH * GW, 1))
    anim_kw = {}
    if o["sway"]:
        fm = sway.flower_mask(render, GW, GH)
        s = sway.weights(fm, sway.heights(Pw, n[..., 2] > 0.75, fm))
        bi, bf, along, wd = sway.bones(Pw, s, count=WIND_BONES)
        jw = np.stack([np.zeros_like(bi), 1 + bi, 2 + bi, np.zeros_like(bi)], -1).reshape(-1, 4).astype(np.uint8)
        ww = np.stack([1 - s, s * (1 - bf), s * bf, np.zeros_like(s)], -1).reshape(-1, 4).astype(np.float32)
        off = sway.motion(WIND_FRAMES, along, wd)
        tr = np.zeros((WIND_FRAMES, 1 + WIND_BONES, 3), np.float32)
        tr[:, 1:, 0], tr[:, 1:, 1] = off[..., 0], off[..., 1]
        anim_kw = {"bones": WIND_BONES, "anim": {"name": "wind", "translation": tr}}

    # triangles: two per grid cell, cells across depth breaks set aside to fill as cracks
    stage(0.65, "simplifying the mesh")
    idx = np.arange(GH * GW).reshape(GH, GW)
    q = np.stack([idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]], -1).reshape(-1, 4)
    tris = np.concatenate([q[:, [0, 2, 1]], q[:, [1, 2, 3]]])
    zt = Z0.reshape(-1)[tris]
    cut = (zt.max(1) - zt.min(1)) / zt.min(1) >= o["crack"]
    report["cracks"] = float(cut.mean())
    cut0, cut1 = cut.reshape(2, GH - 1, GW - 1)
    # simplified: big flat cells where they do, the full grid's triangles (and cracks) elsewhere
    level = np.zeros((GH - 1, GW - 1), np.int16)
    if o["simplify"]:
        level = simplify.cell_levels(Zm, cut0 | cut1, ww.reshape(GH, GW, 4) if o["sway"] else None, o["depth_tol"],
                                     o["uv_tol"] * f.scale / step, max_level=o["max_level"])
    unit_cell = np.tile((level == 0).reshape(-1), 2)
    gaps = tris[unit_cell & cut]
    tris = np.concatenate([tris[unit_cell & ~cut], simplify.triangulate(level, GW)])
    report["full_triangles"] = int(len(q) * 2)
    report["cell_area"] = {f"{1 << l}": float((level == l).mean()) for l in range(int(level.max()) + 1)}
    np.save(f.work / "levels.npy", level)
    # (Z_drawn: as the mesh has it, for surface.steady: a push's edge is a break in the mesh)
    np.savez_compressed(f.work / "simplify_in.npz", Z=Zs, Z_drawn=Z, broken=cut0 | cut1, grid=f.scale / step)

    ys, xs = np.mgrid[0:GH, 0:GW].astype(float)
    uv = np.stack([xs * step / f.RW, ys * step / f.RH], -1).reshape(-1, 2)
    # vertex normals: Marigold's as they are on the full grid; smoothed on a simplified mesh, whose normal map
    # puts the detail back
    nv = unit(np.stack([cv2.GaussianBlur(n[..., k], (0, 0), 4) for k in range(3)], -1)) if o["simplify"] else n
    pw, nw = Pm.reshape(-1, 3), nv.reshape(-1, 3)
    used = np.unique(tris)
    remap = np.zeros(GH * GW, np.int64)
    remap[used] = np.arange(len(used))
    tris = remap[tris]
    full = {"pw": pw, "nw": nw, "uv": uv, "jw": jw, "ww": ww}
    pw, nw, uv, jw, ww = (full[k][used] for k in ("pw", "nw", "uv", "jw", "ww"))

    # cracks: each gap triangle as its own copy, corners slid back along their rays to the farthest corner's depth,
    # so it sits behind the near object and shows the painting there
    if len(gaps):
        Zf, Pcf = Zm.reshape(-1), Pcm.reshape(-1, 3)
        zg = Zf[gaps]
        back = (Pcf[gaps] * (zg.max(1, keepdims=True) / zg)[..., None]).reshape(-1, 3)
        far = gaps[np.arange(len(gaps)), zg.argmax(1)]
        n0 = len(pw)
        pw = np.concatenate([pw, (back - f.T) @ f.Rinv.T])
        nw = np.concatenate([nw, np.repeat(full["nw"][far], 3, 0)])
        uv = np.concatenate([uv, full["uv"][gaps].reshape(-1, 2)])
        jw = np.concatenate([jw, full["jw"][gaps].reshape(-1, 4)])
        ww = np.concatenate([ww, full["ww"][gaps].reshape(-1, 4)])
        tris = np.concatenate([tris, n0 + np.arange(3 * len(gaps)).reshape(-1, 3)])

    # the scene goes together in a side folder and into the game at once (a cancelled build leaves the old one)
    out = common.MESH / f"{name}.incoming"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    tex = f"{name}_scene"
    if o["simplify"]:
        stage(0.75, "normal map")
        want = marigold_normals(f, (f.RH // 2, f.RW // 2))
        nml = simplify.normal_map(level, cut0, cut1, Pm, nv, want, step, f.RW, f.RH)
        gltfout.write_dds(out / f"{tex}_nml.dds", nml)
        gltfout.write_dds(f.work / "scene_nml_base.dds", nml)  # bump (surface.write_normals) starts from it
    gltfout.write(out / "scene.gltf", [{"pos": pw, "nrm": nw, "uv": uv, "idx": tris.reshape(-1), "image": 0,
                                        "color": (1, 1, 1, 1), "double_sided": True, "joints": jw, "weights": ww}],
                  images=[f"{tex}.png"], **anim_kw)

    stage(0.85, "game pages, glow and lighting")
    glow.write_pages(f, anim, plan)
    off = glow.entry_off_art(f, plan)
    if off is None:
        (f.work / "entry_off.npz").unlink(missing_ok=True)
    else:
        np.savez_compressed(f.work / "entry_off.npz", rgb=off[0], weight=off[1].astype(np.float16))
    # the scene's size (it sizes the shadow map, the clouds and the relit light's reach) where it stands (Zs): at
    # LIT_PUSH_CAP 8.5 crater_2's came out 7 times as wide, gon_i's 20 (the shadow map's texels as many times as
    # coarse: gon_i's characters lost their own shadows)
    extent = float(np.ptp(Ps.reshape(-1, 3)[:, :2], axis=0).max())
    (f.work / "extent.txt").write_text(str(extent))
    stage(0.9, "chimneys, fires, flags and sky (Qwen)")
    try:
        report["life"] = {k: len(v) if isinstance(v, list) else v for k, v in life.find(f).items()}
    except Exception as e:  # the scene still builds without them
        log(f"{name}: finding chimneys, fires and flags failed: {e}")
    # the look as the field has it now: the 3D tab may have changed it while this ran (a minute or two), and writing
    # back what was read at the start put Relit back to Painted
    o = options(f)
    write_look(f, o["look"], o, out, over=plan["over"])
    report.update(vertices=len(pw), triangles=len(tris), extent=float(extent), built=time.time(),
                  seconds=round(time.time() - t0, 1), bytes=sum(p.stat().st_size for p in out.iterdir()))
    swap_in(out, common.MESH / name)
    (f.work / "report.json").write_text(json.dumps(report, indent=1))
    snapshot(f)
    log(f"{name}: fit {fit['median_err']:.1%} (walkmesh covers {fit['walk_coverage']:.0%}), {len(tris) / 1e6:.2f}M "
        f"triangles (full grid {report['full_triangles'] / 1e6:.2f}M), {report['cracks']:.1%} cracks, {anim['summary']}, "
        f"{len(plan['tiles'])} overlay tiles")
    return report
