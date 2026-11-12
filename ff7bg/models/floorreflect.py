"""Battle floors that mirror the battle and grow grass: the Studio's Floor card on battle scenes (user, 2026-11-07: "the
puddles should look wet, the grass should have blades that move. Metallic floors should be somewhat reflective"; the
engine: FFNx-3d `reflect` and `grass`, ff7-3d/dev/battle-engine/FORMAT.md sections G and H).

Per battle scene, each floor texture (a scene texture with polygons lying on the ground, y = 0) gets one of MODES:
off, puddles (water where its colour is grey-blue against grass and earth), water (all of it), metal (a soft, blurred
sheen) or polished (a sharp mirror), and a strength. Only its polygons that face up reflect, whatever the mode. And
one of GRASS: off, short or medium blades, where its colour is grass (green, not water), bending in the wind.

Install (the user's click only) puts in, for the scene:
- [floor_STAGE<nn>] in lighting/config.toml: reflect = [...] and grass = [...] (its own section, apart from the battle
  look's [battle_STAGE<nn>]; under the config lock, battlelight.edit_config);
- for puddles: <external_mesh_path>/battle/<texture>_reflect.dds, the mask (red: where), at most MASK_MAX across;
- for grass: <external_mesh_path>/battle/<texture>_grass.dds, likewise.
Uninstall takes out exactly that (a file changed since is left). Install refuses, writing nothing, where a file or the
section is someone else's.

Files: work/models/floor/<scene>/settings.json (the card's choices), work/models/floor/installed.json (the manifest).
"""
import hashlib
import io
import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import battle
from . import battlelight as BL

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "work" / "models" / "floor"
MANIFEST = WORK / "installed.json"
MASK_MAX = 1024        # a mask's longest side (the engine samples it with the texture's own UVs)
GROUND = 3.0           # a polygon lies on the ground when all its corners are within this of y = 0 (battle units)

# what each mode does: water or metal, the blur (pixels at 720 lines), whether it needs a mask, the strength it starts at
MODES = {
    "off": None,
    "puddles": {"water": 1.0, "blur": 0.0, "mask": True, "amount": 1.0},
    "water": {"water": 1.0, "blur": 0.0, "mask": False, "amount": 1.0},
    "metal": {"water": 0.0, "blur": 4.0, "mask": False, "amount": 0.5},
    "polished": {"water": 0.0, "blur": 0.0, "mask": False, "amount": 0.5},
}
AMOUNTS = (0.25, 0.5, 0.75, 1.0)
GRASS = {"off": 0.0, "short": 26.0, "medium": 36.0}  # blade heights in battle units (Cloud is about 950 tall)

_lock = threading.RLock()
_floor_cache = {}  # scene id -> floors()


def _hash(p):
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(p)


# --- the scene's floor --------------------------------------------------------------------------------------------------

def floors(id):
    """The scene's textures with polygons on the ground: [{"name": "STAGE08_T00", "file": "STAGE08_T00_00", "index",
    "share": its part of the ground's area, "tris": its ground triangles' UVs (n, 3, 2)}], the most ground first."""
    id = id.lower()
    if id in _floor_cache:
        return _floor_cache[id]
    s = battle.load_scene(id)
    p = s.pos
    flat = np.abs(p[..., 1]).max(1) < GROUND
    area = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    total = float(area[flat].sum()) or 1.0
    out = []
    for i, ref in enumerate(s.textures):
        m = flat & (s.tex == i)
        if not m.any():
            continue
        file = ref.name.split("/", 1)[-1]  # STAGE08_T00_00
        out.append({"name": file.rsplit("_", 1)[0], "file": file, "index": i,
                    "share": float(area[m].sum()) / total, "tris": s.uv[m].astype(np.float32)})
    out.sort(key=lambda f: -f["share"])
    _floor_cache[id] = out
    return out


