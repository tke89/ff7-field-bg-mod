# FF7 Field Studio

Remaster **FINAL FANTASY VII**'s pre-rendered field backgrounds with AI, and play them in the game.

FF7's 670 unique field backgrounds are small, pre-rendered images, cut into tiles and packed onto texture
pages. This project takes each one apart, has a local AI model (through [ComfyUI](https://www.comfy.org/))
repaint it in higher resolution and a new style while keeping every object where it was, and packs the result
back into textures the game loads through [FFNx](https://github.com/julianxhokaxhiu/FFNx). Doors, lights and
animations are repainted too, so they match.

It works with the 2026 Steam re-release (Steam app 3837340). The game's own files are never modified.

## What's in it

- **FF7 Field Studio**: a local web app for the whole job.
  - Browse every field, composed exactly as the game draws it, and compare original and new with a split view.
  - Restyle with six workflows: Flux.1, Krea 2, Qwen-Image 2.1, FLUX 3 Klein, Mage-Flow, or upscale only.
  - Keep every render as a version, compare any two, and pick the one the game uses.
  - Save named **looks** (prompt, workflow, settings) and assign them to fields or areas by name (`md*` = Midgar).
  - Queue single fields, selections or the whole game; install to the game with one click.
- **`ff7bgmod.py`**, a command-line kit: export fields as layered images to paint by hand, build textures from
  your art, run images made with any outside generator through alignment and packing, and verify round trips.

## Quick start

You need Windows, a copy of the game on Steam, Python 3.11+, the
[ComfyUI Desktop](https://www.comfy.org/download) app and a recent NVIDIA GPU (16 GB+ VRAM; developed on an RTX 5090).

```bat
git clone https://github.com/<you>/ff7-field-bg-mod
cd ff7-field-bg-mod
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

1. Download the models for at least one workflow into ComfyUI's model folders. See
   [Getting started](docs/getting-started.md#2-models); Qwen-Image 2.1 Turbo plus SeedVR2 is a good first set.
2. Start ComfyUI Desktop, then double-click **`Start Studio.bat`**. The Studio opens at http://127.0.0.1:8765.
   The first start takes about a minute to prepare all 670 fields.
3. Pick a field, press **Preview**, compare, adjust the look, then **Generate final**.
4. Set up FFNx once ([Getting started](docs/getting-started.md#4-ffnx)), then press **Install to game**.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Installing, models, first run, FFNx |
| [Studio guide](docs/studio-guide.md) | Every part of the Studio, and a suggested way to work through the game |
| [Workflows](docs/workflows.md) | The six workflows and upscalers compared, with test results and recommended settings |
| [How it works](docs/how-it-works.md) | Field format, alignment, the states pass, glows, where data is stored |
| [Command line](docs/command-line.md) | `ff7bgmod.py`: export and paint by hand, outside generators, verify |
| [Troubleshooting](docs/troubleshooting.md) | Common problems in the Studio, ComfyUI and the game |

## Project layout

```
studio.py            the Studio server (Flask), port 8765
studio/              the Studio's web page (index.html, app.js, app.css) and your style images (styles/)
ff7bgmod.py          command-line kit
ff7bg/               the library: archive and field parsing (lgp, lzs, field, scripts), texture pages
                     (sheets), AI workflows for ComfyUI (comfy), alignment (align), building game textures
                     (ai), the states pass (states), looks and versions
Start Studio.bat     starts the Studio
```

Created on first run and not part of the repository: `ai/` (prepared inputs, every version), `dist/` (the game
textures to install), `studio/looks.json` (your looks), `work/` (command-line exports).

## Legal

This repository contains no game data and no AI models. You need your own copy of FINAL FANTASY VII; everything
the tools produce comes from your installation. FINAL FANTASY VII is a trademark of Square Enix; this is an
unofficial fan project, not affiliated with Square Enix. Check each AI model's license before using its output
(FLUX 3 Klein, for example, is free only for individuals and companies under $5M revenue).

Format details come from [Makou Reactor](https://github.com/myst6re/makoureactor)'s background code and FFNx's
field texture loader.
