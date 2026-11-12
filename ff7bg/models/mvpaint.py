"""Tencent's Hunyuan3D-2.1 Paint as a painter for the battle and field atlases (a trial, 2026-11-07).

Why: the 2D paints (battleatlas.paint, fieldatlas.paint) restyle each view on its own with the Studio's looks and
project them one after another. Where views meet the paint is patchy, and each view can invent its own things (a
second pauldron on Cloud's bare right shoulder, a tongue on Zolom's face). Hunyuan3D-Paint generates all the views of
one mesh together, conditioned on its geometry (normal and position maps per view) and one reference picture, so
the views should agree with each other.

How it fits in (only Hunyuan's multi-view diffusion step is used; everything else is ours):
- conditioning(): the model's normal and position maps from Hunyuan's cameras, rendered by render.py in the exact
  conventions of hy3dpaint (textureGenPipeline + DifferentiableRenderer.MeshRender):
  - views: (elevation, azimuth, bake weight) = (0, 0, 1) front, (0, 90, 0.1), (0, 180, 0.5) back, (0, 270, 0.1),
    (90, 0, 0.05) top, (-90, 180, 0.05) bottom; up to 3 more from a ring at +-20 degrees by coverage (select_views,
    their bake_view_selection);
  - camera: orthographic, 1.2 wide in a frame where the mesh is centred on its bounding box and scaled so its
    furthest vertex is 0.575 from the centre (MeshRender.set_mesh's scale_factor 1.15); here a pinhole FAR radii away
    with that scale at the centre (near enough orthographic; the conditioning and the projection share it);
  - normal map: each face's normal (flat shading, MeshRender's shader_type "face") in their world frame, (n + 1) / 2,
    white behind. Their set_mesh mirrors the mesh (x, y negated, then y and z swapped), so the face normals it takes
    from the winding come out as the outward normal in Blender's axes; for an FF7 model (y down, facing -z) that is
    (n.x, n.z, -n.y) of the outward normal, i.e. the front of a model is (0.5, 0, 0.5). Normals are turned to face the
    camera (FF7's winding isn't consistent);
  - position map: 0.5 + (p - centre) / (2 x radius) in the same axes ((x, z, -y)), white behind;
  - pixels: MeshRender's rasterizer puts NDC -1..1 on pixel centres 0..size-1 and its rows run along the camera's
    up vector, which for its z-down frame is the world's down: the picture is upright. Rendered at 2048 as they render
    (render_size), then resized to the diffusion's 512 by the script, as multiview_utils does.
- mvpaint_texgen.py (run by run_texgen() with tools/texgen/venv's Python, a separate process): loads the paint
  pipeline (fp16, UniPC 15 steps, guidance 3) and DINOv2-giant, takes the reference and the maps, writes the
  generated albedo (and metallic-roughness) views.
- bake(): each view enlarged (Hunyuan uses RealESRGAN x4 here; not installed: the Studio's own upscaler through
  ComfyUI, or Lanczos) and projected onto the atlas with battleatlas' projection (paint.Painter.add: facing and texel
  density to the 4th power, as their cos^4 bake, times the view's weight and an edge weight that drops the outline
  and occlusion edges, as their boundary-unreliable mask), then compose() (each chart's unpainted texels from its own
  paint). Their UV inpainting (mesh_inpaint_processor, a C++ extension) isn't used.
- finish: the recipe's colour_lock, the decals and twins for field models, export(), previews, check(), and the AI
  check (paint2d_extras.check), so the Studio's viewer and the engine take it like any 2D version.

The reference picture decides the style: reference(): the model's front from Hunyuan's front camera, either as the
game draws it, as an existing version paints it, or restyled in the 2D style by the Restyler (battleatlas' look and
words), cut out onto white (their pipeline composites the reference onto white).

Licence: Tencent Hunyuan 3D 2.1 Community License (tools/texgen/Hunyuan3D-2.1-main/LICENSE): not licensed in the EU,
the UK or South Korea; its outputs may not be used to improve other AI models. The model files, Tencent's code and
anything painted with it stay out of the public repo (tools/ and work/ are gitignored).

  python -m ff7bg.models.mvpaint conditioning <battle|field> <id> [--out DIR] [--views N]      the maps only (no AI)
  python -m ff7bg.models.mvpaint dryrun <battle|field> <id> [--out DIR]   the original's views baked back (no AI)
"""
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import battleatlas as BA
from . import paint as paintmod
from . import render

