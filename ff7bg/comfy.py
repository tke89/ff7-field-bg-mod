"""ComfyUI client and the Flux restyle workflow.

Workflow: original view -> 4x-UltraSharp -> working size -> Flux.1-dev img2img, held to the original
layout by ControlNet Union Pro 2.0 (canny edges of the original) -> 4x-UltraSharp -> final size.
Optional USO style reference images steer the look.
"""
import json
import math
import time
import uuid
from pathlib import Path

import requests
import websocket

# The user's tuned defaults (2026-10-20). Keep the prompt about the rendering, not the content:
# naming materials like timber or tiles turned Midgar's train station into a medieval alley in
# testing. Style images (studio/styles, none shipped) are the better way to steer the look.
DEFAULT_PROMPT = (
    "A modern high-end remaster of this video game scene, drawn by hand with a stylised chibi painterly "
    "look. crisp detailed geometry, richly textured surfaces, warm glowing practical lights, soft global "
    "illumination, ambient occlusion, light atmospheric haze, saturated cinematic color grading, sharp "
    "focus. Same camera, same layout, every object in the same place. An empty scene without any people."
)

DEFAULT_SETTINGS = {
    "prompt": DEFAULT_PROMPT,
    "denoise": 0.2,           # how much the AI may repaint (0 = keep original, 1 = ignore it)
    "structure": 1.2,         # ControlNet strength: how hard edges are locked to the original
    "structure_end": 0.75,    # stop locking edges after this fraction of the steps
    "canny_low": 0.2,
    "canny_high": 0.6,
    "guidance": 5.0,
    "steps": 20,
    "cfg": 1.0,               # sampler CFG for edit models that use one (Mage-Flow full: 5)
    "seed": 2107,
    "out_scale": 4,
    "work_mp": 1.4,           # megapixels Flux renders at before the final upscale
    # Detail pass: upscale the render to the output size, then re-run Flux over 1024px tiles at low
    # strength so the extra resolution gets real painted detail instead of upscaler sharpening.
    # Tested on Kalm at 6x: 0.35-0.4 adds real texture; the base model is ~15% faster than the USO
    # one and doesn't ghost where tiles overlap; a per-tile edge lock made no visible difference.
    "detail_pass": False,
    "detail_denoise": 0.15,
    "detail_steps": 8,
    "detail_structure": 0.0,   # ControlNet strength per tile (0 = off)
    "detail_style": False,     # run the detail pass with the style-image model too
    # States pass: doors, lights and animation frames, repainted in place against the new scene.
    "states_pass": True,
    "state_denoise_boost": 0.1,  # extra repaint over the field's, to blend pasted original art in
    # Edit models: how much of each state's own (original) art the states pass repaints. 1 repaints from
    # scratch, which gave every animation frame its own design; lower keeps each frame's shape.
    "state_denoise": 0.5,
    "style_refs": [],             # file names in studio/styles; none by default
    "style_ref_strength": 0.5,
    # Models. "family" picks the workflow (see FAMILIES): Flux.1-based models, or Krea 2.
    "family": "flux1",
    "model_source": "checkpoint",   # "checkpoint" (all-in-one file) or "separate" (model + encoders + VAE)
    "checkpoint": "flux1-dev-fp8.safetensors",
    "unet": "",
    "unet_dtype": "default",
    "clip1": "",
    "clip2": "",
    "clip_type": "flux",
    "vae": "",
    "lora": "",                     # optional extra style LoRA ("" = none)
    "lora_strength": 0.8,
    "controlnet": "FLUX.1-dev-ControlNet-Union-Pro-2.0.safetensors",  # "" = no structure lock
    "upscaler": "4x-UltraSharp.safetensors",                          # "" = plain Lanczos resize, "seedvr2" = SeedVR2
    # SeedVR2 (when "upscaler" is "seedvr2"): one-step diffusion upscaler for the rendered images
    "seedvr2_unet": "seedvr2_3b_int8_convrot.safetensors",
    "seedvr2_vae": "seedvr2_ema_vae_fp16.safetensors",
    "seedvr2_color": "lab",                       # lab / wavelet / adain / none
    "seedvr2_input": False,                       # also use it for the first enlargement of the original art
    "input_upscaler": "4x-UltraSharp.safetensors",  # ...otherwise this one
    # The ControlNet already pins the layout; warping on top of it misreads redesigned textures
    # (e.g. a new floor-tile pattern) as drift and bends the image. Meant for outside images.
    "align": False,
    # Edit-model workflows (Qwen-Image 2.1) regenerate the whole image and tend to zoom in 2-3%:
    # the input gets a blurred margin of this size, cropped off again inside the workflow.
    "edit_margin": 0.08,
}


