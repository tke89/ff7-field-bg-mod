"""A texture space for a model's triangles: every triangle gets UVs in one new atlas texture per model, packed with
padding, the atlas filled with what the triangles show now (docs/models-plan.md, "field models").

Why: field models get most of their colour from vertex colours, with nowhere to paint a texture, and FFNx's external
meshes (what ships the painted look: gltf.py) colour a whole primitive from one material, ignoring vertex colours.

How:
- Charts: per part, triangles that share an edge grow into one chart while they face within MAX_ANGLE of its first
  (largest) triangle and their projection onto its plane doesn't overlap the chart so far. A chart is projected
  flat onto that plane (texel density shrinks by at most cos(MAX_ANGLE) across tilted triangles), turned to its
  smallest bounding rectangle and laid landscape. Parts are rigid, so a posed mesh charts as its parts' own space.
- Packing: every chart is a texel rectangle with `pad` texels of margin on each side, skyline-packed (each as low as
  it goes, tallest first) into the atlas. field.export_part repacks one part's chart rectangles into a texture of its
  own, texel for texel (FFNx loads a texture per part file and shares nothing between them).
- Size: the smallest power of two from 256 to max_size that holds the model at DENSITY texels per model unit, then
  the density raised until it's full (Cloud, 1426 units of surface: see the check script's report). A model too big
  for max_size gets the density that fits.
- Filling: texels are rasterized conservatively (any texel a triangle touches; texels a triangle covers the centre
  of win), with the triangle's vertex colours (Gouraud, as render.shade shows them), or for a textured triangle its
  texture sampled bilinearly (FFNx filters model textures) times its vertex colours (as the game draws it; these are
  near white on most textured triangles). Every other texel copies the nearest triangle's texel, so bilinear
  filtering, and a painter's brush, never pull in an unrelated colour.
- Textured triangles go in the atlas too (textured=True, the default): an external mesh replaces every part of its
  model (FFNx skips a model's other parts once one has a glTF), so the shipped model is all atlas anyway; one texture
  at one density is one canvas for the painter; and their vertex colour shading bakes in per triangle, which a
  shared texture can't hold. textured=False keeps them on their own textures (for FFNx texture overrides).
- The atlas mesh's atlas triangles get white vertex colours: the atlas holds their whole colour, as FFNx draws an
  external mesh (texture x the material's colour), so render.shade shows it the same with or without modulate.
- Degenerate triangles (no area) all point at one texel."""
from dataclasses import dataclass, field as dc_field, replace

import cv2
import numpy as np

from . import mesh as meshmod
from . import tex as texmod

MAX_ANGLE = 55.0   # degrees a triangle may face away from its chart's plane
DENSITY = 16.0     # texels per model unit the size is chosen for (a field model is about 34 units tall)
SIZES = (256, 512, 1024, 2048)
PAD = 3            # texels of margin around every chart (6 between neighbours)
EPS = 1e-6


@dataclass
class Chart:
    part: int             # the mesh part
    tris: np.ndarray      # its triangle indices
    rect: tuple           # (x, y, w, h) its texels in the atlas, margin included


@dataclass
class Atlas:
    mesh: object          # the mesh with atlas UVs (a copy; FieldMesh stays FieldMesh): textures += [the atlas]
    image: np.ndarray     # (S, S, 4) uint8 the atlas as it starts: what the triangles showed
    index: int            # the atlas' index in mesh.textures
    tri: np.ndarray       # (S, S) int32 the triangle each texel belongs to, -1 for margin and free space
    charts: list          # [Chart]
    density: float        # texels per model unit
    pad: int = PAD
    textured: bool = True  # textured triangles went into the atlas
    selected: np.ndarray = dc_field(default=None)  # (t,) bool the triangles in the atlas

    @property
    def size(self):
        return self.image.shape[0]

    def used(self):
        """(S, S) bool: the texels triangles cover (the rest is margin and free space, filled by fill())."""
        return self.tri >= 0

    def part_charts(self, part):
        """The charts of a mesh part (index)."""
        return [c for c in self.charts if c.part == part]


def as_tex(image):
    """A tex.Tex holding an RGBA image (truecolour, one 'palette'): what mesh.TexRef wants."""
    h, w = image.shape[:2]
    return texmod.Tex(w, h, pixels=np.ascontiguousarray(image, np.uint8))


