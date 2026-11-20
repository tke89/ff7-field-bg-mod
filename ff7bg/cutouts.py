"""See-through walk-behind art (fences, grilles, railings): its transparency from the new painting.

Layer-1 tiles are drawn over the characters that walk behind them, and FFNx blends a page's pixels by their alpha.
The pages' alpha is the original's, upscaled smoothly (sheets.tile_alpha): fine for solid scenery, but a chain-link
fence's 1-pixel wires came out as a blurry grid that's mostly half see-through (mds7_w2, the Beginner's Hall: 80%
of the fence's pixels), in the original's blocky pattern, while the AI repaints the mesh finer and elsewhere. So a
character behind the fence showed a ghostly old mesh, with bits of crate and floor colour in it, where the painting
around them has a crisp new one (2026-11-20).

Here, where the original cut-out is full of small holes (a mesh), its wires are found in the new painting instead:
thin lines darker (or lighter) than what shows through the holes, as the original's wires are. Where the painting
can't tell (wires as dark as what's behind them), or what it finds doesn't look like the original's mesh (leaves,
not wires), the original's mesh is used with crisp edges. Solid parts keep the usual smooth alpha.
"""
import cv2
import numpy as np

MIN_MESH = 50  # px at 1x: smaller meshes are left as they are


def _lum(rgb):
    return rgb[..., :3].astype(np.float32) @ np.float32([0.299, 0.587, 0.114])


def _odd(n):
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def _ramp(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0, 1)


def mesh_zone(alpha1):
    """(inside, holes, mesh) at 1x for one sheet's cut-out alpha: the cut-outs' outline with their small holes filled,
    the holes, and the mesh: what lies next to a hole inside the outline."""
    m = (alpha1 > 0).astype(np.uint8)
    inside = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    holes = inside & (1 - m)
    mesh = cv2.dilate(holes, np.ones((3, 3), np.uint8)) & inside
    return inside, holes, mesh


def painted_wires(lum, dark, scale):
    """How wire-like each pixel of the new painting is (0-1), and where that can be told (0-1): thin lines darker
    (dark=True) or lighter than what's around them."""
    L = lum if dark else 255 - lum
    behind = cv2.morphologyEx(L, cv2.MORPH_CLOSE,  # thin dark lines closed over: what shows through the holes
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(2.75 * scale),) * 2))
    wire = cv2.erode(L, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(3.75 * scale),) * 2))  # the wires nearby
    contrast = behind - wire
    found = _ramp((behind - L) / np.maximum(contrast, 1), 0.25, 0.75)
    sure = cv2.GaussianBlur(_ramp(contrast, 15, 35), (0, 0), 0.75 * scale)
    return found, sure


def fine_alpha(alpha1, fence1, behind1, picture, scale, notes=None):
    """New alpha for one sheet's cut-outs where they're a mesh, at the output scale.

    alpha1, fence1: the sheet's original alpha (0-255) and colours at 1x, in screen space; behind1: the original scene
    at 1x (what shows through the holes); picture: the new art at the output scale (RGB, screen space).
    Returns (alpha 0-255 float, where it applies 0-1) or None when the sheet has no mesh."""
    inside, holes, mesh = mesh_zone(alpha1)
    if mesh.sum() < MIN_MESH:
        return None
    h, w = alpha1.shape
    H, W = h * scale, w * scale
    up = lambda x: cv2.resize(x.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    crisp = _ramp(up(alpha1 / 255), 0.3, 0.7)  # the original's mesh with sharp edges
    out = crisp.copy()
    where = np.zeros((H, W), np.float32)
    lum_f, lum_b = _lum(fence1), _lum(behind1)
    n, comp = cv2.connectedComponents(inside, connectivity=8)
    m = alpha1 > 0
    for c in range(1, n):
        zone = (comp == c) & (mesh > 0)
        if zone.sum() < MIN_MESH:
            continue
        ys, xs = np.nonzero(zone)
        pad = 3
        y0, y1, x0, x1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, h), max(xs.min() - pad, 0), min(xs.max() + pad + 1, w)
        sl1 = (slice(y0, y1), slice(x0, x1))
        slS = (slice(y0 * scale, y1 * scale), slice(x0 * scale, x1 * scale))
        z1 = zone[sl1]
        zS = _ramp(cv2.resize(z1.astype(np.float32), (z1.shape[1] * scale, z1.shape[0] * scale),
                              interpolation=cv2.INTER_LINEAR), 0.3, 0.7)
        where[slS] = np.maximum(where[slS], zS)
        wires, gaps = z1 & m[sl1], z1 & (holes[sl1] > 0)
        if not wires.any() or not gaps.any():
            continue
        dark = lum_f[sl1][wires].mean() < lum_b[sl1][gaps].mean()
        found, sure = painted_wires(_lum(picture[slS]), dark, scale)
        inz = zS > 0.5
        # does the painting show a mesh like the original's here? Enough of it can be told, and its wires cover
        # about as much (a 1-pixel wire is a quarter of a 4x pixel's width or more once painted)
        told = sure[inz].mean() if inz.any() else 0
        orig_cover = wires.sum() / z1.sum()
        new_cover = (found * sure)[inz].sum() / max(sure[inz].sum(), 1)
        used = told >= 0.5 and 0.65 * orig_cover <= new_cover <= 1.5 * orig_cover
        if notes is not None:
            notes.append({"at": (int(x0), int(y0), int(x1 - x0), int(y1 - y0)), "dark": bool(dark),
                          "told": round(float(told), 2), "cover": (round(float(orig_cover), 2), round(float(new_cover), 2)),
                          "painted": bool(used)})
        if not used:
            continue  # keep the crisp original mesh
        mix = sure * found + (1 - sure) * crisp[slS]
        out[slS] = np.where(zS > 0, zS * mix + (1 - zS) * out[slS], out[slS])
    return out * 255, where
