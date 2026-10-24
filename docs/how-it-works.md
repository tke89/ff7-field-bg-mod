# How it works

## Field backgrounds and FFNx textures

A field background isn't stored as one picture.
- It lives in `flevel.lgp` (an LGP archive of LZS-compressed field files). Section 8 holds the background and
  section 3 the palettes.
- The game draws it from 16×16 tiles (32×32 for layers 2–3), packed out of order onto 256×256 texture pages,
  8-bit paletted or 16-bit direct color.
- There are four layers:
  - layer 0, the floor or back wall;
  - layer 1, scenery characters can walk behind (each tile has a depth ID);
  - layers 2 and 3, extra back and front layers, often scrolled.
- Some tiles are blended (add, average, subtract) for glows and light beams.
- Some belong to **toggled groups** (doors, lights, animation frames) that scripts switch on and off.

FFNx replaces whole pages with PNGs of any resolution:

```
mods/Textures/field/<map>/<map>_<page:02>_00.png
```

So a new background has to be cut into the same tiles and packed onto the same pages, scaled up. `ff7bg/sheets.py`
does both directions. `ff7bgmod.py verify` rebuilds fields from their own art and checks the result matches the
game pixel for pixel; it passes for all of them.

Several maps share an identical background (`nivl`, `nivl_2`, `nivl_4`). They're handled once and written to every
copy (`ai/jobs.json` lists the copies).

## What a render goes through

1. **Input.** The field composed the way the game shows it on entry: static tiles plus the states its scripts
   switch on before the first `RET` of the init code. Edit models get an extra 8% blurred margin.