def fill(image, used):
    """The image with every texel outside `used` copied from the nearest used texel (the margin around charts and
    the free space): after painting, run this again so filtering and mip levels see the painted colours."""
    if used.all() or not used.any():
        return image.copy()
    _, labels = cv2.distanceTransformWithLabels((~used).astype(np.uint8), cv2.DIST_L2, 5,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(used)
    lab = np.zeros(labels.max() + 1, np.int64)
    # every used texel has a label of its own; every texel carries its nearest used texel's label
    lab[labels[ys, xs]] = ys * used.shape[1] + xs
    src = lab[labels]
    flat = image.reshape(-1, image.shape[-1])
    return flat[src.reshape(-1)].reshape(image.shape)


# --- charts ---------------------------------------------------------------------------------------------------------

def _normals_areas(pos):
    p = pos.astype(np.float64)
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], a / 2


def _basis(n):
    """Two unit vectors spanning the plane with normal n."""
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _overlaps(a, bs):
    """Does the 2D triangle a (3, 2) overlap any of bs (k, 3, 2) by more than touching? (separating axes)"""
    if not len(bs):
        return False
    lo, hi = a.min(0), a.max(0)
    near = (bs.max(1) > lo + EPS).all(1) & (bs.min(1) < hi - EPS).all(1)
    bs = bs[near]
    if not len(bs):
        return False
    tris = np.concatenate([np.broadcast_to(a, (len(bs), 3, 2)), bs], 1).reshape(len(bs), 2, 3, 2)
    e = np.roll(tris, -1, 2) - tris
    axes = np.stack([-e[..., 1], e[..., 0]], -1).reshape(len(bs), 6, 2)
    axes /= np.maximum(np.linalg.norm(axes, axis=-1, keepdims=True), 1e-30)
    pa = np.einsum("kad,vd->kav", axes, a)
    pb = np.einsum("kad,kvd->kav", axes, bs)
    scale = max(1e-9, float(np.abs(a).max()))
    sep = (pa.max(-1) <= pb.min(-1) + 1e-6 * scale) | (pb.max(-1) <= pa.min(-1) + 1e-6 * scale)
    return bool((~sep.any(1)).any())


