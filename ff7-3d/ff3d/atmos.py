"""What the game adds around the characters, taken from a field's painting: the sun's direction (so their shadows fall
the way the painted ones do), its haze (so they fade with distance as the scene behind them does) and the glow of its
lights (drawn over them too)."""
import json
import re
from functools import lru_cache
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image

from . import common, glow as originals, gltfout, ibl
from ff7bg.sheets import PageCache, render_composite  # noqa: E402
from ff7bg import palettes  # noqa: E402  (common puts the Studio on the path)

# the sun: elevations kept off the horizon (long shadows across a painted backdrop look wrong; the fit leans low,
# where shading varies most) and off the zenith
SUN_ELEVATIONS = np.arange(40.0, 81.0, 5.0)
SUN_AZIMUTHS = np.arange(0.0, 360.0, 10.0)
SUN_MIN_FIT = 0.04   # below this share of the shading explained, the painting has no clear sun: FFNx's default
SUN_WEAK_FIT = 0.08  # below this, only its side is trusted: a high sun from there, short shadows under the characters
LUMA = np.array([0.299, 0.587, 0.114], np.float32)  # sRGB luma (the grade, painted lights)
HAZE_MAX = 0.2       # the most haze a character gets, at the far end of where it can walk (local contrast also falls
                     # with finer distant detail, so the measure reads high; 0.35 made far characters look ghostly)


def _linear(rgb):
    return (np.asarray(rgb, np.float32) / 255) ** 2.2


def _luma(lin):
    return lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def sun(f, size=192):
    """(light_rotation_vertical, light_rotation_horizontal, fit): the sun whose light best explains the painting's
    shading (its brightness over Marigold's de-lit colour) on Marigold's surface directions; FFNx's default and fit 0
    when nothing fits. Cached in work/<field>/sun.json for the render it was made from."""
    cache = f.work / "sun.json"
    stamp = f"{f.render.stat().st_mtime}"
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get("stamp") == stamp:
            return c["vertical"], c["horizontal"], c["fit"]
    from .build import marigold_normals, water_mask
    h = max(8, round(size * f.RH / max(f.RW, f.RH)))
    w = max(8, round(size * f.RW / max(f.RW, f.RH)))
    N = marigold_normals(f, (h, w), blur=1.0).reshape(-1, 3)
    render = np.asarray(Image.open(f.render).convert("RGB"))
    paint = _luma(_linear(cv2.resize(render, (w, h), interpolation=cv2.INTER_AREA))).ravel()
    albedo = _luma(_linear(cv2.resize(np.asarray(Image.open(f.work / "mg_albedo.png").convert("RGB")), (w, h),
                                      interpolation=cv2.INTER_AREA))).ravel()
    water = cv2.resize(water_mask(render), (w, h), interpolation=cv2.INTER_AREA).ravel()
    ok = (albedo > 0.03) & (paint > 0.01) & (paint < 0.95) & (water < 0.2) & np.isfinite(N).all(1)
    S = np.clip(paint[ok] / albedo[ok], 0, 4)
    N = N[ok]
    best = (*ibl.DEFAULT_SUN, 0.0)
    if len(S) > 500:
        dirs = [(v, a) for v in SUN_ELEVATIONS for a in SUN_AZIMUTHS]
        L = np.array([ibl.sun_direction(v, a) for v, a in dirs], np.float32)
        x = np.clip(N @ L.T, 0, None)  # light falling on each surface, for each candidate sun
        xc, sc = x - x.mean(0), S - S.mean()
        cov = sc @ xc
        r2 = np.where(cov > 0, cov ** 2 / np.maximum((xc ** 2).sum(0) * (sc ** 2).sum(), 1e-12), 0)
        i = int(np.argmax(r2))
        if r2[i] >= SUN_MIN_FIT:
            best = (float(dirs[i][0]) if r2[i] >= SUN_WEAK_FIT else 75.0, float(dirs[i][1]), float(r2[i]))
    cache.write_text(json.dumps({"stamp": stamp, "vertical": best[0], "horizontal": best[1], "fit": round(best[2], 4)}))
    return best


def haze(f):
    """The painting's haze for the characters: (sRGB colour 0..1, density per world unit, start distance). The
    colour is the farthest part of the scene; the density is how fast the painting's local contrast fades with
    distance, so a character far off fades as much as the scene around it; it starts where the walkable ground
    begins. Never more than the painting's own darks show where characters stand (haze_dark_bound), and capped so a
    character at the far end of the walkmesh keeps most of itself (HAZE_MAX)."""
    Z = np.load(f.work / "simplify_in.npz")["Z"].astype(np.float32)
    h, w = Z.shape
    render = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (w, h), interpolation=cv2.INTER_AREA)
    # camera distance of every grid point (background coordinates -> camera space)
    xs = (np.arange(w) + 0.5) / w * f.w + f.mx
    ys = (np.arange(h) + 0.5) / h * f.h + f.my
    bx, by = np.meshgrid(xs, ys)
    D = Z * np.sqrt(1 + (bx ** 2 + by ** 2) / f.zoom ** 2)
    ok = np.isfinite(D) & (D > 0)
    far = ok & (D >= np.percentile(D[ok], 95))
    colour = render[far].mean(0) / 255 if far.any() else render.reshape(-1, 3).mean(0) / 255
    # local contrast against distance: the painting's own haze
    lum = _luma(_linear(render))
    mean = cv2.blur(lum, (9, 9))
    contrast = np.sqrt(np.maximum(cv2.blur(lum * lum, (9, 9)) - mean * mean, 0)) / np.maximum(mean, 0.02)
    edges = np.percentile(D[ok], np.linspace(5, 98, 12))
    pts = [(np.median(D[ok & (D >= a) & (D < b)]), np.median(contrast[ok & (D >= a) & (D < b)]))
           for a, b in zip(edges[:-1], edges[1:]) if (ok & (D >= a) & (D < b)).sum() > 200]
    W = np.load(f.work / "walk_depth.npy")
    walk = W[np.isfinite(W)]
    start = float(np.percentile(walk, 5)) if walk.size else float(np.percentile(D[ok], 5))
    reach = float(np.percentile(walk, 95)) if walk.size else float(np.percentile(D[ok], 95))
    density = 0.0
    if len(pts) >= 4:
        d, c = np.array(pts, np.float64).T
        slope = np.polyfit(d, np.log(np.maximum(c, 1e-3)), 1)[0]
        density = max(0.0, -slope)
    if density > 0:
        density = min(density, haze_dark_bound(render, D, ok, colour, start, reach))
    if reach > start and density > 0:  # 1 - exp(-k (reach - start)) <= the cap
        # the cap shrinks with how far apart the walkable distances are: where they're all about as far away
        # (anfrst_3: 10% apart), every character would get the most haze for nothing, and haze lifts their dark
        # colours (Cloud looked ghostly)
        cap = HAZE_MAX * min(1.0, (reach - start) / start)
        density = min(density, -np.log(1 - cap) / (reach - start))
    return [round(float(v), 3) for v in colour], round(float(density), 7), round(start, 1)


