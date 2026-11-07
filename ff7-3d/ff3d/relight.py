"""The Relit look (FFNx-3d's relit scene): the painting under the game's light instead of the painter's.

The game shows the painting x (the light on it now / the light on it in the painting), both worked out the same way
(lighting.frag), so with the painting's own sun it shows the painting, and as the sun moves the scene lightens where
the sun now reaches and darkens where it's gone. This step finds the painting's sun (its shading and its cast
shadows), the light's colours, and where the sun reaches over the scene's shape for both suns: <field>_scene_sun.dds,
R now, G in the painting, B where the scene is relit at all (not the sky, water or the lights themselves).
Interiors have no sun: only their lamps change (they flicker on the walls)."""
import json

import cv2
import numpy as np
from PIL import Image

from . import atmos, glow, gltfout, ibl, life

MAX_GAIN = 3.0        # the most a part of the painting brightens (a deep painted shadow the sun now reaches)
FIT_SIZE = 240        # the sun's fit on a grid this wide
FIT_ELEVATIONS = np.arange(15.0, 81.0, 5.0)
FIT_AZIMUTHS = np.arange(0.0, 360.0, 10.0)
FIT_SHORTLIST = 16    # suns re-scored with their cast shadows (the shading alone ranks them first)
MIN_FIT = 0.05        # less of the painting's light explained than this: no clear sun (as atmos.sun)
MARCH_STEPS = 40
CONTRAST = (0.6, 4.0)  # the painting's sun against its sky, kept within
GAME_CONTRAST = (4.0 / np.pi) / 1.5  # the game's at 3D shading 50%: Relit's sun 4 over pi (Lambert) against its sky
                                     # light 1.5 x the image-based light's ~1 (envmap.environment)


def _grid(f, Z, step):
    from . import depth
    from .build import marigold_normals
    bx, by = depth.grid_bg(f, step)
    gh, gw = Z.shape
    ok = np.isfinite(Z)
    _, P = f.unproject(bx[:gh, :gw], by[:gh, :gw], np.where(ok, Z, 1.0))
    return P.astype(np.float32), marigold_normals(f, (gh, gw)).astype(np.float32), ok


def sun_reach(f, sun, Z, step, P, N, ok, reach, char_h):
    """0..1 on the depth grid: whether the sun (light_rotation degrees) reaches each point, marching toward it over
    the depth map: blocked where the scene shows something in front of the march, no thicker than an object would be
    (thin posts don't throw a wall of shadow behind them)."""
    gh, gw = Z.shape
    L = ibl.sun_direction(*sun).astype(np.float32)
    start = P + N * (0.15 * char_h) + L * (0.2 * char_h)  # off the surface: it doesn't shadow itself
    Zf = np.where(ok, Z, np.inf).astype(np.float32)
    lit = np.ones(gh * gw, bool)
    for t in reach * np.linspace(0.0, 1.0, MARCH_STEPS + 1)[1:] ** 2:  # finer near the point
        p = (start + L * t).reshape(-1, 3)
        s, pz = f.project(p)
        gu = (s[:, 0] + f.frame[0] - f.mx) * f.scale / step
        gv = (s[:, 1] + f.frame[1] - f.my) * f.scale / step
        inside = (gu >= 0) & (gu < gw - 1) & (gv >= 0) & (gv < gh - 1) & (pz > 0)
        ui = np.clip(np.round(gu).astype(int), 0, gw - 1)
        vi = np.clip(np.round(gv).astype(int), 0, gh - 1)
        zmap = Zf[vi, ui]
        thick = np.maximum(2.5 * char_h, 0.04 * zmap)
        lit &= ~(inside & (zmap < pz * 0.996) & (pz - zmap < thick))
    lit = lit.reshape(gh, gw).astype(np.float32)
    lit[~ok] = 1.0
    return cv2.GaussianBlur(lit, (0, 0), 0.8)


