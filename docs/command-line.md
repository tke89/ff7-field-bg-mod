# Command line

`ff7bgmod.py` works without the Studio or ComfyUI. Use it to paint backgrounds by hand, to bring in images from
any outside generator, or to check the format code. Run it with the project's Python:

```bat
.venv\Scripts\python ff7bgmod.py <command> ...
```

Every command takes `--lgp <path to flevel.lgp>` if your game isn't in the default Steam folder.

## Paint a field by hand

```bat
.venv\Scripts\python ff7bgmod.py list md1          :: find map names (md1stin = the opening train station)
.venv\Scripts\python ff7bgmod.py export md1stin    :: -> work\md1stin\  (4x by default; --scale N to change)
```

`work\md1stin\` contains:

| File | What it is |
|---|---|
| `preview.png` | the scene as the game shows it by default; paint over this |
| `sheets\L0.png` | layer 0: the floor or back wall, always behind characters |
| `sheets\L1.png` | layer 1: scenery characters can walk behind (tiles carry a depth value) |
| `sheets\L2.png`, `L3.png` | extra back and front layers, often scrolled |
| `sheets\*_pNNsMM.png` | toggled tiles (doors, lights, animations): param NN, state MM |
| `sheets\*_blend-*.png` | translucent effects (glows, light beams) drawn with add, average or subtract |
| `sheets\*_dupN.png` | tiles that overlap another tile in the same sheet |
| `manifest.json` | where every tile came from (used by build) |

**Option A, one flat image (easiest).** Paint a complete replacement for `preview.png`, at any size with the same
aspect ratio (2048×2048 for a 512×512 map at 4×), then:

```bat
.venv\Scripts\python ff7bgmod.py build md1stin --flat C:\path\to\my_md1stin.png
```

The flat image replaces the static scenery in layers 0–1. Doors, lights, blend effects and layers 2–3 keep the
original art unless you also edit their sheets.

**Option B, edit the sheets.** Edit any `sheets\*.png` in place, at the size they were exported, then run
`build md1stin` without `--flat`. You can combine both: `--flat` for the scenery and edited sheets for the doors
and effects.

Build writes `dist\mods\Textures\field\md1stin\md1stin_NN_00.png`, plus `work\md1stin\preview_modded.png`
reassembled from the new pages the way the game draws them. By default the game's transparency mask is kept
(`--mask original`): pixels see-through in the original stay see-through, which keeps the layer-1 cut-outs lined
up with the depth data. `--mask sheet` uses your PNG's alpha instead.

## Images from an outside generator

```bat
.venv\Scripts\python ff7bgmod.py ai-prep --margin 0.08   :: -> ai\input\<map>.png for all 670 unique backgrounds
   ...run the ai\input images through your generator and save the results as ai\output\<map>.png (or .webp, .jpg)...
.venv\Scripts\python ff7bgmod.py ai-build                :: -> dist\mods\Textures, ai\review, ai\report.csv
```

The Studio does the same thing per field with **+ Import image**, and keeps the result as a version.

- **Inputs** show each map's default view at native size. Maps sharing a background are sent once and written to
  every copy.
- **`--margin 0.08`** adds a blurred 8% border, because generators tend to zoom in and crop the edges.
  `--aspect 1:1,3:2,2:3` pads to the closest ratio for generators with fixed output sizes. Both are removed on
  the way back in.
- **The output size sets the resolution.** A 640×480 map returned at 2560×1920 builds at 4×. Any size works as
  long as the framing matches the input.
- **Alignment** undoes the drift with one overall transform ([How it works](how-it-works.md#alignment));
  `--no-align` uses the images exactly as returned.
- **Review:** `ai\review\<map>.jpg` shows the original, the AI output, the in-game result and a check panel. In
  the check panel, red is the original edges and green the walk-behind cut-outs; the new art should sit on those
  lines. `report.csv` flags maps that changed the layout or needed more than 1% filled from the original.

**If you also use the Studio, give both commands a separate `--dir`** (for example `--dir ai_cli`). `ai-prep`
rewrites `ai\jobs.json` and `ai\input`, and with `--margin` or `--aspect` it changes the input framing that the
Studio's versions were made with.

## Verify

```bat
.venv\Scripts\python ff7bgmod.py verify md1stin    :: no names = every map
```

Rebuilds fields from their own exported art and checks the result matches the game's pixels exactly. It's a
check on the format code, not on your art.
