"""Hunyuan3D-2.1 Paint's multi-view diffusion step, run by mvpaint.py in its own Python (tools/texgen/venv: PyTorch
cu128, diffusers 0.30, transformers 4.57), never the Studio's. Standalone: it imports nothing from ff7bg.

Only the diffusion step of Tencent's texture pipeline (hy3dpaint/utils/multiview_utils.py multiviewDiffusionNet, as
textureGenPipeline calls it): no mesh loading, remeshing, UV unwrapping, RealESRGAN or baking (mvpaint renders the
conditioning and projects the result with the Studio's own code). The model files come from the HF cache under
tools/texgen/models/hf, offline.

  <texgen python> -I mvpaint_texgen.py --hy <Hunyuan3D-2.1/hy3dpaint> --hf-home <models/hf> <job dir> [<job dir> ...]

A job folder holds job.json ({"views": [[elev, azim, weight], ...], "resolution": 512, "seed": 0, "steps": 15,
"guidance": 3.0}), ref.png (the reference picture, RGB on white) and per view normal_NN.png and position_NN.png
(mvpaint.conditioning: MeshRender's render_normal(use_abs_coor=True) and render_position, at any size). Writes
albedo_NN.png and mr_NN.png at the job's resolution, and result.json (seconds, peak VRAM).
"""
import argparse
import json
import os
import random
import sys
import time
import traceback


def load(hy, hf_home):
    os.environ["HF_HOME"] = hf_home
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    sys.path.insert(0, hy)
    # hunyuanpaintpbr/__init__.py imports the training model (unet/model.py: pytorch_lightning, not installed); the
    # package is registered bare so multiview_utils' "from hunyuanpaintpbr.unet.modules import Dino_v2" skips it
    import types
    pkg = types.ModuleType("hunyuanpaintpbr")
    pkg.__path__ = [os.path.join(hy, "hunyuanpaintpbr")]
    sys.modules.setdefault("hunyuanpaintpbr", pkg)
    import torch
    from utils.multiview_utils import multiviewDiffusionNet

    class Config:  # textureGenPipeline.Hunyuan3DPaintConfig's fields that multiviewDiffusionNet reads
        device = "cuda"
        multiview_cfg_path = os.path.join(hy, "cfgs", "hunyuan-paint-pbr.yaml")
        custom_pipeline = "hunyuanpaintpbr"
        multiview_pretrained_path = "tencent/Hunyuan3D-2.1"
        dino_ckpt_path = "facebook/dinov2-giant"

    return torch, multiviewDiffusionNet(Config())


def generate(torch, net, ref, normals, positions, res, seed, steps, guidance):
    """multiviewDiffusionNet.forward_one with a seed and steps of our own (it seeds 0 and takes the scheduler's 15)."""
    import numpy as np
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    ref = ref.resize((res, res))
    conds = [im.resize((res, res)) for im in list(normals) + list(positions)]
    n = len(normals)
    kw = dict(generator=torch.Generator(device=net.pipeline.device).manual_seed(seed), width=res, height=res,
              num_in_batch=n, images_normal=[conds[:n]], images_position=[conds[n:]])
    if getattr(net.pipeline.unet, "use_dino", False):
        kw["dino_hidden_states"] = net.dino_v2(ref)
    out = net.pipeline([ref], num_inference_steps=steps, prompt="high quality", sync_condition=None,
                       guidance_scale=guidance, **kw).images
    return out[:n], out[n:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hy", required=True)
    ap.add_argument("--hf-home", required=True)
    ap.add_argument("jobs", nargs="+")
    a = ap.parse_args(argv)
    from PIL import Image
    t0 = time.time()
    torch, net = load(a.hy, a.hf_home)
    torch.cuda.synchronize()
    load_s = time.time() - t0
    loaded_alloc = torch.cuda.memory_allocated()
    print(f"loaded in {load_s:.1f} s, {loaded_alloc / 2**30:.2f} GB on the GPU", flush=True)
    failed = 0
    for jd in a.jobs:
        try:
            job = json.load(open(os.path.join(jd, "job.json"), encoding="utf-8"))
            n = len(job["views"])
            ref = Image.open(os.path.join(jd, "ref.png")).convert("RGB")
            normals = [Image.open(os.path.join(jd, f"normal_{i:02d}.png")).convert("RGB") for i in range(n)]
            positions = [Image.open(os.path.join(jd, f"position_{i:02d}.png")).convert("RGB") for i in range(n)]
            torch.cuda.reset_peak_memory_stats()
            t = time.time()
            with torch.no_grad():
                albedo, mr = generate(torch, net, ref, normals, positions, int(job.get("resolution", 512)),
                                      int(job.get("seed", 0)), int(job.get("steps", 15)),
                                      float(job.get("guidance", 3.0)))
            torch.cuda.synchronize()
            secs = time.time() - t
            for i, (al, m) in enumerate(zip(albedo, mr)):
                al.save(os.path.join(jd, f"albedo_{i:02d}.png"))
                m.save(os.path.join(jd, f"mr_{i:02d}.png"))
            res = {"ok": True, "seconds": round(secs, 2), "load_seconds": round(load_s, 1),
                   "peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                   "peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2),
                   "weights_gb": round(loaded_alloc / 2**30, 2), "torch": torch.__version__,
                   "device": torch.cuda.get_device_name(0)}
            print(f"{jd}: {n} views in {secs:.1f} s, peak {res['peak_reserved_gb']} GB", flush=True)
        except Exception as e:
            failed += 1
            res = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
            print(res["trace"], file=sys.stderr, flush=True)
        with open(os.path.join(jd, "result.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