def charts(pos, tris, max_angle=MAX_ANGLE):
    """Charts of the triangles `tris` (indices into pos (t, 3, 3)): [(triangle indices, 2D corners (k, 3, 2) in
    model units)]. Degenerate triangles are left out (see build)."""
    normals, areas = _normals_areas(pos)
    tris = [int(t) for t in tris if areas[t] > EPS]
    if not tris:
        return []
    # shared edges, by welded corner positions
    scale = max(1e-6, float(np.abs(pos[tris]).max()))
    q = np.round(pos[tris].reshape(-1, 3) / (scale * 1e-5)).astype(np.int64)
    _, vid = np.unique(q, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    edges = {}
    for k, t in enumerate(tris):
        for i in range(3):
            a, b = vid[k, i], vid[k, (i + 1) % 3]
            if a != b:
                edges.setdefault((min(a, b), max(a, b)), []).append(t)
    near = {t: set() for t in tris}
    for ts in edges.values():
        for t in ts:
            near[t].update(ts)
    cos = np.cos(np.radians(max_angle))
    left = set(tris)
    out = []
    for seed in sorted(tris, key=lambda t: -areas[t]):
        if seed not in left:
            continue
        n0 = normals[seed]
        u, v = _basis(n0)
        proj = lambda t: np.stack([pos[t] @ u, pos[t] @ v], -1).astype(np.float64)
        members, flat = [seed], [proj(seed)]
        left.discard(seed)
        queue = [seed]
        while queue:
            t = queue.pop(0)
            for nb in near[t]:
                if nb not in left or normals[nb] @ n0 < cos:
                    continue
                p = proj(nb)
                if _overlaps(p, np.array(flat)):
                    continue
                members.append(nb)
                flat.append(p)
                left.discard(nb)
                queue.append(nb)
        out.append((np.array(members), np.array(flat)))
    return out


def _upright(flat):
    """A chart's 2D corners turned to their smallest bounding rectangle, landscape, from (0, 0): (corners, (w, h))."""
    pts = flat.reshape(-1, 2)
    (cx, cy), (w, h), ang = cv2.minAreaRect(pts.astype(np.float32))
    a = np.radians(ang)
    r = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
    p = pts @ r.T
    p -= p.min(0)
    ext = p.max(0)
    if ext[1] > ext[0]:
        p = np.stack([p[:, 1], ext[0] - p[:, 0]], -1)
        ext = ext[::-1]
    return p.reshape(flat.shape), ext


# --- packing --------------------------------------------------------------------------------------------------------

def skyline(sizes, width):
    """Pack rectangles (w, h) into a strip `width` wide, each as low as it goes (skyline, bottom-left), tallest
    first: (positions [(x, y)], used height). None for a rectangle wider than the strip."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    segs = [[0, 0, width]]  # the skyline: [x, y, w] left to right
    pos = [None] * len(sizes)
    top = 0
    for i in order:
        w, h = sizes[i]
        if w > width:
            continue
        best = None
        for k in range(len(segs)):
            x = segs[k][0]
            if x + w > width:
                break
            # the height at x across w: the highest segment under it
            y, j, room = 0, k, 0
            while room < w:
                y = max(y, segs[j][1])
                room += segs[j][2]
                j += 1
            if best is None or y + h < best[0] or (y + h == best[0] and x < best[1]):
                best = (y + h, x, y)
        _, x, y = best
        pos[i] = (x, y)
        top = max(top, y + h)
        # raise the skyline over [x, x + w)
        out = []
        for sx, sy, sw in segs:
            if sx + sw <= x or sx >= x + w:
                out.append([sx, sy, sw])
                continue
            if sx < x:
                out.append([sx, sy, x - sx])
            if sx + sw > x + w:
                out.append([x + w, sy, sx + sw - x - w])
        out.append([x, y + h, w])
        out.sort()
        merged = [out[0]]
        for sg in out[1:]:
            if sg[1] == merged[-1][1]:
                merged[-1][2] += sg[2]
            else:
                merged.append(sg)
        segs = merged
    return pos, top


def chart_sizes(exts, density, pad):
    """Texel rectangles (w, h) of charts with extents (model units) at a density, margins included."""
    return [(int(np.ceil(e[0] * density)) + 1 + 2 * pad, int(np.ceil(e[1] * density)) + 1 + 2 * pad) for e in exts]


def _fits(exts, density, size, pad):
    sizes = chart_sizes(exts, density, pad)
    pos, top = skyline(sizes, size)
    return top <= size and all(p is not None for p in pos), sizes, pos


def _best_density(exts, size, pad):
    """The largest density at which the charts fit size x size (binary search), or 0."""
    total = sum(e[0] * e[1] for e in exts)
    hi = np.sqrt(size * size / max(total, 1e-9))
    if _fits(exts, hi, size, pad)[0]:
        return hi
    lo = 0.0
    for _ in range(16):
        mid = (lo + hi) / 2
        if _fits(exts, mid, size, pad)[0]:
            lo = mid
        else:
            hi = mid
    return lo


# --- filling --------------------------------------------------------------------------------------------------------

def _sample(img, uv):
    """Bilinear RGB and nearest alpha (n, 4) float of an RGBA image at uv (n, 2) (wrapped)."""
    h, w = img.shape[:2]
    x = np.mod(uv[:, 0], 1.0) * w - 0.5
    y = np.mod(uv[:, 1], 1.0) * h - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    im = img.astype(np.float32)
    g = lambda yy, xx: im[np.mod(yy, h), np.mod(xx, w)]
    rgb = (g(y0, x0) * (1 - fx) * (1 - fy) + g(y0, x0 + 1) * fx * (1 - fy) + g(y0 + 1, x0) * (1 - fx) * fy +
           g(y0 + 1, x0 + 1) * fx * fy)
    xi = (np.mod(uv[:, 0], 1.0) * w).astype(int).clip(0, w - 1)
    yi = (np.mod(uv[:, 1], 1.0) * h).astype(int).clip(0, h - 1)
    rgb[:, 3] = im[yi, xi, 3]
    return rgb


def _raster(mesh, uv_px, sel, size, modulate, images):
    """Atlas colours and triangle map: conservative coverage first, texels whose centre a triangle covers win."""
    img = np.zeros((size, size, 4), np.float32)
    tri = np.full((size, size), -1, np.int32)
    strict = np.zeros((size, size), bool)
    for t in np.nonzero(sel)[0]:
        P = uv_px[t].astype(np.float64)
        area = (P[1, 0] - P[0, 0]) * (P[2, 1] - P[0, 1]) - (P[2, 0] - P[0, 0]) * (P[1, 1] - P[0, 1])
        if abs(area) < 1e-12:
            continue
        x0, y0 = np.floor(P.min(0) - 1).astype(int)
        x1, y1 = np.ceil(P.max(0) + 1).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size - 1), min(y1, size - 1)
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float64) + 0.5
        w0 = ((P[1, 0] - xs) * (P[2, 1] - ys) - (P[2, 0] - xs) * (P[1, 1] - ys)) / area
        w1 = ((P[2, 0] - xs) * (P[0, 1] - ys) - (P[0, 0] - xs) * (P[2, 1] - ys)) / area
        w2 = 1 - w0 - w1
        W = np.stack([w0, w1, w2], -1)
        # distance (texels) of each texel centre outside each edge: w_i times the height onto edge i
        e = np.array([np.linalg.norm(P[(i + 2) % 3] - P[(i + 1) % 3]) for i in range(3)])
        dist = W * (abs(area) / np.maximum(e, 1e-12))
        inside = (W >= 0).all(-1)
        touch = (dist >= -0.71).all(-1)  # the texel square reaches the triangle (roughly)
        reg_strict = strict[y0:y1 + 1, x0:x1 + 1]
        take = (inside | (touch & (tri[y0:y1 + 1, x0:x1 + 1] < 0))) & ~reg_strict
        if not take.any():
            continue
        b = np.clip(W[take], 0, None)
        b /= np.maximum(b.sum(1, keepdims=True), 1e-12)
        vc = b @ mesh.col[t].astype(np.float32)
        ti = mesh.tex[t]
        if ti >= 0:
            uv = b @ np.nan_to_num(mesh.uv[t]).astype(np.float64)
            c = _sample(images[ti], uv)
            if modulate:
                c[:, :3] *= vc[:, :3] / 255.0
        else:
            c = np.concatenate([vc[:, :3], np.full((len(vc), 1), 255.0)], 1)
        sub = img[y0:y1 + 1, x0:x1 + 1]
        sub[take] = c
        tri[y0:y1 + 1, x0:x1 + 1][take] = t
        reg_strict |= take & inside
    return np.clip(np.round(img), 0, 255).astype(np.uint8), tri


# --- the atlas ------------------------------------------------------------------------------------------------------

def build(mesh, size=None, max_size=2048, density=DENSITY, pad=PAD, textured=True, modulate=True,
          max_angle=MAX_ANGLE, name="model"):
    """Atlas of a mesh.Mesh (any: a posed field model, a battle model). size: the atlas' side (filled at the density
    that fits), or None to choose it (see the module docstring). textured: textured triangles go in too. modulate:
    textured triangles' vertex colours are baked into their texels (the game multiplies them in)."""
    T = len(mesh)
    sel = (np.ones(T, bool) if textured else mesh.tex < 0)
    _, areas = _normals_areas(mesh.pos)
    degenerate = sel & (areas <= EPS)
    found = []  # (part, triangles, upright corners, extent)
    for p in np.unique(mesh.part[sel]):
        ts = np.nonzero(sel & (mesh.part == p) & ~degenerate)[0]
        for members, flat in charts(mesh.pos, ts, max_angle):
            corners, ext = _upright(flat)
            found.append((int(p), members, corners, ext))
    if degenerate.any():  # one spot for all of them, with the part of the first
        found.append((int(mesh.part[np.nonzero(degenerate)[0][0]]), np.nonzero(degenerate)[0],
                      np.zeros((int(degenerate.sum()), 3, 2)), np.zeros(2)))
    if not found:
        raise ValueError("nothing to put in an atlas")
    exts = [f[3] for f in found]
    if size is None:
        size = next((s for s in SIZES if s <= max_size and _fits(exts, density, s, pad)[0]), max_size)
    d = _best_density(exts, size, pad)
    if d <= 0:
        raise ValueError(f"the model doesn't fit a {size} atlas")
    _, sizes, pos = _fits(exts, d, size, pad)
    uv_px = np.full((T, 3, 2), np.nan)
    out_charts = []
    for (p, members, corners, ext), (w, h), (x, y) in zip(found, sizes, pos):
        uv_px[members] = corners * d + (x + pad + 0.5, y + pad + 0.5)
        out_charts.append(Chart(p, members, (int(x), int(y), int(w), int(h))))
    images = [t.tex.rgba(t.palette) for t in mesh.textures]
    img, tri = _raster(mesh, uv_px, sel & ~degenerate, size, modulate, images)
    if degenerate.any():  # their spot: the first one's colour
        t = int(np.nonzero(degenerate)[0][0])
        x, y = np.floor(uv_px[t, 0]).astype(int)
        img[y, x] = list(mesh.col[t].mean(0)[:3].astype(np.uint8)) + [255]
        tri[y, x] = t
    img = fill(img, tri >= 0)
    # the mesh with atlas UVs, white where the atlas holds the colour
    ref = meshmod.TexRef(f"atlas/{name}", as_tex(img), 0)
    index = len(mesh.textures)
    uv = mesh.uv.copy()
    uv[sel] = (uv_px[sel] / size).astype(np.float32)
    texi = mesh.tex.copy()
    texi[sel] = index
    col = mesh.col.copy()
    col[sel] = 255
    out = replace(mesh, uv=uv, tex=texi, col=col, textures=list(mesh.textures) + [ref])
    return Atlas(out, img, index, tri, out_charts, float(d), pad, textured, sel)


def with_image(a, image):
    """The atlas' mesh showing another image (a painted atlas, any size): for renders of the painted model."""
    ref = meshmod.TexRef(a.mesh.textures[a.index].key, as_tex(image), 0)
    textures = list(a.mesh.textures)
    textures[a.index] = ref
    return replace(a.mesh, textures=textures)
