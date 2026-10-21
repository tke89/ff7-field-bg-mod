# Troubleshooting

## The Studio

- **The ComfyUI dot is red.** Start ComfyUI Desktop and wait until it's ready. The Studio expects it at
  `http://127.0.0.1:8000`; if yours runs elsewhere, set `comfy_url` in `studio/looks.json` and restart the Studio.
- **A workflow button shows ⚠.** The workflow needs a file or node ComfyUI doesn't have, with your current
  settings (a depth lock or style images can need extra files). Pick the workflow: the Look card lists what's
  missing, with download links, sizes and the folder each file goes in. Put them there, then press **Check
  again**. A render that would need them stops straight away and names them, and **Try all workflows** skips
  those workflows.
- **Upscayl's models don't show in the Upscaler list.** The Studio looks for an installed Upscayl app, or for
  `tools/upscayl/upscayl-bin.exe` with a `models` folder next to it
  ([Getting started](getting-started.md#optional-imagemagick-and-upscayl)). Press Advanced → **Refresh models**.
  If Upscayl fails, the job's message shows what `upscayl-bin` said; a Vulkan error usually means a graphics
  driver update is needed.
- **A job fails with "Value not in list".** A setting names a model file ComfyUI doesn't have. The message names
  the setting; pick another file in Advanced, or download that one.
- **A job fails with a node error** (for example `TextEncodeQwenImage21` or `ReferenceMultiLatent` unknown).
  ComfyUI is too old for that workflow; update ComfyUI Desktop (0.42 or newer).
- **"not put in game: only N% of the original outlines kept".** The AI drew a different scene, usually because
  of style images on an edit model ([Workflows](workflows.md#style-images)). The version is in the strip; turn
  style images off or try another seed.
- **The field is flagged "layout changed".** Fewer than 80% of the original outlines survived. Check the
  walk-behind areas with **Walk-behind** (O). Lower the repaint strength (Flux.1, Krea 2), try another seed, or use
  an edit model with auto-align.
- **"edges filled".** The generator cropped part of the scene, and those edges were filled from the original art.
  Increase **Border padding** (edit models, Advanced) or check auto-align is on.
- **The viewer shows "raw render, not built".** The version hasn't been cut into game textures yet. **Rebuild**
  it, or **Use this version**.
- **Doors, lights or animations look wrong.** **Redo states** on the version repaints them with the current
  settings. A lower **States repaint** keeps frames closer to the scene.
- **Time estimates are off.** They're calibrated on an RTX 5090. Slower cards take proportionally longer, and the
  first job after switching workflows also loads the models.
- **Restarting the Studio** drops the queue (jobs aren't saved), but never versions or settings.

## In the game

- **FFNx isn't loading the textures.** Launch `ff7_en.exe` from `ff7\workingdir`, not the normal Steam launcher,
  and check **Install to game** copied files to `ff7\workingdir\mods\Textures\field\`.
- **See which files the game requests.** In `FFNx.toml` set `trace_loaders = true` and
  `show_missing_textures = true`. `FFNx.log` then lists every texture path it tries.
- **See how FFNx names the textures.** `save_textures = true` dumps the game's own pages into `mods/Textures`.
  Each dump uses a single palette, so some tiles look miscolored; that's expected.
- **Characters drawn in front of or behind the wrong things.** Occlusion comes from the original tile layout and
  depth data, not from the art. Keep large structures where they were (check outlines kept and **Walk-behind**),
  or edit the walkmesh and tiles with [Makou Reactor](https://github.com/myst6re/makoureactor).
- **Faint seams at tile edges at high scales.** This comes from texture filtering on packed tiles. Hard edges in the
  art at tile boundaries make them less visible.

## Limits

- Pages that use 16-bit direct color (51 of about 3,300 pages) are named by FFNx after an internal conversion
  this kit doesn't model. If one doesn't load, check `FFNx.log` with `trace_loaders`.
- Animated palette effects (blinking lights done by palette swaps) are handled by FFNx's hashed "animated
  textures" and aren't produced here.
- Layers 2–3 and toggled glows keep their original art (upscaled and color-matched): 0.6% of tiles plus the
  toggled glows.
