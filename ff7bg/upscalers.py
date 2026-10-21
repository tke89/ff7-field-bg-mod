"""Upscalers that run outside ComfyUI: ImageMagick's Lanczos resize and Upscayl.

- "magick": a 3-lobe Lanczos resize with ImageMagick (`magick ... -filter Lanczos -resize`). Without
  ImageMagick installed, Pillow's Lanczos filter does it: the same filter, within rounding.
- "upscayl:<model>": Upscayl's upscayl-bin (Real-ESRGAN-type models on the GPU through Vulkan) with one
  of its models, e.g. "upscayl:remacri-4x". Found in an Upscayl install, or in tools/upscayl
  (upscayl-bin.exe with a models folder next to it).

ComfyUI can't run outside programs, so a workflow using one of these returns its image at the size it
rendered it and the Studio enlarges that afterwards (finish). The Upscale workflow doesn't need ComfyUI
at all with them.
"""
import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

MAGICK = "magick"
UPSCAYL = "upscayl:"
ROOT = Path(__file__).resolve().parent.parent
EXE = ".exe" if os.name == "nt" else ""
TIMEOUT = 900  # seconds


class UpscalerError(RuntimeError):
    pass


def is_local(name):
    """Whether this upscaler runs outside ComfyUI."""
    return bool(name) and (name == MAGICK or name.startswith(UPSCAYL))


def find_magick():
    exe = shutil.which("magick")
    if exe:
        return exe
    for pf in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")}:
        hits = sorted(glob.glob(os.path.join(pf, "ImageMagick-*", "magick.exe"))) if pf else []
        if hits:
            return hits[-1]
    return None


def find_upscayl():
    """(upscayl-bin, models folder), or None."""
    places = [(ROOT / "tools" / "upscayl" / f"upscayl-bin{EXE}", ROOT / "tools" / "upscayl" / "models")]
    for base in (Path(os.environ.get("LOCALAPPDATA", "~")) / "Programs" / "Upscayl",
                 Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Upscayl"):
        # the app keeps them in resources/ (electron/utils/get-resource-paths.ts)
        places.append((base / "resources" / "bin" / f"upscayl-bin{EXE}", base / "resources" / "models"))
    on_path = shutil.which("upscayl-bin")
    if on_path:
        places.append((Path(on_path), Path(on_path).parent / "models"))
    for exe, models in places:
        if exe.is_file() and models.is_dir():
            return exe, models
    return None


def upscayl_models():
    found = find_upscayl()
    if not found:
        return []
    return sorted(p.stem for p in found[1].glob("*.param") if p.with_suffix(".bin").is_file())


def available():
    """For the Studio's upscaler list."""
    up = find_upscayl()
    return {"magick": find_magick(), "upscayl": str(up[0]) if up else None, "upscayl_models": upscayl_models()}


def seconds(name, in_px, out_px):
    """Rough time for one image (pixel counts in and out); not measured on Upscayl yet."""
    if name == MAGICK:
        return 0.4 + 0.08 * out_px / 1e6
    return 1.5 + 0.15 * out_px / 1e6 + 0.1 * 16 * in_px / 1e6  # 4x models: the GPU works on 16x the input


def upscale(img, name, w, h, cancelled=lambda: False):
    """img (RGB uint8 array) enlarged by the upscaler `name` to exactly w x h."""
    src = Image.fromarray(np.ascontiguousarray(img))
    if name == MAGICK:
        exe = find_magick()
        if not exe:
            out = src.resize((w, h), Image.LANCZOS)
        else:
            out = _run([exe, "{in}", "-filter", "Lanczos", "-resize", f"{w}x{h}!", "-depth", "8",
                        "-define", "png:compression-level=1", "{out}"], src, cancelled)
    elif name.startswith(UPSCAYL):
        found = find_upscayl()
        if not found:
            raise UpscalerError("Upscayl isn't installed: install it, or put upscayl-bin and its models "
                                "folder in tools/upscayl")
        exe, models = found
        model = name[len(UPSCAYL):]
        if not (models / f"{model}.param").is_file():
            raise UpscalerError(f"Upscayl has no model \"{model}\" in {models}")
        # the model's own scale (all of Upscayl's are 4x); the exact size comes from the resize below
        out = _run([str(exe), "-i", "{in}", "-o", "{out}", "-m", str(models), "-n", model, "-f", "png"],
                   src, cancelled)
    else:
        raise UpscalerError(f"unknown upscaler \"{name}\"")
    if out.size != (w, h):
        out = out.resize((w, h), Image.LANCZOS)
    return np.asarray(out.convert("RGB"))


def finish(img, s, w, h, cancelled=lambda: False):
    """A ComfyUI result enlarged to w x h by the outside upscaler in settings s. Unchanged when the
    upscaler runs inside ComfyUI, or when the graph already made it that size (the detail pass)."""
    if not is_local(s["upscaler"]) or img.shape[:2] == (h, w):
        return img
    return upscale(img, s["upscaler"], w, h, cancelled)


def _run(cmd, src, cancelled):
    """Run a tool on src ("{in}"/"{out}" in cmd stand for the files); returns the output image."""
    with tempfile.TemporaryDirectory(prefix="ff7up_", ignore_cleanup_errors=True) as d:
        inp, outp, logp = (os.path.join(d, n) for n in ("in.png", "out.png", "log.txt"))
        src.save(inp, compress_level=1)
        args = [inp if a == "{in}" else outp if a == "{out}" else a for a in cmd]
        with open(logp, "w+", encoding="utf-8", errors="replace") as log:
            # output goes to a file: a pipe nobody reads can fill up and stall the tool
            try:
                proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                raise UpscalerError(f"couldn't start {cmd[0]}: {e}") from e
            waited = 0.0
            while True:
                try:
                    proc.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    waited += 0.25
                    if cancelled() or waited > TIMEOUT:
                        proc.kill()
                        proc.wait()
                        raise InterruptedError("cancelled") if cancelled() else UpscalerError(
                            f"{Path(cmd[0]).stem} took over {TIMEOUT} s")
            log.seek(0)
            tail = " ".join(log.read().split()[-40:])
        if proc.returncode != 0 or not os.path.isfile(outp):
            raise UpscalerError(f"{Path(cmd[0]).stem} failed (exit code {proc.returncode}): {tail}")
        with Image.open(outp) as im:
            return im.convert("RGB")
