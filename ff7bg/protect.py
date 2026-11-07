"""Dark, empty parts of the original art (plain walls in shadow, murky backdrops, the black around a room): the
repainting models tend to invent things there (MOSS painted a cloudy night sky over Sector 1 station's dark wall),
so the main render repaints them more lightly ("protect_flat", at "flat_repaint"), fading into the rest.

Dark, smooth and without edges, all three: brick walls, wood floors and cave rock have edges, so they stay out.
Measured on the original at its own size."""
import cv2
import numpy as np

DARK = (0.20, 0.26)     # brightness (0-1): fully protected below the first, not at all above the second
SMOOTH = (0.03, 0.045)  # local brightness spread over about 6 px: the same, for texture
EMPTY = (0.20, 0.26)    # share of nearby pixels on an edge (Canny): the same, for structure
MIN_SIZE = 9            # px of the original: smaller dark gaps between details stay out


def _below(v, lo, hi):
    """1 at or below lo, 0 at or above hi, a straight ramp between."""
    return np.clip((hi - v) / (hi - lo), 0, 1)


def flat_dark(img):
    """How much each pixel of the original (RGB uint8, its own size) is a dark, empty area: 0-1 float."""
    lum = (img.astype(np.float32) / 255) @ np.array([0.299, 0.587, 0.114], np.float32)
    mean = cv2.GaussianBlur(lum, (0, 0), 3)
    spread = np.sqrt(np.maximum(cv2.GaussianBlur(lum * lum, (0, 0), 3) - mean ** 2, 0))
    edges = (cv2.Canny((lum * 255).astype(np.uint8), 20, 50) > 0).astype(np.float32)
    w = _below(mean, *DARK) * _below(spread, *SMOOTH) * _below(cv2.GaussianBlur(edges, (0, 0), 4), *EMPTY)
    w = cv2.morphologyEx(w, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MIN_SIZE, MIN_SIZE)))
    return np.clip(cv2.GaussianBlur(w, (0, 0), 2), 0, 1)
