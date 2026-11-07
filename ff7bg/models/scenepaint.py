"""Battle scenes painted flat, done better (user, 2026-11-07, battle scene oo: "still quite blurry and bad looking").

- The sky and far scenery as one panorama (paint_panorama): a battle scene's sky dome is laid from separate bands
  of one texture (oo's two bands of cloud) and repeated round it. Painted flat, each band was its own painting and
  they met at a hard line across the sky. Here the far textures are drawn out as the view all round from the middle
  of the action (every battle camera stands near it, the sky is far away), that one picture is painted (wrapping
  round), and every texel takes its paint from where it lands in it.
- Ground detail (Restyler.sheet's detail): the floor is a 256 px texture stretched over the arena, about 10 game
  units a texel even at 4x, several screen pixels each near the camera: no painting looks sharp that coarse.
  Ground textures (classify) are painted at twice the size and given the Studio's detail pass (tiles repainted
  lightly at full size: grass blades, stones, cracks).
- Seams (level_seams): where two textures, or two parts of one, meet in the scene but not on the sheet, the colours
  on each side are pulled to meet over a band of texels.
"""
import math

import cv2
import numpy as np

from . import render

FAR_SHARE = 0.55      # classify: a texture this far out (median distance over the scene's furthest) ...
FAR_UPRIGHT = 0.6     # ...and this upright (median |normal y| under 1 - this) is sky or far scenery ...
FAR_HIGH = 0.15       # ...and so is one that far out this high over the floor (of the scene's size): a dome's top
                      # curves over the arena (oo's sky, area-weighted |normal y| 0.58)
GROUND_FLAT = 0.85    # a texture whose triangles face up this squarely (area-weighted |normal y|) ...
GROUND_LOW = 0.08     # ...within this share of the scene's size from the floor is ground
PANORAMA_W = 2048     # the panorama's width (one repeat of the sky round the arena)
PANORAMA_MAX_ASPECT = 3.0  # ...and its height at least width / this (very wide pictures paint poorly)
SEAM_BAND = 16        # level_seams: texels each side the colours are pulled over


def classify(mesh):
    """Per mesh texture: {"ground": bool, "far": bool, "radius", "height", "up"} from where its triangles are: far out
    and upright (the sky dome, a strip of mountains) or flat and low (the floor)."""
    p = mesh.pos.astype(np.float64)
    c = p.mean(1)
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    up = np.abs(n[:, 1]) / np.maximum(2 * area, 1e-9)
    r = np.hypot(c[:, 0], c[:, 2])
    rmax = max(float(r.max()) if len(r) else 1.0, 1.0)
    floor = float(np.percentile(c[:, 1], 90)) if len(c) else 0.0  # y down: the floor is near the largest y
    out = []
    for k in range(len(mesh.textures)):
        sel = mesh.tex == k
        if not sel.any():
            out.append({"ground": False, "far": False, "radius": 0.0, "height": 0.0, "up": 0.0})
            continue
        w = area[sel]
        flat = float((up[sel] * w).sum() / max(w.sum(), 1e-9))
        rad = float(np.median(r[sel]))
        height = float(np.median(floor - c[sel, 1]))
        out.append({"ground": flat >= GROUND_FLAT and height <= GROUND_LOW * rmax,
                    "far": rad >= FAR_SHARE * rmax and (float(np.median(up[sel])) <= 1 - FAR_UPRIGHT
                                                        or height >= FAR_HIGH * rmax),
                    "radius": rad, "height": height, "up": flat})
    return out