# What switching family in the Studio sets (the files are the ones the official ComfyUI templates use).
FAMILY_PRESETS = {
    "flux1": {"family": "flux1", "model_source": "checkpoint", "checkpoint": "flux1-dev-fp8.safetensors",
              "steps": 20, "detail_steps": 8},
    "krea2": {"family": "krea2", "model_source": "separate", "unet": "krea2_turbo_fp8_scaled.safetensors",
              "unet_dtype": "default", "clip1": "qwen3vl_4b_fp8_scaled.safetensors", "clip2": "",
              "clip_type": "krea2", "vae": "qwen_image_vae.safetensors", "steps": 8, "detail_steps": 4},
    # Qwen-Image 2.1 Turbo edit: fixed 8-step schedule, renders ~2K natively (so no detail pass),
    # and its slight reframing is undone by the auto-align step.
    "qwen21": {"family": "qwen21", "model_source": "separate",
               "unet": "qwen_image_2.1_turbo_int8_convrot.safetensors", "unet_dtype": "default",
               "clip1": "qwen3vl_8b_int8_convrot.safetensors", "clip2": "", "clip_type": "qwen_image",
               "vae": "qwen_image_2.1_vae_bf16.safetensors", "work_mp": 3.0, "align": True,
               "detail_pass": False, "style_ref_strength": 0.0},
    # FLUX 3 Image Klein (step-distilled), an edit model that renders up to 4K natively.
    "flux3k": {"family": "flux3k", "model_source": "separate",
               "unet": "flux3-image-klein-int8-convrot.safetensors", "unet_dtype": "default",
               "clip1": "qwen3vl_4b_bf16.safetensors", "clip2": "", "clip_type": "flux3",
               "vae": "flux2-vae.safetensors", "steps": 16, "cfg": 1.0, "work_mp": 4.0, "align": True,
               "detail_pass": False, "style_ref_strength": 0.0},
    # Mage-Flow-Edit Turbo (Microsoft, 4B): 4-step edits.
    "mageflow": {"family": "mageflow", "model_source": "separate",
                 "unet": "mage_flow_edit_turbo_int8_convrot.safetensors", "unet_dtype": "default",
                 "clip1": "qwen3vl_4b_bf16.safetensors", "clip2": "", "clip_type": "mage",
                 "vae": "mage_flow_vae_bf16.safetensors", "steps": 4, "cfg": 1.0, "work_mp": 2.0, "align": True,
                 "detail_pass": False, "style_ref_strength": 0.0},
    # Bria FIBO 2.0 Edit Turbo (8B): 4-step edits. Trained at about 1 MP; the int8 text encoder has a
    # vision bug (ComfyUI issue #17248), so the bf16 one.
    "fibo2": {"family": "fibo2", "model_source": "separate",
              "unet": "fibo2_edit_turbo_int8_convrot.safetensors", "unet_dtype": "default",
              "clip1": "qwen3vl_4b_bf16.safetensors", "clip2": "", "clip_type": "fibo2",
              "vae": "flux2-vae.safetensors", "steps": 4, "cfg": 1.0, "work_mp": 2.0, "align": True,
              "detail_pass": False, "style_ref_strength": 0.0},
    # Upscale only: no diffusion model; the original art goes straight through the upscaler.
    "upscale": {"family": "upscale", "align": False, "detail_pass": False},
}

# The edit models (qwen21, flux3k, mageflow) start with style images off: they take content from them.
# With Kalm's remaster as a style image, Qwen-Image 2.1 repainted ancnt1 as Kalm, and Mage-Flow pasted
# timber framing over everything; FLUX 3 mostly ignored them.

# Fast and full versions of the edit models (from ComfyUI's templates); the Studio switches between them.
EDIT_VARIANTS = {
    "qwen21": {"turbo": {"unet": "qwen_image_2.1_turbo_int8_convrot.safetensors", "steps": 8, "cfg": 1.0},
               "full": {"unet": "qwen_image_2.1_int8_convrot.safetensors", "steps": 25, "cfg": 1.0}},
    "flux3k": {"turbo": {"unet": "flux3-image-klein-int8-convrot.safetensors", "steps": 16, "cfg": 1.0},
               "full": {"unet": "flux3-image-klein-base-int8-convrot.safetensors", "steps": 50, "cfg": 1.0}},
    "mageflow": {"turbo": {"unet": "mage_flow_edit_turbo_int8_convrot.safetensors", "steps": 4, "cfg": 1.0},
                 "full": {"unet": "mage_flow_edit_int8_convrot.safetensors", "steps": 30, "cfg": 5.0}},
    "fibo2": {"turbo": {"unet": "fibo2_edit_turbo_int8_convrot.safetensors", "steps": 4, "cfg": 1.0},
              "full": {"unet": "fibo2_dev_int8_convrot.safetensors", "steps": 30, "cfg": 5.0}},
}


