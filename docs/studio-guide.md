# Studio guide

The Studio has four parts:
- **Top bar:** ComfyUI status, the queue, Looks, Generate all missing, Install to game.
- **Field list** on the left.
- **Viewer and version strip** in the middle.
- **Right panel:** the Style tab, the Layers & states tab, and the Generate footer.

## A suggested way to work

1. **Find a style on a few fields.** Pick fields with different content: Kalm (`elm`), Nibelheim (`nivl`), the
   train station (`md1stin`), a Shinra interior (`blin1`). Press **Try all workflows** on each. It previews the field
   with every installed workflow and adds them all as versions without touching what's in game. Compare them in
   the version strip and with Split.
2. **Tune the default look** on those fields with **Preview**: the prompt, the repaint or style sliders, the
   output scale. The default look is what every field uses unless told otherwise.
3. **Make looks for areas that need something different** (Midgar's metal versus Kalm's timber). **Save as new
   look…** turns the current field's settings into a look. In **Looks**, add rules like `md*` → "Midgar" so every
   matching field uses it.
4. **Render in batches.** Filter the list (for example by look, or "Not generated"), **Select all shown**, then
   **Generate final**. Or use **Generate all missing** for everything without a final. You can keep browsing
   while the queue runs.
5. **Check the flagged fields.** The "Needs a look" filter shows fields whose render changed the layout (fewer
   than 80% of the original outlines kept) or needed edges filled. Look at them with **Walk-behind** on, try
   another seed or workflow, and use the version you like.
