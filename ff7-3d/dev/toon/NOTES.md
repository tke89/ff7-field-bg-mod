# Anime (toon / cel) shading: prototype (2026-11-07)

The user asked for a shader that makes fields and battles look like 2D anime. Anime pairs painted backgrounds with
flat-shaded, outlined characters. We already paint the backgrounds (the Studio's fields and Models' battle scenes),
so this shader is for the 3D characters and enemies. It runs in FFNx-3d's lighting shader (`misc/FFNx.lighting.frag`,
in the local fork).

## Keys
They go in `lighting/config.toml` under any group: `[field_<name>]`, `[fields]`, `[bat_<n>]`, `[battle_STAGE<nn>]`,
`[battles]` or the top level. They hot-reload.

| Key | Default | What it does |
|---|---|---|
| `toon` | 0 (off) | How much: 1 is full cel shading, in between mixes with the normal shading |
| `toon_threshold` | 0.2 | Where shadow starts (the share of the sun's light a surface gets). Higher puts more of each body in shadow |
| `toon_softness` | 0.05 | How soft the light/shadow edge is. 0.02 is crisp, 0.15 is soft |
| `toon_bands` | 2 | 2: light and shadow. 3 adds a half-lit tone |
| `toon_shadow_color` | [0.68, 0.70, 0.86] | The shadow side against the lit side: about 70%, a little cool. Closer to 1 is lighter |
| `toon_lit` | 0.75 | How much of the sun the lit side gets |
| `toon_rim` | 0.3 | Bright edge along the silhouette (0 is off) |
| `toon_highlight` | 0.25 | Small hard highlights (0 is off; 0.6 looked like plastic) |
| `toon_outline_own_color` | 0.7 | Outlines in a dark shade of each surface's own colour (0 is `char_outline_color`) |
| `toon_scene` | false | The battle stage too (anime keeps backgrounds painted, so off) |

Outlines need a width:
- **Fields:** `char_outline` / `char_outline_scale`, as before.
- **Battles:** `char_outline_width`; 0.003 worked in battle 469.

## How it works
For the characters (not the painted scene):
- **Light and shadow:** the light is cut at `toon_threshold`, after the shadow map, with a smoothstep of `toon_softness`.
- **Lit side:** the ambient/image-based light plus `toon_lit` of the sun, the same whatever the surface's angle.
- **Shadow side:** the lit side times `toon_shadow_color`, so its darkness doesn't depend on the scene's
  sun/ambient balance. The first try used the ambient alone, and battle 469's strong default sun left Cloud nearly
  black.
- **Rim and highlight:** the rim is a hard step on 1 - N.V, and the highlight a hard step on N.H.
- **Outline colour:** the existing outline pass, coloured from the surface.

## Seen in game (battle 469, eals_1)
- **Default light:** flat, brighter tones with dark outlines; Midgar Zolom reads like a cartoon.
- **oo's battle look installed** (overcast light from the painting): the split is soft, and lines and flat colour
  carry it.
- **eals_1:** Cloud is small at the field's distance, so the change is slight (flatter, cooler shadows).
- **Limits:**
  - FF7's low-poly models show their facets in the bands.
  - Only silhouettes get lines; creases inside a body don't.
  - Faces are tiny textures.

## Next, if the user likes it
- Lines inside the body: screen-space edges from depth and normals.
- Studio switches with plain help.
- Anime-style Looks for backgrounds and battle stages.
- Repainted flat-colour character textures (Models).