def _small(f, Z, step):
    """The depth grid at about FIT_SIZE wide, with its step in render pixels."""
    k = max(1, Z.shape[1] // FIT_SIZE)
    gh, gw = f.grid_shape(step * k)
    return Z[::k, ::k][:gh, :gw], step * k


def painting_sun(f, Z, step, char_h, reach):
    """(vertical, horizontal, fit, shading contrast): the sun that best explains the painting's light over its
    de-lit colour (Marigold) with both its shading and the shadows it casts over the scene's shape; fit is the share
    of the painting's light it explains (0: none clear), contrast the sun's light against the sky's in the painting.
    Cached in work/<field>/relight_sun.json for the render."""
    cache = f.work / "relight_sun.json"
    stamp = f"{f.render.stat().st_mtime}"
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get("stamp") == stamp:
            return c["vertical"], c["horizontal"], c["fit"], c["contrast"]
    Zs, ss = _small(f, Z, step)
    P, N, ok = _grid(f, Zs, ss)
    gh, gw = Zs.shape
    render = np.asarray(Image.open(f.render).convert("RGB"))
    paint = atmos._luma(atmos._linear(cv2.resize(render, (gw, gh), interpolation=cv2.INTER_AREA)))
    albedo = atmos._luma(atmos._linear(cv2.resize(np.asarray(Image.open(f.work / "mg_albedo.png").convert("RGB")),
                                                  (gw, gh), interpolation=cv2.INTER_AREA)))
    from .build import water_mask
    water = cv2.resize(water_mask(render), (gw, gh), interpolation=cv2.INTER_AREA)
    use = ok & (albedo > 0.03) & (paint > 0.01) & (paint < 0.95) & (water < 0.2) & np.isfinite(N).all(-1)
    S = np.clip(paint / np.maximum(albedo, 1e-3), 0, 4)[use]
    best = (60.0, 60.0, 0.0, 0.0)
    if len(S) > 500:
        def score(x):  # S = a + b x: the share of S explained, and b / a
            xc, sc = x - x.mean(), S - S.mean()
            cov = float(sc @ xc)
            if cov <= 0:
                return 0.0, 0.0
            b = cov / max(float(xc @ xc), 1e-9)
            a = S.mean() - b * x.mean()
            return cov ** 2 / max(float(xc @ xc) * float(sc @ sc), 1e-12), b / max(a, 0.05)
        dirs = [(v, h) for v in FIT_ELEVATIONS for h in FIT_AZIMUTHS]
        Nu = N[use]
        shading = [np.clip(Nu @ ibl.sun_direction(v, h), 0, None) for v, h in dirs]
        first = sorted(range(len(dirs)), key=lambda i: -score(shading[i])[0])[:FIT_SHORTLIST]
        for i in first:
            vis = sun_reach(f, dirs[i], Zs, ss, P, N, ok, reach, char_h)[use]
            r2, contrast = score(shading[i] * vis)
            if r2 > best[2]:
                best = (float(dirs[i][0]), float(dirs[i][1]), r2, contrast)
    if best[2] < MIN_FIT:
        best = (60.0, 60.0, best[2], 0.0)
    out = {"stamp": stamp, "vertical": best[0], "horizontal": best[1], "fit": round(best[2], 4),
           "contrast": round(best[3], 3)}
    cache.write_text(json.dumps(out))
    return best


LIGHT_SPREAD = 1.5    # the sun's colour at most this far from the sky's, channel by channel


def light_colours(f, render, lit):
    """The painting's sun and sky colours (linear, luma 1): the average colour of what the sun reached (lit > 0.6)
    and of what only the sky did (lit < 0.1), the things in both alike on the whole (warm sunlit sides, cool shade).
    Marigold's de-lit colour kept the painted shadows' cast, so it can't tell. White without enough of either."""
    h, w = lit.shape
    paint = atmos._linear(cv2.resize(render, (w, h), interpolation=cv2.INTER_AREA))
    ok = (atmos._luma(paint) > 0.005) & (atmos._luma(paint) < 0.95)

    def colour(where):
        m = ok & where
        if m.sum() < 200:
            return None
        c = paint[m].mean(0)
        return c / max(float(atmos._luma(c)), 1e-4)
    sun, sky = colour(lit > 0.6), colour(lit < 0.1)
    if sun is None or sky is None:
        return np.ones(3, np.float32), np.ones(3, np.float32)
    sun = sky * np.clip(sun / sky, 1 / LIGHT_SPREAD, LIGHT_SPREAD)
    return sun / max(float(atmos._luma(sun)), 1e-4), sky


def keep_painted(f, render, feats, Z, step):
    """0..1 at render size, 1 where the painting stays as painted: the sky, water and the lights themselves."""
    keep = life.water(f, render).astype(np.float32)
    sky = life.sky_mask(f, feats, Z, step, render, 0) if feats else None
    if sky is not None:
        sky = life.whole_sky(render, sky)
        keep = np.maximum(keep, cv2.resize(sky.astype(np.float32), (f.RW, f.RH), interpolation=cv2.INTER_LINEAR))
    no_depth = cv2.resize((~np.isfinite(Z)).astype(np.float32), (f.RW, f.RH), interpolation=cv2.INTER_LINEAR)
    lights = np.clip(cv2.GaussianBlur(atmos.light_map(f).max(2), (0, 0), 2) / 0.12, 0, 1)
    painted = cv2.resize(atmos.painted_lights(render), (f.RW, f.RH), interpolation=cv2.INTER_LINEAR)
    glass = glow.through_glass(f)  # a far backdrop through a window, like the sky (blin66_2's nebula)
    backdrop = glass[1] if glass is not None else np.zeros_like(keep)
    return np.clip(np.maximum.reduce([keep, no_depth, lights, painted, backdrop]), 0, 1)


def write(f, o, ll, out, tex):
    """The relit scene's sun map beside it: (its lighting keys, the game's sun (vertical, horizontal) or None for
    interiors and scenes without a clear sun, which keep only their lamps' change, a report)."""
    Z, step = life._depth(f)
    char_h = life._char_height(f)
    reach = 0.5 * float((f.work / "extent.txt").read_text())
    feats = life.cached(f) or {}
    indoors = bool((feats.get("scene") or {}).get("indoors"))
    v, h, fit, contrast = painting_sun(f, Z, step, char_h, reach)
    sunny = not indoors and fit >= MIN_FIT
    now = (float(np.clip(v + float(o["sun_height"]), 5.0, 89.0)), float((h + float(o["sun_turn"])) % 360.0))
    render = np.asarray(Image.open(f.render).convert("RGB"))
    gh, gw = Z.shape
    if sunny:
        P, N, ok = _grid(f, Z, step)
        then_lit = sun_reach(f, (v, h), Z, step, P, N, ok, reach, char_h)
        now_lit = then_lit if now == (v, h) else sun_reach(f, now, Z, step, P, N, ok, reach, char_h)
        shade = np.clip(np.einsum("ijk,k->ij", N, ibl.sun_direction(v, h)), 0, None) * then_lit
        sun_c, sky_c = light_colours(f, render, shade)
    else:  # no sun on the scene: only the lamps change
        then_lit = now_lit = np.zeros((gh, gw), np.float32)
        sun_c = sky_c = np.ones(3, np.float32)
    tint = float(ll["light_tint"])
    sun_c, sky_c = [1 + tint * (c - 1) for c in (sun_c, sky_c)]
    if sunny and contrast > 0:  # the sun as strong against the sky as the painter's (3D shading still scales it)
        k = float(np.clip(contrast, *CONTRAST)) / GAME_CONTRAST
        sun_c, sky_c = sun_c * np.sqrt(k), sky_c / np.sqrt(k)
    relit = 1.0 - keep_painted(f, render, feats, Z, step)
    relit = cv2.resize(relit, (gw, gh), interpolation=cv2.INTER_AREA)
    # A: how much each part may mirror the characters as a polished floor (life.floor_mirror; for FFNx-3d's
    # reflection pass, which until it reads this mirrors every surface its normals say faces up)
    sun_map = np.dstack([now_lit, then_lit, relit, life.floor_mirror(f, render, (gh, gw))])
    gltfout.write_dds(out / f"{tex}_sun.dds", np.round(np.clip(sun_map, 0, 1) * 255).astype(np.uint8))
    lamps = 1.0 if float(o["char_lamps"]) > 0 else 0.0
    report = {"sun": [v, h], "fit": round(fit, 3), "contrast": round(contrast, 2), "sunny": sunny,
              "now": list(now), "sun_colour": np.round(sun_c, 3).tolist(), "sky_colour": np.round(sky_c, 3).tolist()}
    (f.work / "relight.json").write_text(json.dumps(report))
    keys = {"relight": [1.0, MAX_GAIN, round(float(ll["brightness"]), 3), lamps],
            "relight_sun": [v, h], "relight_colors": [round(float(x), 4) for x in (*sun_c, *sky_c)]}
    return keys, (now if sunny else None), report


def clear(out, tex):
    (out / f"{tex}_sun.dds").unlink(missing_ok=True)