REPO = Path(__file__).resolve().parents[2]
TEXGEN = REPO / "tools" / "texgen"
TEXGEN_PY = TEXGEN / "venv" / "Scripts" / "python.exe"
TEXGEN_SCRIPT = Path(__file__).with_name("mvpaint_texgen.py")
HY_CODE = TEXGEN / "Hunyuan3D-2.1-main" / "hy3dpaint"
HF_HOME = TEXGEN / "models" / "hf"
JOBS = REPO / "work" / "models" / "mvpaint"

METHOD = "hunyuan3d-paint-2.1"
# hy3dpaint's view candidates (textureGenPipeline.Hunyuan3DPaintConfig): (elevation, azimuth, bake weight); the first
# six are always taken, then the ring by coverage up to max_num_view (6-9)
BASE_VIEWS = [(0, 0, 1.0), (0, 90, 0.1), (0, 180, 0.5), (0, 270, 0.1), (90, 0, 0.05), (-90, 180, 0.05)]
RING_VIEWS = [v for a in range(0, 360, 30) for v in ((20, a, 0.01), (-20, a, 0.01))]
VIEW_LABELS = {(0, 0): "front", (0, 90): "azimuth 90", (0, 180): "back", (0, 270): "azimuth 270", (90, 0): "top",
               (-90, 180): "bottom"}
CAM_DIST = 1.45          # MeshRender camera_distance
ORTHO = 1.2              # MeshRender.set_orth_scale(1.2): the view is 1.2 wide in the normalised frame
SCALE_FACTOR = 1.15      # MeshRender.set_mesh: the mesh scaled so twice its furthest vertex's distance is this
RENDER_SIZE = 2048       # Hunyuan3DPaintConfig.render_size: the conditioning maps' size before the pipeline's resize
RESOLUTION = 512         # the diffusion's view size ("can be 768 or 512")
FAR = 1000.0             # our near-orthographic pinhole: this many radii from the centre
SELECT_SIZE = 1024       # bake_view_selection renders at 1024
MIN_GAIN = 0.01          # ...and takes a ring view while it adds more than this share of the surface
PROJECT_SIZE = 1024      # bake(): the views are projected at this size (paint.Painter.add oversamples it 2x)
EDGE_512 = 2.0           # pixels at 512 next to the outline and occlusion edges that count for less (their
                         # bake_unreliable_kernel_size: 2 px per 512)
JUMP = 0.02              # edge_weight(): an occlusion edge, in the model's radius
# the texgen process' allocator: at 768 the default one reserved 40 GB for 20.6 GB of tensors, past the 5090's 32 GB
# (Windows then spills into shared memory: 51-53 s a model instead of ~20)
ALLOC_CONF = "expandable_segments:True"
METAL_DARK = 0.5         # make_from_job(metal=): Hunyuan paints metal as PBR albedo (bright: a metal's darkness comes
                         # from its reflections), so steel came out near white under the game's flat light; a view's
                         # pixels are darkened by this share of its metallic map (mr_NN.png's red channel). Off by
                         # default: on battle Cloud the map marked his bare arms metal (brown after the lock); grey_lock
                         # (GREY_LOCK) darkens by the game's own grey parts instead
GREY_LOCK = 0.6          # make_from_job(grey=): grey_lock()'s pull toward the game's light and dark on its grey parts
GREY_CHROMA = 40         # ...where the game's colour's max - min channel is under this (of 255)
FACING = (0.26, 0.5)     # |cos| below 0.26 (75 degrees, their bake_angle_thres) takes nothing, full weight from 0.5
WHITE = (255, 255, 255)


# --- the model -------------------------------------------------------------------------------------------------------

@dataclass
class Subject:
    """A battle or field model as mvpaint paints it, in model space (y down, facing -z)."""
    kind: str          # "battle" | "field"
    obj: object        # battleatlas.BattleAtlas | fieldatlas.FieldAtlas
    painted: object    # the atlas-only mesh as the views see it (field: the shadowed twins left out)
    original: object   # the model as the game draws it
    kept: np.ndarray   # triangles never painted (fieldatlas' decals)
    yaw: float = 0.0   # turned this much for Hunyuan's cameras (YAWS)

    @property
    def id(self):
        return self.obj.id

    @property
    def atlas(self):
        return self.obj.atlas

    def shade_original(self, r):
        if self.kind == "field":
            from . import fieldatlas as F
            return F.original_shade(self.original, r)
        return render.shade(self.original, r, modulate=True)

    def work(self):
        from . import fieldatlas as F
        return (F.WORK if self.kind == "field" else BA.WORK) / self.id


