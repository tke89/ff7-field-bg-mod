"""FF7 PC .p model parts (field models in char.lgp, battle models and scenes in battle.lgp, which has no extensions).

Layout as FFNx's load_p_file reads it: a 128-byte header (version 1, vertex type, then the counts: verts, normals,
unknown vec3s, texcoords, vertex colours, edges, polys, two unknowns, hundreds, groups, bounding boxes, normal index
table flag), then the unknown vec3s, vertices, normals, texcoords (float u, v), vertex colours (BGRA), polygon colours
(BGRA), edges, polygons (24 bytes: 0, three vertex indices, normal indices, edge indices, 4 bytes), the unknowns,
hundreds (100 bytes of render state per group), groups (56 bytes) and bounding boxes. A polygon's vertex indices are
its group's (add offvert); a group's texcoords start at offtex, one per group vertex."""
import struct
from dataclasses import dataclass

import numpy as np

HEADER = 128
HUNDRED = 100
GROUP = 56


@dataclass
class Group:
    polytype: int
    offpoly: int
    numpoly: int
    offvert: int
    numvert: int
    offtex: int
    textured: bool
    texid: int
    blend: int = -1     # an effect's blending: the hundred's blend_mode (1 additive, 2 subtractive, 3 quarter) when
                        # its features and options both have V_ALPHABLEND (0x400); else -1 (opaque, alpha-tested)
    colorkey: bool = False  # V_COLORKEY (0x100) in both


@dataclass
class Part:
    verts: np.ndarray     # (n, 3) float32
    normals: np.ndarray   # (m, 3) float32
    uv: np.ndarray        # (k, 2) float32
    vcol: np.ndarray      # (n, 4) uint8 RGBA, one per vertex
    pcol: np.ndarray      # (p, 4) uint8 RGBA, one per polygon
    polys: np.ndarray     # (p, 3) int: group-local vertex indices
    groups: list

    def triangles(self):
        """Per triangle: (vertex index (t, 3) into verts, uv (t, 3, 2) or nan, vertex colour (t, 3, 4) uint8,
        group (t,), texid (t,) -1 when untextured)."""
        idx, uv, grp, tid = [], [], [], []
        for gi, g in enumerate(self.groups):
            p = self.polys[g.offpoly:g.offpoly + g.numpoly].astype(np.int64)
            if not len(p):
                continue
            idx.append(p + g.offvert)
            if g.textured and len(self.uv):
                tex = self.uv[np.clip(g.offtex + p, 0, len(self.uv) - 1)]
            else:
                tex = np.full(p.shape + (2,), np.nan, np.float32)
            uv.append(tex)
            grp.append(np.full(len(p), gi, np.int32))
            tid.append(np.full(len(p), g.texid if g.textured else -1, np.int32))
        if not idx:
            z = np.zeros((0, 3), np.int64)
            return z, np.zeros((0, 3, 2), np.float32), np.zeros((0, 3, 4), np.uint8), np.zeros(0, np.int32), np.zeros(0, np.int32)
        idx = np.concatenate(idx)
        col = self.vcol[np.clip(idx, 0, len(self.vcol) - 1)] if len(self.vcol) else np.full(idx.shape + (4,), 255, np.uint8)
        return idx, np.concatenate(uv).astype(np.float32), col, np.concatenate(grp), np.concatenate(tid)


