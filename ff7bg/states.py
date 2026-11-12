"""States pass: restyle every toggled background state (doors, lights, animation frames).

Rather than re-rendering the whole scene once per state, each state is repainted only where its
tiles are, against the already-restyled scene around it, so the edges always line up.

Each spot where a group's tiles sit is a cluster, with all the group's states that appear there
(animation frames, open and closed versions of one door); its states are painted side by side in the
same image, which keeps frames consistent with each other. A group that flickers in three places is
three small clusters, not one panel the size of the scene. All of a field's clusters are packed into
as few images as possible. When a cluster is on in the main render, an extra "all off" panel restyles
what's underneath it.

Only the pixels a state actually changes (compared with the view the main render shows) are repainted
and replaced; everywhere else a panel is the main render itself, so tile edges can't show seams. They're
repainted only partially (state_denoise), so each frame keeps its own shape and the frames one design.
"""
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import upscalers
from .ai import apply_color
from .comfy import FAMILIES, SEEDVR2, build_states_workflow, work_size
from .sheets import PageCache, canvas_rect, render_composite

MARGIN = 32           # context around a cluster's tiles, in original pixels (>= the 32px big tiles)
MAX_SHEET_MP = 2.2    # Flux works well up to about this size
MAX_SHEET_SIDE = 1792
MIN_SHEET_SIDE = 768  # tiny sheets are rendered larger so Flux has room to paint
KEY_MIN_CHANGE = 0.3  # share of a spot a state must change to get its own painted key frame
MOVING_FADE = 6       # moving parts' light repaint fades into the scene's over this many original pixels
MOVING_PAD = 2        # around what changes between their frames
# 2026-11-12 (user: the states are the weakest part; blin60_2's door opened onto a glass pod, games_1's SUPER DUNK sign
# lit up as "TV Station"):
ANCHOR_MIN = 0.1      # share of a spot a state must fill with new shapes (a door opening onto the room) to be painted whole
RECOLOUR_MAX = 0.25   # a state whose change is at most this share of new shapes only recolours (a sign lighting up)
# the states' prompt: the field's own, without its description (whose objects and quoted signs got painted into small
# panels: "a stage labeled 'TV Station'" on the SUPER DUNK sign), and with this
STATES_GUARD = "Keep every sign's lettering exactly as it is: no new words, letters or logos."
LAYER_COVER = 0.6     # a layered frame: share of its layer the key's layer, moved, must cover
LAYER_ERR = 20.0      # ...and how far apart (mean per channel, slightly blurred) the two may be there
NEAR_PICK = 90        # an anchored frame's pixel is taken from the nearest painted state when its original is this near
                      # (sum of R, G, B differences); farther, it's painted afresh
SHIFT_MAX = 32        # how far (original pixels) a part may slide between frames and still be the key's part moved
SHIFT_MIN = 0.3       # share of a frame's new pixels the key moved must match
# the shifts tried, smallest first: along x or y (doors, lifts), or a few pixels any way
SHIFTS = sorted(({(d, 0) for d in range(-SHIFT_MAX, SHIFT_MAX + 1)} | {(0, d) for d in range(-SHIFT_MAX, SHIFT_MAX + 1)}
                 | {(dx, dy) for dx in range(-3, 4) for dy in range(-3, 4)}) - {(0, 0)},
                key=lambda d: (abs(d[0]) + abs(d[1]), d))


@dataclass
class Cluster:
    param: int
    states: list          # state masks; 0 means "all off"
    rect: tuple           # (x, y, w, h) in canvas pixels, origin at the canvas corner
    footprint: list       # (x, y, size) of every tile, canvas pixels


def _overlap(a, b, pad=0):
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


