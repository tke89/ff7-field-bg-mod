"""A small software renderer for models and battle scenes (numpy only): which triangle every pixel sees and where on
it (perspective-correct barycentrics), from which the colour, the texel each pixel shows (for painting back into the
textures), depth and facing follow.

Camera space: x right, y down, z forward (the image's own axes). Triangles crossing the near plane are clipped there
(a battle scene's floor runs behind the camera). Colour-keyed texels are cut out while rasterizing, so what's behind
a cut-out (leaves, railings) shows through."""
from dataclasses import dataclass

import numpy as np


@dataclass
class Camera:
    eye: np.ndarray
    target: np.ndarray
    up: np.ndarray        # world up (FF7's battle and field data have y down: pass (0, -1, 0))
    fov_y: float = 45.0   # degrees, or focal set directly
    width: int = 1024
    height: int = 768
    near: float = 1.0
    focal: float = None   # pixels; overrides fov_y

    def rotation(self):
        f = np.asarray(self.target, float) - np.asarray(self.eye, float)
        f /= np.linalg.norm(f)
        r = np.cross(f, np.asarray(self.up, float))
        if np.linalg.norm(r) < 1e-9:  # looking along up
            r = np.cross(f, [0.0, 0.0, 1.0] if abs(f[2]) < 0.9 else [1.0, 0.0, 0.0])
        r /= np.linalg.norm(r)
        d = np.cross(f, r)
        return np.stack([r, d, f])

    def f_px(self):
        return self.focal if self.focal else (self.height / 2) / np.tan(np.radians(self.fov_y) / 2)

    def to_camera(self, p):
        return (np.asarray(p, float) - np.asarray(self.eye, float)) @ self.rotation().T

    def project(self, c):
        """Camera-space points (..., 3) -> pixel x, y (..., 2)."""
        f = self.f_px()
        z = np.maximum(c[..., 2], 1e-6)
        return np.stack([self.width / 2 + f * c[..., 0] / z, self.height / 2 + f * c[..., 1] / z], -1)


@dataclass
class Raster:
    tri: np.ndarray    # (h, w) int32 triangle index, -1 for nothing
    bary: np.ndarray   # (h, w, 3) float32 perspective-correct weights of the triangle's vertices
    depth: np.ndarray  # (h, w) float32 camera depth (inf for nothing)


def _clip_near(c, near):
    """Clip a camera-space triangle (3, 3) to z >= near: [(sub-triangle (3, 3), weights (3, 3) of the original
    vertices)], empty when it's all behind."""
    poly = [(c[i], np.eye(3)[i]) for i in range(3)]
    out = []
    for i in range(len(poly)):
        (p, wp), (q, wq) = poly[i], poly[(i + 1) % len(poly)]
        pin, qin = p[2] >= near, q[2] >= near
        if pin:
            out.append((p, wp))
        if pin != qin:
            s = (near - p[2]) / (q[2] - p[2])
            out.append((p + s * (q - p), wp + s * (wq - wp)))
    if len(out) < 3:
        return []
    return [(np.stack([out[0][0], out[k][0], out[k + 1][0]]), np.stack([out[0][1], out[k][1], out[k + 1][1]]))
            for k in range(1, len(out) - 1)]


