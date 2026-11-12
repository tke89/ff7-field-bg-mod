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
  - **Ground detail is off by default since 2026-11-07** (user). On oi it was 22 s and 3.8 MB off, 68 s and 13 MB at 2x,
    and 185 s and 45 MB at 4x, for a difference barely visible from the battle cameras. 2x is left as an option for
    scenes whose camera comes close to the floor.

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

## Painted textures for untextured battle models (2026-11-07)
The user wants battles to look like a 2D anime: the target is an AI repaint of formation 469's frame
(`work/models/battle/target2d/target2d_55.png`; painterly at repaint 0.55). Shaders were rejected, so the drawing has
to live in the models' textures, drawn with flat light. Most battle models are vertex-coloured: Midgar Zolom (`dd`,
ENEMY081) has no texture at all. FFNx-3d draws a part from a per-model atlas when it finds one. The format is in
`ff7-3d/dev/battle-atlas/FORMAT.md`; the data side is `ff7bg/models/battleatlas.py`.
- **Commands:** `python -m ff7bg.models.battleatlas build|paint|check|install|uninstall|status <id> [--version V]`.
  - `build` makes the unpainted atlas (the game's own colours); `paint` restyles it (it refuses while the game runs).
  - Builds go to `work/models/battle/atlas/<id>/<version>/`: `<MODEL>.png`, `<MODEL>.P<nn>.uv`, info.json and
    preview/.
  - `install` copies one into the game's `mesh/battle/atlas/` with a manifest (`work/models/battle/atlas/installed.json`).
    It refuses files that aren't its own; `uninstall` removes exactly what it wrote. A folder it can't remove yet
    (a changed file left in it) stays recorded, so a later uninstall or install takes it away.
  - `make(id, ...)` is the `paint` command as one call (the Studio's "Paint in 2D style"): build, identity, paint with
    the model's recipe, colour lock, grade, previews, export, check; `extra_words` for the user's words, `cancelled`
    and `on_progress` passed down to ComfyUI (a cancel interrupts the view being painted).
- **Part names:** the game loads part `<MODEL>.P<nn>` with nn the bone index (battle.lgp's `<id>` + role `am`+nn),
  skipping bones without a part. Checked against FFNx.log's "reading p file" lines:
  - the Grunt (ENEMY019, 21 parts: P00 P01 P02 P05 ... P34) and Zolom (39 parts: P00-P43 without P07 P09 P11 P36 P40)
    load exactly the bones whose header has a part.
  - Each `.uv` holds the `.p` header's own numverts/numpolys and every polygon in the `.p` table's order
    (`poly_index`: groups' offpoly ranges); `check` re-reads them against the `.p` files.
- **Atlas:** `atlas.build` on the model in part space (`part_space`: parts are rigid, so any pose and animation fits),
  2048 px. Zolom: 280 charts, 0.23 texels per unit, about 1.6x what a 720-line battle view shows of it.
- **Identity check** (`identity`, formation 469's camera, the model alone): mean 0.64 levels, p99 2, 99.3% within 3,
  99.7% within 8. The rest are pixels on triangle edges where neighbouring triangles have different vertex colours
  (bilinear filtering across the edge). Picture: `work/models/battle/atlas/dd/identity/preview/identity.png`.
  - The Grunt (175 textured triangles) against a bilinear original: mean 0.7, p99 4. Against render.shade's
    nearest-texel original it's p99 50: that's the original's sampling, not the atlas.
- **Painting** (`paint`, the projected method with its own view loop):
  - Zolom stands at formation 469's enemy position, turned to face the party (yaw 180). It's checked against the
    in-game frame; the game's idle pose differs a little, since only frame 0 is decoded.
  - Views: the formation's 3 battle cameras with oo behind; then 6 views all round and 3 of the head, with a plain
    gradient of the battle's sky behind (v1-v7; v8 on: 9 all round and 16 close, below).
  - Each view is restyled with painterly at 0.55 and the spec's words, then projected (paint.Painter).
  - About 60 s for 12 views.
- **What the variants showed** (v1-v7; previews in each version's preview/: `vs_target.jpg`, `turntable.jpg`,
  `battle.jpg`):
  - **Projecting every pixel was wrong:** the ground hides the coils' undersides, and the views' edge pixels carry the
    background. A dry run with an identity restyle came back at mean 4.2 and p99 74 from the battle camera.
    `projectable()` now takes only pixels in front of the scene, and weights pixels at the outline and occlusion edges
    down to 0. The dry run is now mean 1.4, p99 8.
  - **The scene behind the round views** (v1-v2) put the olive ground behind the coils. The AI painted the sides like
    camouflage, and the back view got a face painted on the back of the hood. Fixed with a sky gradient and a few
    words per view (`VIEW_HINTS`).
  - **Repaint 0.62-0.7** (v2, v7) paints an open, fanged mouth on the closed face. **0.55** keeps it.
  - "Red-brown stripes/plates down its sides" in the words gave brown patches all over (v3-v5); v6 leaves it out.
  - Lifting the round views' darks toward the battle view's median (`lift`) made the back a little lighter (v4).
  - The battle cameras' paint is kept unless a view sees it 2x better than BETTER (`KEEP_BATTLE`).
  - The AI's colours, held to the original's by keep_colors, came out lighter and yellower than the target (Lab L 63
    against 51). `target_grade` matches Zolom's pixels in the target's framing to the target's mean and spread in Lab.
- **Result, v6** (`work/models/battle/atlas/dd/v6/`, installed; v10 below replaces it: v6 has a tongue painted on
  its face):
  - From the battle camera Zolom reads as drawn, in the target's colours: flat greens, painted shadow shapes on the
    hood and belly plates, thin dark lines between plates and on the hood's ribs, eyes, fangs and tongue. It holds up at
    1440 lines.
  - Short of the target: fewer and weaker outlines, some soft gradients left on the hood, and the low-poly outline is
    unchanged (a texture can't draw silhouette lines).
  - Sides and back are darker and patchier (olive with brown patches, faceted) than the front, which shows when the
    camera swings round.
  - `identity` is also built: the atlas with the game's own colours, for checking the engine path (it should look like
    the game without it).
  - In game it needs FORMAT.md's flat light (`toon = 1`, `toon_shadow_color = [1, 1, 1]`, no rim, highlight or
    outline, in `[bat_469]` or `[battles]`). `install` doesn't write that.
- **Zolom's tongue (user, 2026-11-07: two tongues in game).** The tongue is its own chain of parts, bones 13-16
  (`ENEMY081.P13`-`P16`), and flicks out in animations 1, 10 and 11; the jaw is P10 (it opens in animation 4), the
  fangs P08 (upper) and P12 (lower). In the pose painted from (animation 0, frame 0) the tongue is inside the head and
  the mouth is shut, so the tongue part was never painted. The painted tongue came from the AI: the fangs are pinkish
  in the game, and from the battle camera it drew them as a long tongue down the face and throat, words "mouth closed,
  no tongue" or not (v6, v8, v9). Fixed in v10:
  - `colours` (`recolour()`): the AI sees the fangs white. It then draws two white fangs and a closed mouth.
  - `MOVING` / `hide`: moving parts are left out of every view of the model, then painted on their own from a pose
    that shows them (the tongue from animation 10, front and side), only their own pixels projected.
  - `moving.jpg` in each version's preview checks it: the head with the tongue hidden (closed mouth, open mouth),
    then with the tongue out. v10: no tongue on the face, one painted tongue on the tongue part. v6 for comparison:
    `work/models/battle/atlas/dd/compare_tongue_v6_v10.jpg`.
- **Zolom's sides and back (v8-v12).** v6's were olive with brown patches, faceted. What was tried:
  - v8: 9 views all round instead of 6, 16 close ones (back of the hood, neck, coils, tail; regions by bone:
    `region_points`), each with words for what it shows, no colour words but "plain green scales". No better.
  - v9: those views at repaint 0.62. No better.
  - v10: the round views show the model matched to the battle camera's painting in Lab (`match_input`: lightness to
    its mean and spread, colour to its mean, its colour differences not scaled up). lift() had left them near black,
    and a full Lab match scaled their faint colour noise up 4-7x into patches. The AI still painted brown patches: it's
    the AI, not its input.
  - v11 (sides 0.62) and v12 (each round view restyled whole, `whole`) are no better than v10.
  - What worked is `colour_lock(mode="hue")` on the finished atlas: each texel takes the game's own hue (where the
    original is coloured at all), the painting's saturation and lightness stay. The brown patches go back to green;
    the red-brown side plates and hood markings, which are the game's, stay. The eyes come out red-orange, as in the
    target.
  - **v10 is the one to install.** From the battle camera it's at least as good as v6: closed mouth, white fangs,
    no tongue, the same flat greens and belly lines. All round it's green with plate lines, no brown patches. It's
    still faceted and a little blotchy in lightness on the coils. `work/models/battle/atlas/dd/compare_turntable_v6_v10.jpg`.
- **The party (2026-11-07).** Cloud is `rt` (CLOUD). Barret in formation 469 is `sc` (BARRETT2): FFNx.log has
  "reading p file: battle/BARRETT2.P00".. and BARRETT2_T00-T02, and `part_files("sc")` matches the log's parts
  exactly (16), as `rt`'s do (17). `sb`, `sd` and `se` are his other forms.
  - They're placed where the game's frame shows them (`PARTY_POSITIONS`: Cloud (-1255, 0, 915), Barret (924, 0, 590),
    facing -z): each one's soles cast from `ingame_469_oo_light.png` onto the ground. Cloud's outline then lands on the
    game's; Barret's soles and middle do too, a little smaller at the shoulders (only frame 0 of his idle is decoded).
    From this camera both are seen from behind, and Barret's head is hidden behind his shoulders in the game too (the
    target drew him one).
  - Atlas 1024 (`PARTY_ATLAS_SIZE`: Cloud 0.65 texels per unit with his sword, Barret 0.47). At 2048 a battle camera
    sees over 50 texels per pixel, which `paint.quality` reads as a perfect view, and the battle cameras repainted
    the close views' work.
  - Their weapon is in the atlas: the game loads it as `<MODEL>.W00` and FFNx-3d takes `CLOUD.W00.uv` like any part
    (`part_files(weapon=)`, `build(weapon="auto")`).
  - Identity: Cloud mean 0.56, p99 1; Barret mean 0.64, p99 1 from the battle camera. Close up, the faces' textures
    survive (eyes, beard); chart edges show as faint dark lines on the face before painting.
  - Views (`party_close`): the body from 9 sides framed without the weapon, the head from 4, the arms and hands, the
    sword (Barret: his gun arm), then the battle cameras, which repaint nothing.
  - Cloud: at repaint 0.55 he reads as anime Cloud (flat fills, line art, a clean face), but the AI drew a second
    pauldron on his bare right shoulder, turned his gold hair pale and his navy clothes black. Words naming the
    pauldron's shoulder didn't stop it (v3). 0.65 (v2) added more armour.
  - Barret: drawn, with painted shadow shapes, but patchy where views met, and the AI made his olive vest brown leather
    and his trousers teal. Painting his back first (`first="battle"`, the side the battle camera sees) gave a coherent
    back (v3); `better=2.0` (a later view repaints only what it sees 2x better) a cleaner one (v4).
  - `colour_lock(mode="hsv")`: the game's hue and saturation, the painting's lightness. Gold hair, navy clothes, skin
    on Cloud's right shoulder (the extra pauldron is then a faint drawn shape), Barret's olive vest and green
    trousers.
  - **Picked: Cloud v4, Barret v4.** v5 (round views restyled whole) is about the same. Seen from the battle camera,
    each reads as drawn but small: `work/models/battle/atlas/combined_469_dd-v10_rt-v4_sc-v4.jpg` has the whole frame
    with all three painted, next to the target.
- **Recipes** (`model_views`, `MODEL_VIEWS`; `make()` and the CLI use them):
  - Zolom: v8's views and words, `match`, the fangs white, the tongue as a moving part, then `colour_lock("hue")` and
    the target grade.
  - Party: close views first, battle cameras last; Cloud's first view his front, Barret's his back with `better` 2.0;
    then `colour_lock("hsv")`, no grade (Cloud's pixels in the target's framing were already within a level or four
    of the target's in Lab).
- **Not done:**
  - every pose and formation uses the same atlas (parts are rigid); only the painting views come from formation 469;
  - the weapons for nn other than 00 are named by assumption;
  - Zolom's coils are still faceted in lightness, and the party is small from the battle cameras, so the 2D look shows
    most on Zolom.

### Variants, the AI check, the touch-up, 2D in the viewer (2026-11-07)
The user asked for these after the 2D paints needed hand-iterating. Code: `ff7bg/models/paint2d_extras.py`; the
Studio's "Paint in 2D style" card and viewer (`studio/plugins/models.py`, `models.js`). battleatlas.py is unchanged.
- **Variants:** one paint job paints 1-4 versions, each from its own random seed (the look's `seed`, which the
  workflows give the sampler). `seeded()` swaps the seed into `restyle.look_settings` for one `make()`. Each is a normal
  version; info.json `studio` has `batch`, `variant`, `try`, `seed`. The card shows one job's versions side by side.
- **The AI check** (`check()`, after every paint and touch-up; `check.json` and `preview/check.jpg` in the version):
  - The painted model and the original are drawn from 10 angles (12 when the recipe knows the arms: each shoulder close
    up), on a plain background.
  - Each pair goes to the describer (Qwen3.5 9B, `comfy.CAPTION_CLIP`) as two pictures, with up to three questions:
    - COMPARE: anything added, the same counts, the same colours. The model's words and the recipe's hint for that
      view go with it.
    - COUNT: shoulder pads, open mouths, tongues, heads and arms in each picture, compared in code.
    - SHOULDERS (shoulder views only): skin, cloth or armour on each shoulder, in each picture.
  - It fails when:
    - a part is counted more in the painting, in any view;
    - armour sits on a shoulder that is bare in the original, in 2 views or more;
    - COMPARE finds something added in 4 views or more;
    - the colours differ in 80% of the views or more.
  - It takes 17-25 s a version (45 s for the modded high-poly Barret).
  - "Repaint automatically when the AI check fails" (on by default): a failed version is painted again with a new seed,
    up to 2 more times. The failed ones stay; the first that passed becomes ★.
- **Measured** on the existing versions (ground truth by eye; real AI, 4 rounds of wording on the same set, so these
  numbers are optimistic):
  - Known problems: 5 of 6 caught.
    - Cloud rt v1 (the second pauldron) was caught by SHOULDERS. COMPARE and COUNT missed it: the describer reads the
      game's low-poly bare shoulder as a pad too.
    - rt v2 (more armour), and Zolom dd v2, v6 and v7 (an open mouth and a tongue) were caught.
    - Missed: rt v3, a faint skin-coloured pad shape left after the colour lock.
  - Clean versions: 0 of 5 failed (dd v10, v12; field Cloud aaaa v4, v6, v8).
  - Unclear ones:
    - rt v4, v5 (faint pad shapes) and aaaa v1 passed.
    - sc v4 failed on colours: its painting no longer fits the modded Barret.
    - sc v6 (smeared eyes) passed: the check misses lost details.
  - Wordings that failed: an "invented / description" wording failed every version, and the two pictures side by side
    in one image did no better. COMPARE alone flagged a view or two of clean versions ("markings on the face", "a mouth
    and teeth" where the original's are faint).
  - Calibration files: the session scratchpad, `x2d/calib*`.
- **Touch-up** (the card's Touch up, or "Touch up this view" in the viewer; queue job `model_2d_touchup`):
  - A version's view is drawn from its painted texture. The views are the battle camera, 8 sides, 2 of the head, or the
    viewer's own camera in any pose (`Battle.placed_camera`). Their cameras are stored in the version's `views.json`.
  - The marked areas are repainted with the version's look and words, at the strength asked. This is touchup.py's
    crops, fades and colour match, through the states pass's masked repaint. Inside the marks the AI starts from the
    model as the game colours it (the unpainted atlas, as paint() shows it), not from the painting: started from the
    painting, Cloud's invented grey pad came back blue and gold.
  - Only the changed pixels go back, through paint.Painter.add, onto the texels the camera sees there (`project()`).
    Then the version's colour lock is applied and a new version is exported, with its pictures and the check.
  - Real test on rt v1's pad (sandbox copy):
    - 0 pixels changed outside the marks.
    - The grey pad went: grey 91% -> 0% with words, -> 49% without. Without words, the lock made the rest skin-toned.
    - It stayed a rounded pad shape, though. With "his bare right shoulder, skin, no armour, no shoulder pad" (unlocked
      then) the AI drew a red ring round it, so the lock now applies with words too.
    - The new version still failed the check: the pad shows in other views.
- **The viewer:**
  - Any 2D version (battle, or fieldatlas' field builds) on the model, as the game draws it: flat (the texture's colour),
    with texels at half alpha or less cut out.
  - Field models are rounded as `char_round` does (`pn_round`: PN triangles, a 70 degree crease); a toggle turns it off.
  - Battle models can be shown in each animation's first pose (only frame 0 is decoded: no playback).
  - Right-drag pans.
- **Not done:**
  - animation playback;
  - touch-up for field models;
  - the check misses lost details (smeared eyes) and faint leftovers of an invented part.

## Painted field models: the field atlas (2026-11-07)
The battle atlas for the models the game draws on the fields: `ff7bg/models/fieldatlas.py`. FFNx-3d (main session)
treats field parts like battle parts under `mesh/field/atlas/`, one texture per part.
- **Files** (per part, named as the game loads it: FFNx.log "reading p file: char/AAAC.P", upper case):
  - `<P>.png`: the part's own RGBA texture, named by the part file's stem (`AAAC.P` -> `AAAC.png`). Field parts don't
    share a model name the way battle parts do, so the model's atlas is cut per part, texel for texel
    (`field._part_texture`, as `export_part` does for external meshes).
  - `<P>.P.uv`: FORMAT.md's FFUV v1 against that texture; numverts/numpolys of the .p the game loads (direct/char).
  - Light is the engine's (flat light for painted parts, char_round): nothing here writes lighting settings.
- **The game's model:** the user's chibi field models in `workingdir/direct/char` replace char.lgp, so `field.py` now
  reads direct/char first (`field.char_read`, as `battle.read_file` does for direct/battle; the catalogue's cache key
  includes direct/char). Note this also changes what the Studio's field models show: the chibi models, not
  char.lgp's (the 2026-11-06 choice was the original's).
  - `pfile.read` takes the chibi parts' endings: all 3853 direct/char .p files end in the per-vertex normal index
    table (0..nv-1), flagged or not, and 2939 of them in a u32 0 after it. Before, half the models failed to load
    with direct/char first; now all 396 load, and battle.lgp's files read as .p are the same 8979.
- **Cloud, `AAAA`:** posed with ACFE frame 0 (the standing pose most fields start him on). 16 parts, exactly the 16 the
  log shows the game loading: AAAC AAAE AAAE1 (the Buster Sword on his back) AABA (head) AABF AACB AACD AACF AADB AADD
  AADF AAEB AAED AAEF AAFB AAFD; counts e.g. AAAC 38/72, AABA 442/522.
  - The head is two layers of the same 257 polygons: group 0 untextured (peach vertex colours) and group 4 textured
    (CL: hair and face), with the eye and mouth decals (groups 1-3, 70-96% cut out) between. FFNx draws groups in
    order with LEQUAL, so the later layer shows; render.rasterize's strict test speckled the two. `twins()` finds such
    layers; renders and painting leave the earlier one out (`FieldAtlas.seen`) and `copy_twins` paints it like its twin.
- **Atlas:** 2048 (33.6 texels per unit; the mod's own textures give the model 19-46, the face 46). Identity (atlas
  against the original, bilinear x vertex colours): body mean 0.9, p99 5; head close-up mean 1.5, p99 12 (the face's
  texture resampled). The exported per-part files against the atlas: exact (max 1 level). `check()` re-reads the game's
  .p counts and samples every polygon's middle in its part PNG against the atlas.
- **Painting** (`paint`, battleatlas.paint's loop without battle cameras): painterly at 0.55 plus the 2D words, the
  model on a plain sky gradient: the body from 10 sides (the party's 9 plus from above behind: field cameras look
  down), the head from 5, arms and hands, hands, the sword, the torso's sides with the arms left out (`solo`: the
  arms cover them standing), then from below (soles). ~150 s a version. Then `colour_lock` (the game's hue and
  saturation; field's version also keeps the game's greys grey), the decals restored, the twins copied.
- **What the variants showed:**
  - v1-v3 (0.55; v2 `better` 2.0; v3 0.62): the bodies read as painted, but the head's close views painted another,
    older face with smaller eyes, which landed round the model's eye decals as smears; the AI's purple cast stayed on
    the near-black gloves, the steel pauldron and the sword (`colour_lock` leaves greys alone).
  - v4: the decals keep the game's drawing (`keep`), the face's close views repaint at 0.40 with "big round anime
    eyes"; greys locked. Clean chibi eyes; faint brown marks on the forehead. v5 (body 0.62): more marks, browner hair.
  - v6: as v4 with the face at 0.30: the cleanest face.
  - The top and back of the hair (what a field camera sees most) came out blotchy with brown patches and a dark
    scribble. v7 (the hair's views at 0.35-0.45) and v8 (every later view at 0.45) didn't help: it wasn't the AI's
    strength. The first view sees the hair's top edge-on, its samples landed scattered with the original between them,
    and the later views painted from that state; and paint.Painter.images() colours unpainted texels with one linear
    fit over the whole atlas (a dotted black line along a seam on the right upper arm, red specks on the sword), while
    Painter.fill_holes grows paint into neighbouring charts. Fixed in the pipeline (an identity dry run then matches
    the original closely):
    - `paint(facing=FACING_RAMP)`: a view's pixels count from |cos| 0.3, fully from 0.55, so surfaces seen edge-on
      wait for a view that sees them squarely;
    - `compose()`: the paint where views put some, the rest of each chart from its own nearest paint (whole charts no
      view saw keep the game's colours), used for the final atlas and for what the next views see.
  - **v9 (picked):** v6's recipe (face 0.30, `better` 2.0, decals kept) with those fixes. The hair's top is smoother,
    the scribble and the arm's dotted line are gone. v10 (v8's strengths with the fixes) is about the same, a little
    softer.
  - The look's describer captioned the first view as the background alone ("a plain gradient ... empty and
    featureless"), as it did for the battle party (rt v4): a lead for later (`describe=False` for model views) not tried.
- **Commands:** `python -m ff7bg.models.fieldatlas build|paint|check|install|uninstall|status <hrc> [--version V]`;
  `make()` in Python. Builds: `work/models/field/atlas/<hrc>/<version>/` (`<P>.png`, `<P>.P.uv`, info.json, preview/:
  turntable, head, field, identity, files_identity, the views, atlas.png). `install` copies a build into
  `<workingdir>/mesh/field/atlas/` with a manifest (`work/models/field/atlas/installed.json`), battleatlas's rules.
  `identity` is the unpainted build, for checking the engine path.
- **Judgement (v9):** it reads as a painted version of the chibi Cloud: painted shading and folds on the clothes, steel
  pauldron and glove plates with drawn highlights, painted strands in the hair, the game's colours, the chibi shape
  and the game's big anime eyes. It's a gentler change than the battle models' (the mod's textures are already
  hand-painted), with no strong line art; the hair's top and back are still a little faceted and blotchy where views
  meet, and faint brown marks stay on the forehead and cheek. Not yet seen in game.
- **Not done:** only Cloud has a recipe (`RECIPES`; other models get the body and head views only); the PNG alpha
  carries the decals' cut-outs, so the engine must alpha-test atlas texels where the group did; effect (additive)
  groups aren't handled (Cloud has none); ~25% of the texels (insides, where parts meet) no view sees: filled from
  their chart's paint.

## Battle floors that reflect: the Floor card (2026-11-07)

User: "the puddles should look wet, the grass should have blades that move. Metallic floors should be somewhat
reflective". Offline mock-ups first (work/models/floor_mock), approved; then:
- Engine (FFNx-3d, ff7-3d/dev/battle-engine/FORMAT.md section G): before a battle frame's draws, its 3D draws again,
  mirrored about the ground (y = 0), into an image of their own; floors with a `reflect` entry show it where they are on
  screen. Water ripples over a darker wet body (Fresnel from 0.02); metal is blurred and tinted (from 0.5). Masks are
  `mesh/battle/<texture>_reflect.dds`; the settings go in `[floor_STAGE<nn>]`, apart from the battle look's section.
  Cost at 4K internal with MSAA 4: GPU 18% -> 19-20%; battles stay at their 15 fps.
- Data and Studio: `ff7bg/models/floorreflect.py` and the Floor card on battle scenes (models.py floor_*, models.js
  renderFloor). Each floor texture (scene textures with polygons on the ground) is Off, Puddles (water found by colour),
  Water, Metal (soft) or Polished (sharp), with a strength; a preview shows where. Install and Uninstall only on the
  user's click; off unless installed.
- In the game since the test: oo (both floor textures, puddles 100%) and op (metal 50%), installed through the card.
- Grass blades, the same night (engine `grass`, FORMAT.md section H; the Floor card's "Grass" choice per texture: off,
  short 26 or medium 36 units, where the texture's colour is grass and not water). First try: blades thinner than a
  pixel by the fighters (about 7000 units from the camera) read as sparse dark stalks; now never under a window pixel
  wide, 3x denser near the camera, lighter with warm tips. On the marsh about 60k blades a frame: GPU 12% -> 16%.
- In the game: oo has puddles 100% and short grass on both floor textures; op metal 50%.

## Order
Shared core first (formats, mesh, renderer), then battle scenes end to end (static, unlit, real cameras), then
battle models (posed), then field models (posed; generated UVs and glTF for untextured parts).
