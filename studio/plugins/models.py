"""FF7 Field Studio plug-in: Models (docs/models-plan.md, "Studio").

Adds a Models section next to the fields: the battle scenes, the battle characters and enemies, and the field
characters and objects, each with a thumbnail, a viewer you can turn around (Original / Painted / Split; a battle
scene from any of its battle cameras), Preview and Final paint jobs on the Studio's own queue, versions as the fields
have them, and for battle scenes and battle models Install / Uninstall into the game's FFNx texture overrides.

A paint job renders the model (or battle scene) from several angles, restyles those pictures with one of the
Studio's looks and projects them back onto the textures (ff7bg/models: render, paint, restyle). Battle scenes look
from their battle cameras plus a few angles around the arena; models from all round. Battle scenes and models paint
their own textures (AI-enlarged first); field models paint their atlas (field.atlas: every triangle gets texture
space, at its final size).

Nothing is installed by itself: installing changes how the game looks, so it is always the user's click. Field
models aren't installed at all yet (external meshes still need an engine change for the model's size in some fields,
and they would replace the chibi models in use).

A paint's method is the user's choice per model (METHODS): flat (every texture as a sheet; battle scenes' default),
projected (views mapped back; models' default) or hybrid (repeated textures flat, the rest projected). Battle scenes
painted Flat or Hybrid also take the sky as one panorama, the ground bigger with the detail pass, and blended seams
(scenepaint; each on by default, the user's to turn off). "Try it in a battle" starts one of a scene's
battles in the running game through FFNx-3d's live control (<workingdir>/ffnx3d/cmd.txt: god, autoattack, battle),
only ever from the user's click.

Each model has its own paint settings (model.json "paint", PAINT_DEFAULTS): paint strength, input scale (how much the
original textures are enlarged before painting), output scale (the size the game gets), the user's words and the
automatic description on or off, and the upscalers: the one that enlarges the original textures before painting and
the one that enlarges the painting when the game gets it bigger. A job takes them as they are when it's queued; each
version records them.

A battle scene's "Battle look" (ff7bg/models/battlelight.py; docs/models-plan.md, "Battle scenes beyond textures"): from
the ★ painted version, the light (taken from the painting), haze and the characters' colour match, life (drifting sky,
swaying grass, particles) and surface relief, for FFNx-3d. Build is a queue job (JOB_LOOK; the AI parts, life and
relief, wait for the game to be closed); Install and Uninstall are the user's clicks only, with the parts they tick.

A battle model's "Paint in 2D style" (ff7bg/models/battleatlas.py; docs/models-plan.md, "Painted textures for
untextured battle models"): one painted texture over every triangle of the model (FFNx-3d's battle atlas), so models
without textures (most enemies) can be painted too, in the 2D anime style the user picked for battles. Its own settings
(model.json "paint2d", P2D_DEFAULTS), a queue job (JOB_2D: battleatlas' build, paint and export into a new version
folder, work/models/battle/atlas/<id>/<version>/), the versions there (the ★ one: model.json "current2d") and Install /
Uninstall (battleatlas' manifest; the user's clicks only). The engine lights atlas-drawn parts flat by itself.
Its extras (ff7bg/models/paint2d_extras.py): Variants (one job paints several versions, each with its own seed), the AI
check after each (the Studio's describer compares the painted model with the original from the same angles: check.json
in the version; a failed one is repainted with a new seed when "Repaint automatically" is on, up to P2D_RETRIES more
tries) and Touch up (a version's view repainted where the user marks it, projected back: a new version; JOB_2D_TOUCH).
The viewer shows 2D versions on the model too (ver "2d:<version>"; battle models' and field models' fieldatlas builds)
as the game draws them: flat light, texels at half alpha or less cut out, field models rounded as char_round does
(paint2d_extras.pn_round), a battle model in any animation's first pose; and it can be moved (pan) as well as turned.
"Touch up this view" stores the viewer's camera with the version (views.json, "cam<hash>") for the touch-up.

SYW Unified Battle Textures (Tsunamods, in 7th Heaven's library; restyle.syw_battle_*): its ready-made 4x of a model's
textures can be the upscaler before painting (battle scenes and battle models; the default upscaler for any it lacks),
and the viewer's SYW mode shows it. Square Enix's art upscaled by the SYW team: for use on this PC.

Files (ai/models/):
- <kind>/<id>/model.json: {"current": version id, "look": look id or null, "error": last failure or null}
- <kind>/<id>/<version id>/: meta.json, textures/<FFNx name>.png (battle/OQAC_00.png; a field model's atlas as
  <HRC>_atlas.png plus atlas_uv.npy, the atlas UVs it was painted on), views.jpg (every view, original | painted),
  thumb.jpg
- thumbs/<kind>_<id>.jpg: the original's list thumbnail
- installed.json: per installed model the files copied into the game and the backups of files they replaced
- backup/<kind>_<id>/: those backups
(a battle model's 2D paintings are battleatlas' own: work/models/battle/atlas/, its installed.json)

kind: "scene" (battle scene, id "oq"), "battle" (battle character or enemy, "rt"), "field" (field model, "AAAA")."""
import io
import json
import math
import os
import re
import shutil
import threading
import time
import tomllib
import traceback
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from dataclasses import replace
from functools import wraps
from pathlib import Path

import numpy as np
from flask import Blueprint, abort, jsonify, request, send_file
from PIL import Image

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "models.js"

KINDS = {"scene": "battle scene", "battle": "battle model", "field": "field model"}  # kind -> job name prefix
JOB_FINAL, JOB_PREVIEW = "model", "model_preview"
BG_MODEL = (17, 19, 23)  # the page's background behind models; battle scenes keep the game's black
VIEW_FOV = 30.0          # degrees (vertical) for model views
MAX_VIEW = 1600          # px: the largest viewer render
THUMB = (192, 144)
COVERAGE_MIN = 0.005     # a battle scene filling less of its main camera's view is empty (og, qo, rk)

# Painting methods (the user's choice per model, "method"; paint.paint):
# - flat: every texture painted on its own as a sheet (sheets, repeat 0); the views are only looked through (the first
#   for the description, all for the views sheet). The default for battle scenes: they lay almost every texture 2-20
#   times over, and projections only looked right from their own cameras (2026-11-07).
# - projected: pictures of the whole scene or model, mapped back onto its textures (no sheets). The default for models.
# - hybrid: the textures laid paint.REPEAT times over or more as sheets, the rest projected from the views.
# Battle scenes' views: their battle cameras (the most used first, 15 degrees apart at least) and, for projected and
# hybrid, battle.ring_cameras: all round from the middle of the action, looking out. Models: an orbit all round.
METHODS = ("flat", "projected", "hybrid")
SCENE_VIEWS = {
    "final": {"size": (1280, 960), "battle": 3, "ring": 6, "scale": 4},
    "preview": {"size": (960, 720), "battle": 2, "ring": 4, "scale": 2},
}
# rough seconds (ComfyUI with the model loaded): a view at 1280x960 and the projection's own work per view; a sheet
# per million pixels (a 256 px texture at 4x: about 4 s); the rest (loading, describing, saving)
VIEW_SECONDS, PROJECT_SECONDS, SHEET_SECONDS_MP, SHEET_SECONDS_BASE, OVERHEAD_SECONDS = 7.0, 3.0, 3.0, 1.0, 8.0
# Models: all round, a little from above (yaw from the front, pitch above the horizon, in degrees)
MODEL_ORBIT = {
    "final": [("front", 0, 12), ("front, turned left", 45, 12), ("front, turned right", -45, 12),
              ("left side", 90, 10), ("right side", -90, 10), ("back", 180, 12), ("from above", 0, 55)],
    "preview": [("front", 0, 12), ("left side", 90, 10), ("back", 180, 12), ("right side", -90, 10)],
}
MODEL_VIEWS = {"final": {"size": (1024, 1024), "scale": 4}, "preview": {"size": (768, 768), "scale": 2}}
# seconds a paint takes before any has run (then: the last ones' average)
# The user's own paint settings, per model (model.json "paint"; each version records what it used):
# strength: the repaint, None = the look's own; input: how many times the original textures are enlarged before the AI
# paints them, None = Preview 2x / Final 4x (battle scenes and battle models; field models paint their atlas at its own
# size); output: the size the game gets, times the original texture, None = the same as the input (field models: times
# the atlas, None = 1); prompt: the user's words; describe: the automatic description, None = the look's own.
# up_in / up_out: the upscaler that enlarges the original textures to the input scale, and the one that enlarges the
# painting when the game gets it bigger (None = restyle.DEFAULT_UPSCALER, 4x-UltraSharp; "lanczos" = a plain resize;
# any of the Studio's upscalers: a ComfyUI model file, "seedvr2", "magick", "upscayl:<model>")
# panorama / detail / seams (battle scenes painted Flat or Hybrid; scenepaint): the sky and far scenery as one
# picture all round (None = on), the ground painted bigger with the detail pass (None = 2x; 1 off, 2, 4), the colours
# blended where pieces meet (None = on)
PAINT_DEFAULTS = {"strength": None, "input": None, "output": None, "prompt": "", "describe": None,
                  "up_in": None, "up_out": None, "method": None, "panorama": None, "detail": None, "seams": None}
DETAILS = (1, 2, 4, 8, 16)
# the longest side any texture the game gets may have (output scale, ground detail): restyle.MAX_SHEET_SIDE. The game is
# a 32-bit program: a 16384 px texture is about 1 GB decoded, and it has run out of address space before
MAX_SIDE = 8192
BIG_SIDE = 4096  # textures over this: the page warns that the game may run short of memory with many of them
PANORAMA_SECONDS, SEAMS_SECONDS = 10.0, 3.0
DETAIL_TILE_SECONDS = 3.7  # the detail pass, per 1024 px tile of a ground texture painted bigger
DEFAULT_UPSCALER = "4x-UltraSharp.safetensors"  # restyle.DEFAULT_UPSCALER
SYW = "syw"  # restyle.SYW: SYW Unified Battle Textures' ready-made 4x (7th Heaven), before painting only
STRENGTH_RANGE = (0.2, 0.9)
INPUT_SCALES = (1, 2, 4, 8)
OUTPUT_SCALES = (1, 2, 4, 8, 16)
FIELD_OUTPUT_SCALES = (0.5, 1, 2)
PROMPT_MAX = 400
REPAINT_FAMILIES = {"flux1", "krea2", "moss"}  # the workflows with a repaint strength (studio.REPAINT_FAMILIES)


def clean_paint(kind, ps):
    """A model's paint settings, checked, every key there (PAINT_DEFAULTS for anything missing or wrong)."""
    ps = ps if isinstance(ps, dict) else {}
    out = dict(PAINT_DEFAULTS)
    v = ps.get("strength")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out["strength"] = round(min(max(float(v), STRENGTH_RANGE[0]), STRENGTH_RANGE[1]) * 20) / 20
    if kind != "field" and ps.get("input") in INPUT_SCALES:
        out["input"] = int(ps["input"])
    if ps.get("output") in (FIELD_OUTPUT_SCALES if kind == "field" else OUTPUT_SCALES):
        out["output"] = ps["output"]
    if isinstance(ps.get("prompt"), str):
        out["prompt"] = " ".join(ps["prompt"].split())[:PROMPT_MAX]
    if isinstance(ps.get("describe"), bool):
        out["describe"] = ps["describe"]
    for k in ("up_in", "up_out"):
        v = ps.get(k)
        if isinstance(v, str) and v.strip() and len(v) <= 200 and v != DEFAULT_UPSCALER and (v != SYW or k == "up_in") \
                and ".." not in v and not any(c in v for c in "\r\n\t"):  # model files may sit in subfolders
            out[k] = v.strip()
    if kind == "scene":  # the extras: battle scenes only (stored only when not the default)
        if isinstance(ps.get("panorama"), bool):
            out["panorama"] = None if ps["panorama"] else False
        if ps.get("detail") in DETAILS:
            out["detail"] = None if ps["detail"] == 2 else int(ps["detail"])
        if isinstance(ps.get("seams"), bool):
            out["seams"] = None if ps["seams"] else False
    if ps.get("method") in METHODS and kind != "field":  # field models: projected only (their atlas is cut-up pieces)
        out["method"] = None if ps["method"] == default_method(kind) else ps["method"]
    if kind == "field":
        out["up_in"] = None  # a field model's atlas is painted at its own size: nothing is enlarged first
    return out


def default_method(kind):
    return "flat" if kind == "scene" else "projected"


# The battle look (battlelight): the page's four parts and the build's parts in each; the AI's (ComfyUI: Qwen,
# Marigold), which wait for the game to be closed (battlelight.AI_PARTS)
LOOK_GROUPS = {"light": ("light",), "haze": ("haze", "grade"), "life": ("life",), "normals": ("normals",)}
LOOK_AI = ("life", "normals")
JOB_LOOK = "model_look"
LOOK_PREVIEWS = ("preview_light.png", "preview_view.jpg", "preview_normals.jpg")
# rough seconds (measured on oq and oo, 2026-11-07: light alone 4.6 s, haze and colour match 1.3 s, relief about 2 s a
# Marigold tile): loading the scene, the view all round (light and haze share it), each part; life asks the AI a few
# questions (its model loading first); relief per Marigold tile (the sheet at half size in tiles of up to 768 px)
LOOK_SECONDS = {"load": 3.0, "view": 2.0, "light": 1.0, "haze": 0.5, "grade": 1.5, "life": 1.0, "life_ai": 25.0,
                "normals": 15.0, "normals_tex": 1.0, "normals_tile": 2.0}
RELIEF_CELL = 384  # px: the relief preview's pictures (painted | relief), longest side
RELIEF_LIGHT = (-0.45, -0.45, 0.77)  # ...lit from the top left (texture space: x along, y down, z out)
LIVE_FRESH = 5.0     # s: the game's status.txt (rewritten twice a second by FFNx-3d) is older than this: not running
LIVE_PICKUP = 3.0    # s: how long the game gets to take a command file before it's taken back
LIVE_ANSWER = 1.5    # s: ...and then to write its answer in log.txt
SUMMARY = ("family", "denoise", "keep_colors", "depth_lock", "describe_scene", "steps", "cfg", "seed", "prompt",
           "work_mp", "style_refs", "style_ref_strength")

# Paint in 2D style (battle models; battleatlas): the user's settings per model (model.json "paint2d"; None: battleatlas'
# own, LOOK painterly, STRENGTH 0.55, ATLAS_SIZE 2048), the job, a version's pictures (its preview/ folder: battle.jpg
# the resting battle camera, original | painted; turntable.jpg all round; vs_target.jpg against the 2D target, for the
# models in it (Zolom, Cloud, Barret); moving.jpg its separately moving parts (Zolom's tongue); identity.png the game's
# own colours on the new texture against the original) and each view the AI painted (view_NN.jpg, original | painted)
JOB_2D = "model_2d"
JOB_2D_CHECK, JOB_2D_TOUCH = "model_2d_check", "model_2d_touchup"  # the AI check of a version, a touch-up of one
# variants: how many versions a paint makes (None: 1); retry: a version the AI check fails is painted again with a new
# seed, up to P2D_RETRIES more times (None: on)
P2D_DEFAULTS = {"look": None, "strength": None, "words": "", "size": None, "variants": None, "retry": None}
P2D_SIZES = (1024, 2048, 4096)  # the game is 32-bit: 4096 px is 64 MB decoded (85 with its smaller copies)
P2D_VARIANTS = (1, 2, 3, 4)
P2D_RETRIES = 2
P2D_CHECK_SECONDS = 30.0   # the AI check of a version (10 pairs of pictures), before any has run here
P2D_TOUCH_SECONDS = 30.0   # a touch-up: a marked area or two repainted, the pictures, the files (then the check)
# check.jpg: what the AI check looked at; touchup.jpg: a touch-up's view before (the marks outlined) | after
P2D_PICTURES = ("battle.jpg", "turntable.jpg", "vs_target.jpg", "moving.jpg", "check.jpg", "touchup.jpg", "identity.png")
# a touch-up view (paint2d_extras.Battle.touch_views; "cam" + a hash: one from the viewer's angle)
P2D_VIEW_KEY = re.compile(r"(battle|yaw\d{3}|head\d|cam[0-9a-f]{8})")
P2D_TOUCH_STRENGTH = (0.2, 0.9)
P2D_VIEW = re.compile(r"view_\d{2}\.jpg")
P2D_VIEW_SECONDS = 5.0     # a view painted and put on, before any 2D paint has run here (Zolom: 61 s for 12 views)
P2D_VIEWS = 12             # ...and the views when the model's own can't be counted
P2D_EXTRA_SECONDS = 15.0   # the rest at 2048 px: laying out the texture, the check, the pictures, saving
P2D_SEARCH = {"painted": "2d painted", "game": "2d in game"}  # the list's search words per 2D status


def clean_p2d(ps, looks=None):
    """A model's 2D paint settings, checked, every key there (P2D_DEFAULTS for anything missing or wrong). looks: the
    Studio's look ids (a look that's gone falls back to the default)."""
    ps = ps if isinstance(ps, dict) else {}
    out = dict(P2D_DEFAULTS)
    v = ps.get("look")
    if isinstance(v, str) and v and (looks is None or v in looks):
        out["look"] = v
    v = ps.get("strength")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out["strength"] = round(min(max(float(v), STRENGTH_RANGE[0]), STRENGTH_RANGE[1]) * 20) / 20
    if isinstance(ps.get("words"), str):
        out["words"] = " ".join(ps["words"].split())[:PROMPT_MAX]
    if ps.get("size") in P2D_SIZES and not isinstance(ps.get("size"), bool):
        out["size"] = int(ps["size"])
    if ps.get("variants") in P2D_VARIANTS and not isinstance(ps.get("variants"), bool):
        out["variants"] = None if ps["variants"] == 1 else int(ps["variants"])
    if isinstance(ps.get("retry"), bool):
        out["retry"] = None if ps["retry"] else False
    return out


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


def _some(items, n):
    """"a, b, c." or "a, b, c and 12 more." """
    items = list(items)
    return ", ".join(items[:n]) + (f" and {len(items) - n} more." if len(items) > n else ".")


def _plain(name):
    """A name fit to show: scene.bin's test-formation enemies have placeholder names in other letters."""
    return bool(name) and all(c.isascii() and c.isprintable() for c in name)


def relief_preview(B, out, painted, st):
    """A battle look build's preview_normals.jpg (in `out`, its folder): up to three of its painted textures (from
    `painted`, the version's textures/battle), each next to its relief lit from the top left: raised parts catch the
    light. Big ones show their middle, at half size at most (smaller, fine relief averages away). B:
    ff7bg.models.battlelight; st: the build's settings."""
    rows = []
    L = np.array(RELIEF_LIGHT, np.float32)
    L /= np.linalg.norm(L)
    for rel in ((st.get("files") or {}).get("normals") or [])[:3]:
        p = Path(out) / rel
        src = Path(painted) / (p.name[:-len(B.NML)] + ".png")
        if not p.exists() or not src.exists():
            continue
        nm = np.asarray(Image.open(p).convert("RGBA"))
        h, w = nm.shape[:2]
        cw, ch = min(w, 2 * RELIEF_CELL), min(h, 2 * RELIEF_CELL)
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        nm = nm[y0:y0 + ch, x0:x0 + cw]
        k = min(1.0, RELIEF_CELL / max(cw, ch))
        size = (max(1, round(cw * k)), max(1, round(ch * k)))
        shade = np.clip(B.decode_normals(nm) @ L / L[2], 0, 2)  # a flat surface: 1
        grey = Image.fromarray(np.clip(shade * 165, 0, 255).astype(np.uint8)).resize(size, Image.LANCZOS)
        img = Image.open(src).convert("RGB")
        sx, sy = img.width / w, img.height / h  # the painting is bigger than its relief map
        img = img.crop((round(x0 * sx), round(y0 * sy), round((x0 + cw) * sx), round((y0 + ch) * sy)))
        row = Image.new("RGB", (2 * RELIEF_CELL + 6, size[1]), (17, 19, 23))
        row.paste(img.resize(size, Image.LANCZOS), (0, 0))
        row.paste(grey.convert("RGB"), (RELIEF_CELL + 6, 0))
        rows.append(row)
    if not rows:
        return None
    sheet = Image.new("RGB", (rows[0].width, sum(r.height for r in rows) + 6 * (len(rows) - 1)), (17, 19, 23))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 6
    sheet.save(Path(out) / "preview_normals.jpg", quality=88)
    return Path(out) / "preview_normals.jpg"


