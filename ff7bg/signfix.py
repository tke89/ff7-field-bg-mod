"""Readable signs: the user's own words painted onto the signs of a finished render.

Repaints turn lettering into made-up glyphs (Kalm's shop signs read "TIME CULLENERLEY"). For each sign area the
user gave words to ("say" in the field's text_boxes), the render around it goes to Qwen-Image 2.1, which letters
well, as an edit: make the sign read those words, keep everything else. Only the sign itself (its box, a little
grown, feathered) goes back into the picture, lined up and colour-matched to what was there, so nothing else
changes. The Studio makes a new version of it (run_signfix in studio.py).
"""
import math

import cv2
import numpy as np

from .comfy import EDIT_VARIANTS, FAMILY_PRESETS, _edit_sample, _loaders
from .signs import quad_of, slanted

CONTEXT = 0.8      # the crop around a sign: its box and this much of its size on every side (its surroundings'
MIN_CONTEXT = 20   # style); at least this many original pixels
CROP_MP = 1.0      # Qwen paints the crop at about this size
PASTE_GROW = 0.12  # what goes back: the box grown by this share of its size, fading out over as much again


def instruction(say):
    """What Qwen is asked. The sign says only the user's words ("A / B": two lines): told just to change the lettering
    to them, it left Kalm's small made-up lines over them ("WANNY DEPRMENTS" over MATERIA SHOP)."""
    lines = [x.strip() for x in say.split("/") if x.strip()]
    words = (f'"{lines[0]}"' if len(lines) == 1 else
             " and below it ".join(f'"{x}"' for x in lines) + ", one line each")
    return (f"Repaint the lettering on the sign in the middle of the picture so that all it says is {words}, spelled "
            "exactly like that. Every other word, letter or made-up scribble on the sign goes: the sign shows no other "
            "text. Use the same lettering style and colour as the sign's main letters now, sized to fill the sign "
            "nicely, and keep the sign board, its frame, any emblem or picture on it and everything around it "
            "exactly as they are.")


def settings(base):
    """Qwen-Image 2.1, the full model (better letters than Turbo), on the field's other settings."""
    s = dict(base)
    s.update(FAMILY_PRESETS["qwen21"])
    s.update(EDIT_VARIANTS["qwen21"]["full"])
    s["lora"] = ""
    return s