def stage(id):
    """The scene's battle number nn (STAGE<nn>), from its textures' names; None when they don't say."""
    for ref in battle.load_scene(id).textures:
        n = ref.name.split("/", 1)[-1]
        if n[:5].upper() == "STAGE" and n[5:7].isdigit():
            return int(n[5:7])
    return None


def texture_image(id, f):
    """(RGBA array, source) of a floor texture as the game shows it: the painted override in the game when there is one
    ("painting"), else the game's own ("original")."""
    d = BL.texture_installed(f["file"])
    if d is not None:
        return np.asarray(Image.open(d / f"{f['file']}.png").convert("RGBA")), "painting"
    ref = battle.load_scene(id).textures[f["index"]]
    return np.asarray(ref.tex.rgba(ref.palette)), "original"


def _texture_hash(f):
    d = BL.texture_installed(f["file"])
    return _hash(d / f"{f['file']}.png") if d is not None else "original"


def water_mask(rgba):
    """0..1 per texel: water (grey-blue, unsaturated) against grass (olive, saturated) and earth; soft-edged. Tuned on
    Midgar Zolom's marsh (floor_mock, 2026-11-07); the card's preview shows what it found."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgba[..., :3]), cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    grassy = np.clip((s - 60) / 40, 0, 1) * ((h > 18) & (h < 55))
    water = (1 - grassy) * np.clip((v - 35) / 30, 0, 1) * ((h > 70) | (s < 70))
    water = cv2.GaussianBlur(water, (0, 0), max(0.5, rgba.shape[1] / 600))
    return np.clip((water - 0.35) / 0.3, 0, 1)


def grass_mask(rgba):
    """0..1 per texel: grass (olive to green, saturated, not too dark) and not water; soft-edged. Where blades grow."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgba[..., :3]), cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    green = np.clip((s - 45) / 35, 0, 1) * np.clip((v - 30) / 25, 0, 1) * ((h > 20) & (h < 75))
    green = cv2.GaussianBlur(green, (0, 0), max(0.5, rgba.shape[1] / 800))
    return np.clip((green - 0.3) / 0.4, 0, 1) * (1 - water_mask(rgba))


def coverage(f, h, w):
    """Where the texture's ground polygons are, as a (h, w) bool: their UVs wrapped into the texture (it repeats)."""
    cov = np.zeros((h, w), np.uint8)
    for t in f["tris"]:
        t = t - np.floor(t.min(0))  # into 0..1 (the texture repeats)
        for du in (0.0, -1.0):
            for dv in (0.0, -1.0):
                q = np.round((t + (du, dv)) * (w, h) * 4).astype(np.int32)  # 2 bits of sub-pixel precision
                cv2.fillPoly(cov, [q], 1, cv2.LINE_8, 2)
    return cov.astype(bool)


# --- the card's choices -------------------------------------------------------------------------------------------------

def _clean(s):
    """One texture's choices, made valid: {"mode", "amount", "grass"}."""
    s = s if isinstance(s, dict) else {}
    mode = s.get("mode") if s.get("mode") in MODES else "off"
    amount = s.get("amount")
    if not isinstance(amount, (int, float)) or not 0 < amount <= 1:
        amount = (MODES[mode] or {}).get("amount", 1.0)
    grass = s.get("grass") if s.get("grass") in GRASS else "off"
    return {"mode": mode, "amount": float(amount), "grass": grass}


def settings(id):
    """{texture name: {"mode", "amount", "grass"}} for each floor texture (off where not chosen)."""
    saved = _read_json(WORK / id.lower() / "settings.json", {}).get("textures", {})
    return {f["name"]: _clean(saved.get(f["name"])) for f in floors(id)}


def save_settings(id, textures):
    """Keep the card's choices for the scene's floor textures (unknown names dropped, unknown values off)."""
    names = {f["name"] for f in floors(id)}
    clean = {n: _clean(s) for n, s in (textures or {}).items() if n in names}
    _write_json(WORK / id.lower() / "settings.json", {"textures": clean, "at": time.time()})
    return settings(id)


