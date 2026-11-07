"""Shrink a field's grid mesh for the game (a 32-bit program, where every megabyte of address space counts).

The camera never moves, so a coarser mesh only has to get two things right where the full grid does: each screen
point's depth (what's in front of what, shadows) and which texel it shows. A quadtree takes, everywhere, the biggest
cell whose fan of flat triangles keeps both within tolerance for every grid point inside it, as the GPU interpolates
them (perspective-correct); cells across depth breaks stay at full detail. A cell's fan takes in every vertex its
smaller neighbours put on its edges, so cells of any sizes meet without cracks.

The shading detail the dropped vertices carried goes into a normal map, encoded for FFNx's cotangent frame on the
coarse triangles (misc/FFNx.lighting.sh): T ~ cross(dP/dv, N), B ~ cross(N, dP/du), both over the longer one's length;
n = normalize(T x + B y + N z) with z rebuilt as sqrt(1 - x^2 + y^2) (sic), x, y = byte / 127 - 128 / 127; sign +1
(checked in game)."""
from functools import lru_cache

import numpy as np

# a cell's corners, clockwise on screen from top left, as (row, column) in cell sizes; side k runs corner k -> k + 1
CORNERS = [(0, 0), (0, 1), (1, 1), (1, 0)]


def _bary(p, a, b, c):
    m = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]], float)
    u, v = np.linalg.solve(m, np.array([p[0] - a[0], p[1] - a[1]], float))
    return 1 - u - v, u, v


@lru_cache(maxsize=None)
def _fan(s):
    """Every grid point inside a cell of s grid steps (offsets a, b = row, column), but the fan's own vertices: the
    fan triangle (centre and two corners) it falls in, and its screen barycentric weights there."""
    h = s // 2
    out = []
    for a in range(s + 1):
        for b in range(s + 1):
            if (a, b) == (h, h) or (a in (0, s) and b in (0, s)):
                continue
            ry, rx = a - h, b - h
            side = (0 if ry < 0 else 2) if abs(ry) >= abs(rx) else (1 if rx > 0 else 3)
            c1, c2 = CORNERS[side], CORNERS[(side + 1) % 4]
            v1, v2 = (c1[0] * s, c1[1] * s), (c2[0] * s, c2[1] * s)
            out.append(((a, b), ((h, h), v1, v2), _bary((a, b), (h, h), v1, v2)))
    return out


def _fan_ok(Z, attrs, s, ny, nx, depth_tol, uv_tol, attr_tol):
    """Whether each of the ny x nx cells of s grid steps, drawn as a fan of 4 triangles, keeps every grid point
    inside within tolerance: depth (relative), texture position (grid steps) and other interpolated values."""
    def at(arr, a, b):
        return arr[a:a + ny * s:s, b:b + nx * s:s]
    ok = np.ones((ny, nx), bool)
    for (a, b), verts, lam in _fan(s):
        zp = at(Z, a, b)
        w = [l / at(Z, *v) for l, v in zip(lam, verts)]  # perspective-correct weights, over 1/z
        z = 1 / (w[0] + w[1] + w[2])
        ok &= np.abs(z - zp) <= depth_tol * zp
        dy = (w[0] * (verts[0][0] - a) + w[1] * (verts[1][0] - a) + w[2] * (verts[2][0] - a)) * z
        dx = (w[0] * (verts[0][1] - b) + w[1] * (verts[1][1] - b) + w[2] * (verts[2][1] - b)) * z
        ok &= dy * dy + dx * dx <= uv_tol * uv_tol
        if attrs is not None:
            got = sum(wi[..., None] * at(attrs, *v) for wi, v in zip(w, verts)) * z[..., None]
            ok &= (np.abs(got - at(attrs, a, b)) <= attr_tol).all(-1)
    return ok


def cell_levels(Z, broken, attrs=None, depth_tol=0.005, uv_tol=0.5, attr_tol=0.05, max_level=5):
    """Quadtree level of the leaf over every unit cell (its size is 2**level grid steps).

    Z: (GH, GW) camera depth of the grid points; broken: (GH-1, GW-1) unit cells across a depth break;
    attrs: (GH, GW, k) other values the GPU interpolates (sway weights); uv_tol in grid steps."""
    GH, GW = Z.shape
    CH, CW = GH - 1, GW - 1
    level = np.zeros((CH, CW), np.int16)
    for l in range(1, max_level + 1):
        s = 1 << l
        ny, nx = CH // s, CW // s
        if not ny or not nx:
            break
        ok = ~broken[:ny * s, :nx * s].reshape(ny, s, nx, s).any((1, 3))
        ok &= _fan_ok(Z, attrs, s, ny, nx, depth_tol, uv_tol, attr_tol)
        up = np.zeros((CH, CW), bool)
        up[:ny * s, :nx * s] = np.repeat(np.repeat(ok, s, 0), s, 1)
        level[up] = l  # the biggest cell that fits wins (a cell that fits makes its children leaves of no one)
    return level


