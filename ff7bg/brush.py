"""Brush over: the marked parts of a finished render repainted as broad brush strokes, with no AI.

Ground, rock and walls that were tightly textured in the small original come out of every enlargement oddly: the SYW
pack sharpens the grain into crunchy specks, and MOSS paints it as outlined cells or netting (las0_7's rock, las1_3's
floor). Repainting those parts with the AI afterwards didn't help (2026-11-19 tests on the user's Finals): MOSS Turbo
at 50-65% invented things in the rock (an arm, plants, a face on las1_3's skull-masked figure), the Full model kept
the grain, and smoothing first gave camouflage blobs or blur. A painterly filter did: the generalized Kuwahara filter
(Papari et al. 2007) gives each pixel the average of the calmest wedge of the area around it, so grain and thin cell
outlines go, edges between real shapes stay, and the surface reads as painted flakes that follow the rock. It takes
seconds and can't invent anything.

The Studio's touch-up runs it ("Repaint with: Brush strokes"), usually over the parts texture.texture_mask finds
("Find ground & walls"), with what should stay (characters) rubbed out by the user.
"""
import cv2
import numpy as np

SIZES = (0.5, 4.0)  # the brush, in original pixels: the range allowed...
DEFAULT = 1.5       # ...and the size that cleared las0_7's grain and las1_3's netting (2026-11-19)
SECTORS = 8


def _sectors(r, n=SECTORS):
    """n soft wedges over a disc of radius r, each weighted by a Gaussian (sigma r/2) and summing to 1."""
    y, x = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    ang = np.arctan2(y, x)
    g = np.exp(-(x * x + y * y) / (2 * (r / 2) ** 2)) * ((x * x + y * y) <= r * r)
    out = []
    for i in range(n):
        d = np.angle(np.exp(1j * (ang - 2 * np.pi * i / n)))
        w = np.exp(-2 * (d / (np.pi / n)) ** 2)
        w[r, r] = 1.0 / n
        k = (w * g).astype(np.float32)
        out.append(k / k.sum())
    return out


def kuwahara(img, r):
    """img (uint8 RGB) with each pixel the mean of the wedges around it (radius r pixels) weighted to the calmest."""
    f = img.astype(np.float32) / 255
    num = np.zeros_like(f)
    den = np.zeros(f.shape[:2], np.float32)
    for k in _sectors(r):
        m = cv2.filter2D(f, -1, k, borderType=cv2.BORDER_REFLECT)
        var = np.maximum((cv2.filter2D(f * f, -1, k, borderType=cv2.BORDER_REFLECT) - m * m).sum(-1), 0)
        w = 1.0 / (1.0 + (255 * np.sqrt(var)) ** 8 * 1e-8)  # a calm wedge (spread under ~10 of 255) counts fully
        num += m * w[..., None]
        den += w
    return np.clip(num / np.maximum(den, 1e-12)[..., None] * 255, 0, 255).astype(np.uint8)


def brush_over(img, mask, size, scale):
    """img with the brush strokes faded in where mask says (0..1 at img size). size: the brush in original pixels;
    scale: img pixels per original pixel. Only the marked part's box (and a margin) is filtered."""
    if mask.max() <= 0:
        return img
    r = max(2, int(round(float(np.clip(size, *SIZES)) * scale)))
    a = np.clip(cv2.GaussianBlur(mask.astype(np.float32), (0, 0), max(1.0, scale)), 0, 1)  # brushed marks' edges fade
    ys, xs = np.nonzero(a > 0.004)
    H, W = a.shape
    x1, y1 = max(0, xs.min() - 2 * r), max(0, ys.min() - 2 * r)
    x2, y2 = min(W, xs.max() + 2 * r + 1), min(H, ys.max() + 2 * r + 1)
    out = img.copy()
    crop = img[y1:y2, x1:x2]
    w = a[y1:y2, x1:x2, None]
    out[y1:y2, x1:x2] = np.round(crop * (1 - w) + kuwahara(crop, r) * w).astype(np.uint8)
    return out