# Which way a model is turned for Hunyuan's cameras (battleatlas.placed's yaw, degrees): its front view (weight 1, and
# the reference picture) should see what matters. Battle Cloud stands side-on in his battle stance (his chest toward
# +x, his face toward the enemies at -z): at 45 the front view sees his face, chest and both shoulders, 3/4 on.
YAWS = {("battle", "rt"): 45.0}


def battle_subject(id, size=None, yaw=None):
    ba = BA.build(id, size)
    yaw = YAWS.get(("battle", ba.id), 0.0) if yaw is None else yaw
    hide = sorted({b for part in BA.model_views(ba.id)["moving"] for b in part["bones"]})
    turn = (lambda m: BA.placed(m, (0.0, 0.0, 0.0), yaw)) if yaw else (lambda m: m)
    return Subject("battle", ba, turn(BA.hidden(BA.atlas_only(ba.posed, ba.atlas.index), hide)),
                   turn(BA.hidden(ba.original, hide)), np.zeros(0, np.int64), yaw)


def field_subject(hrc, yaw=None):
    from . import fieldatlas as F
    fa = F.build(hrc)
    yaw = YAWS.get(("field", fa.id), 0.0) if yaw is None else yaw
    turn = (lambda m: BA.placed(m, (0.0, 0.0, 0.0), yaw)) if yaw else (lambda m: m)
    return Subject("field", fa, turn(BA.atlas_only(fa.seen(fa.posed), fa.atlas.index)), turn(fa.seen(fa.original)),
                   np.nonzero(F.keep_mask(fa))[0], yaw)


def shown(sub, hide=None, mesh=None):
    """The painted (or another of the subject's) mesh with the parts on some bones left out (battleatlas.hidden: their
    triangles collapsed, never drawn): a job can paint part of a model (battle Cloud's body without his sword, framed
    bigger; then the sword on its own)."""
    m = sub.painted if mesh is None else mesh
    return BA.hidden(m, list(hide)) if hide else m


def subject(kind, id):
    return field_subject(id.upper()) if kind == "field" else battle_subject(id.lower())


# --- Hunyuan's cameras -----------------------------------------------------------------------------------------------

@dataclass
class Frame:
    centre: np.ndarray  # the bounding box's centre (model space)
    radius: float       # the furthest vertex from it


def frame(mesh):
    """MeshRender.set_mesh's normalisation: the bounding box centre and the furthest vertex (drawn triangles only:
    hidden ones are collapsed onto their centres, inside the box anyway)."""
    p = mesh.pos.astype(np.float64)
    area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    pts = p[area > 1e-9].reshape(-1, 3)
    c = (pts.min(0) + pts.max(0)) / 2
    return Frame(c, float(np.linalg.norm(pts - c, axis=1).max()))


def hy_basis(elev, azim, dist=CAM_DIST):
    """camera_utils.get_mv_matrix's camera in MeshRender's frame: (position, right, up, look). The picture's columns
    run along right, its rows along up (their rasterizer's rows follow NDC y)."""
    e, a = math.radians(-elev), math.radians(azim + 90)
    cp = np.array([dist * math.cos(e) * math.cos(a), dist * math.cos(e) * math.sin(a), dist * math.sin(e)])
    look = -cp / np.linalg.norm(cp)
    right = np.cross(look, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, look)
    up /= np.linalg.norm(up)
    return cp, right, up, look


def to_model(v):
    """A direction in MeshRender's frame -> FF7 model space. Theirs is (-x, z, -y) of the glTF-style input (y up,
    front +z), which is (x, -y, -z) of ours: theirs = (-x, -z, y) of ours."""
    v = np.asarray(v, np.float64)
    return np.array([-v[0], v[2], -v[1]])


def hy_camera(fr, elev, azim, size):
    """render.Camera for one of Hunyuan's views at size x size: the same picture as MeshRender's orthographic one."""
    cp, right, up, look = hy_basis(elev, azim)
    dist = FAR * fr.radius
    half = (ORTHO / 2) * 2 * fr.radius / SCALE_FACTOR  # the view's half width in model units
    down = to_model(up)
    cam = render.Camera(eye=fr.centre + to_model(cp) / np.linalg.norm(cp) * dist, target=fr.centre, up=-down,
                        width=size, height=size, near=dist - 2 * fr.radius, focal=(size - 1) / 2 * dist / half)
    rot = cam.rotation()
    assert np.allclose(rot[0], to_model(right), atol=1e-6) and np.allclose(rot[1], down, atol=1e-6), (elev, azim)
    return cam


def view_label(elev, azim):
    return VIEW_LABELS.get((elev, azim), f"elevation {elev}, azimuth {azim}")


