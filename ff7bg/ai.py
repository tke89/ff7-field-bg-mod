"""Batch pipeline for AI-restyled backgrounds.

prep:     every field's default view -> ai/input/<map>.png (+ ai/jobs.json)
          (identical backgrounds shared by several maps are sent once)
assemble: ai/output/<map>.png -> align to original geometry -> FFNx pages for the map and its copies

What comes from the AI image: static scenery in layers 0-1 (75% of all tiles), the static blend
effects baked into it, and the states the game shows on entry. Toggled states (doors, lights,
animation frames) come from the states pass (ff7bg.states) when it has run; otherwise, like
parallax layers 2-3, they keep the original art, upscaled and color-matched to the new image.
"""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import palettes
from .align import drift_stats, find_drift, fit_global, residuals, warp_global
from .field import parse_background
from .scripts import analyze
from .sheets import (
    PageCache, assign_sheets, canvas_rect, draw_order, is_base, page_tile_source, render_composite,
    sheet_canvases, swizzle, tile_alpha, upscale_rgba, write_pages,
)

ASPECTS = {"1:1": 1.0, "3:2": 1.5, "2:3": 2 / 3, "4:3": 4 / 3, "3:4": 0.75, "16:9": 16 / 9, "9:16": 9 / 16}


def background_key(raw_field):
    """Hash of the palette + background sections: equal keys render identically."""
    count = int.from_bytes(raw_field[2:6], "little")
    offs = [int.from_bytes(raw_field[6 + 4 * i : 10 + 4 * i], "little") for i in range(count)]
    h = hashlib.sha1()
    for sec in (3, 8):
        start = offs[sec]
        end = offs[sec + 1] if sec + 1 < count else len(raw_field)
        h.update(raw_field[start:end])
    return h.hexdigest()


def shows(defaults):
    """Tile filter for the view at field entry: static tiles plus the states switched on."""
    defaults = {int(p): m for p, m in (defaults or {}).items()}
    return lambda t: not t.param or bool(defaults.get(t.param, 0) & t.state)


def default_view(bg, defaults=None):
    rect = canvas_rect(bg)
    return np.asarray(render_composite(bg, rect, PageCache(bg).tile, 1, shows(defaults))), rect


def _pad(img, left, top, right, bottom):
    """Extend the image with a blurred mirror of its edges (reads as out-of-focus scenery)."""
    h, w = img.shape[:2]
    padded = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_REFLECT)
    H, W = padded.shape[:2]
    blurred = cv2.GaussianBlur(padded, (0, 0), max(W, H) / 60)
    mask = np.zeros((H, W), np.float32)
    mask[top : top + h, left : left + w] = 1
    return (padded * mask[..., None] + blurred * (1 - mask[..., None])).astype(np.uint8)


def pad_for_generator(img, margin=0.0, aspects=None):
    """Optional safety margin (generators tend to zoom in and crop edges), then pad to the closest
    allowed aspect ratio (for generators with fixed output sizes). Returns (image, [l, t, r, b])."""
    h, w = img.shape[:2]
    mx, my = round(w * margin), round(h * margin)
    W, H = w + 2 * mx, h + 2 * my
    if aspects:
        a = W / H
        target = min(aspects, key=lambda t: abs(np.log(t / a)))
        if target > a:
            W = round(H * target)
        else:
            H = round(W / target)
    left, top = (W - w) // 2, (H - h) // 2
    pad = [left, top, W - w - left, H - h - top]
    return (_pad(img, *pad) if any(pad) else img), pad


def prep(fields, out_dir, aspects=None, prescale=1, margin=0.0):
    """fields: iterable of (name, decompressed field bytes)."""
    out_dir = Path(out_dir)
    (out_dir / "input").mkdir(parents=True, exist_ok=True)
    jobs = {}
    by_key = {}
    for name, raw in fields:
        key = background_key(raw)
        if key in by_key:
            jobs[by_key[key]]["copies"].append(name.lower())
            continue
        bg = parse_background(name.lower(), raw)
        defaults, animations = analyze(raw)
        img, rect = default_view(bg, defaults)
        img, pad = pad_for_generator(img, margin, aspects)
        out = Image.fromarray(img)
        if prescale != 1:
            out = out.resize((out.width * prescale, out.height * prescale), Image.LANCZOS)
        out.save(out_dir / "input" / f"{bg.name}.png")
        by_key[key] = bg.name
        jobs[bg.name] = {"canvas": list(rect), "pad": pad, "input_size": list(out.size), "copies": [],
                         "defaults": defaults, "animations": animations}
    (out_dir / "jobs.json").write_text(json.dumps(jobs, indent=1))
    return jobs