HAZE_DARK_PCT = 2  # a distance's darks: this percentile of its dark channel (one stray pixel isn't a dark object)


def haze_dark_bound(render, D, ok, colour, start, reach, bands=8):
    """The most haze density the painting's own darks allow where characters stand. Haze adds its colour to
    everything in linear light, as the shader does, so a dark thing at distance d shows at least (1 - e^-k(d-start))
    of the haze colour, channel by channel: the dark channel (He et al.). Each distance band from where the walkable
    ground starts to a little past where it ends bounds k by its darks; the tightest band wins. A painting with
    true blacks across its walkable distances (del1's dock: bollards, shadows) gets none; the old contrast measure
    alone read the far sea and sky's smoothness as haze there, and lifted the party's darks by a third."""
    A = np.maximum(_linear(np.asarray(colour) * 255), 1e-3)
    dark = cv2.erode((_linear(render) / A).min(2), np.ones((3, 3), np.uint8))
    near = ok & (D > start) & (D <= reach * 1.15)
    if near.sum() < 200 * bands:
        return np.inf
    edges = np.percentile(D[near], np.linspace(0, 100, bands + 1))
    bound = np.inf
    for a, b in zip(edges[:-1], edges[1:]):
        m = near & (D >= a) & (D < b)
        d = float(np.median(D[m])) if m.sum() > 200 else start
        if d <= start * 1.05:  # too near the start to say anything about the rate
            continue
        lifted = min(float(np.percentile(dark[m], HAZE_DARK_PCT)), 0.95)
        bound = min(bound, -float(np.log1p(-lifted)) / (d - start))
    return bound


def light_map(f):
    """Every additive layer of the original art that's lit in the painting (lamps, signs, screens, light shafts:
    where its artists put light), static or animated, in its stored colours, at render size (RGB 0..1). Layers a
    script switches that were off when the render was made aren't in it: the game draws them when they come on
    (glow.overlay_plan / glow frames). Until 2026-11-01 they counted, and blin65_2's room lights, off until an
    event, lit its black pit yellow for good."""
    in_render, _ = originals.entry_states(f)
    keep = lambda t: t.z > 0 and (not t.param or in_render(t))
    pals = sorted({t.palette for t in f.bg.tiles if t.blending and t.type_trans in originals.ADDITIVE and keep(t)})
    canvas = np.zeros((f.h, f.w, 3), np.float32)
    file_pal = palettes.file_palettes(f.raw)
    for pal in pals:
        transparent = f.bg.transparency[pal] if pal < len(f.bg.transparency) else True
        canvas += originals.layer(f, pal, tuple(file_pal[pal]), transparent, keep)
    S = f.scale
    up = cv2.resize(canvas, (f.w * S, f.h * S), interpolation=cv2.INTER_CUBIC)
    out = np.zeros((f.RH, f.RW, 3), np.float32)
    y0, x0 = f.frame[1] * S, f.frame[0] * S
    out[y0:y0 + up.shape[0], x0:x0 + up.shape[1]] = up[: f.RH - y0, : f.RW - x0]
    return np.clip(out / 255, 0, 1)