# --- conditioning ----------------------------------------------------------------------------------------------------

def _hy_axes(v):
    """FF7 model-space vectors (..., 3) in the conditioning's axes: (x, z, -y) (Blender's: x, depth, up)."""
    return np.stack([v[..., 0], v[..., 2], -v[..., 1]], -1)


def _u8(x):
    return (np.clip(x, 0, 1) * 255).astype(np.uint8)  # (MeshRender: * 255, astype uint8: truncated)


def conditioning(mesh, cam, fr):
    """(normal map, position map, raster) of one view, MeshRender's render_normal(use_abs_coor=True) and
    render_position: RGB uint8, white where there's nothing."""
    r = render.rasterize(mesh, cam)
    hit = r.tri >= 0
    t = r.tri[hit]
    n = render.normals(mesh)[t].astype(np.float64)
    f = cam.rotation()[2]
    n *= np.where(n @ f > 0, -1.0, 1.0)[:, None]  # outward = toward the camera for what it sees
    H, W = hit.shape
    nm = np.ones((H, W, 3))
    nm[hit] = (_hy_axes(n) + 1) / 2
    p = np.einsum("nk,nkc->nc", r.bary[hit].astype(np.float64), mesh.pos[t].astype(np.float64))
    pm = np.ones((H, W, 3))
    pm[hit] = 0.5 + _hy_axes(p - fr.centre) / (2 * fr.radius)
    return _u8(nm), _u8(pm), r


def select_views(mesh, fr, max_views=6, candidates=None):
    """bake_view_selection: the six base views, then while under max_views the ring view that sees the most surface
    no view has seen yet, if that's more than MIN_GAIN of it. Returns [(elev, azim, weight)]."""
    cands = BASE_VIEWS + RING_VIEWS if candidates is None else candidates
    if max_views <= 6:
        return list(BASE_VIEWS)
    p = mesh.pos.astype(np.float64)
    area = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    share = area / max(area.sum(), 1e-12)
    seen = []
    for e, a, _ in cands:
        r = render.rasterize(mesh, hy_camera(fr, e, a, SELECT_SIZE))
        seen.append(set(np.unique(r.tri[r.tri >= 0]).tolist()))
    chosen = list(range(6))
    have = set().union(*(seen[i] for i in chosen))
    while len(chosen) < max_views:
        gains = [(share[list(s - have)].sum() if i not in chosen else -1, i) for i, s in enumerate(seen)]
        g, i = max(gains)
        if g <= MIN_GAIN:
            break
        chosen.append(i)
        have |= seen[i]
    return [cands[i] for i in chosen]


def coverage(mesh, fr, views):
    """The share of the model's surface (by area) the views see."""
    p = mesh.pos.astype(np.float64)
    area = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    have = set()
    for e, a, _ in views:
        r = render.rasterize(mesh, hy_camera(fr, e, a, SELECT_SIZE))
        have |= set(np.unique(r.tri[r.tri >= 0]).tolist())
    return float(area[list(have)].sum() / max(area.sum(), 1e-12))


# --- the reference picture ---------------------------------------------------------------------------------------------

def front_view(sub, image=None, size=1024, background=None, hide=None):
    """(RGB, mask) the model from Hunyuan's front camera (the framing of view 0) at size: as the game draws it (image
    None) or showing a painted atlas (flat, as FFNx-3d draws it with flat light). background: (RGB, depth) behind it,
    None white. hide: bones left out (shown())."""
    fr = frame(shown(sub, hide))
    cam = hy_camera(fr, 0, 0, size)
    m = shown(sub, hide, sub.original if image is None else sub.painted)
    r = render.rasterize(m, cam)
    fg = sub.shade_original(r) if image is None else paintmod.shade(m, r, [image])
    bg = background if background is not None else (np.full((size, size, 3), 255, np.uint8),
                                                    np.full((size, size), np.inf, np.float32))
    return BA.composite(fg, r, bg), r.tri >= 0


def cut_out(rgb, mask, grow=1):
    """The model's pixels on white (the reference the pipeline wants: theirs composites RGBA onto white)."""
    m = cv2.dilate(mask.astype(np.uint8), np.ones((2 * grow + 1,) * 2, np.uint8)) > 0 if grow else mask
    out = np.full_like(rgb, 255)
    out[m] = rgb[m]
    return out