def _leaves(level, l):
    """Top-left unit cells (rows, columns) of the leaves of level l."""
    s = 1 << l
    CH, CW = level.shape
    return np.nonzero((level == l) & (np.arange(CH)[:, None] % s == 0) & (np.arange(CW)[None, :] % s == 0))


def triangulate(level, GW):
    """Triangles (grid point indices) of the leaves of 2 grid steps and up: fans from the centre through every vertex
    on the edges (the corners of the leaves around). Same winding as the full grid's triangles."""
    CH, CW = level.shape
    corner = np.zeros((CH + 1, CW + 1), bool)
    for l in range(int(level.max()) + 1):
        s = 1 << l
        oy, ox = _leaves(level, l)
        for cy, cx in CORNERS:
            corner[oy + cy * s, ox + cx * s] = True
    out = [np.zeros((0, 3), np.int64)]
    for l in range(1, int(level.max()) + 1):
        s, h = 1 << l, 1 << (l - 1)
        oy, ox = _leaves(level, l)
        centre = (oy + h) * GW + ox + h
        for side in range(4):
            (ay, ax), (by, bx) = CORNERS[side], CORNERS[(side + 1) % 4]
            # the edge's points from corner a to corner b; one triangle per stretch between vertices
            py = oy[:, None] + ay * s + (by - ay) * np.arange(s + 1)[None, :]
            px = ox[:, None] + ax * s + (bx - ax) * np.arange(s + 1)[None, :]
            present = corner[py, px]
            nxt = np.empty(present.shape, int)
            nxt[:, s] = s
            for k in range(s - 1, -1, -1):
                nxt[:, k] = np.where(present[:, k + 1], k + 1, nxt[:, k + 1])
            i, k = np.nonzero(present[:, :s])
            a = py[i, k] * GW + px[i, k]
            b = py[i, nxt[i, k]] * GW + px[i, nxt[i, k]]
            out.append(np.stack([centre[i], b, a], -1))
    return np.concatenate(out)


