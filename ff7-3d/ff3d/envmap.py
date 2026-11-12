"""Image-based ambient light for FFNx (lighting/ibl/): a sky/ground environment so shaded surfaces still get shape
(lighter facing up, warm bounce from the ground, a glow toward the sun) instead of one flat ambient colour.

FFNx needs three files: ibl/envBrdf.dds (split-sum BRDF table, sampled at (N.V, 1 - roughness)),
ibl/field_<id>_s.dds (environment cubemap with mips, for reflections) and ibl/field_<id>_d.dds (its cosine-blurred
version, for diffuse). Directions are the game's world axes (z up); cube faces in D3D order +X -X +Y -Y +Z -Z.

  env_ibl.py [field id] [sun x,y,z]
"""
import struct
import sys
from pathlib import Path
import numpy as np

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
IBL = GAME / "lighting" / "ibl"
LEVEL = 0.45
# make()'s inputs for the files in IBL, one file per field (and suffix): the same light again skips the work (user,
# 2026-11-09: a look step re-made both cubes every time, about 40% of it). Bump KEY_VERSION when make()'s output
# changes for the same inputs
KEYS = Path(__file__).resolve().parents[1] / "work" / "_ibl_keys"
KEY_VERSION = 1


def face_dirs(n):
    """Unit directions for every texel of the 6 faces (D3D/GL cube mapping), shape (6, n, n, 3)."""
    s = (np.arange(n) + 0.5) / n * 2 - 1
    sc, tc = np.meshgrid(s, s)  # sc across, tc down
    one = np.ones_like(sc)
    faces = [(one, -tc, -sc), (-one, -tc, sc), (sc, one, tc), (sc, -one, -tc), (sc, -tc, one), (-sc, -tc, -one)]
    d = np.stack([np.stack(f, -1) for f in faces])
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


NEUTRAL = {"zenith": (0.62, 0.78, 1.10), "horizon": (1.05, 0.98, 0.88), "ground": (0.42, 0.34, 0.24)}


def environment(d, sun, zenith=NEUTRAL["zenith"], horizon=NEUTRAL["horizon"], ground=NEUTRAL["ground"]):
    """Linear RGB radiance: sky colour overhead, haze at the horizon, ground bounce below, a soft glow toward the
    sun. Averages around 1 so the ambient strength setting keeps its meaning."""
    z = d[..., 2:3]
    zenith, horizon, ground = np.array(zenith), np.array(horizon), np.array(ground)
    up = np.clip(z, 0, 1) ** 0.6
    sky = horizon * (1 - up) + zenith * up
    below = horizon * 0.55 * np.exp(z * 6) + ground * (1 - np.exp(z * 6))
    c = np.where(z >= 0, sky, below)
    glow = np.clip(d @ sun, 0, 1)[..., None] ** 6
    return c * 1.25 + glow * np.array([1.0, 0.85, 0.6]) * 0.8


def solid_angles(n):
    s = (np.arange(n) + 0.5) / n * 2 - 1
    sc, tc = np.meshgrid(s, s)
    return (2 / n) ** 2 / (1 + sc ** 2 + tc ** 2) ** 1.5


def convolve(env, dirs_src, dirs_dst, power=None):
    """Cosine (power None) or cosine^power lobe convolution of a small cubemap, normalised."""
    w = np.broadcast_to(solid_angles(dirs_src.shape[1]), dirs_src.shape[:3]).reshape(-1)
    L = env.reshape(-1, 3)
    D = dirs_src.reshape(-1, 3)
    out = np.empty(dirs_dst.shape[:-1] + (3,))
    flat = dirs_dst.reshape(-1, 3)
    res = np.empty((len(flat), 3))
    for i in range(0, len(flat), 4096):
        k = np.clip(flat[i:i + 4096] @ D.T, 0, None)
        if power is not None:
            k = k ** power
        k = k * w
        res[i:i + 4096] = (k @ L) / k.sum(1, keepdims=True)
    return res.reshape(out.shape)


def to_lin8(lin, scale=1.0):
    """FFNx loads these as linear (not sRGB) textures: store linear values / scale in 8 bits."""
    return np.clip(np.round(lin / scale * 255), 0, 255).astype(np.uint8)


