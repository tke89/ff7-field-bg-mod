"""Stage-2 renders for the A/B: the Studio's own workflow (its exact settings for the field's current render, caption
and style images) with the depth lock on, steered either by

  lock  Depth Anything measured on the original, inside the workflow (the Studio's depth lock), or
  game  the game's geometry: the original's depth fitted to the walkmesh, walkable floors set exactly onto it.

Results stay in work/<field>/<variant>/ (render.png + meta.json, as a Studio version would have); the Studio's own
versions are untouched. Sign repainting isn't reproduced (it needs the Studio's sign finder)."""
import json
import sys

import cv2
import numpy as np
from PIL import Image

from . import comfy, common, depth

sys.path.insert(0, str(common.STUDIO))
from ff7bg import ai as sai  # noqa: E402
from ff7bg import comfy as scomfy  # noqa: E402

VARIANTS = {"lock": {"depth_lock": 0.8}, "game": {"depth_lock": 0.8}}
STYLES = common.STUDIO / "studio" / "styles"


def settings(f, variant):
    s = dict(scomfy.DEFAULT_SETTINGS)
    s.update(f.meta.get("settings") or {})
    s.update(VARIANTS[variant])
    if f.meta.get("scene"):
        s["scene"] = f.meta["scene"]
    return s


def game_depth_image(f, padded, pad, out):
    """Near-white depth (as the depth adapters expect) of the padded original, from the game's geometry."""
    l, t = pad[0], pad[1]
    Image.fromarray(padded).save(out.with_name("original_padded.png"))
    d = depth.da3_image(out.with_name("original_padded.png"), out.with_name("original_da3.npy"),
                        f"{f.name}_orig_pad.png", step=1)
    H, W = d.shape
    ys, xs = np.mgrid[0:H, 0:W].astype(float)
    Wk = depth.walkmesh_depth_at(f, xs + 0.5 - l + f.mx, ys + 0.5 - t + f.my)
    m = ~np.isnan(Wk)
    a, b, _ = depth.robust_fit(d[m], Wk[m])
    Z = np.maximum(a * d + b, np.percentile(Wk[m], 1) * 0.3)
    close = m & (np.abs(np.where(m, Wk, 0) - Z) < 0.06 * Z)  # walkable floor seen by both: take the walkmesh's
    Z = np.where(close, Wk, Z)
    disp = 1 / Z
    lo, hi = np.percentile(disp, 1), np.percentile(disp, 99.5)
    img = (np.clip((disp - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    Image.fromarray(np.dstack([img] * 3)).save(out)
    return out


def run(name, variant, log=print):
    f = common.Field(name)
    vdir = f.work / variant
    vdir.mkdir(exist_ok=True)
    if (vdir / "render.png").exists():
        return vdir
    if not scomfy.FAMILIES.get(f.meta.get("family"), {}).get("edit"):
        raise ValueError(f"{name}: its render isn't from an edit model ({f.meta.get('family')})")
    s = settings(f, variant)
    jobs = json.loads((common.STUDIO / "ai" / "jobs.json").read_text())
    w, h = jobs[name]["canvas"][2], jobs[name]["canvas"][3]
    pad = scomfy.edit_padding(w, h, s["edit_margin"])
    src = np.asarray(Image.open(common.STUDIO / "ai" / "input" / f"{name}.png").convert("RGB"))
    padded = sai._pad(src, *pad)
    Image.fromarray(padded).save(vdir / "input.png")
    inp = comfy.upload(vdir / "input.png", f"{name}_{variant}_in.png")
    refs = [comfy.upload(STYLES / r, r) for r in s["style_refs"] if (STYLES / r).exists()]
    wf = scomfy.build_workflow(s, inp, w, h, f"ff7_3d/{name}_{variant}", refs, pad)
    if variant == "game":
        guide = game_depth_image(f, padded, pad, vdir / "guide.png")
        ww, wh = scomfy.work_size(s, w + pad[0] + pad[2], h + pad[1] + pad[3])
        wf["gdepth"] = {"class_type": "LoadImage", "inputs": {"image": comfy.upload(guide, f"{name}_guide.png")}}
        wf["gdepth_s"] = {"class_type": "ImageScale", "inputs": {"image": ["gdepth", 0], "upscale_method": "lanczos",
                                                                 "width": ww, "height": wh, "crop": "disabled"}}
        wf["ctl_apply"]["inputs"]["image"] = ["gdepth_s", 0]
    out = comfy.run(wf)
    img = Image.open(__import__("io").BytesIO(comfy.fetch(comfy.files(out, "save")[0]))).convert("RGB")
    kl, kt, kr, kb = scomfy.edit_keep(pad)
    want = ((w + kl + kr) * s["out_scale"], (h + kt + kb) * s["out_scale"])
    if img.size != want:
        img = img.resize(want, Image.LANCZOS)
    img.save(vdir / "render.png")
    meta = {"family": s["family"], "variant": variant, "frame": [kl, kt, kr, kb], "result": {"scale": s["out_scale"]},
            "settings": {k: v for k, v in s.items() if isinstance(v, (int, float, str, bool, list))}}
    (vdir / "meta.json").write_text(json.dumps(meta, indent=1))
    log(f"{name} {variant}: render {img.size}")
    return vdir
