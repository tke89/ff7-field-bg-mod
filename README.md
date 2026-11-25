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
  - Restyle with eight workflows: Flux.1, Krea 2, Qwen-Image 2.1, FLUX 3 Klein, Mage-Flow, FIBO 2, MOSS-Image, or
    upscale only. Choose how the original art is enlarged for the AI (upscaler and scale).
  - Hold the layout with depth locks (Depth Anything 3), describe each scene in the prompt, keep signs and
    lettering readable, keep the original colours, and keep the best of several renders per field.
  - Upscale with SeedVR2, ESRGAN models, Lanczos (ImageMagick) or Upscayl's models.
  - Keep every render as a version, compare any two, and pick the one the game uses.
  - Save named **looks** (prompt, workflow, settings) and assign them to fields or areas by name (`md*` = Midgar).
  - Queue single fields, selections or the whole game; install to the game with one click.
- **`ff7bgmod.py`**, a command-line kit: export fields as layered images to paint by hand, build textures from
  your art, run images made with any outside generator through alignment and packing, and verify round trips.

## Quick start

You need Windows, a copy of the game on Steam, Python 3.11+, the
[ComfyUI Desktop](https://www.comfy.org/download) app and a recent NVIDIA GPU (16 GB+ VRAM; developed on an RTX 5090).

```bat
git clone https://github.com/tke89/ff7-field-bg-mod
cd ff7-field-bg-mod
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

1. Download the models for at least one workflow into ComfyUI's model folders. See
   [Getting started](docs/getting-started.md#2-models); Qwen-Image 2.1 Turbo plus SeedVR2 is a good first set.
2. Start ComfyUI Desktop, then double-click **`Start Studio.bat`**. The Studio opens at http://127.0.0.1:8765.
   The first start takes about a minute to prepare all 670 fields.
3. Pick a field, press **Preview**, compare, adjust the look, then **Generate final**.
4. Set up FFNx once ([Getting started](docs/getting-started.md#4-ffnx)), then press **Settings ▾ → Install all to
   game**. From then on, each field's new version goes into the game as soon as it's made.

### FINAL FANTASY VIII Remastered (early)

**`Start FF8 Studio.bat`** opens the same Studio for FF8 Remastered (Steam) at http://127.0.0.1:8767. It's a project of
its own: renders, field settings and looks live in `projects/ff8`, apart from FF7's, and both Studios can run at once.
Its looks start as a copy of FF7's. The game loads the new art through
[Demaster](https://github.com/MaKiPL/FF8_demaster) (installed in the game folder, with the game's archives unpacked
into `DEMASTER_EXP`); the Studio installs each field's pages into `DEMASTER_EXP/textures/field_bg`.

Each field opens on what its scripts show at entry, and animations (screens, flames, the library's page turning) come
from its scripts too. Where a script decides by a story flag or by where you came from, the Studio takes the script's
"otherwise" branch. FF8's colour effects (pulsing lights) are applied by the game over the new art, so they need
nothing here. The field list shows each field's save-screen place name ("B-Garden- Infirmary"). Not read: seven test
and logo rooms in older file layouts.

**Play in game** (above the picture) restarts the game standing in the open field, in about half a minute: the Studio
writes a copy of one of your saves moved to that field into slot 2, save 30, restarts the game and loads it. The story
state is that save's (by default `slot1_save04.ff8`; set `"ff8_jump_base"` in `projects/ff8/ai/prefs.json` to use
another). The game needs to run in a window for it (its config.txt, screenmode 2) and Steam must be running.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Installing, models, first run, FFNx |
| [Studio guide](docs/studio-guide.md) | Every part of the Studio, and a suggested way to work through the game |
| [Workflows](docs/workflows.md) | The workflows and upscalers compared, with test results and recommended settings |
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
                     (ai), the states pass (states), looks and versions; game.py: FF7's own parts for the Studio
ff8bg/               FF8 Remastered: archives (archive), backgrounds as the engine's tiles (field), field scripts
                     (scripts: entry states, animations, place), text and place names (text), Demaster's page
                     layout and install (pages), going to a field by save (saves, play, control), and its
                     Studio game (game)
Start Studio.bat     starts the Studio (FF7)
Start FF8 Studio.bat starts the FF8 Studio
```

Created on first run and not part of the repository: `ai/` (prepared inputs, every version), `dist/` (the game
textures to install), `studio/looks.json` (your looks), `work/` (command-line exports), `projects/ff8/` (the FF8
Studio's own ai/, dist/ and looks).

## License and legal

The code and documentation are in the public domain ([the Unlicense](LICENSE)): use them for anything, no
attribution needed.

This repository contains no game data and no AI models. You need your own copy of FINAL FANTASY VII; everything
the tools produce comes from your installation. FINAL FANTASY VII is a trademark of Square Enix; this is an
unofficial fan project, not affiliated with Square Enix. Check each AI model's license before using its output
(FLUX 3 Klein, for example, is free only for individuals and companies under $5M revenue).

Format details come from [Makou Reactor](https://github.com/myst6re/makoureactor)'s background code and FFNx's
field texture loader.
