"""A field's 2D background in the 3D scene. Every page is hidden (the mesh replaces it) except
- its animated additive layers (waterfalls, lamps, screens): FFNx draws those over the mesh as animated textures, one
  per palette state the field's scripts set, each carrying only the moving light, since the painted look is already
  in the mesh;
- its overlays: toggled effects (smoke, haze, light shafts) and toggled tiles the render doesn't hold (animation
  frames, things that appear later), which the game keeps drawing and switching as in 2D, and blended layers a
  script keeps scrolling (the Great Glacier's blizzard, mist, waves), which the game keeps moving;
- its parallax layers where they move apart from the scene (drawn_layers: the far backdrop, layer 2, and a layer 3
  a script set among the scene), which the game draws behind the scene at their parallax place while the scene
  leaves them out (backdrop_alpha)."""
import hashlib
import io
import json
import math
import os
import struct

import cv2
import numpy as np
from PIL import Image

from . import common
from ff7bg import palettes  # noqa: E402  (common puts the Studio on the path)
from ff7bg.sheets import PageCache, draw_order, page_tile_source, render_composite, tile_alpha  # noqa: E402

ADDITIVE = {1, 3}  # tile blend types that add light: 1 = add, 3 = add a quarter
BGSCR = 0x2D       # field opcode: scroll a background layer (banks, layer, x speed, y speed)
SCROLL_BANDS = 1.5  # a scrolling layer's own shapes (wind bands, mist) kept down to this size (original pixels)
SCROLL_GRAIN = 1.3  # its fine grain redrawn at full resolution, this much as strong as the original's
SCROLL_STREAK = 1.5  # the grain drawn out along the scroll (the wind), this long (original pixels)


def scroll_speeds(f):
    """{layer: (x, y) speed} for the background layers the field's scripts scroll (BGSCR): blizzards, mist, waves,
    views from moving trains. (0, 0) when the speed comes from a variable or only stops it."""
    out = {}
    for s in palettes.scripts(f.raw):
        for _, op, b in s.ops():
            if op == BGSCR and len(b) >= 7:
                v = (0, 0) if b[1] else (int.from_bytes(b[3:5], "little", signed=True),
                                         int.from_bytes(b[5:7], "little", signed=True))
                if out.get(b[2], (0, 0)) == (0, 0):
                    out[b[2]] = v
    return out


def scrolling_layers(f):
    return set(scroll_speeds(f))


def scrolling(f, t, layers):
    """A blended tile on a scrolling layer: it moves over the picture, so the game draws it, as in 2D."""
    return bool(t.blending) and t.layer in layers


