# Getting started

## 1. Install

You need:

- **Windows** and **FINAL FANTASY VII** from Steam (the 2026 re-release, app 3837340). The Studio reads
  `ff7\workingdir\data\field\flevel.lgp` from the default Steam folder; pass `--lgp <path>` to `studio.py` if your
  game is elsewhere.
- **Python 3.11 or newer** (developed with 3.14).
- **[ComfyUI Desktop](https://www.comfy.org/download)**, version 0.42 or newer. The newer workflows use nodes that
  only recent ComfyUI versions have; the Desktop app updates itself.
- An **NVIDIA GPU**. Developed on an RTX 5090 (32 GB); 16 GB should work for the int8/fp8 models listed here,
  with less headroom at high output scales. The times quoted in these docs are from the 5090.

Set up the project:

```bat
git clone https://github.com/tke89/ff7-field-bg-mod
cd ff7-field-bg-mod
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## 2. Models

ComfyUI Desktop reads models from `Documents\ComfyUI\models\<folder>` (and from a shared
`AppData\Local\Comfy-Desktop\ComfyUI-Shared\models\<folder>`). Put each file in the folder named below. You only
need the files for the workflows you use; the Studio lists whatever ComfyUI has (Advanced → **Refresh models**
after adding files).

**Suggested first set: Qwen-Image 2.1 Turbo + SeedVR2 3B** (about 26 GB). It gave the nicest results in testing
and keeps the layout well.

### Qwen-Image 2.1 (Turbo and Full)

| File | Folder | Download |
|---|---|---|
| `qwen_image_2.1_turbo_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors) |
| `qwen_image_2.1_int8_convrot.safetensors` (Full, optional) | `diffusion_models` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_int8_convrot.safetensors) |
| `qwen3vl_8b_int8_convrot.safetensors` | `text_encoders` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors) |
| `qwen_image_2.1_vae_bf16.safetensors` | `vae` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors) |

### SeedVR2 (upscaler, used by every workflow)

| File | Folder | Download |
|---|---|---|
| `seedvr2_3b_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_3b_int8_convrot.safetensors) |
| `seedvr2_7b_int8_convrot.safetensors` (optional) | `diffusion_models` | [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_7b_int8_convrot.safetensors) |
| `seedvr2_ema_vae_fp16.safetensors` | `vae` | [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/vae/seedvr2_ema_vae_fp16.safetensors) |
| `4x-UltraSharp.safetensors` (ESRGAN upscaler, also used before SeedVR2) | `upscale_models` | [Kim2091/UltraSharp](https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.safetensors) |

### FLUX 3 Image Klein

The files are gated: sign in to Hugging Face and accept the license on
[black-forest-labs/flux-3-image-klein-comfy](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy)
first. The license is free for individuals and companies under $5M annual revenue.

| File | Folder | Download |
|---|---|---|
| `flux3-image-klein-int8-convrot.safetensors` (Distilled, 16 steps) | `diffusion_models` | gated repo above |
| `flux3-image-klein-base-int8-convrot.safetensors` (Base, 50 steps, optional) | `diffusion_models` | gated repo above |
| `qwen3vl_4b_bf16.safetensors` | `text_encoders` | [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow/resolve/main/text_encoders/qwen3vl_4b_bf16.safetensors) |
| `flux2-vae.safetensors` | `vae` | [Comfy-Org/flux2-dev](https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors) |

### Mage-Flow

| File | Folder | Download |
|---|---|---|
| `mage_flow_edit_turbo_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow/resolve/main/diffusion_models/mage_flow_edit_turbo_int8_convrot.safetensors) |
| `qwen3vl_4b_bf16.safetensors` | `text_encoders` | (same as FLUX 3) |
| `mage_flow_vae_bf16.safetensors` | `vae` | [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow/resolve/main/vae/mage_flow_vae_bf16.safetensors) |

### FIBO 2 (needs a ComfyUI newer than 0.42.1)

Non-commercial license (CC BY-NC 4.0).

