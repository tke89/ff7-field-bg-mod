"""What a workflow needs that ComfyUI (or this PC) doesn't have, and where to get it.

needs() builds the workflows a field's settings would run (the main render, the states pass, the scene
description) and lists the model files their loader nodes name, and every node they use. missing() checks
those against ComfyUI's own lists, so the answer follows the settings exactly: the style-image files only count
with style images picked, the depth model only with a depth lock on, and so on.
"""
import threading
import time

import requests

from . import upscalers
from .comfy import FAMILIES, build_caption_workflow, build_states_workflow, build_workflow, edit_padding

HF = "https://huggingface.co"

# loader node -> its inputs that name a file, and the models folder ComfyUI reads them from
LOADERS = {
    "UNETLoader": [("unet_name", "diffusion_models")],
    "CLIPLoader": [("clip_name", "text_encoders")],
    "DualCLIPLoader": [("clip_name1", "text_encoders"), ("clip_name2", "text_encoders")],
    "VAELoader": [("vae_name", "vae")],
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoints")],
    "ControlNetLoader": [("control_net_name", "controlnet")],
    "LoraLoaderModelOnly": [("lora_name", "loras")],
    "Krea2ControlLoRALoader": [("lora_name", "loras")],
    "UpscaleModelLoader": [("model_name", "upscale_models")],
    "ModelPatchLoader": [("name", "model_patches")],
    "CLIPVisionLoader": [("clip_name", "clip_vision")],
    "LoadDA3Model": [("model_name", "geometry_estimation")],
}


def _hf(repo, path):
    return f"{HF}/{repo}/resolve/main/{path}"


FLUX3_REPO = f"{HF}/black-forest-labs/flux-3-image-klein-comfy"
FLUX3_NOTE = "gated: sign in to Hugging Face and accept the license on the model page first, then download it from Files"

