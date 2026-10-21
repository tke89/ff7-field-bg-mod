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
from .sheets import PageCache, render_composite

MARGIN = 32           # context around a cluster's tiles, in original pixels (>= the 32px big tiles)
MAX_SHEET_MP = 2.2    # Flux works well up to about this size
MAX_SHEET_SIDE = 1792
MIN_SHEET_SIDE = 768  # tiny sheets are rendered larger so Flux has room to paint
KEY_MIN_CHANGE = 0.3  # share of a spot a state must change to get its own painted key frame


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
    others = {p: m for p, m in rendered_defaults.items() if p != cluster.param}

    def inc(t):
        if not t.param:
            return True
        if t.param == cluster.param:
            return bool(state & t.state)
        return bool(others.get(t.param, 0) & t.state)

    x, y, w, h = cluster.rect
    return np.asarray(render_composite(bg, rect, cache.tile, 1, inc))[y : y + h, x : x + w]


def _color(img, M):
    """The original's colors mapped to the new look (float)."""
    return apply_color(np.dstack([img, np.full(img.shape[:2], 255, np.uint8)]), M)[..., :3].astype(np.float32)


def _changed(view, default_view):
    """Pixels (1x) where a state's view differs from what the main render shows, grown by 1px."""
    diff = np.any(view != default_view, axis=2).astype(np.uint8)
    return cv2.dilate(diff, np.ones((3, 3), np.uint8))


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
    blending), so a repainted spot can't show as a lighter or darker patch."""
    h, w = change1x.shape
    m = cv2.dilate(change1x, np.ones((5, 5), np.uint8))  # edge in unchanged territory
    if not m.any():
        return base.copy()
    mh = cv2.resize(m * 255, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    mh[:2, :] = mh[-2:, :] = 0
    mh[:, :2] = mh[:, -2:] = 0
    x0, y0, bw, bh = cv2.boundingRect(mh)
    if bw < 3 or bh < 3:
        return base.copy()
    return cv2.seamlessClone(np.ascontiguousarray(art), np.ascontiguousarray(base), mh,
                             (x0 + bw // 2, y0 + bh // 2), cv2.NORMAL_CLONE)


def run(comfy, bg, rect, main, settings, style_refs, work_dir, progress=None, cancelled=lambda: False):
    """Restyle every state; returns panels for ai.build_pages and saves them under work_dir.

    Round 1 paints a key frame for each spot the main render doesn't show (a fan that's always
    spinning, a light switched on later): strongly, as it starts from original art (0.85 for the edit
    models, the field's repaint plus state_denoise_boost for the others). Round 2 derives every other
    state from its key (the main render itself where it shows the spot): it starts from the key plus
    what changes in the original (a needle moving, a door opening) and repaints only those pixels, at
    state_denoise, so frames stay one design. The Upscale workflow instead sends each state's original art through its upscaler (comfy
    may be None when that runs outside ComfyUI)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.glob("*.png"):
        old.unlink()
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

    if upscale_only:
        keys, derived = [], [(c, s) for c in clusters for s in c.states]
    else:
        keys, derived = _rounds(clusters, defaults)
        # Small changes to what the main render already shows (a needle on a gauge) are derived from it;
        # only states that replace most of a spot (a fan spinning in an empty window) get a key painted.
        painted_keys = []
        for c, key in keys:
            area = sum(t * t for _, _, t in c.footprint)
            (painted_keys if changed_from_default(c, key).sum() >= KEY_MIN_CHANGE * area else derived).append((c, key))
        keys = painted_keys
    key_of = {id(c): s for c, s in keys}
    key_art = {}  # id(cluster) -> painted key at output scale (main crop where the main render is the key)
    total = len(_pack_items(keys, k)) + len(_pack_items(derived, k))
    done = [0]

    def paint(items, start_fn, change_fn, denoise):
        """Render (cluster, state) panels; returns {(id(c), s): output-scale image}."""
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
                ctx[y * g : (y + h) * g, x * g : (x + w) * g] = cv2.resize(main_crop(c), (w * g, h * g), interpolation=cv2.INTER_AREA)
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
        art = cv2.resize(matched[(id(c), s)], (w * g, h * g), interpolation=cv2.INTER_CUBIC)
        m = _soft(changed_from_default(c, s), w, h, g)
        return np.clip(ctx * (1 - m) + art * m, 0, 255).astype(np.uint8)

    painted = paint(keys, key_start, changed_from_default, 0.85 if edit else None) if keys else {}
    for c in clusters:
        key = key_of.get(id(c))
        if key is None:
            key_art[id(c)] = main_crop(c)
        else:
            w, h = c.rect[2], c.rect[3]
            blend = _soft(changed_from_default(c, key), w, h, scale)
            key_art[id(c)] = (main_crop(c) * (1 - blend) + painted[(id(c), key)] * blend).astype(np.uint8)

    # Round 2: everything else, from its key plus the change in the original
    def key_view(c):
        key = key_of.get(id(c))
        return default_crop(c) if key is None else view(c, key)

    def key_matched(c):
        key = key_of.get(id(c))
        return _color(default_crop(c), M) if key is None else matched[(id(c), key)]

    def derived_start(c, s, g):
        w, h = c.rect[2], c.rect[3]
        view(c, s)
        base = cv2.resize(key_art[id(c)], (w * g, h * g), interpolation=cv2.INTER_AREA).astype(np.float32)
        delta = matched[(id(c), s)] - key_matched(c)
        return np.clip(base + cv2.resize(delta, (w * g, h * g), interpolation=cv2.INTER_CUBIC), 0, 255).astype(np.uint8)

    def changed_from_key(c, s):
        return _changed(view(c, s), key_view(c))

    todo = [(c, s) for c, s in derived if upscale_only or changed_from_key(c, s).any()]
    painted2 = paint(todo, derived_start, changed_from_default if upscale_only else changed_from_key,
                     settings["state_denoise"]) if todo else {}

    panels = {"images": {}, "off": []}
    index = []
    for c in clusters:
        cx, cy, w, h = c.rect
        for s in c.states:
            if upscale_only:
                art = painted2[(id(c), s)]
            elif s == key_of.get(id(c)):
                art = key_art[id(c)]
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