def scrolling_art(f, tiles, s, speed, grain_gain=SCROLL_GRAIN, seed=7):
    """A scrolling layer's tiles redrawn `s` times larger as one seamless picture (the layer wraps as it scrolls):
    its own shapes kept smooth, its grain drawn new at full resolution and streaked along `speed`. Returns
    (RGBA canvas, its top-left in background coordinates)."""
    cache = PageCache(f.bg)
    x0, y0 = min(t.dst_x for t in tiles), min(t.dst_y for t in tiles)
    W, H = max(t.dst_x + t.size for t in tiles) - x0, max(t.dst_y + t.size for t in tiles) - y0
    src = np.zeros((H, W, 4), np.float32)
    for t in tiles:
        src[t.dst_y - y0:t.dst_y - y0 + t.size, t.dst_x - x0:t.dst_x - x0 + t.size] = cache.tile(t)
    pad = 8
    wrap = lambda a, p: np.pad(a, ((p, p), (p, p), (0, 0)), mode="wrap")
    low = cv2.GaussianBlur(wrap(src[..., :3], pad), (0, 0), SCROLL_BANDS)[pad:-pad, pad:-pad]
    grain = float((src[..., :3] - low).std())
    up = cv2.resize(wrap(low, pad), ((W + 2 * pad) * s, (H + 2 * pad) * s),
                    interpolation=cv2.INTER_CUBIC)[pad * s:-pad * s, pad * s:-pad * s]
    noise = np.random.default_rng(seed).normal(size=(H * s + 2 * pad * s, W * s + 2 * pad * s)).astype(np.float32)
    vx, vy = speed
    if vx or vy:  # a short line along the wind
        n = max(3, int(round(SCROLL_STREAK * s)) | 1)
        k = np.zeros((n, n), np.float32)
        d = np.array([vx, vy], np.float32) / np.hypot(vx, vy)
        for i in np.linspace(-(n - 1) / 2, (n - 1) / 2, 4 * n):
            k[int(round(n // 2 + i * d[1])), int(round(n // 2 + i * d[0]))] = 1
        noise = cv2.filter2D(noise, -1, k / k.sum())
    noise = noise[pad * s:-pad * s, pad * s:-pad * s]
    noise /= max(float(noise.std()), 1e-6)
    out = np.empty((H * s, W * s, 4), np.float32)
    out[..., :3] = up + (noise * grain * grain_gain)[..., None]
    out[..., 3] = cv2.resize(src[..., 3], (W * s, H * s), interpolation=cv2.INTER_NEAREST)
    return np.clip(out, 0, 255).astype(np.uint8), (x0, y0)


def animated(f):
    """palettes.animated_textures, from the Studio's cache (ai/palette_states/<field>.json, which its builds keep):
    replaying the field's scripts took crcin_1 36 of its 37 minutes (32 million script steps), every build."""
    return palettes.animated_textures_cached(f.raw, f.bg, common.STUDIO / "ai" / "palette_states" / f"{f.name}.json")


def layers(f):
    """{'glowing': {(page, pal): {hash: colours}}, 'rects': layer bounding boxes in background coords plus the
    layer's tile depth (x0, y0, x1, y1, z), 'summary': text}. Animated layers that aren't additive stay as painted (static) in the mesh."""
    anim = animated(f)
    glowing, rects, static = {}, [], []
    for (page, pal), by_hash in anim.items():
        tiles = [t for t in f.bg.tiles if t.palette == pal and t.tex == page]
        if tiles and all(t.blending and t.type_trans in ADDITIVE for t in tiles):
            glowing[(page, pal)] = by_hash
            rects.append((min(t.dst_x for t in tiles), min(t.dst_y for t in tiles),
                          max(t.dst_x + t.size for t in tiles), max(t.dst_y + t.size for t in tiles),
                          max(t.z for t in tiles)))
        else:
            static.append(pal)
    summary = (f"{len(glowing)} animated glow layer(s)" if glowing else "no animated glow layers") + \
              (f", palettes {sorted(set(static))} animated but not additive (static for now)" if static else "")
    return {"glowing": glowing, "rects": rects, "summary": summary}


def light_kept(f):
    """How much of the static light layers (static_light) the render already shows, about 0..1: the Studio gives
    the AI the scene with them drawn in, so it mostly paints them (renders kept 84-118%: eals_1, blin66_4,
    ancnt1; adding them again at static_glow showed every shaft and halo at ~180%). Fitted per colour channel over
    the light and a sample of the rest: render ~ colour transform of the original without them + kept x them.
    Cached in work/<field>/light_kept.json (a new render clears it: common.Field.CACHES)."""
    import json
    from ff7bg import ai
    cache = f.work / "light_kept.json"
    if cache.exists():
        return json.loads(cache.read_text())["kept"]
    sl = static_light(f)
    kept = 0.0
    if sl.max() >= 1:
        light = cv2.resize(sl, (f.w, f.h), interpolation=cv2.INTER_AREA)
        shown = ai.default_view(f.bg, f.meta.get("rendered_defaults", {}))[0][..., :3].astype(np.float32)
        without = np.clip(shown - light, 0, 255)
        r = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (f.w, f.h),
                       interpolation=cv2.INTER_AREA).astype(np.float32)
        near = light.mean(-1) > 3
        if near.sum() >= 50:
            idx = np.flatnonzero(near.ravel())
            rest = np.flatnonzero(~near.ravel())
            pick = np.random.default_rng(0).choice(rest, min(len(rest), 2 * len(idx) + 2000), replace=False)
            sel = np.concatenate([idx, pick])
            W, L, R = (a.reshape(-1, 3)[sel] for a in (without, light, r))
            ks = [np.linalg.lstsq(np.column_stack([W, L[:, j], np.ones(len(sel))]), R[:, j], rcond=None)[0][3]
                  for j in range(3)]
            kept = float(np.clip(np.median(ks), 0.0, 1.0))
    cache.write_text(json.dumps({"kept": round(kept, 3)}))
    return kept


KEPT_TRUST = 0.35  # light_added: a light the render shows at least this much of is the painting's: nothing added


PAINTED_LIFT = 10  # painted_out: a layer lifting the original's picture less than this over its surroundings (luma) can't tell


def painted_out(f, pal):
    """Has the AI painted out a static palette-animated glow layer (its tiles in `pal`, in the field file's colours,
    which the AI saw)? Both: the render shows under KEPT_TRUST of its light (fitted as light_added fits a light, over
    its pixels and a ring around them, on the channels it has) and lifts its place over that ring by under KEPT_TRUST
    of what the layer lifts the original there (luma, weighted by its light: a light the AI kept but recoloured, such
    as crcin_2's floor strips painted orange where the original's are pink-white, fits ~0 and still lifts). Its glow
    frames (painted=True) held only what it adds above its dimmest state, so 3D had none of the light the original
    shows: las4_3's big white radial glow (kept -0.10, lifts +13 where the original +90), jail1's green glow from the
    well, pillar_2's lamps."""
    from ff7bg import ai
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    transparent = f.bg.transparency[pal] if pal < len(f.bg.transparency) else True
    light = layer(f, pal, tuple(palettes.file_palettes(f.raw)[pal]), transparent)
    near = light.mean(-1) > 3
    if near.sum() < 50:
        return False
    key = (f.name, str(f.raw_render), f.raw_render.stat().st_mtime)
    if key not in _VIEWS:  # the original as the AI saw it, and the render, at 1x: once per field
        _VIEWS.clear()
        _VIEWS[key] = (ai.default_view(f.bg, f.meta.get("rendered_defaults", {}))[0][..., :3].astype(np.float32),
                       cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (f.w, f.h),
                                  interpolation=cv2.INTER_AREA).astype(np.float32))
    shown, r = _VIEWS[key]
    without = np.clip(shown - light, 0, 255)
    ring = (cv2.dilate(near.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & ~near
    W, L, R = without[near | ring], light[near | ring], r[near | ring]
    mean = light[near].mean(0)
    on = [j for j in range(3) if mean[j] >= 0.25 * mean.max()]
    ks = [np.linalg.lstsq(np.column_stack([W, L[:, j], np.ones(len(W))]), R[:, j], rcond=None)[0][3] for j in on]
    w = light[near] @ luma
    lift = lambda img: float(((img[near] @ luma) * w).sum() / w.sum() - (img[ring] @ luma).mean())
    original = lift(shown)
    return (float(np.average(ks, weights=mean[on])) < KEPT_TRUST and original >= PAINTED_LIFT
            and lift(r) < KEPT_TRUST * original)


_VIEWS = {}


PICTURE_SHARE = 0.5  # the_picture: a switched glow layer covering this much of the canvas is the picture itself
PICTURE_FIELDS = {"psdun_4"}  # ...and every switched glow layer the render holds in these fields, whatever it covers.
# psdun_4's sky light (16% of the canvas, param 1, on in the render, which holds 87-91% of its light) came out
# white with all of it added again (F25; AI 2D does the same). No general rule tells it from nivl_b1's flames (27%,
# whose states are different flames: with only what each adds they'd stand still again, F28) or zz4's crystal, so
# it's named (user, 2026-11-06: "fix psdun_4"). F25_3's PICTURE_SHARE 0.15 would have taken nivl_b1 too.


def the_picture(f, tiles, pal):
    """Is a switched glow layer (its `tiles`, in `pal`) the picture itself, which the render holds: its param on in the
    render, its glow tiles (every state of it) over PICTURE_SHARE of the canvas, and the render showing its light
    (not painted_out)? Then its frames carry only what it adds over its dimmest state (painted=True). crater_2's ice
    field is 1656 additive tiles in a param the render had on (70% of the canvas, 4 states, 34 palettes): with all of
    their light added over the painting that holds it (push_under at LIT_PUSH_CAP 8.5 brought them out, F19), the
    field went white (4:3 mean luma 164-167 where the original is 137-142; 130-140 with this), with pale tile-shaped
    blocks where surface_light's ratio stepped. A lamp's glow (zz4's crystal, 5%) still gets all its light."""
    on = {int(p): m for p, m in (f.meta.get("rendered_defaults") or {}).items()}
    params = frozenset(t.param for t in tiles if t.param and on.get(t.param, 0))
    if not params:
        return False
    if f.name in PICTURE_FIELDS:
        return not painted_out(f, pal)
    key = (f.name, params)
    if key not in _COVER:
        glowing = set(animated(f))
        cover = np.zeros((f.h, f.w), bool)
        cache = PageCache(f.bg)
        for t in f.bg.tiles:
            if t.param in params and t.blending and (t.tex, t.palette) in glowing:
                x, y = t.dst_x - f.mx, t.dst_y - f.my
                a = cache.tile(t)[..., 3][max(0, -y):f.h - y, max(0, -x):f.w - x] > 0
                cover[max(0, y):max(0, y) + a.shape[0], max(0, x):max(0, x) + a.shape[1]] |= a
        _COVER[key] = float(cover.mean())
    return _COVER[key] >= PICTURE_SHARE and not painted_out(f, pal)


_COVER = {}


def light_added(f, target):
    """The static light layers the scene's colour gets (render size, float RGB 0..255, or None): `target` less what
    the render shows of them (light_kept). Where the render shows them at all (light_kept at least KEPT_TRUST), only
    on the lights the AI left out: each light is fitted on its own (as light_kept, over its own pixels and a ring
    around them, on the channels it has, by their share of its light), and one the render shows at least KEPT_TRUST
    of is as the painting has it, as AI 2D shows it. The church's teal orb and pink floor glow, painted at 0.6 and
    0.4, got the field's 0.26 more on top, in the original's tile shapes: coloured patches AI 2D doesn't have."""
    from ff7bg import ai
    kept = light_kept(f)
    add = max(0.0, float(target) - kept)
    sl = static_light(f)
    if add <= 0 or sl.max() < 1:
        return None
    if kept < KEPT_TRUST:  # painted out on the whole (blin70_2's, cosmo's): all of them, as before
        return sl * add
    light = cv2.resize(sl, (f.w, f.h), interpolation=cv2.INTER_AREA)
    shown = ai.default_view(f.bg, f.meta.get("rendered_defaults", {}))[0][..., :3].astype(np.float32)
    without = np.clip(shown - light, 0, 255)
    r = cv2.resize(np.asarray(Image.open(f.render).convert("RGB")), (f.w, f.h),
                   interpolation=cv2.INTER_AREA).astype(np.float32)
    near = light.mean(-1) > 3
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(near.astype(np.uint8), np.ones((5, 5), np.uint8)), 8)
    keep = np.ones(n, np.float32)  # 1: added, 0: the painting's
    for k in range(1, n):
        m = (labels == k) & near
        if m.sum() < 20:
            continue
        ring = (cv2.dilate((labels == k).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & ~near
        sel = m | ring
        W, L, R = without[sel], light[sel], r[sel]
        mean = light[m].mean(0)
        on = [j for j in range(3) if mean[j] >= 0.25 * mean.max()]  # (a channel it hardly has fits anything)
        ks = [np.linalg.lstsq(np.column_stack([W, L[:, j], np.ones(len(W))]), R[:, j], rcond=None)[0][3] for j in on]
        if float(np.average(ks, weights=mean[on])) >= KEPT_TRUST:
            keep[k] = 0.0
    w = cv2.resize(keep[labels], (f.w * f.scale, f.h * f.scale), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((f.RH, f.RW), np.float32)
    y0, x0 = f.frame[1] * f.scale, f.frame[0] * f.scale
    out[y0:y0 + w.shape[0], x0:x0 + w.shape[1]] = w[: f.RH - y0, : f.RW - x0]
    return sl * (add * out)[..., None] if out.max() > 0 else None


def static_light(f):
    """The field's additive layers that don't animate (light shafts, haze, lamp halos) as light to add to the
    scene's colour, at render resolution (float RGB, 0..255), cached in work/<field>/static_light.npy. Hiding the
    pages loses them; the render mostly has them already (light_kept)."""
    cache = f.work / "static_light.npy"
    if cache.exists():
        return np.load(cache)
    animated_pals = {pal for (_, pal) in animated(f)}
    moving = scrolling_layers(f)  # drawn by the game, moving (overlay_plan): not baked in still
    keep = lambda t: t.z > 0 and not t.param and not scrolling(f, t, moving)
    pals = sorted({t.palette for t in f.bg.tiles if t.blending and t.type_trans in ADDITIVE and keep(t)} - animated_pals)
    canvas = np.zeros((f.h, f.w, 3), np.float32)
    file_pal = palettes.file_palettes(f.raw)
    for pal in pals:
        transparent = f.bg.transparency[pal] if pal < len(f.bg.transparency) else True
        quarter = all(t.type_trans == 3 for t in f.bg.tiles if t.palette == pal)
        canvas += layer(f, pal, tuple(file_pal[pal]), transparent, keep) *             (0.25 if quarter else 1.0)
    S = f.scale
    up = cv2.resize(canvas, (f.w * S, f.h * S), interpolation=cv2.INTER_CUBIC)
    up = cv2.GaussianBlur(np.clip(up, 0, None), (0, 0), S * 0.5)
    out = np.zeros((f.RH, f.RW, 3), np.float32)
    y0, x0 = f.frame[1] * S, f.frame[0] * S
    out[y0:y0 + up.shape[0], x0:x0 + up.shape[1]] = up[: f.RH - y0, : f.RW - x0]
    np.save(cache, out)
    return out


def grid_rect(f, rect, step):
    """Background-coordinate box (+ tile depth) -> (c0, r0, c1, r1, z) on the vertex grid."""
    x0, y0, x1, y1, z = rect
    to_g = lambda b, m, fr: int(round((b - m + fr) * f.scale / step))
    return (to_g(x0, f.mx, f.frame[0]), to_g(y0, f.my, f.frame[1]), to_g(x1, f.mx, f.frame[0]),
            to_g(y1, f.my, f.frame[1]), z)


def layer(f, pal, colours, transparent, keep=lambda t: True):
    """The palette's tiles in one state, assembled on the canvas (RGB float, 0 where transparent). Tiles on 16-bit
    pages are direct colour: a palette number on them means nothing."""
    out = np.zeros((f.h, f.w, 3), np.float32)
    pages = {}
    for t in f.bg.tiles:
        if t.palette != pal or not keep(t) or f.bg.pages[t.tex].depth >= 2:
            continue
        if t.tex not in pages:
            pages[t.tex] = palettes.page_bgra(f.bg, t.tex, colours, transparent).astype(np.float32)
        px = pages[t.tex][t.src_y:t.src_y + t.size, t.src_x:t.src_x + t.size]
        out[t.dst_y - f.my:t.dst_y - f.my + t.size, t.dst_x - f.mx:t.dst_x - f.mx + t.size] = \
            px[..., [2, 1, 0]] * (px[..., 3:] > 0)
    return out


def painted_canvas(f, scale):
    """The render cut to the canvas (frame margin removed) at `scale`."""
    r = np.asarray(Image.open(f.render).convert("RGB")).astype(np.float32)
    S = f.scale
    r = r[f.frame[1] * S:f.frame[1] * S + f.h * S, f.frame[0] * S:f.frame[0] * S + f.w * S]
    return cv2.resize(r, (f.w * scale, f.h * scale), interpolation=cv2.INTER_AREA)


RELIGHT_CORR = 0.6  # surface_light: a toggled light layer this like what's under it lights surfaces (not haze)
RELIGHT_MAX = 3.0   # the most a surface gets brighter (blin65_2 at 4: clipped orange)
RELIGHT_DARK = 16   # painting luma under this gets none of it, twice this gets all


def surface_light(f, ups, scale):
    """For a toggled light layer that brightens the original's own surfaces (its art is theirs, brighter: blin65_2's
    room lights match what's under them at 0.93, 1.7-4x as bright; a message board's text or haze doesn't): each
    frame as the painting lit the same way, {hash: painting x (light / the original under it)}, or None. Added as
    the original's art, its lit walls and floor came out as yellow tile-shaped blocks over the painting's own walls
    and over the black pit it has instead of the original's floor."""
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    rect = (f.mx, f.my, f.w, f.h)
    base = np.asarray(render_composite(f.bg, rect, PageCache(f.bg).tile, 1,
                                       lambda t: not t.param and not t.blending)).astype(np.float32)[..., :3] @ luma
    base = cv2.resize(base, (f.w * scale, f.h * scale), interpolation=cv2.INTER_CUBIC)
    peak = max(ups.values(), key=lambda a: float(a.mean())) @ luma
    on = peak > 4
    if on.sum() < 50 * scale * scale or float(np.corrcoef(peak[on], base[on])[0, 1]) < RELIGHT_CORR:
        return None
    paint = painted_canvas(f, scale)
    # near-black stays black (blin65_2's pit is painted 10-20 levels up: x3 it showed as an olive floor)
    paint = paint * np.clip((paint @ luma - RELIGHT_DARK) / RELIGHT_DARK, 0, 1)[..., None]
    under = np.maximum(cv2.GaussianBlur(base, (0, 0), 0.8 * scale), 8)
    return {hh: np.clip(paint * np.clip(cv2.GaussianBlur(up @ luma, (0, 0), 0.8 * scale) / under, 0, RELIGHT_MAX)[..., None],
                        0, 255) for hh, up in ups.items()}


def glow_frames(f, pal, by_hash, scale=2, gain=0.9, whiten=0.35, painted=True, keep=lambda t: True):
    """{hash: canvas RGB} light added over the painted mesh in each state: the state's glow above the darkest it
    gets, blurred more along the fall than across, shaped by the painting's own streaks, pushed toward white.
    painted=False (a layer the render doesn't hold: toggled off when it was made, e.g. Mt. Corel's haze): all of
    its light, softened a little; a layer that lights the original's surfaces lights the painting's instead
    (surface_light). keep: the tiles to assemble (one switched state of the layer: write_pages)."""
    transparent = f.bg.transparency[pal] if pal < len(f.bg.transparency) else True
    ups = {hh: cv2.resize(layer(f, pal, c, transparent, keep), (f.w * scale, f.h * scale), interpolation=cv2.INTER_CUBIC)
           for hh, c in by_hash.items()}
    if not painted:
        lit = surface_light(f, ups, scale)
        if lit is not None:
            return lit
        return {hh: np.clip(cv2.GaussianBlur(np.clip(up, 0, None), (0, 0), 0.8 * scale), 0, 255) for hh, up in ups.items()}
    floor = np.min(np.stack(list(ups.values())), 0)
    lum = painted_canvas(f, scale).mean(-1)
    streaks = np.clip(lum / np.maximum(cv2.GaussianBlur(lum, (0, 0), 4 * scale), 1), 0.55, 1.6)[..., None]
    out = {}
    for hh, up in ups.items():
        g = cv2.GaussianBlur(np.clip(up - floor, 0, None), (0, 0), sigmaX=1.4 * scale, sigmaY=2.6 * scale)
        g = g * streaks * gain
        out[hh] = np.clip(g * (1 - whiten) + g.mean(-1, keepdims=True) * whiten * 1.15, 0, 255)
    return out


def entry_states(f):
    """(in_render(tile): is this toggled tile on in the render, the field's animated params). The render holds the
    states on when it was made (the version's rendered_defaults; the field's entry states before those existed).
    Animated: the Studio's (its scripts.analyze reads each entity's script 0), other scripts' loops (script_loops),
    and params the render holds a state of that may not be on at entry (entry_unknown): the game switches all of
    them, so the mesh holds none of their states (what's beneath them, entry_off_art) and the game draws each, whole.
    (One the render holds nothing of keeps the render beneath, and its states are judged as switched tiles are.)"""
    job = json.loads((common.STUDIO / "ai" / "jobs.json").read_text())[f.name]
    on = {int(p): m for p, m in (f.meta.get("rendered_defaults") or job.get("defaults") or {}).items()}
    animated = {int(p) for p in job.get("animations", {})} | set(script_loops(f.raw))
    animated |= {p for p in entry_unknown(f.raw) if on.get(p, 0)}
    if EVENT_STATES != "0":
        ev = {p for p in event_switched(f.raw) if on.get(p, 0)} - animated
        animated |= ev if EVENT_STATES == "1" else event_states(f, ev, on)
    return (lambda t: bool(on.get(t.param, 0) & t.state)), animated


# Every state a script switches after entry (a door opened, a lamp put out) handled like those too, where that shows
# (event_states): the mesh holds what's beneath the render's state and the game draws that state as a 2D tile, so it
# goes when a script switches it off. Baked, it stayed in 3D wherever what replaces it doesn't reach: anfrst_4's
# flower beside the one that replaces it, colne_6's rug over the opened trapdoor, onna_52's and onna_4's doors that
# never opened, jtempl's temple. FF3D_EVENT_STATES: "auto" (default) the states event_states keeps (2509 tiles in 100 built fields,
# 2026-11-06); "1" every one (5629 tiles in 167: whole scenes became 2D tiles, bugin1a's room, trnad_1's forest); "0" none.
EVENT_STATES = os.environ.get("FF3D_EVENT_STATES", "auto")
EVENT_COVER = 0.25   # event_states: a state whose start-on tiles cover more of the canvas than this is the scene itself
EVENT_GAIN = 64      # ...the least the original changes where it goes off and nothing switched on with it covers (1x px)
EVENT_HOLE = 0.5     # ...the most of that the original shows as a hole (flat black or a placeholder colour)
EVENT_OFFART = 0.5   # ...the least of it where the Studio's off art differs from the render (> OVERLAY_SAME)
_EVENT = {}


def event_offs(raw, p, held):
    """For each field script that switches a `held` state of param p off after the always-run init (BGOFF of a held
    bit, BGCLR): the states of p it switches on with it (a mask, 0 for none); None for one that sets a state from a
    variable (anything may follow)."""
    out = []
    for s in palettes.scripts(raw):
        off, var, ons = False, False, 0
        for pos, op, b in s.ops():
            if op not in (BGON, BGOFF, BGCLR) or len(b) < 3 or b[1] >> 4 or b[2] != p or (s.index == 0 and pos < s.init_end):
                continue
            if op != BGCLR and len(b) >= 4 and b[1] & 0xF:
                var = True
            elif op == BGCLR or (op == BGOFF and len(b) >= 4 and b[3] < 8 and (1 << b[3]) & held):
                off = True
            elif op == BGON and len(b) >= 4 and b[3] < 8 and not (1 << b[3]) & held:
                ons |= 1 << b[3]
        if var or off:
            out.append(None if var else ons)
    return out


def _holes(img):
    """Flat black or flat saturated pixels (a placeholder the game never shows) of a 1x RGB picture."""
    x = img.astype(np.float32)
    luma = x @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mean = cv2.blur(luma, (5, 5))
    sd = np.sqrt(np.maximum(cv2.blur(luma * luma, (5, 5)) - mean * mean, 0))
    mx, mn = x.max(2), x.min(2)
    return (sd < 1.5) & ((mx < 10) | ((mx > 100) & (mx - mn > 0.6 * mx)))


def event_states(f, params, on):
    """Which of the event-switched `params` the render holds a state of the game draws as tiles that start on, the mesh
    holding the Studio's off art beneath them (entry_off_art), as entry_unknown's are. Only where that shows what the
    mesh can't (F26: in game, at entry, such a tile looks as AI 2D does there, without the 3D's light and gloss, and
    with AI 2D's tile seams: onna_4's door frames, onna_52's door):
    - its start-on tiles cover no more than EVENT_COVER of the canvas: one switch of the whole scene (bugin1a's room
      before the planetarium show, 70%; trnad_1's forest, 95%; zcoal_1's engine, 37%) made the scene a flat 2D tile;
    - a script switches the render's state off (event_offs), and the original changes at least EVENT_GAIN pixels there
      beyond what the states switched on with it cover: otherwise it's never off, changes nothing (md8brdg2's 148
      tiles, las0_1) or gives way to a state drawn whole over it already (swaps: eleout's lift, the boardroom's chairs,
      nvmkin1, move_f, junonr3);
    - the original doesn't show a hole there (EVENT_HOLE: what nothing ever uncovers in game);
    - the Studio's off art differs from the render on at least EVENT_OFFART of it: where the AI kept the render's
      state (kuro_7's caves, nivl, bonevil), the mesh would show it after the event just the same.
    Without the version's pages for every tile there's no off art: none. Cached per field, version and params."""
    key = (f.name, str(f.raw_render), f.raw_render.stat().st_mtime, frozenset(params), tuple(sorted(on.items())))
    if key in _EVENT:
        return _EVENT[key]
    kept = set()
    studio = studio_pages(f) if params else {}
    if studio and all(t.tex in studio for t in f.bg.tiles):
        cache, rect, S = PageCache(f.bg), (f.mx, f.my, f.w, f.h), f.scale
        shows = lambda s: (lambda t: not t.param or bool(s.get(t.param, 0) & t.state))
        orig = np.asarray(render_composite(f.bg, rect, cache.tile, 1, shows(on)))[..., :3].astype(np.int16)
        art = np.asarray(render_composite(f.bg, rect, page_tile_source(studio, S), S, include=lambda t: not t.param))
        art = art[..., :3].astype(np.float32)
        M = page_colours(f, studio)  # in the scene's colours, as entry_off_art paints it
        if M is not None:
            art = np.clip(np.round(np.concatenate([art.reshape(-1, 3), np.ones((art.shape[0] * art.shape[1], 1), np.float32)], 1) @ M),
                          0, 255).reshape(art.shape)
        R = scene_colours(f, Image.open(f.render).convert("RGB"))
        y0, x0 = f.frame[1] * S, f.frame[0] * S
        R = R[y0:y0 + f.h * S, x0:x0 + f.w * S]
        h, w = min(R.shape[0], art.shape[0]), min(R.shape[1], art.shape[1])
        moved = cv2.resize(np.abs(R[:h, :w] - art[:h, :w]).max(2), (f.w, f.h), interpolation=cv2.INTER_AREA) > OVERLAY_SAME

        def drawn(keep):
            m = np.zeros((f.h, f.w), bool)
            for t in f.bg.tiles:
                if not t.blending and keep(t):
                    x, y = t.dst_x - f.mx, t.dst_y - f.my
                    a = cache.tile(t)[..., 3][:f.h - y, :f.w - x] > 0
                    m[y:y + a.shape[0], x:x + a.shape[1]] |= a
            return m
        for p in sorted(params):
            held = on.get(p, 0)
            foot = drawn(lambda t: t.param == p and held & t.state)
            if not foot.any() or foot.mean() > EVENT_COVER:
                continue
            gone = np.asarray(render_composite(f.bg, rect, cache.tile, 1, shows({**on, p: 0})))[..., :3]
            change = (np.abs(orig - gone.astype(np.int16)).max(2) > OVERLAY_SAME) & foot
            gain = np.zeros((f.h, f.w), bool)
            for ons in event_offs(f.raw, p, held):
                gain |= change & ~drawn(lambda t: t.param == p and bool(ons) and bool(t.state & ons))
            if gain.sum() >= EVENT_GAIN and _holes(gone)[gain].mean() <= EVENT_HOLE and moved[gain].mean() >= EVENT_OFFART:
                kept.add(p)
    _EVENT[key] = kept
    return kept


def event_switched(raw):
    """{param} some script switches (bank 0, a constant param) other than in an init's part that always runs."""
    out = set()
    for s in palettes.scripts(raw):
        for pos, op, b in s.ops():
            if op in (BGON, BGOFF, BGCLR) and len(b) >= 3 and not b[1] >> 4 and (s.index != 0 or pos >= s.init_end):
                out.add(b[2])
    return out | entry_unknown(raw)


BGON, BGOFF, BGCLR, WAIT, JMPF, JMPFL, JMPB, JMPBL = 0xE0, 0xE1, 0xE4, 0x24, 0x10, 0x11, 0x12, 0x13  # field opcodes
ALL_STATES = [1 << i for i in range(8)]
# the IF opcodes (IFUB, IFUBL, IFSW, IFSWL, IFUW, IFUWL, IFKEY, IFKEYON, IFKEYOFF, IFPRTYQ, IFMEMBQ): their jump's
# size (bytes, at the end of the opcode); when the condition fails, the script goes on at the jump byte + the jump
IFS = {0x14: 1, 0x15: 2, 0x16: 1, 0x17: 2, 0x18: 1, 0x19: 2, 0x30: 1, 0x31: 1, 0x32: 1, 0xCB: 1, 0xCC: 1}


def entry_unknown(raw):
    """{param} whose states on entry depend on the save: an entity's init switches it only on a condition (inside an
    IF, or its ELSE: a story point, a chest opened, a door unlocked) or to a state from a variable. The Studio's
    entry states (scripts.analyze, as Makou Reactor) take every BGON in an init as done, so the render holds those
    states on, while the game shows them only when the condition holds: hideway2's chest open (lit orange) in 3D
    where the game has it closed, blin69_1's blood (after the escape) in a game saved before it. Baked into the mesh,
    such a state showed whatever the game had, and an overlay the game switched on over it was cut to where it
    differed from it (OVERLAY_SAME)."""
    out = set()
    for s in palettes.scripts(raw):
        if s.index != 0:
            continue
        ops = [(pos, op, b) for pos, op, b in s.ops() if pos < s.init_end]
        cond = []  # code ranges an init runs only on a condition
        for i, (pos, op, b) in enumerate(ops):
            if op in IFS and len(b) > IFS[op]:
                n = IFS[op]
                end = pos + len(b) - n + (b[-1] if n == 1 else struct.unpack_from("<H", b, len(b) - 2)[0])
                cond.append((pos + len(b), end))
                last = [(q, o, c) for q, o, c in ops if q < end][-1]
                if last[1] in (JMPF, JMPFL) and last[0] > pos:  # ...and its ELSE: the block jumps over it
                    q, o, c = last
                    cond.append((end, q + 1 + (c[1] if o == JMPF else struct.unpack_from("<H", c, 1)[0])))
        for pos, op, b in ops:
            if op in (BGON, BGOFF, BGCLR) and len(b) >= 3 and not b[1] >> 4:  # the param a constant
                if (op != BGCLR and len(b) >= 4 and b[1] & 0xF) or any(a <= pos < e for a, e in cond):
                    out.add(b[2])
    return out


def script_loops(raw):
    """{param: [state bits]} that a field script keeps switching in a loop that waits: an animation, in
    whichever script it runs. The Studio's scripts.analyze reads each entity's script 0 only, so it missed Junon's
    sea (jundoc1a/1b, junone7: the "wave"/"SEA" entity's script 1 cycles param 1 through 7 states), and the 3D took
    its frames for one-off switches shown only where the original changes (OVERLAY_SAME): blocky specks of the
    states' sunlit sea over the render's plain one. A loop: a backward jump (JMPB/JMPBL) over a WAIT and over two or
    more states of a param switched on, or one switched on and off (a blink), or a state taken from a variable (every
    state then: colne_6's spinning ball and rkt_w's fire count one up each turn, mrkt3's dancing dolls on the Honey
    Bee sign; the Studio reads bank 0 only, and the 3D showed them cut to where they differ from the render's state,
    which for the dolls was nowhere); a script waiting for an event before switching once isn't one."""
    from ff7bg import scripts
    (off,) = struct.unpack_from("<I", raw, 6)
    (length,) = struct.unpack_from("<I", raw, off)
    sec = raw[off + 4:off + 4 + length]
    entities = sec[2]
    (pos_texts, n_akao) = struct.unpack_from("<HH", sec, 4)
    pos_scripts = 32 + 8 * entities + 4 * n_akao
    end_all = min(struct.unpack_from("<I", sec, 32 + 8 * entities)[0] if n_akao else len(sec), pos_texts)
    table = [struct.unpack_from("<32H", sec, pos_scripts + 64 * e) for e in range(entities)]
    starts = sorted({p for row in table for p in row} | {end_all})
    out = {}
    for start in starts[:-1]:
        code = sec[start:next((p for p in starts if p > start), end_all)]
        ops = list(scripts._ops(code))
        for pos, op in ops:
            if op == JMPB and pos + 1 < len(code):
                target = pos - code[pos + 1]
            elif op == JMPBL and pos + 2 < len(code):
                target = pos - struct.unpack_from("<H", code, pos + 1)[0]
            else:
                continue
            body = [(q, o) for q, o in ops if target <= q < pos]
            if not any(o == WAIT for _, o in body):
                continue
            switched = {}
            for q, o in body:
                if o in (BGON, BGOFF) and q + 3 < len(code) and not code[q + 1] >> 4:
                    if code[q + 1] & 0xF:  # the state from a variable
                        if o == BGON:
                            switched.setdefault(code[q + 2], []).extend((BGON, b) for b in ALL_STATES)
                    elif code[q + 3] < 8:
                        switched.setdefault(code[q + 2], []).append((o, 1 << code[q + 3]))
            for p, events in switched.items():
                ons = [b for o, b in events if o == BGON]
                if len(set(ons)) >= 2 or set(ons) & {b for o, b in events if o == BGOFF}:
                    states = out.setdefault(p, [])
                    states += [b for b in ons if b not in states]
    return out


def swaps(raw):
    """{param: {(on, off)}}: state bits a script switches on (on) while it switches another off (off; BGCLR: all of
    them), bank 0: a door opening (rkt_w: BGOFF 2, BGON 4), a gate turned (ghotel). The state switched off isn't
    there to hide the one switched on, though both are in the original's tiles: with the render's (closed) state
    baked in the mesh, the overlay of the other was cut to what the closed one left of it (rkt_w's four doors: 0 to
    7% of the open ones; nvmkin1, junonr3, southmk2, move_f: all of theirs), so the door stayed shut in 3D."""
    out = {}
    for s in palettes.scripts(raw):
        ons, offs = {}, {}
        for pos, op, b in s.ops():
            if op in (BGON, BGOFF, BGCLR) and len(b) >= 3 and not b[1] >> 4:
                if op == BGCLR:
                    offs.setdefault(b[2], set()).update(ALL_STATES)
                elif len(b) >= 4 and not b[1] & 0xF and b[3] < 8:
                    (ons if op == BGON else offs).setdefault(b[2], set()).add(1 << b[3])
        for p, bits in ons.items():
            out.setdefault(p, set()).update((a, c) for a in bits for c in offs.get(p, ()) if c != a)
    return {p: v for p, v in out.items() if v}


OVERLAY_SAME = 24  # a switched tile's pixel this close to what's under it is the same picture: a layer drawn with its
                   # own palette repeats the sky around it a few levels off, as speckle (del1's ship: 65% of its
                   # area differs at all, 47% by more than 16, and that share holds to 48)
REPAINT_CUT = 0.2  # a state's repaint is shown whole when no more than this share of its spot's edge (where the
                   # original repeats the scene) steps off the scene by more than OVERLAY_SAME (repaint_seams): blin63_t's
                   # lit panels 0 to 0.11, del1's ship 0.77; cut blocks at 0.27 and up (convil_1's square by the well
                   # 0.34, ithill, ghotel, anfrst_4, bugin2, gaiin_3, clsin2_3)
REPAINT_EDGE = 32  # ... and only with this much of such edge to judge by (background px); with less, by its whole
                   # outer edge: shown whole when that is this long and no more than REPAINT_CUT of it steps off
                   # (seamless): tunnel_6's opened shutter, its art cut out inside its tiles (no edge to judge), dark on
                   # dark (8-16 levels: only 7% of it passes OVERLAY_SAME), came out as scattered blocks of the lit tunnel


def seamless(g, scale):
    """Is a repaint_seams spot shown whole? By the edge where the original repeats the scene (REPAINT_CUT), with
    REPAINT_EDGE of it to judge by; else by its whole outer edge: where its art stops short of its tiles' edges
    (transparent there) or changes the picture right up to them, the repaint can't meet the scene with a seam the
    original hasn't got, and what OVERLAY_SAME leaves of it is only scattered pieces where the state is low in
    contrast (tunnel_6's opened shutter, its tunnel dark on the shutter's dark: 17% of it shown, in blocks). A state
    over the whole picture (another view: junin7, junele2) meets the scene nowhere: whole."""
    if g["samples"] >= REPAINT_EDGE * scale:
        return g["cut"] <= REPAINT_CUT
    if g["area"] >= 0.99:
        return True
    return g["edge"] >= REPAINT_EDGE and g["samples"] * g["cut"] <= REPAINT_CUT * g["edge"] * scale


def _shown(f, solid, cache):
    """The original as the mesh shows it (`solid`: the solid tiles it holds), 1x RGB."""
    shown = np.zeros((f.h, f.w, 3), np.uint8)
    for t in draw_order(solid):
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        tile = cache.tile(t)[:f.h - y, :f.w - x]
        on = tile[..., 3] > 0
        shown[y:y + s, x:x + s][on] = tile[..., :3][on]
    return shown


def repaint_seams(f, tiles, shown=None):
    """How each state's repaint meets the scene: the solid switched `tiles` (off in the render) grouped by state into
    spots (tiles touching), and for each, the seam the version's pages (in the scene's colours) make along the spot's
    outer tile edges, where the original's tile repeats what's around it (within OVERLAY_SAME; an object's own outline
    there is no seam): the page's edge row against the render's row just outside, less the render's own step there.
    [{"ids": {id(tile)}, "param", "state", "x", "y", "tiles", "samples" (edge px x scale), "seam": their median,
    "cut": the share over OVERLAY_SAME, "edge": the spot's whole outer edge (background px), "area": its share of
    the canvas}]; [] without pages.

    The Studio's states pass repaints all a state changes in the original, however little (speckle included), and
    blends it into the scene at the edges (Poisson). Where that worked, the repaint is a thing of its own inside the
    spot: blin63_t's panels, which the original only lights a few levels (4-24) and the AI painted as lit "1"/"2"
    panels and hazard stripes. Where it didn't, it runs to the tile edges and stops there, cut square: del1's ship
    layer is whole tiles with sky between its cranes, its repaint changed that sky (77% of its edge meets the
    scene's sky more than OVERLAY_SAME off, median 27 and 45 levels in its two versions; blin63_t's 0-11%), and in
    3D it showed as pale blocks."""
    studio = studio_pages(f)
    tiles = [t for t in tiles if t.tex in studio]
    if not tiles:
        return []
    cache = PageCache(f.bg)
    if shown is None:
        in_render, _ = entry_states(f)
        shown = _shown(f, [t for t in f.bg.tiles if not t.blending and (not t.param or in_render(t))], cache)
    M = page_colours(f, studio)
    R = scene_colours(f, Image.open(f.render).convert("RGB"))
    S = f.scale
    occ = {}
    for t in tiles:
        g = occ.setdefault((t.param, t.state), np.zeros((f.h, f.w), np.uint8))
        g[max(0, t.dst_y - f.my):t.dst_y - f.my + t.size, max(0, t.dst_x - f.mx):t.dst_x - f.mx + t.size] = 1
    samples, edge = {}, {}
    for t in tiles:
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        edge[id(t)] = 0
        if x < 0 or y < 0 or x + s > f.w or y + s > f.h:
            samples[id(t)] = np.zeros(0, np.float32)
            continue
        tile = cache.tile(t)
        mag = np.abs(tile[..., :3].astype(np.int16) - shown[y:y + s, x:x + s]).max(2)
        repeats = (mag <= OVERLAY_SAME) & (tile[..., 3] > 0)
        sz = s * S
        blk = studio[t.tex][t.src_y * S:t.src_y * S + sz, t.src_x * S:t.src_x * S + sz]
        if blk.shape[:2] != (sz, sz):
            samples[id(t)] = np.zeros(0, np.float32)
            continue
        rgb = blk[..., :3].astype(np.float32)
        if M is not None:
            rgb = (np.concatenate([rgb.reshape(-1, 3), np.ones((sz * sz, 1), np.float32)], 1) @ M).reshape(sz, sz, 3)
        Y, X = (y + f.frame[1]) * S, (x + f.frame[0]) * S
        g = occ[(t.param, t.state)]
        got = []
        for side in "LRTB":  # (inner page line, render just outside, render just inside, the original's edge line)
            if side == "L" and x > 0 and not g[y:y + s, x - 1].any():
                line = rgb[:, 0], R[Y:Y + sz, X - 1], R[Y:Y + sz, X], repeats[:, 0]
            elif side == "R" and x + s < f.w and not g[y:y + s, x + s].any():
                line = rgb[:, -1], R[Y:Y + sz, X + sz], R[Y:Y + sz, X + sz - 1], repeats[:, -1]
            elif side == "T" and y > 0 and not g[y - 1, x:x + s].any():
                line = rgb[0], R[Y - 1, X:X + sz], R[Y, X:X + sz], repeats[0]
            elif side == "B" and y + s < f.h and not g[y + s, x:x + s].any():
                line = rgb[-1], R[Y + sz, X:X + sz], R[Y + sz - 1, X:X + sz], repeats[-1]
            else:
                continue
            inner, outer, ref, ok = line
            edge[id(t)] += s
            step = np.abs(inner - outer).max(1) - np.abs(ref - outer).max(1)
            got.append(np.maximum(step, 0)[np.repeat(ok, S)])
        samples[id(t)] = np.concatenate(got) if got else np.zeros(0, np.float32)
    out = []
    for (p, st), g in sorted(occ.items()):
        n, lab = cv2.connectedComponents(g, connectivity=8)
        for k in range(1, n):
            mine = [t for t in tiles if (t.param, t.state) == (p, st)
                    and lab[min(f.h - 1, max(0, t.dst_y - f.my)), min(f.w - 1, max(0, t.dst_x - f.mx))] == k]
            if not mine:
                continue
            al = np.concatenate([samples[id(t)] for t in mine])
            out.append({"ids": {id(t) for t in mine}, "param": p, "state": st, "tiles": len(mine), "samples": len(al),
                        "x": min(t.dst_x for t in mine) - f.mx, "y": min(t.dst_y for t in mine) - f.my,
                        "seam": float(np.median(al)) if len(al) else -1.0,
                        "cut": float((al > OVERLAY_SAME).mean()) if len(al) else 1.0,
                        "edge": sum(edge[id(t)] for t in mine), "area": float((lab == k).mean())})
    return out


# The far backdrop (solid layer-2 tiles: Mt. Corel's rocks and clouds, the sea under the Highwind, the view from the
# Ferris wheel) is drawn by the game, as in 2D, where it moves apart from the scene; the scene leaves it out.
# Needs FFNx-3d to draw those tiles depth-tested behind the scene (deferred.cpp draw_scene_overlays; before that it
# drew every solid field tile over the scene, which would cover all of it): set FF3D_GAME_BACKDROP=0 to keep the
# backdrop baked into the scene as before.
GAME_BACKDROP = os.environ.get("FF3D_GAME_BACKDROP", "1") != "0"
# ...and a layer 3 a script set among the scene (drawn_layers): needs FFNx-3d to draw layer 3's tiles depth-tested
# behind the scene too (backdropTileZ[1]; before, it took them over the scene). Off: such a field keeps both layers
# baked (its backdrop alone, drawn by the game, showed through where layer 3 belongs: crater_1)
GAME_LAYER3 = os.environ.get("FF3D_GAME_LAYER3", "1") != "0"
BACKDROP_MOVE = 2.0  # background px: a backdrop that gets no further than this from its canvas place stays baked


def parallax(f):
    """The field's parallax layers (triggers section): {"range": the camera's centre range (x0, x1, y0, y1),
    2: (width, height, x, y, x speed, y speed) of layer 2's scroll (the game's bg3), 3: the same of layer 3}."""
    t = common._sections(f.raw)[7]
    left, top, right, bottom = struct.unpack_from("<4h", t, 12)
    w3, h3, w4, h4, p3x, p3y, p4x, p4y, s3x, s3y, s4x, s4y = struct.unpack_from("<12h", t, 0x18)
    x0, y0 = left + 160, top + 120  # as FFNx clips the camera (field_clip_with_camera_range_float)
    return {"range": (x0, max(x0, right - 160), y0, max(y0, bottom - 120)),
            2: (w3, h3, p3x, p3y, s3x, s3y), 3: (w4, h4, p4x, p4y, s4x, s4y)}


def layer_reach(f, layer=2):
    """How far (background px, (x, y)) the layer gets from its canvas place, relative to the scene, as the camera
    goes over its range: the game draws layers 2-3 at bg3_pos/16 + bg3_speed x camera / 256, wrapped at the layer's
    size (FFNx background.cpp set_world_and_background_positions), the scene at the camera."""
    p = parallax(f)
    x0, x1, y0, y1 = p["range"]
    w, h, px, py, sx, sy = p[layer]
    dx = [x - math.remainder(px / 16 + sx * x / 256, w or 1024) for x in np.linspace(x0, x1, 33)]
    dy = [y - math.remainder(py / 16 + sy * y / 256, h or 1024) for y in np.linspace(y0, y1, 33)]
    return float(np.abs(dx).max()), float(np.abs(dy).max())


def backdrop_motion(f, layer=2):
    """layer_reach, the larger way. Mt. Corel's trestles: 180 (mtcrl_5, a quarter of the camera's speed) and 304
    (mtcrl_4); the Highwind's sky, fixed to the screen: 136."""
    return max(layer_reach(f, layer))


BGPDH = 0x2C  # field opcode: set a background layer's depth (banks, layer, depth)


def layer_depths(f, layer, entry=False):
    """The depths field scripts set the layer to (BGPDH: banks, layer, depth), a set; None for one from a variable.
    The game draws layer 2 at the back (z 0.9998) unless one does (junonl2: 433, among the scene's tiles), and layer 3
    at the depth one sets (crater_1's crater wall: 3840; hill's Highwind: 512). entry: only those an entity's init
    sets (the depth on entry; trnad_3's wind sets its mist's 4080 later, in its main loop)."""
    return {None if b[1] else int.from_bytes(bytes(b[3:5]), "little")
            for s in palettes.scripts(f.raw) for pos, op, b in s.ops()
            if op == BGPDH and len(b) >= 5 and b[2] == layer and (not entry or (s.index == 0 and pos < s.init_end))}


def depth_set(f, layer):
    """Does a field script set the layer's depth (BGPDH)?"""
    return bool(layer_depths(f, layer))


def layer3_depth(f):
    """The one depth a script sets layer 3 to on entry (a tile depth: x TILE_DEPTH camera units), or None (none, or
    others set later: the depth then isn't known)."""
    d = layer_depths(f, 3)
    if d != layer_depths(f, 3, entry=True):
        return None
    return next(iter(d)) if len(d) == 1 and None not in d else None


def drawn_layers(f):
    """The parallax layers the game draws in the 3D view, the scene leaving them out (backdrop_alpha): a set, of 2
    and 3. A copy at the canvas place slid off the scene as the camera moved (mtcrl_4's sky where 2D has its rocks,
    crater_1's starry sky where 2D has the crater wall), left the canvas's empty parts black where 2D has the layer
    (mtcrl_5, mds5_4, crater_1), and stood still where 2D animates it. One that never moves stays baked.
    - 2, the far backdrop (solid tiles): where it moves apart from the scene (backdrop_motion over BACKDROP_MOVE, or a
      script scrolls it: BGSCR) or a script switches or animates it (the Highwind's sky); not where a script sets its
      depth among the scene's (FFNx-3d takes the tiles at z 0.9998 for the backdrop).
    - 3 (solid tiles), set by a script to one depth among the scene's (BGPDH; FFNx-3d draws it at that depth, in
      front of layer 2): where the camera moves it apart from the scene (crater_1's wall, at an eighth of the
      camera's speed; hill's Highwind, fixed to the screen). The scene's depth goes in front of or behind it as the
      original's tiles are in 2D (build.parallax_order)."""
    if not GAME_BACKDROP or f.variant:
        return set()
    out = set()
    two = [t for t in f.bg.tiles if t.layer == 2 and not t.blending]
    if two and not depth_set(f, 2) and (any(t.param for t in two) or 2 in scroll_speeds(f)
                                        or backdrop_motion(f, 2) > BACKDROP_MOVE):
        out.add(2)
    three = [t for t in f.bg.tiles if t.layer == 3 and not t.blending]
    # no script setting its depth at all: the game draws it at 1, in front of everything (onna_5's keyhole, fixed to
    # the screen: baked at its canvas place, the room showed in a strip beside it and the characters in front; F23)
    if three and (layer3_depth(f) is not None or not depth_set(f, 3)) and backdrop_motion(f, 3) > BACKDROP_MOVE:
        if not GAME_LAYER3:  # the backdrop alone showed through where layer 3 belongs (crater_1's sky over its wall)
            return set()
        out.add(3)
    return out


def backdrop_by_game(f):
    """Does the game draw the field's backdrop (layer 2) in the 3D view (drawn_layers)?"""
    return 2 in drawn_layers(f)


def moves_apart(f, t):
    """Is the tile on a parallax layer (2 or 3) the camera moves apart from the scene (layer_reach over BACKDROP_MOVE)?
    The game draws it at its parallax place, which the painting, made at the canvas place, can't hold: anfrst_1's
    green shaft, 560 px of reach, was in the render at its canvas place, and its glow frames (painted=True) held only
    its flicker, so 3D showed no shaft where the game draws it (AI 2D draws the original's, F4)."""
    if t.layer not in (2, 3):
        return False
    key = (f.name, t.layer)
    if key not in _REACH:
        _REACH[key] = backdrop_motion(f, t.layer)
    return _REACH[key] > BACKDROP_MOVE


_REACH = {}


def _in_scene(f, in_render, animated, drawn):
    """The solid tiles the scene holds: not on a layer the game draws (drawn), static or on in the render and not
    animated (an overlay the game switches has the backdrop beneath it in 2D when it's off)."""
    return lambda t: (not t.blending and t.layer not in drawn and
                      (not t.param or (in_render(t) and t.param not in animated)))


def scene_cover(f):
    """The original's cover of what the scene holds (_in_scene), 1x (0..1): where it's 0 the original shows a layer
    the game draws (drawn_layers), or nothing at all. Static blended tiles count too where nothing solid lies beneath
    them on the canvas (not even the backdrop): the Studio has the AI's painting drawn there by the blended tile itself
    (ai.build_pages glow_tile), so AI 2D shows it, over the backdrop the game scrolls under it. trnad_2's pale sky is
    such additive haze over empty canvas: cut out, it left the backdrop's deep blue streaks alone there. (A glass over
    the backdrop, blin66_2's window, has the backdrop beneath: AI 2D shows that without the glass, and so does 3D.)"""
    in_render, animated = entry_states(f)
    drawn = drawn_layers(f)
    keep = _in_scene(f, in_render, animated, drawn)
    moving = scrolling_layers(f)
    cache = PageCache(f.bg)
    cov = np.zeros((f.h, f.w), np.float32)
    solid = np.zeros((f.h, f.w), bool)  # any solid tile, the backdrop's too
    glaze = np.zeros((f.h, f.w), np.float32)  # static blended tiles of the scene's layers
    for t in f.bg.tiles:
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        a = cache.tile(t)[..., 3][:f.h - y, :f.w - x].astype(np.float32) / 255
        if keep(t):
            cov[y:y + s, x:x + s] = np.maximum(cov[y:y + s, x:x + s], a)
        if not t.blending and (not t.param or in_render(t)):
            solid[y:y + s, x:x + s] |= a > 0
        elif t.blending and not t.param and t.layer not in drawn and not scrolling(f, t, moving):
            glaze[y:y + s, x:x + s] = np.maximum(glaze[y:y + s, x:x + s], a)
    return np.maximum(cov, glaze * ~solid)


_ALPHA = {}


def backdrop_alpha(f):
    """Where the scene is (1) and where it leaves the backdrop to the game (0): the original's cover of the scene's
    tiles (scene_cover), upscaled smoothly to the render (a 1-px beam or gap survives; diagonal edges don't come out
    as the tiles' stairs), at render size (float32, 0 or 1), or None when the parallax layers stay baked
    (drawn_layers). The scene's texture takes it as alpha and FFNx-3d skips what's transparent, so the game's tiles of
    those layers show there, at their parallax place, wherever nothing of the scene is in front."""
    key = (f.name, str(f.raw_render), f.raw_render.stat().st_mtime)
    if key not in _ALPHA:
        _ALPHA.clear()
        a = None
        if drawn_layers(f):
            S = f.scale
            up = cv2.resize(scene_cover(f), (f.w * S, f.h * S), interpolation=cv2.INTER_LINEAR)
            up = (cv2.GaussianBlur(up, (0, 0), 0.5 * S) >= 0.5).astype(np.float32)
            a = np.ones((f.RH, f.RW), np.float32)
            y0, x0 = f.frame[1] * S, f.frame[0] * S
            a[y0:y0 + up.shape[0], x0:x0 + up.shape[1]] = up[: f.RH - y0, : f.RW - x0]
            if a.min() >= 1:  # covered everywhere (fship_3): nothing to leave out
                a = None
        _ALPHA[key] = a
    return _ALPHA[key]


def scene_alpha(f):
    """backdrop_alpha for the look step: only once the build has given the game the backdrop's pages (a look applied
    to a scene built before would leave holes with nothing behind them: rebuild it)."""
    a = backdrop_alpha(f)
    if a is None:
        return None
    drawn = drawn_layers(f)
    need = {(t.tex, page_palette(f, t)) for t in f.bg.tiles if t.layer in drawn and not t.blending}
    have = all((common.PAGES / f.name / f"{f.name}_{p:02d}_{q:02d}.png").exists() for p, q in need)
    return a if have else None


def overlay_plan(f, glowing):
    """What the game keeps drawing over the 3D scene, as in 2D, since the mesh holds the picture as it was rendered:
    every toggled effect (smoke, haze, light shafts: blended tiles a script switches or animates), and every toggled
    tile the render doesn't show or that animates (frames, things that appear later). Palette-animated tiles are
    glow frames instead (`glowing`: their (page, palette) pairs).

    Blended layers a script scrolls are write_scrolling's.

    {"tiles": [...], "vis": {id(tile): how much of it the original shows over the static scene (1x, by the Studio's
    back-to-front order)}, "entry_on": overlay tiles also in the render (animations that start on: the mesh needs
    what's beneath them), "lit": the toggled glow layers' tiles (in `glowing`, switched by a script: their frames
    hold all their light, glow_frames painted=False), which the game draws too, at their tile depth (their "vis" too),
    "still": the static glow layers' tiles (their "vis" too, for over_scene),
    "baked": one frame of a backdrop animation the render lacks, painted into the scene instead (entry_off_art),
    "backdrop": the tiles of the parallax layers the game draws (drawn_layers), every state of them, "painted": its
    static layer-3 tiles, whose art the render holds (write_overlays)}

    Solid tiles on the backdrop layer (2) are never overlays: the game draws that layer fixed to the screen, not
    scrolled with the field, so over the scene, cut to what covers it at the canvas's own place, it slid off as soon
    as the camera moved (fship_22/24: the Highwind's sky through the bridge's window, 80 of its 480 frame tiles on in
    the render, was drawn over the dome, the railing and the machinery, with their cut-outs as black arcs over the
    sky). Where it moves (drawn_layers), the game draws all of it, behind the scene, which leaves it out
    (backdrop_alpha), and a layer 3 a script set among the scene with it; elsewhere the scene keeps the render's state of it (still: no clouds drifting by), and one the
    render lacks gets its animation's first frame painted in.

    A solid tile a script switches on (not animated, off in the render) shows whole, as in 2D, where its state's
    repaint meets the scene without a seam (repaint_seams, REPAINT_CUT): the Studio repaints all a state changes, and
    blin63_t's lit "1"/"2" panels are where the original changes only 4-24 levels. Elsewhere it shows only where it
    changes what the original shows without it (OVERLAY_SAME), and the scene shows the rest, at its true depth:
    del1's ship layer is whole tiles, sky between its cranes included, its repaint changed that sky, and shown whole
    it made pale blocks over the 3D sky."""
    in_render, animated = entry_states(f)
    moving = scrolling_layers(f)
    by_game = backdrop_alpha(f) is not None
    drawn = drawn_layers(f) if by_game else set()

    def backdrop(t):
        return t.layer == 2 and not t.blending

    def drawn_back(t):  # the game draws its layer, whole: its effects too (glow frames and scrolling aside)
        return t.layer in drawn and (t.tex, t.palette) not in glowing and not scrolling(f, t, moving)

    def is_overlay(t):
        if not t.param or (t.tex, t.palette) in glowing or scrolling(f, t, moving) or backdrop(t) or drawn_back(t):
            return False
        return bool(t.blending) or not in_render(t) or t.param in animated
    tiles = [t for t in f.bg.tiles if is_overlay(t)]
    order = json.loads((common.STUDIO / "ai" / "jobs.json").read_text())[f.name].get("animations", {})
    baked = []
    for p in sorted({t.param for t in f.bg.tiles if backdrop(t) and t.param in animated and 2 not in drawn}):
        mine_p = [t for t in f.bg.tiles if backdrop(t) and t.param == p]
        if any(in_render(t) for t in mine_p):
            continue
        frames = [s for s in order.get(str(p), {}).get("order", []) if s] or sorted({t.state for t in mine_p})
        baked += [t for t in mine_p if t.state == frames[0]]
    lit = [t for t in f.bg.tiles if t.param and t.blending and (t.tex, t.palette) in glowing and not scrolling(f, t, moving)]
    # the static glow layers' tiles: their 2D visibility too, for when the game draws them over the scene (over_scene)
    still = [t for t in f.bg.tiles if not t.param and (t.tex, t.palette) in glowing and not scrolling(f, t, moving)]
    # what's painted in the mesh and hides what's drawn before it: solid tiles, static or on in the render
    solid = [t for t in f.bg.tiles if not t.blending and not is_overlay(t) and (not t.param or in_render(t))]
    cache, mine = PageCache(f.bg), {id(t) for t in tiles + lit + still + baked}
    # the original as the mesh shows it, 1x; where animations that start on are, the mesh has their off art instead
    # (entry_off_art), so a switched tile there always counts as a change
    shown = _shown(f, solid, cache)
    switched = [t for t in tiles if not t.blending and not in_render(t) and t.param not in animated]
    whole = set().union(*[g["ids"] for g in repaint_seams(f, switched, shown) if seamless(g, f.scale)])
    unknown = np.zeros((f.h, f.w), bool)
    for t in tiles:
        if not t.blending and in_render(t):
            unknown[t.dst_y - f.my:t.dst_y - f.my + t.size, t.dst_x - f.mx:t.dst_x - f.mx + t.size] = True
    # where the scene leaves the backdrop to the game, what's under a switched tile is the backdrop wherever it has
    # moved to, not its canvas copy: the tile shows whole there, as in 2D
    held = scene_cover(f) >= 0.5 if by_game else np.ones((f.h, f.w), bool)
    # a state in the mesh that a script switches off as it switches another on (a door's closed state, as it opens)
    # hides nothing of that one: its cover is kept apart, per state. Solid ones only: a blended one shown there
    # would have the scene pushed back behind it (build.push_under), and trnad_1's blackout layer (738 tiles behind
    # the whole picture, the render's state of it in front) would have taken all of the scene's depth with it
    swap = swaps(f.raw)
    keyed = {id(t): (t.param, t.state) for t in solid if t.param and t.param in swap}
    cover = np.zeros((f.h, f.w), np.float32)
    apart = {k: np.zeros((f.h, f.w), np.float32) for k in set(keyed.values())}
    vis = {}
    for t in reversed(draw_order(tiles + lit + still + baked + solid)):  # front to back: cover = what's drawn after this tile
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        tile = cache.tile(t)
        alpha = tile[..., 3].astype(np.float32) / 255
        if id(t) in mine:
            off = {(t.param, b) for a, b in swap.get(t.param, ()) if a == t.state and not t.blending}
            cov = cover[y:y + s, x:x + s]
            for k, c in apart.items():
                if k not in off:
                    cov = np.maximum(cov, c[y:y + s, x:x + s])
            v = (1 - cov) * (alpha > 0)[:f.h - y, :f.w - x]
            if not t.blending and not in_render(t) and t.param not in animated and id(t) not in whole:
                diff = np.abs(tile[:f.h - y, :f.w - x, :3].astype(np.int16) - shown[y:y + s, x:x + s]).max(2)
                same = (diff <= OVERLAY_SAME) & ~unknown[y:y + s, x:x + s] & held[y:y + s, x:x + s]
                change = cv2.dilate((~same).astype(np.uint8), np.ones((3, 3), np.uint8))  # grown 1px, as the Studio's
                v = v * change
            vis[id(t)] = v
        else:
            c = apart[keyed[id(t)]] if id(t) in keyed else cover
            c[y:y + s, x:x + s] = np.maximum(c[y:y + s, x:x + s], alpha[:f.h - y, :f.w - x])
    back = [t for t in f.bg.tiles if drawn_back(t)]
    for t in back:  # all of each, every state: the game switches them, and the scene in front hides them
        vis[id(t)] = np.ones((t.size, t.size), np.float32)
    return {"tiles": tiles, "vis": vis, "entry_on": [t for t in tiles if not t.blending and in_render(t)], "lit": lit,
            "still": still, "baked": baked, "backdrop": back,
            "painted": [t for t in back if t.layer == 3 and not t.param and not t.blending]}


OVER_SCENE_HIDDEN = 0.2  # over_scene: a light layer the scene hides this share of (where the original shows it) goes over it
OVER_SCENE_CAPPED = 0.005  # ...a switched one (plan "lit") this share: the push brought out all of it but the tiles past
                           # LIT_PUSH_CAP, and cut its light along their edges (psdun_4's sky: 770 of its 876 tiles at
                           # 6-8.5 times the scene's depth, the rest further, pale tile steps through it; F25)
OVER_SCENE_SOFT = 0.5    # ...its light cut to where the original shows it, with an edge this soft (background px)


def over_scene(f, anim, plan, Z, step, depth):
    """The blended light layers the game draws at their tiles' depth that the 3D scene still hides where the original
    shows them: {(page, palette): the share of the layer's light hidden}, those at OVER_SCENE_HIDDEN or more (a switched
    glow layer's, plan "lit", at OVER_SCENE_CAPPED: what the push leaves of one is cut along its tiles). Z: the
    mesh's camera depth on the vertex grid (step); depth(t): a tile's camera depth (the scene nearer than that hides
    it; with push_under's 8% margin counted too, eals_1's pond glow came out 26% hidden where the game shows it all).
    The glow layers' (switched, plan "lit", and static, "still") and the additive overlays (plan "tiles").

    The original sorts by tile depth only, and a light layer's tiles are often as deep as the backdrop's: blin65_2's
    room lights at z 4087, 11 to 16 times the depth of the room they light (estimated from the painting); subin_1a's
    console lamps, 43 times; blin60_1's moving light, a static layer, behind walls nearer than push_back's foreground.
    build.push_under pushes the scene back behind a switched layer's tiles only up to LIT_PUSH_CAP (any further folds it
    up), so these stayed hidden in 3D and their animation with them. FFNx-3d draws the listed ones over the scene
    (lighting config glow_over_scene: page, palette pairs), not depth tested, before the characters; their pages and
    frames hold their light only where the original shows it (over_vis). A layer the camera moves apart from its
    canvas place (moves_apart) isn't listed: its 2D visibility is for the canvas place. Nor is a switched layer that
    is the picture itself (the_picture: the mesh holds its light, its frames only the shimmer): crater_2's ice field
    is 38% hidden in places on its build, and it's as the original with that."""
    g = f.scale / step
    groups = {}
    for (page, pal), by_hash in anim["glowing"].items():
        tiles = [t for t in f.bg.tiles if t.tex == page and t.palette == pal and id(t) in plan["vis"]]
        if not tiles or any(moves_apart(f, t) for t in tiles):
            continue
        whole = [t for t in f.bg.tiles if t.tex == page and t.palette == pal]
        if any(t.param for t in whole) and the_picture(f, whole, pal):
            continue
        tr = f.bg.transparency[pal] if pal < len(f.bg.transparency) else True
        light = np.max([layer(f, pal, c, tr, lambda t: t.tex == page).mean(-1) for c in by_hash.values()], 0)
        groups[(page, pal)] = [(t, light[t.dst_y - f.my:t.dst_y - f.my + t.size, t.dst_x - f.mx:t.dst_x - f.mx + t.size])
                               for t in tiles]
    cache = PageCache(f.bg)
    for t in plan["tiles"]:
        if t.blending and t.type_trans in ADDITIVE and not moves_apart(f, t):
            tile = cache.tile(t).astype(np.float32)
            groups.setdefault((t.tex, page_palette(f, t)), []).append((t, tile[..., :3].mean(-1) * (tile[..., 3] > 0)))
    out = {}
    lit = {id(t) for t in plan.get("lit", [])}
    for key, tiles in groups.items():
        shown = hidden = 0.0
        least = OVER_SCENE_CAPPED if all(id(t) in lit for t, _ in tiles) else OVER_SCENE_HIDDEN
        for t, light in tiles:
            n = int(round(t.size * g))
            r0, c0 = int(round((t.dst_y - f.my + f.frame[1]) * g)), int(round((t.dst_x - f.mx + f.frame[0]) * g))
            block = Z[max(0, r0):r0 + n, max(0, c0):c0 + n]
            if depth(t) <= 0 or not block.size or not light.size:
                continue
            seen = cv2.resize(plan["vis"][id(t)].astype(np.float32), (n, n))[:block.shape[0], :block.shape[1]] > 0.5
            light = cv2.resize(light, (n, n))[:block.shape[0], :block.shape[1]]
            shown += float(light[seen].sum())
            hidden += float(light[seen & (block < depth(t))].sum())
        if shown > 0 and hidden / shown >= least:
            out[key] = round(hidden / shown, 3)
    return out


def over_vis(f, plan, page, pal, scale):
    """Where the original shows a light layer drawn over the scene (over_scene): its tiles' 2D visibility on the
    canvas at `scale`, softened (OVER_SCENE_SOFT), float 0..1."""
    v = np.zeros((f.h, f.w), np.float32)
    for t in f.bg.tiles:
        if t.tex != page or page_palette(f, t) != pal or id(t) not in plan["vis"]:
            continue
        x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
        tv = plan["vis"][id(t)].astype(np.float32)[:f.h - y, :f.w - x]
        v[y:y + tv.shape[0], x:x + tv.shape[1]] = np.maximum(v[y:y + tv.shape[0], x:x + tv.shape[1]], tv)
    up = cv2.resize(v, (f.w * scale, f.h * scale), interpolation=cv2.INTER_LINEAR)
    return np.clip(cv2.GaussianBlur(up, (0, 0), OVER_SCENE_SOFT * scale), 0, 1)


def page_palette(f, t):
    """The palette number in the name of the override page FFNx draws a tile with (<field>_<page>_<palette>.png): the
    tile's, but 0 on a 16-bit (direct colour) page, which has no palettes. sky's backdrop is on two (27, 28): written
    as _27_07, the game never used it (it fell back to the blank _27_00) and the scene's cut-out stayed black."""
    return t.palette if f.bg.pages[t.tex].depth < 2 else 0


def studio_pages(f):
    """The version's own pages {page: RGBA at the render's scale} (restyled states, original effects), if built."""
    d = f.raw_render.parent / "pages" / "field" / f.name
    pages = {}
    for p in sorted(d.glob(f"{f.name}_*_00.png")) if d.exists() else []:
        pages[int(p.stem[len(f.name) + 1:].split("_")[0])] = np.asarray(Image.open(p).convert("RGBA"))
    return pages


_PASS = {}


def studio_pass(f):
    """The Studio's colour pass on this version: (N (4, 3), k), or None. Its build pulls the render's colours toward
    the original art's palette (keep_colors, the user's 0.8: one 3x4 fitted render -> original for the whole picture,
    ff7bg.ai.keep_colors) before it cuts the pages AI 2D shows: pages = render x (1 - k) + (render @ N[:3] + N[3]) x k.
    Refitted here as the Studio fits it (the render at 1x against the original as rendered), which gives the pages to
    the level on the static tiles (datiao_1, gaia_32, las3_1, cosin1, ancnt4, farm: 0.00 mean). k is what the
    version's build applied (meta result.keep_colors; none before the setting existed)."""
    key = (f.name, str(f.raw_render), f.raw_render.stat().st_mtime)
    if key not in _PASS:
        _PASS.clear()
        k = float(((f.meta or {}).get("result") or {}).get("keep_colors") or 0.0)
        N = None
        if k > 0 and not f.variant:  # a stage-2 variant has no pages (regen.py)
            from ff7bg import ai
            orig = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), PageCache(f.bg).tile, 1,
                                               ai.shows(f.meta.get("rendered_defaults", {}))))
            hi = np.asarray(Image.open(f.render).convert("RGB"))
            hi = hi[f.frame[1] * f.scale:, f.frame[0] * f.scale:][:f.h * f.scale, :f.w * f.scale]
            N = ai.fit_color(cv2.resize(hi, (f.w, f.h), interpolation=cv2.INTER_AREA), orig[..., :3])
        _PASS[key] = (N, k) if N is not None else None
    return _PASS[key]


def scene_colours(f, rgb):
    """An image at the render's scale (RGB, 0..255) in the colours AI 2D shows: the Studio's colour pass
    (studio_pass), which its pages have and the render doesn't. The 3D scene used the render's own until 2026-11-02:
    MOSS's colours, which the pass had pulled back (datiao's statues olive with acid-yellow ridges where AI 2D has
    pink-grey stone, the crater's rocks with orange patches where it's blue-grey, Gaea's Cliff's snow cyan and its
    cliff deep blue, sector 8 green), under the look's saturation on top. Float32."""
    rgb = np.asarray(rgb, np.float32)
    p = studio_pass(f)
    if p is None:
        return rgb
    N, k = p
    return np.clip(rgb * (1 - k) + (rgb @ N[:3].astype(np.float32) + N[3].astype(np.float32)) * k, 0, 255)


PAGE_FIT_LEFT = 0.4  # page_colours: the share of the pages' difference from the render a fit may leave and be used
PAGE_FIT_KEEP = 80   # page_colours: fitted again on the best this many percent, as the Studio fits its colour pass


def page_colours(f, studio):
    """How the scene's colours follow from the version's pages' (a 3x4 on [r, g, b, 1], or None): the Studio cuts
    its pages after a colour pass (keep_colors: one 3x4 for the whole picture), so they're off the render the scene
    shows (del1: +4 red, +2 green; elm and cos_top: about 6 levels either way; corel1 at keep 0.8: 6, up to 30).
    Fitted on the static solid tiles, where both show the same picture, trimmed to the best PAGE_FIT_KEEP percent:
    some static tiles hold other art (corel1's beneath its smoke, which is on in the render: the Studio's "all off"
    repaint), and an untrimmed fit there mixed corel1's channels wildly and was dropped, so its smoke frames' sky
    came out as pale tile blocks beside the column. The trimmed fit leaves 0.3 to 0.8 levels on the median pixel.
    Used only when it explains the difference where it fits (under PAGE_FIT_LEFT of it left). Since the scene takes
    the pass too (scene_colours), this is None wherever the pass is refitted exactly, and catches what's left."""
    scene = scene_colours(f, Image.open(f.render).convert("RGB"))
    s = f.scale
    src, dst = [], []
    for t in f.bg.tiles:
        if t.param or t.blending or t.tex not in studio:
            continue
        sz = t.size * s
        blk = studio[t.tex][t.src_y * s:t.src_y * s + sz, t.src_x * s:t.src_x * s + sz]
        y, x = (t.dst_y - f.my + f.frame[1]) * s, (t.dst_x - f.mx + f.frame[0]) * s
        sc = scene[y:y + sz, x:x + sz]
        if blk.shape[:2] != (sz, sz) or sc.shape[:2] != (sz, sz):
            continue
        whole = blk[..., 3] == 255
        src.append(blk[..., :3][whole][::7].astype(np.float32))
        dst.append(sc[whole][::7])
    if sum(len(a) for a in src) < 5000:
        return None
    A, B = np.concatenate(src), np.concatenate(dst)
    X = np.hstack([A, np.ones((len(A), 1), np.float32)])
    keep = np.ones(len(A), bool)
    for _ in range(4):
        M = np.linalg.lstsq(X[keep], B[keep], rcond=None)[0]
        err = np.abs(X @ M - B).max(1)
        keep = err <= np.percentile(err, PAGE_FIT_KEEP)
    before, after = np.abs(A - B)[keep].mean(), np.abs(X @ M - B)[keep].mean()  # on what it fits
    return M if np.abs(A - B).mean() > 1.0 and after < PAGE_FIT_LEFT * before else None


def write_overlays(f, plan, out):
    """Each overlay tile in its own page and palette (`<field>_<page>_<palette>.png`, which FFNx prefers to the
    blank _00): the version's art where it has pages, else the original's, showing only where the original does.
    Solid tiles from the version's pages take the scene's colours (page_colours), so where they meet the scene the
    sky doesn't change shade at a tile's edge. With them the backdrop's tiles when the game draws it (plan
    "backdrop"): what AI 2D shows (the Studio keeps the original's art, upscaled, for layers 2-3), as opaque as the
    original's tile (the Studio upscales a tile with what's around it on the canvas, so where the layer ends its
    edge came out part transparent: a dark seam where a wrapping layer meets itself, fship_1's sea). A static layer-3
    tile the game draws (plan "painted") takes the render's own painting of it instead, in the scene's colours: the
    Studio's composite has layer 3 in front of everything, so the AI painted all of it, and its upscaled original
    made hill's Highwind blurred and stepped where the scene had it painted.

    A palette the field's scripts animate (not a glow layer's: those are glow frames) gets the version's frames as
    well (`<field>_<page>_<palette>_<hash>.png`, one per palette state, which FFNx picks by the palette it draws
    with), cut the same way: with only the still page, trnad_2's sky stayed in the file palette's deep blue where
    AI 2D, through the frames, shows the pale sky the scripts set."""
    studio = studio_pages(f)
    cache = PageCache(f.bg)  # the original's tiles, any colour depth (16-bit pages have no palette)
    M = page_colours(f, studio) if studio else None
    back = {id(t) for t in plan.get("backdrop", [])}
    painted = {id(t) for t in plan.get("painted", [])}
    tiles = plan["tiles"] + plan.get("backdrop", [])
    moving = set(animated(f)) if tiles else set()  # palette-animated (page, palette) pairs
    scene, shape = None, None
    if painted:  # the render in the scene's colours, and the layer's outline smooth on the canvas (not the tiles' steps)
        S = f.scale
        scene = scene_colours(f, Image.open(f.render).convert("RGB"))
        cov = np.zeros((f.h, f.w), np.float32)
        for t in plan["painted"]:
            x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
            cov[y:y + s, x:x + s] = np.maximum(cov[y:y + s, x:x + s], cache.tile(t)[..., 3][:f.h - y, :f.w - x] / 255)
        up = cv2.GaussianBlur(cv2.resize(cov, (f.w * S, f.h * S), interpolation=cv2.INTER_LINEAR), (0, 0), 0.5 * S)
        shape = np.clip((up - 0.5) * 4 + 0.5, 0, 1)  # an edge about half a background pixel wide

    def from_page(t, page, s, sz):
        block = page[t.src_y * s:t.src_y * s + sz, t.src_x * s:t.src_x * s + sz]
        if M is not None and not t.blending and block.shape[:2] == (sz, sz):
            rgb = np.concatenate([block[..., :3].reshape(-1, 3).astype(np.float32), np.ones((sz * sz, 1), np.float32)], 1)
            block = np.dstack([np.clip(np.round(rgb @ M), 0, 255).astype(np.uint8).reshape(sz, sz, 3), block[..., 3]])
        if id(t) in back and not t.blending and block.shape[:2] == (sz, sz):
            block = np.dstack([block[..., :3], tile_alpha(cache.tile(t), s)])
        return block

    over = plan.get("over") or {}

    def put(img, t, block, s, sz):
        v = cv2.resize(plan["vis"][id(t)], (sz, sz), interpolation=cv2.INTER_LINEAR)
        dst = img[t.src_y * s:t.src_y * s + sz, t.src_x * s:t.src_x * s + sz]
        alpha = np.round(block[..., 3] * np.clip(v, 0, 1)).astype(np.uint8)
        rgb = block[..., :3]
        if t.blending and (t.tex, page_palette(f, t)) in over:  # added over the scene: only where the original shows it
            rgb = np.round(rgb * np.clip(v, 0, 1)[..., None]).astype(np.uint8)
        dst[..., :3] = np.where((alpha >= dst[..., 3])[..., None], rgb, dst[..., :3])
        dst[..., 3] = np.maximum(dst[..., 3], alpha)

    pages, framed = {}, {}
    for t in tiles:
        s = f.scale if t.tex in studio else 1
        sz = t.size * s
        animate = (t.tex, t.palette) in moving and t.tex in studio and f.bg.pages[t.tex].depth < 2
        if id(t) in painted and t.tex in studio and not animate:
            y, x = (t.dst_y - f.my + f.frame[1]) * s, (t.dst_x - f.mx + f.frame[0]) * s
            rgb = np.clip(np.round(scene[y:y + sz, x:x + sz]), 0, 255).astype(np.uint8)
            block = np.dstack([rgb, np.round(shape[y:y + sz, x:x + sz] * 255).astype(np.uint8)])
        elif t.tex in studio:
            block = from_page(t, studio[t.tex], s, sz)
        else:
            block = cache.tile(t)
        if block.shape[:2] != (sz, sz):
            continue
        put(pages.setdefault((t.tex, page_palette(f, t)), np.zeros((256 * s, 256 * s, 4), np.uint8)), t, block, s, sz)
        if animate:
            framed.setdefault((t.tex, t.palette), []).append(t)
    for (page, pal), img in pages.items():
        Image.fromarray(img, "RGBA").save(out / f"{f.name}_{page:02d}_{pal:02d}.png")
    src = f.raw_render.parent / "pages" / "field" / f.name
    frames = 0
    for (page, pal), mine in framed.items():
        s = f.scale
        for fp in sorted(src.glob(f"{f.name}_{page:02d}_{pal:02d}_*.png")):
            art = np.asarray(Image.open(fp).convert("RGBA"))
            if art.shape[0] != 256 * s:
                continue
            img = np.zeros((256 * s, 256 * s, 4), np.uint8)
            for t in mine:
                sz = t.size * s
                block = from_page(t, art, s, sz)
                if block.shape[:2] == (sz, sz):
                    put(img, t, block, s, sz)
            Image.fromarray(img, "RGBA").save(out / fp.name)
            frames += 1
    return len(pages) + frames


def write_scrolling(f, show=True, grain=True):
    """The blended layers the field's scripts scroll (blizzard, mist, waves), as pages the game draws and moves over
    the scene as in 2D: the original's art redrawn at full resolution (scrolling_art), not the Studio's (it paints
    the picture, not what moves over it). grain=False: only its soft shapes (the blizzard's gusts of white, under
    the particles' snow); show=False: none. Returns how many pages. Seen on the next field entry."""
    out = common.PAGES / f.name
    speeds = scroll_speeds(f)
    tiles = [t for t in f.bg.tiles if scrolling(f, t, speeds)]
    s, pages = f.scale, {}
    for page, pal in {(t.tex, page_palette(f, t)) for t in tiles}:
        path = out / f"{f.name}_{page:02d}_{pal:02d}.png"
        if pal:
            path.unlink(missing_ok=True)
            continue
        # a _00 page is every other palette's fallback (the blank that hides them) and all of a 16-bit page's tiles
        # (backdrop, overlays): only these tiles' places in it are written again, emptied first
        img = np.asarray(Image.open(path).convert("RGBA")).copy() if path.exists() else None
        if img is None or img.shape[0] != 256 * s:
            img = np.zeros((256 * s, 256 * s, 4), np.uint8)
        for t in tiles:
            if (t.tex, page_palette(f, t)) == (page, pal):
                img[t.src_y * s:t.src_y * s + t.size * s, t.src_x * s:t.src_x * s + t.size * s] = 0
        pages[(page, pal)] = img
    if not show or not tiles:
        out.mkdir(parents=True, exist_ok=True)
        for (page, pal), img in pages.items():
            Image.fromarray(img, "RGBA").save(out / f"{f.name}_{page:02d}_{pal:02d}.png")
        return 0
    for lay in sorted({t.layer for t in tiles}):
        mine = [t for t in tiles if t.layer == lay]
        # states a script shows one at a time at one place (junonr2/junonl2's chimney smoke: 8 frames on top of each
        # other) are each a picture of their own: drawn into one, the last frame's art (near empty) was every frame's
        spots = {}
        for t in mine:
            spots.setdefault((t.dst_x, t.dst_y), set()).add((t.param, t.state if t.param else 0))
        stacked = any(len(v) > 1 for v in spots.values())
        groups = {}
        for t in mine:
            groups.setdefault((t.param, t.state if t.param else 0) if stacked else 0, []).append(t)
        for group in groups.values():
            art, (x0, y0) = scrolling_art(f, group, s, speeds[lay], SCROLL_GRAIN if grain else 0.0)
            for t in group:
                sz, ax, ay = t.size * s, (t.dst_x - x0) * s, (t.dst_y - y0) * s
                img = pages.setdefault((t.tex, page_palette(f, t)), np.zeros((256 * s, 256 * s, 4), np.uint8))
                img[t.src_y * s:t.src_y * s + sz, t.src_x * s:t.src_x * s + sz] = art[ay:ay + sz, ax:ax + sz]
    out.mkdir(parents=True, exist_ok=True)
    for (page, pal), img in pages.items():
        Image.fromarray(img, "RGBA").save(out / f"{f.name}_{page:02d}_{pal:02d}.png")
    return len(pages)


_GLASS = {}


def through_glass(f):
    """Where AI 2D shows the far backdrop (a layer 2/3 tile: the Studio keeps the original's art there, not the AI's)
    through a static averaging layer that its pages leave out (the Studio's build_pages takes a static blended tile
    over solid ones as painted into the AI image, which isn't used for a backdrop): what AI 2D shows there, from the
    version's pages in the scene's colours, (RGB uint8 at render size, weight 0..1), or None. The render has the
    averaging layer painted in: blin66_2's nebula, seen through the boardroom's glass, came out muted and streaked in
    3D where AI 2D shows it vivid. (In the whole game only blin66_2 and blin66_6.)"""
    key = (f.name, str(f.raw_render), f.raw_render.stat().st_mtime)
    if key not in _GLASS:
        _GLASS.clear()
        _GLASS[key] = _through_glass(f)
    return _GLASS[key]


def _through_glass(f):
    if scene_alpha(f) is not None:  # the game draws the backdrop there, the scene leaves it out
        return None
    static = [t for t in f.bg.tiles if not t.param]
    glass = [t for t in static if t.blending and t.type_trans == 0]
    if not glass or not any(t.layer >= 2 and not t.blending for t in static):
        return None
    cache = PageCache(f.bg)

    def drawn(t):
        x, y = t.dst_x - f.mx, t.dst_y - f.my
        return (slice(y, y + t.size), slice(x, x + t.size)), cache.tile(t)[..., 3][:f.h - y, :f.w - x] > 0
    top = np.full((f.h, f.w), -1, np.int16)  # the layer of the front static solid tile
    for t in draw_order([t for t in static if not t.blending]):
        at, a = drawn(t)
        top[at][a] = t.layer
    over = np.zeros((f.h, f.w), bool)
    for t in glass:
        at, a = drawn(t)
        over[at] |= a
    m = (top >= 2) & over
    studio = studio_pages(f)
    if m.sum() < 64 or not studio or not all(t.tex in studio for t in static):
        return None
    S = f.scale
    view = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), page_tile_source(studio, S), S,
                                       include=lambda t: not t.param))
    M = page_colours(f, studio)
    if M is not None:
        rgb = np.concatenate([view.reshape(-1, 3).astype(np.float32), np.ones((view.shape[0] * view.shape[1], 1), np.float32)], 1)
        view = np.clip(np.round(rgb @ M), 0, 255).astype(np.uint8).reshape(view.shape)
    w = cv2.GaussianBlur(cv2.resize(m.astype(np.float32), (f.w * S, f.h * S), interpolation=cv2.INTER_NEAREST), (0, 0), 0.5 * S)
    rgb, weight = np.zeros((f.RH, f.RW, 3), np.uint8), np.zeros((f.RH, f.RW), np.float32)
    y0, x0 = f.frame[1] * S, f.frame[0] * S
    rgb[y0:y0 + view.shape[0], x0:x0 + view.shape[1]] = view[: f.RH - y0, : f.RW - x0]
    weight[y0:y0 + w.shape[0], x0:x0 + w.shape[1]] = w[: f.RH - y0, : f.RW - x0]
    return rgb, weight


