"""Depth: Depth Anything 3 on the render (through ComfyUI), its relative depth fitted to the walkmesh's true camera
depth, giving every grid vertex a camera-space position."""
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import comfy, glb


def da3(f, resolution=1512, step=2):
    """DA3's depth on the render's grid (one vertex every `step` render pixels), cached in work/<field>/."""
    return da3_image(f.render, f.work / "da3_depth.npy", f"{f.name}_render.png", resolution, step)


def da3_image(path, out, upload_name, resolution=1512, step=2):
    """Depth Anything 3's (relative) depth of any image on a grid of one vertex every `step` pixels, cached."""
    if out.exists():
        return np.load(out)
    from PIL import Image
    W, H = Image.open(path).size
    name = comfy.upload(path, upload_name)
    outputs = comfy.run({
        "img": {"class_type": "LoadImage", "inputs": {"image": name}},
        "model": {"class_type": "LoadDA3Model", "inputs": {"model_name": "depth_anything_3_mono_large.safetensors",
                                                           "weight_dtype": "default"}},
        "inf": {"class_type": "DA3Inference", "inputs": {"da3_model": ["model", 0], "image": ["img", 0],
                                                        "resolution": resolution, "resize_method": "upper_bound_resize",
                                                        "mode": "mono"}},
        "mesh": {"class_type": "DA3GeometryToMesh", "inputs": {"da3_geometry": ["inf", 0], "batch_index": 0,
                                                              "decimation": step, "discontinuity_threshold": 0.0,
                                                              "confidence_threshold": 0.0, "use_sky_mask": False,
                                                              "texture": False}},
        "save": {"class_type": "SaveGLB", "inputs": {"mesh": ["mesh", 0], "filename_prefix": f"ff7_3d/{Path(upload_name).stem}_da3"}},
    })
    entry = comfy.files(outputs, "save")[0]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "d.glb"
        p.write_bytes(comfy.fetch(entry))
        pos = glb.read(p)["pos"]
    gh, gw = -(-H // step), -(-W // step)
    if len(pos) != gh * gw:
        raise ValueError(f"DA3 mesh has {len(pos)} vertices, expected {gh}x{gw}")
    z = np.abs(pos[:, 2]).reshape(gh, gw)[: H // step, : W // step]
    np.save(out, z)
    return z


def grid_bg(f, step=2):
    """Background coordinates of the grid vertices (vertex j sits at render x = j * step)."""
    gh, gw = f.grid_shape(step)
    ys, xs = np.mgrid[0:gh, 0:gw].astype(float)
    return f.to_bg(xs * step, ys * step)


def walkmesh_layers(f, step=2):
    """True camera depth of every walkmesh surface over each grid vertex, nearest first: (layers, gh, gw), NaN where
    there are fewer. Most of a field has one; walkways over walkways have more (mds7st1: the path inside a train car
    and the gangway over the cars). Cached in work/<field>/walk_layers.npy."""
    out = f.work / "walk_layers.npy"
    if out.exists():
        return np.load(out)
    L = walkmesh_depth_at(f, *grid_bg(f, step), layers=True)
    np.save(out, L)
    return L


LAYER_GAP = 0.015  # surfaces closer than this share of their depth are one (neighbouring triangles' shared edges)
MAX_LAYERS = 3


def _add_layer(L, vals):
    """Put each value in its point's sorted list of surfaces (L: (MAX_LAYERS, n), NaN last), merging near-equal
    ones."""
    new = ~np.isnan(vals) & ~np.any(np.abs(L - vals[None]) < LAYER_GAP * vals[None], axis=0)
    if new.any():
        L[:, new] = np.sort(np.concatenate([L[:, new], vals[None, new]]), axis=0)[:MAX_LAYERS]


def walkmesh_depth_at(f, bx, by, layers=False):
    """True camera depth of the walkmesh at background coordinates (arrays), NaN where it isn't: the nearest surface,
    or every surface nearest first ((MAX_LAYERS, *shape)) with `layers`."""
    s, z = f.project(f.tris.reshape(-1, 3))
    s, z = s.reshape(-1, 3, 2), z.reshape(-1, 3)
    Z = np.full(bx.shape, np.nan)
    L = np.full((MAX_LAYERS, bx.size), np.nan) if layers else None
    flat = np.arange(bx.size).reshape(bx.shape)
    for (a, b, c), (za, zb, zc) in zip(s, z):
        if min(za, zb, zc) <= 0:
            continue
        x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
        y0, y1 = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        m = (bx >= x0) & (bx <= x1) & (by >= y0) & (by <= y1)
        if not m.any():
            continue
        px, py = bx[m], by[m]
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-9:
            continue
        l1 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        l2 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        vals = np.where(inside, 1 / (l1 / za + l2 / zb + l3 / zc), np.nan)  # perspective-correct
        if layers:
            sub = L[:, flat[m]]
            _add_layer(sub, vals)
            L[:, flat[m]] = sub
            continue
        cur = Z[m]
        Z[m] = np.where(np.isnan(cur) | (vals < cur), np.where(np.isnan(vals), cur, vals), cur)
    return L.reshape(MAX_LAYERS, *bx.shape) if layers else Z


def robust_fit(x, y, iters=8):
    """y ~ a*x + b, reweighting away from outliers (things standing in front of the floor)."""
    w = np.ones_like(x)
    for _ in range(iters):
        A = np.stack([x * w, w], -1)
        a, b = np.linalg.lstsq(A, y * w, rcond=None)[0]
        r = y - (a * x + b)
        s = 1.4826 * np.median(np.abs(r)) + 1e-9
        w = 1 / np.maximum(1, np.abs(r) / (2.5 * s))
    return a, b, r


def anchor(Z, W, smooth=0.04, up=None, iters=6):
    """Z bent onto the walkmesh: Z * exp(c), c a smooth field (Gaussian `smooth` x the grid's size) fitted to
    log(W / Z) on the floor: where the walkmesh is and the surface faces up (`up`, 0..1: building fronts, fences and
    walls stand over walkmesh behind them and must keep standing in front of it), reweighting away what's still much
    nearer (tables). One scale and offset for a whole scene leaves its ground tilted or bowed against the walkmesh
    (farm: 10% median, 20% at worst; shadows and feet slid off it); this follows it everywhere. Inside upright things the
    correction comes from the floor around them; far from any floor it fades to its median. Returns (Z, weights)."""
    m = ~np.isnan(W)
    up = np.ones(Z.shape, np.float32) if up is None else up.astype(np.float32)
    sig = smooth * max(Z.shape)
    e = np.zeros(Z.shape, np.float32)
    e[m] = np.log(W[m] / Z[m])
    wt = m * up
    blur = lambda a: cv2.GaussianBlur(a, (0, 0), sig)
    for _ in range(iters):
        c = blur(wt * e) / np.maximum(blur(wt), 1e-6)
        r = (e - c)[m]
        s = 1.4826 * np.median(np.abs(r - np.median(r))) + 1e-4
        wt[:] = 0
        wt[m] = up[m] * np.where(r > 4 * s, 0.0, 1.0) / (1 + (r / (2.5 * s)) ** 2)  # r > 0: in front of the floor
    den = blur(wt)
    c = blur(wt * e) / np.maximum(den, 1e-6)
    trust = den / (den + 0.02)
    c = trust * c + (1 - trust) * float(np.median(e[m]))
    return Z * np.exp(c), wt


MIN_WALK_POINTS = 200   # fewer grid points on the walkmesh than this: fitted off the picture (off_picture_fit)
OFF_PICTURE_SPREAD = 1.5  # ...with the scene's far depth (DA3's 95th percentile) this times its near (5th): the median
                          # over the fields fitted on their walkmesh (1.44 over the 271 good fits, 1.52 over all 385)
OFF_PICTURE_BAND = 16.0   # ...anchored on the walkmesh within this many background px of its nearest to the picture


def off_picture_fit(f, d, step):
    """Z = a * d + b for a scene whose walkmesh isn't in the picture (gldelev, junone3, md0: just below its bottom
    edge, where the characters are never seen): anchored where the walkmesh comes nearest the picture (its camera
    depth there against DA3's at the picture's edge nearest it), with the usual depth spread over the scene
    (OFF_PICTURE_SPREAD), since one anchor can't tell scale from offset (b is 24-91% of the depth in fitted scenes).
    Returns (a, b, the walkmesh's anchor points' depths, their fit errors, stats); raises if no walkmesh is in front of
    the camera at all."""
    gh, gw = d.shape
    u, v = np.meshgrid(np.linspace(0, 1, 9), np.linspace(0, 1, 9))
    keep = u + v <= 1
    u, v = u[keep], v[keep]
    t = f.tris  # (n, 3, 3): every triangle sampled evenly
    pts = (t[:, None, 0] * (1 - u - v)[None, :, None] + t[:, None, 1] * u[None, :, None]
           + t[:, None, 2] * v[None, :, None]).reshape(-1, 3)
    s, z = f.project(pts)
    front = z > 1.0
    if not front.any():
        raise ValueError(f"{f.name}: the walkmesh is neither in the picture nor in front of the camera")
    s, z = s[front], z[front]
    # how far outside the picture (background px), and the grid point at its edge nearest each
    x0, y0, x1, y1 = f.mx - f.frame[0], f.my - f.frame[1], f.mx - f.frame[0] + f.RW / f.scale, f.my - f.frame[1] + f.RH / f.scale
    out = np.hypot(np.maximum.reduce([x0 - s[:, 0], s[:, 0] - x1, np.zeros(len(s))]),
                   np.maximum.reduce([y0 - s[:, 1], s[:, 1] - y1, np.zeros(len(s))]))
    near = out <= out.min() + OFF_PICTURE_BAND
    gx = np.clip(np.round((s[near, 0] - x0) * f.scale / step), 0, gw - 1).astype(int)
    gy = np.clip(np.round((s[near, 1] - y0) * f.scale / step), 0, gh - 1).astype(int)
    de, We = float(np.median(d[gy, gx])), float(np.median(z[near]))
    d5, d95 = np.percentile(d, [5, 95])
    # a d95 + b = SPREAD (a d5 + b) and a de + b = We
    k = (d95 - OFF_PICTURE_SPREAD * d5) / (OFF_PICTURE_SPREAD - 1)
    a = We / max(de + k, 1e-6 * max(abs(de), 1.0))
    b = a * k
    rel = np.abs(a * d[gy, gx] + b - z[near]) / z[near]
    stats = {"fallback": "walkmesh off the picture", "off_picture_px": round(float(out.min()), 1),
             "anchor_depth": round(We, 1), "anchor_points": int(near.sum()), "spread": OFF_PICTURE_SPREAD}
    return a, b, z[near], rel, stats


def camera_depth(f, step=2, smooth=0.04, up=None):
    """Camera depth of every grid vertex: DA3 fitted to the walkmesh by Z = a * d + b, then bent onto it locally
    (anchor, on surfaces facing `up`; smooth 0: not). Where walkways overlap on screen, the one the painting shows
    there is the one nearest the fitted depth (the nearest to the camera isn't always: mds7st1's gangway over the open
    train car Cloud walks through). That walkmesh is saved as work/<field>/walk_depth.npy. Also returns fit stats,
    floor errors before and after the bend. A walkmesh that isn't in the picture: off_picture_fit, and no bend."""
    d = da3(f, step=step)
    L = walkmesh_layers(f, step)
    W = L[0]
    m = ~np.isnan(W)
    stats = {"walk_coverage": float(m.mean())}
    if m.sum() < MIN_WALK_POINTS:
        a, b, near, rel, more = off_picture_fit(f, d, step)
        stats.update(more)
        smooth = 0  # nothing to bend onto
        floor = float(np.min(near)) * 0.3
    else:
        single = m & np.isnan(L[1])  # the fit from where there's no doubt which surface it is, if there's enough
        fit_on = single if single.sum() >= 0.5 * m.sum() else m
        a, b, r = robust_fit(d[fit_on], W[fit_on])
        rel = np.abs(r) / W[fit_on]
        floor = np.percentile(W[m], 1) * 0.3  # nothing nearer than a third of the nearest floor
    stats.update(a=float(a), b=float(b), median_err=float(np.median(rel)), p90_err=float(np.percentile(rel, 90)))
    Z = a * d + b
    # before the bend too: a poor fit can put the nearest scenery behind the camera (icedun_2: a 1464, b -322 gave
    # Z < 0, the bend's log made it NaN, its blur spread that over the whole grid, and the NaN extent broke the
    # whole lighting config.toml for every field)
    Z = np.maximum(Z, floor)
    if L.shape[0] > 1 and (~np.isnan(L[1])).any():
        pick = np.nanargmin(np.where(np.isnan(L), np.inf, np.abs(L - Z[None])), axis=0)
        W = np.take_along_axis(L, pick[None], 0)[0]
        stats["layered"] = float((m & ~np.isnan(L[1])).mean())
        stats["not_nearest"] = float((m & (pick > 0)).mean())
    np.save(f.work / "walk_depth.npy", W)
    if smooth > 0:
        Z, wt = anchor(Z, W, smooth, up)
        floor_px = m & (wt > 0.5)
        stats["floor_share"] = float(floor_px.sum() / m.sum())
        if floor_px.any():  # none (bugin1b, bugin3: no walkable surface faces up enough): no floor errors to report
            before = np.abs(a * d + b - W)[floor_px] / W[floor_px]
            after = np.abs(Z - W)[floor_px] / W[floor_px]
            stats.update(floor_err_global=float(np.median(before)), floor_err=float(np.median(after)),
                         floor_p90_err=float(np.percentile(after, 90)))
    stats["clamped"] = float((Z <= floor).mean())
    Z = np.maximum(Z, floor)
    if not np.isfinite(Z).all():
        raise ValueError(f"{f.name}: the depth has {np.mean(~np.isfinite(Z)):.1%} non-finite points")
    (f.work / "depth_fit.json").write_text(json.dumps(stats, indent=1))
    return Z, W, stats