def uv_raster(mesh, k, w, h):
    """Which triangle owns each texel of texture k at w x h, and where in it: (tri (h, w) int32, -1 for none,
    bary (h, w, 3)). Triangles are brought into the first tile (their UVs less the whole part of their least)."""
    tri = np.full((h, w), -1, np.int32)
    bary = np.zeros((h, w, 3), np.float32)
    for t in np.nonzero(mesh.tex == k)[0]:
        uv = np.nan_to_num(mesh.uv[t].astype(np.float64))
        uv = uv - np.floor(uv.min(0))
        P = uv * [w, h]
        x0, y0 = np.floor(P.min(0)).astype(int)
        x1, y1 = np.ceil(P.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
        if x0 > x1 or y0 > y1:
            continue
        area = (P[1, 0] - P[0, 0]) * (P[2, 1] - P[0, 1]) - (P[2, 0] - P[0, 0]) * (P[1, 1] - P[0, 1])
        if abs(area) < 1e-9:
            continue
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float64) + 0.5
        w0 = ((P[1, 0] - xs) * (P[2, 1] - ys) - (P[2, 0] - xs) * (P[1, 1] - ys)) / area
        w1 = ((P[2, 0] - xs) * (P[0, 1] - ys) - (P[0, 0] - xs) * (P[2, 1] - ys)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        tri[y0:y1 + 1, x0:x1 + 1][inside] = t
        bary[y0:y1 + 1, x0:x1 + 1][inside] = np.stack([w0, w1, w2], -1)[inside]
    return tri, bary


def _sample(img, x, y, wrap_x=True):
    """Bilinear samples (n, c) of img at pixel coordinates x, y (x wrapped, y clamped)."""
    h, w = img.shape[:2]
    x = x - 0.5
    y = np.clip(y - 0.5, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    gx = (lambda a: np.mod(a, w)) if wrap_x else (lambda a: np.clip(a, 0, w - 1))
    im = img.astype(np.float32)
    g = lambda yy, xx: im[np.clip(yy, 0, h - 1), gx(xx)]
    return (g(y0, x0) * (1 - fx) * (1 - fy) + g(y0, x0 + 1) * fx * (1 - fy) +
            g(y0 + 1, x0) * (1 - fx) * fy + g(y0 + 1, x0 + 1) * fx * fy)


class Panorama:
    """The view all round from `centre` (x, eye y, z) of the far textures, `period` radians of it (the sky repeats
    round the arena: oo's four times, so a quarter turn holds all of it): angle across, elevation up."""

    def __init__(self, mesh, ks, centre, period=2 * math.pi, width=PANORAMA_W):
        self.mesh, self.ks, self.c = mesh, list(ks), np.asarray(centre, float)
        self.period = period
        sel = np.isin(mesh.tex, self.ks)
        # the height it spans: only triangles with texture on them count. oo's dome is closed over the top by big
        # triangles all on one texel (its dark navy): counted, they stretched the panorama to 90 degrees up and
        # squeezed the bands of cloud (0-26 degrees) into its bottom quarter, fewer rows than the texture has
        uv = np.nan_to_num(mesh.uv.astype(np.float64))
        uv_area = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) -
                               (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1]))
        textured = sel & (uv_area > 1e-6)
        p = mesh.pos[textured if textured.any() else sel].reshape(-1, 3).astype(np.float64)
        el = self._elev(p)
        margin = 0.03 * float(el.max() - el.min())
        self.lo, self.hi = float(el.min()) - margin, float(el.max()) + margin
        span = max(self.hi - self.lo, 1e-3)
        self.W = width
        self.H = int(max(width * span / period, width / PANORAMA_MAX_ASPECT)) // 16 * 16
        self.ky = self.H / span  # pixels per radian up (stretched when the band is thin)

    def _angle(self, p):
        return np.arctan2(p[..., 0] - self.c[0], p[..., 2] - self.c[2])

    def _elev(self, p):
        return np.arctan2(self.c[1] - p[..., 1], np.hypot(p[..., 0] - self.c[0], p[..., 2] - self.c[2]))

    def xy(self, p):
        """Panorama pixel (x, y) of world points (..., 3); x in 0..W (wrapped by the period)."""
        a = np.mod(self._angle(p), self.period)
        return np.stack([a / self.period * self.W, (self.hi - self._elev(p)) * self.ky], -1)

    def draw(self, images):
        """The far textures drawn into the panorama (RGB; also returned: where anything was drawn), nearest in front
        (a dome's colour-keyed texels let what's behind show), from images (per mesh texture, RGBA)."""
        H, W = self.H, self.W
        out = np.zeros((H, W, 3), np.float32)
        depth = np.full((H, W), np.inf, np.float32)
        for t in np.nonzero(np.isin(self.mesh.tex, self.ks))[0]:
            k = int(self.mesh.tex[t])
            img = images[k]
            p = self.mesh.pos[t].astype(np.float64)
            P = self.xy(p)
            P[1:, 0] = P[0, 0] + ((P[1:, 0] - P[0, 0] + W / 2) % W - W / 2)  # a triangle across the seam stays whole
            dist = np.hypot(p[:, 0] - self.c[0], p[:, 2] - self.c[2]).mean()
            uv = np.nan_to_num(self.mesh.uv[t].astype(np.float64))
            # each pixel's ray from the centre meets the triangle: its texture from that point (exact; interpolated
            # across the panorama instead, a 45 degree segment of oo's sky came back ~50 px off mid-way, the flat
            # triangle bending against the angles)
            n = np.cross(p[1] - p[0], p[2] - p[0])
            nn = float(n @ n)
            if nn < 1e-12:
                continue
            a0 = float(self._angle(p[0]))
            offset = a0 - np.mod(a0, self.period)  # the triangle's own turn: its angles less the folded ones
            pad = 0.04 * W
            for shift in (0, -W, W):
                Q = P + [shift, 0]
                x0, y0 = np.floor(Q.min(0) - pad).astype(int)
                x1, y1 = np.ceil(Q.max(0) + pad).astype(int)
                x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W - 1), min(y1, H - 1)
                if x0 > x1 or y0 > y1:
                    continue
                ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float64) + 0.5
                th = (xs - shift) / W * self.period + offset
                ph = self.hi - ys / self.ky
                d = np.stack([np.sin(th) * np.cos(ph), -np.sin(ph), np.cos(th) * np.cos(ph)], -1)
                dn = d @ n
                with np.errstate(divide="ignore", invalid="ignore"):
                    tt = ((p[0] - self.c) @ n) / dn
                q = self.c + d * tt[..., None]
                v0, v1, v2 = p[1] - p[0], p[2] - p[0], q - p[0]
                b1 = np.einsum("...c,c->...", np.cross(v2, v1), n) / nn  # barycentrics of q in the triangle
                b2 = np.einsum("...c,c->...", np.cross(v0, v2), n) / nn
                b0 = 1 - b1 - b2
                inside = (tt > 0) & (b0 >= -1e-6) & (b1 >= -1e-6) & (b2 >= -1e-6)
                win = inside & (dist < depth[y0:y1 + 1, x0:x1 + 1])
                if not win.any():
                    continue
                b = np.stack([b0, b1, b2], -1)[win]
                tuv = b @ uv
                hh, ww = img.shape[:2]
                col = _sample(img, np.mod(tuv[:, 0], 1) * ww, np.mod(tuv[:, 1], 1) * hh)
                keep = col[:, 3] >= 128 if img.shape[2] == 4 else np.ones(len(col), bool)
                win[win] = keep
                out[y0:y1 + 1, x0:x1 + 1][win] = col[keep, :3]
                depth[y0:y1 + 1, x0:x1 + 1][win] = dist
        drawn = np.isfinite(depth)
        if not drawn.all():  # gaps (between bands, past the dome's top) from the colours around them
            out = cv2.inpaint(np.clip(out, 0, 255).astype(np.uint8), (~drawn).astype(np.uint8), 5, cv2.INPAINT_TELEA)
        return np.clip(out, 0, 255).astype(np.uint8), drawn

    def back(self, painted, images):
        """Each far texture's texels from where they land in the painted panorama (RGBA per texture in self.ks, the
        alpha kept); texels no triangle owns keep their colour."""
        out = {}
        for k in self.ks:
            img = images[k]
            h, w = img.shape[:2]
            tri, bary = uv_raster(self.mesh, k, w, h)
            own = tri >= 0
            res = img.copy()
            if own.any():
                p = np.einsum("nk,nkc->nc", bary[own].astype(np.float64), self.mesh.pos[tri[own]].astype(np.float64))
                P = self.xy(p)
                res[own, :3] = np.clip(_sample(painted, P[:, 0], P[:, 1]), 0, 255).astype(np.uint8)
            out[k] = res
        return out


