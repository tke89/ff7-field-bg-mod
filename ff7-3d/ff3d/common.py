"""Paths, and a field with everything the 3D build needs: camera, walkmesh, background tiles, maplist id, and the
Studio render the game uses (the field's current version) with the render <-> background <-> camera mappings."""
import json
import os
import struct
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

STUDIO = Path(os.environ.get("FF7_STUDIO", Path(__file__).resolve().parents[2]))  # the Studio: the repo this is in
sys.path.insert(0, str(STUDIO))
from ff7bg.field import _sections, parse_background  # noqa: E402
from ff7bg.lgp import LgpArchive  # noqa: E402
from ff7bg.lzs import decompress  # noqa: E402
from ff7bg.sheets import canvas_rect  # noqa: E402

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
LGP = GAME / "data" / "field" / "flevel.lgp"
HERE = Path(__file__).resolve().parent.parent
WORK = HERE / "work"
MESH = GAME / "mesh" / "field"            # FFNx-3d scene meshes: MESH/<field>/scene.gltf
PAGES = GAME / "mods" / "3dtest" / "field"  # override textures: hidden 2D pages, glow frames
LIGHTING = GAME / "lighting"


@lru_cache(maxsize=1)
def maplist():
    with LgpArchive(str(LGP)) as lgp:
        d = lgp.read("maplist")
    n = struct.unpack_from("<H", d, 0)[0]
    return [d[2 + 32 * i:2 + 32 * (i + 1)].split(b"\0")[0].decode() for i in range(n)]


def _version(vdir):
    renders = sorted(vdir.glob("render.*"))
    if renders and (vdir / "meta.json").exists():
        return renders[0], json.loads((vdir / "meta.json").read_text())
    return None, None


def latest_render(name):
    """(render path, meta) of the field's newest Studio version, or (None, None)."""
    root = STUDIO / "ai" / "versions" / name
    for v in sorted(root.iterdir(), reverse=True) if root.exists() else []:
        render, meta = _version(v)
        if render:
            return render, meta
    return None, None


def current_version(name):
    """The id of the field's version the game uses (the Studio's current one), or None."""
    p = STUDIO / "ai" / "fields" / f"{name}.json"
    return json.loads(p.read_text()).get("current") if p.exists() else None


def studio_render(name):
    """(render path, meta, version id) of the field's current version, else its newest; (None, None, None)."""
    vid = current_version(name)
    if vid:
        render, meta = _version(STUDIO / "ai" / "versions" / name / vid)
        if render:
            return render, meta, vid
    render, meta = latest_render(name)
    return render, meta, render.parent.name if render else None


def _walkmesh(secs):
    """The walkmesh's triangles: (n, 3 corners, xyz) in field world coordinates."""
    wm = secs[4]
    (n,) = struct.unpack_from("<I", wm, 0)
    return np.array(struct.unpack_from(f"<{n * 12}h", wm, 4), float).reshape(n, 3, 4)[:, :, :3]


def walk_triangle(tris):
    """A central, roomy walkmesh triangle to drop the player on for a look around."""
    c = tris.mean(1)
    area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    d = np.linalg.norm(c[:, :2] - np.median(c[:, :2], 0), axis=1)
    return int(np.argmin(d / (d.max() + 1e-9) - 0.5 * area / (area.max() + 1e-9)))


@lru_cache(maxsize=None)
def spot(name):
    """Where to put the player in a field for a look around: (maplist id, walkmesh triangle), without the render."""
    with LgpArchive(str(LGP)) as lgp:
        raw = decompress(lgp.read(name))
    return maplist().index(name), walk_triangle(_walkmesh(_sections(raw)))


def _camera(cam):
    """The camera section: R (rows: camera x right, y down, z forward, in world), T, zoom. A world point P is at
    background coordinates zoom * c.xy / c.z, c = R P + T."""
    R = np.stack([np.array(struct.unpack_from("<3h", cam, o), float) for o in (0, 6, 12)]) / 4096.0
    return R, np.array(struct.unpack_from("<3i", cam, 0x14), float), struct.unpack_from("<H", cam, 0x24)[0]


@lru_cache(maxsize=64)
def camera(name):
    """(R, T, zoom, canvas rect) of a field, without the rest of Field (the Studio's 3D view)."""
    with LgpArchive(str(LGP)) as lgp:
        raw = decompress(lgp.read(name))
    return (*_camera(_sections(raw)[1]), canvas_rect(parse_background(name, raw)))


