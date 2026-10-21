"""Signs and lettering: found in the original art, then repainted only lightly so the text stays readable.

At the repaint strengths that restyle a scene well (0.3-0.5), letters turn into made-up glyphs (Kalm's shop
signs, Shin-Ra's lobby sign). Qwen3.5 (the scene describer, through ComfyUI's TextGenerate) points out the
signs as boxes, once per field; the user can add the ones it misses and remove wrong ones. The render then
uses a per-pixel repaint strength (Differential Diffusion): `text_repaint` on the signs, rising smoothly to
the scene's own over TEXT_FADE original pixels, so there's no visible edge. The edit models (Qwen, FLUX 3,
Mage-Flow, FIBO 2) paint from scratch instead; they start from the original on the signs and blend it back
in at every step, with the same fading mask. Qwen only with its depth lock: otherwise it shifts the picture
by several pixels and the pinned signs no longer line up (Kalm's INN sign came out doubled).
"""
import json
import re

import cv2
import numpy as np

from .comfy import CAPTION_CLIP, CAPTION_MP, _resize, work_size

TEXT_ASK = (
    "List every sign in this image: anything with letters, numbers, Japanese or Chinese characters, a logo or "
    "symbols on it, including small ones on walls, doors, counters, screens and machines. Give a box around each. "
    'Answer with JSON only, a list like [{"text": "what it says, or a short description", "bbox_2d": [x1, y1, x2, y2]}], '
    "with coordinates from 0 to 1000 relative to the image's width and height. If there are none, answer [].")
# The finder answers twice and the signs from both are kept: plainly (the most likely words: finds the most
# signs, but sometimes gets stuck repeating one, or marching a box down the picture) and with a little
# randomness and a penalty on repeating itself (names fewer signs, but different ones: blin1's Shin-Ra logo).
TEXT_SAMPLED = {"temperature": 0.2, "top_k": 64, "top_p": 0.95, "min_p": 0.05, "repetition_penalty": 1.05, "seed": 0}
TEXT_GROW = 0.10   # each box padded by this share of its size
TEXT_FADE = 18     # original pixels over which the repaint rises from the signs' to the scene's (tested on blin1)


def build_find_workflow(input_name, s, w, h):
    """Ask for the signs, looking at the original art as the AI gets it (enlarged by the workflow's
    original-art upscaler, then about CAPTION_MP). Both answers (TEXT_SAMPLED) come back through PreviewAny."""
    clip, clip_type = CAPTION_CLIP
    wf = {"img": {"class_type": "LoadImage", "inputs": {"image": input_name}}}
    work = _resize(wf, "work", s, ["img", 0], *work_size(s, w, h))
    wf.update({
        "small": {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": work, "upscale_method": "lanczos", "megapixels": CAPTION_MP, "resolution_steps": 32}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": clip_type}},
        "gen": {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": TEXT_ASK, "max_length": 800, "sampling_mode": "off",
            "image": ["small", 0], "thinking": False}},
        "gen2": {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": TEXT_ASK, "max_length": 800, "sampling_mode": "on",
            **{f"sampling_mode.{k}": v for k, v in TEXT_SAMPLED.items()},
            "image": ["small", 0], "thinking": False}},
        "both": {"class_type": "StringConcatenate", "inputs": {
            "string_a": ["gen", 0], "string_b": ["gen2", 0], "delimiter": "\n"}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["both", 0]}},
    })
    return wf


def _items(text):
    """(bbox, text) for each sign in the answer. Read one {...} at a time rather than as a whole JSON list:
    the model sometimes gets stuck repeating its last sign until it runs out of room, leaving the list
    unfinished (blin1), and the signs it named before that are still good."""
    for chunk in re.findall(r"\{[^{}]*", text):
        b = re.search(r'"bbox_2d"\s*:\s*\[([^\]]*)\]', chunk)
        t = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', chunk)
        if not b:
            continue
        try:
            label = json.loads(f'"{t.group(1)}"') if t else ""
        except ValueError:
            label = t.group(1)
        yield b.group(1).split(","), label


def _overlap(a, b):
    """Intersection over union of two boxes."""
    iw = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"])
    ih = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"])
    if iw <= 0 or ih <= 0:
        return 0.0
    i = iw * ih
    return i / ((a["x2"] - a["x1"]) * (a["y2"] - a["y1"]) + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - i)


