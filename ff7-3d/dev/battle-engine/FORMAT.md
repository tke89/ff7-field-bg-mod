# FFNx-3d battle look: data formats (engine side)

Status: implemented in `dev/battle-engine/patches` (2026-11-07), not yet built or tried in game. The engine reads
everything below; anything missing means "as before". Paths are relative to the game's `workingdir`.

Battle world coordinates are the game's: **y down** (the ground is y = 0, the sky is negative y), x and z across
the ground. Distances are battle units: a standing battle character is about 950 tall (Cloud), roughly 11-14 times
a field's (65-90).

## A. Where a battle's settings come from

`lighting/config.toml` (or `config.devtools.toml`), read in this order, the first group that has the key wins:

1. `[bat_<formation>]`  one formation (scene.bin formation id = `battle <formation>` in the live commands);
2. `[battle_STAGE<nn>]` one battle scene, every formation fought in it; `nn` = two digits, the number of the
   exe's `STAGE<nn>.DAT` = scene.bin's battle location (pipeline: battle.lgp id `og` + nn, `oq` = STAGE10);
3. `[battles]`          every battle (game-wide battle settings, like `[fields]` for fields: `char_match`,
   `char_exposure`, `normal_edge`...);
4. the file's top level (the stock keys: light_*, ambient_*, material_*, ...).

`[fields]` never reaches battles and `[battles]` never reaches fields. A key that is an array of tables (`uv_scroll`,
`sway`) is taken whole from the first group that has it (a formation's list replaces its scene's, no merging).

The engine learns the scene number when the game loads the scene's parts (`STAGE<nn>.P00`...), and from the
textures it draws (`battle/STAGE<nn>_T<tt>`). Settings and light are read again on entering a battle, when the
formation or the scene changes, and on every hot reload of `lighting/` (and a field's again when the game comes back
to it: before, a field kept a battle's settings until the next field).

Image-based light: `lighting/ibl/bat_<formation>_s.dds` + `_d.dds` when both exist, else
`lighting/ibl/battle_STAGE<nn>_s.dds` + `_d.dds` (same cube map format as the fields' `field_<id>_s/_d.dds`).

**Sun direction:** battles are now shaded from `light_rotation_vertical` / `light_rotation_horizontal` (the same
angles that place the shadows). Toward the sun, battle world (y down; the frame scene.bin's cameras, particles and
`ff7bg/models/battle.py` use), V and H in degrees: `(cos V sin H, -sin V, cos V cos H)`; V 60 / H 60 = (0.43, -0.87,
0.25). So V is the sun's height above the ground (90 = overhead) and H its direction around: H 0 toward +z, 90 toward
+x, 180 toward -z. **Measured in game** (main session, 2026-11-07, oq formation 310, V 60, H 0/90/180/270): the
characters' lit sides, their shadows and the test domes' bright sides all face +z at H 0 (screen right from 310's
camera, the party's side) and +x at H 90 (the camera's side). FFNx's rotation of (0, 0, -1) gives -cos V cos H for z;
the battle view matrix it goes through mirrors z against this frame. (This file said -cos V cos H, H 0 toward -z,
until then: ff7bg/models/battlelight.py followed it and mirrored every painted sun front to back; fixed there.)
A low sun indoors lights nothing on the floor: oq's floor is all in its walls' shadow up to V 35, half lit at 45, all
at 60 (battlelight.clear_sun raises a painted sun until the floor round the fight sees it).
Before, battles kept whatever sun direction the last field frame left behind (fields set it every frame since the
fork's sun fix; upstream never sets it at all), so `light_rotation_*` only moved the shadows. This applies to all
battles, with or without a group.

## B. Haze, the characters' grade and style in battles

The fields' keys, read from the battle groups above (they were forced off in battles):

| key | meaning in battles |
|---|---|
| `haze_color` = [r, g, b] | sRGB 0..1, as fields |
| `haze_strength` | 0..1, the most haze (0 = off) |
| `haze_density` | per battle unit of distance past `haze_start` |
| `haze_start` | battle units from the camera's eye where it starts |
| `haze_scene` | **new**, 0..1, default 1: how much of the haze the battle scene's own polygons take (characters, enemies and effects always take all of it). Use < 1 when the painting already has its distance haze. |
| `char_grade_full` (+ `char_match`, `char_exposure`) or `char_grade` | 12 numbers, as fields; on characters, enemies and effects, never on the scene |
| `char_soft` | texture softness of characters/enemies/effects (not the scene) |
| `char_outline`, `char_outline_scale`, `char_outline_width`, `char_outline_color` | as fields; drawn on opaque parts that aren't the scene. Width is per unit of distance (screen-constant), so the same numbers give the same width as fields |
| `char_ambient` | the characters' fill light (ambient intensity for everything but the scene; 0 = the scene's `ambient_light_intensity`) |
| `floor_light_rect` = [x0, z0, x1, z1] | **battle meaning:** the ground rectangle (x and z, battle units) the floor-light map covers |
| `floor_light_strength` | 0..1, as fields |

- Haze is measured from the camera's eye in battle world units (the battle camera moves: whatever is far from the
  camera now gets more). Light added on top (additive/subtractive effects) fades out with the haze instead of
  turning haze-coloured.
- "The scene" = every draw whose texture is a battle scene texture (`battle/STAGE*`); there are only 2 untextured
  scene triangles in all 90 scenes (op), which count as characters.
- Textures listed in the top-level `disable_lighting_textures` (the stock list holds the sky domes, e.g.
  `STAGE08_T01`) are drawn unlit: no haze, grade, normal map or sway on them. `uv_scroll` still works on them.
- Floor-light map: `mesh/battle/STAGE<nn>_floorlight.dds`, the fields' encoding (RGB = light multiplier / 2,
  linear, loaded as data; 0.5 grey = unchanged), top-down: u along +x from x0 to x1, v along +z from z0 (top row)
  to z1. Characters/enemies/effects take it where they stand; the scene doesn't.