def plan(bg, rect, rendered_defaults):
    """One cluster per spot: a group's tiles (of any state) within a few pixels of each other."""
    min_x, min_y, cw, ch = rect
    by_param = {}
    for t in bg.tiles:
        if t.param and not t.blending:
            by_param.setdefault(t.param, set()).add((t.dst_x - min_x, t.dst_y - min_y, t.size, t.state))
    clusters = []
    for p, tiles in sorted(by_param.items()):
        tiles = sorted(tiles)
        parent = list(range(len(tiles)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        # neighbours via a coarse grid, so big groups don't need an all-pairs check
        grid = {}
        for i, (x, y, s, _) in enumerate(tiles):
            for gx in range((x - 8) // 32, (x + s + 8) // 32 + 1):
                for gy in range((y - 8) // 32, (y + s + 8) // 32 + 1):
                    grid.setdefault((gx, gy), []).append(i)
        for cell in grid.values():
            for a in cell:
                xa, ya, sa, _ = tiles[a]
                for b in cell:
                    if b > a:
                        xb, yb, sb, _ = tiles[b]
                        if _overlap((xa, ya, xa + sa, ya + sa), (xb, yb, xb + sb, yb + sb), pad=8):
                            parent[find(a)] = find(b)
        spots = {}
        for i in range(len(tiles)):
            spots.setdefault(find(i), []).append(tiles[i])
        for members in sorted(spots.values()):
            states = sorted({st for _, _, _, st in members})
            foot = sorted({(x, y, s) for x, y, s, _ in members})
            x0 = max(0, min(x for x, _, _ in foot) - MARGIN)
            y0 = max(0, min(y for _, y, _ in foot) - MARGIN)
            x1 = min(cw, max(x + s for x, _, s in foot) + MARGIN)
            y1 = min(ch, max(y + s for _, y, s in foot) + MARGIN)
            if rendered_defaults.get(p, 0) & sum(states):
                states.append(0)
            clusters.append(Cluster(p, states, (x0, y0, x1 - x0, y1 - y0), foot))
    return clusters


def _panel_original(bg, rect, cache, cluster, state, rendered_defaults):
    """Original art of the cluster's area with only `state` of its group on (0 = all off)."""
    return _panel(bg, rect, cache.tile, 1, cluster, state, rendered_defaults)


def _panel(bg, rect, tile, scale, cluster, state, rendered_defaults):
    """The cluster's area from `tile` (a tile source at `scale`) with only `state` of its group on."""
    others = {p: m for p, m in rendered_defaults.items() if p != cluster.param}

    def inc(t):
        if not t.param:
            return True
        if t.param == cluster.param:
            return bool(state & t.state)
        return bool(others.get(t.param, 0) & t.state)

    x, y, w, h = cluster.rect
    return np.asarray(render_composite(bg, rect, tile, scale, inc))[y * scale : (y + h) * scale, x * scale : (x + w) * scale]


def _color(img, M):
    """The original's colors mapped to the new look (float)."""
    return apply_color(np.dstack([img, np.full(img.shape[:2], 255, np.uint8)]), M)[..., :3].astype(np.float32)


def _changed(view, default_view):
    """Pixels (1x) where a state's view differs from what the main render shows, grown by 1px."""
    diff = np.any(view != default_view, axis=2).astype(np.uint8)
    return cv2.dilate(diff, np.ones((3, 3), np.uint8))


def _same_shapes(a, b):
    """1x map: True where two views show the same shapes, maybe in other colours (a lamp lit, letters turned green):
    their brightness patterns correlate over 5x5 pixels, or both are flat there."""
    la = cv2.cvtColor(np.ascontiguousarray(a), cv2.COLOR_RGB2GRAY).astype(np.float32)
    lb = cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_RGB2GRAY).astype(np.float32)
    k = (5, 5)
    ma, mb = cv2.blur(la, k), cv2.blur(lb, k)
    va = cv2.blur(la * la, k) - ma * ma
    vb = cv2.blur(lb * lb, k) - mb * mb
    cov = cv2.blur(la * lb, k) - ma * mb
    flat = (va < 30) & (vb < 30)
    return flat | (cov / np.sqrt(np.maximum(va * vb, 1e-3)) > 0.5)


def _crisp(mask1, w, h, scale):
    """A 1x mask at `scale` with edges softened over less than a pixel of the original (a layer's outline stays sharp)."""
    m = cv2.resize(mask1.astype(np.float32), (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    return np.clip(cv2.GaussianBlur(m, (0, 0), max(0.4 * scale, 0.5)), 0, 1)[..., None]


def _moved(img, shift, g):
    """`img` (at g x the original) moved by `shift` original pixels: out[y, x] = img[y - dy, x - dx] (edges repeated)."""
    h, w = img.shape[:2]
    m = np.float32([[1, 0, shift[0] * g], [0, 1, shift[1] * g]])
    return cv2.warpAffine(np.ascontiguousarray(img), m, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)


def _new_shapes(view, ref):
    """1x map: where `view` differs from `ref` with new shapes, not just new colours (a door gone, a room behind)."""
    return np.any(view != ref, axis=2) & ~_same_shapes(view, ref)


def _pack_items(items, k):
    """Shelf-pack panels ((cluster, state) pairs) into sheets. Returns [(g, (W, H), [(cluster, state, x, y)])]
    with positions and sizes in original pixels; g is the integer generation scale of the sheet."""
    items = sorted(items, key=lambda cs: (-cs[0].rect[3], id(cs[0])))
    sheets, cur, x, y, shelf_h, width = [], [], 0, 0, 0, 0
    max_w = MAX_SHEET_SIDE // k
    max_area = MAX_SHEET_MP * 1e6 / (k * k)
    for c, s in items:
        w, h = c.rect[2], c.rect[3]
        if cur and x + w > max_w:
            x, y, shelf_h = 0, y + shelf_h, 0
        if cur and (max(width, x + w) * (y + max(shelf_h, h)) > max_area or (y + h) * k > MAX_SHEET_SIDE):
            sheets.append((cur, width, y + shelf_h))
            cur, x, y, shelf_h, width = [], 0, 0, 0, 0
        cur.append((c, s, x, y))
        x += w
        shelf_h = max(shelf_h, h)
        width = max(width, x)
    if cur:
        sheets.append((cur, width, y + shelf_h))
    out = []
    for placed, W, H in sheets:
        g = k
        while max(W, H) * g < MIN_SHEET_SIDE and W * H * (g + 1) ** 2 <= max_area * k * k:
            g += 1
        out.append((g, (W, H), placed))
    return out


def _key_state(c, defaults):
    """None when the main render shows one of the cluster's states (it's the key), else the state to
    paint first; the cluster's other states are derived from the key."""
    if any(s and defaults.get(c.param, 0) & s for s in c.states):
        return None
    return c.states[0]


def _rounds(clusters, defaults):
    """(key panels to paint, panels derived from a key)."""
    keys, derived = [], []
    for c in clusters:
        key = _key_state(c, defaults)
        if key is not None:
            keys.append((c, key))
        derived += [(c, s) for s in c.states if s != key]
    return keys, derived


def count_runs(bg, rect, rendered_defaults, k):
    """How many ComfyUI runs the states pass needs (for time estimates), and how many panels.
    k: the render scale (comfy.work_size over the canvas)."""
    clusters = plan(bg, rect, rendered_defaults)
    if not clusters:
        return 0, 0
    keys, derived = _rounds(clusters, rendered_defaults)
    return len(_pack_items(keys, k)) + len(_pack_items(derived, k)), len(keys) + len(derived)


def _soft(mask1, w, h, scale, grow=1):
    """A 1x 0/1 mask at `scale`, softened over about a pixel of the original."""
    m = mask1 if grow <= 1 else cv2.dilate(mask1, np.ones((grow, grow), np.uint8))
    m = cv2.resize(m.astype(np.float32), (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)
    return np.clip(cv2.GaussianBlur(m, (0, 0), scale * 0.75) * 1.5, 0, 1)[..., None]


def _merge(base, art, change1x, scale):
    """`art` in `base` where the 1x mask says, with the edges matched to the surroundings (Poisson
    blending), so a repainted spot can't show as a lighter or darker patch. Where the change runs off the
    panel (a ship cut by the picture's edge) there are no surroundings: the edge is matched to the art
    itself. Matched to the render there, del1's dark hull took the bright sky and boat behind it as a haze."""
    h, w = change1x.shape
    m = cv2.dilate(change1x, np.ones((5, 5), np.uint8))  # edge in unchanged territory
    if not m.any():
        return base.copy()
    mh = cv2.resize(m * 255, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    edge = np.zeros(mh.shape, bool)
    edge[:2, :] = edge[-2:, :] = edge[:, :2] = edge[:, -2:] = True
    base = base.copy()
    base[edge & (mh > 0)] = art[edge & (mh > 0)]
    mh[edge] = 0
    x0, y0, bw, bh = cv2.boundingRect(mh)
    if bw < 3 or bh < 3:
        return base
    return cv2.seamlessClone(np.ascontiguousarray(art), np.ascontiguousarray(base), mh,
                             (x0 + bw // 2, y0 + bh // 2), cv2.NORMAL_CLONE)


def animated_boxes(bg, rendered_defaults, params):
    """Where looping animations' solid tiles change the picture between their states (a fan's blades, a gauge's
    needle), as boxes in canvas pixels (signs.masks' format, with MOVING_FADE): their moving parts."""
    rect = canvas_rect(bg)
    cache = PageCache(bg)
    out = []
    for p in sorted(params):
        states = sorted({t.state for t in bg.tiles if t.param == p and not t.blending})
        if not states:
            continue
        c = Cluster(p, states, (0, 0, rect[2], rect[3]), [])
        base = _panel_original(bg, rect, cache, c, 0, rendered_defaults)
        changed = np.zeros(base.shape[:2], np.uint8)
        for s in states:
            changed |= np.any(_panel_original(bg, rect, cache, c, s, rendered_defaults) != base, axis=2).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(changed, np.ones((5, 5), np.uint8)))
        for k in range(1, n):
            x, y, w, h = (int(v) for v in stats[k, :4])
            out.append({"x1": float(max(0, x - MOVING_PAD)), "y1": float(max(0, y - MOVING_PAD)),
                        "x2": float(min(rect[2], x + w + MOVING_PAD)), "y2": float(min(rect[3], y + h + MOVING_PAD)),
                        "fade": MOVING_FADE, "param": p})
    return out


def run(comfy, bg, rect, main, settings, style_refs, work_dir, progress=None, cancelled=lambda: False,
        animated=None, hires=None, whole=None):
    """Restyle every state; returns panels for ai.build_pages and saves them under work_dir.

    Round 1 paints a key frame for each spot the main render doesn't show (a fan that's always
    spinning, a light switched on later): strongly, as it starts from original art (0.85 for the edit
    models, the field's repaint plus state_denoise_boost for the others). Round 2 derives every other
    state from its key (the main render itself where it shows the spot): it starts from the key plus
    what changes in the original (a needle moving, a door opening) and repaints only those pixels, at
    state_denoise, so frames stay one design. Looping animations (`animated`: {group: frame order}) are only
    touched up, at anim_repaint: every frame of a fan or a gauge is the same painted part, moving as the original's
    does (repainting them more turned Kalm's wall fan into a window in some frames and a heart in the main render).
    One that rests on the art underneath (0 in its order) has the main render as its key, however much it changes;
    one without gets its key painted at anim_repaint too.
    Since 2026-11-12: a state that only recolours what its key shows (a lamp lit, a sign's letters turned green) is
    touched up at anim_repaint too (at state_denoise, games_1's SUPER DUNK became other words). A spot where a state
    fills much of it with new shapes (a door opening onto the room behind) gets an anchor: that state painted whole
    from its original art, as a key is. Every other frame (not looping animations) takes each pixel from the key, the
    anchor, or the key moved by a few pixels (a sliding door's panel: SHIFTS) where the original matches it, and only
    what matches none of them (the door's moving edge) is painted, at state_denoise (anim_repaint when the frame only
    recolours). Adding the original's change to the painted key showed blin60_2's painted door through the room, and
    Flux made it a glass pod. The states' prompt is the field's without its description (STATES_GUARD).
    whole (the user's idea, 2026-11-12): whole(states) -> the main render's recipe (its seed and settings) run on the
    original with those states on, at the main render's geometry ({group: state mask}, every group given). When
    given, no state is painted here: a spot's key, anchor and every other frame (looping animations' too, since the
    user preferred them, though a fan's blades are drawn anew in each) are their whole renders, each only where it
    changes the picture; a layered frame is the key's painting moved over the
    anchor's whole render (no shimmer between a door's frames), its new pixels from its own whole render.
    hires: (tile source, its scale) of a cleaner enlargement of the same pages (the SYW Unified pack's 4x, when the
    main render started from it): the states' art comes from there instead of the original enlarged; the original
    still says which pixels a state changes.
    The Upscale workflow instead sends each state's original art through its upscaler (comfy
    may be None when that runs outside ComfyUI)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.glob("*.png"):
        old.unlink()
    settings = {**settings, "scene": "", "prompt": f"{settings['prompt'].rstrip()} {STATES_GUARD}"}
    scale, ai_hi, M, defaults = main["scale"], main["ai_hi"], main["M"], main["defaults"]
    default_view = main["orig"]  # the original as the main render saw it
    k = work_size(settings, rect[2], rect[3])[0] // rect[2]
    cache = PageCache(bg)
    clusters = plan(bg, rect, defaults)
    upscale_only = settings.get("family") == "upscale"
    outside = upscalers.is_local(settings["upscaler"])  # ImageMagick / Upscayl, after ComfyUI
    # Upscale workflow, an outside upscaler before SeedVR2: run it here, the graph then only resizes
    pre_up = (upscale_only and settings["upscaler"] == SEEDVR2 and not settings["seedvr2_input"]
              and upscalers.is_local(settings["input_upscaler"]))
    edit = FAMILIES.get(settings.get("family"), {}).get("edit", False)

    views, matched = {}, {}

    def view(c, s):
        if (id(c), s) not in views:
            views[(id(c), s)] = _panel_original(bg, rect, cache, c, s, defaults)
            matched[(id(c), s)] = _color(views[(id(c), s)], M)
        return views[(id(c), s)]

    def main_crop(c):
        cx, cy, w, h = c.rect
        return ai_hi[cy * scale : (cy + h) * scale, cx * scale : (cx + w) * scale]

    def default_crop(c):
        cx, cy, w, h = c.rect
        return default_view[cy : cy + h, cx : cx + w]

    def changed_from_default(c, s):
        return _changed(view(c, s), default_crop(c))

    views_hi = {}

    def art_at(c, s, g):
        """A state's art, colour-matched to the new look, at g x: from `hires` (shrunk or enlarged), else the
        original's enlarged."""
        w, h = c.rect[2], c.rect[3]
        if hires is None:
            view(c, s)
            return cv2.resize(matched[(id(c), s)], (w * g, h * g), interpolation=cv2.INTER_CUBIC)
        if (id(c), s) not in views_hi:
            views_hi[(id(c), s)] = _color(_panel(bg, rect, hires[0], hires[1], c, s, defaults), M)
        return cv2.resize(views_hi[(id(c), s)], (w * g, h * g),
                          interpolation=cv2.INTER_AREA if g < hires[1] else cv2.INTER_CUBIC)

    if upscale_only:
        keys, derived = [], [(c, s) for c in clusters for s in c.states]
    else:
        keys, derived = _rounds(clusters, defaults)
        # Small changes to what the main render already shows (a needle on a gauge) are derived from it;
        # only states that replace most of a spot (a fan spinning in an empty window) get a key painted.
        # Looping animations that rest on what the main render shows: always derived from it. So is a state that
        # only recolours what it shows, however much (games_1's SUPER DUNK sign lighting up was painted as a key, at
        # the keys' strength, and came out as other words)
        painted_keys = []
        for c, key in keys:
            area = sum(t * t for _, _, t in c.footprint)
            rests = 0 in (animated.get(c.param) or ())
            changed = changed_from_default(c, key)
            big = changed.sum() >= KEY_MIN_CHANGE * area
            recolour = _new_shapes(view(c, key), default_crop(c)).sum() <= RECOLOUR_MAX * max(1, changed.sum())
            (painted_keys if big and not rests and not recolour else derived).append((c, key))
        keys = painted_keys
    key_of = {id(c): s for c, s in keys}
    key_art = {}  # id(cluster) -> painted key at output scale (main crop where the main render is the key)
    animated = dict(animated or {})
    looping = set(animated)

    def key_view(c):
        key = key_of.get(id(c))
        return default_crop(c) if key is None else view(c, key)

    def key_matched(c):
        key = key_of.get(id(c))
        return _color(default_crop(c), M) if key is None else matched[(id(c), key)]

    def changed_from_key(c, s):
        return _changed(view(c, s), key_view(c))

    use_whole = whole is not None and not upscale_only

    def whole_crop(c, s):
        """The spot as the whole render with this state of its group on shows it (the other groups as the main render
        has them)."""
        d = dict(defaults)
        d[c.param] = s
        img = whole(d)
        cx, cy, w, h = c.rect
        return img[cy * scale:(cy + h) * scale, cx * scale:(cx + w) * scale]

    # Anchors: per spot (not looping animations), the state that fills most of it with new shapes, when that's
    # enough of it
    anchor_of = {}
    if not upscale_only:
        for c in clusters:
            if c.param in looping:
                continue
            area = sum(t * t for _, _, t in c.footprint)
            best, best_n = None, 0
            for s in c.states:
                if s == key_of.get(id(c)):
                    continue
                n = int(_new_shapes(view(c, s), key_view(c)).sum())
                if n > best_n:
                    best, best_n = s, n
            # the group all off (what's behind its layer: a doorway's room) is the anchor whenever it's one of the
            # spot's states and changes enough: the other frames are its layer laid over it (layered())
            if 0 in c.states and 0 != key_of.get(id(c)):
                n0 = int(_new_shapes(view(c, 0), key_view(c)).sum())
                if n0 >= ANCHOR_MIN * area:
                    best, best_n = 0, n0
            if best is not None and best_n >= ANCHOR_MIN * area:
                anchor_of[id(c)] = best
    anchors = [(c, anchor_of[id(c)]) for c in clusters if id(c) in anchor_of]
    derived = [(c, s) for c, s in derived if anchor_of.get(id(c)) != s]

    def recolours(c, s):
        """The state only recolours what its key shows (few new shapes among the pixels it changes)."""
        n = changed_from_key(c, s).sum()
        return n > 0 and _new_shapes(view(c, s), key_view(c)).sum() <= RECOLOUR_MAX * n

    pick_cache = {}

    def picks(c, s):
        """A frame per 1x pixel: as its key shows it, as its spot's anchor shows it, as the key shows it moved by a
        few pixels (a sliding door's panel, a lift), or none of them (new; of those, near_anchor: nearer the anchor's
        colours than the key's). Returns (from_key, from_anchor, from_shift, shift, new, near_anchor)."""
        if (id(c), s) in pick_cache:
            return pick_cache[(id(c), s)]
        v, ref = view(c, s), key_view(c)
        from_key = ~np.any(v != ref, axis=2)
        a = anchor_of.get(id(c))
        anc = view(c, a) if a is not None else None
        from_anchor = (~np.any(v != anc, axis=2) & ~from_key) if anc is not None else np.zeros_like(from_key)
        new = ~from_key & ~from_anchor
        shift, from_shift = None, np.zeros_like(new)
        ys, xs = np.nonzero(new)
        if len(ys) >= 16:
            h, w = new.shape
            best_n = 0
            for dx, dy in SHIFTS:
                sx, sy = xs - dx, ys - dy
                ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
                n = np.count_nonzero(np.all(v[ys[ok], xs[ok]] == ref[sy[ok], sx[ok]], axis=1))
                if n > best_n:
                    shift, best_n = (dx, dy), n
            if shift is not None and best_n >= max(16, SHIFT_MIN * len(ys)):
                from_shift = new & ~np.any(v != _moved(ref, shift, 1), axis=2)
                new = new & ~from_shift
            else:
                shift = None
        near_anchor = np.zeros_like(new)
        if anc is not None:
            # an anchored spot's frame: every pixel from whichever of key, anchor or moved key its original is closest
            # to, unless none is near (NEAR_PICK): a clean wipe from closed to open, both painted once. Exact matches
            # alone left the angled door's in-between pixels new, started from the key plus the change: smeared
            def dist(other):
                return np.abs(v.astype(np.int16) - other.astype(np.int16)).sum(2)
            dk, da = dist(ref), dist(anc)
            ds = dist(_moved(ref, shift, 1)) if shift is not None else np.full_like(dk, 10 ** 4)
            best = np.minimum(np.minimum(dk, da), ds)
            changed = ~(dk == 0)
            from_key = (dk == best) & (best <= NEAR_PICK)
            from_anchor = changed & (da == best) & ~from_key & (best <= NEAR_PICK)
            from_shift = changed & (ds == best) & ~from_key & ~from_anchor & (best <= NEAR_PICK)
            new = best > NEAR_PICK
            near_anchor = new & (da < dk)
        pick_cache[(id(c), s)] = (from_key, from_anchor, from_shift, shift, new, near_anchor)
        return pick_cache[(id(c), s)]

    layer_cache = {}

    def layer(c, img):
        """The group's layer in a view of its spot: where it differs from the group all off, cleaned of the
        original's dithering specks and pinholes."""
        diff = np.any(img != view(c, 0), axis=2).astype(np.uint8)
        diff = cv2.morphologyEx(diff, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        return cv2.morphologyEx(diff, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0

    def layered(c, s):
        """A frame of a spot whose anchor is the group all off, when its layer is the key's layer moved (a door
        sliding open, a shutter, a lift): (shift, its layer, the key's layer, the layer's pixels the moved key layer
        doesn't cover), else None. FF7 draws each such frame as the door's tiles at another place over the room."""
        if (id(c), s) in layer_cache:
            return layer_cache[(id(c), s)]
        res = None
        if anchor_of.get(id(c)) == 0 and s != 0:
            ls, lk = layer(c, view(c, s)), layer(c, key_view(c))
            if ls.sum() >= 16 and lk.sum() >= 16:
                v = cv2.GaussianBlur(view(c, s).astype(np.float32), (0, 0), 1.0)
                kv = cv2.GaussianBlur(key_view(c).astype(np.float32), (0, 0), 1.0)
                best = None
                for d in [(0, 0)] + SHIFTS:
                    mk = _moved(lk.astype(np.uint8), d, 1) > 0
                    over = ls & mk
                    if over.sum() < LAYER_COVER * ls.sum():
                        continue
                    err = float(np.abs(v[over] - _moved(kv, d, 1)[over]).mean())
                    if best is None or err < best[0]:
                        best = (err, d, mk)
                if best is not None and best[0] <= LAYER_ERR:
                    res = (best[1], ls, lk, ls & ~best[2])
        layer_cache[(id(c), s)] = res
        return res

    def new_of(c, s):
        lay = layered(c, s)
        return lay[3] if lay is not None else picks(c, s)[4]

    def new_in(c, s):
        return cv2.dilate(new_of(c, s).astype(np.uint8), np.ones((3, 3), np.uint8))

    # what each round paints (and how many sheets, for the progress count)
    plain = [(c, s) for c, s in derived if upscale_only or changed_from_key(c, s).any()]
    upscaled = plain if upscale_only else []
    moving = [cs for cs in plain if not upscale_only and cs[0].param in looping]
    framed = [cs for cs in plain if not upscale_only and cs[0].param not in looping and new_of(*cs).any()]
    light = [cs for cs in framed if recolours(*cs)]
    heavy = [cs for cs in framed if not recolours(*cs)]
    groups = ([cs for cs in keys if cs[0].param not in looping], [cs for cs in keys if cs[0].param in looping],
              anchors, upscaled, heavy, light, moving)
    if use_whole:  # nothing is painted here (looping animations too: the user found their whole renders better than
        groups = ([], [], [], [], [], [], [])  # touched-up frames, 2026-11-12)
    total = sum(len(_pack_items(g, k)) for g in groups if g)
    done = [0]

    def paint(items, start_fn, change_fn, denoise, ctx_fn=None):
        """Render (cluster, state) panels; returns {(id(c), s): output-scale image}. ctx_fn(c, s, g): what's
        around the repaint (default: the main render)."""
        out_imgs = {}
        for g, (W, H), placed in _pack_items(items, k):
            if cancelled():
                raise InterruptedError("cancelled")
            if progress:
                progress(done[0], total, len(placed))
            done[0] += 1
            ctx = np.zeros((H * g, W * g, 3), np.uint8)
            mask = np.zeros((H * g, W * g), np.uint8)
            start = np.zeros((H, W, 3), np.uint8) if upscale_only else np.zeros((H * g, W * g, 3), np.uint8)
            for c, s, x, y in placed:
                w, h = c.rect[2], c.rect[3]
                ctx[y * g : (y + h) * g, x * g : (x + w) * g] = (
                    ctx_fn(c, s, g) if ctx_fn else cv2.resize(main_crop(c), (w * g, h * g), interpolation=cv2.INTER_AREA))
                ch1 = change_fn(c, s)
                # repaint a little beyond the change so it blends in
                m1 = cv2.dilate(ch1, np.ones((5, 5), np.uint8)) * 255
                mask[y * g : (y + h) * g, x * g : (x + w) * g] = cv2.GaussianBlur(
                    cv2.resize(m1, (w * g, h * g), interpolation=cv2.INTER_NEAREST), (0, 0), g)
                if upscale_only:
                    view(c, s)
                    start[y : y + h, x : x + w] = matched[(id(c), s)].astype(np.uint8)
                else:
                    start[y * g : (y + h) * g, x * g : (x + w) * g] = start_fn(c, s, g)
            if upscale_only and outside:
                res = start  # nothing for ComfyUI to do: enlarged by finish() below
            else:
                if pre_up:
                    start = upscalers.upscale(start, settings["input_upscaler"], W * scale, H * scale, cancelled)
                tag = f"{bg.name}_{done[0]}"
                names = []
                for kind, arr in (("ctx", ctx), ("orig", start), ("mask", mask)):
                    p = work_dir / f"_{tag}_{kind}.png"
                    Image.fromarray(arr).save(p)
                    names.append(comfy.upload(p, "ff7studio_states"))
                    p.unlink()
                wf = build_states_workflow(settings, *names, W * g, H * g, W * scale, H * scale,
                                           f"ff7studio/states_{tag}", style_refs, denoise=denoise)
                res = np.asarray(Image.open(io.BytesIO(comfy.run(wf, None, cancelled))).convert("RGB"))
            res = upscalers.finish(res, settings, W * scale, H * scale, cancelled)
            for c, s, x, y in placed:
                w, h = c.rect[2], c.rect[3]
                out_imgs[(id(c), s)] = res[y * scale : (y + h) * scale, x * scale : (x + w) * scale]
        return out_imgs

    # Round 1: key frames, painted from the original art pasted into the new scene
    def key_start(c, s, g):
        w, h = c.rect[2], c.rect[3]
        view(c, s)
        ctx = cv2.resize(main_crop(c), (w * g, h * g), interpolation=cv2.INTER_AREA).astype(np.float32)
        art = art_at(c, s, g)
        m = _soft(changed_from_default(c, s), w, h, g)
        return np.clip(ctx * (1 - m) + art * m, 0, 255).astype(np.uint8)

    painted = {}
    if groups[0]:
        painted.update(paint(groups[0], key_start, changed_from_default, 0.85 if edit else None))
    if groups[1]:
        painted.update(paint(groups[1], key_start, changed_from_default, settings["anim_repaint"]))
    for c in clusters:
        key = key_of.get(id(c))
        if key is None:
            key_art[id(c)] = main_crop(c)
        elif use_whole:
            key_art[id(c)] = _merge(main_crop(c), whole_crop(c, key), changed_from_default(c, key), scale)
        else:
            w, h = c.rect[2], c.rect[3]
            blend = _soft(changed_from_default(c, key), w, h, scale)
            key_art[id(c)] = (main_crop(c) * (1 - blend) + painted[(id(c), key)] * blend).astype(np.uint8)

    def key_at(c, s, g):
        w, h = c.rect[2], c.rect[3]
        return cv2.resize(key_art[id(c)], (w * g, h * g), interpolation=cv2.INTER_AREA)

    # Round 1b: anchors, painted whole from their original art pasted into the key
    def anchor_start(c, s, g):
        w, h = c.rect[2], c.rect[3]
        ctx = key_at(c, s, g).astype(np.float32)
        m = _soft(changed_from_key(c, s), w, h, g)
        return np.clip(ctx * (1 - m) + art_at(c, s, g) * m, 0, 255).astype(np.uint8)

    anchor_art = {}
    if anchors and use_whole:
        for c, a in anchors:
            anchor_art[id(c)] = _merge(key_art[id(c)], whole_crop(c, a), changed_from_key(c, a), scale)
    elif anchors:
        # as strongly as the main render itself was painted: it's the spot's other look, not a new object (at the keys'
        # strength blin60_2's green room behind its door came out a dark blue void)
        got = paint(anchors, anchor_start, changed_from_key, 0.85 if edit else settings["denoise"], ctx_fn=key_at)
        for c, a in anchors:
            w, h = c.rect[2], c.rect[3]
            blend = _soft(changed_from_key(c, a), w, h, scale)
            anchor_art[id(c)] = (key_art[id(c)] * (1 - blend) + got[(id(c), a)] * blend).astype(np.uint8)

    # Round 2: everything else, from its key plus the change in the original
    def derived_start(c, s, g):
        w, h = c.rect[2], c.rect[3]
        view(c, s)
        base = cv2.resize(key_art[id(c)], (w * g, h * g), interpolation=cv2.INTER_AREA).astype(np.float32)
        if hires is not None:  # the change from the key, in the cleaner art
            key = key_of.get(id(c))
            delta = art_at(c, s, g) - art_at(c, key if key is not None else defaults.get(c.param, 0), g)
            return np.clip(base + delta, 0, 255).astype(np.uint8)
        delta = matched[(id(c), s)] - key_matched(c)
        return np.clip(base + cv2.resize(delta, (w * g, h * g), interpolation=cv2.INTER_CUBIC), 0, 255).astype(np.uint8)

    def compose(c, s, g):
        """A frame's start: the key's painting, the anchor's, or the key's moved, where the original matches each;
        elsewhere (new) the nearer of key and anchor plus the original's change from it. A layered frame (layered()):
        the painted room with the painted door moved over it, clipped to where FF7 draws the door in this frame."""
        w, h = c.rect[2], c.rect[3]
        lay = layered(c, s)
        if lay is not None:
            d, ls, lk, new = lay
            base = cv2.resize(anchor_art[id(c)], (w * g, h * g), interpolation=cv2.INTER_AREA).astype(np.float32)
            door = _moved(key_at(c, s, g).astype(np.float32), d, g)
            m = _crisp(ls & (_moved(lk.astype(np.uint8), d, 1) > 0), w, h, g)
            out = base * (1 - m) + door * m
            if new.any() and use_whole:  # door the key never showed: as this frame's whole render has it
                mn = _soft(new.astype(np.uint8), w, h, g)
                out = out * (1 - mn) + cv2.resize(whole_crop(c, s), (w * g, h * g), interpolation=cv2.INTER_AREA) * mn
            elif new.any():  # ...else the room plus the original's change from it
                dl = cv2.resize(matched[(id(c), s)] - matched[(id(c), 0)], (w * g, h * g), interpolation=cv2.INTER_CUBIC)
                out = out + dl * _soft(new.astype(np.uint8), w, h, g)
            return np.clip(out, 0, 255).astype(np.uint8)
        _, from_anchor, from_shift, shift, new, near_anchor = picks(c, s)
        out = key_at(c, s, g).astype(np.float32)
        delta = cv2.resize(matched[(id(c), s)] - key_matched(c), (w * g, h * g), interpolation=cv2.INTER_CUBIC)
        if shift is not None:
            ws = _soft(from_shift.astype(np.uint8), w, h, g)
            out = out * (1 - ws) + _moved(out, shift, g) * ws
        a = anchor_of.get(id(c))
        if a is not None:
            wa = _soft((from_anchor | near_anchor).astype(np.uint8), w, h, g)
            A = cv2.resize(anchor_art[id(c)], (w * g, h * g), interpolation=cv2.INTER_AREA).astype(np.float32)
            out = out * (1 - wa) + A * wa
            da = cv2.resize(matched[(id(c), s)] - matched[(id(c), a)], (w * g, h * g), interpolation=cv2.INTER_CUBIC)
            delta = delta * (1 - wa) + da * wa
        return np.clip(out + delta * _soft(new.astype(np.uint8), w, h, g), 0, 255).astype(np.uint8)

    painted2 = {}
    if upscaled:
        painted2.update(paint(upscaled, derived_start, changed_from_default, settings["state_denoise"]))
    if heavy and not use_whole:
        painted2.update(paint(heavy, compose, new_in, settings["state_denoise"], ctx_fn=compose))
    if light and not use_whole:
        painted2.update(paint(light, compose, new_in, settings["anim_repaint"], ctx_fn=compose))
    if moving and not use_whole:
        painted2.update(paint(moving, derived_start, changed_from_key, settings["anim_repaint"]))

    panels = {"images": {}, "off": []}
    index = []
    for c in clusters:
        cx, cy, w, h = c.rect
        for s in c.states:
            if upscale_only:
                art = painted2[(id(c), s)]
            elif s == key_of.get(id(c)):
                art = key_art[id(c)]
            elif s == anchor_of.get(id(c)):
                art = anchor_art[id(c)]
            elif use_whole and (c.param in looping or layered(c, s) is None):
                art = whole_crop(c, s)
            elif c.param not in looping:
                art = compose(c, s, scale)
                if (id(c), s) in painted2:  # the repaint where the frame shows none of key, anchor or moved key
                    m2 = _soft(new_in(c, s), w, h, scale)
                    art = (art * (1 - m2) + painted2[(id(c), s)] * m2).astype(np.uint8)
            elif (id(c), s) in painted2:
                # the repaint where this state differs from its key, the key elsewhere
                m2 = _soft(changed_from_key(c, s), w, h, scale)
                art = (key_art[id(c)] * (1 - m2) + painted2[(id(c), s)] * m2).astype(np.uint8)
            else:
                art = key_art[id(c)]
            # only where this state changes the picture; the finished scene everywhere else
            panel = _merge(main_crop(c), art, changed_from_default(c, s), scale)
            fname = f"p{c.param}_{'off' if s == 0 else s}_{cx}_{cy}.png"
            Image.fromarray(panel).save(work_dir / fname)
            entry = {"param": c.param, "state": s, "rect": list(c.rect), "file": fname}
            if s == 0:
                entry["footprint"] = c.footprint
                panels["off"].append((c.rect, c.footprint, panel))
            else:
                panels["images"].setdefault((c.param, s), []).append((c.rect, panel))
            index.append(entry)

    (work_dir / "states.json").write_text(json.dumps({"scale": scale, "panels": index}))
    return panels


def load(work_dir, scale):
    """Panels saved by run(), if they match the current output scale."""
    f = Path(work_dir) / "states.json"
    if not f.exists():
        return None
    meta = json.loads(f.read_text())
    if meta["scale"] != scale:
        return None
    panels = {"images": {}, "off": []}
    for e in meta["panels"]:
        img = np.asarray(Image.open(Path(work_dir) / e["file"]).convert("RGB"))
        if e["state"] == 0:
            panels["off"].append((tuple(e["rect"]), [tuple(t) for t in e["footprint"]], img))
        else:
            panels["images"].setdefault((e["param"], e["state"]), []).append((tuple(e["rect"]), img))
    return panels