# ------------------------------------------------------------------------------------------------ cameras

def orbit_camera(render, target, dist, yaw, pitch, w, h, fov=VIEW_FOV, focal=None, near=None):
    """A camera `dist` from `target`, `yaw` degrees round from the front (models face -z: the front is on -z) and
    `pitch` degrees above (y is down)."""
    a, p = math.pi + math.radians(yaw), math.radians(pitch)
    eye = np.asarray(target, float) + dist * np.array([math.sin(a) * math.cos(p), -math.sin(p), math.cos(a) * math.cos(p)])
    return render.Camera(eye=eye, target=np.asarray(target, float), up=np.array([0.0, -1.0, 0.0]), fov_y=fov,
                         width=int(w), height=int(h), near=near or max(dist * 0.01, 1e-3), focal=focal)


def fit_distance(points, target, yaw, pitch, w, h, fov=VIEW_FOV, margin=0.92):
    """How far from `target` a camera at (yaw, pitch) must be to see every point within `margin` of the frame."""
    a, p = math.pi + math.radians(yaw), math.radians(pitch)
    back = np.array([math.sin(a) * math.cos(p), -math.sin(p), math.cos(a) * math.cos(p)])  # target -> eye
    f = -back
    up = np.array([0.0, -1.0, 0.0])
    r = np.cross(f, up)
    r /= max(np.linalg.norm(r), 1e-9)
    u = np.cross(f, r)
    rel = points - target
    ty = math.tan(math.radians(fov) / 2) * margin
    tx = ty * w / h
    need = np.maximum(np.abs(rel @ r) / tx, np.abs(rel @ u) / ty) - rel @ f
    return float(max(need.max(), 1e-3))


def turn(eye, target, dyaw, dpitch, zoom):
    """eye moved round target: dyaw degrees round the vertical, dpitch up (y down), distance / zoom."""
    v = np.asarray(eye, float) - np.asarray(target, float)
    r = float(np.linalg.norm(v))
    rh = math.hypot(v[0], v[2])
    yaw = math.atan2(v[0], v[2]) + math.radians(dyaw)
    pitch = min(max(math.atan2(-v[1], rh) + math.radians(dpitch), math.radians(-60)), math.radians(89))
    r = r / max(zoom, 1e-3)
    return np.asarray(target, float) + r * np.array([math.sin(yaw) * math.cos(pitch), -math.sin(pitch),
                                                     math.cos(yaw) * math.cos(pitch)])


# ------------------------------------------------------------------------------------------------ the plug-in

