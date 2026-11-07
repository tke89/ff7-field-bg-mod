# Models and battle scenes: plan (started 2026-11-06)

The user asked (2026-11-06): "add to our tool the ability to do the same thing we do to the 2D fields, to the
textures on 3D objects, and to the battle scenes". Their choices:
- **All three together:** battle scenes, field characters and objects, battle characters and enemies.
- **Paint the scene, project back:** render the model or scene, restyle that picture with the field looks, and map
  it back onto the textures. Don't restyle texture sheets on their own.
- **Field models: the original game's** (not the chibi mod in `direct/char`).

## What the game has
- **battle.lgp:** 198 model ids with textures, 787 TEX files. Most are 128-256 px and most have several palettes
  (1-8). Party models are about rt-si, enemies an-np, and battle scenes og-ra. Files are `<id><aa..>`: `aa` is the
  skeleton, `ac`-`al` are textures, `am`... are .p parts, and `da` holds the animations.
- **char.lgp:** 385 HRC skeletons, 4180 RSD/.p parts, 694 TEX files (602 of them 32x32: faces and details). Field
  models get their colour mostly from vertex and polygon colours, not textures.
- **scene.bin** (`data/lang-en/battle/`): the battle setups, with each one's battle scene and its camera positions.
- **User's mods:** `direct/char` (chibi field models, 979 textures up to 1024 px) and `direct/battle` (party
  models ru, sb, sc, sd, se). Restyled original art won't show where those mods replace a model.

## How FFNx takes the results
- **Texture overrides:** `mods/textures/<lgp>/<NAME>_<palette %02i>.png` (mod_path "mods/textures"): `char/AABB_00.png`,
  `battle/OPAC_03.png`. Any size; the UVs are normalised.
- **External meshes** (`enable_external_mesh`): `<external_mesh_path>/field/<p name>.gltf` replaces a .p part with a
  glTF, textures from `.../field/textures/`. The original field models' untextured polygons have no texture space,
  so their painted look ships as a textured glTF per part (generated UVs). This replaces the chibi part of the same
  name, so installing it needs the user's OK.

## Pipeline (ff7bg/models/)
1. **Formats:** `tex.py` (TEX -> RGBA per palette), `pfile.py` (.p parts), `battle.py` (skeleton, parts, first
   animation frame, battle scenes, scene.bin cameras), `field.py` (HRC/RSD/.a, the field model sections: which
   model and animation each field uses).
2. **Mesh** (`mesh.py`): posed triangles with positions, UVs, texture and palette, vertex colours, blend mode.
3. **Renderer** (`render.py`, numpy): colour (texture x vertex colour), texel coordinates, triangle id, depth and
   normals for any camera.
4. **Paint and project** (`paint.py`, Text2Tex-style):
   - Choose views: battle scenes use their scene.bin cameras; models use an orbit (front, sides, back, 3/4, top).
   - For each view: render it with the texture painted so far; mask what isn't painted yet or is seen better now;
     restyle (the Studio's look and workflows, masked: Differential Diffusion or the edit models' noise mask);
     project the new pixels back into texture space.
   - Project with z-buffer visibility, weighted by facing angle and texel density.
   - Fill texels no view sees (UV-space inpaint, else the upscaled original).
   - Output at 4x. Other palettes follow the palette's colour change, as the 2D animated frames do.
5. **Studio:** a "Models" section (battle scenes, battle models, field models) with a list, a turntable preview
   (original / painted), Preview and Final, versions as for fields, and install.

## Built so far (2026-11-06)
- **Core** (main session): `tex.py`, `pfile.py`, `mesh.py`, `render.py`, `paint.py`, `restyle.py`.
  - Blend flags: options & features & 0x400. Mode 0 is plain alpha; only modes 1-3 are effects.
  - An identity round trip comes back within 1-3 levels of the original.