class ComfyError(RuntimeError):
    pass


TILE = 1024
TILE_OVERLAP = 128


def tile_count(w, h, tile=TILE, overlap=TILE_OVERLAP):
    """Number of tiles SplitImageToTileList produces (same grid rule)."""
    stride = max(tile * 0.25, tile - overlap)
    nx = 1 if w <= tile else math.ceil((w - tile) / stride) + 1
    ny = 1 if h <= tile else math.ceil((h - tile) / stride) + 1
    return nx * ny


def working_size(w, h, target_mp):
    """Largest integer multiple of the canvas within target_mp (canvases are multiples of 16)."""
    k = max(1, int(math.sqrt(target_mp * 1e6 / (w * h))))
    return w * k, h * k


def _loaders(wf, s):
    """Base model, text encoder(s) and VAE from the chosen files (+ optional LoRA).
    Returns (model, clip, vae) node outputs."""
    if s["model_source"] == "separate":
        wf["unet"] = {"class_type": "UNETLoader", "inputs": {"unet_name": s["unet"], "weight_dtype": s["unet_dtype"]}}
        if s["clip2"]:
            wf["clip"] = {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": s["clip1"], "clip_name2": s["clip2"], "type": s["clip_type"]}}
        else:
            wf["clip"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": s["clip1"], "type": s["clip_type"]}}
        wf["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": s["vae"]}}
        model, clip, vae = ["unet", 0], ["clip", 0], ["vae", 0]
    else:
        wf["ckpt"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": s["checkpoint"]}}
        model, clip, vae = ["ckpt", 0], ["ckpt", 1], ["ckpt", 2]
    if s["lora"]:
        wf["lora"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "lora_name": s["lora"], "strength_model": s["lora_strength"]}}
        model = ["lora", 0]
    if s.get("family") == "qwen21":
        wf["qcache"] = {"class_type": "QwenImage21Cache", "inputs": {"model": model, "device": "auto", "dtype": "default"}}
        model = ["qcache", 0]
    return model, clip, vae


FAMILIES = {
    # Flux.1: FluxGuidance prompt strength, Flux ControlNet structure lock, USO style images.
    "flux1": {"scheduler": "beta", "controlnet": True},
    # Krea 2 Turbo (Qwen3-VL text encoder, Qwen-Image VAE): no native structure lock, so the
    # layout is held by a low repaint strength; style images go through the official
    # style-reference LoRA as reference latents (ComfyUI's image_krea2_..._style_reference template).
    "krea2": {"scheduler": "simple", "controlnet": False},
    # Qwen-Image 2.1 Turbo, used as an image *edit* model (ComfyUI's image_qwen_image_2_1_turbo_image_edit
    # template): the original is <image1>, style images are extra references named in the
    # instruction. Edits regenerate everything, so repaint strength and the detail pass don't apply;
    # the layout holds well and the small reframing is aligned away afterwards.
    "qwen21": {"scheduler": "simple", "controlnet": False, "edit": True},
    # FLUX 3 Image Klein: the original goes in as a reference latent (ReferenceMultiLatent), named
    # <ref_image_0> in the instruction (ComfyUI's image_flux3_klein_*_image_edit templates).
    "flux3k": {"scheduler": "simple", "controlnet": False, "edit": True},
    # Mage-Flow-Edit: instruction + reference images through its own encoder node
    # (ComfyUI's image_mage_flow_edit_*_int8 templates).
    "mageflow": {"scheduler": "simple", "controlnet": False, "edit": True},
    # Bria FIBO 2.0: the original as <image_1> of TextEncodeFibo2, which needs a ComfyUI newer than 0.42.1
    # (PR #17240); the result size is set explicitly, since a single image is cropped to it pixel for pixel.
    "fibo2": {"scheduler": "simple", "controlnet": False, "edit": True, "node": "TextEncodeFibo2"},
    # No restyle: the original enlarged by the chosen upscaler (SeedVR2 or an ESRGAN-style model),
    # states included, as a faithful HD baseline.
    "upscale": {"scheduler": "simple", "controlnet": False, "upscale": True},
}
KREA2_STYLE_LORA = "krea2_style_reference.safetensors"
QWEN21_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"


def _edit_instruction(s, first, ref_tags):
    instr = (f"Restyle {first} while keeping the exact same composition, camera angle, layout and every "
             "object in the same place: " + s["prompt"])
    if ref_tags:
        names = ref_tags[0] if len(ref_tags) == 1 else ", ".join(ref_tags[:-1]) + " and " + ref_tags[-1]
        # plain "match the style, not the content" wasn't enough: Qwen-Image 2.1 pasted a style image's town in
        instr += (f" {names} {'is' if len(ref_tags) == 1 else 'are'} only style references: borrow their brushwork, "
                  f"palette and lighting, but never their buildings, objects or layout. The result must show the scene "
                  f"of {first} and nothing else.")
    return instr


# Style images for edit models are scaled to about this size. At full size (1-1.6 MP each) three of
# them outweighed the scene itself, and Qwen-Image 2.1 Full repainted a style image instead of the field.
EDIT_REF_MP = 0.35


def _edit_conditioning(wf, s, clip, vae, image, refs, size=(0, 0)):
    """Edit instruction with the image to restyle first and the style images after it.
    Returns (positive, negative, latent at the image's size). size: the image's (w, h), for models
    that need it given."""
    fam = _family(s)["name"]
    use = refs if s["style_ref_strength"] > 0 else []
    if EDIT_REF_MP:
        small = []
        for i, r in enumerate(use):
            wf[f"refsmall{i}"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": r, "upscale_method": "lanczos", "megapixels": EDIT_REF_MP, "resolution_steps": 32}}
            small.append([f"refsmall{i}", 0])
        use = small
    if fam == "flux3k":
        tags = [f"<ref_image_{i + 1}>" for i in range(len(use))]
        wf["qpos"] = {"class_type": "CLIPTextEncode", "inputs": {
            "clip": clip, "text": _edit_instruction(s, "<ref_image_0>", tags)}}
        wf["qneg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["qpos", 0]}}
        enc = {"positive": ["qpos", 0], "negative": ["qneg", 0], "vae": vae, "resolution": "keep resolution",
               "images.image0": image}
        for i, r in enumerate(use):
            enc[f"images.image{i + 1}"] = r
        wf["qenc"] = {"class_type": "ReferenceMultiLatent", "inputs": enc}
        return ["qenc", 0], ["qenc", 1], ["qenc", 2]
    if fam == "fibo2":
        tags = [f"<image_{i + 2}>" for i in range(len(use))]
        enc = {"clip": clip, "prompt": _edit_instruction(s, "<image_1>", tags), "negative_prompt": "",
               "vae": vae, "width": size[0], "height": size[1], "batch_size": 1, "images.image_1": image}
        for i, r in enumerate(use):
            enc[f"images.image_{i + 2}"] = r
        wf["qenc"] = {"class_type": "TextEncodeFibo2", "inputs": enc}
        return ["qenc", 0], ["qenc", 1], ["qenc", 2]
    if fam == "mageflow":
        tags = [f"reference image {i + 2}" for i in range(len(use))]
        enc = {"clip": clip, "prompt": _edit_instruction(s, "image 1", tags), "negative_prompt": "",
               "width": 0, "height": 0, "batch_size": 1, "vae": vae, "images.image_1": image}
        for i, r in enumerate(use):
            enc[f"images.image_{i + 2}"] = r
        wf["qenc"] = {"class_type": "TextEncodeMageFlowEdit", "inputs": enc}
        return ["qenc", 0], ["qenc", 1], ["qenc", 2]
    tags = [f"<image{i + 2}>" for i in range(len(use))]
    enc = {"clip": clip, "prompt": _edit_instruction(s, "<image1>", tags), "negative_prompt": "", "resolution": 0,
           "vae": vae, "images.image_1": image}
    for i, r in enumerate(use):
        enc[f"images.image_{i + 2}"] = r
    wf["qenc"] = {"class_type": "TextEncodeQwenImage21", "inputs": enc}
    return ["qenc", 0], ["qenc", 1], ["qenc", 2]


def _edit_sample(wf, s, model, positive, negative, latent, denoise=1.0):
    if _family(s)["name"] != "qwen21" or "turbo" not in s["unet"]:
        wf["sample"] = {"class_type": "KSampler", "inputs": {
            "model": model, "seed": s["seed"], "steps": s["steps"], "cfg": s["cfg"], "sampler_name": "euler",
            "scheduler": "simple", "positive": positive, "negative": negative, "latent_image": latent, "denoise": denoise}}
        return ["sample", 0]
    # partial repaint on the turbo schedule: start from the first step at or below `denoise`
    sigmas = [float(x) for x in QWEN21_TURBO_SIGMAS.split(",")]
    if denoise < 1:
        sigmas = [x for x in sigmas if x <= denoise + 1e-6] or [0.0]
        if len(sigmas) < 2:
            sigmas = [min(denoise, 0.414568), 0.0]
    # Qwen-Image 2.1 Turbo: its fixed 8-step schedule
    wf["qnoise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": s["seed"]}}
    wf["qguider"] = {"class_type": "CFGGuider", "inputs": {"model": model, "positive": positive, "negative": negative, "cfg": 1.0}}
    wf["qsampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    wf["qsigmas"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": ", ".join(f"{x:g}" for x in sigmas)}}
    wf["sample"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["qnoise", 0], "guider": ["qguider", 0], "sampler": ["qsampler", 0], "sigmas": ["qsigmas", 0],
        "latent_image": latent}}
    return ["sample", 0]