# file -> (download link, size in GB, what it's for, note)
FILES = {
    # Qwen-Image 2.1
    "qwen_image_2.1_turbo_int8_convrot.safetensors": (_hf("Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors"), 7.26, "Qwen 2.1 Turbo", ""),
    "qwen_image_2.1_int8_convrot.safetensors": (_hf("Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors"), 7.26, "Qwen 2.1 Full", ""),
    "qwen3vl_8b_int8_convrot.safetensors": (_hf("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_int8_convrot.safetensors"), 9.35, "Qwen 2.1's text encoder", ""),
    "qwen_image_2.1_vae_bf16.safetensors": (_hf("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors"), 0.68, "Qwen 2.1's VAE", ""),
    "qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors": (_hf("Comfy-Org/Qwen-Image-2.1", "model_patches/qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors"), 3.78, "Qwen 2.1's depth lock", ""),
    # FLUX 3 Klein (gated)
    "flux3-image-klein-int8-convrot.safetensors": (FLUX3_REPO, 5.83, "FLUX 3 Distilled", FLUX3_NOTE),
    "flux3-image-klein-base-int8-convrot.safetensors": (FLUX3_REPO, 5.81, "FLUX 3 Base", FLUX3_NOTE),
    "qwen3vl_4b_bf16.safetensors": (_hf("Comfy-Org/Mage-Flow", "text_encoders/qwen3vl_4b_bf16.safetensors"), 8.88, "the text encoder of FLUX 3, Mage-Flow and FIBO 2", ""),
    "flux2-vae.safetensors": (_hf("Comfy-Org/flux2-dev", "split_files/vae/flux2-vae.safetensors"), 0.34, "the VAE of FLUX 3, FIBO 2 and MOSS", ""),
    # Mage-Flow
    "mage_flow_edit_turbo_int8_convrot.safetensors": (_hf("Comfy-Org/Mage-Flow", "diffusion_models/mage_flow_edit_turbo_int8_convrot.safetensors"), 4.16, "Mage-Flow Turbo", ""),
    "mage_flow_edit_int8_convrot.safetensors": (_hf("Comfy-Org/Mage-Flow", "diffusion_models/mage_flow_edit_int8_convrot.safetensors"), 4.16, "Mage-Flow Full", ""),
    "mage_flow_vae_bf16.safetensors": (_hf("Comfy-Org/Mage-Flow", "vae/mage_flow_vae_bf16.safetensors"), 0.35, "Mage-Flow's VAE", ""),
    # FIBO 2
    "fibo2_edit_turbo_int8_convrot.safetensors": (_hf("Comfy-Org/Fibo-2.0", "diffusion_models/fibo2_edit_turbo_int8_convrot.safetensors"), 8.41, "FIBO 2 Edit Turbo", "non-commercial license"),
    "fibo2_dev_int8_convrot.safetensors": (_hf("Comfy-Org/Fibo-2.0", "diffusion_models/fibo2_dev_int8_convrot.safetensors"), 8.41, "FIBO 2 Dev", "non-commercial license"),
    # MOSS-Image
    "moss_image_sft_int8_convrot.safetensors": (_hf("Comfy-Org/MOSS-Image", "diffusion_models/moss_image_sft_int8_convrot.safetensors"), 5.41, "MOSS SFT", ""),
    "moss_image_sft_turbo_int8_convrot.safetensors": (_hf("toxicdog/MOSS-Image-SFT-Turbo-INT8-ConvRot", "diffusion_models/moss_image_sft_turbo_int8_convrot.safetensors"), 5.41, "MOSS Turbo", ""),
    "qwen3.5_9b_w4a8_gptq.safetensors": (_hf("Comfy-Org/MOSS-Image", "text_encoders/qwen3.5_9b_w4a8_gptq.safetensors"), 7.34, "MOSS's text encoder; also writes the scene descriptions", ""),
    "moss_image_color_depth_control.safetensors": (_hf("Comfy-Org/MOSS-Image", "loras/moss_image_color_depth_control.safetensors"), 0.31, "MOSS's depth lock", ""),
    "depth_anything_3_mono_large.safetensors": (_hf("Comfy-Org/Depth-Anything-3", "geometry_estimation/depth_anything_3_mono_large.safetensors"), 1.34, "the depth locks (it measures depth)", ""),
    # Krea 2
    "krea2_turbo_fp8_scaled.safetensors": (_hf("Comfy-Org/Krea-2", "diffusion_models/krea2_turbo_fp8_scaled.safetensors"), 13.14, "Krea 2 Turbo", ""),
    "qwen3vl_4b_fp8_scaled.safetensors": (_hf("Comfy-Org/Krea-2", "text_encoders/qwen3vl_4b_fp8_scaled.safetensors"), 5.24, "Krea 2's text encoder", ""),
    "qwen_image_vae.safetensors": (_hf("Comfy-Org/Qwen-Image_ComfyUI", "split_files/vae/qwen_image_vae.safetensors"), 0.25, "Krea 2's VAE", ""),
    "krea2_style_reference.safetensors": (_hf("Comfy-Org/Krea-2", "loras/krea2_style_reference.safetensors"), 0.46, "Krea 2's style images", ""),
    "krea2_depth_control_lora.safetensors": (_hf("Patil/Krea-2-depth-controlnet", "depth-control-lora.safetensors"), 0.86, "Krea 2's depth lock", "it downloads as depth-control-lora.safetensors: rename it"),
    # Flux.1
    "flux1-dev-fp8.safetensors": (_hf("Comfy-Org/flux1-dev", "flux1-dev-fp8.safetensors"), 17.25, "Flux.1", ""),
    "FLUX.1-dev-ControlNet-Union-Pro-2.0.safetensors": (_hf("Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro-2.0", "diffusion_pytorch_model.safetensors"), 4.28, "Flux.1's edge lock", "it downloads as diffusion_pytorch_model.safetensors: rename it"),
    "uso-flux1-dit-lora-v1.safetensors": (_hf("Comfy-Org/USO_1.0_Repackaged", "split_files/loras/uso-flux1-dit-lora-v1.safetensors"), 0.48, "Flux.1's style images", ""),
    "uso-flux1-projector-v1.safetensors": (_hf("Comfy-Org/USO_1.0_Repackaged", "split_files/model_patches/uso-flux1-projector-v1.safetensors"), 0.02, "Flux.1's style images", ""),
    "sigclip_vision_patch14_384.safetensors": (_hf("Comfy-Org/sigclip_vision_384", "sigclip_vision_patch14_384.safetensors"), 0.86, "Flux.1's style images", ""),
    # upscalers
    "seedvr2_3b_int8_convrot.safetensors": (_hf("Comfy-Org/SeedVR2", "diffusion_models/seedvr2_3b_int8_convrot.safetensors"), 3.46, "the SeedVR2 upscaler (3B)", ""),
    "seedvr2_7b_int8_convrot.safetensors": (_hf("Comfy-Org/SeedVR2", "diffusion_models/seedvr2_7b_int8_convrot.safetensors"), 8.33, "the SeedVR2 upscaler (7B)", ""),
    "seedvr2_ema_vae_fp16.safetensors": (_hf("Comfy-Org/SeedVR2", "vae/seedvr2_ema_vae_fp16.safetensors"), 0.50, "the SeedVR2 upscaler", ""),
    "4x-UltraSharp.safetensors": (_hf("Kim2091/UltraSharp", "4x-UltraSharp.safetensors"), 0.07, "the 4x-UltraSharp upscaler", ""),
}

