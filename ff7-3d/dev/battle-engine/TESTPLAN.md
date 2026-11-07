# FFNx-3d battle look: test plan (for the main session)

What changed and the formats: `FORMAT.md`. The engine change: `patches/battle_look.diff` (against the fork as it was
on 2026-11-07 09:50; `patch -p1 --binary < battle_look.diff` in `Desktop/FFNx-3d` reproduces `patches/files/`
byte for byte, CRLF files kept; or copy `patches/files/*` over the fork). Two new files: `src/ffnx3d_battle.h/.cpp`
(CMake's CONFIGURE_DEPENDS glob picks them up). Shaders changed (`misc/FFNx.lighting.frag`, `misc/FFNx.lighting.sh`),
so build with the shader step and install the shaders too (`install_ffnx3d.sh` does).

Checked here: the patch applies cleanly; `FFNx.lighting.frag` compiles with the fork's shadercRelease for d3d11
(flat and smooth), GL 330 and SPIR-V; the 8 changed/new C++ files pass MSVC `/Zs` with the project's flags. Not
built, linked or run.

Test data: `testdata/` (battle scene oq = STAGE10, Mako Reactor 1 inside; formation 310, two Grunts; camera at
(9187, -904, -2001) looking at (0, -592, 300), so z runs across the screen). Put in / take out with the Studio venv:

    "C:\Users\you\Desktop\ff7-field-bg-mod\.venv\Scripts\python.exe" testdata\install_testdata.py strong|subtle|bat310|nobat310|off

`strong` copies `mods/3dtest/battle/STAGE10_T00_00(.png, _nml.png)`, `STAGE10_T03_00(.png, _nml.png)` (the original
floor and wall 4x, with test normal maps) and `mesh/battle/STAGE10_particles.toml`, `STAGE10_floorlight.dds`, and
writes `[battle_STAGE10]` into `lighting/config.toml` through `ff3d.ibl.write_section` (lock, CRLF, parse check).
The test values are deliberately loud. `off` removes exactly what it added. (If the pipeline has installed oq's
painted textures in `mods/textures/battle` meanwhile, the `mods/3dtest` copies hide them until `off`.)

Start each battle from a field: `god on`, then `battle 310`. Config and `mesh/battle/` edits hot-reload mid-battle
(0.5 s); texture/normal-map changes need a new battle. End test battles with a game restart (no rewards kept).

## T0. Fields unchanged (do first, with the new DLL)
- farm (or md1_1): `view 3d`, a still spot; screenshot with the old DLL (before installing) and the new one. Expect
  identical pixels except the moving things (sway, water, particles, glow flicker). Characters' haze, grade, floor
  light and the scene's normal map paths are the same code with all battle values 0.
- status in fields: no `battle_*` lines.

## T1. A battle with no data (before `install_testdata.py`)
- `battle 310`. status: `battle_stage 10`; `battle_cam` FFNx's eye and `scene` eye both near scene.bin's
  (9187, -904 or -918, -2001) once the camera rests (they move during the intro sweep); `battle_draws ... scene > 0,
  lit > 0` (the scene is lit), `nml 0 scroll 0 sway 0`; `battle_particles 0 0`.
  - battle_stage -1: the exe didn't load `STAGE10.P..` through load_p_file and no `battle/STAGE10_T..` texture was
    drawn: nothing per-scene will work (report the trace: `trace_loaders = true` shows "reading p file" names).
  - the two eyes far apart: FFNx's battle view matrix isn't the scene's camera; haze/sway/floor light then sit in
    the wrong place while particles are right (or the reverse). Report the numbers.
- Look: as before, except the characters' and scene's shading direction: battles now shade from
  `light_rotation_*` (60/60 by default) instead of the direction the last field frame left. Compare with
  `ff7-field-bg-mod/work/models/battle/camera/sheets/oq_310.jpg` (left panel): shading may differ, shadows the same.

## T2. Sun direction (A)
- (Run 2026-11-07: H 0 lights from +z, the party's side, not -z: FORMAT.md corrected; and at 30 degrees oq's floor is
  all in its walls' shadow, so the floor shows neither the sun nor the normal maps below ~40. Results: RESULTS.md.)
- `install_testdata.py strong` (from the field), `battle 310`. The sun is 30 degrees up from -z (the Grunts' side):
  characters' and crates' lit sides face the Grunts' side of the screen; shadows fall toward the party's side.
- Edit `[battle_STAGE10]` `light_rotation_horizontal = 180.0` (hot reload): lit sides and shadows flip together.
  Before this change only the shadows moved.

## T3. Haze (B)
- strong blue haze on the far walls and tanks; the characters (about 9000 from the camera) about half blue too.
- spells and the Beam Gun's glow (additive): no blue boxes or blue tint where they are; they just fade a little.
- `haze_scene = 0.0`: the reactor clears completely, the characters stay hazed. `haze_strength = 0.0`: all gone.
- The sky domes of outdoor scenes (unlit, `disable_lighting_textures`) never take haze; oq has none.

## T4. Character grade (B) and that the scene is not a character
- party and Grunts strongly red; floor, walls, tanks and crates NOT red (they're `battle/STAGE10_*`).
- `char_grade` removed: back to normal colours.

## T5. Normal maps (C)
- status: `nml > 0` (the floor and walls are lit and have maps; `scene` and `lit` as in T1).
- Floor (STAGE10_T00: a sheet whose tiled part covers the floor, repeated): a grid of round domes, 8 x 8 per sheet. Each dome's bright
  side faces the same way as the characters' lit side; with `light_rotation_horizontal` 0 <-> 180 they flip together.
  **Sign check:** if the domes' bright side is opposite to the characters' (they read as dents), the battle frame is
  mirrored against the fields' convention: report it (the fix is a flip of x and y, in the data or in
  `perturb_normal_edge` for battles).
- Walls (STAGE10_T03): brick and panel relief from the painting's own detail, catching the low sun.
- `normal_strength = 0.0`: domes and bricks gone, the floor exactly as in T1. `0.5`: half.
- `normal_edge = 0.1` (upstream's): domes near the sheet's edges go flat (the fade band; where the floor's UVs
  reach them).
- A lit-path counter of 0 with maps installed: the colour override didn't load from `mods/3dtest/battle` (check
  FFNx.log "Using texture") or the lighting window's PBR textures switch is off.

## T6. Floor light (B)
- `mesh/battle/STAGE10_floorlight.dds` over x, z = -4000..4000: 1.8x warm on the Grunts' side (z -4000) down to
  0.4x blue on the party's (z +4000): the Grunts brighter and warmer, the party darker and bluer. The floor itself
  unchanged.
- `floor_light_strength = 0.0`: both back to normal. Delete the .dds (hot reload): same.

## T7. Sky drift and sway (D, E)
- status: `scroll > 0`, `sway > 0`.
- STAGE10_T01 (the tall pipes with the ladder, rising out of sight): their texture slides sideways steadily, a
  twentieth of it a second, wrapping (other parts of the sheet and its cut-outs slide in: expected for a test, not
  a look).
- STAGE10_T02 (railings, crate fronts and wall panels round the room): wobbles in gusts rolling across the room,
  brightening a little in each gust.
- Two shots a second apart differ only there (and characters, particles).
- `uv_scroll` / `sway` removed: still.

## T8. Particles (F)
- status: `battle_particles 3 <n>` (dust, smoke, sparks), n a few hundred.
- warm dust motes drifting over the floor; smoke from the top of the left crate stack drifting toward the
  Grunts' side; a fountain of sparks low behind the Grunts. Characters in front hide them (depth tested); the
  battle menu covers them; dust right in front of the camera fades out (`near = 2500`) during camera swoops.
- Edit `mesh/battle/STAGE10_particles.toml` (e.g. dust `count = 0`): reloads within 0.5 s (log "reloaded battle
  scene files").
- If nothing shows but the count is right: check `battle_cam` (T1). If they are mirrored or upside down (smoke
  falling): the y-down conversion is wrong; report.
- `view original` (or F8): no particles, drift, sway, haze or normal maps (lighting off).

## T9. Group order and the scene's IBL (A)
- `install_testdata.py bat310`: in formation 310 the haze turns orange (the formation's group), everything else
  from `[battle_STAGE10]` stays. `battle 309` (same scene, two 1st Rays): blue haze again. `nobat310` to undo.
- IBL: copy any existing pair, e.g. `lighting/ibl/field_<some id>_s.dds` / `_d.dds`, to
  `lighting/ibl/battle_STAGE10_s.dds` / `_d.dds` (hot reload): the characters' and scene's fill light changes in
  every oq formation; with `bat_310_s/_d.dds` also present, 310 takes those. Delete to undo.

## T10. A real-looking set
- `install_testdata.py subtle`: gentle haze (the scene takes 60%), slight warm grade, character fill light 2.0,
  mild floor light, normal maps at 60%, a slight sway on STAGE10_T02. Screenshot for the user.

## T11. Outlines in battles (optional)
- add `char_outline_width = 0.003` to `[battle_STAGE10]`: dark rims on the party and Grunts (opaque parts only),
  none on the reactor and none round spell effects.

## T12. Other battles and back to the field
- a formation in another scene with no data (any random encounter, or `battle 0`): looks as before but for the sun
  direction (T1); `battle_stage` shows its own number.
- **Back to the field without a restart** (`autoattack on` until the battle is won; restart afterwards without
  saving, so no rewards are kept): the field must look exactly like T0's shot. This is a fix: the settings were only
  re-read on a new field or formation, so after any battle the field kept the battle's (before: no haze, grade,
  softness or outline on its characters, no clouds, contact shading... until the next field; now it would have kept
  the strong test haze). They're now re-read whenever the mode changes. Also a second `battle 310` in a row (from the
  same field) must show the [battle_STAGE10] look, not the field's.
- Opening and closing the field menu: no visible change (the field's settings are re-read, same values).
- `install_testdata.py off` at the end.

## Things to watch in FFNx.log
- "Field particles: ...: <parse error>" for a bad particles file (the battle path uses the same reader).
- with `trace_loaders = true`: "FFNx-3d battle: battle scene STAGE10 (STAGE10.P00)" when the scene loads.