def restyled_reference(sub, look=BA.LOOK, strength=BA.STRENGTH, words=BA.WORDS, extra_words="", seed=None,
                       url=BA.DEFAULT_URL, size=1024, describe=False, hide=None):
    """A fresh 2D-style front view: the model's front (as the game draws it, on paint2d_extras' plain background)
    restyled once with battleatlas' look, strength and words (the recipe's words for the model and its front view),
    then cut out onto white. Returns (reference RGB, the restyled picture, the input)."""
    from . import paint2d_extras as P2X
    from . import restyle
    bg = P2X.plain_backdrop(size, size)
    rgb, mask = front_view(sub, None, size, bg, hide)
    if sub.kind == "field":
        from . import fieldatlas as F
        what = F._words(sub.obj, words)
        hint = F.HINTS.get("front", "")
    else:
        what = BA._model_words(sub.id, words)
        hint = (BA.model_views(sub.id).get("hints") or {}).get("front", "Seen from the front.")
    prompt = " ".join(x for x in (what, (extra_words or "").strip(), hint) if x)
    with P2X.seeded(seed):
        s = restyle.look_settings(look)
    R = restyle.Restyler(s, url=url, kind="battle_model", name=f"mvpaint_ref_{sub.id}", prompt=prompt,
                         strength=strength, describe=describe)
    out = R(rgb, None, None)
    return cut_out(out, mask), out, rgb


# --- the job ---------------------------------------------------------------------------------------------------------