def painted_lights(colour):
    """The painting's own lights (for fields whose original art has no light layers: Wall Market, Cosmo Canyon...), at
    half size, 0..1: near the top of what the picture holds and pale (yellow flowers and neon paint stay saturated),
    blobs rather than lines (stair edges and trim go), alone rather than one of many bright bits (flower beds, white
    walls, sky), and well above their surroundings. Weighted toward dark scenes, where lights matter."""
    small = cv2.resize(np.clip(colour, 0, 255), (f_half(colour.shape[1]), f_half(colour.shape[0])),
                       interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    side = small.shape[1]
    lum = small @ LUMA
    sat = small.max(2) - small.min(2)
    top = max(0.8, float(np.percentile(lum, 99.5)))
    hot = np.clip((lum - top + 0.08) / 0.08, 0, 1) * np.clip((0.55 - sat * (1 - lum) * 2.5) / 0.25, 0, 1)
    r = max(2, side // 300)
    hot = cv2.morphologyEx(hot, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    k = max(3, int(side / 12) | 1)
    alone = np.clip((0.12 - cv2.blur((lum > top - 0.08).astype(np.float32), (k, k))) / 0.08, 0, 1)
    stands = np.clip((lum - cv2.GaussianBlur(lum, (0, 0), side / 25) - 0.35) / 0.2, 0, 1)
    dark = float(np.clip(1.0 - np.median(lum) / 0.45, 0.2, 1.0))
    return hot * alone * stands * dark


def f_half(n):
    return max(1, n // 2)


def glow(f, colour, strength, out, lights=None, seen=None):
    """The painting's lights, blurred, at half size beside the scene (<field>_scene_glow.dds); the game adds it over
    everything 3D, characters too. Two sources: where the original art has light layers (lamps, signs, screens, light
    shafts: big lit areas glow less), and the painting's own lights (painted_lights). lights: life.lights' still
    (lit windows, lanterns, their light on the ground; linear, half size), added whatever the strength. seen: where
    the scene is (glow.backdrop_alpha; None: everywhere): none where it leaves the backdrop to the game. Nothing to
    add: no glow file. Returns this glow, linear (for flicker), or None."""
    path = out / f"{f.name}_scene_glow.dds"
    base = _glow(f, colour, strength) if strength > 0 else None
    total = base if lights is None else (lights if base is None else base + lights)
    if total is None or total.max() <= 1e-5:
        path.unlink(missing_ok=True)
        return None
    _write_glow(path, _scene_under(colour, total.shape), total, seen)
    return base


GLOW_CAP = 0.5  # the most light the glow adds anywhere, linear: never a white sheet


def _scene_under(colour, shape):
    """The scene's colour under the glow (colour: render size, 0..255), linear, at the glow's size."""
    return _linear(np.clip(cv2.resize(colour, (shape[1], shape[0]), interpolation=cv2.INTER_AREA), 0, 255))


def _write_glow(path, scene, light, seen=None):
    """A glow file adding `light` (linear) over `scene` (_scene_under). The game adds the file to the finished,
    gamma-encoded picture (FFNx-3d drawGlow, BLEND_ADD), so it holds the difference the light makes there: added as
    it was until 2026-11-01 (the light itself, gamma-encoded), faint light came out about 3x too strong over lit
    surfaces: halos spread wide, each light's box edge showed as a ring and the light on the ground went milky
    (cos_top). Over black it's the same as before. seen (render size, 0 where the scene leaves the backdrop to the
    game): nothing there, transparent (the glow is drawn on the scene's mesh, and the game's backdrop moves under it)."""
    lit = np.clip(scene + np.clip(light, 0, GLOW_CAP), 0, 1)
    rgb = np.clip(lit ** (1 / 2.2) - scene ** (1 / 2.2), 0, 1)
    a = np.full(rgb.shape[:2], 255, np.uint8)
    if seen is not None:
        on = cv2.resize(seen.astype(np.float32), (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_AREA) >= 0.5
        rgb, a = rgb * on[..., None], np.where(on, a, 0).astype(np.uint8)
    gltfout.write_dds(path, np.dstack([np.round(rgb * 255).astype(np.uint8), a]))


def _glow(f, colour, strength):
    half = (f_half(f.RW), f_half(f.RH))
    side = half[0]
    lin = _linear(np.clip(cv2.resize(colour, half, interpolation=cv2.INTER_AREA), 0, 255))
    lum = _luma(lin)
    emit = np.zeros_like(lin)
    lights = cv2.resize(light_map(f), half, interpolation=cv2.INTER_AREA)
    where = np.clip(cv2.GaussianBlur(lights.max(2), (0, 0), side / 300) / 0.12, 0, 1)
    if where.mean() >= 1e-4:
        emit += (lin * (np.clip((lum - 0.2) / 0.5, 0, 1) * where)[..., None] + _linear(lights * 255) * 0.5) *             min(1.0, 0.02 / where.mean())  # big lit areas (eals_1's shafts and waterfall: a tenth of the picture) far less
    painted = painted_lights(colour)
    if painted.max() > 0.05:
        emit += lin * cv2.resize(painted, half, interpolation=cv2.INTER_LINEAR)[..., None] * 1.5
    # compact sources only: what stands out from the broad light around it (a lamp does; a sheet of light shafts
    # over a waterfall doesn't, and bloomed into a white haze)
    emit = np.maximum(emit - cv2.GaussianBlur(emit, (0, 0), side / 20), 0)
    if emit.max() <= 1e-4:
        return None
    halo = 0.6 * cv2.GaussianBlur(emit, (0, 0), side / 160) + 0.4 * cv2.GaussianBlur(emit, (0, 0), side / 50)
    return halo * strength * 2.0


def grade(f, strength):
    """The characters' colour grade (FFNx-3d's char_grade: rows R, G, B of x, y, z, offset on sRGB 0..1): the new art
    against the original in brightness and contrast, colour balance and colour strength, each kept within sensible
    bounds and applied `strength` of the way. FF7's characters were coloured for the original backgrounds; this takes
    them along to the new ones. (One fitted colour matrix is unsafe: a scene that's nearly all one colour, farm's
    grass, leaves the others to chance, and greyed Cloud out.)"""
    rect = (f.mx, f.my, f.w, f.h)
    o = np.asarray(render_composite(f.bg, rect, PageCache(f.bg).tile, 1, lambda t: not t.param and not t.blending))
    n = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (f.w, f.h), interpolation=cv2.INTER_AREA)
    o, n = o.reshape(-1, 3).astype(np.float32), n.reshape(-1, 3).astype(np.float32)
    keep = (o.sum(1) > 6) & (n.sum(1) > 6)  # not the black around a small scene
    o, n = o[keep], n[keep]
    if len(o) < 200:  # a black scene (blackbg*, bugin1a): nothing to match, no grade
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0], {}
    Lo, Ln = o @ LUMA, n @ LUMA
    p = lambda x, q: float(np.percentile(x, q))
    contrast = float(np.clip((p(Ln, 90) - p(Ln, 10)) / max(p(Lo, 90) - p(Lo, 10), 1.0), 0.7, 1.3))
    brightness = float(np.clip(np.median(Ln) - contrast * np.median(Lo), -40, 40))
    chroma = lambda x: x.max(1) - x.min(1)
    sat = float(np.clip(np.median(chroma(n)) / max(float(np.median(chroma(o))), 1.0), 0.7, 1.4))
    # the light's colour: how the original's neutral things (stone, walls, clouds: low chroma) changed colour. Over
    # the whole picture it follows the repaint instead (farm's grass a different green turned chocobos peach)
    neutral = (chroma(o) < 0.12 * np.maximum(o.max(1), 1)) & (Lo > 25) & (Lo < 235)
    if neutral.sum() < 200:
        neutral = np.ones(len(o), bool)
    balance = lambda x: x.mean(0) / max(float(x.mean(0) @ LUMA), 1.0)
    g = np.clip(balance(n[neutral]) / balance(o[neutral]), 0.9, 1.1)
    g = g / float(g @ LUMA)
    # out = G S (contrast * rgb + brightness), as columns: rgb in 0..255
    T = np.diag(g) @ (sat * np.eye(3) + (1 - sat) * np.outer(np.ones(3), LUMA))
    lin, off = T * contrast, T @ np.full(3, brightness) / 255
    lin = (1 - strength) * np.eye(3) + strength * lin
    off = strength * off
    return [round(float(v), 4) for row, b in zip(lin, off) for v in (*row, b)],         {"contrast": round(contrast, 3), "brightness": round(brightness, 1), "saturation": round(sat, 3),
         "balance": [round(float(v), 3) for v in g]}


FLOOR_CELLS = 96  # the floor light map's size (cells across the walkmesh's larger side)
FLOOR_TINT = (0.88, 1.12)  # how far the floor's colour may tint the characters, per channel


def floor_light(f, strength, out):
    """The light on the field's floor, top-down, for the characters: how lit each spot of walkable ground is in the
    painting (its brightness over Marigold's de-lit colour, so a dark floor isn't a dark spot) against the scene's
    middle, plus a slight tint. Written beside the scene as <field>_floorlight.dds (RGB = multiplier / 2) over the
    walkmesh's world rectangle; FFNx-3d multiplies characters by it where they stand, `strength` of the way. Returns
    the lighting section's floor_light_rect / floor_light_strength (strength 0: no file)."""
    path = out / f"{f.name}_floorlight.dds"
    if strength <= 0:
        path.unlink(missing_ok=True)
        return {"floor_light_strength": 0.0}
    from . import depth
    from .build import grid_step, marigold_normals
    W = np.load(f.work / "walk_depth.npy")
    gh, gw = W.shape
    bx, by = depth.grid_bg(f, grid_step(f))
    bx, by = bx[:gh, :gw], by[:gh, :gw]
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    paint = _linear(cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (gw, gh), interpolation=cv2.INTER_AREA))
    albedo = _linear(cv2.resize(np.asarray(Image.open(f.work / "mg_albedo.png").convert("RGB")), (gw, gh),
                                interpolation=cv2.INTER_AREA))
    up = marigold_normals(f, (gh, gw))[..., 2]
    ok = np.isfinite(W) & (up > 0.6) & (albedo @ luma > 0.02) & (paint @ luma > 0.002)
    if ok.sum() < 200:
        path.unlink(missing_ok=True)
        return {"floor_light_strength": 0.0}
    # brightness on a log scale (one odd pixel can't swing a spot), colour as a small tint
    lum = np.log(np.clip((paint @ luma) / np.maximum(albedo @ luma, 0.01), 0.05, 20))
    # (the tint within FLOOR_TINT: at 0.7..1.3 a green floor dyed the characters green: hill, onna_4; user: "cap it")
    tint = np.clip((paint / np.maximum(albedo, 0.01)) / np.maximum(np.exp(lum)[..., None], 1e-3), *FLOOR_TINT)
    _, Pw = f.unproject(bx, by, np.where(np.isfinite(W), W, 1.0))
    tri = f.tris.reshape(-1, 3)
    x0, y0, x1, y1 = tri[:, 0].min(), tri[:, 1].min(), tri[:, 0].max(), tri[:, 1].max()
    size = max(x1 - x0, y1 - y0) * 1.05
    x0, y0 = (x0 + x1 - size) / 2, (y0 + y1 - size) / 2
    n = FLOOR_CELLS
    ix = np.clip(((Pw[..., 0] - x0) / size * n).astype(int), 0, n - 1)
    iy = np.clip(((Pw[..., 1] - y0) / size * n).astype(int), 0, n - 1)
    acc, cnt = np.zeros((n, n, 4)), np.zeros((n, n))
    np.add.at(acc, (iy[ok], ix[ok]), np.concatenate([lum[ok][:, None], tint[ok]], 1))
    np.add.at(cnt, (iy[ok], ix[ok]), 1)
    blur = lambda a: cv2.GaussianBlur(a.astype(np.float32), (0, 0), 2.0)  # about a character's size
    val = np.stack([blur(acc[..., k]) for k in range(4)], -1) / np.maximum(blur(cnt)[..., None], 1e-6)
    trust = (blur(cnt) / (blur(cnt) + 3.0))[..., None]
    middle, middle_tint = np.median(lum[ok]), np.median(tint[ok], axis=0)
    bright = np.exp(np.clip(0.7 * trust[..., 0] * (val[..., 0] - middle), np.log(0.5), np.log(1.6)))
    colour = np.clip(1 + trust * (val[..., 1:] / middle_tint - 1), 0.85, 1.15)
    m = bright[..., None] * colour
    rgba = np.dstack([np.round(np.clip(m / 2, 0, 1) * 255).astype(np.uint8), np.full((n, n), 255, np.uint8)])
    gltfout.write_dds(path, rgba)
    return {"floor_light_rect": [round(float(v), 1) for v in (x0, y0, x0 + size, y0 + size)],
            "floor_light_strength": round(float(strength), 3)}


OUTLINE_MAX = 1.2  # outline width at 100%, in background pixels (one original pixel is three screen pixels at 720p)
SOFT_MAX = 3.0     # character texture blur at 100%, in screen pixels of the game's internal picture


def char_material(finish):
    """The characters' material (FFNx's material_roughness, _metallic, _specular, per field in FFNx-3d): `finish` 1
    is FFNx's usual (rough 0.7, half metallic: painted plastic or metal under the lights), 0 matte like cloth and
    skin in a painting."""
    a = float(np.clip(finish, 0.0, 1.0))
    return {"material_roughness": round(0.95 - 0.25 * a, 3), "material_metallic": round(0.5 * a * a, 3),
            "material_specular": round(0.04 + 0.06 * a, 3)}


def char_exposure(finish):
    """How much to darken the characters (a factor on their colour grade, sRGB) so a matter finish keeps them as
    bright: a metal takes half the scene's soft light (diffuse x (1 - metallic)), so going matte doubled it and they
    looked washed out. Half that in full: much of their light is lamps' and reflections', which metal doesn't halve
    (the full amount made Kalm's characters by the furnace dim)."""
    metal = char_material(finish)["material_metallic"]
    return (0.5 / max(1.0 - metal, 0.5)) ** (0.5 / 2.2)


def char_style(f):
    """The field's part of the characters' outline for FFNx-3d: char_outline_scale, the width at 100% (OUTLINE_MAX
    background pixels) per unit of camera distance (the field's zoom maps one to the other), times the game-wide
    char_outline; char_outline_color, the painting's own darkest line work (its outlines, for styles that have
    them). Softness is game-wide (fields_settings)."""
    rgb = np.asarray(Image.open(f.render).convert("RGB"))
    small = cv2.resize(rgb, (rgb.shape[1] // 2, rgb.shape[0] // 2), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), 60, 160) > 0
    px = small[edges].astype(np.float32) if edges.sum() > 100 else small.reshape(-1, 3).astype(np.float32)
    lum = px @ LUMA
    dark = px[lum <= np.percentile(lum, 10)]
    colour = np.median(dark, axis=0) / 255 if len(dark) else np.array([0.08, 0.06, 0.06])
    return {"char_outline_scale": outline_scale(f.zoom), "char_outline_color": [round(float(c), 3) for c in colour]}


def outline_scale(zoom):
    return round(OUTLINE_MAX / zoom, 6)


def model_lights(raw):
    """The field's models (its model loader, section 3) with the light the game gives each: [(name, global colour,
    [(lamp colour, lamp direction) x 3])], colours as bytes, directions 4096 long."""
    import struct
    from ff7bg.field import _sections  # noqa: E402
    s = _sections(raw)[2]
    p, out = 6, []
    for _ in range(struct.unpack_from("<H", s, 2)[0]):
        (n,) = struct.unpack_from("<H", s, p)
        name = s[p + 2:p + 2 + n].split(b"\0")[0].decode("ascii", "replace")
        anims = struct.unpack_from("<H", s, p + 2 + n + 14)[0]
        p += 2 + n + 16  # name, then: 2 unknown, the HRC's name (8), its scale (4), how many animations (2)
        lamps = []
        for _ in range(3):
            lamps.append((tuple(s[p:p + 3]), struct.unpack_from("<3h", s, p + 3)))
            p += 9
        out.append((name, tuple(s[p:p + 3]), lamps))
        p += 3
        for _ in range(anims):  # each: its name's length, the name, 2 unknown
            p += 2 + struct.unpack_from("<H", s, p)[0] + 2
    return out


def _sphere(n=256):
    """`n` directions spread evenly over the sphere."""
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    a = np.pi * (1 + 5 ** 0.5) * i
    r = np.sqrt(1 - z * z)
    return np.stack([r * np.cos(a), r * np.sin(a), z], 1)


def game_light_colour(glob, lamps):
    """How colourful a model's own game light gets ((max - min) / max), summed as FFNx.lighting.frag does (linear;
    each lamp x its cosine squared) toward most directions (the 90th percentile over the sphere)."""
    lin = lambda c: (np.asarray(c, float) / 255) ** 2.2
    N = _sphere()
    tot = lin(glob)[None] + sum(lin(c)[None] * np.clip(N @ (-np.asarray(d, float) / 4096), 0, None)[:, None] ** 2
                                for c, d in lamps)
    hi, lo = tot.max(1), tot.min(1)
    return float(np.percentile(np.where(hi > 0, (hi - lo) / np.maximum(hi, 1e-9), 0.0), 90))


def game_light(f):
    """FFNx-3d's game_light_saturation for a field that lights its characters in a strong colour (hill's green and
    yellow lamps, the Forgotten City's blue, burning Nibelheim's red): the game's own light on its party (models
    named main_*; every model without them) more colourful than ibl.CHAR_SAT_MAX caps it at that, on every model of
    the field, brightness kept (F15, user 2026-11-03: "cap it": characters aren't dyed by a strongly coloured scene).
    Fields that light their party neutrally keep the game's colours, so what it colours on purpose with its lights
    there keeps them: farm's chocobos, the golden key, the Ancients' guide."""
    try:
        models = model_lights(f.raw)
    except Exception:  # noqa: BLE001 (a model loader we don't read: the game's colours)
        return {}
    party = [m for m in models if "main_" in m[0]] or models
    if party and max(game_light_colour(g, lamps) for _, g, lamps in party) > ibl.CHAR_SAT_MAX:
        return {"game_light_saturation": ibl.CHAR_SAT_MAX}
    return {}


def fields_settings(o, ambient):
    """The characters' game-wide settings as FFNx-3d's [fields] keys (every field takes them unless its own section
    says otherwise): shadows, haze, colour match, light where they stand, outline strength, softness, the shadow
    under them, finish, and their fill light (`ambient`, build.char_ambient)."""
    finish = float(o["char_finish"])
    return {"shadowmap_occlusion": round(1 - float(o["char_shadow"]), 3), "haze_strength": float(o["char_haze"]),
            "char_match": float(o["char_match"]), "floor_light_strength": float(o["char_local"]),
            "char_outline": float(o["char_outline"]), "char_soft": round(float(o["char_soft"]) * SOFT_MAX, 2),
            "contact_shadow": float(o["contact_shadow"]), **char_material(finish),
            "char_exposure": round(char_exposure(finish), 4), "char_ambient": ambient}


# Particles (FFNx-3d: <field>_particles.toml beside the scene). Per kind: how many the camera sees on a screen at
# 100%, their size and how high above the floor they reach (background pixels where the floor is, so they look the
# same in every field), how visible, their colour, and how far past the walkmesh they go (share of its size)
PARTICLES = {
    "dust": {"per_screen": 60, "size": 2.0, "height": 60, "alpha": 0.6, "color": [1.0, 0.95, 0.85], "around": 0.05,
             "lift": (0.0, 1.0)},
    "embers": {"per_screen": 150, "size": 2.2, "height": 110, "alpha": 1.0, "color": [1.0, 0.55, 0.2], "around": 0.05,
               "lift": (0.0, 1.0)},
    "fireflies": {"per_screen": 40, "size": 3.0, "height": 50, "alpha": 1.0, "color": [0.8, 1.0, 0.45], "around": 0.1,
                  "lift": (0.15, 1.0)},
    "snow": {"per_screen": 400, "size": 1.4, "height": 200, "alpha": 0.8, "color": [1.0, 1.0, 1.0], "around": 0.3},
    "rain": {"per_screen": 700, "size": 0.6, "height": 240, "alpha": 0.35, "color": [0.75, 0.8, 0.9], "around": 0.3},
    # snow driven by the wind, where the original has a blizzard (its scrolling layer gives the wind's direction)
    "blizzard": {"per_screen": 1300, "size": 2.2, "height": 200, "alpha": 0.9, "color": [0.95, 0.97, 1.0],
                 "around": 0.3, "wind": 110, "streak": 0.06},
}
FLOOR_KINDS = ("dust", "embers", "fireflies")  # start above the walkable floor (lift: share of their height)
FLOOR_POINTS = 400  # floor points sampled for them
FLOOR_SPREAD = 20   # how far around a floor point they start (background pixels)
PARTICLE_PX = 0.3   # ffnx3d_particles.cpp's speeds are for a field this many background pixels per world unit
SCREEN_PX = 320 * 240
# Automatic: where in the game a field is (its name), looked at area by area (2026-10-25); then the Studio's
# description of the art; then interiors (houses, inns, bars) by name
AUTO_FIELDS = {"snow": ("snow", "hyou1", "hyou2", "hyou3", "hyou4", "hyou5", "hyou6", "hyou7", "hyou8", "hyou9",
                        "gaia_", "gaiafoot", "gaiin_", "move_"),
               "embers": ("nivl_b",),            # Nibelheim burning
               "fireflies": ("slfrst", "blue_"),  # the Sleeping Forest, the Forgotten City's forest
               "dust": ("anfrst",)}               # the Ancient Forest: motes in its light
INSIDE_NAME = re.compile(r"inn|pb|_im?\b|_i\d|in\d|in_|in$")
INSIDE = r"\b(room|interior|inside|indoors?|hallway|corridor|bedroom|kitchen|bar|tavern|inn|shop|store|office|library|basement|attic|warehouse)\b"


def _caption(name):
    try:
        return json.loads((common.STUDIO / "ai" / "fields" / f"{name}.json").read_text(encoding="utf-8")).get(
            "caption", "").lower()
    except (OSError, ValueError):
        return ""


@lru_cache(maxsize=None)
def blizzard_wind(name):
    """The original's blizzard: the (x, y) screen direction a blended layer its scripts scroll moves in, or None.
    BGSCR's speed moves the layer's offset: the picture moves the other way (hyou1's -320, -80: right and down)."""
    with common.LgpArchive(str(common.LGP)) as lgp:
        raw = common.decompress(lgp.read(name))
    bg = common.parse_background(name, raw)
    for lay, v in originals.scroll_speeds(SimpleNamespace(raw=raw)).items():
        if v != (0, 0) and any(t.blending and t.layer == lay for t in bg.tiles):
            return -v[0], -v[1]
    return None


def particle_kind(name):
    """The particles a field gets on automatic: dust, embers, fireflies, snow, blizzard (snow where the original
    has a blizzard), rain or none."""
    kind = _particle_kind(name)
    return "blizzard" if kind == "snow" and blizzard_wind(name) else kind


def _particle_kind(name):
    for kind, prefixes in AUTO_FIELDS.items():
        if name.startswith(prefixes):
            return kind
    c = _caption(name)
    if c:
        inside = re.search(INSIDE, c)
        if re.search(r"\b(burning|blaze|blazing|flames|inferno|bonfire|campfire|lava|magma|embers)\b", c):
            return "embers"
        if not inside and re.search(r"\b(snow|snowy|snowfall|blizzard|snow-covered|glacier)\b", c):
            return "snow"
        if not inside and re.search(r"\b(rain|rainy|raining|rainfall|downpour|storm)\b", c):
            return "rain"
        if re.search(r"\b(forest|jungle|woods|swamp|grove)\b", c) and re.search(
                r"\b(night|nighttime|dark|dim|moonlit|misty|glowing)\b", c) and not re.search(
                r"\b(daytime|daylight|sunny|sunlight|overcast)\b", c):
            return "fireflies"
        if inside or re.search(r"\b(sunbeams?|sunlight filters|shafts? of light|dusty)\b", c):
            return "dust"
        return "none"
    return "dust" if INSIDE_NAME.search(name) else "none"


def _seen_box(f, box, n=20000):
    """The box cut down to the part the camera can see (the field's whole background), and the share of it that
    is: (box, share)."""
    rng = np.random.default_rng(0)

    def seen(b):
        P = rng.uniform(b[:3], b[3:], (n, 3))
        s, z = f.project(P)
        ok = (z > 1) & (s[:, 0] >= f.mx) & (s[:, 0] < f.mx + f.w) & (s[:, 1] >= f.my) & (s[:, 1] < f.my + f.h)
        return P[ok], max(float(ok.mean()), 1e-3)
    P, share = seen(box)
    if len(P) < 100:
        return box, share
    lo, hi = np.percentile(P, 1, 0), np.percentile(P, 99, 0)
    box = [max(box[0], lo[0]), max(box[1], lo[1]), box[2], min(box[3], hi[0]), min(box[4], hi[1]), box[5]]
    return box, seen(box)[1]


BORDER_PX = 16  # shadows fade out this far past the walkable floor's edge (background pixels: a chocobo's foot blob)


def floor_px(f):
    """Background pixels per world unit where the field's floor is (the camera's zoom over the floor's median depth)."""
    _, z = f.project(f.tris)
    return f.zoom / float(np.median(z[z > 0]))


def _source(s):
    """A particle source (life.py: chimney smoke, sparks, birds) as a [[source]] table."""
    num = lambda v: f"{float(v):.2f}"
    lines = ["", "[[source]]", f'kind = "{s["kind"]}"',
             f"points = [{', '.join(f'{v:.1f}' for v in np.asarray(s['points'], float).reshape(-1))}]"]
    for k in ("count", "size", "alpha", "spread", "rise", "grow"):
        if k in s:
            lines.append(f"{k} = {int(s[k]) if k == 'count' else num(s[k])}")
    for k in ("color", "wind", "life", "every"):
        if k in s:
            lines.append(f"{k} = [{', '.join(num(v) for v in s[k])}]")
    return lines


SHADOW_REACH = (1.5, 0.75)  # a character's shadow fades from this many of its heights below it, over this many more
DROP = 0.3                  # ...and the shadow skirt goes from walkmesh edges with ground this many heights lower beyond


def shadow_reach(f, extrude):
    """Where the characters' shadows may fall (lighting section keys): not onto floors well below them (FFNx's fade by
    the height between a shadow's caster and the floor it falls on: by default 1000 units, so a shadow showed on
    every floor under a walkway and the water under ancnt1's stepping stones), and no skirt (FFNx-3d's shadows
    fading out past the walkmesh's edge, `extrude` world units) over a drop, where the scene beyond an edge is much
    lower: shadows hung in the air there."""
    from collections import defaultdict
    from .build import CHAR_HEIGHT, model_scale
    h = CHAR_HEIGHT * model_scale(f) / 512
    out = {"shadowmap_fade_start_distance": round(SHADOW_REACH[0] * h, 1),
           "shadowmap_fade_range": round(SHADOW_REACH[1] * h, 1)}
    grid = f.work / "simplify_in.npz"
    if not grid.exists():
        return out
    Z = np.load(grid)["Z"]
    step = max(1, round(f.RW / Z.shape[1]))
    # border edges: in one triangle only (walkmesh vertices are whole numbers)
    edges = defaultdict(list)
    for t in f.tris:
        for i, j, k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            key = tuple(sorted((tuple(np.round(t[i]).astype(int)), tuple(np.round(t[j]).astype(int)))))
            edges[key].append(t[k])
    drops = []
    for (a, b), opp in edges.items():
        if len(opp) != 1:
            continue
        a, b, o = np.array(a, float), np.array(b, float), opp[0]
        n = np.array([-(b - a)[1], (b - a)[0], 0.0])
        n /= max(float(np.linalg.norm(n)), 1e-6)
        if n[:2] @ (o[:2] - a[:2]) > 0:  # outward, away from its triangle
            n = -n
        lower = 0
        for t in (0.25, 0.5, 0.75):
            P = a + t * (b - a) + n * 0.6 * extrude
            sxy, _ = f.project(P[None])
            rx, ry = (sxy[0, 0] - f.mx) * f.scale, (sxy[0, 1] - f.my) * f.scale
            gx, gy = int(rx // step), int(ry // step)
            if not (0 <= gx < Z.shape[1] and 0 <= gy < Z.shape[0]) or not np.isfinite(Z[gy, gx]):
                continue
            bx, by = f.to_bg(np.array(rx), np.array(ry))
            seen = f.unproject(bx, by, np.array(float(Z[gy, gx])))[1]  # what the scene shows there
            lower += P[2] - seen[2] > DROP * h
        if lower >= 2:
            drops += [round(float(v), 1) for v in (a + b) / 2]
    if drops:
        out["walkmesh_drops"] = drops
    return out


NEAR = 0.4        # dust, embers and fireflies nearer the camera than this share of the floor's depth stop growing on
                  # screen and fade out (gone at half of it; FFNx-3d `near`, the field-wide kind only): sininb41's low
                  # camera had dust a fifth of the way, big soft blobs
MASK_CELL = 4     # where on the canvas particles show (FFNx-3d `mask`): cells this many background px, at most MASK_MAX
MASK_MAX = 192    # across
FALLING = ("snow", "rain", "blizzard")
SNOWY = (0.6, 0.35)   # _outdoors: snow on the ground is this bright (HSV value) and this pale (saturation) at least
SNOWY_CLOSE = 11      # ...its shaded hollows and footprints closed over (background px)
OUTDOOR_RISE = 24     # ...and the falling snow shows this far above it (background px)


def _outdoors(f, kind, feats):
    """Where falling snow or rain shows in a scene Qwen calls indoors that its description gave them (snmin2, the Icicle
    Inn house cut away with snow outside: the snowfall came down over the whole room): over the snow on the ground
    (bright and pale, bluish) for snow, and Qwen's sky, and a little above them (OUTDOOR_RISE). 1x canvas, bool."""
    from . import life
    img = cv2.resize(life._render(f), (f.w, f.h), interpolation=cv2.INTER_AREA)
    out = np.zeros((f.h, f.w), bool)
    if kind != "rain":
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        snowy = (hsv[..., 2] >= SNOWY[0] * 255) & (hsv[..., 1] <= SNOWY[1] * 255) & (img[..., 2] >= img[..., 0])
        snowy = cv2.morphologyEx(snowy.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SNOWY_CLOSE,) * 2)
        snowy = cv2.morphologyEx(snowy, cv2.MORPH_CLOSE, close)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(snowy, connectivity=8)
        out |= np.isin(labels, [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= 0.002 * f.w * f.h])
    for b in feats.get("sky", []):
        x1, y1, x2, y2 = (int(round(b[k] / f.scale)) for k in ("x1", "y1", "x2", "y2"))
        out[max(0, y1):y2, max(0, x1):x2] = True
    up = out.copy()
    for d in range(1, OUTDOOR_RISE + 1):
        up[:-d] |= out[d:]
    return up


def _mask_keys(f, masks, near):
    """FFNx-3d's particle mask lines: the world -> mask cell projection (3x4: cell x = row 0 . [p 1] / row 2 . [p 1],
    y likewise; row 2 . [p 1] is the camera depth), the grid's size, each mask in `masks` (key: 1x canvas 0..1, or
    None) as one hex digit a cell (0 none .. f all), and `near` (camera depth, or None)."""
    cell = max(MASK_CELL, int(np.ceil(max(f.w, f.h) / MASK_MAX)))
    gw, gh = -(-f.w // cell), -(-f.h // cell)
    R, T, zoom = np.asarray(f.R, float), np.asarray(f.T, float), float(f.zoom)
    M = np.vstack([np.append(zoom * R[0] - f.mx * R[2], zoom * T[0] - f.mx * T[2]) / cell,
                   np.append(zoom * R[1] - f.my * R[2], zoom * T[1] - f.my * T[2]) / cell,
                   np.append(R[2], T[2])])
    lines = [f"mask_project = [{', '.join(f'{float(v):.9e}' for v in M.ravel())}]", f"mask_size = [{gw}, {gh}]"]
    for key, m in masks.items():
        if m is None or float(m.min()) >= 1:
            continue
        pad = np.zeros((gh * cell, gw * cell), np.float32)  # past the canvas: nothing
        pad[:f.h, :f.w] = m
        c = np.round(pad.reshape(gh, cell, gw, cell).mean((1, 3)) * 15).astype(int)
        lines.append(f'{key} = "' + "".join("0123456789abcdef"[v] for v in c.ravel()) + '"')
    if near:
        lines.append(f"near = {near:.1f}")
    return lines


def particles(f, kind, amount, out, sources=()):
    """The field's particles for FFNx-3d, <field>_particles.toml beside the scene: `kind` (auto: particle_kind),
    `amount` times its usual number over the walkable ground, sized and moving so they look the same on screen in
    every field, plus `sources` (life.py's smoke, sparks and birds). No file for none and no sources. Returns the
    kind. All of them only where the scene is on the canvas (not over the black around it: life.void); the floor
    kinds none nearer than NEAR of the floor's depth; the field-wide snow or rain of a scene Qwen calls indoors only
    over its outdoor part (_outdoors); and no field-wide kind for a picture framed on black (life.framed) on
    automatic."""
    from . import life
    path = out / f"{f.name}_particles.toml"
    auto = kind == "auto"
    kind = particle_kind(f.name) if auto else kind
    if auto and kind != "none" and life.framed(f):
        kind = "none"
    extra = [line for s in sources for line in _source(s)]
    s, z = f.project(f.tris)
    seen_floor = (z > 1).all(1).any()
    near = NEAR * float(np.median(z[z > 0])) if seen_floor and kind in FLOOR_KINDS else None
    scene = (~life.void(f)).astype(np.float32)
    main = None
    if kind in FALLING and auto and not any(f.name.startswith(p) for p in AUTO_FIELDS.values()):
        feats = life.cached(f)
        if (feats.get("scene") or {}).get("indoors"):
            main = (_outdoors(f, kind, feats) & (scene > 0)).astype(np.float32)
    head = _mask_keys(f, {"mask": scene, "mask_main": main}, near)
    if kind not in PARTICLES or amount <= 0:
        if extra:
            path.write_text("\n".join(['kind = "none"'] + head + extra) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return "none"
    p = PARTICLES[kind]
    if not seen_floor:  # no walkmesh in front of the camera (rootmap): nowhere to put them
        if extra:
            path.write_text("\n".join(['kind = "none"'] + head + extra) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return "none"
    ppu = f.zoom / float(np.median(z[z > 0]))  # background pixels per world unit where the floor is
    d1, d2 = s[:, 1] - s[:, 0], s[:, 2] - s[:, 0]
    a = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    pts = f.tris.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    m = p["around"] * float(max(hi[0] - lo[0], hi[1] - lo[1]))
    # heights: where most of the floor on screen is (a basement or a pit below it would hide most of them)
    zc = f.tris[..., 2].mean(1)
    order = np.argsort(zc)
    share = np.cumsum(a[order]) / max(a.sum(), 1e-6)
    z0, z1 = (float(zc[order][min(np.searchsorted(share, q), len(zc) - 1)]) for q in (0.2, 0.95))
    box = [lo[0] - m, lo[1] - m, z0, hi[0] + m, hi[1] + m, z1 + p["height"] / ppu]
    lines = []
    if kind in FLOOR_KINDS:
        # above points of the walkable floor the camera sees, as many per screenful of it: the walkmesh's box is
        # mostly inside the scenery (blue_1: a path between giant shells)
        c = s.mean(1)
        vis = (z > 1).all(1) & (c[:, 0] >= f.mx) & (c[:, 0] < f.mx + f.w) & (c[:, 1] >= f.my) & (c[:, 1] < f.my + f.h)
        if not vis.any():
            vis = (z > 1).all(1)
        # they start beyond `near` (FFNx-3d fades them out nearer): sininb41's low camera sees mostly near floor, and
        # nine in ten would have started there
        start = vis & (z.mean(1) >= near) if near else vis
        start = start if start.any() else vis
        rng = np.random.default_rng(0)
        pick = rng.choice(np.flatnonzero(start), FLOOR_POINTS, p=a[start] / a[start].sum())
        u, v = rng.random(FLOOR_POINTS), rng.random(FLOOR_POINTS)
        flip = u + v > 1
        u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
        T = f.tris[pick]
        P = T[:, 0] + u[:, None] * (T[:, 1] - T[:, 0]) + v[:, None] * (T[:, 2] - T[:, 0])
        H = p["height"] / ppu
        lines += [f"floor = [{', '.join(f'{x:.1f}' for x in P.reshape(-1))}]",
                  f"lift = [{p['lift'][0] * H:.1f}, {p['lift'][1] * H:.1f}]", f"spread = {FLOOR_SPREAD / ppu:.1f}"]
        screens, seen = float(np.clip(a[vis].sum() / SCREEN_PX, 0.6, 3.0)), 1.0
    else:  # falling: anywhere the camera sees, across the whole background as it pans
        box, seen = _seen_box(f, box)
        screens = f.w * f.h / SCREEN_PX
    if kind == "blizzard":  # the original's scroll direction on screen, turned level in the world
        sx, sy = blizzard_wind(f.name) or (1, 0)
        d = np.asarray(f.R, float).T @ np.array([sx, sy, 0.0])
        d[2] = 0
        d = d / max(float(np.linalg.norm(d)), 1e-6) * p["wind"] / PARTICLE_PX
        lines += [f"wind = [{d[0]:.1f}, {d[1]:.1f}, 0.0]", f"streak = {p['streak']}"]
    lines = [f'kind = "{kind}"',
             f"count = {int(np.clip(round(p['per_screen'] * amount * screens / seen), 8, 4000))}",
             f"size = {p['size'] / ppu:.2f}",
             f"alpha = {p['alpha']}",
             f"speed = {float(np.clip(PARTICLE_PX / ppu, 0.05, 20)):.3f}",
             f"color = [{', '.join(str(c) for c in p['color'])}]",
             f"box = [{', '.join(f'{float(v):.1f}' for v in box)}]"] + lines
    path.write_text("\n".join(lines + head + extra) + "\n", encoding="utf-8")
    return kind


CLOUD_PX = 220       # a cloud's size, background pixels where the floor is
CLOUD_DRIFT = 18     # how fast they drift, background pixels per second
CLOUD_MAX = 0.6      # how much darker the ground is under a cloud at 100%
FLICKER_FRAMES = 16  # glow frames FFNx-3d plays in order (glow_fps: life.GLOW_FPS), a looping flicker
GRASS_MAX = 1.5      # FFNx-3d's grass_wind at 100% (its blades lean about 0.1% of the texture)
GRASS_DIR = (1.0, 0.2)  # the wind across the texture: left to right, a little down


def grass(amount):
    """The lighting section's grass in the wind (FFNx-3d: grass_wind, grass_wind_dir; where: surface.grass_mask)."""
    return {"grass_wind": round(float(amount) * GRASS_MAX, 3), "grass_wind_dir": list(GRASS_DIR)} if amount > 0 else {}


def clouds(f, amount, extent):
    """The lighting section's cloud shadows (FFNx-3d: cloud_shadow, cloud_cells, cloud_drift): clouds CLOUD_PX
    across drifting CLOUD_DRIFT a second, `amount` of CLOUD_MAX darker under them; placed in the sun's shadow map
    (extent * 0.6 wide, ibl.lighting_section). Nothing at 0."""
    if amount <= 0:
        return {}
    area = extent * 0.6
    ppu = floor_px(f)
    drift = CLOUD_DRIFT / ppu / area
    return {"cloud_shadow": round(float(amount) * CLOUD_MAX, 3), "cloud_cells": round(area / (CLOUD_PX / ppu), 3),
            "cloud_drift": [round(drift * 0.8, 5), round(drift * 0.6, 5)]}


def flicker(f, amount, out, colour, base=None, lights=None, seen=None):
    """Warm lights flickering: frames of the scene's glow (<field>_scene_glow_<n>.dds beside it, which FFNx-3d cycles
    instead of the still one) where each warm light (flames, candles, lanterns: redder than blue) brightens and dims
    on its own, `amount` of +-35%; cool, electric ones stay steady. colour: the scene's (as for glow); base: glow's
    own (linear); lights: life.lights, whose windows and lanterns come with their own frames; seen: as glow's. None at
    0 or without a glow. Returns how many lights flicker."""
    for p in out.glob(f"{f.name}_scene_glow_*.dds"):
        p.unlink()
    extra = lights.get("frames") if lights else None
    if (amount <= 0 and not extra) or (base is None and not extra):
        return 0
    scene = _scene_under(colour, (base if base is not None else extra[0]).shape)
    if base is None:  # only lit windows and lanterns
        for i, fr in enumerate(extra):
            _write_glow(out / f"{f.name}_scene_glow_{i}.dds", scene, fr, seen)
        return int(lights["count"])
    base = np.clip(base, 0, GLOW_CAP)
    rgb = base ** (1 / 2.2) * 255  # (its lights found as they look)
    lum = rgb.mean(-1)
    mask = (cv2.GaussianBlur(lum, (0, 0), 3) > max(4.0, float(np.percentile(lum, 97)) * 0.25)).astype(np.uint8)
    n, labels = cv2.connectedComponents(cv2.dilate(mask, np.ones((9, 9), np.uint8)))
    warm = np.zeros(n, bool)
    for k in range(1, n):
        px = rgb[labels == k]
        warm[k] = px[:, 0].mean() > 1.3 * max(float(px[:, 2].mean()), 1.0)
    if not warm.any() and not extra:
        return 0
    rng = np.random.default_rng(3)
    gains = np.ones((FLICKER_FRAMES, n), np.float32)
    i = np.arange(FLICKER_FRAMES)
    for k in np.flatnonzero(warm) if amount > 0 else []:  # a smooth flicker that loops (frames play in order)
        noise = sum(rng.normal() * np.sin(2 * np.pi * c * i / FLICKER_FRAMES + rng.random() * 6.283) / c for c in range(1, 5))
        noise = noise / max(float(np.std(noise)), 1e-3)
        gains[:, k] = 1 + float(amount) * 0.35 * np.clip(noise, -2, 2) / 2
    for i in range(FLICKER_FRAMES):
        g = cv2.GaussianBlur(gains[i][labels], (0, 0), 4)  # soft edges between a light and its surroundings
        light = base * (g ** 2.2)[..., None]  # (the gain was on the glow as it looks)
        if extra:  # lit windows and lanterns, each on its own
            light = light + extra[i % len(extra)]
        _write_glow(out / f"{f.name}_scene_glow_{i}.dds", scene, light, seen)
    return int(warm.sum()) + (int(lights["count"]) if extra else 0)