def _family(s):
    return FAMILIES.get(s.get("family"), FAMILIES["flux1"]) | {"name": s.get("family", "flux1")}


def _load_refs(wf, style_ref_names):
    refs = []
    for i, name in enumerate(style_ref_names):
        wf[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        refs.append([f"ref{i}", 0])
    return refs


def _conditioning(wf, s, clip, vae, control_image, refs):
    """Returns ((positive, negative) for the main render, (positive, negative) prompt-only)."""
    fam = _family(s)
    wf["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": s["prompt"]}}
    if fam["name"] == "krea2":
        wf["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
        plain = (["pos", 0], ["neg", 0])
        if not refs:
            return plain, plain
        enc = {"clip": clip, "prompt": s["prompt"], "vae": vae}
        for i, r in enumerate(refs[:3]):  # the style LoRA was trained on 1-2 references
            enc[f"image{i + 1}"] = r
        wf["refpos_enc"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": enc}
        wf["refpos"] = {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["refpos_enc", 0], "reference_latents_method": "index_timestep_zero"}}
        wf["refneg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["refpos", 0]}}
        return (["refpos", 0], ["refneg", 0]), plain

    wf["guid"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["pos", 0], "guidance": s["guidance"]}}
    wf["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
    plain = (["guid", 0], ["neg", 0])
    if not s["controlnet"]:
        return plain, plain
    wf["cnl"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": s["controlnet"]}}
    wf["edges"] = {"class_type": "Canny", "inputs": {
        "image": control_image, "low_threshold": s["canny_low"], "high_threshold": s["canny_high"]}}
    wf["cn"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
        "positive": ["guid", 0], "negative": ["neg", 0], "control_net": ["cnl", 0], "image": ["edges", 0],
        "strength": s["structure"], "start_percent": 0.0, "end_percent": s["structure_end"], "vae": vae}}
    return (["cn", 0], ["cn", 1]), plain


SEEDVR2 = "seedvr2"  # special "upscaler" value: the SeedVR2 diffusion upscaler instead of an ESRGAN-style model


def _seedvr2(wf, key, s, image, w, h):
    """SeedVR2 one-step diffusion upscale to exactly w x h (ComfyUI's utility_seedvr2_..._upscale_image
    template): resize, pad, encode, one sampling step, decode, then align/color-match to the input."""
    wf.setdefault("svr_unet", {"class_type": "UNETLoader", "inputs": {"unet_name": s["seedvr2_unet"], "weight_dtype": "default"}})
    wf.setdefault("svr_vae", {"class_type": "VAELoader", "inputs": {"vae_name": s["seedvr2_vae"]}})
    tiled = {"tile_size": 512, "overlap": 128, "temporal_size": 4096, "temporal_overlap": 8}
    wf[f"{key}_svr_in"] = {"class_type": "ImageScale", "inputs": {
        "image": image, "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}}
    wf[f"{key}_svr_pad"] = {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": [f"{key}_svr_in", 0]}}
    wf[f"{key}_svr_lat"] = {"class_type": "VAEEncodeTiled", "inputs": {"pixels": [f"{key}_svr_pad", 0], "vae": ["svr_vae", 0], **tiled}}
    wf[f"{key}_svr_cond"] = {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["svr_unet", 0], "vae_conditioning": [f"{key}_svr_lat", 0]}}
    wf[f"{key}_svr_ks"] = {"class_type": "KSampler", "inputs": {
        "model": ["svr_unet", 0], "seed": s["seed"], "steps": 1, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
        "positive": [f"{key}_svr_cond", 0], "negative": [f"{key}_svr_cond", 1], "latent_image": [f"{key}_svr_lat", 0], "denoise": 1.0}}
    wf[f"{key}_svr_dec"] = {"class_type": "VAEDecodeTiled", "inputs": {"samples": [f"{key}_svr_ks", 0], "vae": ["svr_vae", 0], **tiled}}
    wf[f"{key}_svr_post"] = {"class_type": "SeedVR2PostProcessing", "inputs": {
        "images": [f"{key}_svr_dec", 0], "original_resized_images": [f"{key}_svr_in", 0],
        "color_correction_method": s["seedvr2_color"]}}
    return [f"{key}_svr_post", 0]


def _resize(wf, key, s, image, w, h):
    """Enlarge with the chosen upscaler (if any), then resize exactly to w x h.

    SeedVR2 handles the enlargements of rendered images; the first enlargement of the original low-res
    art ("work", "orig_gen") uses the ESRGAN-style input upscaler, unless seedvr2_input is set."""
    upscaler = s["upscaler"]
    if upscaler == SEEDVR2 and key in ("work", "orig_gen") and not s["seedvr2_input"]:
        upscaler = s["input_upscaler"]
    if upscaler == SEEDVR2:
        image = _seedvr2(wf, key, s, image, w, h)
    elif upscaler:
        loader = "upm" if upscaler == s["upscaler"] else "upm_input"
        wf.setdefault(loader, {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscaler}})
        wf[f"{key}_up"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": [loader, 0], "image": image}}
        image = [f"{key}_up", 0]
    wf[key] = {"class_type": "ImageScale", "inputs": {
        "image": image, "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"}}
    return [key, 0]


def _upscale_chain(wf, s, image, w, h):
    """Upscale-only: the original art enlarged to w x h. With SeedVR2, the input upscaler enlarges first
    and SeedVR2 restores that, unless seedvr2_input is set (then SeedVR2 works from the original)."""
    if s["upscaler"] == SEEDVR2 and not s["seedvr2_input"] and s["input_upscaler"]:
        image = _resize(wf, "work", s, image, w, h)
    return _resize(wf, "final", s, image, w, h)


def _style_chain(wf, s, model, refs, w, h):
    """The model as patched for style reference images (USO on Flux.1, the style LoRA on Krea 2)."""
    if not refs:
        return model
    if _family(s)["name"] == "krea2":
        wf["style_lora"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "lora_name": KREA2_STYLE_LORA, "strength_model": s["style_ref_strength"]}}
        wf["style_shift"] = {"class_type": "ModelSamplingFlux", "inputs": {
            "model": ["style_lora", 0], "max_shift": 1.15, "base_shift": 0.5, "width": w, "height": h}}
        return ["style_shift", 0]
    wf["uso_lora"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": model, "lora_name": "uso-flux1-dit-lora-v1.safetensors",
        "strength_model": s["style_ref_strength"]}}
    wf["uso_patch"] = {"class_type": "ModelPatchLoader", "inputs": {"name": "uso-flux1-projector-v1.safetensors"}}
    wf["uso_vision"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "sigclip_vision_patch14_384.safetensors"}}
    model = ["uso_lora", 0]
    for i, ref in enumerate(refs):
        wf[f"refenc{i}"] = {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["uso_vision", 0], "image": ref, "crop": "center"}}
        wf[f"uso{i}"] = {"class_type": "USOStyleReference", "inputs": {
            "model": model, "model_patch": ["uso_patch", 0], "clip_vision_output": [f"refenc{i}", 0]}}
        model = [f"uso{i}", 0]
    return model


