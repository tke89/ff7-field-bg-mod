"""Per-field lighting: image-based ambient light tinted from the field's own render, and the field's section in
FFNx's lighting/config.toml (shadow map sized to the scene; the sun from atmos.sun, or FFNx's default)."""
import contextlib
import os
import re
import time
import tomllib

import cv2
import numpy as np

from . import common, envmap

DEFAULT_SUN = (60.0, 60.0)  # FFNx's light_rotation_vertical / _horizontal defaults


def sun_direction(vertical, horizontal):
    """Toward the light in field world coordinates (z up), as FFNx turns its two rotation settings into one."""
    v, h = np.radians(vertical), np.radians(horizontal)
    return np.array([np.cos(v) * np.sin(h), -np.cos(v) * np.cos(h), np.sin(v)])


def tint(rgb, target_luma):
    rgb = np.asarray(rgb, float) / 255
    rgb = np.clip(rgb, 1e-3, None) ** 2.2  # to linear
    return rgb * target_luma / max(rgb @ [0.2126, 0.7152, 0.0722], 1e-4)


# daylight's brightness overhead, at the horizon and from the ground, without its colour (the painted look: the
# painting has its light's colour already)
GREY = {"zenith": (0.8,) * 3, "horizon": (0.95,) * 3, "ground": (0.35,) * 3}


CHAR_SAT_MAX = 0.25  # the characters' light: how colourful it may get ((max - min) / max, linear)


def cap_saturation(rgb, most):
    """`rgb` (linear) moved toward its own grey until (max - min) / max is at most `most`, brightness kept."""
    rgb = np.asarray(rgb, float)
    hi, lo = float(rgb.max()), float(rgb.min())
    if hi <= 0 or (hi - lo) / hi <= most:
        return rgb
    grey = float(rgb @ [0.2126, 0.7152, 0.0722])
    # the share t of the way from grey that leaves (max - min) / max = most
    t = most * grey / max((hi - lo) - most * (hi - grey), 1e-6)
    return grey + np.clip(t, 0.0, 1.0) * (rgb - grey)


def write(f, render, W, amount=0.5, look="relit", sun=DEFAULT_SUN, suffix=""):
    """Sky from the top of the picture, ground from what's under the walkmesh, `amount` of the way from colourless
    (painted) or daylight (relit) to the scene's own colours; brighter toward the sun (light_rotation_* degrees).
    suffix "_char": the characters' own light (FFNx-3d), the scene keeping the one without; its colours capped at
    CHAR_SAT_MAX, so a strongly coloured scene doesn't dye them (hill's green grass, the Honey Bee room's magenta:
    user, 2026-11-03: "cap it")."""
    h = render.shape[0]
    top = render[: h // 5].reshape(-1, 3)
    top = top[top.sum(1) >= np.percentile(top.sum(1), 60)].mean(0)
    walk = cv2.resize((~np.isnan(W)).astype(np.uint8), (render.shape[1], h), interpolation=cv2.INTER_NEAREST) > 0
    ground = render[walk].mean(0) if walk.any() else render.reshape(-1, 3).mean(0)
    base = GREY if look == "painted" else envmap.NEUTRAL
    mix = lambda c, k, luma: amount * tint(c, luma) + (1 - amount) * np.array(base[k])
    colours = {"zenith": mix(top, "zenith", 0.8), "horizon": mix(render.reshape(-1, 3).mean(0), "horizon", 0.95),
               "ground": mix(ground, "ground", 0.35)}
    if suffix == "_char":
        colours = {k: cap_saturation(v, CHAR_SAT_MAX) for k, v in colours.items()}
    envmap.make(f.id, sun_direction(*sun), {k: tuple(v) for k, v in colours.items()}, suffix)


def lighting_section(f, extent, extra=None):
    """Put the field's [field_<name>] section in lighting/config.toml: a shadow map covering the scene, plus `extra`
    settings; the rest comes from [fields] (the game-wide 3D settings: the characters' shadows on the walkmesh, where
    the game stands them, and the rest of build.GAMEWIDE), then FFNx's defaults."""
    write_section(f"field_{f.name}", {"shadowmap_area": f"{extent * 0.6:.0f}.0",
                                      "shadowmap_near_far_size": f"{extent * 1.5:.0f}.0", **(extra or {})})


def write_section(table, values):
    """Put a [table] in lighting/config.toml with these keys, in place of the one there (FFNx-3d hot-reloads it)."""
    cfg = common.LIGHTING / "config.toml"
    with config_lock():
        text = cfg.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        body = nl.join([f"[{table}]"] + [f"{k} = {v}" for k, v in values.items()])
        # the section: its header and every line up to the next table header (values like haze_color = [..] have
        # "[" too)
        pattern = re.compile(rf"^\[{re.escape(table)}\][^\r\n]*(?:\r?\n(?!\[)[^\r\n]*)*\r?\n?", re.M)
        if pattern.search(text):
            new = pattern.sub(lambda m: body + nl + nl, text)
        else:
            new = text.rstrip() + nl + nl + body + nl
        if new != text:
            # FFNx-3d reads nothing at all from a file that doesn't parse (every field loses its settings): never
            # write one (icedun_2's "nan.0", 2026-11-02)
            tomllib.loads(new)
            cfg.write_bytes(new.encode("utf-8"))


@contextlib.contextmanager
def config_lock(stale=60):
    """lighting/config.toml is read, changed and written whole by every look step: one writer at a time (builds, look
    re-applies and scripts all take this; a lock older than `stale` seconds was left by a killed process)."""
    lock = common.LIGHTING / "config.toml.lock"
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
