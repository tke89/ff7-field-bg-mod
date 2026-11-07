"""A field's surface maps for the game's light, from its painting and depth, at the strengths its options set
(build.DEFAULTS): the material map (R roughness, G metal, B specular, A occlusion) and fine relief in the normal map.

- occlusion: ambient occlusion from the depth (the build's ao.npy): corners, the floor under furniture and the gaps
  between things get less of the surrounding light. The paintings often have some already: best indoors.
- gloss: shine where the painting has highlights (much brighter than just around them and paler than the surface's
  own colour), spread over the surface they sit on: polished wood, bottles, wet floors, rails.
- metal: of the glossy surfaces, the grey ones reflect like metal.
- bump: the painting's fine texture (grain, cobbles, rock) as height, its slope added to the normal map. Subtle in
  the painted look (the texture's shading is painted in), more visible relit.
- grass: where grass and leaves are (green and textured), in the normal map's spare alpha (FFNx-3d moves them in the
  wind: 1 - alpha).

Tried 2026-10-25 on the bar, md1stin, the farm, condor1, nivl and del1: occlusion grounds furniture indoors, gloss
gives industrial scenes a wet sheen, bump barely shows painted."""
import shutil

import cv2
import numpy as np
from PIL import Image

from . import gltfout

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
BASE_ROUGH = 0.85      # every surface's roughness without gloss (water: build.water_mask)
GLOSS_ROUGH = 0.55     # how much smoother full gloss makes a surface
GLOSS_SPEC = 0.5       # and its specular
BUMP_MAX = 10.0        # the relief's slope at 100%
STEEP = (3.0, 4.5)     # tan of a surface's angle away from the camera: the normal map fades out between these
NEAR_BREAK = (3, 6)    # normal-map texels from a depth break: the map fades in between these


def _sat(c):
    return (c.max(-1) - c.min(-1)) / np.maximum(c.max(-1), 1e-3)


def gloss_metal(f, size):
    """(gloss, metal) 0..1 at `size` (w, h): where the painting has highlights, and which of those are grey."""
    render = np.asarray(Image.open(f.render).convert("RGB")).astype(np.float32) / 255
    albedo = np.asarray(Image.open(f.work / "mg_albedo.png").convert("RGB").resize(
        (render.shape[1], render.shape[0]), Image.LANCZOS)).astype(np.float32) / 255
    L = render @ LUMA
    local = cv2.GaussianBlur(L, (0, 0), 6)
    hl = np.clip((L - local * 1.25) / 0.12, 0, 1) * np.clip(1.2 - _sat(render) / np.maximum(_sat(albedo), 0.05), 0, 1)
    gloss = cv2.GaussianBlur(hl, (0, 0), 10)
    gloss = np.clip(gloss / max(float(np.percentile(gloss, 99.5)), 1e-3), 0, 1)
    grey = np.clip(1 - _sat(albedo) / 0.3, 0, 1) * np.clip((albedo @ LUMA - 0.08) / 0.2, 0, 1)
    metal = gloss * cv2.GaussianBlur(grey, (0, 0), 4)
    small = lambda a: np.clip(cv2.resize(a, size, interpolation=cv2.INTER_AREA), 0, 1)
    return small(gloss), small(metal)


def material(f, water, shine, water_shine, occlusion=0.0, gloss=0.0, metal=0.0):
    """The material map (RGBA uint8) at `water`'s size: rough non-metal, water smoother and shinier (`water` its
    mask, `water_shine` its specular, `shine` everything's), then the options' occlusion, gloss and metal."""
    h, w = water.shape
    rough = BASE_ROUGH - 0.6 * water
    spec = np.maximum(shine, water_shine * water)
    met = np.zeros_like(rough)
    occ = np.ones_like(rough)
    if gloss > 0 or metal > 0:
        g, m = gloss_metal(f, (w, h))
        rough = np.minimum(rough, BASE_ROUGH - GLOSS_ROUGH * g * gloss)
        spec = np.maximum(spec, GLOSS_SPEC * g * gloss)
        met = m * metal
    if occlusion > 0:
        ao = np.clip(cv2.resize(np.load(f.work / "ao.npy").astype(np.float32), (w, h),
                                interpolation=cv2.INTER_LINEAR), 0, 1)
        occ = 1 - occlusion * (1 - ao)
    pbr = np.stack([rough, met, spec, occ], -1)
    return np.round(np.clip(pbr, 0, 1) * 255).astype(np.uint8)