def texel_triangles(level, cut0, cut1, gy, gx):
    """For points of the texture in grid coordinates (gy, gx): the corners (grid row, column) of the mesh triangle
    that shows them, their barycentric weights there, and whether it's a crack (filled by a copy, flat normal)."""
    CH, CW = level.shape
    cy = np.clip(np.floor(gy).astype(int), 0, CH - 1)
    cx = np.clip(np.floor(gx).astype(int), 0, CW - 1)
    l = level[cy, cx].astype(int)
    s = 1 << l
    oy, ox = (cy >> l) << l, (cx >> l) << l
    h = s / 2
    ry, rx = gy - oy - h, gx - ox - h
    horiz = np.abs(ry) >= np.abs(rx)  # top or bottom side
    # where the ray from the centre through the point meets the side, and the neighbour cell just across it
    t = h / np.maximum(np.where(horiz, np.abs(ry), np.abs(rx)), 1e-9)
    edge_y = np.where(horiz, np.where(ry < 0, oy, oy + s), oy + h + ry * t)
    edge_x = np.where(horiz, ox + h + rx * t, np.where(rx < 0, ox, ox + s))
    nby = np.where(horiz, np.where(ry < 0, oy - 1, oy + s), np.clip(np.floor(edge_y).astype(int), oy, oy + s - 1))
    nbx = np.where(horiz, np.clip(np.floor(edge_x).astype(int), ox, ox + s - 1), np.where(rx < 0, ox - 1, ox + s))
    inside = (nby >= 0) & (nby < CH) & (nbx >= 0) & (nbx < CW)
    nl = np.full(len(gy), 99)
    nl[inside] = level[nby[inside], nbx[inside]]
    smaller = nl < l
    # the stretch of the side between vertices: a smaller neighbour's own extent, else the whole side
    ns = np.where(smaller, 1 << np.minimum(nl, 30), s)
    along = np.where(horiz, nbx, nby)
    start = np.where(smaller, (along >> np.minimum(nl, 30)) << np.minimum(nl, 30), np.where(horiz, ox, oy))
    fixed = np.where(horiz, np.where(ry < 0, oy, oy + s), np.where(rx < 0, ox, ox + s))
    vy = np.stack([oy + h, np.where(horiz, fixed, start), np.where(horiz, fixed, start + ns)], -1)
    vx = np.stack([ox + h, np.where(horiz, start, fixed), np.where(horiz, start + ns, fixed)], -1)
    # unit cells: the full grid's two triangles, split along the top-right to bottom-left diagonal
    unit = l == 0
    upper = (gy - cy) + (gx - cx) < 1
    tri0, tri1 = np.array([(0, 0), (1, 0), (0, 1)]), np.array([(0, 1), (1, 0), (1, 1)])
    off = np.where(upper[:, None, None], tri0, tri1)
    vy = np.where(unit[:, None], cy[:, None] + off[..., 0], vy).astype(int)
    vx = np.where(unit[:, None], cx[:, None] + off[..., 1], vx).astype(int)
    crack = unit & np.where(upper, cut0[cy, cx], cut1[cy, cx])
    e1y, e1x, e2y, e2x = vy[:, 1] - vy[:, 0], vx[:, 1] - vx[:, 0], vy[:, 2] - vy[:, 0], vx[:, 2] - vx[:, 0]
    py, px = gy - vy[:, 0], gx - vx[:, 0]
    det = e1y * e2x - e2y * e1x
    u = (py * e2x - e2y * px) / det
    w = (e1y * px - py * e1x) / det
    return vy, vx, np.stack([1 - u - w, u, w], -1), crack


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def encode(want, N, Pu, Pv):
    """Normal map bytes that make FFNx's perturb_normal return `want` on a triangle with interpolated normal N and
    texture derivatives Pu = dP/du, Pv = dP/dv (all world space, one row per texel)."""
    T, B = np.cross(Pv, N), np.cross(N, Pu)
    inv = 1 / np.sqrt(np.maximum(np.maximum((T * T).sum(-1), (B * B).sum(-1)), 1e-20))[:, None]
    M = np.stack([T * inv, B * inv, N], -1)  # columns T, B, N
    M[np.abs(np.linalg.det(M)) < 1e-6] = np.eye(3)  # degenerate frame: leave the normal as it is
    t = np.linalg.solve(M, want[..., None])[..., 0]
    t[:, 2] = np.maximum(t[:, 2], 1e-3)
    k = 1 / np.sqrt(np.maximum(t[:, 2] ** 2 + t[:, 0] ** 2 - t[:, 1] ** 2, 1e-6))  # undo the sqrt(1 - x^2 + y^2)
    xy = np.clip(t[:, :2] * k[:, None], -1, 1)
    return np.round(128 + 127 * xy).astype(np.uint8)


def normal_map(level, cut0, cut1, Pw, nv, want, step, RW, RH):
    """RGBA normal map (want's size) for the simplified mesh: Pw, nv the grid points' world positions and the
    normals the mesh's vertices carry; want the world normal each texel should shade with."""
    NH, NW = want.shape[:2]
    ty, tx = np.mgrid[0:NH, 0:NW]
    gy = ((ty + .5) * RH / NH / step).reshape(-1)
    gx = ((tx + .5) * RW / NW / step).reshape(-1)
    vy, vx, lam, crack = texel_triangles(level, cut0, cut1, gy, gx)
    P = Pw[vy, vx]                                 # (n, 3 corners, xyz)
    N = unit((lam[..., None] * nv[vy, vx]).sum(1))
    du, dv = (vx - vx[:, :1]) * step / RW, (vy - vy[:, :1]) * step / RH
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    det = (du[:, 1] * dv[:, 2] - du[:, 2] * dv[:, 1])[:, None]
    Pu = (e1 * dv[:, 2:3] - e2 * dv[:, 1:2]) / det
    Pv = (e2 * du[:, 1:2] - e1 * du[:, 2:3]) / det
    out = np.full((NH * NW, 4), 255, np.uint8)
    out[:, :2] = 128
    ok = ~crack
    out[ok, :2] = encode(unit(want.reshape(-1, 3)[ok]), N[ok], Pu[ok], Pv[ok])
    return out.reshape(NH, NW, 4)
