"""Pull an AI-restyled image back onto the original field geometry.

The game's occlusion (layer-1 cut-outs) and walkmesh are fixed to the original art, so a restyle
that drifts by more than a pixel or two shows up in game as halos or characters walking "through"
scenery. We find where structures of the original landed in the AI image (subpixel matches of
gradient-magnitude patches, robust to recoloring) and undo it with one global transform.

Only global: a free-form displacement field also "fixes" every locally redrawn detail and bends
straight towers and walls. Edit models like Qwen-Image drift almost entirely globally (a slightly
different zoom in x and y plus a shift): a homography leaves 0.3-0.6 px median residual on the fields
tested, from 1-8 px before.
"""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class DriftStats:
    patches: int
    median: float
    p90: float
    max: float

    def __str__(self):
        return f"median {self.median:.1f}px, p90 {self.p90:.1f}px, max {self.max:.1f}px ({self.patches} patches)"


def drift_stats(dist):
    """Stats of per-match distances (1x pixels)."""
    if len(dist) == 0:
        return DriftStats(0, float("nan"), float("nan"), float("nan"))
    return DriftStats(len(dist), float(np.median(dist)), float(np.percentile(dist, 90)), float(np.max(dist)))


def _gradmag(rgb, blur):
    g = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), blur)
    m = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return m / (m.mean() + 1e-6)


def match_patches(ref, img, k=2, patch=20, step=6, radius=12, min_score=0.6, guess=None):
    """Where each patch of ref (1x RGB) is found in img (any size, same framing).

    Matching runs at k times the original size with subpixel peaks; patches whose second-best match
    is nearly as good (repetitive textures) are dropped. With `guess` (a transform from fit_global),
    each search window is centred where the guess puts the patch. Returns Nx5 (cx, cy, dx, dy, score)
    in 1x units."""
    h, w = ref.shape[:2]
    gr = _gradmag(cv2.resize(ref, (w * k, h * k), interpolation=cv2.INTER_CUBIC), 1.5)
    gi = _gradmag(cv2.resize(img, (w * k, h * k), interpolation=cv2.INTER_AREA), 1.5)
    P, S, R = patch * k, step * k, radius * k
    out = []
    for y in range(0, h * k - P + 1, S):
        for x in range(0, w * k - P + 1, S):
            tpl = gr[y : y + P, x : x + P]
            if tpl.std() < 0.35:
                continue  # flat area, nothing to lock onto
            ox = oy = 0
            if guess is not None:
                c = np.array([[(x + P / 2) / k, (y + P / 2) / k]])
                ox, oy = np.round((_project(guess, c) - c)[0] * k).astype(int)
            x0, y0 = max(0, x + ox - R), max(0, y + oy - R)
            x1, y1 = min(w * k, x + ox + P + R), min(h * k, y + oy + P + R)
            if x1 - x0 < P or y1 - y0 < P:
                continue
            res = cv2.matchTemplate(gi[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, (lx, ly) = cv2.minMaxLoc(res)
            if score < min_score:
                continue
            second = res.copy()
            cv2.circle(second, (lx, ly), 3 * k, -1, -1)
            if second.max() > score - 0.08:
                continue
            sx = sy = 0.0  # parabola through the peak and its neighbours
            if 0 < lx < res.shape[1] - 1:
                a, b, c = res[ly, lx - 1], res[ly, lx], res[ly, lx + 1]
                sx = 0.5 * (a - c) / (a - 2 * b + c - 1e-9)
            if 0 < ly < res.shape[0] - 1:
                a, b, c = res[ly - 1, lx], res[ly, lx], res[ly + 1, lx]
                sy = 0.5 * (a - c) / (a - 2 * b + c - 1e-9)
            out.append(((x + P / 2) / k, (y + P / 2) / k, (x0 + lx + sx - x) / k, (y0 + ly + sy - y) / k, score))
    return np.array(out, np.float32).reshape(-1, 5)


def _project(Hm, pts):
    p = np.c_[pts, np.ones(len(pts))] @ Hm.T
    return p[:, :2] / p[:, 2:]


def find_drift(ref, img):
    """Matches between the original (1x) and the AI image: a coarse pass with a wide search finds the
    overall zoom and shift, then a fine subpixel pass searches close to where that puts each patch."""
    coarse = match_patches(ref, img, k=1, patch=32, step=24, radius=40)
    h, w = ref.shape[:2]
    guess = fit_global(coarse, w, h, min_matches=12, threshold=2.0)
    return match_patches(ref, img, k=2, patch=20, step=6, radius=6 if guess is not None else 12, guess=guess)


def residuals(vecs, Hm):
    """Distance between where each match landed and where the transform puts it (1x pixels)."""
    src = vecs[:, :2].astype(np.float64)
    return np.hypot(*(src + vecs[:, 2:4] - _project(Hm, src)).T)


def fit_global(vecs, w, h, min_matches=30, threshold=1.5):
    """3x3 transform from original pixel positions to AI-image positions (1x units), or None.

    A homography (straight lines stay straight), or an affine fit when that explains the matches
    better; rejected when it would move the frame by more than 15% (a bad fit, not drift)."""
    if len(vecs) < min_matches:
        return None
    src = vecs[:, :2].astype(np.float64)
    dst = src + vecs[:, 2:4]
    fits = []
    Hm, _ = cv2.findHomography(src, dst, cv2.RANSAC, threshold, maxIters=5000, confidence=0.999)
    if Hm is not None:
        fits.append(Hm)
    A, _ = cv2.estimateAffine2D(src, dst, ransacReprojThreshold=threshold, maxIters=5000, confidence=0.999)
    if A is not None:
        fits.append(np.vstack([A, [0, 0, 1]]))
    if not fits:
        return None
    best = min(fits, key=lambda m: np.percentile(residuals(vecs, m), 90))
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], np.float64)
    if np.abs(_project(best, corners) - corners).max() > 0.15 * max(w, h):
        return None
    return best


def warp_global(img, Hm, w, h, scale, src_size=None):
    """Resample img (the AI image, any size) onto the original w x h geometry at `scale`, in one
    interpolation step. Hm maps canvas positions to img positions in 1x units; img covers src_size
    1x pixels (default: the canvas).

    Returns (warped, valid): valid is False where the transform needs pixels the AI image doesn't have
    (a generator that zoomed in and cropped)."""
    fh, fw = img.shape[:2]
    src_w, src_h = src_size or (w, h)
    sx, sy = fw / src_w, fh / src_h
    out_to_1x = np.array([[1 / scale, 0, 0.5 / scale - 0.5], [0, 1 / scale, 0.5 / scale - 0.5], [0, 0, 1]])
    ai_1x_to_full = np.array([[sx, 0, 0.5 * sx - 0.5], [0, sy, 0.5 * sy - 0.5], [0, 0, 1]])
    M = ai_1x_to_full @ Hm @ out_to_1x  # output pixel -> AI image pixel
    W, H = w * scale, h * scale
    warped = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                                 borderMode=cv2.BORDER_REPLICATE)
    valid = cv2.warpPerspective(np.full((fh, fw), 255, np.uint8), M, (W, H),
                                flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
    return warped, valid