def _unpad(img, job, frame=None):
    """Crop the AI output back to the field canvas, whatever size the generator returned.

    frame: extra margin the generator returned around the canvas (1x pixels per side)."""
    left, top, right, bottom = (a + b for a, b in zip(job["pad"], frame or (0, 0, 0, 0)))
    cw, ch = job["canvas"][2], job["canvas"][3]
    h, w = img.shape[:2]
    sx, sy = w / (cw + left + right), h / (ch + top + bottom)
    return img[round(top * sy) : h - round(bottom * sy), round(left * sx) : w - round(right * sx)]


def fit_color(src_px, dst_px):
    """3x4 affine color transform src -> dst by robust least squares on matching pixels."""
    s = src_px.reshape(-1, 3).astype(np.float32)
    d = dst_px.reshape(-1, 3).astype(np.float32)
    if len(s) > 200000:
        idx = np.random.default_rng(0).choice(len(s), 200000, replace=False)
        s, d = s[idx], d[idx]
    X = np.hstack([s, np.ones((len(s), 1), np.float32)])
    keep = np.ones(len(s), bool)
    for _ in range(3):
        M, *_ = np.linalg.lstsq(X[keep], d[keep], rcond=None)
        err = np.linalg.norm(X @ M - d, axis=1)
        keep = err < np.percentile(err, 80)
    return M  # (4, 3)


def keep_colors(main, panels, k):
    """Pull the new art's colours toward the original palette by k (0-1). A colour transform is fitted from
    the render to the original, so the new detail and shading stay; it's the same for the render and every
    state panel, so they keep matching. The original -> new transform is then refitted, for the original
    art that goes next to the new (glows, layers 2-3)."""
    if k <= 0:
        return
    N = fit_color(main["ai_1x"], main["orig"])

    def pull(img):
        rgb = img.astype(np.float32)
        return np.clip(rgb * (1 - k) + (rgb @ N[:3] + N[3]) * k, 0, 255).astype(np.uint8)

    main["ai_hi"], main["ai_1x"] = pull(main["ai_hi"]), pull(main["ai_1x"])
    if panels:
        panels["images"] = {key: [(r, pull(img)) for r, img in lst] for key, lst in panels["images"].items()}
        panels["off"] = [(r, fp, pull(img)) for r, fp, img in panels["off"]]
    main["M"] = fit_color(main["orig"], main["ai_1x"])


def apply_color(px, M):
    rgb = px[..., :3].astype(np.float32)
    out = np.clip(rgb @ M[:3] + M[3], 0, 255).astype(np.uint8)
    return np.dstack([out, px[..., 3]])


def edges_kept(orig, img, tol=2):
    """Share of the original's strong outlines that still have an edge within `tol` px in img (1x).

    Calibration: the original shifted by 3px scores ~0.82; ControlNet renders 0.85-0.95; an
    unconstrained restyle 0.65-0.70. Below ~0.8 the walk-behind cut-outs stop matching the art."""
    def edges(rgb, lo, hi, blur):
        g = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (0, 0), blur)
        return cv2.Canny(g, lo, hi) > 0
    eo = edges(orig, 80, 200, 1.5)
    if not eo.any():
        return 1.0
    ea = edges(img, 40, 120, 1.0)
    dist = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 3)
    return float((dist[eo] <= tol).mean())