def rasterize(mesh, cam, alpha_test=True, blended=False, cull=None):
    """Raster of the mesh's triangles (opaque ones; blended too with blended=True). cull: drop back faces (wound
    counter-clockwise on screen); None (default): the mesh's own `cull` attribute if it has one (battle.load_scene's
    scenes: the game draws them one-sided), else off."""
    if cull is None:
        cull = bool(getattr(mesh, "cull", False))
    H, W = cam.height, cam.width
    tri = np.full((H, W), -1, np.int32)
    bary = np.zeros((H, W, 3), np.float32)
    zbuf = np.full((H, W), np.inf, np.float32)
    C = cam.to_camera(mesh.pos.reshape(-1, 3)).reshape(-1, 3, 3)
    alphas = [t.tex.rgba(t.palette)[..., 3] if alpha_test else None for t in mesh.textures]
    has_cut = [a is not None and (a < 128).any() for a in alphas]
    for t in range(len(mesh)):
        if not blended and mesh.blend[t] >= 0:
            continue
        c = C[t]
        if (c[:, 2] < cam.near).all():
            continue
        subs = [(c, np.eye(3))] if (c[:, 2] >= cam.near).all() else _clip_near(c, cam.near)
        for sc, sw in subs:
            P = cam.project(sc)
            x0, y0 = np.floor(P.min(0)).astype(int)
            x1, y1 = np.ceil(P.max(0)).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W - 1), min(y1, H - 1)
            if x0 > x1 or y0 > y1:
                continue
            area = (P[1, 0] - P[0, 0]) * (P[2, 1] - P[0, 1]) - (P[2, 0] - P[0, 0]) * (P[1, 1] - P[0, 1])
            if abs(area) < 1e-12 or (cull and area < 0):
                continue
            ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float64) + 0.5
            w0 = ((P[1, 0] - xs) * (P[2, 1] - ys) - (P[2, 0] - xs) * (P[1, 1] - ys)) / area
            w1 = ((P[2, 0] - xs) * (P[0, 1] - ys) - (P[0, 0] - xs) * (P[2, 1] - ys)) / area
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
            if not inside.any():
                continue
            iz = w0 / sc[0, 2] + w1 / sc[1, 2] + w2 / sc[2, 2]
            z = 1 / iz
            b = np.stack([w0 / sc[0, 2], w1 / sc[1, 2], w2 / sc[2, 2]], -1) * z[..., None]
            b = b @ sw  # weights of the original vertices
            win = inside & (z < zbuf[y0:y1 + 1, x0:x1 + 1])
            ti = mesh.tex[t]
            if win.any() and ti >= 0 and has_cut[ti]:
                a = alphas[ti]
                uv = b[win] @ mesh.uv[t]
                hh, ww = a.shape
                px = (np.mod(uv[:, 0], 1.0) * ww).astype(int).clip(0, ww - 1)
                py = (np.mod(uv[:, 1], 1.0) * hh).astype(int).clip(0, hh - 1)
                keep = a[py, px] >= 128
                win[win] = keep
            if not win.any():
                continue
            zb = zbuf[y0:y1 + 1, x0:x1 + 1]
            zb[win] = z[win]
            tri[y0:y1 + 1, x0:x1 + 1][win] = t
            bary[y0:y1 + 1, x0:x1 + 1][win] = b[win]
    return Raster(tri, bary, zbuf)


def texels(mesh, r):
    """Per pixel: (texture index (h, w) int32, -1 where none, and uv (h, w, 2) float32 in 0..1 (wrapped))."""
    hit = r.tri >= 0
    ti = np.full(r.tri.shape, -1, np.int32)
    uv = np.zeros(r.tri.shape + (2,), np.float32)
    t = r.tri[hit]
    ti[hit] = mesh.tex[t]
    uv[hit] = np.mod(np.einsum("nk,nkc->nc", r.bary[hit], np.nan_to_num(mesh.uv[t])), 1.0)
    ti[hit & (ti < 0)] = -1
    return ti, uv


def shade(mesh, r, images=None, modulate=False, background=(0, 0, 0)):
    """RGB (h, w, 3) uint8: textured pixels from their texture (images: per mesh texture an RGBA array of any size to
    use instead of the original, e.g. a painted one), untextured ones from their vertex colours. modulate: textured
    pixels times their vertex colour too (as the game shades them); off gives the textures' own colours (what gets
    painted)."""
    H, W = r.tri.shape
    out = np.zeros((H, W, 3), np.float32)
    out[:] = background
    hit = r.tri >= 0
    t = r.tri[hit]
    vc = np.einsum("nk,nkc->nc", r.bary[hit], mesh.col[t].astype(np.float32))[:, :3]
    col = vc.copy()
    ti, uv = texels(mesh, r)
    tih = ti[hit]
    for k in np.unique(tih[tih >= 0]):
        img = images[k] if images is not None and images[k] is not None else mesh.textures[k].tex.rgba(mesh.textures[k].palette)
        sel = tih == k
        hh, ww = img.shape[:2]
        u = uv[hit][sel]
        px = (u[:, 0] * ww).astype(int).clip(0, ww - 1)
        py = (u[:, 1] * hh).astype(int).clip(0, hh - 1)
        c = img[py, px, :3].astype(np.float32)
        col[sel] = c * (vc[sel] / 255.0) if modulate else c
    out[hit] = col
    return np.clip(out, 0, 255).astype(np.uint8)


def normals(mesh):
    """Face normals (t, 3) of the world triangles (unit length; the winding's sign)."""
    p = mesh.pos.astype(np.float64)
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    return (n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)).astype(np.float32)


def facing(mesh, r, cam):
    """Per pixel |cos| between the surface normal and the view ray (0 where nothing): how squarely a view sees it."""
    n = normals(mesh)
    H, W = r.tri.shape
    out = np.zeros((H, W), np.float32)
    hit = r.tri >= 0
    ys, xs = np.nonzero(hit)
    f = cam.f_px()
    ray_c = np.stack([(xs + 0.5 - W / 2) / f, (ys + 0.5 - H / 2) / f, np.ones(len(xs))], -1)
    ray = ray_c @ cam.rotation()  # camera -> world
    ray /= np.linalg.norm(ray, axis=1, keepdims=True)
    out[hit] = np.abs((n[r.tri[hit]] * ray).sum(1))
    return out