- Not in battles: `char_smooth` / `char_round` (field models only), `char_light` (characters' own IBL), point lights,
  contact shadows, reflections, relight, cloud shadows, `scene_fx`, `water_motion`, `grass_wind`.

## C. Normal maps (surface relief) on battle textures

**Changed from the draft (`_n`):** FFNx already loads `<NAME>_<pal>_nml.png` (or `.dds`) beside every override
texture and binds it for the draws that use that texture, so battle normal maps use that name:

    mods/textures/battle/STAGE10_T00_00.png        colour (any size)
    mods/textures/battle/STAGE10_T00_00_nml.png    normal map, same folder (any size; same UV layout)
    mods/textures/battle/STAGE10_T00_00_pbr.png    optional material map (FFNx's: R roughness, G metallic,
                                                   B specular, A occlusion)

The map is only looked for when the colour override loads, and only in the folder it loaded from (`mods/3dtest` is
searched before `mods/textures`; the pair must be together). Loaded as data (not sRGB). Static textures only (not
palette-animated). Changing a map needs the texture reloaded (a new battle; the lighting hot reload doesn't do it).
FFNx's per-pixel tangent frame comes from the UVs' screen derivatives (no vertex tangents), so mirrored or rotated UV
layouts on some polygons are handled.

Encoding: exactly the fields' scene normal maps (`ff3d/simplify.py encode`, `ff3d/surface.py write_normals`), i.e.
FFNx's cotangent frame (`misc/FFNx.lighting.sh perturb_normal`):
- R, G = tangent-space x, y as `byte = round(128 + 127 * x)`; B and A are ignored (write 255).
- x leans the normal toward +u (texture right), y toward **+v (texture down: v = 0 is the image's top row)**, so it is
  "DirectX-style / green-down". A height map H(row, col) becomes x = -dH/dcol, y = -dH/drow (bump code in
  `surface.write_normals`).
- The shader rebuilds z as `sqrt(1 - x^2 + y^2)` (an upstream quirk, kept for both fields and battles). To get an
  exact normal (x, y, z) store `x * k, y * k` with `k = 1 / sqrt(z^2 + x^2 - y^2)` (as `simplify.encode` does); a
  plain green-down normal map is close enough for gentle relief.

Keys (battle groups):

| key | meaning |
|---|---|
| `normal_strength` | 0..1, default 1: how much of its normal map a texture shows (0 = none) |
| `normal_edge` | 0..0.5, default 0.1 (upstream): the band along each texture edge (in UV, from each integer u/v) over which the relief fades out, there to hide seams. Use 0 for maps that wrap seamlessly; at 0.1 about a third of every texture loses some relief |

Normal maps light only lit draws (not `disable_lighting_textures`), with FFNx's lighting on, and only while the
lighting window's "PBR textures" switch is on (the default).

## D. Sky drift: `uv_scroll`

    uv_scroll = [
      { texture = "STAGE08_T01", u = 0.004, v = 0.0 },   # texture widths / heights per second
    ]

- `texture`: the texture's name as the override file without palette and extension (`STAGE08_T01` for
  `battle/STAGE08_T01_00.png`); a leading `battle/` is accepted. Case doesn't matter.
- Every draw with that texture has its UVs offset by (u t, v t), wrapped (textures repeat). The whole texture
  moves, normal map included: give it only to textures that hold just the thing that drifts (a sky panorama) and
  tile in that direction.
- Works on lit and unlit draws (sky domes are unlit in the stock config).

## E. Grass and leaves in the wind: `sway`

    sway = [
      { texture = "STAGE02_T00", amount = 0.5, speed = 1.0, dir = [1.0, 0.0] },
    ]

- `amount` 0..1 (1: the picture leans up to ~0.006 of the texture's width, plus a fine flutter, and gusts brighten
  it by up to ~5%); `speed` time scale (default 1); `dir` = [u, v] the lean in texture space (default [1, 0]).
- Gusts roll across the ground by world position (bands about 1800 battle units apart, travelling along x, z =
  (0.8, 0.6) at about 400 units a second times `speed`), so neighbouring polygons and repeats move together; no mask:
  the whole texture sways.
- Lit draws only (not `disable_lighting_textures`). A texture can have both `uv_scroll` and `sway`.

## F. Particles

    mesh/battle/STAGE<nn>_particles.toml        e.g. mesh/battle/STAGE10_particles.toml

The fields' format (`src/ffnx3d_particles.h`: a main `kind` plus `[[source]]` tables), with these differences:
- Coordinates are battle world, y down: `box = [x0, y0, z0, x1, y1, z1]` (min and max corners), `points`, `floor`
  and `wind` as battle (x, y, z). Snow and rain fall toward +y; embers, smoke and sparks rise toward -y.
- `lift = [lo, hi]` and `rise` are heights / speeds **upward** (positive = up), as in fields.
- The kinds' built-in motions (dust drift, snow fall...) are in field units: scale them with `speed` (about 10-12
  for battles); `size`, `spread`, `rise` and the boxes are in battle units.
- `mask`, `mask_main`, `mask_project`, `mask_size` are ignored. `near` (battle units) works on the live camera: the
  main kind stops growing and fades out nearer than `near` (gone at half of it). Recommended (cameras swoop close).
- Drawn after the battle's 3D (scene, characters, effects) and before its menus, depth tested, not written, with
  the camera the scene itself is drawn with. Not in the Original view. The `mesh/battle/` folder hot-reloads.

## Writing the config

`ff3d/ibl.py write_section(table, values)` already writes any `[table]` the safe way (under `config.toml.lock`, CRLF
kept, refuses a file that doesn't parse, replaces the table in place): use it for `[battle_STAGE<nn>]`, `[bat_<f>]`
and `[battles]`. Values are TOML text (`'[0.4, 0.5, 0.7]'`, `'[{ texture = "STAGE08_T01", u = 0.004, v = 0.0 }]'`);
keep arrays on one line (a section ends at the next line starting with `[`). `testdata/install_testdata.py` shows it.

## Live status (`ffnx3d/status.txt`, in battles)

    battle <formation> <hp/max x3>                   (as before)
    battle_stage <nn>                                the scene number the settings use (-1: not known yet)
    battle_cam <x> <y> <z> scene <x> <y> <z>         the camera's eye in battle world: by FFNx's view matrix (what
                                                     haze, sway and floor light use) and by the scene's own draw (what
                                                     the particles use); both should be scene.bin's camera
    battle_draws frames <f> scene <n> lit <n> nml <n> scroll <n> sway <n> other <n>
                                                     draws over the last f frames: the scene's, the scene's lit ones,
                                                     lit ones with a normal map, drifting, swaying, all the rest
    battle_particles <systems> <shown>               particle systems loaded, particles drawn last frame