def entry_off_art(f, plan):
    """Where tiles also in the render are drawn by the game instead (animations that start on), the scene beneath
    them from the version's pages, which the Studio paints for when they switch off; where a backdrop frame is baked
    in (plan["baked"]), that frame from the pages, where the original shows it: (RGB uint8, weight) at render size,
    or None."""
    studio = studio_pages(f)
    baked = {id(t) for t in plan.get("baked", [])}
    if not (plan["entry_on"] or baked) or not studio or not all(t.tex in studio for t in f.bg.tiles):
        return None
    S = f.scale
    view = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), page_tile_source(studio, S), S,
                                       include=lambda t: not t.param or id(t) in baked))
    M = page_colours(f, studio)  # in the scene's colours, like the overlays drawn over it
    if M is not None:
        rgb = np.concatenate([view.reshape(-1, 3).astype(np.float32), np.ones((view.shape[0] * view.shape[1], 1), np.float32)], 1)
        view = np.clip(np.round(rgb @ M), 0, 255).astype(np.uint8).reshape(view.shape)
    weight = np.zeros((f.h * S, f.w * S), np.float32)
    cache = PageCache(f.bg)
    for t in plan["entry_on"] + plan.get("baked", []):
        x, y, sz = (t.dst_x - f.mx) * S, (t.dst_y - f.my) * S, t.size * S
        a = tile_alpha(cache.tile(t), S) / 255
        if id(t) in baked:  # only where the original shows it
            a = a * cv2.resize(plan["vis"][id(t)].astype(np.float32), (sz, sz), interpolation=cv2.INTER_LINEAR)[:a.shape[0], :a.shape[1]]
        a = a[:weight.shape[0] - y, :weight.shape[1] - x]
        weight[y:y + a.shape[0], x:x + a.shape[1]] = np.maximum(weight[y:y + a.shape[0], x:x + a.shape[1]], a)
    return view, weight


