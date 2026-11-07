# FF7 fields in 3D (FFNx-3d)

Turns a field's Studio render into a real 3D scene the game draws through the FFNx-3d fork (a local FFNx build
with the 3D work, kept beside this repo as `FFNx-3d`; set `FFNX3D` to put it elsewhere; not published).
Lives in the Studio repo as `ff7-3d/`; the Studio loads it as a plug-in (`studio/plugins/ff7_3d.py`).

    python run.py build eals_1 mds7pb_1     # or: build pilot
    python run.py review eals_1             # game running FFNx-3d: jump there, screenshot
    python run.py sheet                     # contact sheets + report/index.html

Per field, `work/<field>/` keeps every step (depth, Marigold colour/normals, AO, fit report, sheet). Options: the
defaults in `ff3d/build.py`, then the set every field follows (`work/options_all.json`), then a field's own changes
(`work/<field>/options.json`, e.g. `{"sway": true}`). The characters' settings (`build.GAMEWIDE`: shadows, haze,
colour match, light where they stand, outline, softness, the shadow under them, finish, brightness) are the same in
every field: lighting/config.toml's `[fields]` (written by every look step and every All-fields change), which
FFNx-3d reads after a field's own section and before the file's top level, so a change shows at once everywhere.
A field's section keeps only its own part of them (`char_grade_full`, `char_outline_scale` / `_color`, its floor
light map). Every writer of config.toml holds `ibl.config_lock()`.

The ground: Depth Anything 3's depth, fitted to the walkmesh with one scale and offset, then bent onto it by a
smooth correction (`depth.anchor`, `anchor` 4% of the grid) fitted on walkable surfaces that face up (Marigold
normals), so building fronts, fences and walls keep standing in front of the walkmesh behind them. Floor-like
ground within 3% of its depth (`snap_rel`, at least `snap_tol`) then snaps onto it. Farm went from 10.8% median floor
error to 0.8% (scenes built before 2026-10-25 have the old, single fit).
A walkmesh that isn't in the picture (gldelev, junone3, md0: just below it) can't be fitted on: DA3 is anchored
where it comes nearest the picture, with the depth spread fitted scenes usually have (`depth.off_picture_fit`).

Output in the game folder: `mesh/field/<field>/` (scene), `mods/3dtest/field/<field>/` (hidden 2D pages and
animated glow frames), `lighting/ibl/field_<id>_*.dds`, and a `[field_<field>]` section in `lighting/config.toml`.

In the Studio, the 3D tab (`studio_plugin.py` / `studio_plugin.js`, loaded by the Studio's `studio/plugins/ff7_3d.py`)
builds a field's scene from its current version through the Studio's queue, switches its look, and drives the game.
Builds use the version the game uses; `report.json` records which, so the Studio can tell an out-of-date scene.
With the Studio's Automatic setting at "final" (the default), every final render that goes in game gets its 3D scene
next in the queue. The 3D tab edits either All fields (`options_all.json`: up-to-date scenes take a change in
seconds, the others when rebuilt) or This field only (its `options.json` keeps only what differs from all fields;
"Same as all fields" drops them). "Rebuild every 3D scene…" (All fields) rebuilds every scene from the image its
field has in the game, optionally dropping each field's own changes (Shape ones, i.e. flower sway, can stay).
Until 2026-11-01 each field's `options.json` was a full copy of the settings it was last built with and new scenes
started from the last ones used (`options_last.json`); `build.adopt_shared` turned that into the above once, with
every field looking the same as before.

The preview gets a 3D mode next to Original / New / Split (`studio_view3d.js`, key 4): the field's scene in WebGL2
from the game camera, framed like the 2D view so the two line up. Drag turns around the scene, right-drag moves,
the wheel goes closer, double-click goes back to the game camera; Painted / with light / Shape only. It draws the
mesh at rest (no sway) and without the 2D overlays the game adds.

The two looks both show the painting. Painted lights it gently with the game's light (sun 1.4, sky 2.3). Relit
(`ff3d/relight.py`) shows painting x (the light on it now / the light on it in the painting), both worked out the
same way in the fork's shader on a white surface, so with the painting's own sun it is the painting, and with the
sun moved (`sun_turn`, `sun_height`) its light and shadows follow. Both start from the painting in AI 2D's colours
(`glow.scene_colours`): the Studio cuts its pages after a colour pass (`keep_colors`, the user's 0.8), and until
2026-11-02 the scene had the render's own colours instead (datiao's statues olive, the crater's rocks orange-patched,
Gaea's Cliff blue, sector 8 green, all under the look's saturation). Relit:
- the painting's sun: shading (Marigold normals) and cast shadows (marched over the depth grid) against painting /
  de-lit colour, 15-80 degrees up, cached in `relight_sun.json`; its sun-to-sky strength sets the scene's (the game's
  is ~0.85 at 3D shading 50%);
