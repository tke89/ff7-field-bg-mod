"""Hand-made test data for the FFNx-3d battle look (../FORMAT.md, ../TESTPLAN.md): battle scene oq = STAGE10 (Mako
Reactor 1 inside), formation 310 (two Grunts). Writes, beside this script:

  mods/3dtest/battle/STAGE10_T00_00.png       the floor: the original texture 4x (the Studio's enlarged copy)
  mods/3dtest/battle/STAGE10_T00_00_nml.png   ...its normal map: 8 x 8 round domes (the sign test)
  mods/3dtest/battle/STAGE10_T03_00.png       the walls: the original 4x
  mods/3dtest/battle/STAGE10_T03_00_nml.png   ...relief from the painting's fine detail (how it looks for real)
  mesh/battle/STAGE10_floorlight.dds          floor light: bright and warm at z = -4000 (the Grunts' side), dark and
                                              blue at z = +4000 (the party's)

Normal maps use the fields' encoding (ff3d/simplify.py encode): R, G = x, y toward +u / +v (v down the image), with the
shader's sqrt(1 - x^2 + y^2) undone. Run with the Studio's venv:
  ..\\..\\..\\..\\ff7-field-bg-mod\\.venv\\Scripts\\python.exe make_testdata.py
"""
import shutil
import struct
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ENLARGED = HERE.parents[3] / "work" / "models" / "enlarged"  # the Studio's
TEX = HERE / "mods" / "3dtest" / "battle"
MESH = HERE / "mesh" / "battle"


def encode(n):
    """(h, w, 3) unit tangent-space normals (x toward +u, y toward +v = down the image) -> RGBA bytes FFNx's
    perturb_normal turns back into them (it rebuilds z as sqrt(1 - x^2 + y^2))."""
    n = n / np.linalg.norm(n, axis=-1, keepdims=True)
    x, y, z = n[..., 0], n[..., 1], np.maximum(n[..., 2], 1e-3)
    k = 1 / np.sqrt(np.maximum(z * z + x * x - y * y, 1e-6))
    out = np.full(n.shape[:2] + (4,), 255, np.uint8)
    out[..., 0] = np.round(128 + 127 * np.clip(x * k, -1, 1))
    out[..., 1] = np.round(128 + 127 * np.clip(y * k, -1, 1))
    return out


def from_height(H, scale=1.0):
    """Normals of a height map (rows down = +v, columns right = +u): (-dH/du, -dH/dv, 1)."""
    gy, gx = np.gradient(H.astype(np.float64))
    return np.dstack([-gx * scale, -gy * scale, np.ones_like(H, dtype=np.float64)])


def domes(size=1024, cells=8, radius=40.0, slope=0.55):
    """cells x cells round bumps, each `radius` texels, steepest `slope` (keep under ~0.6: the shader can't tilt a
    normal more than ~35 degrees along v)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64) + 0.5
    c = size / cells
    dx = (xx % c) - c / 2
    dy = (yy % c) - c / 2
    d = np.sqrt(dx * dx + dy * dy)
    amp = slope * 2 * radius / np.pi  # cos^2 bump: steepest pi * amp / (2 r)
    H = np.where(d < radius, amp * np.cos(np.pi * d / (2 * radius)) ** 2, 0.0)
    return from_height(H)


def relief(path, strength=25.0):
    """Relief from a painting's fine detail (as ff3d/surface.py write_normals' bump): dark grooves, light ridges."""
    from PIL import ImageFilter
    im = Image.open(path).convert("RGB")
    L = np.asarray(im, np.float64) @ np.array([0.2126, 0.7152, 0.0722]) / 255
    Li = Image.fromarray(np.uint8(np.clip(L * 255, 0, 255)))
    broad = np.asarray(Li.filter(ImageFilter.GaussianBlur(4)), np.float64) / 255
    fine = Image.fromarray(np.uint8(np.clip((L - broad) * 127 + 128, 0, 255)))
    H = (np.asarray(fine.filter(ImageFilter.GaussianBlur(0.7)), np.float64) - 128) / 127
    return from_height(H, strength)


def write_dds(path, rgba):
    """Uncompressed 32-bit BGRA DDS (as ff3d/gltfout.write_dds: what FFNx's bimg loader reads)."""
    h, w = rgba.shape[:2]
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8, h, w, w * 4, 0, 1)
    pf = struct.pack("<IIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF) + struct.pack("<I", 0xFF000000)
    caps = struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]).astype(np.uint8)
    Path(path).write_bytes(hdr + pf + caps + bgra.tobytes())


def floor_light(size=64):
    """RGB = light multiplier / 2 (linear), rows from z0 (top) to z1: 1.8x warm at z0 to 0.4x cool at z1."""
    t = (np.arange(size) + 0.5)[:, None, None] / size
    warm, cool = np.array([1.8, 1.5, 1.1]), np.array([0.35, 0.45, 0.7])
    mult = warm * (1 - t) + cool * t
    rgb = np.broadcast_to(mult, (size, size, 3)) / 2
    rgba = np.dstack([np.round(np.clip(rgb, 0, 1) * 255), np.full((size, size), 255)]).astype(np.uint8)
    return rgba


def main():
    TEX.mkdir(parents=True, exist_ok=True)
    MESH.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ENLARGED / "battle_OQAC_00_4x.png", TEX / "STAGE10_T00_00.png")  # texid 0 = oq ac
    shutil.copyfile(ENLARGED / "battle_OQAF_00_4x.png", TEX / "STAGE10_T03_00.png")  # texid 3 = oq af
    Image.fromarray(encode(domes()), "RGBA").save(TEX / "STAGE10_T00_00_nml.png")
    Image.fromarray(encode(relief(TEX / "STAGE10_T03_00.png")), "RGBA").save(TEX / "STAGE10_T03_00_nml.png")
    write_dds(MESH / "STAGE10_floorlight.dds", floor_light())
    for p in sorted(HERE.rglob("*")):
        if p.is_file() and p.suffix in (".png", ".dds", ".toml"):
            print(p.relative_to(HERE), p.stat().st_size)


if __name__ == "__main__":
    main()
