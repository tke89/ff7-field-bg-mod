"""A new, high-detail body for a battle model on its own skeleton (a trial, 2026-11-07).

Where the body comes from (tools/texgen/intended, run in that folder's own Python; its AI models stay out of this repo):
- run_omni.py: Hunyuan3D-Omni makes a detailed mesh from a picture of the model, steered by the model's own surface
  as a point cloud, so it keeps the model's pose and proportions;
- rig_mesh.py: reduces it (8000 triangles), gives each triangle the bone of the game's part nearest it (labels.npy)
  and writes decimated.obj;
- run_intended.py: Tencent's painter textures it (its own UV layout: textured_mesh.obj and .jpg).
All three in the "export frame": mvpaint's (the model turned by its yaw, glTF axes: (x, -y, -z) of FF7's).

What this module does with them (no AI):
- build(): each bone's triangles as a part in the bone's own space. The game poses a part rigidly with its bone, so
  the part's space is found from the game's own part: its vertices (the .p file) against where they are in the model
  at frame 0 of animation 0 (battle.load_model), a rigid fit. Corners keep the painter's UVs; vertex colours are the
  texture sampled at the corners, so the part still looks right where the engine draws without the atlas.
- export(): the parts as .p files under battle.lgp's names (rtam...), written as the game's own parts are
  (pfile.write, each with its original's render state), plus the texture as <MODEL>.png and a .uv per part under the
  game's part names (CLOUD.P00..., the weapon CLOUD.W00): FFNx-3d's battle atlas (ff7-3d/dev/battle-atlas/FORMAT.md).
- verify(): the parts read back through battle.load_model (as the game reads files) in several animations: a
  contact sheet, with the atlas.
- install()/uninstall(): the parts into the game's direct/battle/ (FFNx's direct mode reads them in place of
  battle.lgp's), the atlas files into mesh/battle/atlas/, with a manifest. Only when asked; it refuses while
  battleatlas has this model's painted texture installed (the same atlas file names).

  python -m ff7bg.models.newbody export <id> <rig dir> <painted dir> [--version vN]
  python -m ff7bg.models.newbody verify <id> [--version vN]
  python -m ff7bg.models.newbody install <id> [--version vN]   /   uninstall <id>
"""
import json
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import battle
from . import battleatlas as BA
from . import pfile

WORK = BA.WORK.parent / "newbody"
MANIFEST = WORK / "installed.json"
DIRECT = battle.DIRECT
_lock = threading.RLock()


@dataclass
class NewPart:
    file: str            # battle.lgp's name ("rtam")
    game: str            # the game's name ("CLOUD.P00")
    verts: np.ndarray    # (n, 3) float32 in the part's own space
    polys: np.ndarray    # (p, 3) int
    uv: np.ndarray       # (p, 3, 2) float32 per corner, v = 0 at the image's top (FFUV)
    vcol: np.ndarray     # (n, 4) uint8 RGBA
    pcol: np.ndarray     # (p, 4) uint8 RGBA
    hundred: bytes
    tail: bytes