2. **Main render.** The input is enlarged to the render size ([Original art](workflows.md#original-art)), the
   workflow restyles it, and the upscaler takes it to the output scale ([Workflows](workflows.md)).
3. **Alignment** (when on). See below.
4. **Color fit.** A robust 3×4 color transform maps original colors to the new look. It's used wherever original
   art has to sit next to new art.
5. **States pass** (Final). Doors, lights and animation frames; see below.
6. **Game textures.** Every tile's pixels are taken from the right source and packed into pages; see
   *Which pixels come from where* below.
7. **Review sheet and metrics:** original, AI output, in-game result, outline check, outlines kept, drift, filled
   area.

## Alignment

The game's walk-behind cut-outs and walkmesh are fixed to the original layout, so a restyle that drifts by a
pixel or two shows up as halos, or characters walking "through" scenery.

`ff7bg/align.py` finds where the original's structures landed in the AI image. It uses subpixel matches of
gradient-magnitude patches: a coarse pass with a wide search, then a fine pass at 2× near the coarse fit,
dropping ambiguous repetitive patches. It then undoes the drift with **one overall transform**, a homography or
an affine fit, whichever explains the matches better. Straight lines stay straight.

It never bends parts of the image separately. An earlier local warp also "fixed" details the AI had legitimately
redrawn, and made towers and walls wonky. Local changes stay, and the outlines-kept figure shows how much they
matter.

Edit models drift almost entirely globally: a slightly different zoom in x and y plus a shift. On seven test
fields, 1–8 px of drift went down to 0.3–0.7 px. Where the corrected image needs pixels the generator didn't
produce, they're filled from the color-matched original and the field is flagged "edges filled".

## Doors, lights and animations

444 fields have toggled groups, about 1,160 in all.

- **What's on at entry** comes from the field script: `BGON`/`BGOFF` in each entity's init code up to the first
  `RET`.
- **Animations** are groups switched in a script's per-frame loop. The frame order and speed come from the loop's
  `BGON` order and `WAIT`s, in game frames at 30 fps (`ff7bg/scripts.py`). The viewer plays them.

The **states pass** (`ff7bg/states.py`) repaints every state so it matches the restyled scene, and the frames
match each other:

- **One panel per spot.** A group's tiles that sit together form a cluster, with all the states that appear
  there side by side in one image. A group that flickers in three places is three small panels. Panels are packed
  into one to three sheets per field.
- **Only the pixels a state changes** compared with what the main render shows (a needle, a flame, an opening
  door) are repainted and replaced. Everywhere else, a state's tiles are the main render itself, so tile edges
  can't show seams. The repainted spot is merged with Poisson blending, matching its edges to the scene, so it
  can't show as a lighter or darker patch.
- **Frames are derived from a key.** Where the main render already shows a spot, it is the key.
  - Each state starts from the key plus *what that state changes in the original* (color-matched), and is
    repainted lightly: **States repaint**, 0.3 for Flux.1, Krea 2 and MOSS and 0.5 for the edit models. Frames
    keep the main render's design, and only the moving part differs. This doesn't follow the scene's Repaint
    strength: MOSS at 0.5 plus 0.2 turned blin1's flickering hologram into a different staircase in every frame,
    and its open lift doors into glowing panels.
  - When a state replaces most of a spot (at least 30%) that the main render shows differently, a key frame is
    painted first, from the original art pasted into the scene: at 0.85 for the edit models, and at the Repaint
    strength plus **Hidden parts extra repaint** (0.1) for the others. The others are derived from it. Kalm's fan
    is one: it's always spinning in front of an empty window.
- **"All off" panels.** When a state is on in the main render, an extra panel paints what's underneath it, for
  when the game switches it off. It's used only where the state actually covers the scene; around a door, the
  main render stays.
- **Upscale workflow:** each state's original art goes through the same upscaler instead.
- **Preview** skips the pass; states fall back to the original art, color-matched.

## Which pixels come from where

For each tile of each page (`ai.build_pages`):

| Tile | Source |
|---|---|
| Static scenery, layers 0–1 | the main render |
| ...under a state that's on at entry | the main render where it's visible, the "all off" panel where the state covers it |
| Toggled state | its states-pass panel; without one, the main render if the state is on at entry, else original art (upscaled, color-matched) |
| Static glow (blended) | blank, since the glow is baked into the main render; except where nothing solid lies beneath it, where the glow tile carries the new art itself (61 fields, such as blin1's Shin-Ra screen) |
| Toggled glow | original art, upscaled and color-matched, drawn additively as in the game |
| Layers 2–3 | original art, upscaled and color-matched (0.6% of tiles, in 35 fields) |

Tile transparency comes from the original tiles' alpha, upscaled with soft edges, so the walk-behind cut-outs stay
where the depth data expects them.

## Where things are stored

| Path | |
|---|---|
| `ai/jobs.json` | every map: canvas, copies, states on at entry, animations |
| `ai/input/<map>.png` | the input image each workflow gets |
| `ai/versions/<map>/<id>/` | one version: `render.png`, `meta.json` (workflow, look, mode, all settings, results), `states/`, `pages/` (its game textures), `review/`, `thumb.jpg` |
| `ai/fields/<map>.json` | the field's chosen look, its own setting changes, which version is in game, the last error |
| `studio/looks.json` | looks (shared settings plus per-workflow profiles, only what differs from the defaults), assignment rules, the ComfyUI address |
| `studio/styles/` | style images |
| `dist/mods/Textures/field/` | the in-game version of every field: what **Install to game** copies |

Settings resolve in layers: built-in defaults (`ff7bg/comfy.py` `DEFAULT_SETTINGS` and `FAMILY_PRESETS`), then the
look, then the field's own changes. Each layer stores only what differs, so improving a default reaches every
look that never changed it.

## The Studio server

`studio.py` is a Flask app on port 8765 with a single worker thread that runs the queue. Jobs are:
- **final** and **preview** (a new version, then built);
- **build** (re-cut a version, optionally redoing its states).

It talks to ComfyUI over its HTTP and websocket API (`ff7bg/comfy.py`): upload images, queue a workflow, follow
progress, fetch the result. The page (`studio/`) is plain JavaScript and polls the server once a second.

Plug-ins extend it without touching its code: every `studio/plugins/*.py` with a `register(studio)` function is
loaded at start (`studio` is the server module). A plug-in can
- add job kinds that run in the same queue (`JOB_KINDS[kind] = {"run": fn(worker, job), "est": fn(name)}`);
- hear when a field's version in the game changes (`ON_CURRENT`);
- add routes to the Flask app, and return the URL of a script the page loads.

On the page, that script can label its jobs (`JOB_LABELS`), add field list filters (`LIST_FILTERS` plus an option in
the status filter), add a tab to the side panel (a button in `#panelTabs`, a `.tab` with the same `data-tab`), and
follow the page through the `studio:field`, `studio:jobs` and `studio:list` events.

## Palette effects

Some animations change colours, not tiles. A script copies a palette out of VRAM (`STPAL`), changes the copy
(`MPPAL`/`MPPAL2` multiply, `ADPAL` adds, `RTPAL` rotates, `CPPAL` copies) and loads it back (`LDPAL`), and every
tile drawn with that palette changes. Aerith's waterfall (eals_1) is two bright bands moving through palette 8, one
step a frame, repeating every 144 frames. A remastered page is a finished full-colour image, so the waterfall
stands still.

`ff7bgmod.py palettes` finds them ([Command line](command-line.md#palette-effects)). On 21 October 2026 that was
216 fields:
- 212 with an ambient animation;
- 22 with a fade or sequence when you enter;
- 13 with changes during events.

Nearly all are glows (blended tiles), which the main render currently bakes in as they look in the field file.

FFNx can swap pages per palette state: with `enable_animated_textures`, it fingerprints each page as the game
converts it with the current palette (XXH3-64), and loads `<map>_<page>_<palette>_<hash>.png` when it exists.
Making those files means replaying the scripts' palette commands to list every state, then fingerprinting each one
exactly as FFNx does. Work in progress.