def register(studio):
    from ff7bg.models import atlas as atlasmod, battle, field, paint, render, restyle, scenebin

    project, worker = studio.project, studio.worker
    ROOT = Path(studio.AI) / "models"
    THUMBS = ROOT / "thumbs"
    INSTALLED = ROOT / "installed.json"
    BACKUP = ROOT / "backup"
    lock = threading.RLock()       # caches, model.json and installed.json
    send_lock = threading.Lock()   # one command file at a time to the game
    thumb_gate = threading.Semaphore(2)
    cat = {"battle": None, "field": None, "error": None, "loading": False}

    # ---------------------------------------------------------------- catalogues

    def load_catalogues():
        with lock:
            if cat["loading"] or (cat["battle"] is not None and cat["field"] is not None):
                return
            cat["loading"] = True
        try:
            b = battle.catalogue()
            f = field.catalogue()
            with lock:
                cat.update(battle=b, field=f, error=None)
        except Exception as e:  # the game files missing or unreadable: the page says so
            traceback.print_exc()
            with lock:
                cat["error"] = f"{type(e).__name__}: {e}"
        finally:
            with lock:
                cat["loading"] = False

    def ready():
        if cat["battle"] is None or cat["field"] is None:
            threading.Thread(target=load_catalogues, daemon=True).start()
            return False
        return True

    def entry(kind, mid):
        """The catalogue entry of a model, or 404."""
        if kind not in KINDS or not ready():
            abort(404)
        if kind == "field":
            e = cat["field"].get(str(mid).upper())
        else:
            e = cat["battle"].get(str(mid).lower())
            if e is not None and (e["kind"] == "scene") != (kind == "scene"):
                e = None
        if e is None:
            abort(404)
        return e

    def key_of(kind, e):
        return e["hrc"] if kind == "field" else e["id"]

    def title(kind, e):
        if kind == "scene":
            return f"Battle scene {e['id']}"
        if kind == "field":
            return e["name"]
        if e["kind"] == "party":
            return e["name"]
        return e["name"] if _plain(e["name"]) and e["name"] != e["id"] else f"Unnamed model {e['id']}"

    def scene_enemies(e):
        return [n for n in (e.get("enemies") or {}) if _plain(n)]

    def empty_scene(kind, e):
        return kind == "scene" and float(e.get("coverage") or 0) < COVERAGE_MIN

    def paintable(kind, e):
        """(can it be painted, why not in plain words)."""
        if empty_scene(kind, e):
            return False, "This battle scene is empty in the game's files (the game shows black there), so there's nothing to paint."
        if kind == "battle" and not e.get("textures"):
            return False, ("This model has no textures: all its colour comes from the model itself, so the Paint card "
                           "has nothing to paint on. The Paint in 2D style card can: it gives the whole model a painted "
                           "texture of its own.")
        return True, ""

    # ---------------------------------------------------------------- per model files

    def model_dir(kind, mid):
        return ROOT / kind / _safe_name(mid)

    def model_meta(kind, mid):
        p = model_dir(kind, mid) / "model.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"current": None, "look": None, "error": None}

    def save_model_meta(kind, mid, **changes):
        with lock:
            m = model_meta(kind, mid)
            m.update(changes)
            d = model_dir(kind, mid)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "model.json.tmp"
            tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
            tmp.replace(d / "model.json")
            return m

    def version_ids(kind, mid):
        d = model_dir(kind, mid)
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir()
                      if p.is_dir() and not p.name.endswith(".partial") and (p / "meta.json").exists())

    def version_meta(kind, mid, vid):
        p = model_dir(kind, mid) / _safe_name(vid) / "meta.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def new_version_id(kind, mid):
        base = time.strftime("%Y%m%d-%H%M%S")
        vid, n = base, 2
        while (model_dir(kind, mid) / vid).exists():
            vid, n = f"{base}-{n}", n + 1
        return vid

    def auto_words(v):
        """A version's automatic description: its "words", or (versions from before they were kept apart) its whole
        prompt text without the fixed hint in front."""
        if v.get("words") is not None:
            return v["words"]
        text = v.get("scene") or ""
        for hint in restyle.HINTS.values():
            if text.startswith(hint):
                text = text[len(hint):].strip()
        return text

    def paint_settings(kind, mid):
        return clean_paint(kind, model_meta(kind, mid).get("paint"))

    def default_input(kind, mode):
        return 1 if kind == "field" else (SCENE_VIEWS if kind == "scene" else MODEL_VIEWS)[mode]["scale"]

    def input_for(kind, mode, ps):
        return 1 if kind == "field" else (ps["input"] or default_input(kind, mode))

    def output_for(kind, inp, ps):
        """The game's textures, times the original (field models: times the atlas)."""
        return (ps["output"] or 1) if kind == "field" else (ps["output"] or inp)

    def auto_strength(s):
        """What Automatic paint strength means with this look: {"uses": whether its workflow has a repaint
        strength at all, "views": its own, "sheets": what texture sheets get (a share of it)}."""
        fam = s.get("family")
        d = float(s.get("denoise") or 0)
        return {"uses": fam in REPAINT_FAMILIES, "family": fam, "views": round(d, 2),
                "sheets": round(d * restyle.SHEET_REPAINT, 2)}

    def current(kind, mid):
        cur = model_meta(kind, mid).get("current")
        return cur if cur and (model_dir(kind, mid) / cur / "meta.json").exists() else None

    def installed_all():
        if INSTALLED.exists():
            try:
                return json.loads(INSTALLED.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {}

    def save_installed(data):
        INSTALLED.parent.mkdir(parents=True, exist_ok=True)
        tmp = INSTALLED.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(INSTALLED)

    # ---------------------------------------------------------------- the game folder

    def game_dir():
        return Path(studio.GAME_WORKDIR)

    def mod_root():
        """Where FFNx looks for texture overrides: the game's mod_path (FFNx.toml), "mods/textures" by default."""
        mod_path = "mods/textures"
        toml = game_dir() / "FFNx.toml"
        try:
            mod_path = tomllib.loads(toml.read_text(encoding="utf-8")).get("mod_path") or mod_path
        except (OSError, ValueError):
            pass
        return game_dir() / Path(mod_path)

    def mod_note(kind, e):
        """Plain words when the user's game has files in direct/battle that replace this model or its textures."""
        if kind == "field":
            return ""
        d = game_dir() / "direct" / "battle"
        try:
            mine = sorted(p.name.lower() for p in d.iterdir() if p.name.lower().startswith(e["id"]))
        except OSError:
            return ""
        if not mine:
            return ""
        tex_files = {t["file"] for t in e.get("textures", [])}
        parts = [n for n in mine if n not in tex_files]
        if parts:
            return ("A mod in your game (in direct\\battle) replaces this model. The painted textures fit the "
                    "original model, not the mod's, so in the game they may look wrong on it. Recommended: leave it "
                    "uninstalled while that mod is in.")
        game = {t["file"]: t["key"].split("/", 1)[-1] for t in e.get("textures", [])}  # rtad -> CLOUD_T01
        names = ", ".join(f"{game.get(n, n.upper())} ({n})" for n in mine)
        return (f"A mod in your game (in direct\\battle) replaces some of this model's textures: {names}. Installing "
                f"puts the painted ones over them.")

    # ---------------------------------------------------------------- meshes and views (cached)

    meshes = OrderedDict()   # (kind, id) -> dict
    painted = OrderedDict()  # (kind, id, version) -> dict

    def _remember(cache, key, value, size):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > size:
            cache.popitem(last=False)

    def loaded(kind, mid, keep=True):
        """The model's meshes and what the views need: mesh (as the game draws it), pm (what gets painted: the
        same for battle, the atlas mesh for field models), cams (a battle scene's), target and radius (models).
        keep: remember it (the viewer's); thumbnails don't, so browsing the list doesn't push the viewed one out."""
        key = (kind, mid)
        with lock:
            if key in meshes:
                meshes.move_to_end(key)
                return meshes[key]
            if kind == "scene":
                m = battle.load_scene(mid)
                d = {"mesh": m, "pm": m, "cams": battle.cameras(mid, 640, 480, near=battle.NEAR, min_angle=15)}
            elif kind == "battle":
                m = battle.load_model(mid)
                d = {"mesh": m, "pm": m}
            else:
                m = field.load_model(mid)
                d = {"mesh": m, "pm": None, "atlas": None}
            if len(m) == 0:
                raise ValueError("this model has no triangles")
            pts = m.pos.reshape(-1, 3).astype(np.float64)
            lo, hi = pts.min(0), pts.max(0)
            d["target"] = (lo + hi) / 2
            d["radius"] = float(np.linalg.norm(pts - d["target"], axis=1).max()) or 1.0
            d["points"] = np.unique(pts, axis=0)
            if keep:
                _remember(meshes, key, d, 8)
            return d

    def field_atlas(d):
        """A field model's atlas (built once) and the mesh painted on it: every triangle on the atlas, nothing else."""
        with lock:
            if d.get("atlas") is None:
                a = field.atlas(d["mesh"])
                pm = replace(a.mesh, textures=[a.mesh.textures[a.index]],
                             tex=np.where(a.mesh.tex == a.index, 0, -1).astype(np.int32))
                d["atlas"], d["pm"] = a, pm
            return d["atlas"], d["pm"]

    def syw_set(kind, mid):
        """The model as SYW Unified Battle Textures has it, for the viewer: {"mesh", "images"} (None where the pack
        hasn't a texture: the original shows there), or None when it has none of them."""
        key = (kind, mid, SYW)
        with lock:
            if key in painted:
                painted.move_to_end(key)
                return painted[key]
        if kind == "field" or syw_pack() is None:
            return None
        m = loaded(kind, mid)["mesh"]
        imgs = [restyle.syw_battle_texture(ref, syw_pack()) for ref in m.textures]
        if all(i is None for i in imgs):
            return None
        out = {"mesh": m, "images": imgs}
        with lock:
            _remember(painted, key, out, 4)
        return out

    def painted_set(kind, mid, vid):
        """A version's painted textures for the viewer: {"images": per pm texture an RGBA or None, "mesh": the
        mesh to draw them on}, or None when the version has none."""
        key = (kind, mid, vid)
        with lock:
            if key in painted:
                painted.move_to_end(key)
                return painted[key]
        meta = version_meta(kind, mid, vid)
        if not meta:
            return None
        vdir = model_dir(kind, mid) / _safe_name(vid)
        d = loaded(kind, mid)
        if kind == "field":
            a, pm = field_atlas(d)
            t = next((t for t in meta.get("textures", []) if t.get("atlas")), None)
            if not t or not (vdir / t["file"]).exists():
                return None
            img = np.asarray(Image.open(vdir / t["file"]).convert("RGBA"))
            uvp = vdir / "atlas_uv.npy"
            if uvp.exists():
                uv = np.load(uvp)
                if uv.shape == pm.uv.shape:
                    pm = replace(pm, uv=uv)  # the UVs it was painted on, should the atlas be laid out differently now
            pm = replace(pm, textures=[render_ref(pm.textures[0], img)])
            out = {"mesh": pm, "images": [img]}
        else:
            m = d["mesh"]
            files = {t["name"]: t["file"] for t in meta.get("textures", [])}
            imgs = []
            for ref in m.textures:
                p = vdir / files[ref.name] if ref.name in files else None
                imgs.append(np.asarray(Image.open(p).convert("RGBA")) if p and p.exists() else None)
            out = {"mesh": m, "images": imgs}
        with lock:
            _remember(painted, key, out, 4)
        return out

    def render_ref(ref, img):
        from ff7bg.models import mesh as meshmod
        return meshmod.TexRef(ref.key, atlasmod.as_tex(img), 0)

    def view_camera(kind, d, cam, yaw, pitch, zoom, w, h):
        if kind == "scene":
            cams = d["cams"]
            if not cams:
                raise ValueError("this battle scene has no camera")
            c = cams[min(max(int(cam), 0), len(cams) - 1)]
            eye = turn(c.eye, c.target, yaw, pitch, zoom)
            # the game's 4:3 picture always fits: a viewer taller than 4:3 shows more above and below, not less
            # at the sides
            focal = min(battle.focal(h), battle.focal(w * 3 / 4))
            return render.Camera(eye=eye, target=np.asarray(c.target, float), up=np.array([0.0, -1.0, 0.0]),
                                 fov_y=c.fov_y, width=w, height=h, near=battle.NEAR, focal=focal)
        # the starting distance fits the model's outline from every side at this aspect (so it stays put while
        # turning, and a long sword doesn't shrink the model as a bounding sphere would)
        aspect = round(w / h, 2)
        fits = d.setdefault("fits", {})
        if aspect not in fits:
            fits[aspect] = max(fit_distance(d["points"], d["target"], y, 12, aspect * 100, 100, margin=0.9)
                               for y in range(0, 360, 30))
        dist = fits[aspect] / max(zoom, 1e-3)
        return orbit_camera(render, d["target"], dist, 25 + yaw, 12 + pitch, w, h, near=d["radius"] * 0.01)

    def draw(kind, mesh, cam, images=None):
        r = render.rasterize(mesh, cam)
        bg = (0, 0, 0) if kind == "scene" else BG_MODEL
        return render.shade(mesh, r, images, modulate=True, background=bg)

    def pan_camera(cam, panx, pany):
        """The camera moved sideways with what it looks at (the viewer's pan): panx, pany in view heights, right and
        down."""
        if not panx and not pany:
            return cam
        R = cam.rotation()
        dist = float(np.linalg.norm(np.asarray(cam.target, float) - np.asarray(cam.eye, float)))
        k = dist / cam.f_px() * cam.height
        off = (R[0] * float(panx) + R[1] * float(pany)) * k
        return replace(cam, eye=np.asarray(cam.eye, float) + off, target=np.asarray(cam.target, float) + off)

    posed = OrderedDict()  # (battle model, animation) -> its mesh at that animation's first frame

    def posed_original(mid, pose):
        """A battle model as the game draws it at the first frame of animation `pose` (the viewer's Pose)."""
        key = (mid, int(pose))
        with lock:
            if key in posed:
                posed.move_to_end(key)
                return posed[key]
        m = battle.load_model(mid, int(pose))
        with lock:
            _remember(posed, key, m, 6)
        return m

    def p2d_poses(mid):
        """[(animation, label)] the poses the viewer offers a battle model: the first frame of each of its
        animations whose pose differs from the one painted (battleatlas.MOVING's parts named where they show)."""
        key = ("poses", mid)
        with lock:
            if key in posed:
                return posed[key]
        out = []
        try:
            from ff7bg.models import battleatlas as A
            moving = {p["animation"]: p["name"] for p in A.model_views(mid).get("moving") or []}
            hd = battle.header(mid)
            base = battle.load_model(mid, 0)
            seen = [base.pos]
            for k in range(1, int(hd.animations)):
                try:
                    m = battle.load_model(mid, k)
                except Exception:
                    continue
                if m.pos.shape != base.pos.shape or any(np.abs(m.pos - p).max() < 1.0 for p in seen):
                    continue
                seen.append(m.pos)
                out.append((k, f"animation {k}" + (f" ({moving[k]} showing)" if k in moving else "")))
        except Exception:
            traceback.print_exc()  # the viewer shows the painted pose only
        with lock:
            posed[key] = out
        return out

    def p2d_viewer_dir(kind, mid, vid):
        """A 2D version's folder: battleatlas' (battle models) or fieldatlas' (field models), or None."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", vid or "") or vid.endswith(".partial") or vid.startswith("."):
            return None
        if kind == "battle":
            d = batlas().WORK / mid / vid
        elif kind == "field":
            from ff7bg.models import fieldatlas as F
            d = F.WORK / mid.lower() / vid
        else:
            return None
        return d if (d / "info.json").exists() else None

    def p2d_viewer_versions(kind, mid):
        """The model's 2D versions for the viewer: [{id, at, painted, check}], oldest first (field models: fieldatlas'
        builds; battle models: the card's own list)."""
        if kind == "battle":
            A = batlas()
            return [{"id": v["id"], "at": v["at"], "painted": v["painted"], "current": False,
                     "check": v["check"], "label": p2d_from_line(v)} for v in p2d_versions(A, mid)]
        if kind != "field":
            return []
        from ff7bg.models import fieldatlas as F
        d = F.WORK / mid.lower()
        out = []
        for p in (d.iterdir() if d.is_dir() else []):
            if not p.is_dir() or p.name.endswith(".partial") or not (p / "info.json").exists():
                continue
            try:
                info = json.loads((p / "info.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pt = info.get("painted") or {}
            out.append({"id": p.name, "at": p2d_time(info.get("at"), p.stat().st_mtime), "painted": bool(pt),
                        "current": False, "check": p2d_check_summary(p),
                        "label": (f"{pt.get('look', '')} · strength {pt.get('strength')}" if pt else
                                  "the game's own colours (not painted)")})
        out.sort(key=lambda v: (v["at"], v["id"]))
        return out

    def p2d_from_line(v):
        bits = [f"strength {v['strength']:.2f}" if isinstance(v.get("strength"), (int, float)) else "",
                f"touched up from {v['touchup']['source']}" if v.get("touchup") else ""]
        return " · ".join(b for b in bits if b) or ("painted" if v.get("painted") else "not painted")

    p2d_sets = OrderedDict()  # (kind, model, version, pose, folder time) -> {"mesh", "images"}

    def p2d_viewer_set(kind, mid, vid, pose=0, rounded=False):
        """A 2D version on its model for the viewer, as the game draws it: {"mesh", "images"} in model space (the
        viewer's camera), or None. Battle: battleatlas' layout (build_of) showing the version's painted texture,
        posed at the first frame of animation `pose`. Field: the version's own files, a texture per part
        (fieldatlas.files_mesh, the layers the game shows: FieldAtlas.seen). Drawn flat (the texture's colour, no
        light: the painting carries the shading); render.rasterize cuts out texels at half alpha or less, as the engine
        does (field Cloud's eye and mouth decals). rounded: a field model rounded as the engine's char_round rounds it
        (paint2d_extras.pn_round)."""
        d = p2d_viewer_dir(kind, mid, vid)
        if d is None:
            return None
        key = (kind, mid, vid, int(pose), bool(rounded), d.stat().st_mtime)
        with lock:
            if key in p2d_sets:
                p2d_sets.move_to_end(key)
                return p2d_sets[key]
        if kind == "battle":
            A = batlas()
            ba = A.build_of(mid, vid)
            png = d / f"{ba.name}.png"
            if not png.exists():
                return None
            img = np.asarray(Image.open(png).convert("RGBA"))
            m = A.posed_as(ba, pose) if pose else ba.posed
            out = {"mesh": A.atlas_only(A.with_image(ba, img, m), ba.atlas.index), "images": [img]}
        elif rounded:  # (the flat one rounded: the build is the slow part)
            flat = p2d_viewer_set(kind, mid, vid, pose, rounded=False)
            out = {"mesh": p2x().pn_round(flat["mesh"]), "images": flat["images"]}
        else:
            from ff7bg.models import fieldatlas as F
            fa = F.build_of(mid, vid)
            fm = fa.seen(F.files_mesh(fa, F.build_files(d)))
            out = {"mesh": fm, "images": [np.asarray(t.tex.pixels) for t in fm.textures]}
        with lock:
            _remember(p2d_sets, key, out, 3)
        return out

    def jpeg(img, quality=86):
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, "JPEG", quality=quality)
        buf.seek(0)
        return buf

    def original_thumb(kind, mid):
        path = THUMBS / f"{kind}_{_safe_name(mid)}.jpg"
        if path.exists():
            return path
        with thumb_gate:
            if path.exists():
                return path
            d = loaded(kind, mid, keep=False)
            w, h = THUMB
            if kind == "scene":
                cam = view_camera(kind, d, 0, 0, 0, 1, w, h)
            else:
                t = d["target"]
                cam = orbit_camera(render, t, fit_distance(d["points"], t, 25, 12, w, h), 25, 12, w, h,
                                   near=d["radius"] * 0.01)
            img = draw(kind, d["mesh"], cam)
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(path, quality=85)
            return path

    # ---------------------------------------------------------------- the paint job

    def job_name(kind, mid):
        return f"{KINDS[kind]} {mid}"

    def parse_job_name(name):
        for kind, prefix in KINDS.items():
            if name.startswith(prefix + " "):
                return kind, name[len(prefix) + 1:]
        raise ValueError(f"not a model job: {name}")

    def look_info(look_id):
        """(look id used, its name, its effective settings): the model's look, else the Studio's default look."""
        st = project.looks
        with st.lock:
            data = st.data
            lid = look_id if look_id in data["looks"] else data["default"]
            return lid, data["looks"][lid].get("name", lid), st.look_settings(lid)

    def method_for(kind, ps):
        return "projected" if kind == "field" else (ps["method"] or default_method(kind))

    def used_textures(kind, mid):
        """(indices of the mesh textures its triangles use, their paint.repeats): what Flat and Hybrid would paint as
        sheets. Field models: their atlas (one texture, laid once)."""
        d = loaded(kind, mid)
        if kind == "field":
            return [0], [1.0]
        if "repeats" not in d:
            m = d["mesh"]
            d["repeats"] = paint.repeats(m)
            d["used"] = sorted(int(k) for k in np.unique(m.tex[m.tex >= 0]))
        return d["used"], d["repeats"]

    def game_size(w, h, factor):
        """A texture's size in the game: w x h times factor, its longest side capped at MAX_SIDE."""
        w_, h_ = w * factor, h * factor
        if max(w_, h_) > MAX_SIDE:
            f = MAX_SIDE / max(w_, h_)
            w_, h_ = max(1, round(w_ * f)), max(1, round(h_ * f))
        return int(w_), int(h_)

    def detail_for(w, h, inp, detail):
        """The detail a ground texture really gets (Restyler.sheet: no bigger than restyle.MAX_SHEET_SIDE)."""
        cap = getattr(restyle, "MAX_SHEET_SIDE", MAX_SIDE)
        return max(1, min(int(detail), cap // max(1, max(w, h) * inp)))

    def scene_parts(kind, mid):
        """A battle scene's textures as scenepaint.classify sees them: {"far": [indices], "ground": [indices]}."""
        if kind != "scene":
            return {"far": [], "ground": []}
        d = loaded(kind, mid)
        if "classes" not in d:
            from ff7bg.models import scenepaint
            cls = scenepaint.classify(d["mesh"])
            d["classes"] = {"far": [k for k, c in enumerate(cls) if c["far"]],
                            "ground": [k for k, c in enumerate(cls) if c["ground"]]}
        return d["classes"]

    def extras_for(kind, method, ps):
        """The extras a paint uses: {"panorama": bool, "detail": int, "seams": bool} (battle scenes, Flat or Hybrid;
        otherwise none)."""
        on = kind == "scene" and method in ("flat", "hybrid")
        return {"panorama": on and ps["panorama"] is not False, "detail": (ps["detail"] or 2) if on else 1,
                "seams": on and ps["seams"] is not False}

    def plan(kind, mid, mode, ps):
        """What a paint of this model does with its settings: {"method", "sheets": textures painted flat, "views":
        views painted from, "hybrid_flat" / "used": how many of its used textures Hybrid would paint flat}."""
        method = method_for(kind, ps)
        used, rep = used_textures(kind, mid)
        hybrid_flat = sum(1 for k in used if rep[k] >= paint.REPEAT)
        if kind == "scene":
            spec = SCENE_VIEWS[mode]
            n_battle = min(spec["battle"], max(1, len(loaded(kind, mid)["cams"])))
            n_views = n_battle + spec["ring"]
        else:
            n_views = len(MODEL_ORBIT[mode])
        if method == "flat":
            flat = [k for k in used]
        elif method == "hybrid":
            flat = [k for k in used if rep[k] >= paint.REPEAT]
        else:
            flat = []
        ex = extras_for(kind, method, ps)
        parts = scene_parts(kind, mid)
        far = [k for k in parts["far"] if k in used] if ex["panorama"] else []
        flat_set = set(flat) | set(far)  # the panorama takes the far textures whatever their repeats
        sheets = [k for k in flat_set if k not in far]
        ground = [k for k in sheets if k in parts["ground"]] if ex["detail"] > 1 else []
        views = n_views if (method != "flat" and len(flat_set) < len(used)) else 0  # all of it flat: no views
        # sizes in the game: every texture at the output scale, the ground ones times the detail they really get
        inp = input_for(kind, mode, ps)
        out = output_for(kind, inp, ps)
        texs = (cat["battle"] or {}).get(mid, {}).get("textures") or [] if kind != "field" else []
        ground_px, eff, tiles, max_side, out_capped = 0, 1, 0.0, 0, False
        for k in used:
            if k >= len(texs):
                continue
            tw, th = texs[k]["width"], texs[k]["height"]
            gk = detail_for(tw, th, inp, ex["detail"]) if k in ground else 1
            w_, h_ = game_size(tw, th, out * gk)
            max_side = max(max_side, w_, h_)
            out_capped |= max(tw, th) * out * gk > MAX_SIDE
            if k in ground:
                eff = max(eff, gk)
                painted = max(tw, th) * inp * gk
                ground_px = max(ground_px, painted)
                tiles += (tw * inp * gk) * (th * inp * gk) / 1024 ** 2 if gk > 1 else 0  # 1024 px tiles
        if kind == "field":
            max_side = int(1024 * (ps["output"] or 1))
        return {"method": method, "sheets": len(sheets), "views": views, "hybrid_flat": hybrid_flat, "used": len(used),
                "panorama": len(far), "detail": ex["detail"] if ground else 1, "detail_eff": eff if ground else 1,
                "ground": len(ground), "ground_px": int(ground_px), "detail_tiles": round(tiles, 1),
                "seams": ex["seams"], "max_side": int(max_side), "out_capped": out_capped}

    def paint_seconds(kind, e, mode, ps, pl):
        """Rough seconds for the AI's part: each sheet (its pixels at the input scale), each view (its pixels, plus
        the projection's own work), the description and the rest."""
        inp = input_for(kind, mode, ps)
        texs = e.get("textures") or []
        used = used_textures(kind, key_of(kind, e))[0] if kind != "field" else []
        px = [texs[k]["width"] * texs[k]["height"] * inp * inp / 1e6 for k in used if k < len(texs)]
        avg_px = sum(px) / len(px) if px else 1.0
        sheet_s = SHEET_SECONDS_BASE + SHEET_SECONDS_MP * avg_px
        w, h = (SCENE_VIEWS if kind == "scene" else MODEL_VIEWS)[mode]["size"]
        view_s = VIEW_SECONDS * (w * h) / (1280 * 960) + PROJECT_SECONDS
        return (OVERHEAD_SECONDS + pl["sheets"] * sheet_s + pl["views"] * view_s
                + (PANORAMA_SECONDS if pl.get("panorama") else 0)
                + pl.get("detail_tiles", 0) * DETAIL_TILE_SECONDS
                + (SEAMS_SECONDS if pl.get("seams") else 0))

    syw_seen = {"at": 0.0, "pack": None}

    def syw_pack():
        """SYW Unified Battle Textures' archive in 7th Heaven's library, or None (looked for at most every 10 s)."""
        if time.time() - syw_seen["at"] > 10:
            try:
                syw_seen["pack"] = restyle.syw_battle_pack()
            except Exception:
                syw_seen["pack"] = None
            syw_seen["at"] = time.time()
        return syw_seen["pack"]

    def syw_info(kind, e):
        """{"pack": its file name or None, "have": how many of this model's textures it has, "total", "missing": the
        game names it lacks}. Field models: none (the pack is battle textures)."""
        pack = syw_pack()
        names = [f"{t['key'].split('/', 1)[-1].upper()}_00" for t in e.get("textures") or []] if kind != "field" else []
        if pack is None or not names:
            return {"pack": pack.name if pack else None, "have": 0, "total": len(names), "missing": names}
        try:
            idx = restyle.syw_battle_index(pack)
        except Exception as ex:
            print(f"models: SYW battle pack unreadable: {ex}")
            return {"pack": None, "have": 0, "total": len(names), "missing": names}
        missing = [n for n in names if n not in idx]
        return {"pack": pack.name, "have": len(names) - len(missing), "total": len(names), "missing": missing}

    def upscaler_seconds(name, items):
        """Rough seconds for an upscaler over [(in w, in h, out w, out h)] (ff7bg.upscalers.seconds; SeedVR2 more)."""
        from ff7bg import upscalers
        total = 0.0
        for iw, ih, ow, oh in items:
            if name == "lanczos" or (ow <= iw and oh <= ih):
                total += 0.05
            elif name == "seedvr2":
                total += 4.5 + 2.0 * ow * oh / 1e6
            else:
                total += upscalers.seconds(name, iw * ih, ow * oh)
        return total

    def upscale_time(kind, e, mode, ps):
        """What the two upscalers add to a paint: enlarging the original textures (those not enlarged before with the
        same upscaler and scale: they're kept) and enlarging the painting for the game."""
        inp = input_for(kind, mode, ps)
        out = output_for(kind, inp, ps)
        up_in, up_out = ps["up_in"] or DEFAULT_UPSCALER, ps["up_out"] or DEFAULT_UPSCALER
        if kind == "field":
            size = 1024  # about a field model's atlas
            return upscaler_seconds(up_out, [(size, size, int(size * out), int(size * out))]) if out > 1 else 0.0
        texs = e.get("textures") or []
        tag = getattr(restyle, "_cache_tag", None)
        in_pack = set() if up_in != SYW else {n for n in (f"{t['key'].split('/', 1)[-1].upper()}_00" for t in texs)
                                                if n not in syw_info(kind, e)["missing"]}
        todo, from_pack = [], 0
        for t in texs:
            cached = tag and (restyle.TMP.parent / "enlarged" /
                              f"{t['key'].replace('/', '_')}_00_{inp}x_{tag(up_in)}.png").exists()
            if cached:
                continue
            if f"{t['key'].split('/', 1)[-1].upper()}_00" in in_pack:
                from_pack += 1
            else:
                todo.append((t["width"], t["height"], t["width"] * inp, t["height"] * inp))
        total = (upscaler_seconds(DEFAULT_UPSCALER if up_in == SYW else up_in, todo) + 0.1 * from_pack) if inp > 1 else 0.0
        if out > inp:
            total += upscaler_seconds(up_out, [(t["width"] * inp, t["height"] * inp, t["width"] * out, t["height"] * out)
                                               for t in texs])
        return total

    def estimate_for(kind, e, mode, ps):
        """Seconds a paint takes with these settings (once ComfyUI has its model loaded)."""
        pl = plan(kind, key_of(kind, e), mode, ps)
        return round(paint_seconds(kind, e, mode, ps, pl) + upscale_time(kind, e, mode, ps))

    def views_for(kind, mid, d, mode, method="flat"):
        """[(label, kind of view, render.Camera)] the job paints from, in order (the first is restyled whole)."""
        out = []
        if kind == "scene":
            spec = SCENE_VIEWS[mode]
            w, h = spec["size"]
            real = battle.cameras(mid, w, h, near=battle.NEAR, min_angle=15)
            if not real:
                raise ValueError("this battle scene has no camera to paint from")
            for i, c in enumerate(real[:spec["battle"]]):
                n = len(c.formations)
                out.append((f"battle camera {i + 1}" + (f" ({n} battle{'s' if n != 1 else ''})" if n else ""), "battle", c))
            if method != "flat":  # all round from the middle of the action, looking out (what the battle cameras swing to)
                ring = battle.ring_cameras(mid, spec["ring"], w, h)
                for i, c in enumerate(ring):
                    out.append((f"looking out from the middle, {i + 1} of {len(ring)}", "ring", c))
            return out
        w, h = MODEL_VIEWS[mode]["size"]
        t = d["target"]
        for label, yaw, pitch in MODEL_ORBIT[mode]:
            dist = fit_distance(d["points"], t, yaw, pitch, w, h)
            out.append((label, "orbit", orbit_camera(render, t, dist, yaw, pitch, w, h, near=d["radius"] * 0.01)))
        return out

    def cam_json(label, how, c):
        return {"label": label, "kind": how, "eye": [round(float(x), 3) for x in c.eye],
                "target": [round(float(x), 3) for x in c.target], "fov_y": round(float(c.fov_y), 3),
                "focal": round(float(c.focal), 3) if c.focal else None, "width": c.width, "height": c.height}

    def small(c, w):
        """The same camera rendering `w` pixels wide."""
        k = w / c.width
        return render.Camera(c.eye, c.target, c.up, c.fov_y, int(round(c.width * k)), int(round(c.height * k)), c.near,
                             c.focal * k if c.focal else None)

    def run_paint(w, job):
        kind, mid = parse_job_name(job["name"])
        mode = "final" if job["kind"] == JOB_FINAL else "preview"
        e = entry_or_raise(kind, mid)
        ok, why = paintable(kind, e)
        if not ok:
            raise RuntimeError(why)
        started = time.time()
        try:
            js = job.get("settings") or {}
            vid = paint_version(w, kind, key_of(kind, e), e, mode, js.get("look") or None, clean_paint(kind, js.get("paint")))
        except InterruptedError:
            raise
        except Exception as ex:
            msg = str(ex) if isinstance(ex, (RuntimeError, ValueError)) else f"{type(ex).__name__}: {ex}"
            save_model_meta(kind, key_of(kind, e), error=msg)
            raise
        job["version"] = vid
        job["note"] = f"{mode} {vid} in {round(time.time() - started)} s"

    def entry_or_raise(kind, mid):
        if not ready():
            load_catalogues()
        e = (cat["field"] or {}).get(mid.upper()) if kind == "field" else (cat["battle"] or {}).get(mid.lower())
        if e is None:
            raise ValueError(f"no {KINDS[kind]} {mid} in the game files")
        return e

    def paint_version(w, kind, mid, e, mode, look_id, ps):
        t0 = time.time()

        def cancelled():
            if w.cancel_flag:
                raise InterruptedError("cancelled")

        w._stage("loading the model", 0.01)
        d = loaded(kind, mid)
        lid, look_name, s = look_info(look_id)
        state = {"done": 0, "calls": 0, "sub": 0.0, "sheet": None}
        views, n = [], 1
        lo, hi = 0.12, 0.9
        band = {"sheets": (lo, lo), "views": (lo, hi)}  # set once the sheets are counted

        def stage():
            if state["sheet"] is not None:  # painting the sheets
                i, k, name = state["sheet"]
                a, b = band["sheets"]
                w._stage(f"painting texture sheet {i + 1} of {k}: {name.split('/')[-1]}",
                         a + (b - a) * (i + state["sub"]) / max(k, 1))
                return
            if not views:
                return
            i = min(state["done"], n - 1)
            share = (min(state["done"], n) + state["sub"]) / n
            a, b = band["views"]
            w._stage(f"painting view {i + 1} of {n}: {views[i][0]}", a + (b - a) * share)

        def on_progress(node, value, maximum):
            if w.cancel_flag:
                comfy = getattr(R, "comfy", None)
                if comfy is not None:
                    comfy.interrupt()
                raise InterruptedError("cancelled")
            if maximum:
                state["sub"] = min(0.95, value / maximum)
                stage()

        # first: is ComfyUI there (the Restyler says so in plain words when it isn't)
        w._stage("connecting to ComfyUI", 0.02)
        R = restyle.Restyler(s, url=project.comfy_url, kind="scene" if kind == "scene" else "model",
                             name=f"{kind}_{mid}", on_progress=on_progress, prompt=ps["prompt"],
                             strength=ps["strength"], describe=ps["describe"])
        if kind == "field":
            a, pm = field_atlas(d)
            scale, base = 1, [a.image]
        else:
            pm = d["mesh"]
            scale = input_for(kind, mode, ps)
            if kind == "battle" and not (pm.tex >= 0).any():
                raise RuntimeError("this model has no textured parts to paint")
            cancelled()
            w._stage(f"enlarging its {len(pm.textures)} texture{'s' if len(pm.textures) != 1 else ''}", 0.04)
            base = restyle.enlarge_textures(pm, scale, url=project.comfy_url, upscaler=ps["up_in"] or DEFAULT_UPSCALER, s=s)
        cancelled()
        method = method_for(kind, ps)
        views[:] = views_for(kind, mid, d, mode, method)
        n = len(views)
        sheet_kw = {}
        if method in ("flat", "hybrid"):  # sheets first (flat: every texture; hybrid: the repeated ones), then views
            pl = plan(kind, mid, mode, ps)
            t_sheets = pl["sheets"] * 4.0
            t_views = pl["views"] * (VIEW_SECONDS + PROJECT_SECONDS)
            split = lo + (hi - lo) * t_sheets / max(t_sheets + t_views, 1e-6)
            band.update(sheets=(lo, split), views=(split, hi))

            def sheet_fn(img, name, wrap=(True, True), **kw):  # wrap: the edges the scene joins; kw: detail
                cancelled()
                return R.sheet(img, name, wrap=wrap, **kw)

            def on_sheet(i, k, name):
                cancelled()
                state["sheet"], state["sub"] = (i, k, name), 0.0
                stage()

            sheet_kw = {"sheets": sheet_fn, "on_sheet": on_sheet, "repeat": 0 if method == "flat" else paint.REPEAT}
            ex = extras_for(kind, method, ps)
            if kind == "scene":  # the sky as one panorama, the ground with detail, the seams blended
                sheet_kw.update(panorama=ex["panorama"], detail=ex["detail"], seams=ex["seams"])

        def restyle_view(orig, cur, mask):
            cancelled()
            state["sheet"] = None
            state["calls"] += 1
            state["sub"] = 0.0
            stage()
            return R(orig, cur, mask)

        if hasattr(R, "prepare"):  # paint.paint describes the scene from its first view before painting sheets
            restyle_view.prepare = R.prepare
        if hasattr(R, "panorama"):  # ...and paints the sky as one panorama only when the restyle object can
            restyle_view.panorama = R.panorama

        def progress(i, total):
            state["done"], state["sub"] = i, 0.0
            cancelled()
            if i < n:
                stage()

        stage()
        P, log = paint.paint(pm, [c for _, _, c in views], restyle_view, scale=scale, progress=progress, base=base,
                             **sheet_kw)
        cancelled()
        w._stage("saving the painted textures", 0.92)
        vid = new_version_id(kind, mid)
        vdir = model_dir(kind, mid) / vid
        tmp = vdir.with_name(vid + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "textures").mkdir(parents=True)
        try:
            meta = save_files(w, kind, mid, e, mode, vid, tmp, d, pm, P, log, views, scale, lid, look_name, s, R, t0,
                              a if kind == "field" else None, dict(ps, method=method))
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        tmp.rename(vdir)
        changes = {"error": None}
        if mode == "final" or not current(kind, mid):
            changes["current"] = vid  # what Install takes; the game itself isn't touched
        save_model_meta(kind, mid, **changes)
        w._stage("done", 1.0)
        return vid

    def save_files(w, kind, mid, e, mode, vid, tmp, d, pm, P, log, views, scale, lid, look_name, s, R, t0, a, ps):
        """The version's files into tmp: textures (at the output scale), views sheet, thumbnail, meta.json. Returns
        the meta."""
        images = P.images()
        sheets = list((log[0].get("sheets") if log else None) or [])  # painted flat (battle scenes' repeated textures)
        pano = list((log[0].get("panorama") if log else None) or [])    # ...of them, the sky as one panorama
        detail = list((log[0].get("detail") if log else None) or [])    # ...and the ground with the detail pass
        out = output_for(kind, scale, ps)
        if kind == "field":
            sizes = [(max(1, round(a.size * out)),) * 2]
        else:
            sizes = []
            for k, t in enumerate(pm.textures):
                # painted bigger than the rest (ground detail): the game gets it that much bigger too; every texture
                # capped at MAX_SIDE on its longest side
                k_det = max(1, round(images[k].shape[1] / max(1, t.tex.width * scale)))
                sizes.append(game_size(t.tex.width, t.tex.height, out * k_det))
        tex_meta = []
        if kind == "field":
            img = atlasmod.fill(images[0], a.used())
            if (img.shape[1], img.shape[0]) != sizes[0]:
                w._stage("sizing the atlas for the game", 0.93)
                img = restyle.resize_textures([img], sizes, url=project.comfy_url, upscaler=ps["up_out"] or DEFAULT_UPSCALER,
                                              s=s)[0]
            name = f"{mid}_atlas.png"
            Image.fromarray(img).save(tmp / "textures" / name)
            np.save(tmp / "atlas_uv.npy", pm.uv)
            tex_meta.append({"name": f"atlas/{mid}", "file": f"textures/{name}", "width": img.shape[1],
                             "height": img.shape[0], "atlas": True,
                             "painted": round(float((P.wsum[0] > 0).mean()), 3)})
            shown = [img]
        else:
            if any((im.shape[1], im.shape[0]) != sz for im, sz in zip(images, sizes)):
                w._stage(f"sizing the textures for the game ({out}× the original)", 0.93)
                images = restyle.resize_textures(images, sizes, url=project.comfy_url,
                                                 upscaler=ps["up_out"] or DEFAULT_UPSCALER, s=s)
            shown = images
            for k, ref in enumerate(pm.textures):
                rel = f"textures/{ref.name}.png"  # battle/OQAC_00.png: FFNx's override name
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(images[k]).save(tmp / rel)
                sheet = ref.name in sheets
                tex_meta.append({"name": ref.name, "file": rel, "width": images[k].shape[1],
                                 "height": images[k].shape[0], "sheet": sheet,
                                 "panorama": ref.name in pano, "detail": ref.name in detail,
                                 "painted": 1.0 if sheet else round(float((P.wsum[k] > 0).mean()), 3)})
        # the views sheet (original | painted, as the game shades them) and the thumbnail
        w._stage("drawing the views sheet", 0.95)
        draw_mesh = d["mesh"]
        painted_mesh = replace(pm, textures=[render_ref(pm.textures[0], shown[0])]) if kind == "field" else pm
        cell = 480 if kind == "scene" else 400
        rows = []
        for label, _, c in views:
            sc = small(c, cell)
            rows.append(np.hstack([draw(kind, draw_mesh, sc), draw(kind, painted_mesh, sc, None if kind == "field" else shown)]))
        sheet = np.vstack(rows)
        Image.fromarray(sheet).save(tmp / "views.jpg", quality=88)
        tc = small(views[0][2], 320)
        Image.fromarray(draw(kind, painted_mesh, tc, None if kind == "field" else shown)).save(tmp / "thumb.jpg", quality=85)
        by_cam = {id(l["camera"]): l for l in log if l.get("camera") is not None}
        meta = {
            "id": vid, "kind": kind, "model": mid, "title": title(kind, e), "mode": mode, "created": time.time(),
            "method": ps.get("method") or method_for(kind, ps),
            "seconds": round(time.time() - t0, 1), "look": lid, "look_name": look_name, "family": s.get("family"),
            "settings": {k: R.s.get(k, s.get(k)) for k in SUMMARY if k in s or k in R.s},
            "scene": (getattr(R, "s", None) or {}).get("scene", ""), "scale": scale, "out_scale": out,
            "words": getattr(R, "words", None) or "",  # the automatic description alone
            # what this version was painted with (the user's settings; strength_used: the number the AI got)
            "paint": dict(ps, input=scale, output=out, describe=bool(getattr(R, "describe", ps["describe"])),
                          up_in=(ps["up_in"] or DEFAULT_UPSCALER) if kind != "field" and (scale > 1 or ps["up_in"] == SYW)
                          else None,  # (SYW counts at 1x too: its 4x brought down to the game's size)
                          up_out=(ps["up_out"] or DEFAULT_UPSCALER) if out > (1 if kind == "field" else scale) else None,
                          strength_used=(ps["strength"] if ps["strength"] is not None else
                                         (auto_strength(s)["sheets"] if kind == "scene" else auto_strength(s)["views"]))),
            # per view: the share it was first to paint, and whether it painted at all (views skip when there's
            # nothing new for them, and all of them when every texture was a sheet)
            "views": [dict(cam_json(label, how, c), new=round(float(by_cam[id(c)]["new"]), 4) if id(c) in by_cam else 0.0,
                           painted=id(c) in by_cam and by_cam[id(c)]["image"] is not None)
                      for label, how, c in views],
            "sheets": sheets, "panorama": pano, "detail": detail,
            "extras": extras_for(kind, ps.get("method") or method_for(kind, ps), ps) if kind == "scene" else None,
            "textures": tex_meta,
        }
        if kind == "field":
            meta["atlas"] = {"size": a.size, "density": round(a.density, 3)}
        if ps["up_in"] == SYW and kind != "field":
            si = syw_info(kind, e)
            meta["syw"] = {"pack": si["pack"], "have": si["have"], "total": si["total"], "missing": si["missing"]}
        if getattr(R, "stand_in", False):
            meta["stand_in"] = True
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return meta

    def est_for(mode):
        def est(name):
            try:
                kind, mid = parse_job_name(name)
            except ValueError:
                return 60
            e = (cat["field"] or {}).get(mid.upper()) if kind == "field" else (cat["battle"] or {}).get(mid.lower())
            return estimate_for(kind, e, mode, paint_settings(kind, mid)) if e else 60
        return est

    # ---------------------------------------------------------------- install

    def install(kind, mid, vid):
        """Copy a version's textures to the game's texture overrides. Files there that aren't ours are moved
        aside first and put back by uninstall. Returns the manifest entry."""
        meta = version_meta(kind, mid, vid)
        if not meta:
            raise ValueError("that version is gone")
        vdir = model_dir(kind, mid) / _safe_name(vid)
        root = mod_root()
        key = f"{kind}/{mid}"
        with lock:
            data = installed_all()
            if key in data:
                uninstall_entry(data.pop(key))
            ours = {f["path"].lower() for entry in data.values() for f in entry["files"]}
            files = []
            for t in meta.get("textures", []):
                src = vdir / t["file"]
                if t.get("atlas") or not src.exists():
                    continue
                target = root / Path(t["file"]).relative_to("textures")
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = None
                if target.exists() and str(target).lower() not in ours:
                    b = BACKUP / f"{kind}_{_safe_name(mid)}" / target.name
                    b.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(b))
                    backup = str(b)
                shutil.copy2(src, target)
                files.append({"path": str(target), "backup": backup})
            entry = {"version": vid, "at": time.time(), "dest": str(root), "files": files}
            data[key] = entry
            save_installed(data)
            return entry

    def uninstall_entry(entry):
        """Remove exactly the files an install copied, and put back what they replaced."""
        removed = 0
        for f in entry.get("files", []):
            p = Path(f["path"])
            if p.exists():
                p.unlink()
                removed += 1
            if f.get("backup") and Path(f["backup"]).exists():
                shutil.move(f["backup"], str(p))
        for f in entry.get("files", []):
            b = f.get("backup")
            if b:
                try:
                    Path(b).parent.rmdir()  # empty now
                except OSError:
                    pass
        return removed

    # ---------------------------------------------------------------- views of the data

    def list_view(kind):
        data = installed_all()
        out = []
        if kind == "field":
            for hrc, e in cat["field"].items():
                fields = list(e.get("fields") or {})
                names = list(e.get("names") or {})
                out.append({"id": hrc, "title": e["name"], "uses": len(fields),
                            "sub": f"{hrc} · " + (f"in {len(fields)} field{'s' if len(fields) != 1 else ''}" if fields else "in no field"),
                            "search": " ".join([hrc, e["name"], *names, *fields[:60]]).lower(), "textured": True})
            out.sort(key=lambda x: (-x["uses"], x["title"], x["id"]))
        else:
            p2d_list = p2d_status() if kind == "battle" else {}
            for mid, e in cat["battle"].items():
                if (e["kind"] == "scene") != (kind == "scene"):
                    continue
                if kind == "scene":
                    ens = scene_enemies(e)
                    empty = empty_scene(kind, e)
                    n = len(e.get("formations") or [])
                    sub = ("empty: nothing to paint" if empty else ", ".join(ens[:3]) or
                           (f"{n} battle setup{'s' if n != 1 else ''}" if n else "no battles use it"))
                    out.append({"id": mid, "title": f"{mid} · location {e.get('location')}", "sub": sub,
                                "search": " ".join([mid, f"location {e.get('location')}", *ens]).lower(),
                                "textured": not empty, "order": (e["index"],)})
                else:
                    t = title(kind, e)
                    tex = bool(e.get("textures"))
                    what = {"party": "party", "enemy": "enemy", "other": "not in any battle"}[e["kind"]]
                    sub = f"{what} · {mid}" + ("" if tex else " · no textures")
                    p2 = p2d_list.get(mid, "")
                    out.append({"id": mid, "title": t, "sub": sub, "p2d": p2,
                                "search": f"{mid} {t} {what} {P2D_SEARCH.get(p2, '')}".lower(), "textured": tex,
                                "order": ({"party": 0, "enemy": 1, "other": 2}[e["kind"]] if tex else 3,
                                          t.lower() if e["kind"] != "party" else "", e["index"])})
            out.sort(key=lambda x: x["order"])
            for x in out:
                del x["order"]
        for x in out:
            mm = model_meta(kind, x["id"]) if model_dir(kind, x["id"]).exists() else {}
            x["current"] = mm.get("current")
            x["mode"] = (version_meta(kind, x["id"], x["current"]) or {}).get("mode") if x["current"] else None
            x["installed"] = f"{kind}/{x['id']}" in data
            x["error"] = mm.get("error")
        return out

    def item_view(kind, e):
        mid = key_of(kind, e)
        mm = model_meta(kind, mid)
        cur = current(kind, mid)
        inst = installed_all().get(f"{kind}/{mid}")
        versions = []
        for vid in version_ids(kind, mid):
            m = version_meta(kind, mid, vid) or {}
            versions.append({"id": vid, "mode": m.get("mode"), "look": m.get("look"), "look_name": m.get("look_name"),
                             "family": m.get("family"), "created": m.get("created"), "seconds": m.get("seconds"),
                             "views": len(m.get("views", [])), "scale": m.get("scale"), "current": vid == cur,
                             "installed": bool(inst) and inst.get("version") == vid, "stand_in": m.get("stand_in", False),
                             "settings": m.get("settings", {}), "scene": m.get("scene", ""),
                             "sheets": sum(1 for t in m.get("textures", []) if t.get("sheet")),
                             "from_views": sum(1 for t in m.get("textures", []) if not t.get("sheet") and t.get("painted")),
                             "views_painted": sum(1 for v in m.get("views", []) if v.get("painted")),
                             "panorama": len(m.get("panorama") or []), "detail_n": len(m.get("detail") or []),
                             "extras": m.get("extras"),
                             "out_scale": m.get("out_scale", m.get("scale")), "paint": m.get("paint"),
                             # versions from before the choice: flat if any texture was a sheet, else projected
                             "method": m.get("method") or ("flat" if any(t.get("sheet") for t in m.get("textures", []))
                                                           else "projected"),
                             "words": m.get("words")})
        ok, why = paintable(kind, e)
        ps = clean_paint(kind, mm.get("paint"))
        lid_used = mm.get("look") if mm.get("look") in project.looks.data["looks"] else project.looks.data["default"]
        auto = dict(auto_strength(project.looks.look_settings(lid_used)),
                    describe=bool(project.looks.look_settings(lid_used).get("describe_scene")))
        lines = []
        cams = []
        if kind == "scene":
            n = len(e.get("formations") or [])
            ens = scene_enemies(e)
            lines.append(f"Battle location {e.get('location')}: {n} battle setup{'s' if n != 1 else ''} use it.")
            if ens:
                lines.append("Fought here: " + _some(ens, 8))
            try:
                for i, c in enumerate(loaded(kind, mid)["cams"]):
                    k = len(c.formations)
                    cams.append({"label": f"Camera {i + 1}" + (f" · {k} battle setup{'s' if k != 1 else ''}" if k else
                                                               " · the camera most battles start on")})
            except Exception as ex:
                lines.append(f"Couldn't read its cameras: {ex}")
        elif kind == "battle":
            texs = e.get("textures") or []
            if texs:
                sizes = ", ".join(f"{t['width']}×{t['height']}" for t in texs[:6]) + ("…" if len(texs) > 6 else "")
                lines.append(f"{len(texs)} texture{'s' if len(texs) != 1 else ''} ({sizes}).")
                try:
                    m = loaded(kind, mid)["mesh"]
                    k = int((m.tex >= 0).sum())
                    if k < len(m):
                        lines.append(f"Only the textured parts take the painting: {k} of its {len(m)} triangles. The rest "
                                     f"gets its colour from the model itself and stays as it is.")
                except Exception:
                    pass
            if e.get("scenes"):
                lines.append(f"Fought in {len(e['scenes'])} battle scene{'s' if len(e['scenes']) != 1 else ''}: "
                             + _some(e["scenes"], 8))
        else:
            fields = list(e.get("fields") or {})
            names = list(e.get("names") or {})
            lines.append(f"Called {', '.join(names[:4])}{'…' if len(names) > 4 else ''} in the game." if names else
                         "No field uses it.")
            if fields:
                lines.append(f"In {len(fields)} field{'s' if len(fields) != 1 else ''}: " + _some(fields, 10))
            if e.get("first_animation"):
                lines.append(f"Shown in its standing pose (animation {e['first_animation']}, the one fields start it on).")
        lid = mm.get("look")
        return {
            "kind": kind, "id": mid, "title": title(kind, e), "lines": lines, "cameras": cams,
            "paintable": ok, "why_not": why, "versions": versions, "current": cur, "look": lid,
            "error": mm.get("error"), "job_name": job_name(kind, mid),
            "estimate": {m: estimate_for(kind, e, m, ps) for m in ("preview", "final")},
            "default_upscaler": DEFAULT_UPSCALER,
            "method": method_for(kind, ps), "default_method": default_method(kind),
            "parts": {k: len(v) for k, v in scene_parts(kind, mid).items()} if kind == "scene" and ok else None,
            "plans": {m: plan(kind, mid, m, ps) for m in ("preview", "final")} if ok else None,
            "syw": syw_info(kind, e),
            "paint": ps, "paint_auto": auto, "paint_defaults": PAINT_DEFAULTS,
            "inputs": {m: input_for(kind, m, ps) for m in ("preview", "final")},
            "default_inputs": {m: default_input(kind, m) for m in ("preview", "final")},
            # what the AI was told about it last time, automatically
            "last_words": next((auto_words(v) for v in reversed(versions) if v.get("words") is not None or v.get("scene")),
                               ""),
            "view_counts": {m: (SCENE_VIEWS[m]["battle"] if kind == "scene"
                                else len(MODEL_ORBIT[m])) for m in ("preview", "final")},
            "installed": ({"version": inst["version"], "at": inst["at"], "files": len(inst["files"]),
                           "dest": inst.get("dest"), "replaced": sum(1 for f in inst["files"] if f.get("backup"))}
                          if inst else None),
            "can_install": kind != "field" and ok,
            # the files Install writes, by the names the game asks FFNx for (battle/STAGE10_T00 -> STAGE10_T00_00.png)
            "game_files": [f"{t['key'].split('/', 1)[-1]}_00.png" for t in e.get("textures", [])] if kind != "field" else [],
            "dest": str(mod_root() / "battle") if kind != "field" else None,
            "mod_note": mod_note(kind, e),
            "formations": scene_formations(e) if kind == "scene" else [],
            "sheet_count": len(e.get("textures") or []) if kind == "scene" else 0,
        }

    # ---------------------------------------------------------------- trying a scene in the running game

    def scene_formations(e):
        """The battles fought in a scene, for "Try it in a battle": [{id, label}], labelled by their enemies (with
        counts, the most numerous first) and their layout when it isn't a normal one; the battles whose enemies are
        the scene's most common come first. Battles whose enemies have no names (the test formations) are left out."""
        sb = scenebin.load(battle.SCENE_BIN)
        byid = {f.id: f for f in sb.formations}
        common = e.get("enemies") or {}
        out = []
        for fid in e.get("formations") or []:
            f = byid.get(fid)
            if f is None or not f.enemies:
                continue
            names = [sb.names.get(eid, "") for eid, _ in f.enemies]
            if not all(_plain(n) for n in names):
                continue
            c = Counter(names)
            label = ", ".join(f"{n} ×{k}" if k > 1 else n for n, k in c.most_common())
            if f.layout and scenebin.LAYOUTS.get(f.layout):
                label += f" ({scenebin.LAYOUTS[f.layout]})"
            out.append({"id": fid, "label": label, "score": max(common.get(n, 0) for n in c)})
        out.sort(key=lambda x: (-x["score"], x["id"]))
        for x in out:
            del x["score"]
        return out

    def live_dir():
        return game_dir() / "ffnx3d"  # FFNx-3d's live control: cmd.txt in, log.txt and status.txt out

    def game_live():
        """The running game as FFNx-3d's status.txt tells it: running (rewritten in the last LIVE_FRESH s), mode (0 a
        field, 1 a battle), field, battle, god / autoattack, and whether a command file is still waiting."""
        st = live_dir() / "status.txt"
        try:
            age = time.time() - st.stat().st_mtime
            text = st.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"running": False, "age": None, "mode": None}
        info, flags = {}, {}
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "god":
                flags = dict(zip(parts[::2], parts[1::2]))
            else:
                info[parts[0]] = " ".join(parts[1:])
        try:
            mode = int(info.get("mode", "-1"))
        except ValueError:
            mode = -1
        return {"running": age < LIVE_FRESH, "age": round(age, 1), "mode": mode, "field": info.get("field"),
                "battle": info.get("battle"), "god": flags.get("god") == "1", "autoattack": flags.get("autoattack") == "1",
                "waiting": (live_dir() / "cmd.txt").exists()}

    def start_battle(fid, god, autoattack):
        """Send god / autoattack / battle <fid> to the game (only ever from the user's click) and read its answer:
        {"ok": True | False | None, "message": plain words, "log": its new lines}."""
        d = live_dir()
        cmd, log = d / "cmd.txt", d / "log.txt"
        said, new = [], []
        with send_lock:
            if cmd.exists():
                return {"ok": False, "message": "The game hasn't taken its last commands yet, so nothing was sent. Is it "
                                                "running with FFNx-3d? Try again in a moment."}
            before = log.stat().st_size if log.exists() else 0
            lines = [f"god {'on' if god else 'off'}", f"autoattack {'on' if autoattack else 'off'}", f"battle {fid}"]
            text = "\n".join(lines) + "\n"
            d.mkdir(parents=True, exist_ok=True)
            with open(cmd, "a", encoding="utf-8") as f:  # as ff7-3d's live.py writes it
                f.write(text)
            end = time.time() + LIVE_PICKUP
            while cmd.exists() and time.time() < end:
                time.sleep(0.1)
            if cmd.exists():
                try:  # not taken: take it back, so the battle can't start later by surprise
                    if cmd.read_text(encoding="utf-8") == text:
                        cmd.unlink()
                except OSError:
                    pass
                return {"ok": False, "message": "The game didn't take the command within 3 seconds, so no battle was "
                                                "started (the command was taken back). Is the game running with FFNx-3d?"}
            end = time.time() + LIVE_ANSWER
            while True:
                try:
                    with open(log, "rb") as f:
                        f.seek(before)
                        new = f.read().decode("utf-8", errors="replace").splitlines()
                except OSError:
                    new = []
                said = [ln for ln in new if " battle" in f" {ln}"]
                if said or time.time() > end:
                    break
                time.sleep(0.1)
        for ln in said:
            words = ln.split()
            rest = words[1:] if words and words[0].isdigit() else words  # log lines start with the game's frame
            if rest[:2] == ["battle", str(fid)]:
                return {"ok": True, "message": f"Battle {fid} is starting in your game.", "log": new}
            if rest and rest[0].startswith("battle:"):
                said_words = " ".join(rest)[len("battle:"):].strip()
                return {"ok": False, "log": new, "message": (
                    "The game didn't start it: it has to be walking around a field, not in a menu, a battle or a movie, "
                    "and not on its way to another field. Walk around a field and try again."
                    + (f" (The game said: {said_words}.)" if said_words else ""))}
        return {"ok": None, "log": new, "message": "The game took the command but didn't say whether the battle "
                                                   "started: have a look at the game."}

    # ---------------------------------------------------------------- the battle look (battle scenes)

    # an error whose words the queue shows as they are (the worker prefixes others with their type)
    Plain = getattr(studio, "ComfyError", RuntimeError)
    look_last = {}  # scene id -> the last look build's outcome this session: {"ok", "message", "at", "version"}
    GAME_RUNNING = ("Your game is running. Life and Surface relief are worked out by the AI, which shares the graphics "
                    "card with the game, so they wait until it's closed: close the game and build again, or build "
                    "without the AI (Life is then worked out less well, and Surface relief is left out).")
    NO_COMFY = ("ComfyUI isn't running (or isn't reachable), and Life and Surface relief need it: start ComfyUI and "
                "build again, or build without the AI (Life less well, no Surface relief).")

    def bl():
        """ff7bg.models.battlelight, imported when first needed (an error in it shows on the card, not here)."""
        from ff7bg.models import battlelight
        return battlelight

    def look_job_running(mid):
        cur = worker.state().get("current")
        return bool(cur and cur.get("name") == job_name("scene", mid) and cur.get("kind") == JOB_LOOK)

    def look_built_groups(st):
        """Which of the page's parts a build has: light (its light files), haze (haze or the colour match), life, relief
        (its relief pictures; a build without the AI has none)."""
        parts, files = set(st.get("parts") or []), st.get("files") or {}
        return {"light": "light" in parts and bool(files.get("ibl")), "haze": bool(parts & {"haze", "grade"}),
                "life": "life" in parts, "normals": bool(files.get("normals"))}

    def look_lines(st):
        """What a build worked out, per page part, in plain words."""
        v, info, files = st.get("values") or {}, st.get("info") or {}, st.get("files") or {}
        parts = set(st.get("parts") or [])
        s = lambda n: "" if n == 1 else "s"
        out = {}
        if "light_rotation_vertical" in v:
            li = info.get("light") or {}
            h = float(v.get("light_rotation_horizontal", 0)) % 360
            side = ("from behind the party" if h <= 45 or h >= 315 else "from behind the enemies" if 135 <= h <= 225
                    else "from the side")
            share = float(li.get("sun_share") or 0)
            sky = (" The painting looks overcast, so the sun is soft." if share and share <= 0.3 else
                   " The painting looks sunny, so the sun is strong." if share >= 0.45 else "")
            out["light"] = (f"A sun {float(v['light_rotation_vertical']):.0f}° up, {side}"
                            + (f", gives {share * 100:.0f}% of the light; the rest comes from all round, as painted."
                               if share else ".") + sky)
        hz = []
        if "haze" in parts and "haze_color" in v:
            hz.append("distant things fade toward the painting's air colour")
        if "grade" in parts and "char_grade_full" in v:
            g = info.get("grade") or {}
            bits = []
            b, sat, con = float(g.get("brightness", 0)), float(g.get("saturation", 1)), float(g.get("contrast", 1))
            bal = g.get("balance") or [1, 1, 1]
            if b > 4:
                bits.append("a little brighter")
            elif b < -4:
                bits.append("a little darker")
            if sat > 1.05:
                bits.append("more colourful")
            elif sat < 0.95:
                bits.append("less colourful")
            if con > 1.05:
                bits.append("more contrast")
            elif con < 0.95:
                bits.append("softer")
            if len(bal) == 3 and bal[0] - bal[2] > 0.03:
                bits.append("warmer")
            elif len(bal) == 3 and bal[2] - bal[0] > 0.03:
                bits.append("cooler")
            hz.append("the characters' colours are matched to the painting: " +
                      (", ".join(bits) if bits else "close to how they are already"))
        if hz:
            text = "; ".join(hz)
            out["haze"] = text[0].upper() + text[1:] + "."
        if "life" in parts:
            lf = info.get("life") or {}
            drift, sway = len(v.get("uv_scroll") or []), len(v.get("sway") or [])
            kind = lf.get("particles") if files.get("particles") else None
            air = {"dust": "dust drifts in the air", "embers": "embers drift in the air", "snow": "snow falls",
                   "rain": "rain falls", "fireflies": "fireflies float in the air"}
            bits = [f"the sky drifts ({drift} texture{s(drift)})" if drift else "no open sky to drift",
                    f"grass sways ({sway} texture{s(sway)})" if sway else "no grass to sway",
                    air.get(kind, f"{kind} in the air") if kind else "no drifting particles"]
            text = "; ".join(bits)
            out["life"] = (text[0].upper() + text[1:] + "." +
                           (" The AI looked at the scene." if lf.get("asked") else " Worked out without the AI."))
        n = len(files.get("normals") or [])
        if n:
            out["normals"] = f"Relief on {n} painted texture{s(n)} (ground, walls and the like; not the sky)."
        elif "normals" in parts:
            out["normals"] = "Not built: it needs the AI, with the game closed."
        return out

    def look_stale(mid, vid, st):
        """The painted textures that changed (or came or went) since the build: their names."""
        src = st.get("source") or {}
        now = {f"battle/{p.stem}": p.stat().st_mtime
               for p in (model_dir("scene", mid) / _safe_name(vid) / "textures" / "battle").glob("*.png")}
        return [k.split("/")[-1] for k in sorted(set(src) | set(now))
                if k not in src or k not in now or abs(float(src[k]) - now[k]) > 0.01]

    def look_installed_parts(B, inst):
        """The build's parts an install put in: as it recorded them, or (installs from before it did) from what's in the
        game's config.toml sections and files."""
        if isinstance(inst.get("parts"), list):
            return inst["parts"]
        try:
            cfg = tomllib.loads((B.LIGHTING / "config.toml").read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            cfg = {}
        keys = {k for t in inst.get("sections", []) for k in (cfg.get(t) or {})}
        names = [Path(f["path"]).name for f in inst.get("files", [])]
        has = {"light": any(k.startswith("light_") for k in keys) or any(n.endswith(".dds") for n in names),
               "haze": any(k.startswith("haze_") for k in keys), "grade": any(k.startswith("char_grade") for k in keys),
               "life": bool({"uv_scroll", "sway"} & keys) or any(n.endswith("_particles.toml") for n in names),
               "normals": any(n.endswith(B.NML) for n in names)}
        return [p for p in B.PARTS if has.get(p)]

    def relief_tiles(B, mid, vid):
        """(textures, Marigold tiles) a relief build of this version works through (battlelight.normals: the painted,
        lit, not-far textures, each at half size in tiles of at most MG_TILE)."""
        meta = version_meta("scene", mid, vid) or {}
        try:
            m = loaded("scene", mid)["mesh"]
            far = set(scene_parts("scene", mid)["far"])
            unlit = B.unlit_textures()
        except Exception:
            return 0, 0
        index = {ref.name: k for k, ref in enumerate(m.textures)}
        n = tiles = 0
        for t in meta.get("textures", []):
            k = index.get(t.get("name"))
            if k is None or k in far or m.textures[k].key.split("/", 1)[-1].upper() in unlit:
                continue
            w, h = max(1, int(t.get("width") or 1)), max(1, int(t.get("height") or 1))
            side = min(B.NORMAL_MAX, max(64, max(w, h) // 2))
            ow, oh = (side, round(side * h / w)) if w >= h else (round(side * w / h), side)
            n += 1
            tiles += math.ceil(oh / B.MG_TILE) * math.ceil(ow / B.MG_TILE)
        return n, tiles

    def look_seconds(B, mid, vid):
        """Rough seconds per step of a build of this version: {"load", "view", "light", "haze" (with the colour
        match), "life", "life_ai", "normals"}."""
        n, tiles = relief_tiles(B, mid, vid) if vid else (0, 0)
        S_ = LOOK_SECONDS
        return {"load": S_["load"], "view": S_["view"], "light": S_["light"], "haze": S_["haze"] + S_["grade"],
                "life": S_["life"], "life_ai": S_["life_ai"],
                "normals": S_["normals"] + n * S_["normals_tex"] + tiles * S_["normals_tile"]}

    def look_estimate(secs, parts, ai):
        t = secs["load"] + (secs["view"] if ("light" in parts or "haze" in parts) else 0.0)
        t += secs["light"] if "light" in parts else 0.0
        t += secs["haze"] if ("haze" in parts or "grade" in parts) else 0.0
        t += (secs["life_ai"] if ai else secs["life"]) if "life" in parts else 0.0
        t += secs["normals"] if ("normals" in parts and ai) else 0.0
        return round(t)

    def comfy_up(B):
        try:
            with urllib.request.urlopen(B._ff3d("comfy").HOST + "/system_stats", timeout=5) as r:
                r.read()
            return True
        except (OSError, ValueError):
            return False

    def look_view(e):
        """A battle scene's battle look for the page: the ★ version's build (what it has, in plain words, whether the
        painting changed since, its preview pictures), the other versions built, what's in the game, the last build's
        outcome, rough seconds per part."""
        mid = key_of("scene", e)
        cur = current("scene", mid)
        out = {"id": mid, "current": cur, "build": None, "builds": [], "installed": None, "problems": [],
               "painting_installed": (installed_all().get(f"scene/{mid}") or {}).get("version"),
               "last": look_last.get(mid), "running": look_job_running(mid), "seconds": None, "error": None}
        try:
            B = bl()
        except Exception as ex:
            traceback.print_exc()
            out["error"] = f"The battle look's code didn't load: {type(ex).__name__}: {ex}"
            return out
        d = B.WORK / mid
        out["builds"] = sorted(p.name for p in d.iterdir() if (p / "settings.json").exists()) if d.exists() else []
        out["seconds"] = look_seconds(B, mid, cur)
        st = B.built(mid, cur) if cur else None
        if st:
            vdir = B.WORK / mid / cur
            out["build"] = {"version": cur, "built": st.get("built"), "groups": look_built_groups(st),
                            "parts": st.get("parts") or [], "lines": look_lines(st),
                            "haze_color": (st.get("values") or {}).get("haze_color") if "haze" in (st.get("parts") or []) else None,
                            "stale": look_stale(mid, cur, st),
                            "previews": [n for n in LOOK_PREVIEWS if (vdir / n).exists()]}
        inst = B.installed().get(mid)
        if inst:
            parts = look_installed_parts(B, inst)
            out["installed"] = {"version": inst.get("version"), "at": inst.get("at"), "files": len(inst.get("files", [])),
                                "groups": [g for g, ps in LOOK_GROUPS.items() if any(p in parts for p in ps)
                                           and (g != "normals" or any(f["path"].endswith(B.NML) for f in inst.get("files", [])))],
                                "normals_skipped": len(inst.get("normals_skipped") or [])}
            try:
                out["problems"] = B.check(mid)
            except Exception as ex:
                out["problems"] = [f"Couldn't check it: {type(ex).__name__}: {ex}"]
        return out

    def run_look(w, job):
        """A battle look build (battlelight.build) of the version the job names, with its parts and the AI or not."""
        kind, mid = parse_job_name(job["name"])
        if kind != "scene":
            raise Plain("Only battle scenes have a battle look.")
        B = bl()
        js = job.get("settings") or {}
        vid = job.get("version")
        parts = [p for p in B.PARTS if p in (js.get("parts") or [])]
        ai = bool(js.get("ai")) and any(p in B.AI_PARTS for p in parts)
        if not parts:
            raise Plain("No part was ticked, so nothing was built.")
        if not vid or not version_meta(kind, mid, vid):
            raise Plain("The painted version it was to be built from is gone, so nothing was built.")
        started = time.time()
        try:
            if ai:
                w._stage("checking that the game is closed", 0.01)
                if B.game_running():
                    raise Plain(GAME_RUNNING)
                w._stage("connecting to ComfyUI", 0.02)
                if not comfy_up(B):
                    raise Plain(NO_COMFY)
            secs = look_seconds(B, mid, vid)
            w._stage("loading the scene and its painting", 0.03)
            steps = [("load", secs["load"])] + ([("view", secs["view"])] if ("light" in parts or "haze" in parts) else []) + [
                (p, {"light": secs["light"], "haze": secs["haze"] / 4, "grade": secs["haze"] * 3 / 4,
                     "life": secs["life_ai"] if ai else secs["life"], "normals": secs["normals"] if ai else 0.5}[p])
                for p in parts]
            total = max(sum(t for _, t in steps), 1.0)
            at, acc = {}, 0.0
            for name, t in steps:
                at[name] = acc / total
                acc += t
            words = {"view": "looking all round the arena from the middle of the fight",
                     "light": "working out the light from the painting",
                     "haze": "working out the haze", "grade": "matching the characters' colours to the painting",
                     "life": "finding sky to drift, grass to sway and particles" + (" (asking the AI)" if ai else ""),
                     "normals": "working out the surface relief (the AI looks at each painted texture)" if ai
                     else "surface relief needs the AI: skipped"}

            def step(part):
                if w.cancel_flag:
                    raise InterruptedError("cancelled")
                w._stage(words.get(part, part), 0.03 + 0.92 * at.get(part, 0.0))

            st = B.build(mid, vid, parts, ai=ai, allow_game=False, step=step,
                         progress=lambda m: print(f"battle look: {m}"))
            if ai and "normals" in parts and (st.get("files") or {}).get("normals"):
                w._stage("drawing the relief preview", 0.97)
                try:
                    relief_preview(B, B.WORK / mid / vid, model_dir(kind, mid) / _safe_name(vid) / "textures" / "battle", st)
                except Exception:
                    traceback.print_exc()  # only a picture for the page
        except InterruptedError:
            look_last[mid] = {"ok": False, "message": "cancelled", "at": time.time(), "version": vid}
            raise
        except (urllib.error.URLError, ConnectionError) as ex:
            look_last[mid] = {"ok": False, "message": NO_COMFY, "at": time.time(), "version": vid}
            raise Plain(NO_COMFY) from ex
        except Exception as ex:
            msg = str(ex) if isinstance(ex, Plain) else f"{type(ex).__name__}: {ex}"
            look_last[mid] = {"ok": False, "message": msg, "at": time.time(), "version": vid}
            raise
        names = {"light": "light", "haze": "haze", "grade": "colour match", "life": "life", "normals": "relief"}
        made = [names[p] for p in parts if p != "normals" or (ai and (st.get("files") or {}).get("normals"))]
        job["note"] = f"battle look built: {', '.join(made)} ({round(time.time() - started)} s)"
        look_last[mid] = {"ok": True, "message": job["note"], "at": time.time(), "version": vid}
        w._stage("done", 1.0)

    # ---------------------------------------------------------------- paint in 2D style (battle models)

    P2D_GAME_RUNNING = ("Your game is running. Painting in 2D style is done by the AI, which shares the graphics card "
                        "with the game, so it has to wait until the game is closed: close the game, then press Paint "
                        "again.")
    P2D_GAME_RUNNING_CHECK = ("Your game is running. The AI check is done by the AI, which shares the graphics card with "
                              "the game, so it has to wait until the game is closed: close the game, then press AI check "
                              "again.")
    P2D_GAME_RUNNING_TOUCH = ("Your game is running. Touching up is done by the AI, which shares the graphics card with "
                              "the game, so it has to wait until the game is closed: close the game, then press Repaint "
                              "marked areas again.")
    P2D_NO_COMFY = ("ComfyUI isn't running (or isn't reachable), and painting in 2D style needs it: start ComfyUI, then "
                    "press Paint again.")
    P2D_NO_NAME = ("The game's own name for this model isn't known here (it's one of the Galian Beast's forms), so the "
                   "game couldn't find a painting for it: it can't be painted in 2D style.")
    p2d_game = {"at": 0.0, "running": False}  # the game running, as last asked (tasklist), for the page
    p2d_secs = {"at": 0.0, "paint": None, "check": None}  # the AI's part of the 2D paints and checks so far, on average

    def batlas():
        """ff7bg.models.battleatlas, imported when first needed (an error in it shows on the card, not here)."""
        from ff7bg.models import battleatlas
        return battleatlas

    def p2x():
        """ff7bg.models.paint2d_extras (variants, the AI check, the touch-up), imported when first needed."""
        from ff7bg.models import paint2d_extras
        return paint2d_extras

    def p2d_settings(mid):
        return clean_p2d(model_meta("battle", mid).get("paint2d"), project.looks.data["looks"])

    def p2d_default_look(A):
        """battleatlas' look (painterly) when it's one of the user's, else the Studio's default look."""
        data = project.looks.data
        return A.LOOK if A.LOOK in data["looks"] else data["default"]

    def p2d_effective(A, ps):
        """What a paint uses: the user's settings, and battleatlas' own where they're None."""
        return {"look": ps["look"] or p2d_default_look(A),
                "strength": ps["strength"] if ps["strength"] is not None else float(A.STRENGTH),
                "size": ps["size"] or int(A.ATLAS_SIZE), "words": ps["words"],
                "variants": ps.get("variants") or 1, "retry": ps.get("retry") is not False}

    def p2d_game_running(fresh=False):
        """Is the game running (battlelight.game_running: the task list)? Asked at most every 3 s for the page."""
        if fresh or time.time() - p2d_game["at"] > 3:
            p2d_game.update(running=bool(bl().game_running()), at=time.time())
        return p2d_game["running"]

    def p2d_time(text, fallback):
        """battleatlas' "%Y-%m-%d %H:%M:%S" times as seconds since 1970 (the page's times)."""
        try:
            return time.mktime(time.strptime(str(text), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            return fallback

    def p2d_dirs(A, mid):
        """The model's build folders (battleatlas' versions: an info.json in them; not one being made)."""
        d = A.WORK / mid
        if not d.is_dir():
            return []
        return [p for p in d.iterdir() if p.is_dir() and not p.name.endswith(".partial") and (p / "info.json").exists()]

    def p2d_versions(A, mid):
        """The model's 2D versions, oldest first: what each was painted with and its pictures."""
        out = []
        for p in p2d_dirs(A, mid):
            try:
                info = json.loads((p / "info.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pt, st = info.get("painted") or {}, info.get("studio") or {}
            prev = p / "preview"
            tu = info.get("touchup") or None
            out.append({"id": p.name, "at": p2d_time(info.get("at"), p.stat().st_mtime), "painted": bool(pt),
                        "look": pt.get("look"), "strength": pt.get("strength"),
                        "size": (info.get("atlas") or {}).get("size"),
                        "seconds": (tu.get("seconds") if tu else pt.get("seconds")),
                        "views": 0 if tu else len(pt.get("views") or []),
                        "studio": bool(st), "words": st.get("words") if st else None,
                        "prompt": pt.get("words") or "", "described": pt.get("describer") or "",
                        "graded": bool(info.get("graded")), "pictures": [n for n in P2D_PICTURES if (prev / n).exists()],
                        "view_pictures": sorted(x.name for x in prev.glob("view_*.jpg") if P2D_VIEW.fullmatch(x.name))
                        if prev.is_dir() else [],
                        # one paint job's variants share a batch; variant k, try t (a repaint after a failed check)
                        "batch": st.get("batch"), "variant": st.get("variant"), "try": st.get("try"),
                        "check": p2d_check_summary(p),
                        "touchup": ({"source": tu.get("source"), "view": tu.get("label"), "areas": tu.get("areas"),
                                     "hint": tu.get("hint") or "", "strength": tu.get("strength")} if tu else None)})
        out.sort(key=lambda v: (v["at"], v["id"]))
        return out

    def p2d_check_summary(d):
        """A version's AI check for the page: {"ok": True / False / None (it couldn't run), "reason", "problems":
        the views' remarks in plain words, "at"}, or None when it hasn't been checked."""
        try:
            res = p2x().load_check(d)
        except Exception:
            traceback.print_exc()  # the versions show without it
            return None
        if not res:
            return None
        return {"ok": res.get("ok"), "reason": res.get("reason") or res.get("error") or "",
                "problems": [x.get("text") for x in res.get("problems") or [] if x.get("text")][:8],
                "at": p2d_time(res.get("at"), None)}

    def p2d_current(A, mid, versions):
        """The ★ version: the one picked (model.json "current2d"), else the one in the game, else the newest painted."""
        ids = [v["id"] for v in versions]
        cur = model_meta("battle", mid).get("current2d")
        if cur in ids:
            return cur
        inst = (A.installed().get(mid) or {}).get("version")
        if inst in ids:
            return inst
        return ([v["id"] for v in versions if v["painted"]] or ids or [None])[-1]

    def p2d_status():
        """Per battle model with 2D versions: "game" (in the game), "painted" (a painted version) or "built" (only the
        game's own colours on the new texture): the list's status."""
        try:
            A = batlas()
            out = {}
            if A.WORK.is_dir():
                for d in A.WORK.iterdir():
                    if not d.is_dir():
                        continue
                    found = ""
                    for p in p2d_dirs(A, d.name):
                        found = found or "built"
                        try:
                            if json.loads((p / "info.json").read_text(encoding="utf-8")).get("painted"):
                                found = "painted"
                                break
                        except (OSError, ValueError):
                            pass
                    if found:
                        out[d.name] = found
            for k in A.installed():
                out[k] = "game"
            return out
        except Exception:
            traceback.print_exc()  # the list shows without it
            return {}

    def p2d_job(mid):
        """This model's 2D paint in the Studio's queue: "running", "queued" or None."""
        st, name = worker.state(), job_name("battle", mid)
        cur = st.get("current")
        if cur and cur.get("name") == name and cur.get("kind") == JOB_2D:
            return "running"
        return "queued" if any(j["name"] == name and j["kind"] == JOB_2D for j in st["queued"]) else None

    def p2d_rel(path):
        """A path in the game's folder, from there (as the page shows them)."""
        game = str(bl().GAME).rstrip("\\/") + os.sep
        path = str(path)
        return path[len(game):] if path.lower().startswith(game.lower()) else path

    def p2d_problems(inst):
        """What isn't as Install left it: its files gone, or changed since."""
        import hashlib
        out = []
        for f in inst.get("files", []):
            p = Path(f["path"])
            try:
                if not p.exists():
                    out.append(f"{p2d_rel(p)} is gone")
                elif hashlib.sha1(p.read_bytes()).hexdigest() != f.get("sha1"):
                    out.append(f"{p2d_rel(p)} was changed after Install put it there")
            except OSError as ex:
                out.append(f"{p2d_rel(p)}: {ex}")
        return out

    def p2d_view_count(A, mid):
        """How many views a paint of this model has (battleatlas.model_views: its round, close and moving parts'
        views, and its battle cameras), or None."""
        try:
            mv = A.model_views(mid)
            n = (len(mv.get("orbit") or []) + len(mv.get("close") or [])
                 + sum(len(p.get("views") or []) for p in mv.get("moving") or []))
            if mv.get("battle_cameras", "first") != "none":
                n += len(A.battle_views(mid))
            return n or None
        except Exception:
            return None

    def p2d_estimate(A, mid, size, variants=1):
        """Rough seconds a 2D paint takes: per variant its views at what a view took in the paints so far
        (P2D_VIEW_SECONDS before any), the rest, which grows with the painting's size, and the AI check (what the
        checks so far took, P2D_CHECK_SECONDS before any). A repaint after a failed check adds one more of those."""
        if time.time() - p2d_secs["at"] > 30:
            per, chk = [], []
            try:
                for p in A.WORK.glob("*/*/info.json"):
                    try:
                        pt = json.loads(p.read_text(encoding="utf-8")).get("painted") or {}
                    except (OSError, ValueError):
                        continue
                    s, n = pt.get("seconds"), len(pt.get("views") or [])
                    if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 and n:
                        per.append(float(s) / n)
                for p in A.WORK.glob("*/*/check.json"):
                    try:
                        s = json.loads(p.read_text(encoding="utf-8")).get("seconds")
                    except (OSError, ValueError):
                        continue
                    if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0:
                        chk.append(float(s))
            except OSError:
                pass
            p2d_secs.update(at=time.time(), paint=sum(per) / len(per) if per else None,
                            check=sum(chk) / len(chk) if chk else None)
        views = p2d_view_count(A, mid) or P2D_VIEWS
        one = (views * (p2d_secs["paint"] or P2D_VIEW_SECONDS) + P2D_EXTRA_SECONDS * max(1.0, (size / 2048) ** 2)
               + (p2d_secs.get("check") or P2D_CHECK_SECONDS))
        return round(one * max(1, int(variants)))

    def p2d_view(e):
        """A battle model's 2D painting for the page: its settings (and what they mean with battleatlas' defaults), its
        versions with their pictures, the ★ one, what's in the game (and whether it's as Install left it), the game
        running, the last failure, rough seconds."""
        mid = key_of("battle", e)
        out = {"id": mid, "title": title("battle", e), "party": e["kind"] == "party", "can": True, "why_not": "",
               "versions": [], "current": None, "installed": None, "problems": [], "error": None,
               "job": p2d_job(mid), "game_running": False}
        try:
            A = batlas()
        except Exception as ex:
            traceback.print_exc()
            out["error"] = f"The 2D painting's code didn't load: {type(ex).__name__}: {ex}"
            return out
        if not battle.game_name(mid):
            out.update(can=False, why_not=P2D_NO_NAME)
        ps = p2d_settings(mid)
        eff = p2d_effective(A, ps)
        s = project.looks.look_settings(eff["look"])
        versions = p2d_versions(A, mid)
        cur = p2d_current(A, mid, versions)
        inst = A.installed().get(mid)
        for v in versions:
            v["current"] = v["id"] == cur
            v["installed"] = bool(inst) and inst.get("version") == v["id"]
        tex_inst = installed_all().get(f"battle/{mid}")
        try:
            dest = p2d_rel(A.atlas_dir())
        except Exception:
            dest = "mesh\\battle\\atlas"
        out.update({
            "settings": ps, "effective": eff,
            "defaults": {"look": p2d_default_look(A), "strength": float(A.STRENGTH), "size": int(A.ATLAS_SIZE),
                         "variants": 1, "retry": True},
            "sizes": list(P2D_SIZES), "strength_range": list(STRENGTH_RANGE), "variant_choices": list(P2D_VARIANTS),
            "retries": P2D_RETRIES, "touch_strength_range": list(P2D_TOUCH_STRENGTH),
            "uses_strength": s.get("family") in REPAINT_FAMILIES, "family": s.get("family"),
            "versions": versions, "current": cur,
            "installed": ({"version": inst.get("version"), "at": p2d_time(inst.get("at"), None),
                           "files": len(inst.get("files", []))} if inst else None),
            "problems": p2d_problems(inst)[:8] if inst else [],
            "error": model_meta("battle", mid).get("error2d"),
            "game_running": p2d_game_running(),
            "estimate": p2d_estimate(A, mid, eff["size"], eff["variants"]),
            "estimate_one": p2d_estimate(A, mid, eff["size"]),
            "touch_estimate": round(P2D_TOUCH_SECONDS + (p2d_secs.get("check") or P2D_CHECK_SECONDS)),
            "dest": dest, "game_name": battle.game_name(mid),
            "textures_installed": bool(tex_inst),
        })
        return out

    def p2d_label(label):
        """battleatlas' view labels in the page's words ("formation 469 camera 0" -> "battle 469's camera 1")."""
        m = re.fullmatch(r"formation (\d+) camera (\d+)", str(label))
        return f"battle {m.group(1)}'s camera {int(m.group(2)) + 1}" if m else str(label)

    def p2d_next(A, mid):
        """The next version name (v1, v2, ...) after every one there, those being made included."""
        d = A.WORK / mid
        n = [int(m.group(1)) for p in (d.iterdir() if d.is_dir() else [])
             if (m := re.fullmatch(r"v(\d+)(?:\.partial)?", p.name))]
        return f"v{max(n, default=0) + 1}"

    def p2d_identity(A, bat, out):
        """battleatlas' identity check from the model's resting battle camera (as its build command does): the game's
        own colours on the new texture against the original, preview/identity.png and the numbers; None when the
        model has no battle camera."""
        try:
            views = A.battle_views(bat.id)
            if not views:
                return None
            _, cam, pos, _ = views[0]
            yaw = A.model_yaw(bat.id) if hasattr(A, "model_yaw") else A.ENEMY_YAW  # the party faces the other way
            o, a, st = A.identity(bat, cam, pos, yaw)
            d = np.abs(o.astype(int) - a.astype(int)).max(-1)
            heat = np.clip(d * 16, 0, 255).astype(np.uint8)
            Image.fromarray(np.hstack([o, a, np.dstack([heat] * 3)])).save(Path(out) / "identity.png")
            return st
        except Exception:
            traceback.print_exc()  # only a check
            return None

    def p2d_partial_cleanup(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            tmp.parent.rmdir()  # the model's folder, when this was to be its first version
        except OSError:
            pass

    def p2d_finish_dir(A, mid, tmp, vid):
        """The finished <vid>.partial renamed to its version (the next free one, if another was made meanwhile)."""
        while (A.WORK / mid / vid).exists():  # made by someone else meanwhile (battleatlas' own command)
            vid = f"v{int(vid[1:]) + 1}"
        tmp.rename(A.WORK / mid / vid)
        return vid

    def p2d_make(w, A, mid, eff, seed=None, prefix="", extra=None):
        """battleatlas.make (the CLI's `paint`: the texture laid out, the identity check, the paint with the model's own
        recipe, the game's colours put back, the grade, the pictures and the files, checked) for the queue, painted with
        `seed` (paint2d_extras.seeded; None: the look's own). Made as <version>.partial and renamed when complete, so a
        cancel or a failure leaves nothing half made; the views a touch-up can use are stored with it (views.json).
        Returns the version id. The painting size: the user's, else battleatlas' own choice (smaller for the party).
        prefix: the stage texts' start ("variant 2 of 3: "); extra: more for info.json's "studio"."""
        X = p2x()
        t0 = time.time()
        vid = p2d_next(A, mid)
        tmp = A.WORK / mid / f"{vid}.partial"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.parent.mkdir(parents=True, exist_ok=True)

        def progress(i, n, label):
            w._stage(f"{prefix}painting the views: {i} of {n} done (the last: {p2d_label(label)})",
                     0.1 + 0.8 * i / max(n, 1))

        w._stage(f"{prefix}laying out the model's surface on one painted texture, then painting the first view", 0.05)
        try:
            size = None if eff["size"] == getattr(A, "ATLAS_SIZE", None) else eff["size"]
            try:
                with X.seeded(seed):
                    _folder, info = A.make(mid, size=size, look=eff["look"], strength=eff["strength"],
                                           extra_words=eff["words"] or "", url=project.comfy_url, progress=progress,
                                           cancelled=lambda: bool(w.cancel_flag), out_dir=tmp)
            except ValueError as e:
                raise Plain(f"The painted texture didn't come out right, so it wasn't kept: {e}")
            w._stage(f"{prefix}saving", 0.97)
            info_file = tmp / "info.json"
            if info_file.exists():
                data = json.loads(info_file.read_text(encoding="utf-8"))
                data["studio"] = {"words": eff["words"], "look": eff["look"], "strength": eff["strength"],
                                  "size": size, "seconds": round(time.time() - t0), "seed": seed, **(extra or {})}
                info_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
            try:  # the cameras of the views a touch-up can use (made again from the code when missing)
                model, _img = X.battle_model(mid, tmp.name)
                X.save_views(tmp, model.touch_views())
            except Exception:
                traceback.print_exc()
            vid = p2d_finish_dir(A, mid, tmp, vid)
        except BaseException:
            p2d_partial_cleanup(tmp)
            raise
        return vid

    def p2d_check(w, A, mid, vid, prefix=""):
        """The AI check of a version (paint2d_extras.check: check.json and preview/check.jpg in its folder). It's
        advice: when it can't run (the describer's model missing, ComfyUI's error), check.json says why and nothing
        else changes. A cancel raises InterruptedError (the version stays, not checked)."""
        X = p2x()
        d = A.WORK / mid / vid
        stop = lambda: bool(w.cancel_flag)  # noqa: E731
        try:
            w._stage(f"{prefix}the AI is checking {vid}: drawing it and the original from the same angles", 0.05)
            model, img = X.battle_model(mid, vid)
            w._stage(f"{prefix}the AI is checking {vid}: comparing it with the original from "
                     f"{len(X.check_views(model))} angles", 0.15)
            if not (d / X.VIEWS_FILE).exists():
                X.save_views(d, model.touch_views())
            return X.check(model, img, project.comfy_url, d, cancelled=stop)
        except InterruptedError:
            raise
        except Exception as ex:
            if w.cancel_flag:
                raise InterruptedError("cancelled") from ex
            traceback.print_exc()
            res = {"ok": None, "reason": "", "error": f"{type(ex).__name__}: {ex}"[:300],
                   "at": time.strftime("%Y-%m-%d %H:%M:%S"), "problems": [], "views": []}
            try:
                X.save_check(d, res)
            except OSError:
                pass
            return res

    def p2d_check_words(res):
        """A check's result in a few words, for the job's note."""
        if not res or res.get("ok") is None:
            return "AI check couldn't run" + (f" ({res['error']})" if res and res.get("error") else "")
        return "AI check passed" if res["ok"] else f"AI check: {res.get('reason') or 'a problem'}"

    def p2d_ready(w, mid):
        """The game closed and ComfyUI up (the paint, the check and the touch-up all need the graphics card)."""
        if not battle.game_name(mid):
            raise Plain(P2D_NO_NAME)
        w._stage("checking that the game is closed", 0.01)
        if p2d_game_running(fresh=True):
            raise Plain(P2D_GAME_RUNNING)
        w._stage("connecting to ComfyUI", 0.02)
        if not studio.Comfy(project.comfy_url).online():
            raise Plain(P2D_NO_COMFY)

    def p2d_errors(kind, mid, fn):
        """fn() with run_p2d's failures in plain words (the card's last error, model.json "error2d")."""
        import requests
        try:
            return fn()
        except InterruptedError:
            raise
        except (urllib.error.URLError, ConnectionError, requests.ConnectionError) as ex:
            save_model_meta(kind, mid, error2d=P2D_NO_COMFY)
            raise Plain(P2D_NO_COMFY) from ex
        except ValueError as ex:
            msg = str(ex)
            if "no formation" in msg:  # battleatlas.paint: no battle puts it anywhere to paint it from
                msg = ("It couldn't be painted: the painting starts from a battle's own cameras, looking at the model "
                       "where that battle puts it, and the game's battle data has no battle with it in. "
                       f"(It said: {ex}.)")
            else:
                msg = f"ValueError: {ex}"
            save_model_meta(kind, mid, error2d=msg)
            raise Plain(msg) from ex
        except Exception as ex:
            save_model_meta(kind, mid, error2d=str(ex) if isinstance(ex, Plain) else f"{type(ex).__name__}: {ex}")
            raise

    def run_p2d(w, job):
        """A 2D paint of a battle model (JOB_2D) with the settings it was queued with: the game closed and ComfyUI up
        first, then its variants, each painted with its own seed (p2d_make) and checked by the AI (p2d_check); with
        "retry" on, a variant the check fails is painted again with a new seed, up to P2D_RETRIES more times (the
        failed ones stay: the check is advice). The progress covers every variant (a repaint adds to it). Becomes ★:
        the first that passed the check, else the first made. A cancel keeps the variants finished before it. The
        game isn't touched."""
        X = p2x()
        kind, mid = parse_job_name(job["name"])
        if kind != "battle":
            raise Plain("Only battle characters and enemies are painted in 2D style.")
        e = entry_or_raise(kind, mid)
        mid = key_of(kind, e)
        A = batlas()
        eff = p2d_effective(A, clean_p2d(job.get("settings"), project.looks.data["looks"]))
        n, auto = int(eff["variants"]), bool(eff["retry"])
        started = time.time()
        batch = time.strftime("%Y%m%d-%H%M%S")
        made, results = [], {}
        plan = {"done": 0, "total": n}  # paints done, paints planned (a repaint adds one)

        def band(lo, hi):  # the share of the job's bar this paint's part reports into; never back (a repaint after a
            k, t = plan["done"], plan["total"]  # failed check holds the bar where it is until the work catches up)
            shown = float((w.current or {}).get("progress") or 0) if isinstance(w.current, dict) else 0.0
            w.band = (max((k + lo) / t, min(shown, (k + hi) / t)), (k + hi) / t)

        def pick():
            ok = [v for v in made if (results.get(v) or {}).get("ok")]
            return (ok or made or [None])[0]
        try:
            p2d_errors(kind, mid, lambda: p2d_ready(w, mid))
            for k in range(n):
                for t in range(1 + (P2D_RETRIES if auto else 0)):
                    if w.cancel_flag:
                        raise InterruptedError("cancelled")
                    what = [f"variant {k + 1} of {n}"] if n > 1 else []
                    if t:
                        what.append(f"repaint {t} of {P2D_RETRIES} after the AI check")
                    prefix = f"{', '.join(what)}: " if what else ""
                    band(0.0, 0.88)
                    vid = p2d_errors(kind, mid, lambda: p2d_make(
                        w, A, mid, eff, seed=X.new_seed(), prefix=prefix,
                        extra={"batch": batch, "variant": k + 1, "try": t + 1, "variants": n}))
                    made.append(vid)
                    band(0.88, 1.0)
                    res = p2d_check(w, A, mid, vid, prefix)
                    results[vid] = res
                    plan["done"] += 1
                    if res.get("ok") is not False or not auto or t == P2D_RETRIES:
                        break
                    plan["total"] += 1
        finally:
            w.band = (0.0, 1.0)
            if made:  # (also when a later variant failed or was cancelled: the ones made are kept)
                save_model_meta(kind, mid, current2d=pick())
        save_model_meta(kind, mid, error2d=None)
        job["version"] = pick()
        job["made"] = made
        secs = round(time.time() - started)
        if len(made) == 1:
            job["note"] = f"painted in 2D style: {made[0]} in {secs} s ({p2d_check_words(results.get(made[0]))})"
        else:
            passed = [v for v in made if (results.get(v) or {}).get("ok")]
            job["note"] = (f"painted {len(made)} versions in 2D style ({', '.join(made)}) in {fmt_secs(secs)}: "
                           f"{len(passed)} passed the AI check" + (f", {pick()} is ★" if pick() else ""))
        w._stage("done", 1.0)

    def fmt_secs(s):
        return f"{s} s" if s < 90 else f"{round(s / 60)} min"

    def p2d_est(name):
        try:
            kind, mid = parse_job_name(name)
            A = batlas()
            eff = p2d_effective(A, p2d_settings(mid))
            return p2d_estimate(A, mid, eff["size"], eff["variants"])
        except Exception:
            return round(P2D_VIEWS * P2D_VIEW_SECONDS + P2D_EXTRA_SECONDS + P2D_CHECK_SECONDS)

    def run_p2d_check(w, job):
        """The AI check of one 2D version (JOB_2D_CHECK; the card's "AI check" on a version painted before the check
        existed, or to check again)."""
        kind, mid = parse_job_name(job["name"])
        e = entry_or_raise(kind, mid)
        mid = key_of(kind, e)
        A = batlas()
        vid = job.get("version")
        if not vid or vid not in {v["id"] for v in p2d_versions(A, mid)}:
            raise Plain("That version is gone, so it wasn't checked.")
        p2d_ready(w, mid)
        res = p2d_check(w, A, mid, vid)
        if res.get("ok") is None:
            raise Plain(f"The AI check of {vid} couldn't run: {res.get('error') or 'no answer'}.")
        job["note"] = f"{vid}: {p2d_check_words(res)}"
        w._stage("done", 1.0)

    # a version's views for the touch-up: (WORK, model, version, PNG's time) -> (paint2d_extras model, texture)
    p2d_models = OrderedDict()
    p2d_models_lock = threading.Lock()

    def p2d_model(A, mid, vid):
        X = p2x()
        d = A.WORK / mid / vid
        png = next(iter(sorted(d.glob("*.png"))), None)
        key = (str(A.WORK), mid, vid, png.stat().st_mtime if png else 0)
        with p2d_models_lock:
            if key in p2d_models:
                p2d_models.move_to_end(key)
                return p2d_models[key]
            got = X.battle_model(mid, vid)
            p2d_models[key] = got
            while len(p2d_models) > 2:
                p2d_models.popitem(last=False)
            return got

    def p2d_touch_views(A, mid, vid):
        """The version's touch-up views (stored with it: views.json, made now when it hasn't one)."""
        model, _img = p2d_model(A, mid, vid)
        return p2x().views_of(model, A.WORK / mid / vid)

    def run_p2d_touchup(w, job):
        """A touch-up of a 2D version (JOB_2D_TOUCH): its view drawn from its painted texture, the areas the user
        marked repainted with the version's look and words at the strength asked (paint2d_extras.touch_up), only
        those pixels put back on the texture, then a new version of it (the pictures, the files, checked) and the AI
        check. The new version becomes ★ when its source was; the game isn't touched (a version in the game stays
        there until the user installs the new one)."""
        X = p2x()
        kind, mid = parse_job_name(job["name"])
        e = entry_or_raise(kind, mid)
        mid = key_of(kind, e)
        A = batlas()
        src = job.get("version")
        mask_path = Path(job.get("mask") or "")
        started = time.time()
        try:
            if not src or src not in {v["id"] for v in p2d_versions(A, mid)}:
                raise Plain("The version to touch up is gone, so nothing was done.")
            p2d_ready(w, mid)
            d = A.WORK / mid / src
            was_current = p2d_current(A, mid, p2d_versions(A, mid)) == src
            w._stage(f"drawing {src}'s view from its painted texture", 0.04)
            model, img = X.battle_model(mid, src)
            views = {v.key: v for v in X.views_of(model, d)}
            view = views.get(job.get("view"))
            if view is None:
                raise Plain("That view of the model isn't there any more, so nothing was done.")
            marks = np.asarray(Image.open(mask_path).convert("RGBA").getchannel("A"), np.float32) / 255
            info = json.loads((d / "info.json").read_text(encoding="utf-8"))
            pt = info.get("painted") or {}
            strength = float(job.get("strength") or pt.get("strength") or A.STRENGTH)
            hint = (job.get("hint") or "").strip()
            w.band = (0.05, 0.7)
            new, tinfo, before, after = X.touch_up(
                model, img, view, marks, pt.get("look") or p2d_default_look(A), strength, pt.get("prompt") or "",
                hint, lock=info.get("locked"), url=project.comfy_url, cancelled=lambda: bool(w.cancel_flag),
                progress=lambda i, n: w._stage(f"repainting marked area {i + 1} of {n} in {view.label}", i / max(n, 1)))
            w.band = (0.7, 0.88)
            w._stage("putting the repainted parts back on the painted texture, drawing the pictures", 0.0)
            vid = p2d_next(A, mid)
            tmp = A.WORK / mid / f"{vid}.partial"
            if tmp.exists():
                shutil.rmtree(tmp)
            try:
                X.export_touchup(model, new, d, tmp, {
                    "source": src, "view": view.key, "label": view.label, "strength": strength, "hint": hint,
                    "seconds": round(time.time() - started), **tinfo}, before, after, marks, view)
                data = json.loads((tmp / "info.json").read_text(encoding="utf-8"))
                st = dict(info.get("studio") or {})
                for k in ("batch", "variant", "try", "variants"):
                    st.pop(k, None)
                data["studio"] = {**st, "touchup_of": src}
                (tmp / "info.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
                vid = p2d_finish_dir(A, mid, tmp, vid)
            except ValueError as ex:
                p2d_partial_cleanup(tmp)
                raise Plain(f"The touched-up texture didn't come out right, so it wasn't kept: {ex}")
            except BaseException:
                p2d_partial_cleanup(tmp)
                raise
            w.band = (0.88, 1.0)
            res = p2d_check(w, A, mid, vid)
        except ValueError as ex:  # touch_up: nothing was marked, a look that only enlarges
            raise Plain(f"Nothing was touched up: {ex}.") from ex
        finally:
            w.band = (0.0, 1.0)
            mask_path.unlink(missing_ok=True)
        if was_current:
            save_model_meta(kind, mid, current2d=vid)
        job["version"] = vid
        n = tinfo["areas"]
        inst = (A.installed().get(mid) or {}).get("version")
        job["note"] = (f"touched up {src} ({n} area{'s' if n != 1 else ''} in {view.label}): {vid}, "
                       f"{p2d_check_words(res)}" + (f". The game still has {inst}: install {vid} to see it there"
                                                    if inst else ""))
        w._stage("done", 1.0)

    # ---------------------------------------------------------------- routes

    bp = Blueprint("ff7_models", __name__, url_prefix="/api/modelpaint")

    def safe(fn):
        """Errors come back as {"message"} for the page (the Studio's api() shows it), not an HTML page."""
        @wraps(fn)
        def inner(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as ex:
                if hasattr(ex, "code") and hasattr(ex, "get_response"):  # abort(): as it is
                    raise
                traceback.print_exc()
                return jsonify(message=f"{type(ex).__name__}: {ex}"), 500
        return inner

    @bp.get("/ui.js")
    def ui():
        return send_file(SCRIPT, mimetype="text/javascript", max_age=0)

    @bp.get("/list/<kind>")
    @safe
    def api_list(kind):
        if kind not in KINDS:
            abort(404)
        if not ready():
            return jsonify(loading=True, error=cat["error"], items=[])
        return jsonify(loading=False, items=list_view(kind))

    @bp.get("/item/<kind>/<mid>")
    @safe
    def api_item(kind, mid):
        return jsonify(item_view(kind, entry(kind, mid)))

    @bp.get("/thumb/<kind>/<mid>.jpg")
    @safe
    def api_thumb(kind, mid):
        e = entry(kind, mid)
        mid = key_of(kind, e)
        if request.args.get("v") == "new":
            cur = current(kind, mid)
            p = model_dir(kind, mid) / cur / "thumb.jpg" if cur else None
            if p and p.exists():
                return send_file(p, mimetype="image/jpeg", max_age=3600)
        return send_file(original_thumb(kind, mid), mimetype="image/jpeg", max_age=3600)

    @bp.get("/vthumb/<kind>/<mid>/<vid>.jpg")
    @safe
    def api_vthumb(kind, mid, vid):
        e = entry(kind, mid)
        p = model_dir(kind, key_of(kind, e)) / _safe_name(vid) / "thumb.jpg"
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="image/jpeg", max_age=3600)

    @bp.get("/sheet/<kind>/<mid>/<vid>.jpg")
    @safe
    def api_sheet(kind, mid, vid):
        e = entry(kind, mid)
        p = model_dir(kind, key_of(kind, e)) / _safe_name(vid) / "views.jpg"
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="image/jpeg", max_age=0)

    @bp.get("/view/<kind>/<mid>.jpg")
    @safe
    def api_view(kind, mid):
        """The model as the game shades it: ver "orig" or a version id; cam (battle scenes), yaw / pitch (degrees
        from the starting view), zoom, w, h."""
        e = entry(kind, mid)
        mid = key_of(kind, e)
        a = request.args
        w = min(max(int(float(a.get("w", 640))), 64), MAX_VIEW)
        h = min(max(int(float(a.get("h", 480))), 48), MAX_VIEW)
        d = loaded(kind, mid)
        cam = view_camera(kind, d, int(a.get("cam", 0)), float(a.get("yaw", 0)), float(a.get("pitch", 0)),
                          min(max(float(a.get("zoom", 1)), 0.1), 20), w, h)
        cam = pan_camera(cam, float(a.get("panx", 0) or 0), float(a.get("pany", 0) or 0))
        ver = a.get("ver", "orig")
        pose = int(a.get("pose", 0) or 0) if kind == "battle" else 0
        t0 = time.time()
        if ver.startswith("2d:"):  # a 2D version (Paint in 2D style; a field model's fieldatlas build), as the game
            got = p2d_viewer_set(kind, mid, ver[3:], pose,  # draws it: flat light, half-alpha texels cut out
                                 rounded=kind == "field" and a.get("round", "1") == "1")
            if got is None:
                abort(404)
            r = render.rasterize(got["mesh"], cam)
            img = paint.shade(got["mesh"], r, got["images"], background=BG_MODEL)
        elif ver == "orig" and pose:
            img = draw(kind, posed_original(mid, pose), cam)
        elif ver == "orig":
            img = draw(kind, d["mesh"], cam)
        elif ver == SYW:
            ps = syw_set(kind, mid)
            if ps is None:
                abort(404)
            img = draw(kind, ps["mesh"], cam, ps["images"])
        else:
            ps = painted_set(kind, mid, ver)
            if ps is None:
                abort(404)
            img = draw(kind, ps["mesh"], cam, ps["images"])
        resp = send_file(jpeg(img, 82 if w * h < 300000 else 88), mimetype="image/jpeg", max_age=0)
        resp.headers["X-Render-Ms"] = str(round((time.time() - t0) * 1000))
        return resp

    @bp.post("/paint")
    @safe
    def api_paint():
        body = request.get_json(force=True)
        kind, mode = body.get("kind"), body.get("mode")
        if mode not in ("preview", "final"):
            abort(400)
        e = entry(kind, body.get("id"))
        mid = key_of(kind, e)
        ok, why = paintable(kind, e)
        if not ok:
            return jsonify(message=why), 409
        lid, _, s = look_info(model_meta(kind, mid).get("look"))
        job = studio.make_job(job_name(kind, mid), JOB_FINAL if mode == "final" else JOB_PREVIEW,
                              family=s.get("family"), settings={"look": lid, "paint": paint_settings(kind, mid)})
        return jsonify(added=worker.enqueue([job]))

    @bp.post("/look")
    @safe
    def api_look():
        body = request.get_json(force=True)
        e = entry(body.get("kind"), body.get("id"))
        look = body.get("look") or None
        if look is not None and look not in project.looks.data["looks"]:
            abort(400)
        save_model_meta(body["kind"], key_of(body["kind"], e), look=look)
        return jsonify(item_view(body["kind"], e))

    @bp.post("/settings")
    @safe
    def api_settings():
        """Change a model's paint settings (any of PAINT_DEFAULTS' keys; null for the default), or reset: all back
        to the defaults."""
        body = request.get_json(force=True)
        kind = body.get("kind")
        e = entry(kind, body.get("id"))
        mid = key_of(kind, e)
        if body.get("reset"):
            ps = dict(PAINT_DEFAULTS)
        else:
            ps = paint_settings(kind, mid)
            ps.update({k: body[k] for k in PAINT_DEFAULTS if k in body})
            ps = clean_paint(kind, ps)
        save_model_meta(kind, mid, paint=ps)
        return jsonify(item_view(kind, e))

    @bp.post("/use")
    @safe
    def api_use():
        body = request.get_json(force=True)
        kind = body.get("kind")
        e = entry(kind, body.get("id"))
        mid = key_of(kind, e)
        if not version_meta(kind, mid, body.get("version", "")):
            abort(404)
        save_model_meta(kind, mid, current=body["version"])
        return jsonify(item_view(kind, e))

    @bp.post("/delete")
    @safe
    def api_delete():
        body = request.get_json(force=True)
        kind = body.get("kind")
        e = entry(kind, body.get("id"))
        mid, vid = key_of(kind, e), body.get("version", "")
        if not version_meta(kind, mid, vid):
            abort(404)
        inst = installed_all().get(f"{kind}/{mid}")
        if inst and inst.get("version") == vid:
            return jsonify(message="This version is in the game: uninstall it first."), 409
        shutil.rmtree(model_dir(kind, mid) / _safe_name(vid))
        with lock:
            painted.pop((kind, mid, vid), None)
        if model_meta(kind, mid).get("current") == vid:
            left = version_ids(kind, mid)
            finals = [v for v in left if (version_meta(kind, mid, v) or {}).get("mode") == "final"]
            save_model_meta(kind, mid, current=(finals or left or [None])[-1])
        return jsonify(item_view(kind, e))

    @bp.post("/install")
    @safe
    def api_install():
        body = request.get_json(force=True)
        kind = body.get("kind")
        e = entry(kind, body.get("id"))
        mid = key_of(kind, e)
        if kind == "field":
            return jsonify(message="Field models can't be installed yet."), 409
        vid = current(kind, mid)
        if not vid:
            return jsonify(message="Paint a version first."), 409
        got = install(kind, mid, vid)
        return jsonify(item=item_view(kind, e), copied=len(got["files"]), dest=got["dest"],
                       replaced=sum(1 for f in got["files"] if f.get("backup")))

    @bp.post("/uninstall")
    @safe
    def api_uninstall():
        body = request.get_json(force=True)
        kind = body.get("kind")
        e = entry(kind, body.get("id"))
        mid = key_of(kind, e)
        with lock:
            data = installed_all()
            got = data.pop(f"{kind}/{mid}", None)
            removed = uninstall_entry(got) if got else 0
            save_installed(data)
        return jsonify(item=item_view(kind, e), removed=removed,
                       restored=sum(1 for f in (got or {}).get("files", []) if f.get("backup")))

    @bp.get("/game")
    @safe
    def api_game():
        return jsonify(game_live())

    @bp.post("/battle")
    @safe
    def api_battle():
        """Start one of a battle scene's battles in the running game: only ever from the user's click."""
        body = request.get_json(force=True)
        e = entry("scene", body.get("id"))
        fid = body.get("formation")
        if not isinstance(fid, int) or fid not in {f["id"] for f in scene_formations(e)}:
            return jsonify(message="Pick one of the battles fought in this scene."), 400
        g = game_live()
        if not g["running"]:
            return jsonify(message="Your game isn't running with FFNx-3d (its status hasn't changed for a while), so "
                                   "nothing was sent."), 409
        if g["mode"] == 1:
            return jsonify(message="Your game is in a battle already: finish it or run away first."), 409
        return jsonify(start_battle(fid, bool(body.get("god", True)), bool(body.get("autoattack", False))))

    def look_groups(body):
        """The page's ticked parts (LOOK_GROUPS' keys), in order; 400 when it sent something else."""
        groups = body.get("parts")
        if not isinstance(groups, list) or any(g not in LOOK_GROUPS for g in groups):
            abort(400)
        return [g for g in LOOK_GROUPS if g in groups]

    @bp.get("/battlelook/<mid>")
    @safe
    def api_battlelook(mid):
        return jsonify(look_view(entry("scene", mid)))

    @bp.get("/battlelook/<mid>/<vid>/<name>")
    @safe
    def api_battlelook_picture(mid, vid, name):
        e = entry("scene", mid)
        if name not in LOOK_PREVIEWS:
            abort(404)
        p = bl().WORK / key_of("scene", e) / _safe_name(vid) / name
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="image/png" if name.endswith(".png") else "image/jpeg", max_age=0)

    @bp.post("/battlelook/build")
    @safe
    def api_battlelook_build():
        """Queue a battle look build of the ★ version with the ticked parts. With the AI (the default) and life or
        relief ticked while the game runs, nothing is queued: {"game_running": true}, for the page to ask whether to
        build without the AI."""
        body = request.get_json(force=True)
        e = entry("scene", body.get("id"))
        mid = key_of("scene", e)
        ok, why = paintable("scene", e)
        if not ok:
            return jsonify(message=why), 409
        groups = look_groups(body)
        if not groups:
            return jsonify(message="Tick at least one part to build."), 400
        vid = current("scene", mid)
        if not vid:
            return jsonify(message="Paint a version first: the battle look is worked out from the painting."), 409
        B = bl()
        ai = body.get("ai", True) is not False
        ai_groups = [g for g in groups if g in LOOK_AI]
        if ai and ai_groups and B.game_running():
            return jsonify(added=0, game_running=True, ai_parts=ai_groups)
        parts = [p for g in groups for p in LOOK_GROUPS[g] if ai or p != "normals"]
        if not parts:
            return jsonify(message="Surface relief can only be worked out by the AI: close the game and build again."), 409
        ai = ai and bool(ai_groups)
        job = studio.make_job(job_name("scene", mid), JOB_LOOK, version=vid, settings={"parts": parts, "ai": ai})
        job["est"] = look_estimate(look_seconds(B, mid, vid), parts, ai)
        return jsonify(added=worker.enqueue([job]), est=job["est"], ai=ai)

    @bp.post("/battlelook/install")
    @safe
    def api_battlelook_install():
        """Put the ★ version's built look into the game: the ticked parts that are built (only ever from the user's
        click). battlelight.install refuses, writing nothing, where a file or section isn't its own."""
        body = request.get_json(force=True)
        e = entry("scene", body.get("id"))
        mid = key_of("scene", e)
        groups = look_groups(body)
        if not groups:
            return jsonify(message="Tick at least one part to install."), 400
        vid = current("scene", mid)
        B = bl()
        st = B.built(mid, vid) if vid else None
        if not st:
            return jsonify(message="The battle look isn't built for the ★ painted version yet: Build first."), 409
        if look_job_running(mid):
            return jsonify(message="The battle look is being built right now: install it when that's done."), 409
        have = look_built_groups(st)
        use = [g for g in groups if have[g]]
        if not use:
            return jsonify(message="None of the ticked parts is built yet: Build them first."), 409
        parts = [p for g in use for p in LOOK_GROUPS[g] if p in (st.get("parts") or [])]
        # an install replaces the scene's earlier one: battlelight takes that out before it looks for clashes
        before = (" The battle look that was in the game before was taken out first, so battles here have the game's "
                  "usual light now." if mid in B.installed() else "")
        try:
            got = B.install(mid, vid, parts=parts)
        except FileExistsError as ex:
            # "not ours, left alone: <path>, [section], ...": paths from the game folder, sections by their file
            game = str(B.GAME).rstrip("\\/") + os.sep
            what = "; ".join(f"the {x} settings in lighting\\config.toml" if x.startswith("[") else
                             x[len(game):] if x.lower().startswith(game.lower()) else x
                             for x in (s.strip() for s in str(ex).split(":", 1)[-1].split(", ")) if x)
            return jsonify(message="Nothing was installed. Some of the files or settings it would write in your game "
                                   "aren't the Studio's (another mod's, or put there by hand), so they were left alone: "
                                   f"{what} (in your game's folder). Move or remove them yourself if you want this look "
                                   "in." + before), 409
        except FileNotFoundError as ex:
            return jsonify(message="Nothing was installed: something it needs in your game is missing "
                                   f"({ex.filename or ex}). Is FFNx-3d set up there?" + before), 409
        return jsonify(look=look_view(e), installed=use, not_built=[g for g in groups if not have[g]],
                       files=len(got["files"]), normals_skipped=len(got.get("normals_skipped") or []))

    @bp.post("/battlelook/uninstall")
    @safe
    def api_battlelook_uninstall():
        """Take the scene's battle look out of the game: exactly what its install put in (the user's click)."""
        body = request.get_json(force=True)
        e = entry("scene", body.get("id"))
        mid = key_of("scene", e)
        B = bl()
        if mid not in B.installed():
            return jsonify(message="This scene's battle look isn't in the game."), 409
        removed, left, sections = B.uninstall(mid)
        game = str(B.GAME).rstrip("\\/") + os.sep  # the files left: from the game's folder
        left = [p[len(game):] if p.lower().startswith(game.lower()) else p for p in left]
        return jsonify(look=look_view(e), removed=removed, left=left, sections=len(sections))

    @bp.get("/paint2d/<mid>")
    @safe
    def api_p2d(mid):
        return jsonify(p2d_view(entry("battle", mid)))

    @bp.get("/paint2d/game")
    @safe
    def api_p2d_game():
        """Is the game running (for the card's warning: painting waits until it's closed)."""
        return jsonify(running=p2d_game_running())

    @bp.get("/paint2d/<mid>/<vid>/<name>")
    @safe
    def api_p2d_picture(mid, vid, name):
        e = entry("battle", mid)
        if (name not in P2D_PICTURES and not P2D_VIEW.fullmatch(name)) or vid.startswith(".") or vid.endswith(".partial"):
            abort(404)
        p = batlas().WORK / key_of("battle", e) / _safe_name(vid) / "preview" / name
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="image/png" if name.endswith(".png") else "image/jpeg", max_age=0)

    @bp.post("/paint2d/settings")
    @safe
    def api_p2d_settings():
        """Change a battle model's 2D paint settings (any of P2D_DEFAULTS' keys; null for battleatlas' own), or reset:
        all back to the defaults."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        looks_ = project.looks.data["looks"]
        if body.get("reset"):
            ps = dict(P2D_DEFAULTS)
        else:
            if body.get("look") not in (None, "") and body["look"] not in looks_:
                abort(400)
            ps = p2d_settings(mid)
            ps.update({k: body[k] for k in P2D_DEFAULTS if k in body})
            ps = clean_p2d(ps, looks_)
            A = batlas()  # the defaults stored as None: they follow battleatlas' own
            if ps["look"] == p2d_default_look(A):
                ps["look"] = None
            if ps["size"] == int(A.ATLAS_SIZE):
                ps["size"] = None
            if ps["strength"] is not None and abs(ps["strength"] - float(A.STRENGTH)) < 1e-6:
                ps["strength"] = None
        save_model_meta("battle", mid, paint2d=ps)
        return jsonify(p2d_view(e))

    @bp.post("/paint2d/paint")
    @safe
    def api_p2d_paint():
        """Queue a 2D paint of a battle model with its settings as they are. While the game runs nothing is queued:
        the AI waits until it's closed, and the page says so."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        if not battle.game_name(mid):
            return jsonify(message=P2D_NO_NAME), 409
        if p2d_game_running(fresh=True):
            return jsonify(message=P2D_GAME_RUNNING, game_running=True), 409
        A = batlas()
        ps = p2d_settings(mid)
        eff = p2d_effective(A, ps)
        job = studio.make_job(job_name("battle", mid), JOB_2D, family=project.looks.look_settings(eff["look"]).get("family"),
                              settings=ps)
        job["est"] = p2d_estimate(A, mid, eff["size"], eff["variants"])
        return jsonify(added=worker.enqueue([job]), est=job["est"], variants=eff["variants"])

    def p2d_version_or_404(A, mid, vid):
        if vid.startswith(".") or vid.endswith(".partial") or vid not in {p.name for p in p2d_dirs(A, mid)}:
            abort(404)
        return A.WORK / mid / vid

    @bp.post("/paint2d/check")
    @safe
    def api_p2d_check():
        """Queue the AI check of a 2D version ({"id", "version"}): a version painted before the check existed, or
        checked again. While the game runs nothing is queued (the AI needs the graphics card)."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        A = batlas()
        vid = str(body.get("version") or "")
        p2d_version_or_404(A, mid, vid)
        if p2d_game_running(fresh=True):
            return jsonify(message=P2D_GAME_RUNNING_CHECK, game_running=True), 409
        job = studio.make_job(job_name("battle", mid), JOB_2D_CHECK, version=vid)
        job["est"] = round(p2d_secs.get("check") or P2D_CHECK_SECONDS)
        return jsonify(added=worker.enqueue([job]), est=job["est"])

    @bp.get("/paint2d/<mid>/<vid>/views")
    @safe
    def api_p2d_views(mid, vid):
        """A 2D version's views for the touch-up ({key, label, width, height}), whether it's the one in the game, and
        what a touch-up of it starts from (its look and strength)."""
        e = entry("battle", mid)
        mid = key_of("battle", e)
        A = batlas()
        d = p2d_version_or_404(A, mid, vid)
        views = p2d_touch_views(A, mid, vid)
        try:
            pt = json.loads((d / "info.json").read_text(encoding="utf-8")).get("painted") or {}
        except (OSError, ValueError):
            pt = {}
        inst = (A.installed().get(mid) or {}).get("version")
        return jsonify(views=[{"key": v.key, "label": v.label, "width": v.camera.width, "height": v.camera.height}
                              for v in views],
                       installed=inst, painted=bool(pt), look=pt.get("look"),
                       strength=float(pt.get("strength") or A.STRENGTH), estimate=round(
                           P2D_TOUCH_SECONDS + (p2d_secs.get("check") or P2D_CHECK_SECONDS)))

    @bp.get("/viewer2d/<kind>/<mid>")
    @safe
    def api_viewer2d(kind, mid):
        """The 2D versions the viewer can show on the model (battle models: Paint in 2D style's; field models:
        fieldatlas' builds) and, for battle models, the poses it can show them in."""
        e = entry(kind, mid)
        mid = key_of(kind, e)
        if kind not in ("battle", "field"):
            return jsonify(versions=[], poses=[])
        return jsonify(versions=p2d_viewer_versions(kind, mid),
                       poses=[{"animation": k, "label": label} for k, label in p2d_poses(mid)] if kind == "battle" else [])

    @bp.post("/paint2d/<mid>/<vid>/views/custom")
    @safe
    def api_p2d_view_custom(mid, vid):
        """A touch-up view from the viewer's angle ({yaw, pitch, zoom, panx, pany, pose, aspect}): the viewer's
        camera (model space) moved to where the touch-up has the model, stored with the version's views
        (views.json, key "cam" + its hash) and drawn at most 1024 px. Returns {"key", "label"}."""
        import hashlib
        e = entry("battle", mid)
        mid = key_of("battle", e)
        A = batlas()
        d = p2d_version_or_404(A, mid, vid)
        body = request.get_json(force=True)
        try:
            yaw, pitch = float(body.get("yaw", 0)), float(body.get("pitch", 0))
            zoom = min(max(float(body.get("zoom", 1)), 0.1), 20)
            panx, pany = float(body.get("panx", 0) or 0), float(body.get("pany", 0) or 0)
            aspect = min(max(float(body.get("aspect", 1)), 0.25), 4.0)
            pose = int(body.get("pose", 0) or 0)
        except (TypeError, ValueError):
            abort(400)
        if pose and pose not in {k for k, _ in p2d_poses(mid)}:
            abort(400)
        w, h = (1024, max(64, round(1024 / aspect))) if aspect >= 1 else (max(64, round(1024 * aspect)), 1024)
        cam = pan_camera(view_camera("battle", loaded("battle", mid), 0, yaw, pitch, zoom, w, h), panx, pany)
        X = p2x()
        model, _img = p2d_model(A, mid, vid)
        views = X.views_of(model, d)
        spec = json.dumps([round(yaw, 1), round(pitch, 1), round(zoom, 3), round(panx, 3), round(pany, 3), pose, w, h])
        key = "cam" + hashlib.sha1(spec.encode()).hexdigest()[:8]
        label = "your view from the viewer" + (f", animation {pose}" if pose else "")
        if not any(v.key == key for v in views):  # (the version's own views, and the last few from the viewer)
            fixed = [v for v in views if not v.key.startswith("cam")]
            mine = [v for v in views if v.key.startswith("cam")][-5:]
            X.save_views(d, fixed + mine + [X.View(key, label, model.placed_camera(cam), "sky", pose)])
        return jsonify(key=key, label=label)

    p2d_render_lock = threading.Lock()

    @bp.get("/paint2d/<mid>/<vid>/view/<key>.jpg")
    @safe
    def api_p2d_view_picture(mid, vid, key):
        """One of a version's touch-up views drawn from its painted texture (what the touch-up repaints), kept in the
        version's touchup_views/ once drawn."""
        e = entry("battle", mid)
        mid = key_of("battle", e)
        A = batlas()
        d = p2d_version_or_404(A, mid, vid)
        if not P2D_VIEW_KEY.fullmatch(key):
            abort(404)
        p = d / "touchup_views" / f"{key}.jpg"
        if not p.exists():
            with p2d_render_lock:  # one at a time: the page asks for all of them at once
                if not p.exists():
                    view = next((v for v in p2d_touch_views(A, mid, vid) if v.key == key), None)
                    if view is None:
                        abort(404)
                    model, img = p2d_model(A, mid, vid)
                    rgb, _, _ = model.draw(img, view.camera, view.background, view.animation)
                    p.parent.mkdir(exist_ok=True)
                    tmp = p.with_suffix(".tmp")
                    Image.fromarray(rgb).save(tmp, "JPEG", quality=92)
                    tmp.replace(p)
        return send_file(p, mimetype="image/jpeg", max_age=0)

    @bp.post("/paint2d/touchup")
    @safe
    def api_p2d_touchup():
        """Repaint the areas the user marked on one of a 2D version's views: {"id", "version", "view", "mask": a PNG
        data URL at the view's size (its alpha: the marks), "hint": what should be there (optional), "strength": how
        much to change, P2D_TOUCH_STRENGTH}. A queue job (JOB_2D_TOUCH) that makes a new version."""
        import base64
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        A = batlas()
        vid = str(body.get("version") or "")
        p2d_version_or_404(A, mid, vid)
        key = str(body.get("view") or "")
        if not P2D_VIEW_KEY.fullmatch(key):
            abort(400)
        data = str(body.get("mask") or "")
        if not data.startswith("data:image/png;base64,"):
            return jsonify(message="Nothing was marked: brush over what should be repainted first."), 400
        img = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA")
        if not img.getchannel("A").getbbox():
            return jsonify(message="Nothing was marked: brush over what should be repainted first."), 400
        if p2d_game_running(fresh=True):
            return jsonify(message=P2D_GAME_RUNNING_TOUCH, game_running=True), 409
        try:
            strength = float(body.get("strength"))
        except (TypeError, ValueError):
            strength = None
        if strength is not None:
            strength = round(min(max(strength, P2D_TOUCH_STRENGTH[0]), P2D_TOUCH_STRENGTH[1]) * 20) / 20
        path = Path(studio.AI) / "input" / f"p2d_{_safe_name(mid)}_touchup_marks_{int(time.time() * 1000)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        job = studio.make_job(job_name("battle", mid), JOB_2D_TOUCH, version=vid,
                              settings={"view": key, "mask": path.name})
        job.update(view=key, mask=str(path), hint=" ".join(str(body.get("hint") or "").split())[:200],
                   strength=strength)
        job["est"] = round(P2D_TOUCH_SECONDS + (p2d_secs.get("check") or P2D_CHECK_SECONDS))
        added = worker.enqueue([job])
        if not added:
            path.unlink(missing_ok=True)
        return jsonify(added=added, est=job["est"])

    @bp.post("/paint2d/use")
    @safe
    def api_p2d_use():
        """Mark a 2D version ★: the one Install puts in the game (the game itself isn't touched)."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        if body.get("version") not in {v["id"] for v in p2d_versions(batlas(), mid)}:
            abort(404)
        save_model_meta("battle", mid, current2d=body["version"])
        return jsonify(p2d_view(e))

    @bp.post("/paint2d/install")
    @safe
    def api_p2d_install():
        """Put the ★ 2D version into the game (battleatlas.install; only ever from the user's click). It replaces the
        model's earlier one, and refuses, writing nothing, where a file it would write isn't its own."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        A = batlas()
        vid = p2d_current(A, mid, p2d_versions(A, mid))
        if not vid:
            return jsonify(message="Nothing to install yet: paint it first."), 409
        before = (" The 2D painting that was in the game before was taken out first, so battles show the model as the "
                  "game draws it now." if mid in A.installed() else "")
        try:
            got = A.install(mid, vid)
        except FileExistsError as ex:
            # "not ours, left alone: <path>, <path>": files in the game's folder
            what = "; ".join(p2d_rel(x.strip()) for x in str(ex).split(":", 1)[-1].split(", ") if x.strip())
            return jsonify(message="Nothing was installed. Some of the files it would write in your game aren't the "
                                   "Studio's (another mod's, or put there by hand), so they were left alone: "
                                   f"{what} (in your game's folder). Move or remove them yourself if you want this "
                                   "painting in." + before), 409
        except FileNotFoundError as ex:
            return jsonify(message=f"Nothing was installed: the files of {vid} are missing ({ex}). Paint it again, or "
                                   "pick another version." + before), 409
        return jsonify(p2d=p2d_view(e), version=got.get("version"), files=len(got.get("files", [])))

    @bp.post("/paint2d/uninstall")
    @safe
    def api_p2d_uninstall():
        """Take the model's 2D painting out of the game: exactly the files its install wrote (the user's click)."""
        body = request.get_json(force=True)
        e = entry("battle", body.get("id"))
        mid = key_of("battle", e)
        A = batlas()
        if mid not in A.installed():
            return jsonify(message="This model's 2D painting isn't in the game."), 409
        removed, left = A.uninstall(mid)
        return jsonify(p2d=p2d_view(e), removed=removed, left=[p2d_rel(p) for p in left])

    # everything is defined: hook it into the Studio (nothing above changed it yet)
    studio.app.register_blueprint(bp)
    studio.JOB_KINDS[JOB_FINAL] = {"run": run_paint, "est": est_for("final")}
    studio.JOB_KINDS[JOB_PREVIEW] = {"run": run_paint, "est": est_for("preview")}
    studio.JOB_KINDS[JOB_LOOK] = {"run": run_look, "est": lambda name: 90}
    studio.JOB_KINDS[JOB_2D] = {"run": run_p2d, "est": p2d_est}
    studio.JOB_KINDS[JOB_2D_CHECK] = {"run": run_p2d_check, "est": lambda name: round(P2D_CHECK_SECONDS)}
    studio.JOB_KINDS[JOB_2D_TOUCH] = {"run": run_p2d_touchup,
                                      "est": lambda name: round(P2D_TOUCH_SECONDS + P2D_CHECK_SECONDS)}
    threading.Thread(target=load_catalogues, daemon=True).start()  # the lists are ready when the page asks
    return "/api/modelpaint/ui.js"