def read_obj(path):
    """(vertices (n, 3), faces (f, 3), uv per corner (f, 3, 2) or None) of a triangle OBJ (v, vt, f a/b)."""
    v, vt, f = [], [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("v "):
            v.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("vt "):
            vt.append([float(x) for x in line.split()[1:3]])
        elif line.startswith("f "):
            f.append([[int(c) - 1 if c else -1 for c in (k.split("/") + [""])[:2]] for k in line.split()[1:4]])
    v, f = np.array(v, np.float64), np.array(f, np.int64)
    uv = np.array(vt, np.float64)[f[..., 1]] if vt and (f[..., 1] >= 0).all() else None
    return v, f[..., 0], uv


def to_model(p, yaw):
    """Export-frame points -> FF7 model space: glTF axes back ((x, -y, -z)), then the yaw undone (battleatlas.placed
    turned rows by R.T)."""
    q = np.asarray(p, np.float64) * np.array([1.0, -1.0, -1.0])
    return q @ battle.rotation(0, yaw, 0) if yaw else q


def kabsch(P, Q):
    """Rigid (R, t) with R P + t ~ Q (rows)."""
    mp, mq = P.mean(0), Q.mean(0)
    U, _, Vt = np.linalg.svd((Q - mq).T @ (P - mp))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt
    return R, mq - R @ mp


def part_frames(ba):
    """Per part of the posed model (ba.posed.parts index): (R, t) its part space -> model space at frame 0 of
    animation 0, from the game's .p file against the posed triangles (same order: battle.load_model adds a part's
    polygons in its file's order). Raises when a fit isn't rigid (the order assumption broken)."""
    out = {}
    m = ba.posed
    for j, name in enumerate(m.parts):
        P = pfile.read(battle.read_file(name))
        idx = P.triangles()[0]
        local = P.verts[idx].reshape(-1, 3).astype(np.float64)
        world = m.pos[m.part == j].reshape(-1, 3).astype(np.float64)
        if len(local) != len(world):
            raise ValueError(f"{name}: {len(local) // 3} polygons in the file, {len(world) // 3} posed")
        R, t = kabsch(local, world)
        err = np.abs(local @ R.T + t - world).max()
        if err > 1e-2 * max(np.ptp(world, 0).max(), 1.0):
            raise ValueError(f"{name}: not a rigid fit ({err:.3f})")
        out[j] = (R, t)
    return out


def build(id, rig_dir, painted_dir, yaw=None):
    """The new parts (NewPart per part of the model that has triangles; parts left with none keep a single tiny
    triangle so the game still finds a file) and the texture (RGBA)."""
    from . import mvpaint as M
    sub = M.subject("battle", id)
    ba = sub.obj
    yaw = sub.yaw if yaw is None else yaw
    rig_dir, painted_dir = Path(rig_dir), Path(painted_dir)
    V, F, _ = read_obj(rig_dir / "decimated.obj")
    lab = np.load(rig_dir / "labels.npy")
    tv, tf, tuv = read_obj(painted_dir / "textured_mesh.obj")
    tex = np.asarray(Image.open(painted_dir / "textured_mesh.jpg").convert("RGB"))
    if len(tf) != len(F) or tuv is None:
        raise ValueError("the painted mesh isn't the rigged one (faces differ)")
    P, T = V[F], tv[tf]
    if np.abs(P.mean(1) - T.mean(1)).max() > 1e-4 * np.ptp(V, 0).max():
        raise ValueError("the painted mesh's faces are in another order")
    perms = np.array([[0, 1, 2], [1, 2, 0], [2, 0, 1], [0, 2, 1], [2, 1, 0], [1, 0, 2]])
    err = np.stack([np.linalg.norm(T[:, p] - P, axis=-1).sum(-1) for p in perms], 1)
    uvc = np.take_along_axis(tuv, perms[err.argmin(1)][..., None], 1)  # the painter's uv per rigged corner
    uvc = np.stack([uvc[..., 0], 1 - uvc[..., 1]], -1).astype(np.float32)  # OBJ v runs up; FFUV's from the top
    H, W = tex.shape[:2]
    corner_rgb = tex[np.clip((uvc[..., 1] * H).astype(int), 0, H - 1), np.clip((uvc[..., 0] * W).astype(int), 0, W - 1)]
    Vm = to_model(V, yaw)
    frames = part_frames(ba)
    files = {pf.file: pf for pf in BA.part_files(ba.id, weapon=ba.original.weapon if ba.original.weapon >= 0 else None)}
    parts = []
    for j, name in enumerate(ba.posed.parts):
        R, t = frames[j]
        data = battle.read_file(name)
        hund, tail = pfile.hundred_of(data)
        sel = np.nonzero(lab == j)[0]
        if not len(sel):  # a bone the new body gave nothing: a single degenerate triangle at its origin
            parts.append(NewPart(name, files[name].game, np.zeros((3, 3), np.float32), np.array([[0, 1, 2]]),
                                 np.zeros((1, 3, 2), np.float32), np.full((3, 4), 255, np.uint8),
                                 np.full((1, 4), 255, np.uint8), hund, tail))
            continue
        vids, inv = np.unique(F[sel], return_inverse=True)
        polys = inv.reshape(-1, 3)
        local = (Vm[vids] - t) @ R  # R^T (w - t), as rows
        rgb = corner_rgb[sel].reshape(-1, 3).astype(np.float64)
        acc = np.zeros((len(vids), 3))
        cnt = np.zeros(len(vids))
        np.add.at(acc, polys.ravel(), rgb)
        np.add.at(cnt, polys.ravel(), 1)
        vcol = np.column_stack([np.clip(acc / np.maximum(cnt, 1)[:, None], 0, 255), np.full(len(vids), 255)])
        pcol = np.column_stack([corner_rgb[sel].mean(1), np.full(len(sel), 255)])
        parts.append(NewPart(name, files[name].game, local.astype(np.float32), polys, uvc[sel],
                             vcol.astype(np.uint8), pcol.astype(np.uint8), hund, tail))
    rgba = np.dstack([tex, np.full(tex.shape[:2], 255, np.uint8)])
    return parts, rgba, ba


def uv_bytes(part):
    return b"FFUV" + struct.pack("<3I", 1, len(part.verts), len(part.polys)) + \
        np.ascontiguousarray(part.uv, np.float32).tobytes()


def version_dir(id, version=None):
    d = WORK / id.lower()
    if version:
        return d / version
    vs = sorted((p for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()), key=lambda p: int(p.name[1:]))
    if not vs:
        raise FileNotFoundError(f"no new body for {id}")
    return vs[-1]


def export(id, rig_dir, painted_dir, version=None, info=None):
    """A version folder: parts/<lgp name> (.p), atlas/<MODEL>.png and <MODEL>.<Pnn>.uv, info.json. Returns it."""
    parts, rgba, ba = build(id, rig_dir, painted_dir)
    d = WORK / id.lower()
    if version is None:
        n = [int(p.name[1:]) for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()] if d.exists() else []
        version = f"v{max(n, default=0) + 1}"
    out = d / version
    (out / "parts").mkdir(parents=True, exist_ok=True)
    (out / "atlas").mkdir(parents=True, exist_ok=True)
    model = parts[0].game.split(".")[0]
    for p in parts:
        data = pfile.write(p.verts, p.vcol, p.polys, p.pcol, p.hundred, p.tail)
        q = pfile.read(data)  # the written file reads back as built
        assert len(q.verts) == len(p.verts) and np.array_equal(q.polys, p.polys)
        (out / "parts" / p.file).write_bytes(data)
        (out / "atlas" / f"{p.game}.uv").write_bytes(uv_bytes(p))
    Image.fromarray(rgba).save(out / "atlas" / f"{model}.png")
    info = dict(info or {}, id=id, model=model, made=time.strftime("%Y-%m-%d %H:%M:%S"),
                rig=str(rig_dir), painted=str(painted_dir),
                parts={p.file: {"game": p.game, "verts": int(len(p.verts)), "polys": int(len(p.polys))}
                       for p in parts},
                triangles=int(sum(len(p.polys) for p in parts)),
                original_triangles=int(len(ba.posed.pos)))
    (out / "info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return out


@contextmanager
def overlay(parts_dir):
    """battle.read_file / has_file serving a version's part files first (the way the game will once installed)."""
    files = {p.name.lower(): p for p in Path(parts_dir).iterdir() if p.is_file()}
    rf, hf = battle.read_file, battle.has_file

    def read_file(name, battle_lgp=battle.BATTLE_LGP):
        p = files.get(name.lower())
        return p.read_bytes() if p is not None else rf(name, battle_lgp)

    def has_file(name, battle_lgp=battle.BATTLE_LGP):
        return name.lower() in files or hf(name, battle_lgp)
    battle.read_file, battle.has_file = read_file, has_file
    try:
        yield
    finally:
        battle.read_file, battle.has_file = rf, hf


def posed(id, version_path, animation=0, frame=0):
    """(the new body as the game would pose it: a BattleMesh read through overlay(), per-triangle atlas uv (t, 3, 2),
    the atlas RGBA)."""
    vp = Path(version_path)
    with overlay(vp / "parts"):
        m = battle.load_model(id, animation, frame, weapon=0)
    uv = np.zeros((len(m.pos), 3, 2), np.float32)
    for j, name in enumerate(m.parts):
        sel = np.nonzero(m.part == j)[0]
        game = json.loads((vp / "info.json").read_text())["parts"][name]["game"]
        raw = (vp / "atlas" / f"{game}.uv").read_bytes()
        n = struct.unpack_from("<3I", raw, 4)[2]
        u = np.frombuffer(raw[16:], np.float32).reshape(n, 3, 2)
        if len(u) != len(sel):
            raise ValueError(f"{name}: {len(u)} uv polygons, {len(sel)} posed")
        uv[sel] = u
    model = json.loads((vp / "info.json").read_text())["model"]
    atlas = np.asarray(Image.open(vp / "atlas" / f"{model}.png").convert("RGBA"))
    return m, uv, atlas


def verify(id, version=None, animations=((0, 0), (5, 0), (21, 0), (13, 8)), size=360):
    """The version read back as the game reads it, posed in several animations, drawn with its atlas (flat): the
    original beside it. Writes <version>/preview/verify.jpg; returns its path."""
    from dataclasses import replace
    from . import paint as paintmod, render
    vp = version_dir(id, version)
    rows = []
    for a, f in animations:
        try:
            new, uv, atlas = posed(id, vp, a, f)
        except Exception as e:  # an animation that isn't there
            print("skip", a, f, e)
            continue
        old = battle.load_model(id, a, f, weapon=0)
        nm = replace(new, uv=uv, tex=np.zeros(len(new.pos), np.int32))
        cells = []
        for m, img in ((old, None), (nm, atlas)):
            for yaw in (BA.model_yaw(id) if hasattr(BA, "model_yaw") else 0, 90, 180):
                lo, hi = old.pos.reshape(-1, 3).min(0), old.pos.reshape(-1, 3).max(0)
                c = (lo + hi) / 2
                r = np.linalg.norm(hi - lo) * 0.6
                ang = np.radians(yaw)
                eye = c + np.array([np.sin(ang), -0.15, -np.cos(ang)]) * r * 3
                cam = render.Camera(eye=eye, target=c, up=np.array([0.0, -1.0, 0.0]), fov_y=40, width=size,
                                    height=size)
                # (no alpha test for the new body: its tex 0 is the atlas here, but the mesh's texture 0 is still the
                # game's own, whose cut-outs would punch holes)
                ras = render.rasterize(m, cam, alpha_test=img is None)
                fg = render.shade(m, ras, modulate=True) if img is None else paintmod.shade(m, ras, [img])
                bg = np.full((size, size, 3), 228, np.uint8)
                bg[ras.tri >= 0] = fg[ras.tri >= 0]
                cells.append(bg)
        rows.append(np.hstack(cells))
    out = vp / "preview" / "verify.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack(rows)).save(out, quality=90)
    return out


# --- install ---------------------------------------------------------------------------------------------------------

def installed():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def install(id, version=None):
    """The version's parts into direct/battle/ and its atlas files into mesh/battle/atlas/, recorded with their
    hashes. Refuses (writing nothing) while battleatlas has this model installed, or where a file it would write is
    there and isn't ours."""
    id = id.lower()
    vp = version_dir(id, version)
    if id in BA.installed():
        raise FileExistsError(f"{id} has a painted texture installed (battleatlas): uninstall it first")
    plan = [(p, DIRECT / p.name) for p in sorted((vp / "parts").iterdir())]
    plan += [(p, BA.atlas_dir() / p.name) for p in sorted((vp / "atlas").iterdir())]
    with _lock:
        data = installed()
        if id in data:
            uninstall(id)
            data = installed()
        clash = [str(d) for _, d in plan if d.exists()]
        if clash:
            raise FileExistsError("not ours, left alone: " + ", ".join(clash))
        done, made = [], []
        try:
            for _, d in plan:
                for folder in reversed([d.parent, *d.parent.parents]):
                    if not folder.exists():
                        folder.mkdir()
                        made.append(str(folder))
            for s, d in plan:
                d.write_bytes(s.read_bytes())
                done.append({"path": str(d), "sha1": BA._hash(d)})
        except Exception:
            for f in done:
                Path(f["path"]).unlink(missing_ok=True)
            raise
        data[id] = {"version": vp.name, "from": str(vp), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "files": done, "folders": made}
        _save(data)
        return data[id]


def uninstall(id):
    """Exactly the files install() wrote (left where something changed them since), and the folders it made if
    empty. Returns (removed, left)."""
    id = id.lower()
    with _lock:
        data = installed()
        entry = data.pop(id, None)
        if not entry:
            return 0, []
        removed, left = 0, []
        for f in entry["files"]:
            p = Path(f["path"])
            if not p.exists():
                continue
            if BA._hash(p) != f.get("sha1"):
                left.append(str(p))
                continue
            p.unlink()
            removed += 1
        for d in reversed(entry.get("folders", [])):
            try:
                Path(d).rmdir()
            except OSError:
                pass
        _save(data)
        return removed, left


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.newbody")
    ap.add_argument("cmd", choices=("export", "verify", "install", "uninstall"))
    ap.add_argument("id")
    ap.add_argument("rig", nargs="?")
    ap.add_argument("painted", nargs="?")
    ap.add_argument("--version")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        out = export(a.id, a.rig, a.painted, a.version)
        print(out)
        print(verify(a.id, out.name))
    elif a.cmd == "verify":
        print(verify(a.id, a.version))
    elif a.cmd == "install":
        print(json.dumps(install(a.id, a.version), indent=1))
    else:
        print(uninstall(a.id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