def _l1_cutout_edges(bg, rect, scale):
    """Outline of layer-1 static cut-outs: where sprites get hidden behind scenery."""
    min_x, min_y, w, h = rect
    m = np.zeros((h, w), np.uint8)
    cache = PageCache(bg)
    for t in bg.tiles:
        if t.layer == 1 and is_base(t):
            x, y = t.dst_x - min_x, t.dst_y - min_y
            m[y : y + t.size, x : x + t.size] |= (cache.tile(t)[..., 3] > 0).astype(np.uint8)
    m = cv2.resize(m * 255, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    return cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0


def prepare_main(bg, ai_path, job, rendered_defaults=None, scale=None, do_align=True, frame=None):
    """Load the AI render, align it if asked, and fit the color transform original -> new look.

    rendered_defaults: the states that were on in the image sent to the generator.
    frame: margin the render has around the canvas (1x pixels per side), for the aligner to pull from."""
    rect = canvas_rect(bg)
    min_x, min_y, w, h = rect
    orig = np.asarray(render_composite(bg, rect, PageCache(bg).tile, 1, shows(rendered_defaults)))

    raw = np.asarray(Image.open(ai_path).convert("RGB"))
    ai_full = _unpad(raw, job, frame)
    s_est = ai_full.shape[1] / w
    if abs(ai_full.shape[0] / h - s_est) > 0.05 * s_est:
        raise ValueError(f"output aspect {ai_full.shape[1]}x{ai_full.shape[0]} doesn't match canvas {w}x{h}")
    scale = scale or max(1, round(s_est))

    # Where the original's structures landed in the AI image. Only confident, unambiguous matches:
    # restyled textures (new tile patterns etc.) would otherwise look like drift.
    vecs = find_drift(orig, ai_full)
    before = after = drift_stats(np.hypot(vecs[:, 2], vecs[:, 3]))
    valid = np.ones((h * scale, w * scale), bool)
    T = fit_global(vecs, w, h) if do_align else None
    if T is not None:
        resid = residuals(vecs, T)
        if np.median(resid) < before.median - 0.1:  # worth a resample
            fl, ft, fr, fb = frame or (0, 0, 0, 0)
            shift = np.array([[1, 0, fl], [0, 1, ft], [0, 0, 1]], np.float64)  # canvas -> framed render
            ai_hi, valid = warp_global(_unpad(raw, job), shift @ T, w, h, scale, (w + fl + fr, h + ft + fb))
            after = drift_stats(resid)
    if after is before:
        ai_hi = cv2.resize(ai_full, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    ai_1x = cv2.resize(ai_hi, (w, h), interpolation=cv2.INTER_AREA)

    valid_1x = cv2.resize(valid.astype(np.uint8), (w, h), interpolation=cv2.INTER_AREA) > 0
    M = fit_color(orig[valid_1x], ai_1x[valid_1x])
    missing = 1 - valid.mean()
    if missing > 0:
        # fill edges the generator cropped away with the color-matched original, feathered
        fill = apply_color(upscale_rgba(np.dstack([orig, np.full((h, w), 255, np.uint8)]), scale), M)[..., :3]
        soft = cv2.GaussianBlur(valid.astype(np.float32), (0, 0), 2 * scale)
        soft = np.minimum(soft, valid)[..., None]
        ai_hi = (ai_hi * soft + fill * (1 - soft)).astype(np.uint8)
    return {
        "rect": rect, "scale": scale, "orig": orig, "ai_full": ai_full, "ai_hi": ai_hi, "ai_1x": ai_1x,
        "M": M, "before": before, "after": after, "missing": missing,
        "defaults": {int(p): m for p, m in (rendered_defaults or {}).items()},
    }


ADD_BLEND = {1, 3}  # tile blends that add light: add, add a quarter
# Palette effects with more states than this stay still: every state is a texture FFNx loads while the field plays.
# 203 fields have under 300; five have 600 to 13,700 (bugin3, rootmap, ancnt2, whitein, crater_2), and SYW's
# pack turns animation off in those too.
MAX_ANIMATED_FRAMES = 300


def carries_art(t):
    """Does a blended tile's block hold the original's own art (build_pages block_for: original_tile)? Toggled ones
    and the parallax layers' (2-3); static ones on layers 0-1 are glow_tile (the AI image, or blank)."""
    return bool(t.param) or t.layer >= 2


STATE_FLOOR = 4.0  # state_light: stored light this dim (0..255, smooth) can't be scaled: the state's light is added


def state_light(rgb, stored, state):
    """An additive tile's art in the stored colours (`rgb`, at the output scale) relit to a palette state: scaled by
    the state's smooth light over the stored one (luma: per channel, its edges took colour fringes), so the art's own
    detail (zz7's dithered glow) dims with it; where the stored light is too dim to scale, the state's light added."""
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    y0, y1 = stored @ luma, state @ luma
    k = np.clip(y1 / np.maximum(y0, STATE_FLOOR), 0, 8)[..., None]
    return np.where((y0 > STATE_FLOOR)[..., None], rgb * k, rgb + np.maximum(state - stored, 0))


def animated_pages(bg, anim, stored, pages, scale, M):
    """FFNx animated textures for a field's palette effects, from this version's own pages: yields
    ((page, palette, hash), RGBA page) for every palette state the scripts set (palettes.animated_textures).

    Each state is the version's tiles with the state's colour change on top, matched to the new look. The main
    render has the colours as stored in the field file (`stored`) baked in: tiles that add light get the light a
    state adds over those, solid tiles change colour both ways. Averaging and subtracting tiles stay as they are.
    Additive tiles that carry the original's own art (toggled ones and the parallax layers': block_for's
    original_tile) hold that art in the stored colours, which the game never shows once the scripts run (zz7's
    crystal glow is stored at 75 and cycles 5-35; woa_2's mist is stored at 46 and set to 21 for good): they take
    the state's light both ways (state_light). Adding only what a state adds above the stored colours left them at
    the stored light all the time, 2-14x what the original shows, blown out white over the AI's own painted glow."""
    lin = M[:3].astype(np.float32)
    for (page, pal), by_hash in sorted(anim.items()):
        tiles = [t for t in bg.tiles if t.tex == page and t.palette == pal]
        if not tiles or page not in pages:
            continue
        transparent = bg.transparency[pal] if pal < len(bg.transparency) else True

        def at_1x(colours):
            return palettes.page_bgra(bg, page, colours, transparent)[..., [2, 1, 0, 3]].astype(np.float32)

        def smooth(a):  # the change is light, not pixels: a soft enlargement, then a little blur
            a = cv2.resize(a, (256 * scale, 256 * scale), interpolation=cv2.INTER_CUBIC)
            return cv2.GaussianBlur(a, (0, 0), 0.35 * scale)

        def light(colours):  # a state's light, smooth, in the new look's colours
            return np.maximum(smooth(at_1x(colours)[..., :3] @ lin), 0)
        base, src = at_1x(stored[pal]), pages[page]
        base_alpha = cv2.resize(base[..., 3], (256 * scale, 256 * scale), interpolation=cv2.INTER_LINEAR)
        base_light = light(stored[pal])
        for h, colours in sorted(by_hash.items()):
            delta = smooth((at_1x(colours)[..., :3] - base[..., :3]) @ lin)  # the change, in the new look's colours
            lit = light(colours) if any(t.blending and carries_art(t) for t in tiles) else None
            img = np.zeros_like(src)
            for t in tiles:
                ys = slice(t.src_y * scale, (t.src_y + t.size) * scale)
                xs = slice(t.src_x * scale, (t.src_x + t.size) * scale)
                block, d = src[ys, xs].astype(np.float32), delta[ys, xs]
                if t.blending and t.type_trans in ADD_BLEND and carries_art(t):
                    rgb, alpha = state_light(block[..., :3], base_light[ys, xs], lit[ys, xs]), block[..., 3]
                elif t.blending and t.type_trans in ADD_BLEND:
                    rgb, alpha = block[..., :3] + np.maximum(d, 0), np.maximum(block[..., 3], base_alpha[ys, xs])
                elif t.blending:
                    rgb, alpha = block[..., :3], block[..., 3]
                else:
                    rgb, alpha = block[..., :3] + d, block[..., 3]
                img[ys, xs, :3] = np.clip(rgb, 0, 255)
                img[ys, xs, 3] = alpha
            yield (page, pal, h), img


def still_light(bg, anim, stored, pages, scale, M):
    """For a field whose palette effects stay still (over MAX_ANIMATED_FRAMES): the additive tiles that carry the
    original's art (carries_art) in `pages` relit, in place, to the states' average light instead of the stored
    colours, which the game never shows (whitein's searchlights: every beam lit at once at the stored colours, where
    the game sweeps one at a time; ancnt2's light shafts at full where the game pulses them)."""
    lin = M[:3].astype(np.float32)
    for (page, pal), by_hash in sorted(anim.items()):
        tiles = [t for t in bg.tiles if t.tex == page and t.palette == pal and t.blending
                 and t.type_trans in ADD_BLEND and carries_art(t)]
        if not tiles or page not in pages or not by_hash:
            continue
        transparent = bg.transparency[pal] if pal < len(bg.transparency) else True

        def light(colours):
            a = palettes.page_bgra(bg, page, colours, transparent)[..., [2, 1, 0]].astype(np.float32) @ lin
            a = cv2.resize(a, (256 * scale, 256 * scale), interpolation=cv2.INTER_CUBIC)
            return np.maximum(cv2.GaussianBlur(a, (0, 0), 0.35 * scale), 0)
        base, mean = light(stored[pal]), np.mean([light(c) for c in by_hash.values()], 0)
        for t in {(t.src_x, t.src_y): t for t in tiles}.values():  # a block several tiles use is relit once
            ys = slice(t.src_y * scale, (t.src_y + t.size) * scale)
            xs = slice(t.src_x * scale, (t.src_x + t.size) * scale)
            block = pages[page][ys, xs]
            block[..., :3] = np.clip(state_light(block[..., :3].astype(np.float32), base[ys, xs], mean[ys, xs]), 0, 255)


def build_pages(bg, main, job, mod_root, review_dir, panels=None, raw=None, anim_cache=None):
    """Cut the new art into FFNx pages for the map and its copies, and write the review sheet. With the field's
    raw data, also its palette effects as animated textures (anim_cache: where the replayed states are kept).

    panels (from the states pass): {"images": {(param, state): (rect, RGB)},
    "off": [(rect, footprint, RGB)]}, each image covering its rect at the output scale."""
    rect, scale, ai_hi, M = main["rect"], main["scale"], main["ai_hi"], main["M"]
    min_x, min_y, w, h = rect
    defaults = main["defaults"]
    panels = panels or {"images": {}, "off": []}
    cache = PageCache(bg)
    names = assign_sheets(bg, rect)
    m = 2  # context around each original tile so its upscaled edges match its neighbours
    canvases = {n: np.pad(c, ((m, m), (m, m), (0, 0))) for n, c in sheet_canvases(bg, rect, names, cache).items()}
    tile_sheet = dict(zip(map(id, bg.tiles), names))

    # Footprints of groups that were on in the main render: the static tiles underneath show the
    # state there, so they need the "all off" panel (or original art) for when the state turns off.
    covered = {}
    for t in bg.tiles:
        if t.param and not t.blending and defaults.get(t.param, 0):
            covered.setdefault(t.param, []).append((t.dst_x - min_x, t.dst_y - min_y, t.size))

    def hits(t, tiles):
        x, y = t.dst_x - min_x, t.dst_y - min_y
        return any(x < fx + fs and fx < x + t.size and y < fy + fs and fy < y + t.size for fx, fy, fs in tiles)

    # Where those states actually cover the scene in the main render. Only there do the tiles beneath
    # need the "all off" art: around a door, the same tiles are visible in the default view and must
    # match the main render, or they show as rectangles of differently painted (or original) art.
    cover = None
    if covered:
        hidden_view = shows({p: m for p, m in defaults.items() if p not in covered})
        hidden = np.asarray(render_composite(bg, rect, cache.tile, 1, hidden_view))
        diff = np.any(hidden != main["orig"], axis=2).astype(np.float32)
        cover = cv2.resize(diff, (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)

    def under(t):
        """Is this static tile beneath a state that was on in the main render?"""
        return any(hits(t, tiles) for tiles in covered.values())

    def off_panel(t):
        return next(((r, img) for r, tiles, img in panels["off"] if hits(t, tiles)), None)

    def original_tile(t):
        """Original art for one tile, upscaled with its surroundings and color-matched."""
        x, y = t.dst_x - min_x, t.dst_y - min_y
        region = canvases[tile_sheet[id(t)]][y : y + t.size + 2 * m, x : x + t.size + 2 * m]
        up = upscale_rgba(np.ascontiguousarray(region), scale)
        return apply_color(up[m * scale : (m + t.size) * scale, m * scale : (m + t.size) * scale], M)

    # Static glows are baked into the AI image, so their own tiles go blank, except where nothing solid
    # lies beneath them: there the glow alone draws the scene (blin1's Shin-Ra screen is additive tiles
    # over empty background), so it has to carry the new art itself or that area turns black.
    # (built from the solid tiles' own upscaled alpha, so the two add up exactly at their edges)
    shown = shows(defaults)
    solid = np.zeros((h * scale, w * scale), np.float32)
    for t in bg.tiles:
        if not t.blending and shown(t):
            x, y, sz = (t.dst_x - min_x) * scale, (t.dst_y - min_y) * scale, t.size * scale
            solid[y : y + sz, x : x + sz] = np.maximum(
                solid[y : y + sz, x : x + sz], tile_alpha(cache.tile(t), scale).astype(np.float32) / 255)
    bare = 1 - solid
    # one glow per spot carries it: the last drawn, so averaging glows drawn after it can't halve it
    carrier = {}
    for t in draw_order([t for t in bg.tiles if t.blending and not t.param and t.layer < 2]):
        carrier[(t.dst_x, t.dst_y)] = id(t)
    GAIN = {1: 1, 3: 4, 0: 2}  # add, add a quarter, average (over black); subtract can't add light

    def glow_tile(t):
        sz = t.size * scale
        x, y = (t.dst_x - min_x) * scale, (t.dst_y - min_y) * scale
        m = bare[y : y + sz, x : x + sz]
        if carrier.get((t.dst_x, t.dst_y)) != id(t) or t.type_trans not in GAIN or m.max() < 0.01:
            return np.zeros((sz, sz, 4), np.uint8)
        rgb = np.clip(ai_hi[y : y + sz, x : x + sz] * m[..., None] * GAIN[t.type_trans], 0, 255)
        return np.dstack([rgb.astype(np.uint8), np.full((sz, sz), 255, np.uint8)])

    def state_panel(t):
        """The restyled panel holding this state tile (one per spot the state appears in)."""
        x, y = t.dst_x - min_x, t.dst_y - min_y
        for r, img in panels["images"].get((t.param, t.state), []):
            if r[0] <= x and x + t.size <= r[0] + r[2] and r[1] <= y and y + t.size <= r[1] + r[3]:
                return r, img
        return None

    def from_panel(t, panel_rect, img):
        rx, ry = panel_rect[:2]
        sz = t.size * scale
        x, y = (t.dst_x - min_x - rx) * scale, (t.dst_y - min_y - ry) * scale
        return np.dstack([img[y : y + sz, x : x + sz], tile_alpha(cache.tile(t), scale)])

    def block_for(t):
        sz = t.size * scale
        x, y = (t.dst_x - min_x) * scale, (t.dst_y - min_y) * scale

        def from_ai():
            return np.dstack([ai_hi[y : y + sz, x : x + sz], tile_alpha(cache.tile(t), scale)])

        if t.blending:
            # static glows: see glow_tile; toggled ones stay additive overlays of the original art, and so do the
            # parallax layers' (2-3): the game scrolls those apart from the scene (the Ancient Forest's light shafts
            # were 374 and 205 px off the scene's place at the review's spots), so the AI image can't hold them
            # where they're drawn. Blanked, anfrst_1's beam was gone wherever the game shows it; carried as the AI
            # image (glow_tile over nothing solid), anfrst_3 got a bright copy of the picture with tile edges.
            return glow_tile(t) if not t.param and t.layer < 2 else original_tile(t)
        if t.param:
            found = state_panel(t)
            if found:
                return from_panel(t, *found)
            if defaults.get(t.param, 0) & t.state:
                return from_ai()  # on in the main render
            return original_tile(t)
        if is_base(t):
            if not under(t):
                return from_ai()
            found = off_panel(t)
            beneath = from_panel(t, *found) if found else original_tile(t)
            m = cover[y : y + sz, x : x + sz, None]
            rgb = ai_hi[y : y + sz, x : x + sz] * (1 - m) + beneath[..., :3] * m
            return np.dstack([rgb.astype(np.uint8), tile_alpha(cache.tile(t), scale)])
        return original_tile(t)

    pages = swizzle(bg, scale, block_for)
    frames, too_many, anim = 0, 0, {}
    if raw is not None:
        anim = (palettes.animated_textures_cached(raw, bg, anim_cache) if anim_cache
                else palettes.animated_textures(raw, bg))
        if sum(len(v) for v in anim.values()) > MAX_ANIMATED_FRAMES:
            still_light(bg, anim, palettes.file_palettes(raw), pages, scale, M)
            too_many, anim = sum(len(v) for v in anim.values()), {}
    written = write_pages(bg.name, pages, mod_root)
    for copy in job["copies"]:
        write_pages(copy, pages, mod_root)
    if anim:
        for (page, pal, hsh), img in animated_pages(bg, anim, palettes.file_palettes(raw), pages, scale, M):
            for n in (bg.name, *job["copies"]):
                d = Path(mod_root) / "field" / n
                d.mkdir(parents=True, exist_ok=True)
                # FFNx's name for it: the hash as %llx prints it, no leading zeros
                Image.fromarray(img, "RGBA").save(d / f"{n}_{page:02d}_{pal:02d}_{hsh:x}.png", compress_level=3)
            frames += 1

    # Review sheet: original | AI as returned | what the game will draw, with checks overlaid.
    # Built at panel size; the checks don't need the full 8x resolution.
    orig = main["orig"]
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    panel_w = min(w * scale, 900)
    panel_h = round(h * panel_w / w)
    rs = max(1, min(scale, round(panel_w / w)))
    result = np.asarray(render_composite(bg, rect, page_tile_source(pages, scale), scale, shows(defaults))
                        .resize((w * rs, h * rs), Image.LANCZOS))
    check = result.copy()
    edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(
        cv2.resize(orig, (w * rs, h * rs), interpolation=cv2.INTER_LANCZOS4),
        cv2.COLOR_RGB2GRAY), (0, 0), rs), 60, 160) > 0
    check[edges] = (255, 50, 50)
    check[_l1_cutout_edges(bg, rect, rs)] = (60, 255, 90)
    shown = [orig, main["ai_full"], result, check]
    labels = ["original", "AI output", "in game (aligned)", "red: original edges  green: walk-behind cut-outs"]
    sheet = Image.new("RGB", (panel_w * 2 + 10, (panel_h + 24) * 2 + 10), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (p, label) in enumerate(zip(shown, labels)):
        px, py = (i % 2) * (panel_w + 10), (i // 2) * (panel_h + 34)
        sheet.paste(Image.fromarray(p).resize((panel_w, panel_h), Image.LANCZOS), (px, py + 24))
        d.text((px + 4, py + 5), label, fill=(230, 230, 230))
    sheet.save(review_dir / f"{bg.name}.jpg", quality=90)

    before, after = main["before"], main["after"]
    return {
        "map": bg.name, "copies": job["copies"], "scale": scale, "pages": len(written),
        "drift_before": before, "drift_after": after, "missing": main["missing"],
        "aligned": after is not before, "edges_kept": edges_kept(orig, main["ai_1x"]),
        "state_panels": sum(len(v) for v in panels["images"].values()) + len(panels["off"]),
        "animated_frames": frames, "animation_left_still": too_many,
    }


def assemble(bg, ai_path, job, mod_root, review_dir, scale=None, do_align=True, rendered_defaults=None,
             panels=None):
    main = prepare_main(bg, ai_path, job, rendered_defaults, scale, do_align)
    return build_pages(bg, main, job, mod_root, review_dir, panels)


def flags_for(result):
    flags = []
    if result["edges_kept"] < 0.8:
        flags.append("layout changed")
    if result["missing"] > 0.01:
        flags.append("edges filled")
    return flags