| File | Folder | Download |
|---|---|---|
| `fibo2_edit_turbo_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/Fibo-2.0](https://huggingface.co/Comfy-Org/Fibo-2.0/resolve/main/diffusion_models/fibo2_edit_turbo_int8_convrot.safetensors) |
| `fibo2_dev_int8_convrot.safetensors` (Dev, optional) | `diffusion_models` | [Comfy-Org/Fibo-2.0](https://huggingface.co/Comfy-Org/Fibo-2.0/resolve/main/diffusion_models/fibo2_dev_int8_convrot.safetensors) |
| `qwen3vl_4b_bf16.safetensors` | `text_encoders` | (same as FLUX 3) |
| `flux2-vae.safetensors` | `vae` | (same as FLUX 3) |

### Krea 2 Turbo

| File | Folder | Download |
|---|---|---|
| `krea2_turbo_fp8_scaled.safetensors` | `diffusion_models` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors) |
| `qwen3vl_4b_fp8_scaled.safetensors` | `text_encoders` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `vae` | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors) |
| `krea2_style_reference.safetensors` (for style images) | `loras` | [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2/resolve/main/loras/krea2_style_reference.safetensors) |

### Flux.1

| File | Folder | Download |
|---|---|---|
| `flux1-dev-fp8.safetensors` | `checkpoints` | [Comfy-Org/flux1-dev](https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors) |
| `FLUX.1-dev-ControlNet-Union-Pro-2.0.safetensors` | `controlnet` | [Shakker-Labs](https://huggingface.co/Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro-2.0/resolve/main/diffusion_pytorch_model.safetensors) (rename the file) |
| `uso-flux1-dit-lora-v1.safetensors` (style images) | `loras` | [Comfy-Org/USO_1.0_Repackaged](https://huggingface.co/Comfy-Org/USO_1.0_Repackaged/resolve/main/split_files/loras/uso-flux1-dit-lora-v1.safetensors) |
| `uso-flux1-projector-v1.safetensors` (style images) | `model_patches` | [Comfy-Org/USO_1.0_Repackaged](https://huggingface.co/Comfy-Org/USO_1.0_Repackaged/resolve/main/split_files/model_patches/uso-flux1-projector-v1.safetensors) |
| `sigclip_vision_patch14_384.safetensors` (style images) | `clip_vision` | [Comfy-Org/sigclip_vision_384](https://huggingface.co/Comfy-Org/sigclip_vision_384/resolve/main/sigclip_vision_patch14_384.safetensors) |

The **Upscale** workflow needs only the SeedVR2 or UltraSharp files.

## 3. First run

1. Start **ComfyUI Desktop** and wait until it's ready.
2. Double-click **`Start Studio.bat`** (or run `.venv\Scripts\python studio.py`). It opens
   http://127.0.0.1:8765 in your browser; add `--no-browser` to skip that, `--port` to change the port.
3. The first start prepares an input image for each of the 670 unique backgrounds (about a minute) and creates
   `ai\` and `studio\looks.json`. The dot next to **ComfyUI** in the top bar turns green once the Studio reaches
   ComfyUI (at `http://127.0.0.1:8000`; change `comfy_url` in `studio\looks.json` if yours runs elsewhere).
4. Pick a workflow you have models for (Style tab → Look card), select a field such as `elm` (Kalm) and press
   **Preview**. The [Studio guide](studio-guide.md) walks through the rest.

**Style images** go in `studio\styles\` (or use **+ Add** in the Studio). None are included: use images you
have the rights to. The edit-model workflows start with style images off; see
[Workflows](workflows.md#style-images).

## 4. FFNx

The game only loads replacement textures through **FFNx**. From the
[FFNx install guide](https://github.com/julianxhokaxhiu/FFNx/blob/master/docs/how_to_install.md), with the game
folder being `steamapps\common\FINAL FANTASY VII Steam Edition`:

1. Download the latest `FFNx-Steam` zip from https://github.com/julianxhokaxhiu/FFNx/releases and extract it into
   `ff7\workingdir` (next to `data`).
2. Copy `ff7\resources\ff7_1.02\ff7_en` to `ff7\workingdir\ff7_en.exe`.
3. Copy `ff7\workingdir\data\lang-ja\kernel\window.bin` to `ff7\workingdir\data\kernel\windows.bin`.
4. Create `ff7\workingdir\steam_appid.txt` containing `3837340`.
5. Set `game_language` in `FFNx.toml`, then launch `ff7_en.exe` (not the normal Steam launcher).

Then **Install to game** in the Studio copies every field's in-game version to
`ff7\workingdir\mods\Textures\field\<map>\`. Fields you haven't remastered keep the original art.

If you use 7th Heaven instead, switch it and FFNx to the **Canary** channel; stable builds don't run the 2026
version.

## Updating

`git pull`, then restart the Studio. Your versions, looks and settings in `ai\` and `studio\looks.json` are kept.
New features may add settings; looks store only what you changed, so new defaults reach every look that never
changed that setting.