def dds_header(w, h, mips, cube):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8 | (0x20000 if mips > 1 else 0)
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, flags, h, w, w * 4, 0, mips)
    pf = struct.pack("<IIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF) + struct.pack("<I", 0xFF000000)
    caps1 = 0x1000 | (0x8 | 0x400000 if mips > 1 or cube else 0)
    caps2 = 0x200 | 0xFC00 if cube else 0
    return hdr + pf + struct.pack("<IIII4x", caps1, caps2, 0, 0)


def write_cube(path, faces_by_mip):
    """faces_by_mip: list over mips of (6, n, n, 3) uint8 RGB."""
    n = faces_by_mip[0].shape[1]
    out = [dds_header(n, n, len(faces_by_mip), True)]
    for f in range(6):
        for mip in faces_by_mip:
            rgb = mip[f]
            bgra = np.dstack([rgb[..., 2], rgb[..., 1], rgb[..., 0], np.full(rgb.shape[:2], 255, np.uint8)])
            out.append(np.ascontiguousarray(bgra).tobytes())
    Path(path).write_bytes(b"".join(out))


def env_brdf(n=64, samples=512):
    """Split-sum GGX table (Karis): R = scale, G = bias on F0. x = N.V, y = 1 - roughness (row 0 = roughness 1)."""
    rng = np.random.default_rng(0)
    u1, u2 = (np.arange(samples) + 0.5) / samples, rng.random(samples)
    out = np.zeros((n, n, 2))
    for j in range(n):
        rough = max(1 - (j + 0.5) / n, 0.02)
        a = rough * rough
        for i in range(n):
            nv = (i + 0.5) / n
            V = np.array([np.sqrt(1 - nv * nv), 0, nv])
            phi = 2 * np.pi * u2
            ct = np.sqrt((1 - u1) / (1 + (a * a - 1) * u1))
            st = np.sqrt(1 - ct * ct)
            H = np.stack([st * np.cos(phi), st * np.sin(phi), ct], -1)
            vh = H @ V
            L = 2 * vh[:, None] * H - V
            nl, nh = np.clip(L[:, 2], 0, 1), np.clip(H[:, 2], 0, 1)
            vh = np.clip(vh, 0, 1)
            k = a / 2
            g = (nl / (nl * (1 - k) + k)) * (nv / (nv * (1 - k) + k))
            gv = np.where(nl > 0, g * vh / np.maximum(nh * nv, 1e-6), 0)
            fc = (1 - vh) ** 5
            out[j, i] = [np.mean((1 - fc) * gv), np.mean(fc * gv)]
    return out


def _stamp(paths):
    """The files' sizes and change times (a file changed since the key was written: made again)."""
    return " ".join(f"{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in paths)


def make(field, sun, colours=NEUTRAL, suffix=""):
    """lighting/ibl/field_<id><suffix>_s.dds (reflections, with mips) and _d.dds (diffuse), plus envBrdf.dds if
    missing. suffix "_char": the characters' own (FFNx-3d). Nothing to do when the files there were made from the same
    sun and colours (KEYS)."""
    sun = np.asarray(sun, float) / np.linalg.norm(sun)
    IBL.mkdir(parents=True, exist_ok=True)
    outs = [IBL / f"field_{field}{suffix}_d.dds", IBL / f"field_{field}{suffix}_s.dds"]
    key = f"v{KEY_VERSION} {LEVEL} " + " ".join(f"{float(v):.5f}" for v in sun) + " " + " ".join(
        f"{k}:" + ",".join(f"{float(c):.5f}" for c in colours[k]) for k in sorted(colours))
    keyfile = KEYS / f"field_{field}{suffix}.txt"
    if all(p.exists() for p in outs) and (IBL / "envBrdf.dds").exists() and keyfile.exists():
        try:
            if keyfile.read_text(encoding="utf-8") == f"{key}\n{_stamp(outs)}":
                return
        except OSError:
            pass
    src_dirs = face_dirs(32)
    src = environment(src_dirs, sun, **colours)
    mips, n, level = [], 128, 0
    while n >= 1:  # specular: 128 px faces, mip chain blurred with a widening lobe (roughness grows with the level)
        dirs = face_dirs(n)
        mips.append(environment(dirs, sun, **colours) if level == 0 else
                    convolve(src, src_dirs, dirs, power=max(2.0, 256 / 4 ** level)))
        n //= 2
        level += 1
    # diffuse: cosine-convolved. FFNx's flat ambient is strength * albedo / pi but its image-based one has no 1/pi:
    # bring the brightest irradiance down to LEVEL so the ambient strength setting means about the same either way
    diff = convolve(src, src_dirs, face_dirs(32))
    top = diff.max() / LEVEL
    write_cube(outs[0], [to_lin8(diff, top)])
    write_cube(outs[1], [to_lin8(m, top) for m in mips])
    KEYS.mkdir(parents=True, exist_ok=True)
    keyfile.write_text(f"{key}\n{_stamp(outs)}", encoding="utf-8")
    if not (IBL / "envBrdf.dds").exists():
        lut = env_brdf()
        rgba = np.zeros(lut.shape[:2] + (4,), np.uint8)
        rgba[..., 0] = np.round(np.clip(lut[..., 0], 0, 1) * 255)
        rgba[..., 1] = np.round(np.clip(lut[..., 1], 0, 1) * 255)
        rgba[..., 3] = 255
        Path(IBL / "envBrdf.dds").write_bytes(dds_header(lut.shape[1], lut.shape[0], 1, False)
                                             + np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]).tobytes())