def write(verts, vcol, polys, pcol, hundred=None, header_tail=None):
    """A .p part as the game's own battle parts are laid out (rtam and the rest, checked 2026-11-07): one untextured
    group (polytype 1), no normals (FFNx and the game work them out), vertex and polygon colours, an edge table, one
    hundred (render state: pass the part's original), one bounding box and the normal index table (flag 1, 0..n-1).
    verts (n, 3) float in the part's own space, vcol (n, 4) RGBA uint8, polys (p, 3) vertex indices, pcol (p, 4) RGBA.
    header_tail: the header's last 64 bytes (the original's are left-over pointers; zeros by default)."""
    verts = np.ascontiguousarray(verts, np.float32).reshape(-1, 3)
    polys = np.asarray(polys, np.int64).reshape(-1, 3)
    nv, npoly = len(verts), len(polys)
    if nv > 65535:
        raise ValueError("a .p part holds at most 65535 vertices")
    edge_of = {}
    pedges = np.zeros((npoly, 3), np.int64)
    for i, (a, b, c) in enumerate(polys):
        for k, (u, v) in enumerate(((a, b), (b, c), (c, a))):
            key = (min(u, v), max(u, v))
            if key not in edge_of:
                edge_of[key] = len(edge_of)
            pedges[i, k] = edge_of[key]
    edges = np.array(list(edge_of.keys()), np.uint16).reshape(-1, 2)
    head = struct.pack("<16I", 1, 1, 1, nv, 0, 0, 0, nv, len(edges), npoly, 0, 0, 1, 1, 1, 1)
    head += (header_tail or b"\0" * 64)[:64].ljust(64, b"\0")
    bgra = lambda c: np.ascontiguousarray(np.asarray(c, np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]]).tobytes()
    raw = np.zeros((npoly, 12), np.uint16)
    raw[:, 1:4] = polys
    raw[:, 7:10] = pedges
    hund = (hundred or b"\0" * HUNDRED)[:HUNDRED].ljust(HUNDRED, b"\0")
    group = struct.pack("<14I", 1, 0, npoly, 0, nv, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    lo, hi = verts.min(0), verts.max(0)
    bbox = struct.pack("<I6f", 0, *hi.tolist(), *lo.tolist())
    table = np.arange(nv, dtype=np.uint32).tobytes()
    return b"".join([head, verts.tobytes(), bgra(vcol), bgra(pcol), edges.tobytes(), raw.tobytes(), hund, group, bbox,
                     table])


def hundred_of(data):
    """The first hundred (render state, 100 bytes) of a .p file, and its header's last 64 bytes."""
    h = struct.unpack_from("<16I", data, 0)
    nv, nn, nunk, nt, nc, ne, npoly, nunk2, nunk3 = h[3:12]
    off = HEADER + nunk * 12 + nv * 12 + nn * 12 + nt * 8 + nc * 4 + npoly * 4 + ne * 4 + npoly * 24 + nunk2 * 24 + nunk3 * 3
    return data[off:off + HUNDRED], data[64:HEADER]


def read(data):
    if len(data) < HEADER:
        raise ValueError("not a .p file")
    h = struct.unpack_from("<16I", data, 0)
    if h[0] != 1:
        raise ValueError("not a .p file (version)")
    nv, nn, nunk, nt, nc, ne, npoly, nunk2, nunk3, nh, ng, nb = h[3], h[4], h[5], h[6], h[7], h[8], h[9], h[10], h[11], h[12], h[13], h[14]
    off = HEADER

    def take(n, size):
        nonlocal off
        b = data[off:off + n * size]
        if len(b) < n * size:
            raise ValueError(".p file cut short")
        off += n * size
        return b

    take(nunk, 12)
    verts = np.frombuffer(take(nv, 12), np.float32).reshape(-1, 3).copy()
    normals = np.frombuffer(take(nn, 12), np.float32).reshape(-1, 3).copy()
    uv = np.frombuffer(take(nt, 8), np.float32).reshape(-1, 2).copy()
    vcol = np.frombuffer(take(nc, 4), np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]].copy()
    pcol = np.frombuffer(take(npoly, 4), np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]].copy()
    take(ne, 4)
    raw = np.frombuffer(take(npoly, 24), np.uint16).reshape(-1, 12)
    polys = raw[:, 1:4].astype(np.int32)
    take(nunk2, 24)
    take(nunk3, 3)
    hundreds = take(nh, HUNDRED)
    gdata = take(ng, GROUP)
    take(nb, 28)
    if h[15]:
        take(nv, 4)
    # battle.lgp's files have no extensions: a .p file's counts add up to its length. Mods' parts (the user's direct/
    # Barret) also carry the per-vertex normal index table without the header's flag for it: 4 bytes per vertex more,
    # which FFNx ignores as the game does. The chibi field models (direct/char, checked 2026-11-07 on all 3853 parts)
    # end in that table (0..nv-1), flagged or not, and 2939 of them in a u32 0 after it
    rest = len(data) - off
    table = 0 if h[15] else nv * 4
    if rest not in (0, table) and not (rest == table + 4 and data[-4:] == b"\0\0\0\0"):
        raise ValueError("not a .p file (size)")
    groups = []
    for i in range(ng):
        g = struct.unpack_from("<14I", gdata, i * GROUP)
        grp = Group(polytype=g[0], offpoly=g[1], numpoly=g[2], offvert=g[3], numvert=g[4], offtex=g[11],
                    textured=bool(g[12]), texid=g[13])
        if i < nh:
            hd = struct.unpack_from("<25I", hundreds, i * HUNDRED)
            on = hd[2] & hd[3]  # options & features (FFNx common.cpp: a state is set where features has it)
            # mode 0 is plain alpha blending (FFNx BLEND_AVG: src alpha), opaque where the texture is: battle scenes
            # are drawn that way. 1-3 are the effects (additive, subtractive, quarter additive)
            grp.blend = hd[17] if on & 0x400 and hd[17] in (1, 2, 3) else -1
            grp.colorkey = bool(on & 0x100)
        groups.append(grp)
    return Part(verts, normals, uv, vcol, pcol, polys, groups)