FRAME_MAX = 1024  # a field's distinct glow frames (per layer, summed) at most: each is a 1 MB texture in game (512x512)
# and FFNx-3d keeps 1536 cached (ffnx3d_texcache, F24); past this, frames that look alike share one image
MERGE_TOLS = (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 48, 64, 96, 128, 255)  # levels, tried in order


def frame_signature(img):
    """A glow frame's colours at 1/8 size (uint8): what merge_frames compares."""
    rgb = img[..., :3].astype(np.float32)
    h, w = rgb.shape[:2]
    return np.round(rgb.reshape(h // 8, 8, w // 8, 8, 3).mean((1, 3))).astype(np.uint8)


def merge_frames(layers, sigs, limit=FRAME_MAX):
    """({layer: {digest: the digest drawn instead}}, tolerance) for {layer: {hash: digest}} and {digest: signature}:
    within a layer, frames no more than `tol` levels apart anywhere (at 1/8 size) share the first one's image, at the
    smallest tolerance of MERGE_TOLS that brings the field to `limit` distinct frames; none merged (tol 0) when it
    has no more already (2026-11-06: bugin3 had 13,029 distinct frames, ancnt2 1,043, crater_2 601)."""
    def merged(tol):
        out = {}
        for key, hs in layers.items():
            ds = sorted(set(hs.values()), key=lambda d: float(sigs[d].mean()))
            rep = out[key] = {}
            if tol <= 0:
                rep.update((d, d) for d in ds)
                continue
            stack = np.empty((len(ds),) + sigs[ds[0]].shape, np.int16)
            means, ids = [], []
            for d in ds:
                s = sigs[d].astype(np.int16)
                m = float(s.mean())
                # the mean differs no more than the largest difference: only representatives this close can match
                lo = int(np.searchsorted(np.asarray(means), m - tol)) if means else 0
                hit = None
                if lo < len(ids):
                    diff = np.abs(stack[lo:len(ids)] - s).reshape(len(ids) - lo, -1).max(1)
                    ok = np.nonzero(diff <= tol)[0]
                    if ok.size:
                        hit = ids[lo + int(ok[0])]
                if hit is None:
                    stack[len(ids)] = s
                    means.append(m)
                    ids.append(d)
                    hit = d
                rep[d] = hit
        return out

    def count(out):
        return sum(len(set(r.values())) for r in out.values())

    plan = merged(0)
    if count(plan) <= limit:
        return plan, 0
    # the smallest that fits, halving the range (the low tolerances are the slow ones: bugin3 took 280 s trying
    # them in turn); the last, 255, leaves one image per layer
    lo, hi, best = -1, len(MERGE_TOLS) - 1, None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        plan = merged(MERGE_TOLS[mid])
        if count(plan) <= limit:
            hi, best = mid, plan
        else:
            lo = mid
    return (best if best is not None else merged(MERGE_TOLS[hi])), MERGE_TOLS[hi]


def write_pages(f, anim, plan, scale=2):
    """Hide every page (transparent _00, which every palette falls back to), except the overlays (the game keeps
    drawing and switching those over the scene, as in 2D), and write the animated glow frames."""
    out = common.PAGES / f.name
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    blank = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    for page in sorted({t.tex for t in f.bg.tiles}):
        blank.save(out / f"{f.name}_{page:02d}_00.png")
    write_overlays(f, plan, out)
    in_render, _ = entry_states(f)
    # F24: frames are made first and written after, within FRAME_MAX distinct images (merge_frames); identical files
    # share one texture in game (ffnx3d_texcache)
    frames, images, sigs = {}, {}, {}
    for (page, pal), by_hash in anim["glowing"].items():
        tiles = [t for t in f.bg.tiles if t.palette == pal and t.tex == page]
        # only static layers (waterfalls, lamps) are trusted to be in the painting; toggled ones (haze, smoke) the
        # game draws in full over it, as the 2D game does, since the AI often paints them out, and so are layers the
        # camera moves apart from the scene (moves_apart: the painting has them at the canvas place)
        painted = all(not t.param and not moves_apart(f, t) for t in tiles)
        if painted and painted_out(f, pal):  # the AI painted it out: all of its light, as a toggled one's
            painted = False
        elif not painted and the_picture(f, tiles, pal):  # the render holds it, all over: only what it adds
            painted = True
        # drawn over the scene (over_scene): its light only where the original shows it
        seen = over_vis(f, plan, page, pal, scale)[..., None] if (page, pal) in (plan.get("over") or {}) else 1.0
        # each switched state gets its own art: the game switches the states' tiles, which often share their places.
        # Assembled on one canvas, every state's tiles there took the same art, so the game's switching showed
        # nothing: nivl_b1's flames, 4 states at the same spots, stood still in 3D (only the palette flickered; F28)
        groups = {}
        for t in tiles:
            groups.setdefault((t.param, t.state) if t.param else None, []).append(t)
        if len(groups) > 1:
            parts = [(group, glow_frames(f, pal, by_hash, scale, painted=painted,
                                         keep=(lambda t, k=key: ((t.param, t.state) if t.param else None) == k)))
                     for key, group in groups.items()]
        else:
            parts = [(tiles, glow_frames(f, pal, by_hash, scale, painted=painted))]
        for hh in parts[0][1]:
            img = np.zeros((256 * scale, 256 * scale, 4), np.uint8)
            for group, adds in parts:
                add = adds[hh] * seen
                for t in group:
                    sx, sy, sz = t.src_x * scale, t.src_y * scale, t.size * scale
                    dx, dy = (t.dst_x - f.mx) * scale, (t.dst_y - f.my) * scale
                    img[sy:sy + sz, sx:sx + sz, :3] = add[dy:dy + sz, dx:dx + sz]
                    img[sy:sy + sz, sx:sx + sz, 3] = 255
            digest = hashlib.blake2b(img.tobytes(), digest_size=16).digest()
            if digest not in images:
                buf = io.BytesIO()
                Image.fromarray(img, "RGBA").save(buf, "PNG")
                images[digest] = buf.getvalue()
                sigs[digest] = frame_signature(img)
            frames.setdefault((page, pal), {})[hh] = digest
    merged, tol = merge_frames(frames, sigs) if frames else ({}, 0)
    n = 0
    for (page, pal), by_hash in frames.items():
        for hh, digest in by_hash.items():
            # the hash as FFNx prints it (%llx): no leading zeros, or FFNx never finds the frame
            (out / f"{f.name}_{page:02d}_{pal:02d}_{hh:x}.png").write_bytes(images[merged[(page, pal)][digest]])
            n += 1
    if frames:
        kept = sum(len(set(r.values())) for r in merged.values())
        print(f"glow frames: {n} ({len(images)} distinct, {kept} kept" + (f", alike within {tol} merged)" if tol else ")"),
              flush=True)
    return n