def grass_mask(f, size):
    """0..1 at `size` (w, h): grass and leaves, green and textured (a flat green wall or sign isn't)."""
    rgb = np.asarray(Image.open(f.render).convert("RGB").resize(size, Image.LANCZOS))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    # yellow-green grass (the farm's: hue ~70) up to blue-green leaves; olive-tinted towns (nivl: dirt ~55, dark
    # buildings ~60) aren't
    green = (np.clip((sat - 0.22) / 0.18, 0, 1) * np.clip((val - 0.1) / 0.12, 0, 1)
             * np.clip((hue - 60) / 8, 0, 1) * np.clip((175 - hue) / 12, 0, 1))
    L = rgb.astype(np.float32) @ LUMA / 255
    texture = np.sqrt(np.maximum(cv2.GaussianBlur(L * L, (0, 0), 3) - cv2.GaussianBlur(L, (0, 0), 3) ** 2, 0))
    return np.clip(cv2.GaussianBlur(green * np.clip((texture - 0.015) / 0.03, 0, 1), (0, 0), 1.5), 0, 1)


def steady(f, size):
    """How much of the normal map to keep per texel (size w, h): none on surfaces nearly edge-on to the camera and
    right beside depth breaks. The game builds each pixel's shading frame from screen-space derivatives, unsteady
    there, and the map showed as a band of bright dots along object edges (ancnt1, 2026-11-01: gone with these two
    faded out, and with a flat map; not with only its saturated texels flattened). Shading there is the mesh's own
    smoothed normal. None for scenes built without simplify_in.npz."""
    p = f.work / "simplify_in.npz"
    if not p.exists():
        return None
    d = np.load(p)
    # the depth as the mesh draws it, glow pushes and all (Z_drawn; Z is where the scene stands, F25): a push's edge is
    # a break in the mesh
    Z = (d["Z_drawn"] if "Z_drawn" in d.files else d["Z"]).astype(np.float32)
    # a surface turned theta away from the camera changes depth by tan(theta) / zoom of itself per background pixel
    slope = np.hypot(cv2.Sobel(Z, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(Z, cv2.CV_32F, 0, 1, ksize=3)) / 8
    tan = slope / np.maximum(Z, 1e-6) * float(d["grid"]) * f.zoom
    keep = np.clip((STEEP[1] - tan) / (STEEP[1] - STEEP[0]), 0, 1)
    whole = ~np.pad(d["broken"], ((0, 1), (0, 1)))
    texels = cv2.distanceTransform(whole.astype(np.uint8), cv2.DIST_L2, 3) * size[0] / Z.shape[1]
    keep *= np.clip((texels - NEAR_BREAK[0]) / (NEAR_BREAK[1] - NEAR_BREAK[0]), 0, 1)
    return cv2.resize(keep, size, interpolation=cv2.INTER_AREA)


def write_normals(f, path, bump, grass=False):
    """The scene's normal map at `path` with `bump` (0..1) of the painting's fine relief added, and where the grass
    is (grass_mask) in its alpha when `grass`. The build's own is kept as work/<field>/scene_nml_base.dds (build.py
    writes it; scenes built before then: the first one seen)."""
    base = f.work / "scene_nml_base.dds"
    if not base.exists():
        if not path.exists():
            return
        shutil.copyfile(path, base)
    nml = gltfout.read_dds(base)
    h, w = nml.shape[:2]
    if grass:
        nml[..., 3] = np.round(255 * (1 - grass_mask(f, (w, h)))).astype(np.uint8)
    keep = steady(f, (w, h))
    if bump <= 0 and keep is None:
        gltfout.write_dds(path, nml)
        return
    # the stored normal back to tangent space (simplify.encode's inverse), tilted by the slope, stored again
    xy = (nml[..., :2].astype(np.float32) - 128) / 127
    t = np.dstack([xy, np.sqrt(np.clip(1 - xy[..., 0] ** 2 + xy[..., 1] ** 2, 1e-6, None))])
    t /= np.linalg.norm(t, axis=-1, keepdims=True)
    if bump > 0:
        paint = np.asarray(Image.open(f.render).convert("RGB").resize((w, h), Image.LANCZOS)).astype(np.float32) / 255
        L = paint @ LUMA
        height = cv2.GaussianBlur(L - cv2.GaussianBlur(L, (0, 0), 4), (0, 0), 0.7)  # fine texture, not painted light
        t[..., 0] -= bump * BUMP_MAX * cv2.Sobel(height, cv2.CV_32F, 1, 0, ksize=3) / 8
        t[..., 1] -= bump * BUMP_MAX * cv2.Sobel(height, cv2.CV_32F, 0, 1, ksize=3) / 8
        t /= np.linalg.norm(t, axis=-1, keepdims=True)
    if keep is not None:
        t[..., :2] *= keep[..., None]
        t /= np.linalg.norm(t, axis=-1, keepdims=True)
    t[..., 2] = np.maximum(t[..., 2], 1e-3)
    k = 1 / np.sqrt(np.maximum(t[..., 2] ** 2 + t[..., 0] ** 2 - t[..., 1] ** 2, 1e-6))
    out = nml.copy()
    out[..., :2] = np.round(128 + 127 * np.clip(t[..., :2] * k[..., None], -1, 1)).astype(np.uint8)
    crack = (nml[..., 0] == 128) & (nml[..., 1] == 128)
    out[crack] = nml[crack]
    gltfout.write_dds(path, out)