def build_workflow(s, image, say, w, h, prefix):
    """Qwen-Image 2.1 edit of `image` (uploaded, w x h): the sign made to read `say`."""
    wf = {"src": {"class_type": "LoadImage", "inputs": {"image": image}}}
    model, clip, vae = _loaders(wf, s)
    wf["qenc"] = {"class_type": "TextEncodeQwenImage21", "inputs": {
        "clip": clip, "prompt": instruction(say), "negative_prompt": "", "resolution": 0, "vae": vae,
        "images.image_1": ["src", 0]}}
    _edit_sample(wf, s, model, ["qenc", 0], ["qenc", 1], ["qenc", 2])
    wf["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    # the encoder rounds sizes to multiples of 32: back to the crop's size
    wf["back"] = {"class_type": "ImageScale", "inputs": {
        "image": ["dec", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}}
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": ["back", 0], "filename_prefix": prefix}}
    return wf


def to_render(box, canvas, margins, size):
    """A canvas-pixel box -> render pixels (x1, y1, x2, y2), through the render's margins (1x px per side:
    the field's padding plus the version's frame)."""
    cw, ch = canvas
    l, t, r, b = margins
    W, H = size
    sx, sy = W / (cw + l + r), H / (ch + t + b)
    return (l + box["x1"]) * sx, (t + box["y1"]) * sy, (l + box["x2"]) * sx, (t + box["y2"]) * sy


def _line_up(new, old, ring):
    """`new` warped onto `old` (an edit can shift and zoom a little), judged on the surroundings (`ring`)."""
    a = cv2.cvtColor(old, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    b = cv2.cvtColor(new, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    M = np.eye(2, 3, dtype=np.float32)
    try:
        _, M = cv2.findTransformECC(a, b, M, cv2.MOTION_AFFINE,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5),
                                    ring.astype(np.uint8), 5)
    except cv2.error:
        return new
    if np.abs(M[:, :2] - np.eye(2)).max() > 0.08 or np.abs(M[:, 2]).max() > 0.1 * max(old.shape[:2]):
        return new  # a bad fit: keep it as it came
    return cv2.warpAffine(new, M, (old.shape[1], old.shape[0]), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def _to_qwen(crop, paint, say):
    """The crop painted by Qwen at about CROP_MP (multiples of 32), back at its own size."""
    ch, cw = crop.shape[:2]
    k = math.sqrt(CROP_MP * 1e6 / (cw * ch))
    gw, gh = max(64, round(cw * k / 32) * 32), max(64, round(ch * k / 32) * 32)
    small = cv2.resize(crop, (gw, gh), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LANCZOS4)
    return cv2.resize(np.asarray(paint(small, say)), (cw, ch), interpolation=cv2.INTER_LANCZOS4)


def _matched(new, crop, ring):
    """`new` lined up with and colour-matched to `crop` on the surroundings (`ring`): an edit can shift, zoom and
    tint a little."""
    new = _line_up(new, crop, ring)
    if ring.sum() > 100:
        o, n = crop[ring > 0].astype(np.float32), new[ring > 0].astype(np.float32)
        gain = np.clip(o.std(0) / np.maximum(n.std(0), 1.0), 0.8, 1.25)
        new = np.clip((new.astype(np.float32) - n.mean(0)) * gain + o.mean(0), 0, 255)
    return new


def _slanted_sign(out, b, say, canvas, margins, scale, paint):
    """A sign at an angle (its corners moved): the picture around it straightened so the sign faces Qwen square
    on, as it letters best, painted, and turned back onto the sign; only the sign goes back."""
    H, W = out.shape[:2]
    cw, ch = canvas
    l, t, r, bm = margins
    sx, sy = W / (cw + l + r), H / (ch + t + bm)
    q = np.float32([[(l + x) * sx, (t + y) * sy] for x, y in quad_of(b)])
    tl, tr, br, bl = q
    sw = float(np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    sh = float(np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    if sw < 4 or sh < 4:
        return
    mx, my = max(MIN_CONTEXT * scale, sw * CONTEXT), max(MIN_CONTEXT * scale, sh * CONTEXT)
    CW, CH = int(round(sw + 2 * mx)), int(round(sh + 2 * my))
    M = cv2.getPerspectiveTransform(q, np.float32([[mx, my], [mx + sw, my], [mx + sw, my + sh], [mx, my + sh]]))
    crop = cv2.warpPerspective(out, M, (CW, CH), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    new = _to_qwen(crop, paint, say)
    gx, gy = sw * PASTE_GROW, sh * PASTE_GROW
    hard = np.zeros((CH, CW), np.uint8)
    hard[int(max(0, my - gy)):int(min(CH, my + sh + gy)), int(max(0, mx - gx)):int(min(CW, mx + sw + gx))] = 1
    fade = max(2.0, min(sw, sh) * PASTE_GROW)
    ring = (cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5) > fade).astype(np.uint8)
    new = _matched(new, crop, ring)
    d = np.clip(1 - cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5) / fade, 0, 1)
    m = (d * d * (3 - 2 * d)).astype(np.float32)
    # back onto the picture, only over the sign and its fade (the rest stays untouched, not resampled)
    reach = np.float32([[[0, 0]], [[CW, 0]], [[CW, CH]], [[0, CH]]])
    back = cv2.perspectiveTransform(reach, np.linalg.inv(M)).reshape(-1, 2)
    X1, Y1 = (int(max(0, np.floor(v))) for v in back.min(0))
    X2, Y2 = int(min(W, np.ceil(back[:, 0].max()))), int(min(H, np.ceil(back[:, 1].max())))
    if X2 <= X1 or Y2 <= Y1:
        return
    inv = np.linalg.inv(np.float64([[1, 0, -X1], [0, 1, -Y1], [0, 0, 1]]) @ np.linalg.inv(M) @ np.eye(3))
    size = (X2 - X1, Y2 - Y1)
    new_back = cv2.warpPerspective(new.astype(np.float32), inv, size, flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP)
    m_back = cv2.warpPerspective(m, inv, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)[..., None]
    region = out[Y1:Y2, X1:X2].astype(np.float32)
    out[Y1:Y2, X1:X2] = np.round(region * (1 - m_back) + np.clip(new_back, 0, 255) * m_back).astype(np.uint8)


def repaint(render, boxes, canvas, margins, scale, paint, progress=lambda i, n, say: None):
    """render (RGB array) with each box's sign repainted to read its "say". paint(crop, say) -> the crop,
    repainted, same size. scale: render pixels per original pixel."""
    out = render.copy()
    H, W = out.shape[:2]
    todo = [b for b in boxes if (b.get("say") or "").strip()]
    for i, b in enumerate(todo):
        say = b["say"].strip()
        progress(i, len(todo), say)
        if slanted(b):
            _slanted_sign(out, b, say, canvas, margins, scale, paint)
            continue
        x1, y1, x2, y2 = to_render(b, canvas, margins, (W, H))
        bw, bh = x2 - x1, y2 - y1
        mx = max(MIN_CONTEXT * scale, bw * CONTEXT)
        my = max(MIN_CONTEXT * scale, bh * CONTEXT)
        X1, Y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
        X2, Y2 = int(min(W, x2 + mx)), int(min(H, y2 + my))
        crop = out[Y1:Y2, X1:X2]
        ch, cw = crop.shape[:2]
        if cw < 8 or ch < 8:
            continue
        new = _to_qwen(crop, paint, say)
        # the sign (grown a little) and a soft edge around it; the rest of the crop is what lines it up
        gx, gy = bw * PASTE_GROW, bh * PASTE_GROW
        hard = np.zeros((ch, cw), np.uint8)
        hard[int(max(0, y1 - gy - Y1)):int(min(ch, y2 + gy - Y1)), int(max(0, x1 - gx - X1)):int(min(cw, x2 + gx - X1))] = 1
        fade = max(2.0, min(bw, bh) * PASTE_GROW)
        ring = (cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5) > fade).astype(np.uint8)
        new = _matched(new, crop, ring)
        d = np.clip(1 - cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5) / fade, 0, 1)
        m = (d * d * (3 - 2 * d))[..., None]
        out[Y1:Y2, X1:X2] = np.round(crop * (1 - m) + new * m).astype(np.uint8)
    return out