- **Battle** (`battle.py`, `scenebin.py`):
  - **Ids** are base 26:
    - aa-of (0-369): enemies (scene.bin enemy id = index);
    - og-rr (370-459): the 90 battle scenes (location L = 370 + L);
    - rs-sm: the party (rt Cloud, ru Tifa, rv Aeris, rw Red XIII, rx Yuffie, ry Cait Sith, rz Cid, sb-se Barret, sf-sh
      Vincent, si a finer Cloud).
  - **Files:** `aa` is the battle hrc header plus 12-byte bones; textures start at `ac` (texid t = `ac` + t); parts are
    `am` + bone; weapons start at `ck`; `da` holds the animations.
  - **Animations:** only frame 0 is decoded (YXZ rotations); later frames are deltas, not done yet.
  - **Palettes:** each battle TEX stores exactly one palette (the group's CLUT row), so overrides are
    `battle/<FILE>_00.png`. `_00` against `_<row>` is unverified in game; FFNx falls back to `_00`.
  - **Cameras:** `cameras(scene)` gives scene.bin's exact eye and look-at points. The focal length (512 px at 320x240)
    is an estimate; check against an in-game screenshot. It's set by `battle.BATTLE_H`.
  - The user's `direct/battle` mods replace Tifa (ru) and the four Barret models.
- **Field** (`field.py`, `atlas.py`, `gltf.py`):
  - **Loading:** all 385 HRC models load and pose. Rotations are Ry·Rx·Rz; the root translation has its y negated;
    models are y-down and face -z.
  - **Atlas:** `field.atlas()` gives every triangle space in one atlas per model (256-2048 px, 3 px margins), filled
    with the game's own colours. Textured triangles go in too, because FFNx ignores vertex colours on external meshes.
  - **Export:** `export_model` writes glTFs for FFNx external meshes (`mesh/field/<P>.gltf`, textures as
    `textures/<P>_atlas.dds`): one joint per part, with the bone's model-space transform per frame of every
    animation the fields play.
  - **Engine changes needed before installing:**
    - The fork looks up `mesh/field/<name>.gltf` for every .p, battle included, and 245 field part names are also
      battle part names. Limit the lookup to char.lgp, or use a `mesh/char/` folder.
    - External meshes are drawn at the field's scale, not the model's own scale entry (261 of 4891 uses differ).
  - **Size:** Cloud's 495 animations make 14.8 MB of glTF; consider limiting the animations.
  - **Blending:** additive glow triangles (the save point) can't be additive on external meshes and are
    approximated with alpha.
  - Once any part of a model is external, FFNx draws only external parts, so a whole model is exported or none.

## Verified in game (2026-11-06, late)
- **Battle texture names:** the 2026 re-release's ff7_en.exe loads battle files by their PSX names (STAGE%02d.DAT,
  ENEMY%03d.DAT, CLOUD.DAT, BARRETT2.DAT ...) and maps them to battle.lgp itself; FFNx names the textures after them.
  - So overrides are `mods/textures/battle/STAGE10_T00_00.png` (scene oq = location 10, texid 0, palette 0),
    `ENEMY019_T00_00.png` (Grunt), `CLOUD_T00_00.png` and so on. `battle.game_name`/`texture_key` build them, and
    TexRef keys use them.
  - **Seen:** oq's painted textures, installed as STAGE10_T0n_00.png, showed in a real battle (formation 310).
  - The party table follows the exe's order; only CLOUD and BARRETT2 were seen.
- **External meshes and battle:** the game requests battle parts as `STAGE10.P00` / `CLOUD.P00`, so FFNx's
  external-mesh lookup sees `STAGE10`, `CLOUD`... It never sees battle.lgp's names, so field part glTFs can't
  hijack battle parts.
- **FFNx-3d live commands** (DLL 2026-11-06 23:18; field.cpp, ffnx3d_live.cpp, gamehacks.h):
  - `battle <formation>` starts a scene.bin formation from the field;
  - `god on|off` refills the party's HP/MP to their own max every battle frame;
  - `autoattack`, `encounters` and `speed` set FFNx's own hacks;
  - status shows `battle <formation> <hp/max x3>`.
  - End a test battle with a game restart (no rewards kept).
- **Battle cameras, calibrated (2026-11-07, 12 battles in 9 scenes; median 0.8 px at 960x720):** the battle rests on
  each formation's slot 0 camera.
  - Eye and look-at are used as scene.bin stores them, plus `CAMERA_OFFSET` (0, -14, 0); its cause is unknown, 12-15
    in every battle.
  - The focal is 512 px per 240 lines (BATTLE_H was right), centred.
  - Battle scenes are drawn back-face culled (`BattleMesh.cull`, `render.rasterize(cull=None)` follows the mesh).
  - The first attempt's steep view was the stock placeholder camera (10000, -5000, 6000), which 732 formations keep
    in slots 1-2.
  - Evidence: `work/models/battle/camera/overview.jpg`.