def write_job(sub, job_dir, ref_rgb, views=None, max_views=6, resolution=RESOLUTION, seed=0, steps=15, guidance=3.0,
              size=RENDER_SIZE, hide=None):
    """A job folder for mvpaint_texgen.py: job.json, ref.png, normal_NN.png and position_NN.png. hide: bones left out
    (shown()); the framing is then the rest's. Returns the job."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    mesh = shown(sub, hide)
    fr = frame(mesh)
    views = select_views(mesh, fr, max_views) if views is None else views
    for i, (e, a, _) in enumerate(views):
        nm, pm, _ = conditioning(mesh, hy_camera(fr, e, a, size), fr)
        Image.fromarray(nm).save(job_dir / f"normal_{i:02d}.png")
        Image.fromarray(pm).save(job_dir / f"position_{i:02d}.png")
    Image.fromarray(np.ascontiguousarray(ref_rgb)).save(job_dir / "ref.png")
    job = {"kind": sub.kind, "id": sub.id, "views": [list(v) for v in views], "resolution": int(resolution),
           "seed": int(seed), "steps": int(steps), "guidance": float(guidance), "render_size": int(size),
           "frame": {"centre": fr.centre.round(4).tolist(), "radius": round(fr.radius, 4)},
           "coverage": round(coverage(mesh, fr, views), 4), "hide": sorted(hide) if hide else []}
    (job_dir / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
    return job


def _gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def run_texgen(job_dirs, log_path=None):
    """mvpaint_texgen.py on the job folders, in tools/texgen/venv's Python (one process: the model loads once).
    Returns {"seconds", "gpu_before_mb", "gpu_peak_mb" (nvidia-smi, whole GPU), "returncode", per job its
    result.json}."""
    if not TEXGEN_PY.exists():
        raise FileNotFoundError(f"the texgen Python isn't there: {TEXGEN_PY}")
    job_dirs = [Path(j) for j in job_dirs]
    cmd = [str(TEXGEN_PY), "-I", str(TEXGEN_SCRIPT), "--hy", str(HY_CODE), "--hf-home", str(HF_HOME),
           *map(str, job_dirs)]
    before = _gpu_used_mb()
    peak = [before or 0]
    done = threading.Event()

    def poll():
        while not done.wait(0.5):
            u = _gpu_used_mb()
            if u:
                peak[0] = max(peak[0], u)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    t = time.time()
    log = open(log_path, "w", encoding="utf-8") if log_path else subprocess.DEVNULL
    try:
        env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF=ALLOC_CONF) if ALLOC_CONF else None
        rc = subprocess.run(cmd, cwd=str(job_dirs[0]), stdout=log, stderr=subprocess.STDOUT, env=env).returncode
    finally:
        done.set()
        th.join()
        if log_path:
            log.close()
    out = {"seconds": round(time.time() - t, 1), "gpu_before_mb": before, "gpu_peak_mb": peak[0], "returncode": rc,
           "jobs": {}}
    for j in job_dirs:
        try:
            out["jobs"][str(j)] = json.loads((j / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["jobs"][str(j)] = {"ok": False, "error": "no result.json"}
    return out


# --- baking ----------------------------------------------------------------------------------------------------------

def edge_weight(r, px, radius):
    """battleatlas.projectable's weight with its own distances: 0 at the model's outline and at occlusion edges
    (neighbouring pixels' depths more than JUMP x the model's radius apart: the camera is FAR radii away, so
    projectable's share of the depth would never trip), 1 from `px` pixels inside."""
    vis = r.tri >= 0
    z = np.where(vis, r.depth, np.inf).astype(np.float64)
    cut = ~vis
    for axis in (0, 1):
        a = np.swapaxes(z, 0, axis)
        with np.errstate(invalid="ignore"):
            d = np.abs(a[1:] - a[:-1])
        jump = ~(d <= JUMP * radius)  # (inf - inf, both off the model: nan, a jump too)
        c = np.swapaxes(cut, 0, axis)
        c[1:] |= jump
        c[:-1] |= jump
    dist = cv2.distanceTransform((~cut).astype(np.uint8), cv2.DIST_L2, 3)
    return vis, np.clip(dist / max(px, 1e-6), 0, 1).astype(np.float32) * vis


def albedo_views(job_dir, n, metal=0.0):
    """The generated views (RGB at the job's resolution); metal > 0: darkened by that share of their metallic map."""
    out = []
    for i in range(n):
        a = np.asarray(Image.open(Path(job_dir) / f"albedo_{i:02d}.png").convert("RGB")).astype(np.float32)
        if metal > 0:
            m = np.asarray(Image.open(Path(job_dir) / f"mr_{i:02d}.png").convert("RGB"))[..., 0].astype(np.float32) / 255
            a *= (1 - metal * m)[..., None]
        out.append(np.clip(a, 0, 255).astype(np.uint8))
    return out


def enlarge(img, size, upscaler="", url=BA.DEFAULT_URL):
    """A generated view at size x size: through the Studio's upscaler (restyle.upscale: a ComfyUI model such as
    4x-UltraSharp, or "" Lanczos), enlarged 4x first when it's a model and then resized."""
    from . import restyle
    h, w = img.shape[:2]
    if (w, h) == (size, size):
        return img
    if upscaler and size > w:
        big = restyle.upscale(np.ascontiguousarray(img), w * 4, h * 4, upscaler, url=url)
        return np.asarray(Image.fromarray(big).resize((size, size), Image.LANCZOS))
    return np.asarray(Image.fromarray(img).resize((size, size), Image.LANCZOS))


def bake(sub, views, images, size=PROJECT_SIZE, power=4.0, hide=None, P=None):
    """The atlas painted from the generated views (RGB, any size: resized to size): each projected with
    paint.Painter.add (facing and texel density ** power) weighted by its bake weight, an edge weight (EDGE_512) and the
    facing ramp (FACING), the kept triangles left out. hide: the job's hidden bones (its mesh and framing: shown());
    P: a Painter to add to (several jobs of one model). Returns (atlas RGBA, Painter, coverage: the share of the atlas'
    drawn texels some view painted)."""
    from . import fieldatlas as F
    mesh = shown(sub, hide)
    fr = frame(mesh)
    P = paintmod.Painter(sub.painted, 1, [sub.atlas.image]) if P is None else P
    P.mesh = mesh  # (Painter.add rasterizes its mesh)
    for (e, a, w), img in zip(views, images):
        cam = hy_camera(fr, e, a, size)
        img = np.asarray(Image.fromarray(np.ascontiguousarray(img)).resize((size, size), Image.LANCZOS)) \
            if img.shape[:2] != (size, size) else img
        r = render.rasterize(mesh, cam)
        vis, edge = edge_weight(r, EDGE_512 * size / 512, fr.radius)
        if len(sub.kept):
            vis &= ~np.isin(r.tri, sub.kept)
            edge = edge * vis
        lo, hi = FACING
        ramp = np.clip((render.facing(mesh, r, cam) - lo) / (hi - lo), 0, 1)
        weight = (w * edge * ramp).astype(np.float32)
        P.add(cam, r, img, np.where(edge * ramp >= BA.EDGE_MIN, weight, 0).astype(np.float32), power=power)
    P.mesh = sub.painted
    used = sub.atlas.used()
    cov = float(((P.wsum[0] > 0) & used).sum() / max(used.sum(), 1))
    return F.compose(sub.obj, P), P, cov


def grey_lock(sub, img, amount=GREY_LOCK):
    """The painting's light and dark pulled toward the game's where the game's colour is grey (its saturation under
    battleatlas.LOCK_MIN_SAT: steel, buckles, gloves): Hunyuan paints metal near white (PBR albedo), and its own
    metallic map can't say which parts those are (on battle Cloud it marked his bare arms metal too). amount: how far
    (0 the painting's, 1 the game's)."""
    if amount <= 0:
        return img
    p = cv2.cvtColor(np.ascontiguousarray(img[..., :3]), cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    o = cv2.cvtColor(np.ascontiguousarray(sub.atlas.image[..., :3]), cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    # grey by chroma (max - min), not HSV saturation: the game's dark blue-grey steel ((40, 50, 60): saturation 0.33)
    # reads as coloured by saturation
    rgb = sub.atlas.image[..., :3].astype(np.float32)
    grey = (rgb.max(-1) - rgb.min(-1)) < GREY_CHROMA
    p[..., 2] = np.where(grey, p[..., 2] * (1 - amount) + o[..., 2] * amount, p[..., 2])
    out = img.copy()
    out[..., :3] = cv2.cvtColor(np.clip(p, 0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB_FULL)
    return out


def finish_image(sub, img, lock="auto"):
    """The recipe's finishing: battle the model's colour_lock (the party's "hsv"); field fieldatlas' colour_lock
    ("hsv", greys kept grey), the decals restored and the twins copied. Returns (image, lock used)."""
    if sub.kind == "field":
        from . import fieldatlas as F
        lock = "hsv" if lock == "auto" else lock
        out = F.colour_lock(sub.obj, img, lock) if lock else img
        return F.copy_twins(sub.obj, F.restore_kept(sub.obj, out)), lock
    lock = BA.model_views(sub.id).get("lock") if lock == "auto" else lock
    return (BA.colour_lock(sub.obj, img, lock) if lock else img), lock


def _next_version(sub):
    d = sub.work()
    n = [int(p.name[1:]) for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()] if d.exists() else []
    return f"v{max(n, default=0) + 1}"


def view_sheets(job_dir, views, out_dir, cell=512):
    """preview/view_NN.jpg per view: normal map | position map | generated albedo (the Studio lists view_*.jpg)."""
    d = Path(out_dir) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(len(views)):
        ims = [Image.open(Path(job_dir) / f"{k}_{i:02d}.png").convert("RGB").resize((cell, cell), Image.LANCZOS)
               for k in ("normal", "position", "albedo")]
        Image.fromarray(np.hstack([np.asarray(x) for x in ims])).save(d / f"view_{i:02d}.jpg", quality=90)


def export_version(sub, img, out, info):
    """The version's files and pictures: battleatlas' or fieldatlas' export, previews and check()."""
    out = Path(out)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    if sub.kind == "field":
        from . import fieldatlas as F
        F.finish(sub.obj, img, out, info)
        return out
    ba = sub.obj
    info["identity"] = BA._save_identity(ba, out / "preview")
    info["weapon"] = ba.original.weapon if ba.original.weapon >= 0 else None
    BA.previews(ba, img, out)
    files = BA.export(ba, img, out, info)
    probs = BA.check(ba, files)
    if probs:
        raise ValueError(f"{out.name}: " + "; ".join(probs))
    return out


def ai_check(sub, img, out, url=BA.DEFAULT_URL):
    """paint2d_extras.check on the version (check.json, preview/check.jpg)."""
    from . import paint2d_extras as P2X
    model = P2X.Field(sub.obj) if sub.kind == "field" else P2X.Battle(sub.obj)
    return P2X.check(model, img, url, out)


def make_from_job(sub, job_dir, out=None, upscaler="", url=BA.DEFAULT_URL, lock="auto", reference="", texgen=None,
                  check=True, extra_info=None, metal=0.0, grey=GREY_LOCK):
    """A version from finished jobs (one, or several painting parts of the model: job_dir a list, baked into one
    atlas in order): bake, finish, export, the AI check. Returns (version folder, info)."""
    job_dirs = [Path(j) for j in job_dir] if isinstance(job_dir, (list, tuple)) else [Path(job_dir)]
    job_dir = job_dirs[0]
    jobs = [json.loads((j / "job.json").read_text(encoding="utf-8")) for j in job_dirs]
    job = jobs[0]
    t = time.time()
    P = None
    for jd, jb in zip(job_dirs, jobs):
        vs = [tuple(v) for v in jb["views"]]
        imgs = [enlarge(x, PROJECT_SIZE, upscaler, url) for x in albedo_views(jd, len(vs), metal)]
        raw, P, cov = bake(sub, vs, imgs, hide=jb.get("hide"), P=P)
    views = [tuple(v) for v in job["views"]]
    out = Path(out) if out else sub.work() / _next_version(sub)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    Image.fromarray(raw).save(out / "preview" / "unlocked.png")
    img, lock = finish_image(sub, grey_lock(sub, raw, grey), lock)
    res = {}
    try:
        rs = [json.loads((j / "result.json").read_text(encoding="utf-8")) for j in job_dirs]
        res = dict(rs[0], seconds=round(sum(r.get("seconds") or 0 for r in rs), 2)) if len(rs) > 1 else rs[0]
        if len(rs) > 1:
            res["parts"] = rs
    except (OSError, ValueError):
        pass
    words = ""
    info = {"painted": {"look": METHOD, "strength": None, "method": "mvpaint", "words": words, "describer": "",
                        "reference": reference,
                        "job": str(job_dir) if len(job_dirs) == 1 else [str(j) for j in job_dirs],
                        "hide": [jb.get("hide") or [] for jb in jobs], "yaw": sub.yaw, "resolution": job["resolution"],
                        "seed": job["seed"], "steps": job["steps"], "guidance": job["guidance"],
                        "upscaler": upscaler or "lanczos", "project_size": PROJECT_SIZE, "metal_dark": metal, "grey_lock": grey,
                        "coverage": round(cov, 4),
                        "view_coverage": job.get("coverage"),
                        "views": [dict(label=view_label(e, a), kind="hunyuan", new=1.0, elevation=e, azimuth=a,
                                       weight=w) for e, a, w in views],
                        "seconds": round((res.get("seconds") or 0) + time.time() - t),
                        "hunyuan": res, **({"texgen": texgen} if texgen else {})},
            **({"locked": lock} if lock else {}), **(extra_info or {})}
    export_version(sub, img, out, info)
    view_sheets(job_dir, views, out)
    for k, jd in enumerate(job_dirs):  # Hunyuan's raw views next to the version, for the record
        tag = "" if k == 0 else f"part{k + 1}_"
        for i in range(len(jobs[k]["views"])):
            Image.open(jd / f"albedo_{i:02d}.png").save(out / "preview" / f"hunyuan_{tag}albedo_{i:02d}.png")
        Image.open(jd / "ref.png").save(out / "preview" / f"{tag}reference.png")
    if check:
        try:
            info["check"] = {k: v for k, v in ai_check(sub, img, out, url).items() if k in ("ok", "reason", "seconds")}
        except Exception as e:  # advice only
            info["check"] = {"ok": None, "reason": f"{type(e).__name__}: {e}"}
    return out, info


# --- checks without AI ---------------------------------------------------------------------------------------------------

def dryrun(sub, out_dir, size=RESOLUTION):
    """The original's own views (as the game draws it) from Hunyuan's cameras at the diffusion's size, baked back as
    if Hunyuan had painted them: how much the 512 px views and the projection lose. Returns stats (identity of the
    baked atlas against the original from the front, and coverage)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fr = frame(sub.painted)
    views = list(BASE_VIEWS)
    imgs = []
    for e, a, _ in views:
        cam = hy_camera(fr, e, a, size)
        r = render.rasterize(sub.original, cam)
        imgs.append(BA.composite(sub.shade_original(r), r, (np.full((size, size, 3), 255, np.uint8),
                                                              np.full((size, size), np.inf, np.float32))))
    t = time.time()
    img, P, cov = bake(sub, views, imgs)
    secs = time.time() - t
    cam = hy_camera(fr, 20, 30, 1024)
    o = sub.shade_original(render.rasterize(sub.original, cam))
    r = render.rasterize(sub.painted, cam)
    a = paintmod.shade(sub.painted, r, [img])
    hit = r.tri >= 0
    d = np.abs(o.astype(int) - a.astype(int)).max(-1)[hit]
    Image.fromarray(np.hstack([o, a])).save(out_dir / "dryrun.jpg", quality=90)
    Image.fromarray(img).save(out_dir / "dryrun_atlas.png")
    return {"coverage": round(cov, 4), "mean": round(float(d.mean()), 2), "p99": float(np.percentile(d, 99)),
            "bake_seconds": round(secs, 1)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.mvpaint")
    ap.add_argument("cmd", choices=("conditioning", "dryrun"))
    ap.add_argument("kind", choices=("battle", "field"))
    ap.add_argument("id")
    ap.add_argument("--out")
    ap.add_argument("--views", type=int, default=6)
    a = ap.parse_args(argv)
    sub = subject(a.kind, a.id)
    out = Path(a.out) if a.out else JOBS / f"{a.kind}_{sub.id}_{a.cmd}"
    if a.cmd == "conditioning":
        rgb, mask = front_view(sub)
        job = write_job(sub, out, rgb, max_views=a.views)
        print(json.dumps({"out": str(out), **{k: job[k] for k in ("views", "coverage", "frame")}}, indent=1))
    else:
        print(json.dumps(dryrun(sub, out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
