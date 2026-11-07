"""Life in a field's 3D scene, found in its painting so it works in any field: smoke from chimneys, sparks and heat
shimmer over fires, lit windows and lanterns (candlelight flickering on its own and spilling onto the ground), the
sky drifting, flags in the wind, birds crossing the sky.

Qwen3.5 (the Studio's scene describer) points out chimneys, fires, lit windows, flags and the sky as boxes, once
per render (work/<field>/features.json; find); the painting refines them (a fire's hottest spot, the sky's edge, a
flag's pole) and the scene's depth puts each in the world:

  smoke, sparks, birds   FFNx-3d particle sources (atmos.particles writes them into <field>_particles.toml)
  sky, heat, flags       FFNx-3d's effects map, <field>_scene_fx.dds (R, G, B) and the scene_fx lighting keys
  windows, lanterns      the scene's glow (atmos.glow), each flickering on its own (atmos.flicker's frames)
"""
import json

import cv2
import numpy as np
from PIL import Image

from . import common, gltfout

# Asked of Qwen3.5 about the render, one question per kind (one long question found fewer of each)
FIND_MP = 1.6  # the render shown to it at about this size (chimneys are small)
_TAIL = ('Answer with JSON only, a list like [{"text": "%s", "bbox_2d": [x1, y1, x2, y2]}], with coordinates from 0 '
         "to 1000 relative to the image's width and height. If there are none, answer [].")
FIND = {
    "chimney": "List every chimney in this image: chimneys on roofs (brick, stone or metal, with or without pots on "
               "top) and the smokestacks of furnaces, forges or factories. Not towers, spires, poles, or pipes running "
               "along walls, and not tanks, boilers, domes, stills, vats or other machinery. Give a box around each whole "
               "chimney. " + _TAIL % "chimney",
    "fire": "List every fire or flame in this image: open fires, campfires, braziers, torches, fireplaces, stoves or "
            "furnaces with fire showing, forges, and burning lanterns or candles. Not electric lights, lamps, screens, "
            "glowing signs or lava. Give a box around the burning part of each, and as its text say which it is: fire, "
            "furnace, brazier, torch, fireplace, lantern or candle. " + _TAIL % "torch",
    "flag": "List every flag, banner or pennant in this image: cloth hanging from a pole, mast or rope that would move "
            "in the wind. Not signs, painted emblems, curtains or clothes. Give a box around the cloth of each. "
            + _TAIL % "flag",
    "window": "List every window in this image that is lit from inside, glowing with warm light (candles, lamps, a "
              "fire). Not dark windows, not walls, signs or emblems. Give a box around each. " + _TAIL % "lit window",
    "screen": "List every screen, monitor, display, control panel lit up with readouts, or glowing neon or lit sign in "
              "this image. Not windows, lamps or plain painted signs. Give a box around each. " + _TAIL % "screen",
    "tank": "List every tank, tube, cylinder or capsule filled with liquid in this image (specimen tanks, mako tanks, "
            "aquariums, glass vats). Not empty glass, pipes, barrels or machinery. Give a box around the liquid of each. "
            + _TAIL % "tank",
    "mist": "List every area of mist, fog, haze or steam lying in the air in this image (over water, the ground or a "
            "drop). Not clouds in the sky, smoke from chimneys or light beams. Give a box around each. " + _TAIL % "mist",
    "indicator": "List every small indicator light in this image: status LEDs, warning lamps, button lights and door "
                 "lights glowing on machines, consoles, panels and doors. Not lamps that light a room, screens, signs or "
                 "reflections. Give a small box around each. " + _TAIL % "indicator light",
    "beam": "List every beam or shaft of light in this image: sunlight or lamplight streaming through the air in visible "
            "rays. Not lamps, windows or glowing objects themselves. Give a box around each. " + _TAIL % "light beam",
    "pool": "List every area of water in this image: sea, lake, river, pool, pond, puddles, a fountain or a waterfall. "
            "Not blue walls, pipes, glass, metal, floors, sky or light. Give a box around each. " + _TAIL % "water",
    "sky": "Is open sky visible in this image (outdoors, above or beyond the scenery)? If so, give one box around all "
           "of the visible sky. " + _TAIL % "sky" + " Also answer [] indoors, underground, or when only walls, "
           "ceilings, ground or water are seen.",
}
INDOORS = ("Is this scene indoors: inside a building, a room, a cave, a tunnel or a vehicle? Answer with one word, "
           "yes or no.")
WATER = ("Is there any water in this scene: a sea, lake, river, pool, puddles, a fountain or a waterfall? Answer with "
         "one word, yes or no.")  # the colour test for water (build.water_mask) also takes teal floors and glass
POLISHED = ("Is the floor people walk on in this scene polished or glossy (marble, polished stone, shiny metal plate, "
            "lacquered wood or wet), so it would show reflections of people standing on it? Answer with one word, yes "
            "or no.")
EVENING = ("Is the light in this scene daylight, or is it evening, sunset, dusk or night? Answer with one word, day or "
           "evening.")  # candlelit windows only in the evening and at night
# Then each thing found is looked at again on its own, outlined in red with its surroundings: the first answers
# include look-alikes (Bone Village's tent sides as chimneys, Wall Market's posters as flags, counters as windows)
CHECK = {
    "chimney": "Is the thing outlined in red a chimney, stovepipe or smokestack, that smoke from a fire comes out of? "
               "Not a pot, jar, tank, vent, post or pipe along a wall. Answer with one word, yes or no.",
    "fire": "What is outlined in red? Answer with one word: fire (real flames: a campfire, brazier, torch, fireplace or "
            "furnace fire), lamp (a lantern, candle, lamp or glowing light), or no (anything else).",
    "flag": "Is the thing outlined in red a flag, banner or pennant flying from a pole, mast, spire or rope outdoors? "
            "Not a sign, poster or emblem fixed to a wall, and not a curtain. Answer with one word, yes or no.",
    "window": "Is the thing outlined in red a window of a building or vehicle? Answer with one word, yes or no.",
    "screen": "Is the thing outlined in red a lit screen, monitor, display or glowing sign? Answer with one word, yes or "
              "no.",
    "tank": "Is the thing outlined in red a glass tank or tube full of liquid, with fluid you can see through its walls "
            "(a specimen tank, a mako tank, an aquarium)? Not plants or objects in glass cases, not a pool, pond or "
            "basin of water, not a display case, vehicle, machine, lift or elevator. Answer with one word, yes or no.",
    "indicator": "Is the thing outlined in red a small electric indicator light that is glowing: an LED, a status lamp or a "
                 "button light on a machine, console, panel or door? Not a gauge or dial, a knob, a post cap, a light "
                 "fitting or a reflection. Answer with one word, yes or no.",
    "pool": "Is the area outlined in red water: a pool, pond, river, sea, puddle, fountain or waterfall? Not a blue "
            "wall, pipe, glass or metal surface. Answer with one word, yes or no.",
    "mist": "Is the area outlined in red filled with mist, fog, haze or steam hanging in the air? Not just a pale or "
            "brightly lit surface. Answer with one word, yes or no.",
}
CHECK_PX = 448  # each one shown at about this size
QUESTIONS = {"indoors": INDOORS, "evening": EVENING, "water": WATER, "polished": POLISHED}
FIRES = ("fire", "furnace", "brazier", "fireplace", "forge", "campfire", "bonfire", "stove")  # sparks and heat
SMALL_FIRES = ("torch",)            # a few sparks, a little heat
FLAMES = ("lantern", "candle")      # light only

# Sizes and speeds in background pixels (an original pixel) where the thing is, so they look the same in any field
SMOKE = {"per": 30, "size": 2.4, "rise": 4.5, "wind": 2.0, "life": (5.0, 9.0), "grow": 1.8, "alpha": 0.8}
SPARKS = {"per": 30, "size": 1.1, "rise": 22.0, "life": (0.6, 1.5), "alpha": 1.0, "color": (1.0, 0.72, 0.32)}
BIRDS = {"flock": 7, "size": 1.6, "fly": 20.0, "every": (12.0, 30.0), "alpha": 0.9, "depth": 0.9}
WIND_SCREEN = (1.0, 0.0)  # smoke drifts and the sky moves this way across the picture
SKY_MAX = 5.0     # FFNx-3d's scene_fx at 100% (0.6% of the picture per 9 s cycle at 1.0: at 2, too slow to notice)
HEAT_MAX = 2.0
FLAG_MAX = 2.0
SKY_UP = 0.5       # sky: no more upward-facing than this (Marigold's normals); ground beyond a wall is
SKY_DEPTH = 0.93   # sky: no nearer than this share of the depth at the top of its column
SKY_SHIFT = 0.006  # the shader's sky drift per cycle at scene_fx 1 (share of the picture: FFNx.lighting.frag)
SKY_DETAIL = 24    # sky_scenery: a pixel this much lighter or darker than its surroundings (any channel) is detail
SKY_LINE = 15      # ...a straight edge at least this long (background px) is structure
SKY_STAND = 12     # ...a group standing on what's below the sky at least this tall (background px) is scenery
SKY_FLOOR = 0.04   # the effects map's R over all the sky, drifting or not (FFNx.lighting.frag: no cloud shadows there)
# windows: emission over the painting's own colour, their light on the ground (background px, at the window)
WINDOW_EMIT = 1.2
WINDOW_LIT = 0.03  # a window box lit less than this on average (pane_light) is dark glass or a wall: none
# fields whose dark glass gets candlelight anyway, behind every pane Qwen found (the look before 2026-11-03): Kalm's
# blue windows lit warm (user, 2026-11-03: "we can keep it")
CANDLELIT_GLASS = {"elm"}
SPILL_PX = 22.0
SPILL = 0.45
SCREEN_EMIT = 0.35  # screens and indicator lights: extra glow over what the painting has
SCREEN_V = (0.35, 0.6)  # screens: a pixel this bright (max channel) glows, all of it from the second
SCREEN_EDGE = 0.2   # screens: their glow fades out over this share of the box's shorter side at its edges
SCREEN_GAIN = 2.0   # screens: shaped by the painting, a box's glow up to this much brighter where it's brightest
LED_EMIT = 1.5
LEDS = 120
MOTES = {"per": 60, "size": 0.45, "alpha": 0.9, "spread": 3.0, "drift": 1.2, "life": (6.0, 12.0)}
MIST = {"per": 24, "size": 16.0, "alpha": 0.12, "spread": 10.0, "wind": 1.5, "grow": 1.6, "life": (14.0, 26.0)}
BUBBLES = {"per": 10, "size": 0.45, "alpha": 0.8, "spread": 0.25, "rise": 6.0}
# the scene's lights on the characters: reach (character heights), power, flicker, by kind
LAMP = {"fire": (4.0, 5.0, 0.8), "lantern": (2.5, 3.0, 0.5), "window": (2.0, 1.5, 0.3), "screen": (1.6, 1.4, 0.05),
        "tank": (2.0, 2.0, 0.0), "lamp": (2.5, 2.5, 0.0)}
