"""Marigold v2 through ComfyUI: de-lit colour (albedo) and surface normals of a field's render, cached in
work/<field>/mg_<task>.png."""
import numpy as np
from PIL import Image

from . import comfy

TASKS = {  # prediction: (lora, vae, conditioning)
    "albedo": ("marigold_v2_albedo", "marigold_v2_albedo_vae", "marigold_v2_albedo_conditioning"),
    "normals": ("marigold_v2_normals", "marigold_v2_normals_vae", "marigold_v2_normals_conditioning"),
    "depth": ("marigold_v2_depth_log_stage2", "marigold_v2_depth_log_stage2_vae", "marigold_v2_depth_conditioning"),
}


def workflow(image, task, prefix):
    lora, vae, cond = TASKS[task]
    return {
        "img": {"class_type": "LoadImage", "inputs": {"image": image}},
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2509_int8_convrot.safetensors",
                                                         "weight_dtype": "default"}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": lora + ".safetensors",
                                                                  "strength_model": 1.0}},
        "samp": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["lora", 0], "shift": 1.73,
                                                                    "sampling": "img_to_img_velocity"}},
        "cond": {"class_type": "ConditioningLoader", "inputs": {"conditioning_name": cond + ".safetensors"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae + ".safetensors"}},
        "guide": {"class_type": "BasicGuider", "inputs": {"model": ["samp", 0], "conditioning": ["cond", 0]}},
        "noise": {"class_type": "DisableNoise", "inputs": {}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sigmas": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.5, 0"}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["vae", 0]}},
        "run": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["guide", 0],
                                                                  "sampler": ["sampler", 0], "sigmas": ["sigmas", 0],
                                                                  "latent_image": ["enc", 0]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["run", 0], "vae": ["vae", 0]}},
        "post": {"class_type": "MarigoldV2PostProcess", "inputs": {"image": ["dec", 0], "prediction": task}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["post", 0], "filename_prefix": prefix}},
    }


def run(f, tasks=("albedo", "normals")):
    """Paths of the outputs, computing the missing ones. Renders whose sides aren't multiples of 16 are padded for
    the model and cropped back."""
    todo = [t for t in tasks if not (f.work / f"mg_{t}.png").exists()]
    if todo:
        src = Image.open(f.render).convert("RGB")
        w, h = src.size
        pw, ph = -(-w // 16) * 16, -(-h // 16) * 16
        if (pw, ph) != (w, h):
            canvas = Image.new("RGB", (pw, ph))
            canvas.paste(src, (0, 0))
            a = np.asarray(canvas).copy()
            a[h:, :w] = a[h - 1:h, :w]
            a[:, w:] = a[:, w - 1:w]
            src = Image.fromarray(a)
        tmp = f.work / "_marigold_in.png"
        src.save(tmp)
        name = comfy.upload(tmp, f"{f.name}_mg_in.png")
        for t in todo:
            out = comfy.run(workflow(name, t, f"ff7_3d/{f.name}_mg_{t}"))
            img = Image.open(__import__("io").BytesIO(comfy.fetch(comfy.files(out, "save")[0]))).convert("RGB")
            img.crop((0, 0, w, h)).save(f.work / f"mg_{t}.png")
        tmp.unlink(missing_ok=True)
    return {t: f.work / f"mg_{t}.png" for t in tasks}
