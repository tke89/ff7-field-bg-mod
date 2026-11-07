# Painted textures for untextured battle models (2026-11-07)

**Goal (user):** a 2D anime look in battles. The target is an AI repaint of a battle frame
(`work/models/battle/target2d/`, if saved). In it the characters and monsters are drawn: flat fills, painted shadow
shapes, thin coloured lines. Shaders and screen filters were tried and rejected: "those look awful", "3d with thick
black lines". The drawing has to live in the models' textures, rendered with flat light.

**Problem:** FF7's battle models are mostly untextured: vertex-coloured polygons. Midgar Zolom (`dd`, the game's
`ENEMY081`) has no textures at all, and a Grunt has 175 textured triangles out of 724. A painting needs a texture
on every triangle. FFNx's external meshes don't work in battles: upstream left the battle model draw replacement
commented out.

**Solution:** a per-model atlas texture and per-part UV files. FFNx-3d draws a part's triangles with the atlas
instead of its vertex colours (and its own textures).

## Files (in the game's workingdir)
- `mesh/battle/atlas/<MODEL>.png`
  - One RGBA image per model: `<MODEL>` is the game's name, e.g. `ENEMY081`.
  - It holds every triangle's whole colour (the vertex colours or texture as the game draws them, or the painting
    over them).
  - Any size up to 4096; power of two preferred.
- `mesh/battle/atlas/<MODEL>.<Pnn>.uv`
  - One file per part, named as the game loads the part (`ENEMY081.P05` for `battle/ENEMY081.P05`; FFNx's log with
    `trace_loaders = true` shows "reading p file: battle/ENEMY081.P05").
  - Binary, little-endian:
    - `char magic[4] = "FFUV"`, `uint32 version = 1`, `uint32 numverts`, `uint32 numpolys`;
    - then `numpolys` x 3 corners x (`float32 u`, `float32 v`).
  - Polygons are in the .p file's own order (its polygon table), corners in its order (vertex1, vertex2, vertex3).
  - `numverts` and `numpolys` are the .p file's own. FFNx-3d checks them and ignores the file when they don't match.
  - u, v are 0..1 across the atlas, v = 0 at the image's top row (D3D / FFNx convention).
- No `.uv` file: the part draws as the game does. A model may have atlas files for some parts only.
- **Party weapons:** the game loads the weapon a party member holds as `battle/<MODEL>.W<nn>` (FFNx.log, 2026-11-07:
  `CLOUD.W00` and `BARRETT2.W00` with their first weapons, battle.lgp's `rtck` and `scck`). FFNx-3d takes a `.uv`
  for any `.p` file under `battle/` and the atlas by the name before the dot, so `<MODEL>.W00.uv` paints the weapon
  from the model's own atlas. That nn is the weapon's index (role `ck` + nn) is assumed for the others, not seen; a
  weapon without a `.uv` draws as the game does.
- **Moving parts:** parts are rigid, so one atlas holds for every animation; but a part that moves apart from its
  neighbours (Zolom's tongue, P13-P16, which flicks out; its jaw, P10) must carry only its own paint. A painting made
  in a pose where it's hidden can still draw it onto its neighbours (Zolom v6 had a tongue painted down its face:
  two tongues in game). The data side paints such parts on their own (battleatlas.MOVING).

## Engine (FFNx-3d, local fork)
- **Load:** when a .p file under `battle/` loads, look for its `.uv`.
  - Validate it and keep it with the polygon data.
  - Load the model's PNG once, and free both with the polygon data.
- **Draw** (the lit and unlit paths, and the shaped path when it's used): a part with an atlas draws each triangle's
  three corners as their own vertices.
  - Each corner takes its original vertex's position and normal (FFNx's own normal calculation, as now), the atlas
    u, v, and white vertex colour (alpha kept).
  - It binds the atlas PNG as the texture and draws as a textured lit draw. A group's polygons are the polygons
    `offpoly .. offpoly + numpoly` of the .p file; record each indexed_primitive's group offset when it's built.
- **Config:** `battle_atlas = false` turns it off (any group; default on when files exist). Hot reload of the files
  isn't needed: a new battle reloads the parts.
- **Trace (trace_loaders):** "battle atlas: ENEMY081.P05 (n polys) from ..." or why it was refused.

## Data (ff7bg/models, Studio repo)
- **Atlas:** an atlas of a battle model's triangles in part space (parts are rigid; the atlas is pose-independent),
  filled with what the game shows now, i.e. vertex colours (Gouraud) or texture x vertex colours.
  - `atlas.py` does this for field models.
  - **Identity check:** a render of the atlas-textured mesh matches the original render within a few levels.
- **Painting:** the model's views from the battle cameras of its formations (scene.bin; 469 for Zolom) plus close
  views, painted with the Studio's look and words, then projected back into the atlas (the Models projected method).
  - The party (Cloud `rt`/CLOUD, Barret `sc`/BARRETT2 in formation 469) isn't in scene.bin: it stands where the game's
    frame shows it (battleatlas.PARTY_POSITIONS), facing -z. It's small from the battle cameras, so it's painted from
    close views all round, the battle cameras last.
  - The 2D target's settings: look `painterly`, repaint 0.55, words: "A frame from a hand-drawn 2D anime: ... drawn
    flat in cel animation style, clean shapes, flat colours with simple painted shadow shapes, thin coloured line
    art, no 3D shading".
- **Export:** the PNG and the `.uv` files under the game's part names.
  - **Install:** `mesh/battle/atlas/`, only when asked, with a manifest so uninstall removes exactly those files.

## Look in game
Flat light on the characters, so the painting carries the shading: `toon = 1`, `toon_shadow_color = [1, 1, 1]`,
`toon_rim = 0`, `toon_highlight = 0`, no outline width (in `[bat_469]` or `[battles]`).