def panorama_period(mesh, ks, repeats):
    """The turn one copy of the sky takes round the arena: 2 pi / n where the far textures are each laid n times
    (repeats, near a whole number, the same n for all of them), else the whole turn."""
    ns = {round(repeats[k]) for k in ks}
    if len(ns) == 1:
        n = ns.pop()
        if n >= 1 and all(abs(repeats[k] - n) < 0.15 for k in ks):
            return 2 * math.pi / n
    return 2 * math.pi


def charts(mesh, tol=1e-4):
    """Per triangle a chart number: triangles joined by an edge that's continuous on the texture too (same texture,
    the same UVs at both ends) share one."""
    parent = list(range(len(mesh)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    pos = np.round(mesh.pos / 4.0).astype(np.int64)
    edges = {}
    for t in range(len(mesh)):
        for i in range(3):
            a, b = tuple(pos[t, i]), tuple(pos[t, (i + 1) % 3])
            key = (a, b) if a <= b else (b, a)
            edges.setdefault(key, []).append((t, i))
    for key, uses in edges.items():
        for (t1, i1), (t2, i2) in zip(uses, uses[1:]):
            if mesh.tex[t1] != mesh.tex[t2] or mesh.tex[t1] < 0:
                continue
            u1 = {tuple(np.round(mesh.uv[t1, j], 4)) for j in (i1, (i1 + 1) % 3)}
            u2 = {tuple(np.round(mesh.uv[t2, j], 4)) for j in (i2, (i2 + 1) % 3)}
            if u1 == u2:
                parent[find(t1)] = find(t2)
    return np.array([find(t) for t in range(len(mesh))])


def level_seams(mesh, images, ks=None, band=SEAM_BAND, samples=24):
    """Where two charts meet in the scene (an edge shared in 3D, discontinuous on the texture: two textures, or two
    parts of one), pull the colours each side to meet: half the difference added each side at the seam, fading
    over `band` texels into its own chart. images: per mesh texture RGBA (changed copies are returned); ks: the
    textures to change (default all)."""
    ks = set(range(len(images))) if ks is None else set(ks)
    chart = charts(mesh)
    pos = np.round(mesh.pos / 4.0).astype(np.int64)
    edges = {}
    for t in range(len(mesh)):
        if mesh.tex[t] < 0:
            continue
        for i in range(3):
            a, b = tuple(pos[t, i]), tuple(pos[t, (i + 1) % 3])
            key = (a, b) if a <= b else (b, a)
            edges.setdefault(key, []).append((t, i, a <= b))
    owners = {}
    corr = {k: np.zeros(images[k].shape[:2] + (3,), np.float32) for k in ks}
    wsum = {k: np.zeros(images[k].shape[:2], np.float32) for k in ks}
    s = np.linspace(0.02, 0.98, samples)
    for key, uses in edges.items():
        if len(uses) != 2:
            continue
        (t1, i1, f1), (t2, i2, f2) = uses
        if chart[t1] == chart[t2]:
            continue
        k1, k2 = int(mesh.tex[t1]), int(mesh.tex[t2])
        if k1 not in ks and k2 not in ks:
            continue
        sides = []
        for t, i, fwd, k in ((t1, i1, f1, k1), (t2, i2, f2, k2)):
            a, b = mesh.uv[t, i].astype(np.float64), mesh.uv[t, (i + 1) % 3].astype(np.float64)
            if not fwd:  # the same direction along the shared edge on both sides
                a, b = b, a
            base = np.floor(np.minimum(np.nan_to_num(mesh.uv[t]).min(0), 1e9))
            a, b = np.nan_to_num(a) - base, np.nan_to_num(b) - base
            h, w = images[k].shape[:2]
            line = (a[None] + (b - a)[None] * s[:, None]) * [w, h]
            col = _sample(images[k], line[:, 0], line[:, 1], wrap_x=False)[:, :3]
            sides.append((t, k, a * [w, h], b * [w, h], col))
        diff = (sides[1][4] - sides[0][4]) / 2  # side 0 goes up by this, side 1 down: they meet at the average
        for sign, (t, k, A, B, _) in ((1.0, sides[0]), (-1.0, sides[1])):
            if k not in ks:
                continue
            if k not in owners:
                h, w = images[k].shape[:2]
                owners[k] = uv_raster(mesh, k, w, h)[0]
            own = owners[k]
            h, w = own.shape
            x0, y0 = np.floor(np.minimum(A, B) - band).astype(int)
            x1, y1 = np.ceil(np.maximum(A, B) + band).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
            if x0 > x1 or y0 > y1:
                continue
            ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float64) + 0.5
            d = B - A
            L2 = max(float(d @ d), 1e-9)
            tt = np.clip(((xs - A[0]) * d[0] + (ys - A[1]) * d[1]) / L2, 0, 1)
            dist = np.hypot(xs - (A[0] + tt * d[0]), ys - (A[1] + tt * d[1]))
            mine = own[y0:y1 + 1, x0:x1 + 1]
            same = (mine >= 0) & (chart[np.maximum(mine, 0)] == chart[t])
            wgt = np.clip(1 - dist / band, 0, 1) * same
            if not wgt.any():
                continue
            idx = np.clip(np.round(tt * (samples - 1)).astype(int), 0, samples - 1)
            corr[k][y0:y1 + 1, x0:x1 + 1] += sign * diff[idx] * wgt[..., None]
            wsum[k][y0:y1 + 1, x0:x1 + 1] += wgt
    out = list(images)
    for k in ks:
        if not wsum[k].any():
            continue
        c = corr[k] / np.maximum(wsum[k], 1.0)[..., None]
        img = images[k].astype(np.float32)
        img[..., :3] += c
        out[k] = np.dstack([np.clip(img[..., :3], 0, 255).astype(np.uint8), images[k][..., 3]])
    return out