- `<field>_scene_sun.dds` (fork tex_12): R where the sun reaches now, G where the painting's did, B where the scene is
  relit (not the sky, grown from Qwen's box by colour, water or the lights themselves);
- lighting keys `relight` [on, most it brightens (3), brightness, lamps], `relight_sun`, `relight_colors` (the
  painting's sunlit and shaded colours, `light_tint` of the way from white);
- interiors and scenes with no clear sun: no sun change; their lamps (`point_lights`) flicker on the walls.
Relit used to be Marigold's de-lit colour with its chroma restored: painted shadows half stayed, the colour step put
the sunlight back as orange patches, the sky went blotchy, and it took no shadows.
Since FFNx fda0e493 the sun's shading never followed `light_rotation_*` (lightDirData was no longer filled; only the
shadows moved): the fork fills it again in fields.

Around the characters (`ff3d/atmos.py`, applied by the look step, sliders in the 3D tab):
- the sun's direction fitted to the painting's shading (Marigold normals against shading = painting / de-lit colour),
  40-80 degrees up; a weak fit keeps only its side, with a high sun (short shadows); none: FFNx's default;
- character shadows on the walkmesh (FFNx's field shadow, `shadowmap_occlusion` = 1 - strength): the scene's ground
  is estimated and can be well off the walkmesh (farm: 4-36% of the depth), and shadows cast onto it slid away
  from the feet; the scene itself casts and receives none (`scene_shadow_strength` 0);
- haze on characters with distance (`haze_*`), from the painting's farthest colour and how its contrast falls with
  distance, at most 20% at the far end of the walkmesh, and never more than the painting's own darks show where
  characters stand (`atmos.haze_dark_bound`: haze adds its colour in linear light, as the shader blends it, so true
  blacks at a distance mean no haze there). Contrast alone read the far sea and sky's smoothness as haze: del1's party
  got their blacks lifted by a third. Since 2026-10-31 only 5 of 226 fields keep more than 1% at mid-walk (the
  glacier, the ice caves);
- glow: `<field>_scene_glow.dds`, the painting blurred where the original art has additive light layers, drawn by
  FFNx-3d on the scene where it's what's seen (depth equal: not over characters in front, not again from hidden
  layers such as the crack fills), under the text windows. The file holds the difference its light (linear) makes
  over the scene's colour, gamma-encoded (`atmos._write_glow`), and the fork loads it sRGB so the screen gets exactly
  that. Until 2026-11-01 the file held the light itself and was gamma-encoded twice on the way (loaded as data):
  faint light showed about 4x too strong and drowned cos_top in it.
- the characters' colour grade (`atmos.grade` -> `char_grade`, 3x4 on sRGB): how the new art differs from the
  original in contrast/brightness, saturation and the light's colour (from the original's neutral surfaces), bounded,
  `char_match` of the way (0.75);
- the characters' fill light (`char_fill` -> `char_ambient`, FFNx-3d swaps the ambient strength for everything lit
  but the scene): `build.CHAR_FILL` x brightness at 100%. With Shading high the sun takes most of the light and,
  until 2026-11-01, characters went nearly black wherever it missed them (anfrst_1), while the relit scene was fine;
- the characters' own image-based light, `lighting/ibl/field_<id>_char_{s,d}.dds`, tinted from the painting
  (`char_light`, 0.6); the scene keeps the neutral one.
- light where they stand: `<field>_floorlight.dds` beside the scene, the painting's light on the walkable floor
  (painting / Marigold de-lit colour, log-averaged per cell, against the scene's middle, slight tint) top-down over
  the walkmesh's world rectangle (`floor_light_rect`); FFNx-3d multiplies characters by it at their world xy
  (`char_local` 0.6 -> `floor_light_strength`).
- glow also from the painting's own lights (`atmos.painted_lights`: pale, near the top of the range, blobs not lines,
  alone, well above their surroundings; weighted to dark scenes), compact sources only (broad light minus its
  surroundings), capped (GLOW_CAP);