def parse_boxes(text, w, h):
    """The model's answer -> [{x1, y1, x2, y2, text, auto}] in original pixels (its coordinates are 0-1000),
    each sign once."""
    raw = []
    for b, label in _items(text):
        try:
            x1, y1, x2, y2 = (float(v) for v in b)
        except ValueError:
            continue
        x1, x2 = sorted((max(0.0, min(1000.0, x1)), max(0.0, min(1000.0, x2))))
        y1, y2 = sorted((max(0.0, min(1000.0, y1)), max(0.0, min(1000.0, y2))))
        raw.append(((x1, y1, x2, y2), label))
    looped = set()
    for i in range(2, len(raw)):
        if _marching(*(r[0] for r in raw[i - 2:i + 1])):
            looped |= {i - 1, i}
    out = []
    for i, ((x1, y1, x2, y2), label) in enumerate(raw):
        if i in looped:
            continue
        box = {"x1": round(x1 / 1000 * w, 1), "y1": round(y1 / 1000 * h, 1),
               "x2": round(x2 / 1000 * w, 1), "y2": round(y2 / 1000 * h, 1)}
        bw, bh = box["x2"] - box["x1"], box["y2"] - box["y1"]
        if bw < 2 or bh < 2 or bw * bh > 0.4 * w * h:  # specks, or "the whole picture"
            continue
        if any(_overlap(box, o) > 0.7 for o in out):  # the same sign again
            continue
        out.append({**box, "text": str(label)[:60], "auto": True})
    return out


def _marching(a, b, c):
    """Three boxes in a row the same size, each moved by the same step: the model stuck in a loop, walking a
    made-up box across the picture (Kalm: 15 of them down one wall). The first of them is kept."""
    same = lambda p, q: all(abs(u - v) <= 1.5 for u, v in zip(p, q))
    size = lambda r: (r[2] - r[0], r[3] - r[1])
    step = lambda p, q: (q[0] - p[0], q[1] - p[1])
    return same(size(a), size(b)) and same(size(b), size(c)) and same(step(a, b), step(b, c))


def clean_boxes(boxes, w, h):
    """Boxes from the Studio (the user's edits), checked and tidied."""
    out = []
    for b in boxes or []:
        try:
            x1, x2 = sorted((float(b["x1"]), float(b["x2"])))
            y1, y2 = sorted((float(b["y1"]), float(b["y2"])))
        except (KeyError, TypeError, ValueError):
            continue
        x1, x2, y1, y2 = max(0.0, x1), min(float(w), x2), max(0.0, y1), min(float(h), y2)
        if x2 - x1 >= 1 and y2 - y1 >= 1:
            out.append({"x1": round(x1, 1), "y1": round(y1, 1), "x2": round(x2, 1), "y2": round(y2, 1),
                        "text": str(b.get("text", ""))[:60], "auto": bool(b.get("auto"))})
    return out


def masks(boxes, w, h, scale, inside):
    """(repaint, keep) masks at w*scale x h*scale, 8-bit. repaint: `inside` (the signs' strength relative to
    the scene's) on the padded boxes, rising smoothly to 1 over TEXT_FADE original pixels; keep: how much
    each pixel is a sign (1 on them, fading to 0), for keeping them through the detail pass."""
    hard = np.zeros((h * scale, w * scale), np.uint8)
    for b in boxes:
        gx = (b["x2"] - b["x1"]) * TEXT_GROW / 2 + 1
        gy = (b["y2"] - b["y1"]) * TEXT_GROW / 2 + 1
        x1, y1 = max(0, round((b["x1"] - gx) * scale)), max(0, round((b["y1"] - gy) * scale))
        x2, y2 = min(w * scale, round((b["x2"] + gx) * scale)), min(h * scale, round((b["y2"] + gy) * scale))
        hard[y1:y2, x1:x2] = 1
    r = np.clip(cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5) / (TEXT_FADE * scale), 0, 1)
    r = r * r * (3 - 2 * r)  # smoothstep: no line where the fade starts or ends
    repaint = inside + (1 - inside) * r
    return (repaint * 255).round().astype(np.uint8), ((1 - r) * 255).round().astype(np.uint8)
