"""Touch-up: the parts of a finished render the user painted over, repainted with the field's own settings.

In the Studio the user brushes over what doesn't look right (a smeared face, a garbled corner, a halo), optionally
saying what should be there. Each marked area goes to ComfyUI with the picture around it as it is, through the
states pass's masked repaint (comfy.build_states_workflow): the version's own model, description and style images
repaint only inside the marks, at the strength asked, so the new part matches its surroundings. It goes back
faded in at its edges, so nothing outside the marks changes. The Studio makes a new version of it (run_touchup in
studio.py).
"""
import math

import cv2
import numpy as np

CONTEXT = 0.75     # the crop around a marked area: its box and this much of its size on every side...
MIN_CONTEXT = 32   # ...at least this many original pixels...
MIN_CROP = 128     # ...and at least this big (original pixels): the model needs room to see the style around it
CROP_MP = 1.0      # the model paints the crop at about this size
JOIN = 24          # marks closer than this (original pixels) are repainted together
FEATHER = 3.0      # the repaint fades into what was there over this many original pixels, past the marks
MAX_SHIFT = 12.0   # the most the colour match moves the repainted part (0-255)


def hint_prompt(s, hint):
    """The version's settings for repainting a part: the user's words for it first, without the whole scene's
    description (it outweighed them: asked for a stone lantern, datiao_1 got another statue), else a plea for clean
    detail on the usual prompt. Every model reads the prompt (the edit models through their instruction)."""
    hint = (hint or "").strip().rstrip(".")
    if hint:
        return dict(s, prompt=f"{hint}. Painted in exactly the same style, light and colours as the rest of the "
                              f"picture. {s['prompt'].strip()}", scene="")
    return dict(s, prompt=s["prompt"].rstrip() + " Clean, well-defined detail: nothing smeared, melted, doubled or garbled.")


def full_mask(mask, canvas, margins, size):
    """The user's marks (0..1, the canvas at any scale) -> render pixels, through the render's margins (1x px per
    side: the field's padding plus the version's frame)."""
    cw, ch = canvas
    l, t, r, b = margins
    W, H = size
    sx, sy = W / (cw + l + r), H / (ch + t + b)
    x0, y0, w, h = round(l * sx), round(t * sy), round(cw * sx), round(ch * sy)
    m = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((H, W), np.float32)
    out[y0:y0 + h, x0:x0 + w] = m[:max(0, H - y0), :max(0, W - x0)]
    return out


def areas(mask, scale):
    """The marked areas as render-pixel boxes (x1, y1, x2, y2), marks near each other together."""
    hard = (mask > 0.5).astype(np.uint8)
    if not hard.any():
        return []
    j = max(3, int(JOIN * scale)) | 1
    joined = cv2.dilate(hard, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (j, j)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(joined)
    return [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h, _ in stats[1:]]


def _disc(r):
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def repaint(render, mask, scale, paint, progress=lambda i, n: None):
    """(render with each marked area repainted, how many areas). mask: 0..1 at render size. scale: render pixels
    per original pixel. paint(crop, model mask, gen size) -> the crop repainted inside the model mask, crop size:
    the crop and mask come at the model's size (about CROP_MP, multiples of 32)."""
    out = render.copy()
    H, W = out.shape[:2]
    boxes = areas(mask, scale)
    f = max(2.0, FEATHER * scale)
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        progress(i, len(boxes))
        bw, bh = x2 - x1, y2 - y1
        mx = max(MIN_CONTEXT * scale, bw * CONTEXT, (MIN_CROP * scale - bw) / 2)
        my = max(MIN_CONTEXT * scale, bh * CONTEXT, (MIN_CROP * scale - bh) / 2)
        X1, Y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
        X2, Y2 = int(min(W, x2 + mx)), int(min(H, y2 + my))
        crop, m = out[Y1:Y2, X1:X2], mask[Y1:Y2, X1:X2]
        ch, cw = crop.shape[:2]
        if cw < 8 or ch < 8:
            continue
        k = math.sqrt(CROP_MP * 1e6 / (cw * ch))
        gw, gh = max(256, round(cw * k / 32) * 32), max(256, round(ch * k / 32) * 32)
        marks = (m > 0.5).astype(np.uint8)
        pasted = cv2.dilate(marks, _disc(f))  # what goes back: the marks grown a little...
        weight = np.clip(cv2.GaussianBlur(pasted.astype(np.float32), (0, 0), f / 2), 0, 1)  # ...faded at the edge
        # the model repaints a little further out, so its own edge never shows in what goes back
        model = cv2.GaussianBlur(cv2.dilate(marks, _disc(2.5 * f)).astype(np.float32), (0, 0), f / 2)
        up = cv2.INTER_AREA if k < 1 else cv2.INTER_LANCZOS4
        new = np.asarray(paint(cv2.resize(crop, (gw, gh), interpolation=up),
                               np.round(np.clip(cv2.resize(model, (gw, gh)), 0, 1) * 255).astype(np.uint8),
                               (gw, gh)), np.float32)
        if new.shape[:2] != (ch, cw):
            new = cv2.resize(new, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
        # colour matched on a ring just outside what goes back, where nothing should have changed
        ring = (cv2.dilate(pasted, _disc(3 * f)) > 0) & (model < 0.05)
        if ring.sum() > 100:
            new += np.clip(crop[ring].astype(np.float32).mean(0) - new[ring].mean(0), -MAX_SHIFT, MAX_SHIFT)
        a = weight[..., None]
        out[Y1:Y2, X1:X2] = np.round(crop * (1 - a) + np.clip(new, 0, 255) * a).astype(np.uint8)
    return out, len(boxes)