6. **Install to game** and play (FFNx needed, see [Getting started](getting-started.md#4-ffnx)).

## Field list

All 670 unique backgrounds. Maps that share a background (`nivl`, `nivl_2`, `nivl_4`) are listed once with
"+N copies"; whatever you make is used for all of them.

- **Status dots:** gray = nothing yet, purple = has versions but none in game, blue = a preview is in game,
  green = a final is in game, yellow = needs a look, red = the last run failed, pulsing = rendering now.
- **Tags:** the field's look (when it isn't the default) and "field changes" when the field changes settings for
  itself.
- **Search** by name, **filter** by status or look.
- **Select several:** tick the checkboxes, Ctrl+click to toggle, Shift+click for a range, or **Select all
  shown**. A bar at the bottom of the list shows how many and the estimated time; **Generate final** or **Preview**
  queues them all, each with its own look. Esc clears the selection.
- ↑ / ↓ move through the list.

## Viewer

The field is drawn exactly the way the game composes it, from the actual game textures.

| Control | |
|---|---|
| **Original / New / Split** (keys 1 / 2 / 3, Space toggles) | Original art, the selected version, or both side by side (drag the line) |
| Split menus | Pick what's on each side: the original or any version |
| Wheel, drag | Zoom, pan. **Fit** (F), **1:1** (original pixels), **Native** (the new texture's pixels) |
| **Animate** (A) | Plays the field's animations (fans, fires, flickering lights) in both views |
| **Walk-behind** (O) | Outlines where characters are hidden behind scenery; the new art should sit on those lines |
| **Pixelated** | Shows the original's pixels sharp instead of smoothed |

The badge in the corner says what you're looking at. "raw render, not built" means the version hasn't been
cut into game textures yet (layer and state toggles don't apply to it).

## Versions

Every render is kept as a version in the strip under the viewer. The one marked ★ is in game.

- **Click** a version to view it as New; ← and → step through them. **Shift+click** puts a version on the left
  of Split, to compare any two.
- The line above the strip shows how it was made: workflow, look, scale, how many of the original outlines it
  kept, alignment, states, seed.
- **Use this version** puts it in game. If it never had the states pass, that runs in the queue afterwards.
- **Use *workflow* for …** switches your look (or this field) to the workflow that made the viewed version.
- **Rebuild** cuts the version into game textures again (no AI run; useful after an update).
- **Redo states** repaints just its doors, lights and animation frames with today's states settings, keeping the
  main image.
- **Review sheet**: the original, the AI output, the in-game result and an outline check, side by side.
- **Delete** removes a version (not the one in game).
- **+ Import image** adds an image made elsewhere (say with ChatGPT, from the **input image** link next to it) as
  a version. It's aligned to the original and cut into textures in the queue.

A new final goes in game automatically. A preview goes in game only if nothing better is there. Try-all versions
never do. As a safety net, a version that keeps fewer than 60% of the original outlines (the AI drew a different
scene) doesn't go in game by itself.

## Style tab

### Look bar

A **look** is a named set of settings: workflow, prompt, style images, output, model files. Each workflow keeps
its own tuning inside a look, so switching a look from Qwen to Flux and back loses nothing.

- **Look** menu: **Automatic** (the first matching rule, else the default look) or a specific look for this field.
  **Manage looks…** opens the Looks manager.
- **Edit look "…"** / **This field only**: what the controls below change. Editing the look changes every
  field using it. This field only stores changes for the selected field on top of its look.
  - • marks settings the edited layer changes from the built-in defaults.
  - ◆ (while editing the look) marks settings this field overrides for itself.
- **Save as new look…**: makes a look from this field's current settings (look plus its own changes) and uses it
  here.
- **Clear field changes**: drops the field's own changes.

### Look card

- **Workflow:** Flux.1, Krea 2, Qwen 2.1, FLUX 3, Mage-Flow, FIBO 2, MOSS or Upscale. The edit models (Qwen, FLUX 3, Mage-Flow, FIBO 2)
  and MOSS have a fast/full switch underneath (Turbo · 8 steps / Full · 25 steps and so on). ⚠ marks a version whose model
  file isn't installed. See [Workflows](workflows.md) for which to use.
- **Prompt:** describe the rendering, not the content. Naming materials ("timber, tiles") turned Midgar's
  train station into a medieval alley in testing.
- **Describe the scene in the prompt:** adds the field's own description (made once by a vision-language
  model, editable here, **Describe again** to remake it). See [Workflows](workflows.md#scene-descriptions).
- **Style images:** click to toggle, **+ Add** to upload. Off by default for the edit models, which copy content
  from them.
- **Sliders:** only the ones the workflow uses. Flux.1 has repaint strength, structure lock, lock until, edge
  sensitivity, prompt guidance and style influence (and a depth lock); Krea 2 has repaint, depth lock and style
  influence; MOSS has repaint and its depth lock; Qwen has a depth lock; the edit models have style influence.
- **Seed:** the same seed and settings give the same result. **New seed** for a different take.

### Output

- **Output scale:** the texture resolution versus the original. The game shows 320×240 original pixels, so 4×
  is 1280×960, 6× suits 1440p and 8× is close to 4K. Textures take about 1 MB per megapixel (roughly 8 GB for
  the whole game at 8×).
- **Upscaler:** enlarges the render to the output size. The options are SeedVR2 (a diffusion upscaler, finer
  detail), an ESRGAN model like 4x-UltraSharp, Lanczos (ImageMagick), or one of Upscayl's models. Lanczos and
  Upscayl run outside ComfyUI ([Workflows](workflows.md#upscalers)). Upscayl's models appear here once Upscayl is
  installed. In the Upscale workflow with SeedVR2, "SeedVR2 works from the original pixels" skips the upscaler
  before it.
- **Detail pass** (Flux.1, Krea 2): repaints the full-size image in tiles for real texture at high scales. Slower.
- **States pass:** repaints doors, lights and animation frames (Final only). See
  [How it works](how-it-works.md#doors-lights-and-animations).
- **Keep original colours:** pulls the render's colours back toward the original palette, applied when the
  version is built ([Workflows](workflows.md#keeping-the-original-colours)).
- **Renders per field** and **Retry when under 80% of outlines:** several seeds per Final, keeping the one that
  keeps the layout best ([Workflows](workflows.md#several-tries-per-field)).
- **Auto-align:** undoes the small zoom and shift edit models introduce (one correction for the whole image, so
  straight lines stay straight). On for the edit models, usually off for Flux.1.

### Advanced (collapsed)

Render steps, CFG, render size (megapixels the model works at), edit margin, detail- and states-pass tuning
(**States repaint** for the edit models), **Original art** (the upscaler that enlarges the original for the AI,
and by how much: [Workflows](workflows.md#original-art)), the model files (checkpoint, or diffusion model + text encoders + VAE),
the Flux ControlNet, an extra LoRA, SeedVR2's model, VAE and color correction. **Refresh models** re-reads the
lists from ComfyUI after you add files.

## Layers & states tab

Turn layers 0–3 and glow effects on and off, and switch the field's toggled groups (doors, lights, animation
frames): **default** (what the game shows when you enter), **all**, **none**, or single states. Animated groups
show "playing" while **Animate** is on; turn it off to pick frames by hand.

## Generate (bottom of the panel)

Shows the workflow and look the field renders with, plus the size and estimated time of a Final and a Preview.

- **Preview:** up to 4×, no detail or states pass. For trying styles.
- **Generate final:** the output scale, with the detail and states passes if they're on.
- **Try all workflows:** a preview with every installed workflow, added as versions; what's in game is unchanged.
- **Cancel queue** stops the current job and clears the queue.

## Top bar

- **ComfyUI** dot: green when the Studio can reach ComfyUI.
- **Queue:** what's running, how much is queued and the time left. Click it for the queued jobs (× removes one)
  and the finished ones (click a name to open that field).
- **Looks:** the Looks manager.
  - Rename, duplicate or delete looks, and pick the default.
  - **Assign by field name** rules map name patterns to looks. `md*` is all of Midgar, `mds*` the slums, `elm*`
    Kalm, `nivl*` Nibelheim. The first matching rule wins. A field given a look directly keeps it.
- **Generate all missing:** every field without a final in game, each with its own look. It shows the count per
  look and the estimated time first.
- **Install to game:** copies every field's in-game version to `ff7\workingdir\mods\Textures`.

## Keyboard

| Key | |
|---|---|
| 1 / 2 / 3, Space | Original / New / Split, toggle original and new |
| F | Fit |
| A | Animate |
| O | Walk-behind outlines |
| ↑ / ↓ | Previous / next field |
| ← / → | Previous / next version |
| Esc | Close the queue popup, clear the selection, close Looks |