class Field:
    """variant: a stage-2 render in work/<field>/<variant>/ instead of the Studio's latest (see regen.py)."""

    def __init__(self, name, variant=None):
        self.name = name
        self.variant = variant
        with LgpArchive(str(LGP)) as lgp:
            self.raw = decompress(lgp.read(name))
        secs = _sections(self.raw)
        self.R, self.T, self.zoom = _camera(secs[1])
        # R is stored to 1/4096, so it isn't quite a rotation: R.T is no inverse. Unprojecting with it put points
        # off their camera rays by (R R^T - I)(P - T), up to 7 background px at depth 200 where the camera is ~11000
        # from the world's origin (blue_1: near rocks slid off their painting, black holes along their edges)
        self.Rinv = np.linalg.inv(self.R)
        self.tris = _walkmesh(secs)
        self.bg = parse_background(name, self.raw)
        self.mx, self.my, self.w, self.h = canvas_rect(self.bg)
        self.id = maplist().index(name)
        if variant:
            vdir = WORK / name / variant
            self.render, self.meta = vdir / "render.png", json.loads((vdir / "meta.json").read_text())
            self.version = variant
        else:
            self.render, self.meta, self.version = studio_render(name)
        self.work = WORK / name / variant if variant else WORK / name
        self.work.mkdir(parents=True, exist_ok=True)
        if self.render is not None:
            from PIL import Image
            self.raw_render = self.render
            self.render = self.aligned()  # the Studio's aligned image: the render on the original's geometry
            self.RW, self.RH = Image.open(self.render).size
            self.scale = self.RW // self.w
            self.frame = [0, 0, 0, 0]  # left, top, right, bottom margin around the canvas, background px

    CACHES = ("da3_depth.npy", "walk_depth.npy", "walk_layers.npy", "mg_albedo.png", "mg_normals.png", "ao.npy", "static_light.npy", "light_kept.json",
              "depth_fit.json")

    def aligned(self):
        """The render warped onto the original by the Studio's aligner (as it does before cutting pages) and
        cropped to the canvas: work/.../aligned.png. Cached; the 3D caches go when the source render changes."""
        out = self.work / "aligned.png"
        stamp = self.work / "aligned_from.txt"
        src = f"{self.raw_render} {self.raw_render.stat().st_mtime}"

        def key(s):  # the version and file time, not the folder: the same render in a copy of the Studio is the same
            path, mtime = s.rsplit(" ", 1)
            return Path(path).parent.name, Path(path).name, mtime
        if out.exists() and stamp.exists() and key(stamp.read_text()) == key(src):
            return out
        from PIL import Image
        from ff7bg import ai
        jobs = json.loads((STUDIO / "ai" / "jobs.json").read_text())
        main = ai.prepare_main(self.bg, self.raw_render, jobs[self.name], self.meta.get("rendered_defaults", {}),
                               do_align=True, frame=self.meta.get("frame"))
        Image.fromarray(main["ai_hi"]).save(out)
        for c in self.CACHES:
            (self.work / c).unlink(missing_ok=True)
        stamp.write_text(src)
        (self.work / "align.json").write_text(json.dumps({"drift_before": main["before"].median,
                                                          "drift_after": main["after"].median,
                                                          "missing": float(main["missing"])}))
        return out

    # render pixel coordinates (continuous) -> background coordinates
    def to_bg(self, rx, ry):
        return rx / self.scale - self.frame[0] + self.mx, ry / self.scale - self.frame[1] + self.my

    def grid_shape(self, step=2):
        return self.RH // step, self.RW // step

    def unproject(self, bx, by, Z):
        """Background coords + camera depth -> camera space and world points."""
        Pc = np.stack([bx * Z / self.zoom, by * Z / self.zoom, Z], -1)
        Pw = (Pc.reshape(-1, 3) - self.T) @ self.Rinv.T
        return Pc, Pw.reshape(Pc.shape)

    def project(self, pts):
        pc = pts @ self.R.T + self.T
        z = pc[..., 2]
        return np.stack([self.zoom * pc[..., 0] / z, self.zoom * pc[..., 1] / z], -1), z

    def walk_triangle(self):
        return walk_triangle(self.tris)