# nodes that come from a node pack, not ComfyUI itself: (pack, where to get it)
NODE_PACKS = {n: ("comfyui-krea2-controlnet", "https://github.com/facok/comfyui-krea2-controlnet")
              for n in ("Krea2ControlLoRALoader", "Krea2ControlApply", "Krea2ControlImageEncode")}

# ComfyUI's answers, kept for a minute (refresh_models() drops them)
_cache = {"at": 0.0, "info": {}}
_lock = threading.Lock()
TTL = 60


def refresh_models():
    with _lock:
        _cache["at"] = 0.0


def _info(comfy, node):
    """ComfyUI's description of a node, or None when it doesn't have it."""
    with _lock:
        if time.time() - _cache["at"] > TTL:
            _cache.update(at=time.time(), info={})
        if node in _cache["info"]:
            return _cache["info"][node]
    try:
        info = requests.get(f"{comfy.url}/object_info/{node}", timeout=10).json().get(node)
    except (requests.RequestException, ValueError):
        return None
    with _lock:
        _cache["info"][node] = info
    return info


def _options(info, field):
    spec = (info.get("input", {}).get("required", {}) | info.get("input", {}).get("optional", {})).get(field)
    if not spec:
        return None
    return spec[1].get("options", []) if spec[0] == "COMBO" else spec[0]


def needs(s):
    """{(loader node, input, file): models folder} and the set of nodes the field's workflows use."""
    fam = FAMILIES.get(s.get("family"), {})
    pad = edit_padding(640, 480, s["edit_margin"]) if fam.get("edit") else (0, 0, 0, 0)
    refs = [f"style{i}.png" for i in range(len(s["style_refs"]))]
    wfs = [build_workflow(s, "x.png", 640, 480, "p", refs, pad)]
    if s["states_pass"]:
        wfs.append(build_states_workflow(s, "c.png", "o.png", "m.png", 1024, 768, 2560, 1920, "p", refs))
    if s.get("describe_scene") and s.get("family") != "upscale":
        wfs.append(build_caption_workflow("x.png"))
    files, nodes = {}, set()
    for wf in wfs:
        for node in wf.values():
            nodes.add(node["class_type"])
            for field, folder in LOADERS.get(node["class_type"], ()):
                name = node["inputs"].get(field)
                if isinstance(name, str) and name:
                    files[(node["class_type"], field, name)] = folder
    return files, nodes


def missing(s, comfy):
    """What these settings need that isn't there: {"files": [...], "nodes": [...], "tools": [...]}."""
    files, nodes = needs(s)
    out = {"files": [], "nodes": [], "tools": []}
    seen = set()
    for (node, field, name), folder in files.items():
        info = _info(comfy, node)
        have = _options(info, field) if info else None
        if have is None or name in have or name in seen:
            continue  # a missing loader node is reported with the nodes
        seen.add(name)
        url, gb, what, note = FILES.get(name, (None, None, "", ""))
        out["files"].append({"file": name, "folder": folder, "url": url, "gb": gb, "what": what, "note": note})
    packs = set()
    for node in sorted(nodes):
        if _info(comfy, node) is not None:
            continue
        pack, url = NODE_PACKS.get(node, (None, None))
        if pack in packs:
            continue
        packs.add(pack)
        out["nodes"].append({"node": node, "pack": pack, "url": url})
    used = [s["upscaler"]]
    if s.get("family") != "upscale" or (s["upscaler"] == "seedvr2" and not s["seedvr2_input"]):
        used.append(s["input_upscaler"])
    for u in used:
        if u.startswith(upscalers.UPSCAYL) and u[len(upscalers.UPSCAYL):] not in upscalers.upscayl_models():
            out["tools"].append({"name": "Upscayl", "url": "https://upscayl.org",
                                 "note": "install it, or pick another upscaler"})
            break
    return out


def summary(miss):
    """One line for an error message."""
    parts = [f["file"] for f in miss["files"]]
    parts += [f"the {n['pack']} node pack" if n["pack"] else f"ComfyUI's {n['node']} node (update ComfyUI)"
              for n in miss["nodes"]]
    parts += [t["name"] for t in miss["tools"]]
    return ", ".join(parts)