def _sampler(s, model, positive, negative, latent, steps, denoise):
    return {"class_type": "KSampler", "inputs": {
        "model": model, "seed": s["seed"], "steps": steps, "cfg": 1.0, "sampler_name": "euler",
        "scheduler": _family(s)["scheduler"], "positive": positive, "negative": negative,
        "latent_image": latent, "denoise": denoise}}


def edit_padding(w, h, margin):
    """Margin (in original pixels, multiples of 16) around the input for edit-model workflows."""
    mx, my = round(w * margin / 16) * 16, round(h * margin / 16) * 16
    return mx, my, mx, my


def edit_keep(pad):
    """How much of that margin the edit workflow returns (1x pixels per side): all of it. Edits zoom in
    (Qwen Turbo 1-3%, the full model more), which pushes the scene's edges out into the margin; the
    aligner pulls them back from there."""
    return tuple(pad)


def _build_edit_workflow(s, input_name, canvas_w, canvas_h, prefix, style_ref_names, pad):
    """Edit models (Qwen-Image 2.1, FLUX 3 Klein, Mage-Flow): input_name is the original with `pad` margins
    added (see edit_padding). The
    result keeps edit_keep(pad) of that margin around the canvas."""
    l, t, r, b = pad
    pw, ph = canvas_w + l + r, canvas_h + t + b
    ww, wh = working_size(pw, ph, s["work_mp"])
    k = ww // pw
    wf = {"src": {"class_type": "LoadImage", "inputs": {"image": input_name}}}
    base, clip, vae = _loaders(wf, s)
    refs = _load_refs(wf, style_ref_names)
    work = _resize(wf, "work", s, ["src", 0], ww, wh)
    positive, negative, latent = _edit_conditioning(wf, s, clip, vae, work, refs, size=(ww, wh))
    _edit_sample(wf, s, base, positive, negative, latent)
    wf["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    # the encoder rounds sizes to multiples of 32: back to the exact working size, then drop the margin
    wf["back"] = {"class_type": "ImageScale", "inputs": {
        "image": ["dec", 0], "upscale_method": "lanczos", "width": ww, "height": wh, "crop": "disabled"}}
    kl, kt, kr, kb = edit_keep(pad)
    fw, fh = canvas_w + kl + kr, canvas_h + kt + kb
    wf["crop"] = {"class_type": "ImageCrop", "inputs": {
        "image": ["back", 0], "width": fw * k, "height": fh * k, "x": (l - kl) * k, "y": (t - kt) * k}}
    result = _resize(wf, "final", s, ["crop", 0], fw * s["out_scale"], fh * s["out_scale"])
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": result, "filename_prefix": prefix}}
    return wf


def build_workflow(s, input_name, canvas_w, canvas_h, prefix, style_ref_names=(), pad=(0, 0, 0, 0)):
    if _family(s).get("upscale"):
        wf = {"src": {"class_type": "LoadImage", "inputs": {"image": input_name}}}
        result = _upscale_chain(wf, s, ["src", 0], canvas_w * s["out_scale"], canvas_h * s["out_scale"])
        wf["save"] = {"class_type": "SaveImage", "inputs": {"images": result, "filename_prefix": prefix}}
        return wf
    if _family(s).get("edit"):
        return _build_edit_workflow(s, input_name, canvas_w, canvas_h, prefix, style_ref_names, pad)
    ww, wh = working_size(canvas_w, canvas_h, s["work_mp"])
    ow, oh = canvas_w * s["out_scale"], canvas_h * s["out_scale"]
    wf = {"src": {"class_type": "LoadImage", "inputs": {"image": input_name}}}
    base, clip, vae = _loaders(wf, s)
    refs = _load_refs(wf, style_ref_names)
    work = _resize(wf, "work", s, ["src", 0], ww, wh)
    wf["enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": work, "vae": vae}}
    (positive, negative), plain = _conditioning(wf, s, clip, vae, work, refs)
    styled = _style_chain(wf, s, base, refs, ww, wh)
    wf["sample"] = _sampler(s, styled, positive, negative, ["enc", 0], s["steps"], s["denoise"])
    wf["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    result = _resize(wf, "final", s, ["dec", 0], ow, oh)
    if s["detail_pass"]:
        # Tiles are processed as a list: every node below runs once per tile.
        wf["tiles"] = {"class_type": "SplitImageToTileList", "inputs": {
            "image": result, "tile_width": TILE, "tile_height": TILE, "overlap": TILE_OVERLAP}}
        wf["tenc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["tiles", 0], "vae": vae}}
        pos, neg = plain
        if s["detail_style"] and _family(s)["name"] == "krea2" and refs:
            pos, neg = positive, negative  # the Krea 2 style model expects its reference conditioning
        if s["detail_structure"] > 0 and s["controlnet"] and _family(s)["controlnet"]:
            wf["tedges"] = {"class_type": "Canny", "inputs": {
                "image": ["tiles", 0], "low_threshold": s["canny_low"], "high_threshold": s["canny_high"]}}
            wf["tcn"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": pos, "negative": neg, "control_net": ["cnl", 0], "image": ["tedges", 0],
                "strength": s["detail_structure"], "start_percent": 0.0, "end_percent": 0.6, "vae": vae}}
            pos, neg = ["tcn", 0], ["tcn", 1]
        wf["tsample"] = _sampler(s, styled if s["detail_style"] else base, pos, neg, ["tenc", 0],
                                 s["detail_steps"], s["detail_denoise"])
        wf["tdec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["tsample", 0], "vae": vae}}
        wf["merge"] = {"class_type": "ImageMergeTileList", "inputs": {
            "image_list": ["tdec", 0], "final_width": ow, "final_height": oh, "overlap": TILE_OVERLAP}}
        result = ["merge", 0]
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": result, "filename_prefix": prefix}}
    return wf


def build_states_workflow(s, ctx_name, orig_name, mask_name, gen_w, gen_h, out_w, out_h, prefix,
                          style_ref_names=(), denoise=None):
    """Repaint only the masked areas of a sheet of state panels.

    ctx: the finished new art around each group (kept); orig: the starting point for each state at
    the sheet's size (the new art plus what the state changes in the original, see states.run), or
    for the Upscale workflow the states' original art at 1x; mask: the pixels each state changes.
    The masked areas are re-rendered with the field's own settings, so every state ends up in the
    same style as the scene around it."""
    if _family(s).get("upscale"):  # the states' original art through the same upscaler
        wf = {"orig": {"class_type": "LoadImage", "inputs": {"image": orig_name}}}
        result = _upscale_chain(wf, s, ["orig", 0], out_w, out_h)
        wf["save"] = {"class_type": "SaveImage", "inputs": {"images": result, "filename_prefix": prefix}}
        return wf
    wf = {
        "ctx": {"class_type": "LoadImage", "inputs": {"image": ctx_name}},
        "orig": {"class_type": "LoadImage", "inputs": {"image": orig_name}},
        "mask": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
    }
    base, clip, vae = _loaders(wf, s)
    refs = _load_refs(wf, style_ref_names)
    orig_gen = ["orig", 0]  # already at the sheet's size
    wf["inp"] = {"class_type": "ImageCompositeMasked", "inputs": {
        "destination": ["ctx", 0], "source": orig_gen, "x": 0, "y": 0, "resize_source": False,
        "mask": ["mask", 0]}}
    wf["enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["inp", 0], "vae": vae}}
    wf["noise_mask"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc", 0], "mask": ["mask", 0]}}
    if _family(s).get("edit"):
        # partial repaint inside the mask (keeps each state's shape); the noise mask keeps the rest
        positive, negative, _ = _edit_conditioning(wf, s, clip, vae, ["inp", 0], refs, size=(gen_w, gen_h))
        _edit_sample(wf, s, base, positive, negative, ["noise_mask", 0],
                     denoise=s["state_denoise"] if denoise is None else denoise)
    else:
        (positive, negative), _ = _conditioning(wf, s, clip, vae, orig_gen, refs)
        styled = _style_chain(wf, s, base, refs, gen_w, gen_h)
        wf["sample"] = _sampler(s, styled, positive, negative, ["noise_mask", 0], s["steps"],
                                min(0.95, s["denoise"] + s["state_denoise_boost"]))
    wf["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    result = _resize(wf, "final", s, ["dec", 0], out_w, out_h)
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": result, "filename_prefix": prefix}}
    return wf


class Comfy:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def online(self):
        try:
            return requests.get(f"{self.url}/system_stats", timeout=2).ok
        except requests.RequestException:
            return False

    def models(self, node, field):
        r = requests.get(f"{self.url}/object_info/{node}", timeout=10).json()
        spec = r[node]["input"]["required"][field]
        return spec[1]["options"] if spec[0] == "COMBO" else spec[0]

    def has_node(self, node):
        try:
            return bool(requests.get(f"{self.url}/object_info/{node}", timeout=10).json())
        except (requests.RequestException, ValueError):
            return False

    def upload(self, path, subfolder="ff7studio"):
        with open(path, "rb") as f:
            r = requests.post(f"{self.url}/upload/image", files={"image": (Path(path).name, f, "image/png")},
                              data={"overwrite": "true", "subfolder": subfolder, "type": "input"}, timeout=60)
        r.raise_for_status()
        j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    def interrupt(self):
        try:
            requests.post(f"{self.url}/interrupt", timeout=5)
        except requests.RequestException:
            pass

    def run(self, workflow, on_progress=None, cancelled=lambda: False):
        """Queue a workflow, stream progress, return the saved output image bytes."""
        ws = websocket.WebSocket()
        ws.connect(f"{self.url.replace('http', 'ws', 1)}/ws?clientId={self.client_id}", timeout=10)
        try:
            r = requests.post(f"{self.url}/prompt", json={"prompt": workflow, "client_id": self.client_id}, timeout=30)
            if not r.ok:
                raise ComfyError(_describe_error(r))
            pid = r.json()["prompt_id"]
            ws.settimeout(1.0)
            while True:
                if cancelled():
                    self.interrupt()
                    raise ComfyError("cancelled")
                try:
                    msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not isinstance(msg, str):
                    continue  # binary preview frames
                m = json.loads(msg)
                d = m.get("data", {})
                if d.get("prompt_id") not in (None, pid):
                    continue
                if m["type"] == "progress" and on_progress:
                    on_progress(d.get("node"), d["value"], d["max"])
                elif m["type"] == "executing" and on_progress and d.get("node"):
                    on_progress(d["node"], 0, 0)
                elif m["type"] == "execution_error":
                    raise ComfyError(f"{d.get('node_type')}: {d.get('exception_message', '').strip()}")
                elif m["type"] == "execution_interrupted":
                    raise ComfyError("interrupted")
                elif m["type"] == "execution_success" or (m["type"] == "executing" and d.get("node") is None):
                    break
        finally:
            ws.close()
        hist = requests.get(f"{self.url}/history/{pid}", timeout=30).json()[pid]
        for out in hist["outputs"].values():
            for img in out.get("images", []):
                if img.get("type") == "output":
                    r = requests.get(f"{self.url}/view", params=img, timeout=120)
                    r.raise_for_status()
                    return r.content
        raise ComfyError("workflow finished without an output image")


def _describe_error(r):
    try:
        j = r.json()
    except ValueError:
        return r.text[:500]
    parts = [j.get("error", {}).get("message", "")]
    for node, info in (j.get("node_errors") or {}).items():
        for e in info.get("errors", []):
            parts.append(f"{node}: {e.get('message')} {e.get('details', '')}")
    return "; ".join(p for p in parts if p)