REFLECT = (0.7, 0.3)  # water, polished floors at 100%
CARPET_SAT = (0.7, 0.85)  # a floor painted this saturated (HSV) is a carpet or rug: never polished (blin60_2's red
                          # carpet: 0.98 across its walkable floor; the polished floors Qwen named: 0.11-0.59)
SPILL_DARK = 16       # lights(): no spill on painting this dark (sRGB luma), all of it from twice this
WATER_REACH = 80      # water_level: water at most this far (background px) below a floor's edge on screen
FLICKER_FRAMES = 16  # glow frames, played in order at GLOW_FPS (FFNx-3d glow_fps): a 2 s loop
GLOW_FPS = 8
CANDLE = np.array([1.0, 0.55, 0.22], np.float32)  # candlelight, linear


def _key(f):
    return f"{f.version} {f.raw_render.stat().st_mtime}"


def _find_workflow(image):
    from ff7bg.comfy import CAPTION_CLIP
    clip, clip_type = CAPTION_CLIP
    wf = {"img": {"class_type": "LoadImage", "inputs": {"image": image}},
          "small": {"class_type": "ImageScaleToTotalPixels", "inputs": {
              "image": ["img", 0], "upscale_method": "lanczos", "megapixels": FIND_MP, "resolution_steps": 32}},
          "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": clip_type}}}
    for k, ask in {**FIND, **QUESTIONS}.items():
        wf[f"gen_{k}"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": ask, "max_length": 700 if k in FIND else 8, "sampling_mode": "off",
            "image": ["small", 0], "thinking": False}}
        wf[f"show_{k}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"gen_{k}", 0]}}
    return wf


def _check_workflow(items):
    """One question per (image, question): items [(uploaded name, question)]; answers at show_<i>."""
    from ff7bg.comfy import CAPTION_CLIP
    clip, clip_type = CAPTION_CLIP
    wf = {"clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": clip_type}}}
    for i, (image, ask) in enumerate(items):
        wf[f"img_{i}"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        wf[f"gen_{i}"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": ask, "max_length": 6, "sampling_mode": "off", "image": [f"img_{i}", 0],
            "thinking": False}}
        wf[f"show_{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"gen_{i}", 0]}}
    return wf


def _check(f, boxes, render):
    """Each box of the kinds in CHECK looked at again, outlined in red among its surroundings; what isn't one
    goes. Fires are sorted into fires and lamps (their text). Returns (boxes, answers)."""
    from . import comfy
    folder = f.work / "check"
    folder.mkdir(exist_ok=True)
    items, refs = [], []
    for kind in CHECK:
        for j, b in enumerate(boxes.get(kind, [])):
            bw, bh = b["x2"] - b["x1"], b["y2"] - b["y1"]
            m = 0.7 * max(bw, bh) + 12 * f.scale
            X1, Y1 = int(max(0, b["x1"] - m)), int(max(0, b["y1"] - m))
            X2, Y2 = int(min(f.RW, b["x2"] + m)), int(min(f.RH, b["y2"] + m))
            crop = np.ascontiguousarray(render[Y1:Y2, X1:X2])
            k = CHECK_PX / max(crop.shape[:2])
            crop = cv2.resize(crop, (max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                              interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
            cv2.rectangle(crop, (int((b["x1"] - X1) * k) - 2, int((b["y1"] - Y1) * k) - 2),
                          (int((b["x2"] - X1) * k) + 2, int((b["y2"] - Y1) * k) + 2), (255, 0, 0), 2)
            path = folder / f"{kind}_{j}.png"
            Image.fromarray(crop).save(path)
            items.append((comfy.upload(path, f"{f.name}_{kind}_{j}.png"), CHECK[kind]))
            refs.append((kind, j))
    if not items:
        return boxes, {}
    outs = comfy.run(_check_workflow(items))
    said = {ref: "".join(outs.get(f"show_{i}", {}).get("text", [])).strip().lower() for i, ref in enumerate(refs)}
    out = {k: list(v) for k, v in boxes.items()}
    for kind in CHECK:
        kept = []
        for j, b in enumerate(boxes.get(kind, [])):
            a = said.get((kind, j), "")
            if kind == "fire":
                if a.startswith("fire"):
                    kept.append(b if any(x in b["text"] for x in FIRES + SMALL_FIRES) else {**b, "text": "fire"})
                elif a.startswith("lamp"):  # a furnace's glowing mouth looks like a lamp up close: the first look
                    burning = any(x in b["text"] for x in ("furnace", "forge"))  # (with the scene) decides; a
                    kept.append(b if burning else {**b, "text": "lantern"})    # "fireplace" in an office is a lamp
            elif a.startswith("yes"):
                kept.append(b)
        out[kind] = kept
    return out, {f"{k} {j}": a for (k, j), a in said.items()}


def _boxes(text, w, h, biggest=0.4):
    """The answer -> [{x1, y1, x2, y2, text}] in render pixels, each thing once."""
    import re
    from ff7bg.signs import _marching, _overlap
    raw = []
    for chunk in re.findall(r"\{[^{}]*", text):  # one {...} at a time (an answer can stop half way)
        b = re.search(r'"bbox_2d"\s*:\s*\[([^\]]*)\]', chunk)
        if not b:
            continue
        t = re.search(r'"(?:text|label)"\s*:\s*"((?:[^"\\]|\\.)*)"', chunk)
        b, label = b.group(1).split(","), (t.group(1) if t else "")
        try:
            x1, y1, x2, y2 = (min(1000.0, max(0.0, float(v))) for v in b)
        except ValueError:
            continue
        raw.append(((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), label))
    looped = {j for i in range(2, len(raw)) if _marching(*(r[0] for r in raw[i - 2:i + 1])) for j in (i - 1, i)}
    out = []
    for i, ((x1, y1, x2, y2), label) in enumerate(raw):
        box = {"x1": x1 / 1000 * w, "y1": y1 / 1000 * h, "x2": x2 / 1000 * w, "y2": y2 / 1000 * h}
        bw, bh = box["x2"] - box["x1"], box["y2"] - box["y1"]
        if i in looped or bw < 3 or bh < 3 or bw * bh > biggest * w * h:
            continue
        if any(_overlap(box, o) > 0.6 for o in out):
            continue
        out.append({**{k: round(v, 1) for k, v in box.items()}, "text": str(label).strip().lower()[:40]})
    return out


def recheck(f):
    """Find again from Qwen's first answers (kept in features.json) with today's checks: no new first look."""
    path = f.work / "features.json"
    d = json.loads(path.read_text())
    answers = d["answers"]
    boxes = {k: _boxes(answers.get(k, ""), f.RW, f.RH, biggest=1.0 if k in ("sky", "mist", "beam", "pool") else 0.15)
             for k in FIND}
    scene = d["boxes"]["scene"]
    if scene["indoors"]:
        boxes.update(sky=[], flag=[], chimney=[], window=[])
    boxes, checked = _check(f, boxes, _render(f))
    boxes["scene"] = scene
    d.update(boxes=boxes, checked=checked)
    path.write_text(json.dumps(d, indent=1))
    return boxes


def find(f, force=False):
    """Chimneys, fires, lit windows, flags and the sky in the field's render, as boxes in render pixels: {kind: [box]}. Asked
    once per render (ComfyUI must be running), then kept in work/<field>/features.json."""
    path = f.work / "features.json"
    if not force and path.exists():
        d = json.loads(path.read_text())
        if d.get("source") == _key(f) and not stale(d["boxes"]):
            return d["boxes"]
    from . import comfy
    outs = comfy.run(_find_workflow(comfy.upload(f.render, f"{f.name}_find.png")))
    answers = {k: "".join(outs.get(f"show_{k}", {}).get("text", [])) for k in (*FIND, *QUESTIONS)}
    boxes = {k: _boxes(answers[k], f.RW, f.RH, biggest=1.0 if k in ("sky", "mist", "beam", "pool") else 0.15)
             for k in FIND}
    first = {k: len(v) for k, v in boxes.items()}
    indoors = answers["indoors"].strip().lower().startswith("yes")
    if indoors:  # no sky, wind or chimney smoke inside; its windows show the light outside, not candles
        boxes.update(sky=[], flag=[], chimney=[], window=[])
    boxes, checked = _check(f, boxes, _render(f))
    boxes["scene"] = {"indoors": indoors, "evening": answers["evening"].strip().lower().startswith("even"),
                      "water": not answers["water"].strip().lower().startswith("no"),
                      "polished": answers["polished"].strip().lower().startswith("yes")}
    path.write_text(json.dumps({"source": _key(f), "boxes": boxes, "first": first, "answers": answers,
                                "checked": checked}, indent=1))
    return boxes


def stale(boxes):
    """Found before a question was added: ask again."""
    return not boxes or not {*FIND, "scene"} <= set(boxes) or not set(QUESTIONS) <= set(boxes["scene"])


def has_water(f):
    """Whether the scene has water (Qwen's answer; unknown: yes, the colour test decides)."""
    return (cached(f).get("scene") or {}).get("water", True)


def water(f, render):
    """Where the scene's water is, 0..1 at render size: build.water_mask's colour test, but only inside the water
    Qwen found and confirmed (smkin_4's blue-lit reactor walls passed the colour test and Qwen's yes/no "any water?":
    Cloud was mirrored on them). Found before Qwen was asked for water boxes: its yes/no."""
    key = (f.name, _key(f), render.shape)
    if key not in _WATER:
        _WATER.clear()
        _WATER[key] = _water(f, render)
    return _WATER[key]


_WATER = {}


def _under_floor(f, shape):
    """Where the scene stands just under a walkable surface (within 2 character heights below it, straight down:
    the sides of stepping stones, ledges, quays), at `shape`. A top-down map of the walkmesh's height, cells of
    about a tenth of a character."""
    if not (f.work / "simplify_in.npz").exists():
        return np.zeros(shape, bool)
    Z, step = _depth(f)
    from . import depth
    bx, by = depth.grid_bg(f, step)
    gh, gw = Z.shape
    _, P = f.unproject(bx[:gh, :gw], by[:gh, :gw], np.where(np.isfinite(Z), Z, 1.0))
    ch = _char_height(f)
    cell = ch / 10
    lo = f.tris[..., :2].reshape(-1, 2).min(0) - cell
    n = np.ceil((f.tris[..., :2].reshape(-1, 2).max(0) + cell - lo) / cell).astype(int) + 1
    top = np.full((n[1], n[0]), -np.inf, np.float32)
    for t in f.tris:
        poly = np.round((t[:, :2] - lo) / cell).astype(np.int32)
        m = np.zeros_like(top, np.uint8)
        cv2.fillPoly(m, [poly], 1)
        top[m > 0] = np.maximum(top[m > 0], float(t[:, 2].max()))
    top = cv2.dilate(np.where(np.isfinite(top), top, -1e9).astype(np.float32), np.ones((3, 3), np.uint8))
    ix = np.clip(((P[..., 0] - lo[0]) / cell).astype(int), 0, n[0] - 1)
    iy = np.clip(((P[..., 1] - lo[1]) / cell).astype(int), 0, n[1] - 1)
    floor_z = top[iy, ix]
    under = np.isfinite(Z) & (floor_z > -1e8) & (P[..., 2] <= floor_z + 10) & (P[..., 2] >= floor_z - 2 * ch)
    return cv2.resize(under.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST) > 0


def _water(f, render):
    from .build import water_mask
    feats = cached(f)
    m = water_mask(render)
    if "pool" not in feats:
        return m * has_water(f)
    if not feats["pool"]:
        return np.zeros(m.shape, np.float32)
    # inside the water Qwen confirmed: the colour test's water, grown by colour (GrabCut) over the rest of it (a
    # mirror pool's water is the colour of what it reflects: ancnt1's purple, 0.6% passed the test), never over the
    # walkable floor
    q = 4
    small = cv2.resize(render, (f.RW // q, f.RH // q), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    gc = np.full((sh, sw), cv2.GC_BGD, np.uint8)
    for b in feats["pool"]:
        gc[int(b["y1"] / q):int(np.ceil(b["y2"] / q)), int(b["x1"] / q):int(np.ceil(b["x2"] / q))] = cv2.GC_PR_FGD
    inside = gc == cv2.GC_PR_FGD
    sure = cv2.resize(m, (sw, sh), interpolation=cv2.INTER_AREA) > 0.6
    gc[inside & sure] = cv2.GC_FGD
    floor = np.zeros((sh, sw), np.uint8)
    s, z = f.project(f.tris.reshape(-1, 3))
    tri = (((s - [f.mx, f.my]) * f.scale / q).reshape(-1, 3, 2)).round().astype(np.int32)
    ok = (z.reshape(-1, 3) > 1).all(1)
    cv2.fillPoly(floor, list(tri[ok]), 1)
    gc[inside & (floor > 0)] = cv2.GC_BGD
    gc[inside & _under_floor(f, (sh, sw))] = cv2.GC_BGD  # a stepping stone's side isn't water
    out = (inside & sure & (gc != cv2.GC_BGD)).astype(np.float32)
    if (gc == cv2.GC_FGD).sum() >= 20:
        try:
            cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), gc, None, np.zeros((1, 65)), np.zeros((1, 65)), 4,
                        cv2.GC_INIT_WITH_MASK)
            out = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.float32)
        except cv2.error:
            pass
    out = cv2.morphologyEx(out, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return np.clip(cv2.GaussianBlur(cv2.resize(out, (f.RW, f.RH), interpolation=cv2.INTER_LINEAR), (0, 0), f.scale), 0, 1)


def cached(f):
    """What find found for this render, without asking ({} when it hasn't been asked: a look switch never waits)."""
    path = f.work / "features.json"
    if path.exists():
        d = json.loads(path.read_text())
        if d.get("source") == _key(f):
            return d["boxes"]
    return {}  # (asked before windows were: the rest still counts)


# --- where things are ---

def _depth(f):
    d = np.load(f.work / "simplify_in.npz")
    Z = d["Z"]
    return Z, max(1, round(f.RW / Z.shape[1]))


def _world(f, rx, ry, z):
    bx, by = f.to_bg(np.asarray(rx, float), np.asarray(ry, float))
    return f.unproject(bx, by, np.asarray(z, float))[1]


def _near(Z, step, x1, y1, x2, y2, q=5):
    """The near end of the depth in a render-pixel box (the thing, not what's behind it)."""
    gh, gw = Z.shape
    gx1, gy1 = int(np.clip(x1 // step, 0, gw - 1)), int(np.clip(y1 // step, 0, gh - 1))
    gx2, gy2 = int(np.clip(np.ceil(x2 / step), gx1 + 1, gw)), int(np.clip(np.ceil(y2 / step), gy1 + 1, gh))
    patch = Z[gy1:gy2, gx1:gx2]
    patch = patch[np.isfinite(patch)]
    return float(np.percentile(patch, q)) if patch.size else float(np.nanmedian(Z))


def _world_size(f, px, z):
    """Background pixels -> world units at camera depth z."""
    return float(px) * float(z) / f.zoom


def _screen_dir(f, sx, sy):
    """A direction across the picture -> level world direction."""
    d = np.asarray(f.R, float).T @ np.array([sx, sy, 0.0])
    d[2] = 0
    return d / max(float(np.linalg.norm(d)), 1e-6)


def _render(f):
    return np.asarray(Image.open(f.render).convert("RGB"))


# --- where the scene is (F23) ---

VOID_DARK = 16      # void: the render this dark (max channel) where the original shows nothing (no tile, or pure black)
VOID_OPEN = 0.02    # ...in patches this wide at least (share of the canvas's shorter side: not a gap between shelves)
VOID_SHARE = 0.01   # ...joined to the canvas's edge, in pieces this big at least (share of the canvas)
FRAMED_VOID = 0.15  # framed: the black around the scene this much of the canvas at least, on all four sides of it,
FRAMED_FILL = 0.92  # ...the scene filling its bounding box this much (a picture's rectangle),
FRAMED_SCREEN = 0.94  # ...and smaller than the screen (320 x 240) this much each way: black on screen all round it
                      # (rcktin7's rocket, one screen of a wide black canvas, is a scene)
_VOID = {}


def void(f):
    """The black around the scene, at 1x (bool, canvas size): where the original shows nothing (no tile, or pure black)
    and the render is near-black too, in wide pieces (an opening VOID_OPEN across) joined to the canvas's edge and at
    least VOID_SHARE of it. A cut-away room's outside (niv_ti1, mds7pb_1, snmin2's corner), the dark around Gaea's
    Cliff's caves (gaiin_1), a framed photograph's border (nivl_e3). Shadows and the gaps between the mansion library's
    shelves (sininb41: pure black in the original too) are narrower, or closed in: they stay scene."""
    key = (f.name, str(f.render), f.raw_render.stat().st_mtime)
    if key not in _VOID:
        from ff7bg.sheets import PageCache, render_composite
        cache = PageCache(f.bg)
        cov = np.zeros((f.h, f.w), bool)
        for t in f.bg.tiles:
            x, y, s = t.dst_x - f.mx, t.dst_y - f.my, t.size
            cov[y:y + s, x:x + s] |= cache.tile(t)[..., 3][:f.h - y, :f.w - x] > 0
        orig = np.asarray(render_composite(f.bg, (f.mx, f.my, f.w, f.h), cache.tile)).max(2)
        art = cv2.resize(_render(f), (f.w, f.h), interpolation=cv2.INTER_AREA).max(2)
        dark = (((orig == 0) | ~cov) & (art <= VOID_DARK)).astype(np.uint8)
        r = max(3, int(round(min(f.w, f.h) * VOID_OPEN)))
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
        edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]).tolist())) - {0}
        _VOID.clear()
        _VOID[key] = np.isin(labels, [k for k in edge if stats[k, cv2.CC_STAT_AREA] >= VOID_SHARE * f.w * f.h])
    return _VOID[key]


def framed(f):
    """Is the scene a picture framed on black (nivl_e3: the photograph of Nibelheim in the middle of the screen)? The
    black around it (void) is at least FRAMED_VOID of the canvas, on all four sides of it, it fills its bounding
    box (FRAMED_FILL) and it is smaller than the screen (FRAMED_SCREEN). Life is for scenes, not for pictures of them:
    Qwen took the photo for a real town (smoke and sparks rose from its painted chimneys past its edge into the
    black). Of the 670 built fields only nivl_e3 is one (2026-11-03)."""
    v = void(f)
    if v.mean() < FRAMED_VOID:
        return False
    ys, xs = np.nonzero(~v)
    if not len(xs):
        return False
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    if x0 == 0 or y0 == 0 or x1 == f.w or y1 == f.h:
        return False
    if x1 - x0 > FRAMED_SCREEN * 320 or y1 - y0 > FRAMED_SCREEN * 240:
        return False
    return float((~v[y0:y1, x0:x1]).mean()) >= FRAMED_FILL


def _fmt(v, nd=1):
    return round(float(v), nd)


# --- smoke, sparks, birds (particle sources) ---

def chimney_tops(f, boxes, Z, step):
    """Each chimney's top middle in the world (smoke comes out there)."""
    pts = []
    for b in boxes:
        w, h = b["x2"] - b["x1"], b["y2"] - b["y1"]
        if h < 0.3 * w or w > 0.08 * f.RW:  # flat slabs and whole roofs aren't chimneys
            continue
        z = _near(Z, step, b["x1"] + 0.25 * w, b["y1"], b["x2"] - 0.25 * w, b["y1"] + 0.3 * h)
        pts.append((_world(f, (b["x1"] + b["x2"]) / 2, b["y1"] + 0.05 * h, z), z))
    return pts


def _smoke_colour(f, render, boxes):
    """Smoke lit like the air around the chimneys: their surroundings' colour, greyed, never black or white."""
    cols = []
    for b in boxes:
        w, h = b["x2"] - b["x1"], b["y2"] - b["y1"]
        x1, x2 = int(max(0, b["x1"] - w)), int(min(f.RW, b["x2"] + w))
        y1, y2 = int(max(0, b["y1"] - 3 * h)), int(max(1, b["y1"]))
        if x2 > x1 and y2 > y1:
            cols.append(np.median(render[y1:y2, x1:x2].reshape(-1, 3), 0) / 255)
    c = np.mean(cols, 0) if cols else np.full(3, 0.5)
    lum = float(c @ [0.299, 0.587, 0.114])
    grey = np.clip(lum * 0.5 + 0.12, 0.3, 0.45)  # darker than most: it shows over pale walls and streets
    c = 0.35 * c / max(lum, 1e-3) * grey + 0.65 * grey
    return [round(float(v), 3) for v in np.clip(c, 0, 1)]


def smoke_source(f, feats, Z, step, amount, render):
    tops = chimney_tops(f, feats.get("chimney", []), Z, step)
    if not tops or amount <= 0:
        return None, 0
    z = float(np.median([t[1] for t in tops]))
    wind = _screen_dir(f, *WIND_SCREEN) * _world_size(f, SMOKE["wind"], z)
    return {"kind": "smoke", "points": np.array([t[0] for t in tops]),
            "count": int(max(len(tops), round(SMOKE["per"] * len(tops) * amount))),
            "size": _world_size(f, SMOKE["size"], z), "alpha": SMOKE["alpha"],
            "color": _smoke_colour(f, render, feats.get("chimney", [])),
            "spread": _world_size(f, 0.6, z), "rise": _world_size(f, SMOKE["rise"], z), "wind": [*wind[:2], 0.0],
            "grow": SMOKE["grow"], "life": list(SMOKE["life"])}, len(tops)


def _hot_point(render, b):
    """The hottest spot of a fire's box (its brightest warm pixels), render pixels."""
    x1, y1, x2, y2 = (int(round(b[k])) for k in ("x1", "y1", "x2", "y2"))
    patch = render[y1:y2, x1:x2]
    if patch.size == 0:
        return (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, s, v = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    heat = v * (s > 0.25) * ((hue < 60) | (hue > 345))
    if heat.max() <= 0.3:
        return (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
    ys, xs = np.nonzero(heat >= np.percentile(heat[heat > 0], 85))
    return x1 + float(xs.mean()), y1 + float(ys.mean())


def _heat(render, b):
    """How fire-like a box is: its brightest warm pixels' brightness times their saturation (0..1)."""
    x1, y1, x2, y2 = (int(round(b[k])) for k in ("x1", "y1", "x2", "y2"))
    p = render[y1:y2, x1:x2]
    if p.size == 0:
        return 0.0
    hsv = cv2.cvtColor(p, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, s, v = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    warm = ((hue < 60) | (hue > 345)) * v * s
    return float(np.percentile(warm, 95))


def fires(f, feats, Z, step, render):
    """[(kind, box, hot point (render px), world point, depth)] of the fires found. Fires close together (a
    furnace's mouth and its glowing gauges) are one: the hottest burns, the rest only glow (as lanterns)."""
    out = []
    boxes = sorted(feats.get("fire", []), key=lambda b: -_heat(render, b))
    kept = []
    for b in boxes:
        kind = next((k for k in FIRES + SMALL_FIRES + FLAMES if k in b.get("text", "")), "fire")
        w, h = b["x2"] - b["x1"], b["y2"] - b["y1"]
        cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        near = any(abs(cx - kx) < 1.5 * (w + kw) and abs(cy - ky) < 1.5 * (h + kh) for kx, ky, kw, kh in kept)
        if near and kind not in FLAMES:
            kind = "lantern"
        kept.append((cx, cy, w, h))
        hx, hy = _hot_point(render, b)
        z = _near(Z, step, hx - 0.2 * w, hy - 0.2 * h, hx + 0.2 * w, hy + 0.2 * h, q=20)
        out.append((kind, b, (hx, hy), _world(f, hx, hy, z), z))
    return out


def spark_source(f, found, amount):
    burning = [(k, b, p, P, z) for k, b, p, P, z in found if k not in FLAMES]
    if not burning or amount <= 0:
        return None
    z = float(np.median([x[4] for x in burning]))
    per = [SPARKS["per"] if k in FIRES else 5 for k, *_ in burning]
    # a fire's points repeated by its share of the sparks (the engine shares them out evenly over points)
    pts = np.concatenate([np.repeat(P[None], max(1, round(n / 5)), 0) for (_, _, _, P, _), n in zip(burning, per)])
    spread = np.median([(b["x2"] - b["x1"]) / f.scale for _, b, *_ in burning]) * 0.25
    return {"kind": "sparks", "points": pts, "count": int(max(4, round(sum(per) * amount))),
            "size": _world_size(f, SPARKS["size"], z), "alpha": SPARKS["alpha"], "color": list(SPARKS["color"]),
            "spread": _world_size(f, spread, z), "rise": _world_size(f, SPARKS["rise"], z),
            "life": list(SPARKS["life"])}


def bird_source(f, sky, Z, step, amount, render):
    """Flocks now and then between points of the open sky (a little in front of it), far away and small."""
    if sky is None or amount <= 0:
        return None
    hh, hw = sky.shape
    pts, zs = [], []
    for gx in range(12):  # low over the skyline, across the picture: the camera follows the characters, and the
        x0 = gx * hw // 12  # top of a tall field's sky is only seen from its highest spots (Kalm)
        ys, xs = np.nonzero(sky[:, x0:(gx + 1) * hw // 12] > 0.6)
        if len(xs) < 20:
            continue
        for q in (0.65, 0.85):
            k = int(np.argmin(np.abs(ys - np.percentile(ys, q * 100))))
            rx, ry = (x0 + xs[k]) * 2, ys[k] * 2
            z = Z[min(int(ry // step), Z.shape[0] - 1), min(int(rx // step), Z.shape[1] - 1)]
            if np.isfinite(z):
                z = float(z) * BIRDS["depth"]
                pts.append(_world(f, rx, ry, z))
                zs.append(z)
    if len(pts) < 3:
        return None
    z = float(np.median(zs))
    lo, hi = BIRDS["every"]
    sky_rgb = np.median(render[cv2.resize(sky, (f.RW, f.RH)) > 0.6].reshape(-1, 3), 0) / 255
    return {"kind": "birds", "points": np.array(pts), "count": BIRDS["flock"], "size": _world_size(f, BIRDS["size"], z),
            "alpha": BIRDS["alpha"], "color": [round(float(v), 3) for v in sky_rgb * 0.22],
            "rise": _world_size(f, BIRDS["fly"], z), "every": [lo / amount, hi / amount]}


# --- the sky, heat and flags (the effects map, at half size) ---

def sky_mask(f, feats, Z, step, render, shift):
    """The open sky, 0..1 at half size, eroded `shift` half pixels from its edge (the drift never pulls in a roof),
    or None. Inside Qwen's box, split from the scenery by colour (GrabCut), with the nearer depths and anything not
    joined to the box's top as scenery."""
    boxes = feats.get("sky", [])
    if not boxes:
        return None
    b = max(boxes, key=lambda b: (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]))
    q = 4  # GrabCut at a quarter of the render
    small = cv2.resize(render, (f.RW // q, f.RH // q), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    pad = 0.04
    x1, y1 = int(max(0, (b["x1"] / f.RW - pad) * sw)), int(max(0, (b["y1"] / f.RH - pad) * sh))
    x2, y2 = int(min(sw, (b["x2"] / f.RW + pad) * sw)), int(min(sh, (b["y2"] / f.RH + pad) * sh))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    Zs = cv2.resize(np.nan_to_num(Z, nan=float(np.nanmax(Z))).astype(np.float32), (sw, sh), interpolation=cv2.INTER_LINEAR)
    m = np.full((sh, sw), cv2.GC_BGD, np.uint8)
    m[y1:y2, x1:x2] = cv2.GC_PR_FGD
    inside = Zs[y1:y2, x1:x2]
    near = inside < np.percentile(inside, 35)
    m[y1:y2, x1:x2][near] = cv2.GC_PR_BGD
    # clearly nearer than the sky at the top of its column: scenery for sure (a wall's top inside Qwen's box)
    top = np.median(Zs[y1:y1 + 3, x1:x2], 0)
    m[y1:y2, x1:x2][inside < SKY_DEPTH * top[None, :]] = cv2.GC_BGD
    try:
        cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), m, None, np.zeros((1, 65)), np.zeros((1, 65)), 4,
                    cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    fg = ((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)).astype(np.uint8)
    n, labels = cv2.connectedComponents(fg)
    top = set(np.unique(labels[max(0, y1):max(0, y1) + 2, x1:x2])) | set(np.unique(labels[0, :]))
    keep = np.isin(labels, [k for k in top if k > 0]).astype(np.uint8)
    keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if keep.sum() < 0.01 * sh * sw:
        return None
    half = (f.RW // 2, f.RH // 2)
    hard = (cv2.resize(keep.astype(np.float32), half, interpolation=cv2.INTER_LINEAR) > 0.5).astype(np.uint8)
    # ground seen from above isn't sky, whatever its colour (Kalm's red plains beyond the wall): the sky faces the
    # camera (Marigold's normals: up near 0), ground faces up
    from .build import marigold_normals
    up = marigold_normals(f, (sh, sw))[..., 2][keep > 0]
    if up.size and float(np.median(up)) > SKY_UP:
        return None
    # what stands in front of the sky that the quarter-size cut took for sky (masts, rigging, a white ship among
    # white clouds): holes, so the drift fades out before it reaches them, as at the sky's edge
    img = cv2.resize(render, half, interpolation=cv2.INTER_AREA)
    hard[cv2.dilate(sky_scenery(img, hard, f.scale / 2).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = 0
    # the picture's edge on the side the drift pulls from (WIND_SCREEN) is an edge too: past it the shader takes the
    # scene texture's other side (junone7: a dark sliver down the left edge, the right edge's sky drifting in)
    sx, sy = np.sign(WIND_SCREEN).astype(int)
    pad = (int(sy > 0), int(sy < 0), int(sx > 0), int(sx < 0))  # top, bottom, left, right
    dist = cv2.distanceTransform(cv2.copyMakeBorder(hard, *pad, cv2.BORDER_CONSTANT, value=0), cv2.DIST_L2, 5)
    dist = dist[pad[0]:pad[0] + hard.shape[0], pad[2]:pad[2] + hard.shape[1]]
    return np.clip(dist / max(shift, 1.0), 0, 1)


def sky_scenery(img, sky, px):
    """Scenery inside the sky mask (`img` at the mask's size, `px` of its pixels per background pixel): fine detail
    (lighter or darker than its surroundings, SKY_DETAIL) and long straight edges (LSD; not bright lying-down ones,
    which are cloud streaks), grouped, keeping the groups that stand on what's below the sky and rise into it.
    Clouds float and lie flat: a cloud's texture alone, however contrasty (Cosmo Canyon's sunset), stays sky.
    del1: its cruise ship, masts and lighthouse; Corel's mountain tops; Gold Saucer's towers."""
    g = img.astype(np.float32)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(4.5 * px),) * 2)
    lighter = np.max([cv2.morphologyEx(g[..., c], cv2.MORPH_TOPHAT, k) for c in range(3)], 0)
    darker = np.max([cv2.morphologyEx(g[..., c], cv2.MORPH_BLACKHAT, k) for c in range(3)], 0)
    bits = (np.maximum(lighter, darker) > SKY_DETAIL).astype(np.uint8)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    segs = cv2.createLineSegmentDetector().detect(gray)[0]
    for x1, y1, x2, y2 in (segs.reshape(-1, 4) if segs is not None else []):
        if np.hypot(x2 - x1, y2 - y1) < SKY_LINE * px:
            continue
        one = np.zeros_like(bits)
        cv2.line(one, (int(x1), int(y1)), (int(x2), int(y2)), 1, _odd(2.5 * px))
        if abs(y2 - y1) < 0.35 * abs(x2 - x1) and (lighter > darker)[one > 0].mean() > 0.5:
            continue  # a bright streak lying down: cloud
        bits |= one
    solid = cv2.morphologyEx(bits, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(5.5 * px),) * 2))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    # standing: touching what isn't sky just below it (masts on a deck, a tower on its roof), and at least
    # SKY_STAND tall; non-sky above a cloud (the box's edge) doesn't hold it up
    below = sky == 0
    ground = np.zeros_like(below)
    for d in range(1, max(2, round(3 * px)) + 1):
        ground[:-d] |= below[d:]
    standing = set(np.unique(labels[ground & (labels > 0)])) - {0}
    keep = [i for i in standing if stats[i, cv2.CC_STAT_HEIGHT] >= SKY_STAND * px]
    return np.isin(labels, keep) & (cv2.dilate(sky, np.ones((9, 9), np.uint8)) > 0)


def _odd(v):
    return max(3, int(round(v)) | 1)


def whole_sky(render, sky):
    """Qwen's sky (half size) grown over the rest of the sky it missed (its box is often a part: datiao_1's stopped at
    two thirds of the way across): smooth parts of the upper picture in its colours, joined to it."""
    q = 4
    small = cv2.resize(render, (render.shape[1] // q, render.shape[0] // q), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    seed = cv2.resize(sky.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA) > 0.5
    if seed.sum() < 50:
        return sky
    lab = cv2.cvtColor(small.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    mu, sd = lab[seed].mean(0), lab[seed].std(0)
    similar = (np.abs(lab - mu) / (2.5 * sd + np.array([4.0, 3.0, 3.0]))).max(-1) < 1
    L = lab[..., 0]
    smooth = np.sqrt(np.maximum(cv2.blur(L * L, (5, 5)) - cv2.blur(L, (5, 5)) ** 2, 0)) < 4.0
    upper = np.arange(sh)[:, None] < 0.6 * sh
    grow = (similar & smooth & upper) | seed
    n, labels = cv2.connectedComponents(grow.astype(np.uint8))
    keep = np.isin(labels, [k for k in np.unique(labels[seed]) if k > 0])
    keep = cv2.morphologyEx(keep.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(np.float32)
    return np.maximum(sky.astype(np.float32), cv2.resize(keep, (sky.shape[1], sky.shape[0]), interpolation=cv2.INTER_LINEAR))


def heat_mask(f, found):
    """Air shimmering over each fire: strongest just above its flames, fading upward and to the sides (half size)."""
    hw, hh = f.RW // 2, f.RH // 2
    out = np.zeros((hh, hw), np.float32)
    ys, xs = np.mgrid[0:hh, 0:hw].astype(np.float32)
    for kind, b, (hx, hy), _, _ in found:
        if kind in FLAMES:
            continue
        w = max(b["x2"] - b["x1"], 3 * f.scale) / 2
        h = max(b["y2"] - b["y1"], 3 * f.scale) / 2
        reach = 3.0 * h * (0.6 if kind in SMALL_FIRES else 1.0)
        top = b["y1"] / 2
        up = np.clip((top + h * 0.5 - ys) / max(reach, 1.0), 0, 1)        # 0 at the flames, 1 at the top
        along = np.where(ys <= top + h * 0.5, np.exp(-2.5 * up), 0)        # strongest low down
        across = np.exp(-((xs - hx / 2) / (w * (1.0 + up)) * 1.2) ** 2)    # widening as it rises
        out = np.maximum(out, along * across * (0.6 if kind in SMALL_FIRES else 1.0))
    return out


def flag_mask(f, feats, render):
    """Each flag's cloth, 0 at its pole to 1 at its free end (half size), and the way it flies: +1 (pole on the
    left) or -1."""
    hw, hh = f.RW // 2, f.RH // 2
    out = np.zeros((hh, hw), np.float32)
    ys, xs = np.mgrid[0:hh, 0:hw].astype(np.float32)
    lum = render.astype(np.float32) @ [0.299, 0.587, 0.114]
    votes = 0.0
    for b in feats.get("flag", []):
        x1, y1, x2, y2 = (int(round(b[k])) for k in ("x1", "y1", "x2", "y2"))
        w, h = x2 - x1, y2 - y1
        if w < 3 or h < 3:
            continue
        # the pole: the sharpest upright line at one end
        edges = np.abs(np.diff(lum[y1:y2, max(0, x1 - 2):x2 + 2], axis=1)).mean(0)
        q = max(1, len(edges) // 4)
        left = edges[:q].max() >= edges[-q:].max()
        votes += (1 if left else -1) * w * h
        pole = x1 / 2 if left else x2 / 2
        along = np.clip((xs - pole) / (w / 2) * (1 if left else -1), 0, 1.3)
        fade_x = np.clip(1.3 - along, 0, 0.3) / 0.3
        cy, ry = (y1 + y2) / 4, h / 4
        across = np.clip((ry * 1.4 - np.abs(ys - cy)) / (ry * 0.4), 0, 1)
        out = np.maximum(out, np.clip(along, 0, 1) * fade_x * across)
    return out, (1.0 if votes >= 0 else -1.0)


# --- lit windows and lanterns (the scene's glow, each flickering on its own) ---

def _ramp(x, a, b):
    """0 at a, 1 at b, linear between (falling when b < a)."""
    return np.clip((x - a) / (b - a), 0, 1)


def pane_light(hue, s, v):
    """How much each pixel looks like glass with a lamp or candle behind it, 0..1 (HSV: hue in degrees, s and v
    0..1): yellow, orange or near white, and bright for its colour. Pale needs to be near white (cream paper and
    plaster are 0.7-0.85), saturated yellow less; orange brighter and more saturated than wood or paper a lantern
    lights (uttmpin1's beige walls and shoji: hue 24-28, s 0.5-0.65, v 0.7-0.75; its lit slum windows far off in
    mds6_22: hue 23-34, s 0.83, v 0.76-0.88). Blue, teal and green glass isn't candlelight (church, elm)."""
    warm = np.maximum(_ramp(hue, 14, 24) * _ramp(hue, 80, 68), _ramp(s, 0.2, 0.1))
    orange = _ramp(hue, 38, 28) * _ramp(hue, 0.5, 1)
    need = 0.9 - 0.3 * _ramp(s, 0.2, 0.45) + orange * (0.12 + 0.3 * _ramp(s, 0.8, 0.6))
    return warm * _ramp(v - need, -0.1, 0.05)


def windows(f, feats, render):
    """Qwen's lit windows as light sources at half size: [(ys, xs, weight per pixel, linear colour, flicker
    share)]. Candlelight where the painting shows a lit pane, each pixel by how lit it looks (pane_light): panes,
    not the frame's thin lines (lit wood, gold trim), and brighter than around them (not a lit wall); a box with
    hardly any is left as painted. Only in the evening and at night (Qwen's answer: sunlit glass would only bloom).
    Until 2026-11-03 every pixel in a box got at least a third of the light and beige paper counted as lit: walls,
    shoji, walkways and dark glass went under flat pale rectangles (uttmpin1)."""
    scene = feats.get("scene") or {}
    if scene and (scene.get("indoors") or not scene.get("evening")):
        return []
    hw, hh = f.RW // 2, f.RH // 2
    small = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    lit = pane_light(hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255).astype(np.float32)
    lum = small.astype(np.float32) @ [0.299, 0.587, 0.114] / 255
    # (asked before the evening question: fainter the brighter the scene)
    dark = 1.0 if scene else float(np.clip(1.0 - np.median(lum) / 0.45, 0.2, 1.0))
    out = []
    for b in feats.get("window", []):
        x1, y1, x2, y2 = (b[k] / 2 for k in ("x1", "y1", "x2", "y2"))
        mx, my = (x2 - x1) * 0.12, (y2 - y1) * 0.12  # the glass, inside the frame
        X1, Y1, X2, Y2 = int(round(x1 + mx)), int(round(y1 + my)), int(round(x2 - mx)), int(round(y2 - my))
        if X2 - X1 < 2 or Y2 - Y1 < 2:
            continue
        if f.name in CANDLELIT_GLASS:  # every pane lit, brighter on the glass than on the bars
            ys, xs = np.mgrid[Y1:Y2, X1:X2].reshape(2, -1)
            hue, s, v = hsv[ys, xs, 0] * 360 / 256, hsv[ys, xs, 1] / 255, hsv[ys, xs, 2] / 255
            warm = (hue > 15) & (hue < 60) & (s > 0.3) & (v > 0.5)
            glow = 0.35 + 0.65 * min(1.0, float(warm.mean()) * 3)
            out.append((ys, xs, (0.35 + 0.65 * v) * glow * dark, CANDLE, 0.25, "window"))
            continue
        k = max(3, int(round(min(X2 - X1, Y2 - Y1) * 0.15)) | 1)  # a grey opening: panes stay, thin lines go
        w = lit[Y1:Y2, X1:X2]
        w = np.minimum(w, cv2.morphologyEx(w, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))))
        r = max(X2 - X1, Y2 - Y1)  # brighter than its surroundings, a box's size around
        A1, B1, A2, B2 = max(0, X1 - r), max(0, Y1 - r), min(hw, X2 + r), min(hh, Y2 + r)
        around = cv2.GaussianBlur(lum[B1:B2, A1:A2], (0, 0), 0.5 * r)[Y1 - B1:Y2 - B1, X1 - A1:X2 - A1]
        w = w * _ramp(lum[Y1:Y2, X1:X2] / (around + 0.02), 1.0, 1.25)
        if float(w.mean()) < WINDOW_LIT:
            continue
        ys, xs = np.nonzero(w > 0.02)
        out.append((ys + Y1, xs + X1, w[ys, xs] * dark, CANDLE, 0.25, "window"))
    return out


def screens(f, feats, render):
    """Qwen's lit screens and signs as light sources at half size: their bright pixels in their own colour (already
    bright in the painting: a soft extra glow), each with a slow shimmer and a scan bar rolling down it. Each pixel by
    how bright it is (SCREEN_V), fading out toward the box's edges (SCREEN_EDGE): until 2026-11-03 every pixel over
    0.35 in the box glowed alike, and a round lamp Qwen boxed as a screen (nvmkin21's cyan lamp over the Jenova door,
    its painted halo filling the box) lit up as a hard square."""
    hw, hh = f.RW // 2, f.RH // 2
    small = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA)
    v = small.max(2).astype(np.float32) / 255
    lin = (small.astype(np.float32) / 255) ** 2.2
    out = []
    for b in feats.get("screen", []):
        X1, Y1, X2, Y2 = (int(round(b[k] / 2)) for k in ("x1", "y1", "x2", "y2"))
        if X2 - X1 < 2 or Y2 - Y1 < 2:
            continue
        patch = v[Y1:Y2, X1:X2]
        ys, xs = np.nonzero(patch > SCREEN_V[0])
        if len(xs) < 4:
            continue
        h, w = patch.shape
        # above what the box's edge shows (a lamp's halo the box cuts through: none there, all at its hot middle; a
        # screen's dark frame: about as before), and nothing right at the edge
        ring = np.concatenate([patch[0], patch[-1], patch[:, 0], patch[:, -1]])
        lo, hi = max(SCREEN_V[0], float(np.median(ring))), float(np.percentile(patch, 98))
        edge = np.minimum(np.minimum(xs + 0.5, w - xs - 0.5), np.minimum(ys + 0.5, h - ys - 0.5))
        shape = _ramp(patch[ys, xs], lo, max(hi, lo + 0.05)) * _ramp(patch[ys, xs], *SCREEN_V) * \
            np.clip(edge / max(1.0, SCREEN_EDGE * min(w, h)), 0, 1)
        # as much light as before (every pixel at SCREEN_EMIT), shaped so (up to SCREEN_GAIN times as bright)
        weight = SCREEN_EMIT * shape * min(SCREEN_GAIN, len(xs) / max(float(shape.sum()), 1e-6))
        ys, xs = ys + Y1, xs + X1
        out.append((ys, xs, weight.astype(np.float32), lin[ys, xs].mean(0), 0.0, "screen"))
    return out


def leds(f, feats, render):
    """Qwen's indicator lights (status LEDs, door and button lights): each one's brightest pixels, at half size:
    [(ys, xs, weight, linear colour, 0, "led")]. (A colour test for small bright dots also took the highlights on
    stair edges and pipes: 120 in blin1's lobby.)"""
    hw, hh = f.RW // 2, f.RH // 2
    small = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA)
    v = small.max(2).astype(np.float32) / 255
    lin = (small.astype(np.float32) / 255) ** 2.2
    out = []
    for b in feats.get("indicator", [])[:LEDS]:
        X1, Y1, X2, Y2 = (int(round(b[k] / 2)) for k in ("x1", "y1", "x2", "y2"))
        if X2 - X1 < 1 or Y2 - Y1 < 1:
            continue
        patch = v[Y1:Y2, X1:X2]
        ys, xs = np.nonzero(patch >= max(0.5, float(np.percentile(patch, 70))))
        if not len(xs):
            continue
        ys, xs = ys + Y1, xs + X1
        out.append((ys, xs, np.full(len(xs), LED_EMIT, np.float32), lin[ys, xs].mean(0), 0.0, "led"))
    return out


def _gains(mode, share, flicker, rng, frames):
    """A light's brightness over the looping frames: flames and candles a smooth flicker that loops (`flicker` x
    `share`), screens a slow shimmer, indicator lights blinking (half of them) or steady."""
    i = np.arange(frames)
    if mode == "led":
        r = rng.random()
        if r < 0.5:
            return np.ones(frames)
        period = frames if r < 0.8 else frames // 2
        on = ((i + rng.integers(period)) % period) < (period // 2 if rng.random() < 0.6 else 1)
        return np.where(on, 1.0, 0.0)
    noise = sum(rng.normal() * np.sin(2 * np.pi * k * i / frames + rng.random() * 6.283) / k for k in range(1, 5))
    noise = noise / max(float(np.std(noise)), 1e-3)
    if mode == "screen":
        return 1 + 0.06 * noise
    return 1 + flicker * share * 0.35 * np.clip(noise, -2, 2)


def lights(f, feats, amount, flicker, found=None, blink=0.0):
    """The light of lit windows, lanterns and fires (`amount`) and screens and indicator lights (`blink`): their own
    glow (over the painting's colour, with a little halo) and its spill on the floor in front of and below them
    (SPILL_PX), each flickering, shimmering or blinking on its own (_gains; flames by `flicker`) over FLICKER_FRAMES
    looping frames. Linear RGB at half size: {"still", "frames" (or None), "count", "windows", "screens", "leds",
    "sources"} or None."""
    if amount <= 0 and blink <= 0:
        return None
    from .build import marigold_normals
    render = _render(f)
    Z, step = _depth(f)
    hw, hh = f.RW // 2, f.RH // 2
    normals_half = marigold_normals(f, (hh, hw))
    win = windows(f, feats, render) if amount > 0 else []
    scr = screens(f, feats, render) if blink > 0 else []
    led = leds(f, feats, render) if blink > 0 else []
    found = fires(f, feats, Z, step, render) if found is None else found
    lin = (cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255) ** 2.2
    hsv = cv2.cvtColor(cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2HSV_FULL)
    sources = [(ys, xs, w * amount, c, sh, m) for ys, xs, w, c, sh, m in win]  # (pixels, weight, colour, share, mode)
    sources += [(ys, xs, w * blink, c, sh, m) for ys, xs, w, c, sh, m in scr + led]
    for kind, b, *_ in (found if amount > 0 else []):  # a lantern's or fire's bright warm pixels, in their own colour
        x1, y1, x2, y2 = (int(round(b[k] / 2)) for k in ("x1", "y1", "x2", "y2"))
        p = hsv[y1:y2, x1:x2].astype(np.float32)
        if p.size == 0:
            continue
        hot = (p[..., 2] > 140) & (p[..., 1] > 60) & ((p[..., 0] < 45) | (p[..., 0] > 245))
        ys, xs = np.nonzero(hot)
        if len(xs):
            ys, xs = ys + y1, xs + x1
            sources.append((ys, xs, np.full(len(xs), (0.4 if kind in FLAMES else 0.6) * amount, np.float32),
                            lin[ys, xs].mean(0), 0.4 if kind in FLAMES else 0.6, "flame"))  # already bright: less added
    if not sources:
        return None
    # the floor around them, on the depth grid: where it is in the world and that it faces up
    gh, gw = Z.shape
    from . import depth
    bx, by = depth.grid_bg(f, step)
    _, Pw = f.unproject(bx[:gh, :gw], by[:gh, :gw], np.where(np.isfinite(Z), Z, 1.0))
    ng = marigold_normals(f, (gh, gw))
    floor = np.isfinite(Z) & (ng[..., 2] > 0.75)
    rng = np.random.default_rng(5)
    moving = flicker > 0 or any(m in ("screen", "led") for *_, m in sources)
    frames = FLICKER_FRAMES if moving else 0
    acc_h = np.zeros((1 + frames, hh, hw, 3), np.float32)   # glow, at half size
    acc_g = np.zeros((1 + frames, gh, gw, 3), np.float32)   # spill, on the grid
    px = f.scale / 2
    for ys, xs, weight, colour, share, mode in sources:
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        gains = _gains(mode, share, flicker, rng, frames) if frames else np.ones(0)
        # its glow: its pixels and a soft halo, in a box around it
        m = int(px * 6) + 2
        Y0, Y1, X0, X1 = max(0, y0 - m), min(hh, y1 + m), max(0, x0 - m), min(hw, x1 + m)
        mask = np.zeros((Y1 - Y0, X1 - X0), np.float32)
        mask[ys - Y0, xs - X0] = weight
        glow = cv2.GaussianBlur(mask, (0, 0), max(0.6, px * 0.5)) + 0.6 * cv2.GaussianBlur(mask, (0, 0), max(1.0, px * 1.8))
        g = glow[..., None] * colour * WINDOW_EMIT
        acc_h[0, Y0:Y1, X0:X1] += g * (gains.mean() if mode == "led" and len(gains) else 1.0)
        rel = (np.arange(Y0, Y1, dtype=np.float32) - y0) / max(1, y1 - y0)  # down a screen, 0..1
        for i, k in enumerate(gains):
            if mode == "screen":  # a bright bar rolling down it once a loop
                bar = 0.35 * np.exp(-((rel - (i + 0.5) / frames) / 0.08) ** 2)
                acc_h[1 + i, Y0:Y1, X0:X1] += g * (k + bar)[:, None, None]
            else:
                acc_h[1 + i, Y0:Y1, X0:X1] += g * k
        if mode == "led":  # too small to light the ground
            continue
        # its light on the floor: in front (the way its wall faces) and below it, fading with distance
        cy, cx = float(ys.mean()), float(xs.mean())
        gy, gx = min(int(cy * 2 // step), gh - 1), min(int(cx * 2 // step), gw - 1)
        z = _near(Z, step, x0 * 2, y0 * 2, x1 * 2, y1 * 2, q=30)
        P = _world(f, cx * 2, cy * 2, z)
        R = _world_size(f, SPILL_PX, z)
        n = normals_half[min(int(cy), hh - 1), min(int(cx), hw - 1)].copy()
        n[2] = 0
        facing = float(np.linalg.norm(n)) > 0.3
        n = n / max(float(np.linalg.norm(n)), 1e-6)
        r = int(np.ceil(R * 3 / max(_world_size(f, step / f.scale, z), 1e-6))) + 2  # grid cells to look over
        Gy0, Gy1, Gx0, Gx1 = max(0, gy - r), min(gh, gy + r + 1), max(0, gx - r), min(gw, gx + r + 1)
        D = Pw[Gy0:Gy1, Gx0:Gx1] - P
        dist = np.linalg.norm(D[..., :2], axis=-1)
        w = np.exp(-(dist / R) ** 2) * floor[Gy0:Gy1, Gx0:Gx1] * (D[..., 2] < 0)
        if facing:
            w *= np.clip((D[..., :2] @ n[:2]) / np.maximum(dist, 1e-6) + 0.3, 0, 1)
        size = min(1.0, np.sqrt(len(xs) / (px * px) / 12)) * float(np.mean(weight))  # big, bright: more light
        s = w[..., None] * colour * SPILL * size
        acc_g[0, Gy0:Gy1, Gx0:Gx1] += s
        for i, k in enumerate(gains):
            acc_g[1 + i, Gy0:Gy1, Gx0:Gx1] += s * k
    # light falling on a surface shows by what it lights: none on near-black (blin63_1's lift shaft took its door
    # lights' cyan and red, and a lamp's pale patch, over the black AI 2D shows)
    luma = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA).astype(np.float32) @ [0.2126, 0.7152, 0.0722]
    lit = np.clip((luma - SPILL_DARK) / SPILL_DARK, 0, 1)[..., None]
    spill = lambda a: cv2.resize(cv2.GaussianBlur(a, (0, 0), 1.0), (hw, hh), interpolation=cv2.INTER_LINEAR) * lit
    out = [acc_h[i] + spill(acc_g[i]) for i in range(1 + frames)]
    return {"still": out[0], "frames": out[1:] or None, "count": len(sources), "windows": len(win),
            "screens": len(scr), "leds": len(led), "sources": sources}


# --- motes in light shafts, drifting mist, bubbles in tanks ---

def _soft(render, scale):
    """How soft and bright each pixel's neighbourhood is (light shafts, mist: little texture), at half size."""
    hw, hh = render.shape[1] // 2, render.shape[0] // 2
    lum = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA).astype(np.float32) @ [0.299, 0.587, 0.114] / 255
    r = max(3, int(scale * 2) | 1)
    mean = cv2.blur(lum, (r, r))
    tex = np.sqrt(np.maximum(cv2.blur(lum * lum, (r, r)) - mean * mean, 0))
    return lum, mean, tex


def _air_points(f, Z, step, mask, n, near, far, rng):
    """n points in the air in front of the scene where `mask` (half size) is: along each pixel's ray, between `near`
    and `far` of the way to the surface behind it. World points and their depths."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None, None
    pick = rng.choice(len(xs), min(n, len(xs)), replace=False)
    rx, ry = xs[pick] * 2.0, ys[pick] * 2.0
    gz = Z[np.clip((ry // step).astype(int), 0, Z.shape[0] - 1), np.clip((rx // step).astype(int), 0, Z.shape[1] - 1)]
    ok = np.isfinite(gz)
    if ok.sum() < 5:
        return None, None
    z = gz[ok] * rng.uniform(near, far, ok.sum())
    return _world(f, rx[ok], ry[ok], z), z


def _box_mask(f, boxes, shape):
    m = np.zeros(shape, bool)
    for b in boxes:
        m[int(b["y1"] / 2):int(b["y2"] / 2), int(b["x1"] / 2):int(b["x2"] / 2)] = True
    return m


def mote_source(f, feats, Z, step, amount, render):
    """Dust catching the light in its shafts: the original's still light layers (glow.static_light) and Qwen's beams
    (their soft, bright part), in the air in front of the surfaces behind them."""
    if amount <= 0:
        return None
    from . import glow
    hw, hh = f.RW // 2, f.RH // 2
    sl = cv2.resize(glow.static_light(f), (hw, hh), interpolation=cv2.INTER_AREA)
    shaft = sl.mean(-1) > 8
    if feats.get("beam"):
        lum, mean, tex = _soft(render, f.scale / 2)
        shaft |= _box_mask(f, feats["beam"], (hh, hw)) & (lum > 0.35) & (tex < 0.06)
    if shaft.mean() < 0.002:
        return None
    rng = np.random.default_rng(11)
    P, z = _air_points(f, Z, step, shaft, 300, 0.75, 0.98, rng)
    if P is None:
        return None
    zm = float(np.median(z))
    lit = sl[shaft]
    col = np.clip(lit.mean(0) / max(float(lit.mean(0).max()), 1.0), 0, 1) if lit.size and lit.max() > 8 else np.ones(3)
    col = 0.5 + 0.5 * col
    share = float(np.clip(shaft.mean() / 0.05, 0.3, 2.0))
    return {"kind": "motes", "points": P, "count": int(max(10, round(MOTES["per"] * share * amount * 2))),
            "size": _world_size(f, MOTES["size"], zm), "alpha": MOTES["alpha"], "color": [round(float(c), 3) for c in col],
            "spread": _world_size(f, MOTES["spread"], zm), "rise": _world_size(f, MOTES["drift"], zm),
            "life": list(MOTES["life"])}


def mist_source(f, feats, Z, step, amount, render):
    """Mist rolling slowly through Qwen's mist areas (their soft, pale part): big faint puffs just in front of the
    surfaces, carried along by the air."""
    if amount <= 0 or not feats.get("mist"):
        return None
    hw, hh = f.RW // 2, f.RH // 2
    lum, mean, tex = _soft(render, f.scale / 2)
    area = _box_mask(f, feats["mist"], (hh, hw)) & (tex < 0.05) & (lum > 0.25)
    if area.mean() < 0.003:
        return None
    rng = np.random.default_rng(12)
    P, z = _air_points(f, Z, step, area, 200, 0.85, 0.99, rng)
    if P is None:
        return None
    zm = float(np.median(z))
    small = cv2.resize(render, (hw, hh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    col = np.clip(small[area].mean(0) * 1.1, 0, 1)
    wind = _screen_dir(f, *WIND_SCREEN) * _world_size(f, MIST["wind"], zm)
    share = float(np.clip(area.mean() / 0.1, 0.4, 2.0))
    return {"kind": "mist", "points": P, "count": int(max(8, round(MIST["per"] * share * amount * 2))),
            "size": _world_size(f, MIST["size"], zm), "alpha": MIST["alpha"], "color": [round(float(c), 3) for c in col],
            "spread": _world_size(f, MIST["spread"], zm), "rise": _world_size(f, 1.0, zm), "wind": [*wind[:2], 0.0],
            "grow": MIST["grow"], "life": list(MIST["life"])}


def bubble_source(f, feats, Z, step, amount, render):
    """Bubbles rising through each of Qwen's tanks, from near its bottom to near its top, at its front."""
    if amount <= 0 or not feats.get("tank"):
        return None
    pts, zs, heights, cols = [], [], [], []
    for b in feats["tank"]:
        w, h = b["x2"] - b["x1"], b["y2"] - b["y1"]
        if h < 4 * f.scale or w < 2 * f.scale or h < 0.9 * w:  # tanks stand upright (ancnt4's wide basin isn't one)
            continue
        cx, by = (b["x1"] + b["x2"]) / 2, b["y1"] + 0.88 * h
        z = _near(Z, step, b["x1"] + 0.3 * w, b["y1"] + 0.3 * h, b["x2"] - 0.3 * w, b["y2"] - 0.1 * h, q=10)
        pts.append(_world(f, cx, by, z))
        zs.append(z)
        heights.append(0.75 * h / f.scale)
        patch = render[int(b["y1"]):int(b["y2"]), int(b["x1"]):int(b["x2"])].reshape(-1, 3) / 255
        cols.append(np.median(patch, 0))
    if not pts:
        return None
    zm = float(np.median(zs))
    rise = BUBBLES["rise"]
    life = float(np.median(heights)) / rise
    col = np.clip(0.4 * np.mean(cols, 0) + 0.6, 0, 1)
    return {"kind": "bubbles", "points": np.array(pts), "count": int(max(4, round(BUBBLES["per"] * len(pts) * amount * 2))),
            "size": _world_size(f, BUBBLES["size"], zm), "alpha": BUBBLES["alpha"], "color": [round(float(c), 3) for c in col],
            "spread": _world_size(f, BUBBLES["spread"] * np.median([(b["x2"] - b["x1"]) / f.scale for b in feats["tank"]]), zm),
            "rise": _world_size(f, rise, zm), "life": [round(0.6 * life, 2), round(life, 2)]}


# --- the scene's lights on the characters, reflections ---

def _char_height(f):
    from .build import CHAR_HEIGHT, model_scale
    return CHAR_HEIGHT * model_scale(f) / 512


def point_lights(f, feats, Z, step, render, found, amount, windows_on):
    """Up to 16 of the scene's lights as point lights for the characters (FFNx-3d point_lights: x y z radius, r g b
    flicker each; point_light_strength): fires and lanterns, lit windows (in the evening), screens, glowing tanks and
    the original's light layers (its lamps and signs). Each just in front of where it is in the painting, coloured
    like it, reaching LAMP[kind] character heights. Nothing at 0."""
    if amount <= 0:
        return {}
    from . import atmos
    ch = _char_height(f)
    cam = -(f.Rinv @ np.asarray(f.T, float))  # the camera's world position (R isn't quite a rotation: Field.Rinv)
    lin = (render.astype(np.float32) / 255) ** 2.2
    cand = []  # (score, world point, linear colour, kind)

    def add(kind, x1, y1, x2, y2, colour=None):
        x1, y1, x2, y2 = (float(v) for v in (x1, y1, x2, y2))
        if x2 - x1 < 1 or y2 - y1 < 1:
            return
        z = _near(Z, step, x1, y1, x2, y2, q=30)
        P = _world(f, (x1 + x2) / 2, (y1 + y2) / 2, z)
        d = cam - P
        P = P + d / max(float(np.linalg.norm(d)), 1e-6) * 0.35 * ch  # in front of its wall
        if colour is None:
            patch = lin[int(y1):int(y2), int(x1):int(x2)].reshape(-1, 3)
            bright = patch[patch.sum(1) >= np.percentile(patch.sum(1), 75)]
            colour = bright.mean(0) if len(bright) else patch.mean(0)
        colour = np.asarray(colour, np.float32)
        colour = colour / max(float(colour.max()), 1e-3)
        radius, power, flick = LAMP[kind]
        cand.append((power * radius, P, colour * power, radius * ch, flick, kind))

    for kind, b, *_ in found:
        add("fire" if kind not in FLAMES else "lantern", b["x1"], b["y1"], b["x2"], b["y2"])
    if windows_on:  # the lit ones, at their lit panes (windows: in the evening, outdoors)
        for ys, xs, *_ in windows(f, feats, render):
            add("window", xs.min() * 2, ys.min() * 2, (xs.max() + 1) * 2, (ys.max() + 1) * 2, CANDLE)
    for b in feats.get("screen", []):
        add("screen", b["x1"], b["y1"], b["x2"], b["y2"])
    for b in feats.get("tank", []):
        patch = lin[int(b["y1"]):int(b["y2"]), int(b["x1"]):int(b["x2"])]
        if patch.size and float(patch.mean()) > 0.08:  # a glowing tank
            add("tank", b["x1"], b["y1"], b["x2"], b["y2"])
    # the original's light layers: their brightest compact blobs
    lm = atmos.light_map(f)
    if lm.max() > 0.05:
        q = 4
        small = cv2.resize(lm, (f.RW // q, f.RH // q), interpolation=cv2.INTER_AREA)
        lum = small.max(2)
        n, labels, stats, _ = cv2.connectedComponentsWithStats((lum > 0.25).astype(np.uint8), 8)
        for k in range(1, n):
            x, y, w, h, area = stats[k]
            if area < 4 or area > 0.05 * lum.size:
                continue
            col = small[labels == k].mean(0)
            add("lamp", x * q, y * q, (x + w) * q, (y + h) * q, col)
    cand.sort(key=lambda c: -c[0])
    vals = []
    for _, P, col, radius, flick, _ in cand[:16]:
        vals += [*(round(float(v), 1) for v in P), round(float(radius), 1), *(round(float(c), 3) for c in col), flick]
    if not vals:
        return {}
    return {"point_lights": vals, "point_light_strength": round(2.0 * float(amount), 3)}


def water_level(f, Z, step, render):
    """The water's height in the world, from what stands in it: below each front edge of the walkable floor that has
    water just beneath it on screen (a stepping stone's rim, a quay), its side runs straight down to the water; the
    height where the camera's ray to that water line meets the side is the water's. Their median, or None. (Not the
    water's own depth: Depth Anything reads its reflections as depth, so ancnt1's mirror pool came out far too deep,
    nor the scene's points along the water's edge: towers far behind it put that at 1183, over the stones at -149.)"""
    from collections import Counter
    wm = cv2.resize(water(f, render), (f.w, f.h), interpolation=cv2.INTER_AREA) > 0.5
    if wm.mean() < 0.01:
        return None
    key = lambda v: tuple(np.round(v).astype(int))
    count = Counter(tuple(sorted((key(t[a]), key(t[b])))) for t in f.tris for a, b in ((0, 1), (1, 2), (2, 0)))
    border = {v for e, c in count.items() if c == 1 for v in e}
    levels = []
    for v in border:
        P = np.array(v, float)
        sxy, z = f.project(P[None])
        if z[0] <= 1:
            continue
        x, y = int(sxy[0, 0] - f.mx), int(sxy[0, 1] - f.my)
        if not (0 <= x < f.w and 0 <= y < f.h) or wm[y, x]:
            continue
        below = np.nonzero(wm[y + 1:min(f.h, y + 1 + WATER_REACH), x])[0]
        if not len(below):
            continue
        yb = y + 1 + int(below[0]) + f.my
        lo, hi = P[2] - 50 * _char_height(f), P[2]  # the side's foot is between these
        for _ in range(40):
            mid = (lo + hi) / 2
            sy = f.project(np.array([[P[0], P[1], mid]]))[0][0, 1]
            if sy > yb:  # lower on screen than the water line: too deep
                lo = mid
            else:
                hi = mid
        levels.append((P[2], (lo + hi) / 2))
    # only the lowest floors beside the water: an upper walkway has the pool far behind it on screen, not under it
    # (ancnt1's gave 856-1428 against -153 to -200 from its platform's edges)
    if len(levels) < 3:
        return None
    lowest = min(z for z, _ in levels)
    near = [w for z, w in levels if z <= lowest + 2 * _char_height(f)]
    return float(min(np.median(near), lowest)) if len(near) >= 2 else None


def _floor(f, shape):
    """The walkmesh as the camera sees it (where characters stand), True/False at `shape` (h, w) over the render."""
    m = np.zeros(shape, np.uint8)
    s, z = f.project(f.tris.reshape(-1, 3))
    k = np.array([shape[1] / f.w, shape[0] / f.h])
    tri = ((s - [f.mx - f.frame[0], f.my - f.frame[1]]) * k).reshape(-1, 3, 2).round().astype(np.int32)
    cv2.fillPoly(m, list(tri[(z.reshape(-1, 3) > 1).all(1)]), 1)
    return m > 0


def _carpet(rgb):
    """0..1: how surely each pixel's colour is a carpet's or rug's (too saturated for polished stone, metal or wood)."""
    rgb = rgb.astype(np.float32)
    sat = (rgb.max(-1) - rgb.min(-1)) / np.maximum(rgb.max(-1), 1.0)
    return np.clip((sat - CARPET_SAT[0]) / (CARPET_SAT[1] - CARPET_SAT[0]), 0, 1)


def floor_mirror(f, render, shape):
    """0..1 at `shape` (h, w): where the scene may mirror the characters as a polished floor (relight writes it in
    the sun map's A for FFNx-3d): surfaces facing up by Marigold's normals (not the scene's own: Depth Anything
    ramps ancnt3's altar front up into a floor, and it mirrored Cloud with sparkles), not carpet."""
    from .build import marigold_normals
    up = np.clip((marigold_normals(f, shape)[..., 2] - 0.6) / 0.2, 0, 1)  # (its floors seen low read 0.8 up)
    small = cv2.GaussianBlur(cv2.resize(render, (shape[1], shape[0]), interpolation=cv2.INTER_AREA), (0, 0), 1.0)
    return (up * (1 - _carpet(small))).astype(np.float32)


def reflections(f, feats, Z, step, render, amount):
    """How much the scene mirrors the characters (FFNx-3d reflection = [water, polished floors], reflection_level):
    its water if it has any, its floors if Qwen called them polished and they aren't carpet (Qwen called blin60_2's
    red carpet polished: Cloud was mirrored in it)."""
    scene = feats.get("scene") or {}
    if amount <= 0 or not scene:
        return {}
    found = feats.get("pool")
    water = REFLECT[0] * min(1.0, 2.0 * amount) if (scene.get("water") if found is None else found) else 0.0
    floor = REFLECT[1] * min(1.0, 2.0 * amount) if scene.get("polished") else 0.0
    if floor > 0:
        q = 4
        small = cv2.resize(render, (f.RW // q, f.RH // q), interpolation=cv2.INTER_AREA)
        walk = _floor(f, small.shape[:2])
        if walk.sum() >= 50 and float(np.median(_carpet(small)[walk])) >= 0.5:
            floor = 0.0
    if water <= 0 and floor <= 0:
        return {}
    level = water_level(f, Z, step, render) if water > 0 else None
    if water > 0 and level is None:  # no floor edge right over the water to measure its height by (smkin_4's is
        water = 0.0                   # deep down its shaft): no reflection rather than one at a guessed height
    if water <= 0 and floor <= 0:
        return {}
    out = {"reflection": [round(water, 3), round(floor, 3)]}
    if water > 0 and floor <= 0:
        out["reflection_level"] = round(level, 1)
    return out


# --- all of it ---

def _off_backdrop(f, feats):
    """What Qwen found mostly over a far backdrop seen through a window (glow.through_glass) isn't a screen, mist or a
    lit window: it's the view (blin66_2's nebula: a screen's glow, shimmer and scan bar, purple mist puffs and a
    purple lamp on it, ghosting it pale). Nor over a backdrop the game draws (glow.scene_alpha): it moves apart
    from the scene, and the scene leaves it out."""
    from . import glow
    seen = glow.scene_alpha(f) if feats else None
    glass = glow.through_glass(f) if feats and seen is None else None
    if glass is None and seen is None:
        return feats
    w = 1 - seen if seen is not None else glass[1]

    def over(b):
        patch = w[int(b["y1"]):int(np.ceil(b["y2"])), int(b["x1"]):int(np.ceil(b["x2"]))]
        return float(patch.mean()) if patch.size else 0.0
    return {k: ([b for b in v if over(b) < 0.5] if k in ("screen", "mist", "window") else v) for k, v in feats.items()}


def write_fx(f, path, render, channels):
    """The effects map at half size: R sky, G heat, B flags (channels, None for none) and A where there is no
    water. Always written: it tells the shader where the water is (without one, it took the material map's smooth
    parts for water, and Shine smooths polished floors: they rippled)."""
    hw, hh = f.RW // 2, f.RH // 2
    rgba = np.zeros((hh, hw, 4), np.uint8)
    for i, c in enumerate(channels):
        if c is not None:
            rgba[..., i] = np.round(np.clip(c, 0, 1) * 255).astype(np.uint8)
    wet = cv2.resize(water(f, render), (hw, hh), interpolation=cv2.INTER_AREA)
    rgba[..., 3] = np.round((1 - np.clip(wet, 0, 1)) * 255).astype(np.uint8)
    gltfout.write_dds(path, rgba)


def write(f, o, out, feats=None):
    """Everything for the field from what find found (the cache; without it only the water): the particle sources
    (for atmos.particles), the effects map (<field>_scene_fx.dds: sky, heat, flags and where the water is) and its
    lighting keys, and the lights (for atmos.glow / atmos.flicker). Returns (sources, lighting keys, lights,
    summary)."""
    feats = cached(f) if feats is None else feats
    feats = _off_backdrop(f, feats)
    path = out / f"{f.name}_scene_fx.dds"
    get = lambda k: float(o.get(k, 0.0) or 0.0)
    render = _render(f)
    if feats and framed(f):  # a picture framed on black (nivl_e3): none of it
        summary = {**(feats.get("scene") or {}), "framed": True}
        (f.work / "life.json").write_text(json.dumps(summary), encoding="utf-8")
        feats = {}
    if not feats or not (f.work / "simplify_in.npz").exists():
        write_fx(f, path, render, [None, None, None])
        return [], {}, None, {}
    Z, step = _depth(f)
    found = fires(f, feats, Z, step, render)
    shift = SKY_SHIFT * SKY_MAX * max(get("sky"), 0.25) * (f.RW // 2) + 2
    sky = sky_mask(f, feats, Z, step, render, shift)
    heat = heat_mask(f, found) if get("fire") > 0 and found else None
    flags, flag_dir = flag_mask(f, feats, render) if get("flags") > 0 and feats.get("flag") else (None, 1.0)
    keys = {}
    # R: all the sky at SKY_FLOOR (the shader keeps cloud shadows off it: del1's sky near its masts sits inside the
    # sun's shadow map), plus how far it drifts above that
    whole = whole_sky(render, sky > 0) if sky is not None else None
    sky_r = None
    if whole is not None:
        # closed over thin things: the grown sky stops short of a mast, and the band beside it took cloud shadows
        # as a grey strip; masts and rigging go without (too thin to tell), a hull or a tower keeps them
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(7 * f.scale / 2),) * 2)
        whole = cv2.morphologyEx((whole > 0.5).astype(np.uint8), cv2.MORPH_CLOSE, k) > 0
        drift = sky if get("sky") > 0 else np.zeros_like(sky)
        sky_r = np.where(whole, SKY_FLOOR + (1 - SKY_FLOOR) * drift, 0.0)
    channels = [sky_r, heat, flags]
    if get("birds") <= 0 and get("sky") <= 0:
        sky = None
    write_fx(f, path, render, channels)
    if any(c is not None and c.max() > 0.01 for c in channels):
        keys = {"scene_fx": [_fmt(get("sky") * SKY_MAX, 3) if channels[0] is not None else 0.0,
                             _fmt(get("fire") * HEAT_MAX, 3) if heat is not None else 0.0,
                             _fmt(get("flags") * FLAG_MAX, 3) if flags is not None else 0.0],
                "scene_fx_dir": [WIND_SCREEN[0], WIND_SCREEN[1], flag_dir, 0.0]}
    sources = []
    smoke, chimneys = smoke_source(f, feats, Z, step, get("smoke"), render)
    sparks = spark_source(f, found, get("fire"))
    birds = bird_source(f, sky, Z, step, get("birds"), render)
    light = lights(f, feats, get("windows"), get("flicker"), found=found, blink=get("blink"))
    motes = mote_source(f, feats, Z, step, get("motes"), render)
    mist = mist_source(f, feats, Z, step, get("mist"), render)
    bubbles = bubble_source(f, feats, Z, step, get("bubbles"), render)
    sources = [s for s in (smoke, sparks, birds, motes, mist, bubbles) if s]
    lamps = point_lights(f, feats, Z, step, render, found, get("char_lamps"), get("windows") > 0)
    keys.update(lamps)
    keys.update(reflections(f, feats, Z, step, render, get("reflections")))
    summary = {**(feats.get("scene") or {}), "chimneys": chimneys, "fires": len(found), "sky": bool(sky is not None and sky.max() > 0),
               "flags": len(feats.get("flag", [])), "lights": light["count"] if light else 0,
               "windows": light["windows"] if light else 0, "screens": len(feats.get("screen", [])),
               "leds": light["leds"] if light else 0, "tanks": len(feats.get("tank", [])),
               "mist": len(feats.get("mist", [])), "beams": bool(motes), "lamps": len(lamps.get("point_lights", [])) // 8,
               "reflections": keys.get("reflection", [0, 0])}
    (f.work / "life.json").write_text(json.dumps(summary))
    return sources, keys, light, summary