- characters' outline (`char_outline`, 0 by default: for outlined styles) = an inverted-hull pass in the fork, width
  OUTLINE_MAX bg px at 100% (`char_outline_width` per unit distance), colour the painting's darkest line work;
  softness (`char_soft` 0.35 -> up to 3 px texture blur). Until 2026-11-01 the outline pass left the part's own draw
  on the flat-shaded program, so every outlined character came out faceted (each triangle one colour);
- the models less faceted, in the fork only (`ffnx3d_shape.cpp`, on in every field unless the lighting config says
  `char_smooth` / `char_round` = 0, per field or at its top): shading flows across faces meeting at under 70 degrees,
  and each triangle is split in four with its edge middles pushed onto that curve (PN triangles; sharp and open edges
  stay straight, so parts don't crack). The live status's `shaped` line counts the parts. Rounding is off since
  2026-11-01 (`char_round = 0.0` at the top of lighting/config.toml): it bulged heads over eyes drawn as separate
  pieces in front of the face (the bar's NPCs lost their eyes); the smooth shading stays.
These need the FFNx-3d build from 2026-10-25 (FSTexFlags.w scene flag, fieldHaze uniforms, SceneMesh::drawGlow).

Toggled layers (smoke, haze, light shafts, animation frames, a ship that docks) stay 2D tiles the game draws and
switches over the scene (`glow.overlay_plan`): only where the original shows them. A solid tile a script switches on
shows only where it changes the picture (more than `OVERLAY_SAME` levels: a layer with its own palette repeats the sky
around it as speckle), so the scene shows the rest at its true depth. The fork draws solid ones over the scene before
any character (`deferred.cpp` `draw_scene_overlays`: each takes the scene's depth away under itself, then draws as the
game asked), so they show as in 2D and the characters sort against their tile depth as they did there. Blended ones
(smoke, haze, shafts) have the scene moved back behind them (`build.push_under`, at most 3x). Tile depths are often
only for sorting (del1's ship: 4094, five times the pier's in front of it): before 2026-10-31 the scene was moved back
behind solid ones too, the cap left most of del1's ship hidden behind its pier, and a scene built before then keeps
those moves (rebuild to drop them). Their art comes from the version's pages, which the Studio cuts after a colour
pass, so it's mapped to the scene's colours first (`glow.page_colours`, a 3x4 fitted on the static tiles, used only
where it explains the difference): otherwise the sky in a tile shows a shade off the scene's around it.

The sky drift (`life.sky_mask`, the effects map's R): GrabCut at quarter size, then `life.sky_scenery` takes out what
stands in front of the sky at half size (detail and long straight edges, grouped, keeping groups that stand on what's
below the sky and rise into it: masts, rigging, a white ship among white clouds, mountain tops; floating, flat cloud
texture stays sky). The drift fades out near every hole, so it never drags them. R also carries all the sky (grown
from that, closed over thin gaps) at `SKY_FLOOR`: the fork keeps cloud shadows off it, since the scene's sky is often
near enough to sit inside the sun's shadow map (del1's showed grey plumes once its scene stopped being pushed back).

The mesh is simplified (`ff3d/simplify.py`): big flat cells wherever depth stays within 0.5% and the texture within
0.25 background px of the full grid, full detail at depth breaks; the shading detail goes into `<field>_scene_nml.dds`.
The look step fades that map out (`surface.steady`) on surfaces nearly edge-on to the camera and right beside depth
breaks, where FFNx's shading frame (from screen derivatives) is unsteady: until 2026-11-01 it showed as bands of
bright dots along object edges (ancnt1 under its platform).
FF7 is 32-bit, so scene size is address space: `{"simplify": false}` builds the full grid for comparison.

tools/: `live.py` (status / jump / shot / reload while the game runs), `ctl.py` + `walk.py` (window, keys),
`apply_profile.py` (7th Heaven profile -> FFNx direct mode), `build_ffnx.ps1` + `install_ffnx3d.sh` (FFNx-3d),
`mem.py` (the game's memory across reloads and field changes), `ab.py` (render versions side by side in game),
`watch3d.py` (follows the Studio's builds and captures each new scene as 3D / AI 2D / original -> report/review/).
Jumps and view switches also work while an event holds the player (FFNx-3d since 2026-11-01: any field opcode issues
a pending jump; before, astage_b's play or a cutscene on entry held every jump until it ended).