# --- the preview --------------------------------------------------------------------------------------------------------

TINT = {"water": (40, 120, 255), "metal": (190, 110, 255), "grass": (70, 235, 60)}


def preview(id, choice=None, side=360):
    """JPEG bytes: each floor texture as the game shows it, the parts not on the ground dimmed, what reflects tinted
    (water blue, metal purple) and where grass grows (green), labelled. choice: {name: {"mode", "grass"}} (default: the
    saved settings)."""
    choice = choice or settings(id)
    tiles = []
    for f in floors(id):
        img, _ = texture_image(id, f)
        h, w = img.shape[:2]
        k = side / max(h, w)
        th, tw = max(1, round(h * k)), max(1, round(w * k))
        rgb = cv2.resize(np.ascontiguousarray(img[..., :3]), (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32)
        cov = coverage(f, th, tw)
        mode = (choice.get(f["name"]) or {}).get("mode", "off")
        grass = (choice.get(f["name"]) or {}).get("grass", "off")
        small = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        m = MODES.get(mode)
        if m:
            where = water_mask(small) if m["mask"] else np.ones((th, tw), np.float32)
            a = (where * cov * 0.55)[..., None]
            rgb = rgb * (1 - a) + np.array(TINT["water" if m["water"] > 0.5 else "metal"], np.float32) * a
        if GRASS.get(grass):
            a = (grass_mask(small) * cov * 0.5)[..., None]
            rgb = rgb * (1 - a) + np.array(TINT["grass"], np.float32) * a
        rgb[~cov] *= 0.3
        tile = np.full((th + 34, max(tw, 200), 3), 24, np.uint8)
        tile[34:34 + th, :tw] = np.clip(rgb, 0, 255).astype(np.uint8)
        label = f"{f['name']}  {mode}" + (f" + {grass} grass" if GRASS.get(grass) else "")
        cv2.putText(tile, label, (6, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
        tiles.append(tile)
    if not tiles:
        tiles = [np.full((60, 300, 3), 24, np.uint8)]
    H = max(t.shape[0] for t in tiles)
    row = np.concatenate([np.pad(t, ((0, H - t.shape[0]), (0, 8), (0, 0)), constant_values=24) for t in tiles], axis=1)
    buf = io.BytesIO()
    Image.fromarray(row).save(buf, "JPEG", quality=88)
    return buf.getvalue()


# --- install --------------------------------------------------------------------------------------------------------------

def installed():
    return _read_json(MANIFEST, {}) or {}


def section_name(id):
    nn = stage(id)
    if nn is None:
        raise FileNotFoundError(f"{id}: its textures don't say which battle scene it is (STAGE<nn>)")
    return f"floor_STAGE{nn:02d}"


def chosen(id):
    """The floor textures that reflect or grow grass, as chosen: {name: {"mode", "amount", "grass"}} (what Install
    would put in)."""
    return {n: c for n, c in settings(id).items() if MODES.get(c["mode"]) or GRASS.get(c["grass"])}


def plan(id):
    """What Install would put in: ({"reflect": [entries], "grass": [entries]}, [(file name, mask float array)],
    {texture: source hash})."""
    values, masks, sources = {"reflect": [], "grass": []}, [], {}
    picks = settings(id)
    for f in floors(id):
        c = picks[f["name"]]
        m, height = MODES.get(c["mode"]), GRASS.get(c["grass"], 0.0)
        if not m and not height:
            continue
        small = None
        if (m and m["mask"]) or height:
            img, _ = texture_image(id, f)
            h, w = img.shape[:2]
            k = min(1.0, MASK_MAX / max(h, w))
            small = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else img
            sources[f["name"]] = _texture_hash(f)
        if m:
            e = {"texture": f["name"], "amount": round(c["amount"], 3), "water": m["water"]}
            if m["water"] < 0.5:
                e["blur"] = m["blur"]
            values["reflect"].append(e)
            if m["mask"]:
                masks.append((f"{f['name']}_reflect.dds", water_mask(small)))
        if height:
            values["grass"].append({"texture": f["name"], "height": height})
            masks.append((f"{f['name']}_grass.dds", grass_mask(small)))
    return {k: v for k, v in values.items() if v}, masks, sources


def install(id):
    """Put the scene's floor choices in the game (see the module's text). Returns the manifest entry."""
    id = id.lower()
    values, masks, sources = plan(id)
    if not values:
        raise ValueError("Pick how at least one floor texture reflects, or where grass grows, first.")
    sec = section_name(id)
    folder = BL.mesh_dir() / "battle"
    targets = [(name, folder / name) for name, _ in masks]
    with _lock:
        data = installed()
        if id in data:
            uninstall(id)
            data = installed()
        ours = {f["path"].lower() for e in data.values() for f in e["files"]}
        clash = [str(p) for _, p in targets if p.exists() and str(p).lower() not in ours]
        if sec in BL.config_sections() and not any(e.get("section") == sec for e in data.values()):
            clash.append(f"[{sec}]")
        if clash:
            raise FileExistsError("not ours, left alone: " + ", ".join(clash))
        gltf = BL._ff3d("gltfout")
        files, made = [], []
        try:
            for (name, mask), (_, dst) in zip(masks, targets):
                if not dst.parent.exists():
                    for d in reversed([dst.parent, *dst.parent.parents]):
                        if not d.exists():
                            d.mkdir()
                            made.append(str(d))
                v = np.round(np.clip(mask, 0, 1) * 255).astype(np.uint8)
                gltf.write_dds(dst, np.dstack([v, v, v, np.full_like(v, 255)]))
                files.append({"path": str(dst), "sha1": _hash(dst)})
            BL.edit_config({sec: values})
        except Exception:
            for f in files:
                Path(f["path"]).unlink(missing_ok=True)
            for d in reversed(made):
                try:
                    Path(d).rmdir()
                except OSError:
                    pass
            raise
        entry = {"at": time.time(), "section": sec, "values": values, "files": files, "folders": made,
                 "sources": sources, "choices": chosen(id)}
        data[id] = entry
        _write_json(MANIFEST, data)
        return entry


def uninstall(id):
    """Take a scene's floor out of the game: exactly the files Install wrote (left where something changed them since)
    and its section. Returns (files removed, files left, section removed or None)."""
    id = id.lower()
    with _lock:
        data = installed()
        entry = data.get(id)
        if not entry:
            return 0, [], None
        removed, left = 0, []
        for f in entry["files"]:
            p = Path(f["path"])
            if not p.exists():
                continue
            if _hash(p) != f.get("sha1"):
                left.append(str(p))
                continue
            p.unlink()
            removed += 1
        for d in reversed(entry.get("folders", [])):
            try:
                Path(d).rmdir()
            except OSError:
                pass
        sec = entry.get("section")
        gone = None
        if sec and sec in BL.config_sections():
            BL.edit_config(remove=[sec])
            gone = sec
        data.pop(id)
        _write_json(MANIFEST, data)
        return removed, left, gone


def stale(id):
    """The installed masks (puddles, grass) whose floor texture in the game changed since (a new painting installed):
    the textures' names."""
    entry = installed().get(id.lower())
    if not entry:
        return []
    by = {f["name"]: f for f in floors(id)}
    return sorted(n for n, h in (entry.get("sources") or {}).items() if n in by and _texture_hash(by[n]) != h)


if __name__ == "__main__":
    import sys
    cmd, sid = sys.argv[1], sys.argv[2]
    if cmd == "floors":
        for f in floors(sid):
            print(f["name"], f"{f['share']:.2f}", len(f["tris"]), "triangles")
    elif cmd == "preview":
        Path(sys.argv[3]).write_bytes(preview(sid))
    elif cmd == "install":
        print(json.dumps(install(sid), indent=1))
    elif cmd == "uninstall":
        print(uninstall(sid))
