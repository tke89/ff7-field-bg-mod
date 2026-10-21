# Workflows

A workflow is the AI model and graph that restyles a field. Pick it in the Look card; each look keeps separate
settings for every workflow. Model files and download links are in
[Getting started](getting-started.md#2-models).

## Which one?

| Workflow | Keeps the layout | Look | Speed (render, 4×, RTX 5090) | Use it for |
|---|---|---|---|---|
| **Qwen 2.1 Turbo** | very well, after alignment; near-exactly with its depth lock | rich, stylised, warm | about 8–12 s | the default; nicest overall in testing |
| **Qwen 2.1 Full** | very well, after alignment | Turbo plus more detail (brick, ground texture) | about 15–20 s | finals where detail matters |
| **FLUX 3 Klein** | the best (0.1 px drift) | clean, closer to the original colours, less painterly | Distilled about 15–20 s (estimated), Base 50–85 s | fields where exact layout matters most |
| **Mage-Flow Turbo** | very well | keeps the original colours; flattens some surfaces | about 2–5 s | fast drafts and big batches |
| **FIBO 2 Edit Turbo** | very well (99% of outlines on Kalm) | clean and neutral; loses night grading | about 13 s | a clean, realistic take |
| **MOSS-Image** | well, with its depth lock | strong painterly restyle | SFT about 7–15 s, Turbo about half | bold restyles that keep the scene |
| **Flux.1** | well (ControlNet edge lock, plus an optional depth lock) | depends heavily on settings | about 15–20 s | style images (USO), the detail pass |
| **Krea 2 Turbo** | only at low repaint (a depth lock is wired in, untested) | good, stylised | about 9–19 s | experimenting |
| **Upscale** | exactly (no restyle) | the original, sharper | about 2–4 s | a faithful HD version, or a baseline |

The **Try all workflows** button previews a field with every installed workflow at once, which is the quickest
way to compare.

Throughout these docs, "outlines kept" is the share of the original's strong edges that still have an edge
within 2 original pixels in the result. It's shown under the viewer for every version. Below 80% the field is
flagged, because the walk-behind areas (where characters disappear behind scenery) stop matching the art.

## Qwen-Image 2.1 (Turbo, Full)

An image *edit* model. The original goes in as `<image1>` and the instruction becomes *"Restyle `<image1>`
while keeping the exact same composition, camera angle, layout and every object in the same place: your
prompt"*. It follows ComfyUI's Qwen-Image 2.1 image-edit templates. The optional prompt-enhancer model is
skipped.

- **Turbo:** a fixed 8-step schedule, CFG 1. **Full:** 25 steps. Full adds noticeably more detail in about
  twice the time.
- Renders at about 3 MP (Render size), close to its native 2K, then the upscaler takes it to the output scale.
  Repaint strength, steps (Turbo) and the detail pass don't apply.
- Edits zoom in by 1–3% (slightly differently in x and y) and shift a few pixels; Full more than Turbo. So the
  input gets an 8% blurred margin (Edit margin), which comes back with the result, and **auto-align** is on.
  Measured on seven fields, the drift was 1–8 px (median, in original pixels). One overall transform explains
  almost all of it: after alignment 0.3–0.7 px remain, and no edge needs filling from the original.
- Outlines kept after alignment: Nibelheim 97–99%, the station 99%, Kalm 88–95%.
- **Depth lock** (off by default): Qwen-Image 2.1's Fun ControlNet Union, built into ComfyUI, fed with
  Depth Anything 3's depth map. On Kalm the drift went from 6.6 px to 0.2 px before alignment, and outlines
  kept from 98.9% to 99.9%, with every object in place. In exchange it restyles less: the result stays closer
  to the original's look. Use it on fields where Qwen moves or changes things. It needs the ControlNet file
  ([Getting started](getting-started.md#qwen-image-21-turbo-and-full)).

## FLUX 3 Image Klein (Distilled, Base)

Black Forest Labs' 5.6B edit model (October 2026), following ComfyUI's FLUX 3 Klein image-edit template.

- The original goes in as `<ref_image_0>` through ReferenceMultiLatent.
- It renders up to 4K natively; the default render size is 4 MP.
- **Distilled** is 16 steps, **Base** 50.
- The most layout-faithful restyle: 0.12–0.15 px drift before any alignment, 98–99% of outlines kept.
- It's clean and warm, but stays closer to the original colours and is less painterly than Qwen, with smoother
  ground.
- Gated download; the license is free for individuals and companies under $5M revenue.

## Mage-Flow (Turbo)

Microsoft's 4B edit model.

- **Turbo:** 4 steps, renders in 1–3 s, barely drifts (0.3–0.6 px before alignment) and keeps 96–99% of
  outlines. It keeps the original colours more than Qwen, but flattens some surfaces: Kalm's plaza went smooth.
- **Full** (30 steps, CFG 5) redesigns the scene freely, even at CFG 2: the train station became a golden tunnel.
  It's in the switch, labelled "redesigns", but isn't useful here.

## FIBO 2 (Edit Turbo, Dev)

Bria's 8B FIBO 2.0 (released 20 October 2026), the top open-weights image editor on Artificial Analysis at
release. It runs through ComfyUI's native support (`TextEncodeFibo2`, PR #17240, from late October 2026). On an
older ComfyUI the workflow button shows ⚠ and **Try all workflows** skips it.

- The original goes in as `<image_1>`. The result size is set explicitly; the default render size is 2 MP,
  against its native size of about 1 MP. The instruction is plain text; FIBO also understands Bria's structured
  JSON prompts (VGL).
- **Edit Turbo:** 4 steps, CFG 1, negative prompt has no effect. **Dev:** 30 steps, CFG 5 (optional download,
  `fibo2_dev_int8_convrot`).
- It uses the bf16 Qwen3-VL-4B text encoder, because the int8 one has a vision bug (ComfyUI issue #17248).
- License: CC BY-NC 4.0, non-commercial only.
- Tested on Kalm (Edit Turbo): a 13 s render with SeedVR2 before and after, 99% of outlines kept. It came out
  clean and evenly lit, like a neutral 3D render: the layout holds, but the original's night grading is gone.

## MOSS-Image (SFT)

OpenMOSS's 5B MOSS-Image (released 16 October 2026) is a text-to-image model, so like Krea 2 it repaints the
original partially (**Repaint strength**) instead of editing it. No style images.

- **Depth lock** (0 turns it off). Depth Anything 3 measures the original's depth
  and MOSS's color/depth control adapter holds the scene to it, through ComfyUI's `MOSSImageControl` node. It
  needs two extra files ([Getting started](getting-started.md#moss-image)).
- Kalm, outlines kept:

  | Repaint | No lock | Depth lock |
  |---|---|---|
  | 0.35 | 97% | – |
  | 0.5 | 91% | 91–93% |
  | 0.6 | 85% | 84% |
  | 0.7 | 67%, a different town | 73–76%, the same town |

  The numbers hide the difference. Without the lock, objects change from about 0.6 (the tower, the machinery)
  and at 0.85 MOSS paints another scene entirely. With it, the town stays the same while the brushwork, light
  and materials change. The default is 0.5 with the lock at its trained strength 1.
- Strength 2 looked no better; 4 turns sketchy. The adapter's color-region half needs object segmentations,
  which FF7's backgrounds don't split into, so only depth is used.
- **SFT** (default), settings from ComfyUI's template: 25 steps, CFG 2.5, `er_sde`, `simple`. The sampler runs
  every step whatever the repaint strength: about 7 s at 1.2 MP.
- **Turbo**: an 8-step merge at CFG 1 (`moss_image_sft_turbo_int8_convrot`, a community file from
  toxicdog/MOSS-Image-SFT-Turbo-INT8-ConvRot, Apache 2.0). It ran in about half SFT's time on Kalm, looked
  much the same at repaint 0.2, and works with the depth lock. The official few-step adapters need a custom
  node and the bf16 model, so they aren't used.
- License: Apache 2.0 (the model, the adapter and Depth Anything 3 Mono Large).

## Flux.1

Flux.1-dev img2img, held to the original layout by **ControlNet Union Pro 2.0** (canny edges of the original).
Style comes from the prompt and from **USO** style images.

1. Original → 4x-UltraSharp → working size (about 1.4 MP) → Flux at the repaint strength, with the structure
   lock.
2. Upscaler → output scale.
3. Optional **detail pass**: Flux repaints the full-size image again in 1024 px tiles at low strength and 8 steps.
   An upscaler alone only sharpens the ~2× render; the detail pass paints new texture (stone grain, grime,
   lettering) at full size. It takes about 3.7 s per tile, about 16 tiles for an average field at 8×. Strength
   0.35–0.4 adds real texture; above about 0.45, neighbouring tiles start to disagree.

These defaults were tuned by hand:

| Setting | Value |
|---|---|
| Repaint / structure lock / lock until | 0.2 / 1.2 / 75% |
| Guidance / edge sensitivity / steps | 5 / 0.2 / 20 |
| Style image influence | 0.5 |
| Output scale | 4×, detail pass off |

Auto-align stays off for Flux.1. The structure lock already holds the layout, and before alignment became
global-only, warping bent redesigned textures.

**Depth lock** (off by default): the same Union Pro 2.0 ControlNet also takes depth, so a second control with
Depth Anything 3's depth map goes on top of the edge lock. At the default repaint of 0.2 it changes little. At
0.45 it's what keeps the scene: without it Kalm's central tower turned into a dark, spiky creature; with it
(0.6), the tower and its machinery stayed.

## Krea 2 Turbo

8 steps. Style images go through Krea's style-reference LoRA (Style image influence is its strength; it was
trained on 1–2 images). Without its depth lock, the layout is held only by a low repaint strength.

| Repaint | Outlines kept (Kalm) |
|---|---|
| 0.2 | 96% |
| 0.35 | 88% |
| 0.5 | 71% |

Midgar's station dropped to 71% already at 0.35, so stay around 0.2–0.3. A render takes about 9 s without style
images and about 19 s with two.

**Depth lock** (off by default). Krea 2 has no ControlNet of its own; the community depth ControlNet-LoRA
([Patil/Krea-2-depth-controlnet](https://huggingface.co/Patil/Krea-2-depth-controlnet), Krea 2 Community
License) adds one, the same idea as MOSS's: Depth Anything 3 measures the original's depth and the render keeps
it. 1 holds strictly, 0.6 leaves more freedom. ComfyUI doesn't run it natively: it needs the
[comfyui-krea2-controlnet](https://github.com/facok/comfyui-krea2-controlnet) node pack, installed with ComfyUI
Manager ([Getting started](getting-started.md#krea-2-turbo)). Without it the slider is dimmed, and a render with
the lock on stops with a message. Not tested here yet.

## Upscale

No restyle: the original enlarged by the upscaler chosen under Output, as a faithful HD version or a baseline to
compare against. With the states pass on, doors, lights and animation frames go through the same upscaler. A Kalm
final at 4× takes about 7–14 s including its states.

| Upscaler | Result |
|---|---|
| **4x-UltraSharp** | the most faithful, but grainy |
| **UltraSharp, then SeedVR2** ("works from the original pixels" off) | clean and close to the original; smooths flat areas like the ground |
| **SeedVR2 from the original** | the most detail, but invents texture: Kalm's cracked plaza became cobblestones |
| **Lanczos (ImageMagick)** | a plain resize: soft, nothing added or invented; about 3 s at 4× with the states, no ComfyUI needed |
| **Upscayl** models | not tested here yet; no ComfyUI needed |

## Upscalers

Every workflow ends with an upscaler (Output → **Upscaler**) that takes the render to the output scale.

- **SeedVR2** is a one-step diffusion upscaler that color-matches back to its input (`lab` by default). It gives
  finer, more natural detail than UltraSharp's sharpening at about the same speed: 3.4 s to 4×, 23 s to 8×
  (5120×3840).
  - **3B** stays smooth and faithful. **7B** adds crisper, hatched texture (cobbles, brickwork) and is about 3 s
    slower at 8×.
- **ESRGAN models** (4x-UltraSharp and others in `upscale_models`) are faster and sharper, but grainier.
- **None** uses a plain Lanczos resize inside ComfyUI.
- **Lanczos (ImageMagick)** and **Upscayl** run on your PC outside ComfyUI:
  - **Lanczos (ImageMagick)** is ImageMagick's `-filter Lanczos` resize to the output scale (2×, 4× or any
    other). Without ImageMagick installed, Pillow runs the same filter. That gives exactly the same image as
    **None**: on Kalm not one pixel differed. With ImageMagick installed, it's ImageMagick itself.
  - **Upscayl** uses Upscayl's models through its `upscayl-bin` (Vulkan): Upscayl Standard and Lite, High
    Fidelity, Digital Art, Remacri, UltraMix Balanced and UltraSharp. They enlarge 4×; other output scales get a
    Lanczos resize after them. To set it up, see [Getting started](getting-started.md#optional-imagemagick-and-upscayl).
    It isn't tested on real Upscayl yet; the wiring was tested with a stand-in tool.
  - In the **Upscale** workflow they're the whole job, and ComfyUI doesn't need to run.
  - In the other workflows, ComfyUI returns the render at its working size and the Studio enlarges it, the
    states too. With the detail pass on, the render is enlarged with a plain Lanczos resize inside ComfyUI
    before the pass, instead.

## Scene descriptions

With **Describe the scene in the prompt** (Look card), the prompt gets a short description of the field: what
it shows, what things are made of, the ground and the light. Qwen3.5 9B (MOSS-Image's text encoder, through
ComfyUI's TextGenerate) writes it once per field, in about 2 s, the first time a render needs it. It's saved
with the field and can be edited; **Describe again** replaces it.

The style prompt tells a model how to paint; the description tells it what it's painting. On Kalm:

- MOSS at 0.5 painted the wavy cobbles as water; with the description they stayed cobbles.
- Krea 2 at 0.3 added people and turned a sign into a big pink jug; with the description the scene stayed
  empty, and the middle became the barrels and pipes it describes.

The descriptions aren't perfect: Kalm's says "dusk or dawn" for a night scene. Correct them where it matters.
Edit models see the image anyway and need it least.

## Keeping the original colours

**Keep original colours** (Output) pulls a render's colours back toward the original's palette. A colour
transform is fitted from the render to the original, so the new detail and shading stay; the same transform
goes on every state panel, so doors and frames keep matching. FIBO 2 turned Kalm into an evenly lit day
scene; at 70% its blue night came back. It's applied when a version is built, so **Rebuild** tries another
value without rendering again.

## Several tries per field

**Renders per field** (Output, Final only) renders a field with that many seeds and keeps the one that keeps
the most original outlines; the others are deleted, and the kept one lists all the scores. **Retry when under
80% of outlines** adds one more try when even the best falls short, with a new seed and, for workflows with a
repaint strength, 0.1 less of it. Each try costs a full render; the states pass runs once, for the winner.

## Original art

Before any AI sees it, the 1× original art is enlarged to the size the model renders at. Advanced → **Original
art** sets how, per workflow:

- **Upscaler**: any of the upscalers above. 4x-UltraSharp is the default; SeedVR2 adds detail to the original
  but can invent texture; Lanczos keeps it soft. ImageMagick and Upscayl run before ComfyUI.
- **Scale**: how many times its size the original is enlarged, which is also the size the AI renders at.
  **Auto** uses **Render size** (the largest whole multiple within that many megapixels: 2× for most fields at
  1.4 MP). The panel shows what that comes to for the field. Flux.1 on Kalm took 44 s at 3× instead of 18 s
  at 2×. Most models were trained around 1–2 MP (the edit models up to 4) and start repeating details well
  above that.

Doors, lights and animation frames (the states pass) are repainted from the main render plus the original art
blended in, so this setting doesn't apply to them.

Until 21 October 2026 the original art went through the Output upscaler. Looks that relied on that were
converted on the first start, so their results don't change.

## Style images

Style images live in `studio/styles/` and are toggled per look in the Look card.

- **Flux.1** (USO) and **Krea 2** (style LoRA) were built for style images and use them as style.
- **The edit models take content from them**, so they start with style influence 0 (off).
  - With Kalm's remaster as a style image, Qwen 2.1 repainted the Ancient Capital (`ancnt1`) as Kalm, keeping 37%
    of its outlines. It still did so with the images scaled down to 0.35 MP and the instruction saying to borrow
    only their style.
  - Mage-Flow pasted timber framing over everything.
  - FLUX 3 mostly ignored them.

  If you turn them on anyway, they're scaled down and the instruction is strict, and the safety net keeps a
  version that drew a different scene out of the game.

## Prompts

Describe how it should be rendered, not what's in it; the image already says what's in it. Words like "timber"
or "tiles" pull every field toward those materials. A good start:

> A modern high-end remaster of this video game scene, drawn by hand with a stylised painterly look. Crisp
> detailed geometry, warm glowing practical lights, soft global illumination, ambient occlusion, light
> atmospheric haze, saturated cinematic color grading, sharp focus. Same camera, same layout, every object in
> exactly the same place.

Use looks to vary the prompt by area (Midgar, Kalm, the Northern Crater) rather than rewriting it per field.

## Adding another model family

Workflows live in `ff7bg/comfy.py`. Each one needs:
- an entry in `FAMILIES` and `FAMILY_PRESETS` (default model files and settings);
- for edit models, a branch in `_edit_conditioning` and `_edit_sample`; for others, `_conditioning` (and
  `_model_chain` for anything that patches the model);
- an entry in `looks.FAMILIES`;
- the UI's `FAMILIES`, `LOOK_SLIDERS` and help texts in `studio/app.js`;
- time estimates in `Project.estimate`.

ComfyUI's own templates (`comfyui_workflow_templates_json` in ComfyUI's Python environment) are the best
reference for each model's graph.