- **Not our runtime:** the re-release's own runtime (FFVII.exe) has boosters (R3 battle boost), but FFNx runs the
  classic ff7_en.exe, which has none.

## Painting battle scenes: texture sheets (2026-11-07)
- **Projection-only was poor.** It was slow, and the views came out muddy: the look's input_scale 4 made each view a
  20 MP render. The Restyler now forces input_scale 0. It also looked right only from the cameras it painted from.
  Almost every battle-scene texture is laid 2-20 times over (paint.repeats), and a projection averages each place's
  different painting into mush.
- **Battle scenes now paint every texture flat** (`paint.paint(..., sheets=R.sheet, repeat=0)`, about 20-35 s a
  scene):
  - wrap padding only across the edges the scene joins (`paint.wraps`), mirrored elsewhere;
  - no depth lock (a flat strip's estimated depth made oo's mountains a mesa);
  - repaint 0.85 of the look's unless the user sets a strength;
  - the scene's description from its first battle camera.
- **Known limits:** gentle; logos split across textures (op's "08") can break.
- Battle and field models still use projection: views all round, no repeats.
- **Models section settings,** per model, recorded per version: paint strength (Automatic or 0.20-0.90), input scale
  (1-8x), output scale, your words plus automatic description, and the upscaler before painting and for the game
  size. `restyle.upscale` covers every Studio upscaler.
- **"Try in a battle"** writes `god`, `autoattack` and `battle <formation>` to the game's ffnx3d/cmd.txt, only on
  the user's click.
- **Painting method,** per model:
  - Flat: `repeat=0` sheets. The default for battle scenes.
  - Projected: no sheets; views are the battle cameras plus `battle.ring_cameras` for scenes, the orbit for models.
    The default for models.
  - Hybrid: sheets for paint.repeats >= 1.6, the rest from the views.
  - Field models are Projected only.
- **SYW Unified Battle Textures** (`restyle.syw_battle_*`) is an upscaler-before-painting choice for 87 battle
  scenes, plus an SYW view.
- **Battle-scene extras for Flat and Hybrid** (`scenepaint.py`, 2026-11-07). The user found oo blurry, with a line
  across the sky.
  - **The sky as one panorama** (`paint.paint(panorama=True)`):
    - The far textures (classify: far out, and upright or high) are drawn as the view all round from the middle,
      folded by the sky's repeat period, then painted as one picture and mapped back.
    - Both directions are exact per pixel: a ray from the centre to the triangle. The first version interpolated in
      angle space and came back ~50 px off on 45-degree segments.
    - Single-texel dome tops are left out of its height range. The no-AI round trip is within 1-3 levels.
  - **Ground detail** (`detail=2|4`): ground textures (flat and low) are painted bigger, with the Studio's detail pass
    (out_scale and detail_pass in the sheet's workflow).
  - **Seams** (`seams=True`): `level_seams` pulls colours together across chart edges that meet in 3D.
    `restyle._wrap_blend` blends each wrapping edge with the AI's other painting of it (the panorama's ends, and
    tiles).
  - Checked with the real AI on oo and oi: no line in the sky, the mountains continuous, the ground a bit sharper.
  - About a minute per scene at 2x ground detail. The Models card has switches for all three.

## Battle scenes beyond textures (started 2026-11-07, user: "the first 4")
1. **Lighting from the painting:**
   - image-based light from a cube rendered at the arena's middle with the painted textures;
   - a sun and ambient from the painted sky.
   - The engine already reads `[bat_<formation>]` and `ibl/bat_<formation>_*.dds`.
2. **Haze and the characters' grade in battles.** The engine forces them off for battles today (lighting.cpp).
3. **Life:**
   - sky drift (`uv_scroll`);
   - grass sway (`sway`);
   - particles (`battle/STAGE<nn>_particles.toml`).
4. **Normal maps on battle textures** (`<NAME>_<pal>_n.png`, `normal_strength`).
- **Shared formats:** a per-scene group `[battle_STAGE<nn>]` and `ibl/battle_STAGE<nn>_*`, falling back from the
  per-formation ones. The engine agent's final formats are in `ff7-3d/dev/battle-engine/FORMAT.md`.
- **Who does what:**
  - Engine: patches in ff7-3d/dev/battle-engine (the main session builds and tests them with `battle <formation>`).
  - Data: `ff7bg/models/battlelight.py`.
  - The Models plug-in gets wired afterwards.

### The data side: `battlelight.py` (2026-11-07)
- **Commands:** `python -m ff7bg.models.battlelight build|install|uninstall|check|status <id>`.
  - Builds go to `work/models/battlelight/<id>/<version>/`.
  - `install` keeps a manifest (`work/models/battlelight/installed.json`). It refuses files and sections that aren't
    its own, and edits config.toml under `ff3d.ibl.config_lock()`. `formations=True` also writes the
    per-formation sections and files.
  - The AI parts refuse to run while the game is running.
- **Light:** the cube at the fight's middle (y = -475), times the scene's vertex colours.
  - **Sun:** the brightest part of the painted sky, kept 35-60 degrees up (higher when walls shadow the floor:
    `clear_sun`, below); on overcast scenes it's the side where the sky is brightest. How sunny the painting looks
    sets its share, 0.25-0.5.
  - **Checked in game:** battle-space directions sample the cube as `envmap.face_dirs` lays it out (six coloured
    faces, formation 469).
  - **The stage takes the light too** (all but `disable_lighting_textures`). So the floor gets 1 x its painted
    colour, with 0.25 of the light's colour cast and 0.85 of the painting's difference between directions. With the
    sun off, oo's floor measured 1.03-1.05 of the painting in game.
  - **A painted sky isn't light:**
    - oo's flat navy dome cap takes the rest of the sky's average;
    - nothing above (oq) is filled from what is above;
    - the ground is at most 0.5 x the sky.
  - **Reflections:** the reflections' cube is 0.1 of the diffuse one. At full strength the grazing term put the sky's
    blue on oo's dark floor.
- **Haze:** the colour is the painted horizon; the density is the painting's contrast falloff, bounded by its darks.
  `haze_scene = 0`.
- **Grade:** `char_grade_full` is atmos.grade's fit over the resting views. `char_match` and `char_exposure` are
  left to `[battles]`.
- **Normal maps** (`_nml.png`, as FORMAT.md settled):
  - Marigold runs in tiles, each tile's slant removed. A whole sheet came back flat, and tiles without that made
    blocks.
  - The painting's fine detail is added as bump.
  - They're installed only beside an installed painted texture.
- **Life:**
  - **Sky:** far textures above 12 degrees, or that Qwen calls only sky, drift about 0.14 degrees a second, along
    the way they tile.
  - **Grass:** ground textures that Qwen calls grass sway.
  - **Particles:** Qwen picks the kind; in battle units, with `near`.
  - **Results:** oo drifts T01 (Qwen: no grass, no particles); oq gets dust.

### Tested in game (main session, 2026-11-07)
- **Engine:** all of the battle-look tests pass except returning to the field after a won battle, which wasn't run:
  the victory screen needs a button press. Details are in `ff7-3d/dev/battle-engine/RESULTS.md`.
- **Sun direction was mirrored:** in game, `light_rotation_horizontal` 0 lights from **+z** and 90 from +x (measured
  with the characters' lit sides, their shadows and test domes on the floor). FORMAT.md said -z, and battlelight
  followed it, so every painted sun came from front to back. `sun_angles` / `sun_direction` are fixed.
- **A low sun lights nothing indoors:** oq's floor is all in its walls' shadow up to 35 degrees, half lit at 45 and
  all lit at 60. That's in game, and a ray test against the scene matches it.
  - `clear_sun` raises the painted sun in 5-degree steps until 85% of the floor round the fight sees it, up to 75.
    It's recorded as `info.light.floor_sunlit`.
  - oq went from 35 degrees (0% lit: its floor at only the image-based share of its painted brightness, relief
    flat) to 55 (96%). oo stays at 60 and oh at 35 (98%).
  - Rebuilt light-only: oq, oo, oh.
- **Grade before haze:** battles graded the characters after the haze, which graded the haze colour too. A blue
  haze went pink on red-graded characters. Fixed in the shader, for battles only.
- **oo in game with its build** (formation 469): Midgar Zolom and the party take the overcast sky's soft light. Zolom
  reads more solid and green, and Cloud is lit from the camera's side. Uninstalled again afterwards (the user's
  call).
- **Test tooling:** god mode now gives the battle's Defense and MDefense 511, so a one-hit kill does about 1 damage.
  A Game Over happened before this fix. Peerless was tried and pulses the party gold.

## Order
Shared core first (formats, mesh, renderer), then battle scenes end to end (static, unlit, real cameras), then
battle models (posed), then field models (posed; generated UVs and glTF for untextured parts).
