"""A posed model or scene as plain triangles: what the renderer and the painter work on, whatever it came from (a
battle scene, a battle model, a field model)."""
from dataclasses import dataclass, field

import numpy as np

from . import pfile


@dataclass
class TexRef:
    key: str          # FFNx's override name without the palette: "battle/OPAC", "char/AABB"
    tex: object       # tex.Tex
    palette: int = 0  # the palette these triangles show

    @property
    def name(self):
        return f"{self.key}_{self.palette:02d}"


@dataclass
class Mesh:
    pos: np.ndarray       # (t, 3, 3) float32 world positions
    uv: np.ndarray        # (t, 3, 2) float32, nan where untextured
    col: np.ndarray       # (t, 3, 4) uint8 vertex colours (RGBA)
    tex: np.ndarray       # (t,) int32 index into textures, -1 untextured
    part: np.ndarray      # (t,) int32 index into parts
    group: np.ndarray     # (t,) int32 the part's group
    blend: np.ndarray     # (t,) int16 blend mode, -1 for opaque
    textures: list = field(default_factory=list)  # [TexRef]
    parts: list = field(default_factory=list)     # part names (the .p files)

    def __len__(self):
        return len(self.tex)

    def bounds(self):
        p = self.pos.reshape(-1, 3)
        return p.min(0), p.max(0)


class Builder:
    """Mesh from posed parts: add_texture() each texture once, add_part() each part with its world matrix and how its
    group texids map to the mesh's textures."""

    def __init__(self):
        self.textures, self.parts, self.chunks = [], [], []
        self._tex = {}

    def add_texture(self, ref):
        k = (ref.key, ref.palette)
        if k not in self._tex:
            self._tex[k] = len(self.textures)
            self.textures.append(ref)
        return self._tex[k]

    def add_part(self, part, matrix=None, texmap=None, name=""):
        """part: pfile.Part; matrix: 4x4 (local -> world, column vectors), None for identity; texmap: {group texid:
        mesh texture index} (a texid missing from it draws untextured)."""
        idx, uv, col, grp, tid = part.triangles()
        if not len(idx):
            return
        v = part.verts.astype(np.float64)
        if matrix is not None:
            m = np.asarray(matrix, np.float64)
            v = v @ m[:3, :3].T + m[:3, 3]
        texmap = texmap or {}
        tex = np.array([texmap.get(int(t), -1) if t >= 0 else -1 for t in tid], np.int32)
        uv = uv.copy()
        uv[tex < 0] = np.nan
        blend = np.array([part.groups[g].blend for g in grp], np.int16)
        pi = len(self.parts)
        self.parts.append(name)
        self.chunks.append((v[idx].astype(np.float32), uv, col, tex, np.full(len(idx), pi, np.int32), grp, blend))

    def mesh(self):
        if not self.chunks:
            z = np.zeros((0, 3, 3), np.float32)
            return Mesh(z, np.zeros((0, 3, 2), np.float32), np.zeros((0, 3, 4), np.uint8), np.zeros(0, np.int32),
                        np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.int16), self.textures, self.parts)
        cols = list(zip(*self.chunks))
        return Mesh(*[np.concatenate(c) for c in cols], textures=self.textures, parts=self.parts)


def from_part(data, name=""):
    """A single .p file's bytes as a Mesh (untextured): for quick looks."""
    b = Builder()
    b.add_part(pfile.read(data), name=name)
    return b.mesh()
