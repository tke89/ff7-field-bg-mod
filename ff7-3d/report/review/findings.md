# 3D rebuild review (2026-11-01, every field): the fix list

The user's whole-game rebuild (669 fields, Studio queue from 20:55 on 2026-11-01). Each finished scene is captured
in game by `tools/watch3d.py`: 3D, AI 2D and original from the same spot, sheet in `report/review/<field>.jpg`, raw
shots in `report/review/<field>/` (3d.jpg, ai2d.jpg, original.jpg), one line per field in `log.jsonl`.
User, 2026-11-01: "keep note of all these so we can loop back and fix them". This file is that list.
(Rebuilt 23:35 from the session's record after a failed scripted edit emptied it.)

## Resumed 2026-11-06 19:08 (user: "resume")

- **The 11-03 rebuild queue had finished** (last build 20:09; no failures; every F22 and F20 field rebuilt after its
  merge). The Studio and the game had stopped with the session; both restarted 19:09-19:10. The 3D is up to date
  on all 670 fields.
- **In progress:** the Studio is re-assembling AI 2D for F23's 10 palette fields; the look is being re-applied on
  the 157 fields built before F23's merge; agent F24 (second run) is on the memory fix.
- **Done 19:39:** looks re-applied on all 148 fields built before F23 (no failures). AI 2D re-assembled for the
  10 palette fields; their 3D (9, not md_e1, whose build fails) is queued for a rebuild 19:4x.
- **Dev folders tidied (user, 19:4x: "there's also now a whole bunch of ff7 3d folders"):** the 16 merged
  agents' folders moved from the Desktop to `ff7-3d\dev\<fN>` (and `dev\first`); the paths in this file are
  updated. f24 followed when its agent finished.
- **F24's root cause (19:3x):** bgfx's 4096 texture handles run out. When the view is Original while a field
  animates, `load_texture` refuses externals, so each palette change makes a new 512^2 texture that
  `gl_replace_texture` never destroys (the set is still external and animated).
  - Once the handles are gone, every createTexture2D fails and leaks the decoded image (1 MB per glow frame):
    the 80 MB/s climb, and the 16:59 crash's textureId 65535s.
  - Every watcher capture does 3d/ai2d/original/3d.
- **Fix:** destroy a slot's own textures; unload destroys each once; animated frames go through a shared,
  refcounted cache (one texture per file, 1536-frame LRU); no texture is created without a free handle; loadPng
  refuses when address space is short. New status line: `textures <live> <max> ...`.
  - Built and installed 19:44 (new files `ffnx3d_texcache.cpp/.h`; backup in this session's scratchpad `f24`).
  - **Verified (F24 agent):** two rounds of jump/ai2d/original/3d each in crater_2 (address space 1765-1805 MB),
    ancnt2 (1762-1774) and whitein (1787-1789). Texture handles return to ~69 every time, and the glow still
    animates (`ff7-3d\dev\f24\measure\`). **These fields no longer crash the game.**
  - **Pipeline (merged 19:5x):** `glow.write_pages` merges look-alike frames above 1024 distinct per field
    (`merge_frames`; only bugin3 13029 -> 851 at 20 levels, and ancnt2 1043 -> 1024 at 1 level); identical
    frames are written byte-identical, so the engine shares them. bugin3 and ancnt2 queued for a rebuild.
  - **Watcher (merged):** `MemoryLog` writes `report/review/memory.jsonl` once a second during captures (address
    space, committed, largest free block, and the engine's `textures` line).
  - Not verified: the LRU drop path (no field reaches it in game) and bugin3's merged fades.
- **Where things stand (2026-11-06 ~20:00):** every finding the user asked to fix is fixed and merged, except
  what's listed under the user's calls and open fixes in "Paused 2026-11-03 17:31" below. FFNx-3d DLL
  11-06 19:44 (adds F24). The Studio is rebuilding bugin3 and ancnt2's 3D (F24's frame merge).
- **User 2026-11-06 ~20:0x: "Yes"** to the open options:
  - the full latent/event-states fix;
  - the Studio `scripts.analyze` change;
  - re-rendering the conditional-state fields and colne_6's states;
  - psdun_4's haze.
  Agents started:
  - **F25:** psdun_4's far-push haze, a general fix.
  - **F26:** a trial of `FF3D_EVENT_STATES=1` on a sample in game before any roll-out (F21 recommended off).
  - **F27:** the Studio scripts.py change, plus jobs.json animations and defaults, and prep of the re-render
    list. The main session runs the AI renders with the game closed.
- **F27 done (Studio side, uncommitted; backups `ff7-3d\dev\f27\backup\`):**
  - `ff7bg/scripts.py` got F21's analyze change (loops in every script, variable-state loops); `ai/jobs.json`
    gained `animations` for 28 fields. No 3D change follows: those groups were already in the 3D's script_loops.
  - **Defaults set to the game's fresh-entry state for 11 fields:** blin63_1, blin65_1, blin68_1, blin69_1,
    ealin_2, ghotel, gidun_3, gldgate, hideway1/2/3. Checked against the original view in game, except ghotel,
    which can't be seen from the jump spot. Their `ai/input` images were redrawn to match.
  - Studio restarted ~20:2x. The 11 fields plus colne_6 (description fixed: a mirror ball, not a lantern) are being
    re-rendered with the game closed (scratchpad `renders.py`); their 3D rebuilds follow automatically.
- **F26 (event states), merged ~20:5x:** recommendation (b), implemented. `FF3D_EVENT_STATES` now defaults to
  `"auto"` (`glow.event_states`).
  - A state an event switches becomes a start-on 2D tile only if all of these hold:
    - it covers at most 25% of the canvas;
    - a script really switches it off and the original changes 64 px or more beyond what's switched on with it;
    - at most 50% of that area is a hole;
    - the Studio's off art differs from the render on at least 50%.
  - It keeps 100 of the 167 fields `=1` would change (2509 tiles, against 5629). It excludes whole-scene states
    (bugin1a's room, trnad_1's forest, zcoal_1's engine) and states that gain nothing.
  - In game: on and off differ on 0.2-6% at entry. The 2D tiles show AI 2D's art without the 3D light (onna_52's
    door loses its sheen; onna_4's door frames show AI 2D's seams). Characters sort right against them.
    Evidence `ff7-3d\dev\f26\evidence\`.
  - Roll-out: 81 look fields re-applied (apply_f26), 8 rebuilt (blin68_2, fship_3, junbin5, mtcrl_4, nvdun3,
    onna_5, uttmpin4, wcrimb_1), 8 already done by the agent, and the re-rendered fields get it from their rebuild.
  - **The user's call:** hyou5_2's floes. `=1` showed AI 2D's floe sizes and thin-ice rings (the old "every floe
    plain white" item), with AI 2D's half-floe seams. The rule leaves it out; it needs its own switch.
- **Left for the user (F27):**
  - blin3_1's input image still draws message groups 2-4 that its hand-set defaults turn off (a stale input;
    `ff7-3d\dev\f27\scratch\inputs.py write blin3_1`, then re-render).
  - eals_1 and blin63_t show their shafts/door/shutters on at entry, while their renders have them off.
  - jail1's fence gate is on until story point 457, and the render has it off.
- **Checked in game 21:18-21:25 (watcher, 17 fields; memory flat at 1.6-1.9 GB, no restarts):** the 11
  re-rendered fields, colne_6, and a sample of F26's start-on fields.
  - **All 17 match AI 2D.** Unlike: 0-3% except jtempl 5% (water) and mtcrl_4 29% (F13's parallax backdrop:
    the game's scrolled rocks show through the trestle, as in the original).
  - **The re-renders show the game's entry state** (compared with the original shot): blin63_1, blin65_1,
    blin68_1, blin69_1 (no blood), ealin_2, gidun_3 (the face), gldgate, and hideway1/2/3 (the chests
    closed). colne_6 shows the mirror ball.
  - **F26's start-on tiles** in onna_4, onna_52, anfrst_4, mtcrl_4 and onna_5 look as AI 2D; onna_5 has no strip.
  - Sheets: this session's scratchpad `r1.jpg`-`r6.jpg` (3D | AI 2D), `o1.jpg` (original | 3D).
  - **Found, not new:** blin69_1's floor sign reads "39" in the render where the game says "69". The 11-01 render
    already had it; the input says 69. The Studio's text box for it already says "69", so a "signs" job
    (Qwen-Image, game closed) would fix it. Left for the user.
- **F25 (psdun_4's far-push haze), merged 21:55** (backups in this session's scratchpad `f25m/before/`; agent
  folder `ff7-3d\dev\f25`). psdun_4 had three separate problems:
  - **Pale cut-off patches on the rocks (F25_1, build.py + surface.py):** the look step's effects read the
    pushed depth (`simplify_in.npz` Z, `ao.npy`). Mist puffs are sized for their median depth, which was in the
    pushed sky, so the puffs over the rocks came out 7 times as big. Motes, birds, the sky mask, point lights,
    shadow drops and AO were also computed on the pushed depth.
    - Fix: `Zs` (the scene where it stands) feeds AO, simplify_in's Z and the extent; `Z_drawn` keeps the pushed
      depth for `surface.steady`. In game, rock luma 7.1/10.8 -> 4.0/4.1. Controls zz4, blin3_1 and eals_1 show
      animation-level noise only.
    - It affects all 78 fields with glow pushes. The visible ones: nivl_b1 (motes 7x), zz7 (mist 4x),
      mtcrl_2, sininb36, snow, eals_1, cosmo2, junonr1, convil_4.
  - **Pale steps in the sky (two causes):**
    - Stale Studio palette caches from before F23's fix: page 15's frames never load. Same in psdun_4, semkin_8
      and rcktbas2 (`dev\f25\scratch\hashcheck.py`; crcin_1, crcin_2 and nivl_b12 not checked: 10-36 min
      replays). semkin_8's and rcktbas2's caches moved to scratchpad `f25m/palette_states_before/` and their AI 2D
      re-assembled 21:57.
    - Tiles past the 8.5 push cap stay hidden. **F25_2 (glow.py):** `OVER_SCENE_CAPPED = 0.005` for switched glow
      layers, so they're drawn over the scene. Also lists layers in ghotin_2, icedun_2, kuro_3 and mtcrl_2.
  - **White sky (the user's call, F25_3, not merged):** doubled light. The render holds 87-91% of the light
    and the frames add all of it again; AI 2D does the same (240-246 against the original's 187-197). Cap 6
    only looked right because the layer stayed hidden. Option: `PICTURE_SHARE` 0.5 -> 0.15 (sky 217, 8% at 250+).
    It also changes blue_2, nvdun31, lastmap, nivl_b1, sninn_2 and sininb31 (not checked in game).
  - **psdun_4 is held at its cap-6 build** (looks right today; agent restored it) until that call: a rebuild
    without F25_3 gives AI 2D's white sky. Its palette cache is still the stale one too.
  - **User 2026-11-06 ~23:5x: "fix psdun_4".** Not F25_3 as written: PICTURE_SHARE 0.15 would also have made
    nivl_b1's flames (27%, a cycling param like psdun_4's, so no rule tells them apart) "the picture", and they'd stand
    still again (F28). Instead `glow.PICTURE_FIELDS = {"psdun_4"}`: its switched glow layers the render holds count as
    the picture whatever they cover. nivl_b1 checked unchanged (the_picture False). Backup scratchpad
    `f25m/before_psdun4/`. Its palette cache was moved aside (`f25m/palette_states_before/`), AI 2D re-assembled
    and 3D rebuilt; capture in game follows. blue_2, nvdun31, lastmap, sninn_2 and sininb31 stay as they are.
  - **Checked in game 23:57:** unlike AI 2D 1%. The light at the top left is a soft teal, not white (AI 2D is a little
    paler there), and the rocks have no pale patches. Sheet `report/review/psdun_4.jpg`.
  - **Queued 21:57:** 78 3D rebuilds (the F25_1 list, F25_2's four, semkin_8 and rcktbas2; not psdun_4 or md_e1,
    whose build fails). List in scratchpad `f25m/rebuild.json`.
  - Not fixed (engine): relit lamps light pushed walls from their pushed position, so a little less lamp light.
- **F25's first 14 rebuilds, checked in game 22:02-22:09:** 12 captured; sininb36 and cosmo2 send you straight on
  to another field (can't be looked at). convil_4's 15% is the drifting clouds and the bird.
  - **icedun_2: a black wedge on the right in 3D** (14.5% black). Not new: F20's 11-03 shot has it
    (`dev\f20\evidence\icedun_2_full.jpg`). The depth fit is poor (fit 26.5%), and 7% of the grid is still nearer
    than the near plane after `clear_near_plane` (`near_plane.left` 0.07), so it's clipped. **Open: needs a depth
    fix for this field.**
- **F28. FIXED 22:12: switched glow layers stood still in 3D (user: "the fire wasn't animated there", nivl_b1).**
  - **Cause:** `glow.write_pages` made each glow layer's frames from one canvas holding every state of the layer
    (`layer()` draws all of a palette's tiles), then cut every tile from it at its screen place. States sharing a
    place (nivl_b1's flames: param 1, 4 states at the same spots, each its own art in the original) all got the same
    art, so the game's switching changed nothing; only the palette flicker was left.
  - **Fix (glow.py):** a layer mixing states gets one canvas per state (`glow_frames(keep=...)`), and each tile is
    cut from its own state's. Layers of one state are byte-identical (zz4, eals_1 checked).
  - **In game (nivl_b1, 16 shots per view):** fire spread over time 0.6 -> 7.6 (moving share 0% -> 25%); AI 2D 7.1
    (22%), the original 16.3 (53%). The original moves more because the render's own fire stays under the flames.
    Sheets in this session's scratchpad `fire\nivl_b1_before\sheet.jpg` and `fire\nivl_b1_devpages\sheet.jpg`.
  - **Affects 32 fields** (dev\f28\scratch\scan.json): ancnt4, blin2_i, blin59, bugin3, cosmo2, crater_2, ghotin_1,
    gninn, gon_i, gon_wa1, goson, hyou5_2, icedun_2, kuro_1, kuro_3, las2_1, md1_2, md8_3, mrkt2, niv_w, nivl_b1,
    nivl_e1, psdun_4, rcktbas2, rcktin5, sbwy4_4, semkin_3, semkin_8, snmayor, spgate, whitein, woa_2. 31 queued
    for a rebuild 22:13 (not psdun_4: held for the sky call). Backup `f28m/before/`; nivl_b1's old pages in
    `dev\f28\backup\pages\`.
  - **Checked in game after the rebuilds (22:52-23:05, scratchpad `firetest.py`, 16 shots per view, where the
    original moves):**

    | Field | 3D (spread, share moving) | AI 2D | Note |
    |---|---|---|---|
    | nivl_b1 | 12.3, 52% | 13.9, 57% | |
    | woa_2 | 15.5, 72% | 17.6, 77% | |
    | snmayor | 8.2, 47% | 9.2, 46% | |
    | ghotin_1 | 9.0, 41% | 11.7, 49% | |
    | hyou5_2 | 11.2, 56% | 20.4, 100% | the original is 12.2 |
    | crater_2 | 4.4, 3% | 2.7, 7% | both weak: the_picture keeps only what each state adds |

    - **Inconclusive:** kuro_3, rcktbas2 and bugin3. From their spots almost nothing moves on screen: kuro_3's glow is
      at the top edge, and bugin3 is the white-out.
    - **Retried 23:10** (the game had refused the jump right after a view switch; firetest now waits and retries):
      gninn 3D 8.7, 42% (AI 2D 11.5, 52%); las2_1 55.0, 81% (73.7, 92%); md8_3 15.4, 63% (17.1, 68%).
    - **F28 verified:** wherever the animation is visible, 3D now moves about as much as AI 2D.
- **Spot check 19:10-19:16:** all match AI 2D. subin_1a's console lights, goson, rkt_w's fireplace, las0_1's rope
  ladder and exhaust, gninn's orange flame, ujunon4, onna_5 (no strip), snmin2 (snow only outside), sininb41 (no
  blobs), tin_2's tint, tunnel_6's lit tunnel, gon_i. Memory flat at 1.6-1.8 GB.

## Paused 2026-11-03 17:31 (user: "wrap up and we'll continue again later")

- **Merged today after the user's go-ahead:** F15 (character lamp cap), F20 (glow over the scene, plus the extent
  fix), F21 (switched states), F22 (hiding), F23 (particles, onna_5, screens; Studio palette fix). F19's AI 2D
  glow fix is in the Studio. The FFNx-3d DLL is from 15:19 (F13/14/16/20/23/15 engine changes plus the shadow-map
  fix).
- **The Studio queue keeps running** (it doesn't need me). About 415 3D rebuilds were left at 17:31: F22's mesh
  change, F20's 108 fields, and F23's code for builds after 16:58. All builds now use every merged fix.
- **To do when resuming, in this order:**
  1. Check the queue finished; re-queue failures (GET /api/queue `recent`, ok false).
  2. **Look re-apply** (`run.py look <look> <field>`) for every built field whose `work/<f>/report.json` is
     older than 16:58 (F23's merge, in scratchpad `f23m/merged_at.txt`). F23's particle masks, near dust and
     screens are look-step changes. (`ff7-3d\dev\f23\scratch2\reapply_fields.txt` lists the ones it changes.)
  3. **Studio palette fix (F23):** with the queue empty, restart the Studio, re-assemble AI 2D for mtnvl4, mtnvl2,
     mtnvl5, mtnvl6, mtnvl6b, cosmo2, md_e1, rootmap, sninn_2 and bugin3 (POST /api/generate mode build), then
     rebuild their 3D. Their old palette caches are in scratchpad `f23m/palette_states_before/`.
  4. **F24 (game memory) wasn't finished; the agent was stopped mid-work.** Two in-game tests (VRAM held, GPU
     busy) did NOT reproduce the climb: frames sit in VRAM (1 MB per 3D frame, 4 MB per AI 2D frame) and are
     freed on leaving or re-entry. Its planned fixes: an engine texture budget (LRU), shared identical frames, an
     allocation guard so a failed load doesn't crash; a pipeline per-field glow-frame budget; per-second memory
     logging in the watcher. Its dev folder: `ff7-3d\dev\f24`. (Fixed 2026-11-06, see above.)
  5. Review a sample of the rebuilt fields with the watcher (`tools/watch3d.py <fields>`): F20's 30 checked glow
     fields, onna_5, and some F22 mesh changes.
- **Open the user's calls:** F21's `FF3D_EVENT_STATES` (full latent fix, 179 fields, off); F21's Studio
  `scripts.analyze` diff (28 fields' animations); re-rendering 16 conditional-state fields and colne_6's states;
  F22's fringe (now off round always-in-front tiles).
- **Open fixes:** psdun_4 (cap 8.5's far push haze; held at a cap-6 build, keep it out of rebuilds); blin65_2's
  event lights; faint tile-edge cuts (lastmap); nivl_b1's muted fire on characters (F15 side effect).

## Resumed 2026-11-03 15:10 (user: "you may continue")

- The game, Studio and ComfyUI are up (game 1.77 GB). Second-run fix agents were started 15:1x on F15, F20, F21,
  F22 and F23, each continuing in its predecessor's dev folder. F20 and F21 check and restore the fields their
  predecessors left in dev states first.
- **F23 particle engine installed 15:18** (fork `ffnx3d_particles.cpp`: optional `mask_project`, `mask_size`, `mask`, `mask_main` and `near` keys in `<field>_particles.toml`; files without them behave as before; backup scratchpad `f23/`). hyou8_1, mds7pb_1, nvmin1_1 and zz4 draw fine; the agent is testing.
- **F15 test engine installed 15:22** (fork: lighting.frag, lighting.cpp/.h, renderer.cpp, defs.h, graphics.cpp, common.cpp; new keys `game_light_saturation` (default 1 = as now) and `game_light_cap_parts`; the original view is untouched; backup scratchpad `f15eng/`). The agent is A/B testing through devtools.

## Paused 2026-11-03 ~14:20 (user: "pause for now")

- **All five fix agents (F15, F20-F23) were stopped mid-work, with no reports.** Their dev folders hold their
  work so far: `ff7-3d\dev\f15`, `f20`, `f21`, `f22`, `f23`.
  - To resume, start fresh agents on the same items and point them at their predecessor's dev folder (and its
    `backup\`); patches there are unfinished.
  - F15 was adding a test switch (`game_light_cap_parts`) in its dev copy of the fork.
  - F21 was writing a Studio diff for `scripts.analyze`.
  - F22 and F23 were mid-edit.
- **Game files left in agents' dev states (not verified):**
  - F20 rebuilt blin60_1, blin65_2, gninn, losin2, psdun_4, subin_1a and ujunon4 with its dev code; six of them
    have a `glow_over_scene` key in config.toml, and the installed engine draws them.
  - F21 changed blin69_1, ghotel, gidun_3, hideway2, junonl2, junonr2 and mrkt3 (pages; colne_6 and rkt_w backed
    up too).
  - Backups: `ff7-3d\dev\f20\backup\`, `ff7-3d\dev\f21\backup\`. Check them in game, or restore them, before
    trusting them.
- **The live pipeline was not changed by agents.** Merged today: the Kalm exception, the character light cap, the
  floor tint, and every earlier fix.
- **The fork is unchanged since its 14:06 build (F20 engine installed 14:10).**
- **The Studio:** F19's AI 2D fix is applied and 111 fields re-assembled; its queue is empty.
- **Cleaned up:** F15's leftover shot script was stopped and its game.lock removed. No devtools override is left,
  no watcher is running, and the game is on view 3d (hill2).

## Where we are (2026-11-03 13:15): second run finished and reviewed

- **The second whole-game run is complete.** All 670 fields were built, and the last build finished 12:23; no
  failures since the 10:31 resume. Every built field has been captured and looked at, apart from the "Can't be
  looked at" list. Fields changed by fixes since have been retaken.
- **Merged today (2026-11-03):**
  - F16, the tint fade: engine.
  - F13 follow-ups: frames, cover, layer-3 depth.
  - F17: switched states shown whole.
  - F18: lit-window panes.
  - F19: parallax shafts, glow cap 8.5, painted-out glows.
  - The shadow-map crash fix: engine.
  - The watcher: crash detection, a memory guard and the reshoot after switches.
  - The installed FFNx-3d DLL is from 12:33.
- **No agents running.** F19's crater_2 regression is fixed (13:3x).
- **User's calls (2026-11-03 13:4x):** "1. we can keep it 2. Yes cap it 3. Yes. Then start working through fixing
  your findings".
  1. Kalm keeps its candlelit dark glass: `life.CANDLELIT_GLASS = {"elm"}` (the old per-pane light); elm's look
     re-applied.
  2. F15 cap: the characters' own IBL is capped at saturation 0.25 (`ibl.CHAR_SAT_MAX`, `cap_saturation`) and
     the floor tint narrowed to 0.88-1.12 (`atmos.FLOOR_TINT`). In game this did NOT remove hill's green
     (`scratchpad f15/ab.jpg`). One-at-a-time overrides (no grade, no floor light, ambient, exposure, sun, IBL
     ambient) didn't change the hue either. Suspect: the game's per-field model lights in the shader
     (`toLinear` on gameLightColor, GAME_LIGHTING_PER_PIXEL). Agent F15 (13:5x).
  3. F19's Studio fix applied to `ff7bg/ai.py` (Studio repo, uncommitted; backup scratchpad `studio_f19/`); the
     Studio was restarted and 107 fields queued for re-assembly 13:4x. All 111 fields' game pages were rewritten by 14:1x; no failures,
     and every 3D scene is still up to date.
- **F20 engine (glow_over_scene) installed 14:10** (fork deferred.cpp, lighting.cpp/.h, ffnx3d_live.cpp; status `glow_over_draws`; backup scratchpad `f20/`). It does nothing until a field's section lists pages; the agent is testing.
- **Fix agents started 13:5x:** F15 (green characters), F20 (glow buried behind the scene: `glow_over_scene`),
  F21 (states switched after entry: colne_6, rkt_w, blin69_1, las0_1, mrkt3, frmin, hideway2, junonr2/l2,
  whitein), F22 (characters wrongly in front: onna_52, sango1, sbwy4_3/4, kuro_4, elm, bigwheel), F23 (particles
  in the wrong places, cargoin's fade, onna_5's strip, glow tile edges).
- **Biggest open items:**
  - F19's ~22 fields whose glow is buried too deep for the cap (needs the designed `glow_over_scene` engine change).
  - F1's latent switched states (colne_6, ghotel, anfrst_4).
  - F2's hiding misses (onna_52, sango1, sbwy4_3/4, kuro_4, elm, bigwheel).
  - F14's cargoin, which isn't fully black.
  - F5's minors.

## Resumed 2026-11-03 10:3x (user: "you may continue")

- ComfyUI was down overnight. Its launcher opened on the instance screen and needed one click from the user. 7 builds
  had failed when it stopped at 19:59. The Studio was restarted 10:10.
- The run's remaining 179 fields were re-queued 10:31 (POST /api/3d/build). The order was the 74 the first run
  didn't build (crater_2, datiao_3, ... zz8), then the 105 first-run fields. The watcher is following.

## Paused 2026-11-02 ~19:58 (user: "lets pause for now")

- **The review is paused.**
  - The watcher is stopped and no agents are running. The game is left on view 3d, in subin_1a.
  - The Studio's rebuild run was NOT touched and keeps building (current: subin_1b, job ~1188).
  - Reviewed so far: everything up to snw_w (alphabetical within the run).
- **Captured, not yet looked at:** sky and gldst (retaken after the 16-bit page fix: check the sky behind the plane
  and gldst's 2 switched tiles), southmk1, southmk2, spgate, spipe_1, spipe_2, startmap. Build their pairs with
  scratchpad `review/pairs.py`, or open the sheets `report/review/<field>.jpg`. subin_1a was mid-capture; the
  watcher retakes it.
- **To resume:** `cd ff7-3d; python tools/watch3d.py --batch 8`. It carries on from `seen.json`, so fields built
  meanwhile queue up.
- **Waiting on the user:** F15, the characters tinted by strongly coloured scenes (hill/hill2 green, onna_4
  magenta). Should their light's colour be capped? That changes the character look, so it's the user's call.
- **Since 16:00, merged and live:**
  - F11 colour pass: 154 rebuilds and 132 look re-applies.
  - F14 scripted black: engine.
  - F13 parallax backdrops: engine and pipeline, layers 2 and 3, the 16-bit page fix; 24 fields rebuilt.
  - The watcher's camera fix: it reshoots 3D after a switch.
  - rootmap's particles crash.
  - The fork's installed DLL has F14 + F13 + layer 3 (18:42). Backups in scratchpad `f14/`, `f13/`, `f13l3/`.

## Before the pause (2026-11-02 10:40)

- **Second whole-game run, with every fix merged**, queued 10:39 (`run.json`: jobs 36-705): the 553 fields not
  rebuilt since the first run first, then the 117 the first run did (they lack today's fixes: F2's deeper floor /
  props / reach changes the mesh). ~17 h. The watcher reviews it (`seen.json` reset; the first run's log and seen
  list are in `run1/`).
- **Merged today**: F3 agent (glass backdrop, carpet/floor-mirror reflections, light_added, spill), F1 agent
  (repaint_seams, page_colours), F2 agent (sink_deep/sink_slope, placed props, reach rule), main (F7, F10, the
  config lock), the Studio's parallax-tile fix (32 fields re-assembled) and blin3_1's re-render. FFNx-3d installed
  ~10:35 with the fades, [fields], floor-mirror shader and overlay brightness match. The Studio was restarted
  10:20 (the 3D tab's game-wide group and the preview camera fix are live).
- **Still open with an agent**: blin3_1's messages in 3D (F1 agent).
- **crcin_1** (user: "check why crcin_1 takes 6+ minutes then fails"): the 09:37 build failed on a half-made edit
  of mine to the live pipeline (write_fields_section). The slowness: profiled, 36 of its 37 minutes were the
  palette-animation replay (`ff7bg.palettes.animated_textures`: 32 million script steps), which the Studio caches
  (ai/palette_states/) but the 3D build redid every time. `glow.animated` now uses the Studio's cache (identical
  result): crcin_1 rebuilt in 18 s. Cancelling the running crcin_1 build also cleared the run's whole queue (the
  Studio's cancel empties the queue); re-queued at once, same order.

## To fix

**F1. Script-switched tiles** (F1 agent, second round 2026-11-02; see Done for what's fixed):
- **Switched light layers whose tiles sit far behind the scene** stay hidden: blin65_2's room lights (z 4087, x11-13
  over most of the room, past LIT_PUSH_CAP 6) and blin60_1's moving light. Proper fix (engine): blended field
  tiles the original draws over the scene tested against characters only, not the scene (e.g. a stencil mark on
  the scene's pixels), with each tile's 2D visibility in the glow frames' alpha.
- **blin3_1**: the 3D messages are softer than AI 2D's (`glow_frames(painted=False)` blurs a toggled layer's light
  0.8 x scale); a smaller blur would sharpen them.
- **corel3** (art): the original's fire layer (param 1, additive, animated) was drawn into the AI input, the AI
  painted it in, and both 3D and AI 2D add it again: doubled light ending at its 320-px tile edge. (nvdun31's
  materia fountain is white blown out in AI 2D only; 3D matches the original there.) Same contrast in
  both views when shot at the same moment. Fix belongs in the Studio (don't draw toggled additive layers into the
  AI input; blin3_1's fix by hand was one case of it). Taking the layer out of the mesh left purple-blue ghosts.
- **Latent (found by the F1 agent, not fixed)**: a param state on in the render is baked into the mesh; when a
  script switches it to another state, the baked tiles stay and hide the new state's overlay (anfrst_4's crystals,
  colne_6, ghotel's "Wonder" stone). Invisible at entry. (Seen 2026-11-03: colne_6's globe shows as the orange lantern state in 3D, blue Earth in AI 2D.) Fixing it means treating them like animations that start
  on (overlays, with the off art in the mesh), which would turn many closed doors into 2D overlays.
- **frmin**: AI 2D shows a pale chair right of the table's top that 3D doesn't (a switched tile, presumably).
- **hideway2**: the chest at the far end shows open and lit (orange) in 3D, closed and dark green in AI 2D (a switched
  tile's state, or the AI art; check).
- **hyou5_2** (the ice floes): AI 2D shows rings/holes on many floes (thin ice, switched or animated tiles?); in 3D
  every floe is plain white. Check whether the floes' states are overlays the 3D drops.
- **hyou8_1**: a rectangular paler band down the left edge (x < ~140) and along the bottom in 3D, a scrolled
  blizzard tile edge over the scene (AI 2D is whited out there).
- **las0_1** (the Highwind over the crater): AI 2D shows the rope ladder hanging under the ship; 3D doesn't
  (a switched tile). las0_2: the white mist/light at the top right and the glow on the left are much weaker in 3D.
- **mds5_4** (FIXED by F13, 18:32) (Cloud falls into Aerith's church roof area): a large black rectangle behind Cloud in 3D (the
  ceiling/girders AI 2D shows); depth is in range (1035-2435, not clipping): likely a switched tile drawn black or
  an entry_off hole. **mds6_1**: a tall pole (top middle) in AI 2D is a short stub in 3D.
- **rkt_w** (Cid's house, Shera's room): AI 2D shows the fireplace at the top left lit (orange fire), 3D shows it
  dark. The field has switched solid params 1-4 and a blended param 5 (49 tiles, 66 overlay tiles in all), none
  detected as animated: the fire's state is likely switched by a script after entry (see the latent baked-state
  issue above), or caught at a different moment.
- **blin69_1** (Shinra 69F after the escape): AI 2D has many blood smears over the floor and walls, but 3D shows
  only a few (a switched state the render lacks, shown in part: an F17-like spot REPAINT_CUT keeps cut, or below
  OVERLAY_SAME). The lift's window is green in 3D and cyan in AI 2D. Check with F17's plan tools.
- **cargoin** (the cargo ship's hold, "It's not ready yet..."): AI 2D is fully black under a fade; 3D shows the hold
  faintly (dim, not black). An F14/F16 fade path not covered (the game's fade quad not fully opaque over the
  scene, or a fade type the fork doesn't tint)? It was F7's first case (glow and particles over the fade, fixed);
  check the `tint` and `field_drawn` status there.
- **cos_top's dish**: not a problem: a rotating model (not in the render); both views show its flat dark back at
  some moments.

**F2 (rest)**
- **kuro_4** (the Time Guardian clock): the clock's hands (models) are cut along diagonal edges in 3D, the upper hand
  mostly hidden; AI 2D shows them whole over the clock face.
- **elm** tri 82: Cloud's head hidden in 3D, shown in AI 2D (before and after the F2 fix).
- **bigwheel** tri 6: Cloud shows in front of the green cabin in 3D; 2D hides him behind a z=2 tile (the AI
  painted floor there, and floor never covers).
- **bwhlin** (FIXED by F13, 18:24): its windows show the painting's own exterior; AI 2D shows switched art there (F1-like).
- Props: `PROP_UNIT` 4 estimated from the gondola's size on screen; prop turn and OFST z sign unknown.
- **sango1** (Mideel area, the hollow log, tri 74): 3D shows Cloud standing on the log; AI 2D and the original
  hide him entirely (he's inside it, walking through the hollow). The log's top is in front of him in 2D, behind
  him in the mesh: the hiding match misses a character inside a hollow prop.
- **sbwy4_3, sbwy4_4** (the sewer shafts): part of a character shows in 3D where AI 2D hides it. In sbwy4_3, a
  purple shape at the bottom left; in sbwy4_4, a purple limb behind the pipes at the middle left. Both are
  hidden by the original's tiles in 2D.
- **onna_52** (Honey Bee Inn room, Cloud behind the round bed): 3D draws his hips and hands in front of the bed,
  with a thin slice of the bed's top edge across them; in AI 2D and the original the bed hides him from the waist
  down. The bed's front is too deep in the mesh (the original's hiding tile isn't matched there).
  `report/review/onna_52/3d.jpg`.

**F4. Art (Studio side; diagnosed by the F3 agent 2026-11-02).** User: "yes apply the studio fix and re-render
blin3_1". Done ~10:30: `ff7bg/ai.py` patched (Studio repo, uncommitted; backup scratchpad `studiofix/ai.py.before`),
Studio restarted, the 32 fields queued for re-assembly (POST /api/generate mode build, current). blin3_1: its
`ai/jobs.json` defaults for the blended params 2, 3, 4 set to 0 (backup `studiofix/jobs.json.before-blin3_1`) and a
new final render made (12 s, game closed meanwhile); its 3D build followed automatically. To check in game: anfrst_1
(beam back), anfrst_3 (no rectangle), the double-shaft spots, blin3_1's board.
- **anfrst_1, anfrst_3** (Studio bug, `ff7bg/ai.py build_pages`): the light shafts are layer-3 additive tiles the
  game draws with parallax (canvas x 146 vs the scene's 520 in anfrst_1). `glow_tile` blanked the ones over solid
  tiles (anfrst_1's beam gone where the game draws it) and carried the AI picture as light in the rest (anfrst_3's
  rectangle with tile edges). Diff ready: `ff7-3d/dev/f3/patches/studio_ff7bg_ai.py.diff` (static blended tiles on
  layers 2-3 stay original overlays). Affects 32 fields on re-assemble (anfrst_1-4, the glacier blizzards hyou* /
  move_*, kuro_1, fr_e, onna_5, trnad_3/4, woa_1-3), no re-render; but the renders have the shafts painted at their
  unscrolled place, so some spots show two until those fields are re-rendered. Not applied: the user's call.
- **blin3_1** (Studio bug, input and states pass): the AI input had message state 1 of param 4 on, so it painted
  that message into the board; blended toggles that are on at entry get no "off" art. Fix: re-render blin3_1 with
  toggled blended layers off in the AI input (136 fields have toggled blended tiles on in their input). User's call.
- **las4_3** (AI 2D only): AI 2D is black but for a faint glow, while the original and 3D show the crystal
  cluster (seen twice). The Studio's pages are there with the art (`mods/Textures/field/las4_3/las4_3_00_00.png`,
  1024 px, 46% covered). Its base tiles use page 0 with palettes 1, 4 and 5, and one layer-0 tile has
  type_trans 60. Why the game draws nothing from them in AI 2D is unknown. 3D lacks the original's big white
  radial glow at the bottom left; that's one of its 4 palette-animated additive layers, so possibly timing.
- **crater_2** (AI 2D only): AI 2D is blown out white over the crystal field, with pale cyan rectangles on the
  right (tile blocks). 3D shows the ice's texture and looks better. Probably an additive layer the Studio's pages
  stack over the AI art, as with anfrst_3's rectangle.
- **blin60_2**: not reproducible (no tile missing in the pages; 8 AI 2D shots 0.3-22 s after entry show none): the
  blocks were caught mid-transition (a monitor movie plays on entry).

**F13. Parallax backdrops** (found 2026-11-02 16:5x: mtcrl_4, mtcrl_5, the Mt. Corel coaster trestles). The far
background (solid tiles on layer 2, z 4096; mtcrl_5: 120 tiles, mtcrl_4: 160) is scrolled by the game apart from
the scene: FFNx's `background.cpp` places layers 2-3 at `bg3_pos/16 + bg3_speed x camera delta / 256`, wrapped at the
layer's width/height. The 3D scene bakes the render's copy of it at its canvas place. mtcrl_5's field is 720 tall
and its backdrop only 368, so the camera at the bottom sees black through the trestle in 3D (black 20.7%) where AI 2D
and the original show rocks and clouds. mtcrl_4 shows the backdrop's sky where 2D shows its rocks (unlike 73%), and
the mountain peak at the right is gone. The same cause as the Highwind's backdrop (Done) and anfrst_1's shafts (F4).
Likely fix: the game draws the backdrop as in 2D, with the version's art in the pages. The scene's backdrop and empty
pixels go behind the tile depth (4 x 4096) so the game's tiles show through there. Agent F13.
- **F13 agent's result (18:00):**
  - The suggested fix doesn't work with today's engine: `deferred.cpp draw_scene_overlays` draws solid field
    tiles over the scene with depth ALWAYS, so backdrop pages covered everything.
  - Engine change: the backdrop's triangles (at layer 2's z, `backdropTileZ`) are drawn once behind the scene,
    depth-tested. The scene shader discards texels with alpha under 0.5, and the scene is drawn unblended.
    Fields with opaque scenes, every field today, are unchanged.
  - Pipeline change, behind `glow.GAME_BACKDROP` (`FF3D_GAME_BACKDROP`, default off): for fields whose backdrop
    moves, the scene's alpha is 0 where the backdrop shows. Those backdrop tiles are written into the pages from
    the version's art. `through_glass` and the life finds step aside there.
  - 21 fields: blin66_2/6, bwhlin, bwhlin2, crater_1, eleout, fship_1/2/22/24, ghotel, hill, hill2, junbin21,
    las1_1, md8_b2, mds5_4 (F1's black rectangle was this), midgal, mrkt4, mtcrl_4, mtcrl_5.
  - Evidence: `ff7-3d\dev\f13\evidence\cmp_<field>_t<tri>.jpg` (3D now | with the fix, composited from real
    shots | AI 2D).
  - Pipeline merged 18:01 with the flag off (backups in scratchpad `merge_f13/before/`). The engine change was
    applied to the fork 18:00 and is building (backups in scratchpad `f13/`). Then: flip the flag, rebuild the
    21, check in game. hill/hill2 will look like AI 2D's Highwind; the review had liked 3D's.
- **Checked in game after the rebuild (18:05-18:34):** mtcrl_4 and mtcrl_5 (the rocks, clouds and mountain behind
  the trestles, no black), blin66_2 and blin66_6 (window and hologram right), bwhlin (the windows now show the
  Gold Saucer as AI 2D: F2's bwhlin item), bwhlin2, fship_1 (the sea scrolls), fship_22 and fship_24 (the sky in
  the dome), ghotel, junbin21 (window sky as AI 2D: F5's item), las1_1, md8_b2 (no bright band: F5's item),
  mds5_4 (no black rectangle: F1's item), midgal, mrkt4 and eleout (the shaft's lit column over the platform, as
  in the original) all look right. fship_2 was caught in its white fade again.
- **crater_1 after F13 (18:11):**
  - 3D shows the game's starry sky (layer 2, 320x160) where AI 2D shows the crater's pale wall. There's also a
    black block at the bottom left, where the scene is cut out and layer 2 doesn't reach.
  - The wall is layer 3: 93 solid tiles at z 0, 320x320 on an 1104x720 canvas. F13 left layer 3 baked at its
    canvas place, which is off-screen from this spot.
  - Layer 3 needs the same treatment (game-drawn, behind the scene; the engine's `backdropTileZ` for layer 4 too).
  - hill and hill2 have solid layer 3 as well; check them.
- **Layer 3 too (F13 agent's follow-up, rolled out 18:42-18:46):**
  - crater_1's wall and hill/hill2's Highwind are solid layer 3, which a script places with BGPDH depths
    (3840, 512) and the camera moves. The engine now draws layer-3 triangles too, depth-tested, after layer 2
    (`backdropTileZ[2]`, 3 passes).
  - The pipeline's `glow.drawn_layers` picks the layers. `GAME_LAYER3` is on. Layer-3 art comes from the render
    in scene colours: crisper than AI 2D's upscaled original.
  - `build.parallax_order` orders the mesh around layer 3 as in 2D.
  - Engine installed 18:42 (backups in scratchpad `f13l3/`); pipeline merged 18:43; crater_1, hill and hill2
    rebuilt.
  - In game: crater_1's wall is where AI 2D has it, with no black block. hill and hill2's Highwind is at its 2D
    place with the AI's art, crisper than AI 2D.
  - Still open (Studio): the Studio composites layer 3 in front of everything, whatever its BGPDH depth, so
    crater_1's cliff tiles under the wall's square carry AI-painted wall, in AI 2D and 3D alike. A fix needs a
    re-render.
- **sky after F13 (19:33):** the Tiny Bronco scene is black behind the plane in 3D (38.9%); AI 2D shows the sky and
  mountains. Layer 2 is 88 solid tiles a BGSCR script scrolls (-320, 0); F13 planned it as game-drawn (backdrop
  88), but nothing shows. FIXED (confirmed in the watcher's retake 2026-11-03). Sent back to the F13 agent (19:4x), also to find other built fields with the same cause.
  - Cause (F13 agent): sky's backdrop is on 16-bit direct-colour pages (27, 28), which FFNx looks up as `_00`.
    `write_overlays` named them by the tile's palette (`sky_27_07.png`), which the game never reads, so the blank
    `_00` showed.
  - Fix: `glow.page_palette` (0 on 16-bit pages), used by `write_overlays`, `write_scrolling` and `scene_alpha`.
    `write_scrolling` now rewrites only its own tiles in a `_00` page. Paletted fields' plans are unchanged.
  - Merged 19:50 (backup in scratchpad `pal16/`). sky was rebuilt by the agent and fixed in game
    (`ff7-3d\dev\f13\evidence\cmp3_sky_t21.jpg`); a faint wrap line remains, which AI 2D has too.
  - Only other field with the cause: gldst's 2 switched overlay tiles had never shown in 3D. Rebuilt 19:50.
- **trnad_2 (2026-11-03):** the game-drawn sky (layer 2, BGSCR) is a deep saturated blue in 3D where AI 2D and the
  original show pale white-blue. Possibly `page_colours` M extrapolating on the backdrop's colours. **trnad_3:**
  the scrolling mist (layer 3, blended) shows as a rectangle with a hard straight top edge across the screen; in
  AI 2D it follows the cliff outline. Both sent to the F13 agent (11:0x).
  - **trnad_2, cause (F13 agent):** not M (None for all 29 game-drawn backdrop fields).
    - Its layer 2 is on palettes 6 and 7, which a script animates. AI 2D draws the Studio's per-palette-state
      frames (`<f>_<page>_<pal>_<hash>.png`); the 3D pages had only the still page, in the file palette's
      deep-blue state.
    - Fix: `write_overlays` also writes the version's frames for game-drawn tiles on palette-animated pages.
    - Merged 11:12 (backup in scratchpad `frames/before/`). trnad_2 and mogu_1 were rebuilt by the agent; elmpb,
      mds7st32 and rcktin5 rebuilt 11:12-11:13. trnad_2's sky can't be seen from the entry spot. **Not fixed yet:** the watcher's 11:18 retake still shows trnad_2's sky deep blue (the camera after a view switch shows it). The hypothesis that the still `_03_06.png` shadows the frames is with the F13 agent (11:2x).
  - **trnad_2, real cause (F13 agent, follow-up 4):** not the frames. Both views load only the still pages; the
    hashed frames come into use only after a palette write. The pale top-left is layer 1's static additive haze
    over empty canvas. The Studio has those blended tiles carry the AI painting itself (`glow_tile`), but
    `scene_cover` counted only solid tiles, so 3D cut the area out and left the game's deep-blue backdrop alone.
    Fix: `scene_cover` also counts static blended tiles of the scene's layers where nothing solid lies beneath
    (glass over a backdrop, blin66_2, stays cut out). Merged 11:36 (backup scratchpad `cover/`). trnad_2 and
    white1 were rebuilt by the agent and fixed in game (`ff7-3d\dev\f13\evidence\cover\cmp5_trnad_2.jpg`);
    wcrimb_2, sbwy4_5 and wcrimb_1 rebuilt 11:37.
  - **trnad_3:** the mist band's hard top is the mist layer's own tile edge, which AI 2D has too. In 3D it shows
    more over the darker sky at the left (`write_scrolling` art and sky brightness, not F13). Left as is.
  - **del3 improved** (F13 agent): `glow.layer3_depth` trusts only a depth set by an entity's init, and the
    layer-3 ordering now applies to del3's surf (depth 256). The foam is wide and soft as in AI 2D
    (`ff7-3d\dev\f13\evidence\frames\cmp4_del3_t126.jpg`); closes F5's del3 note.
- The watcher shot blin66_2 at 17:33 while F13 was testing it, rebuilt with F13's dev code. The nebula window ran
  down over the table as a tall panel with a green bar at its foot, and the table's green hologram was gone. Check
  blin66_2 again once F13 is merged or rolled back.

**F14. 3D shows the scene where the game shows black** (found 2026-11-02 17:2x).
- **nivl** (the Nibelheim flashback's start, "...The town was quiet."): AI 2D and the original stay black for over
  10 s with the narration up, while 3D shows the whole town. nivl's tiles are almost all static (1199 of 1311), so
  no switched tiles hide it; it's a fade or the game not drawing its background then.
- **nivl_e2** (the starry sky, "From the beginning...OK?"): AI 2D black, 3D shows the stars.
- Probably the same: **las4_4** (F5: AI 2D black around the party, 3D shows the crystals) and **lastmap**.
- **nivl_b2** (the burning-town white-out): all three views are near-white with faint traces; 3D's are a little
  stronger.
- **Cause (F14 agent, 17:5x):** the game deliberately skips drawing the field: `field_draw_everything` (1.02 US
  0x63A60B) draws nothing (background, models, fade quad) while `MPDSP_flag` (0xCC0DC0) is set. FADE type 4
  ("instant black") sets it, and so does MPDSP 1. It also skips picking background tiles while a field movie is on.
  FFNx-3d drew its scene every field frame anyway. Read in game: nivl and nivl_e2 have flag 1 and fade type 4
  for the whole narration.
- **Fix (engine):** the scene, its glow and particles and the floor reflections draw only on frames where the game
  picked its background tiles (`markBackgroundPicked` in `field_layer1..4_pick_tiles`). The live status has a
  new line, `field_drawn <0|1> hidden <MPDSP>`. Fades of other types keep drawing tiles, so F7's cargoin/chrin_1b
  don't change. Diff: `ff7-3d\dev\f14\patches\engine_F14.diff`. **Built and installed 17:58** (the previous DLL
  and source files backed up in scratchpad `f14/`). Verified in game: nivl and nivl_e2 are black under their
  narration in 3D, as in 2D (`field_drawn 0 hidden 1`), and nvmin1_1 draws as before (scratchpad
  `f14/check/grid.jpg`).
- **Affected:** 26 fields with dialog on a scripted black (`ff7-3d\dev\f14\f14\affected_fields.txt`); FADE type 4
  in 157 fields (134 on entry: 3D flashed the scene at short blacks); field movies in 79.
- **Not this cause:** lastmap's grey blocks are in its AI render (the Studio caption says "scattered gray blocks";
  art, F4). nivl_b2 is a white fade (type 6) the game draws over the 3D scene too; its traces are cosmetic.

**F15. Characters tinted by a strongly coloured scene** (found 18:4x): in 3D and AI 2D alike (so the characters'
light, not the scene), while the original shows them dark and neutral.
- **hill, hill2**: Tifa's face and Cloud are strongly green (the grassy hill), in shots before and after F13.
- **onna_4**: magenta and red (the Honey Bee room).
- Likely the characters' own image-based light (`ibl.write(..., suffix="_char")`: sky from the picture's top, ground
  from under the walkmesh, `amount` toward the scene's colours) together with today's fill light (`char_fill`).
  A cap on its colourfulness would keep their skin tones. Characters' light is the user's look: ask before
  changing the default.

**F21. States switched after entry (F21 agent, second run, merged 2026-11-03 15:4x).**
- **Causes found:**
  - **Baked conditional states** (blin69_1, hideway2, gidun_3, ghotel): an init switches the state only inside
    IF/ELSE, but the Studio's analyze takes every init BGON as on, so the render baked it.
  - **Missed animations** with the state from a variable (mrkt3's dolls, colne_6's ball, rkt_w).
  - **junonr2/l2 smoke:** `write_scrolling` drew all 8 stacked smoke frames into one picture.
  - **Hidden by depth** (F20's area): rkt_w's fire and las0_1's ladder.
  - **AI 2D art, not 3D:** colne_6's lantern states (painted by the states pass; needs a states re-render), frmin's
    chair, whitein's wide beam, blin69_1's remaining blood (the original has none) and gidun_3's face.
- **Fix (glow.py):**
  - `script_loops` takes variable-state loops.
  - New `entry_unknown`: conditional init states the render holds become animations that start on, with off art in
    the mesh.
  - New `swaps`: a closed door that a script switches off no longer covers the open state's overlay.
  - Stacked scrolling states each get their own art.
  - `FF3D_EVENT_STATES` (the full latent fix, 179 fields, 6889 tiles to 2D) is left OFF; the user's call.
- **Applied:** 57 fields pages-only and 20 look re-applies (`ff7-3d\dev\f21\patches\apply_f21.py`; backups
  in `ff7-3d\dev\f21\backup\`). Controls blin63_t, del1, corel1, tunnel_6 and fship_22 are unchanged.
- **In game:** hideway2's chest closed, mrkt3's dolls change, junonr2/l2's smoke shows, blin69_1's lift window is
  cyan (`ff7-3d\dev\f21\evidence\`).
- **Retakes 15:45-16:09:** hideway2, mrkt3, junonr2, junonl2, blin69_1, uttmpin1, yufy2, nvmkin1, junonr3,
  southmk2 and move_f all match AI 2D. blin60_2's 3D is whole, where AI 2D is caught mid-transition. The game
  stopped once (15:49, entering junonl2) and the watcher restarted it; it now keeps FFNx.log in
  `report/review/crash_logs/` before each restart.
- **Not done (the user's call):**
  - The Studio `scripts.analyze` diff (`patches\studio_scripts.py.diff`: animations in every script's loops; changes
    28 fields' animations when jobs.json is regenerated).
  - Re-rendering the 16 conditional-state fields, and colne_6's states.

**F15. FIXED 2026-11-03 16:1x (F15 agent, second run): characters dyed by the game's own coloured lamps.**
- **Cause:** hill's model lights are strongly coloured (lamp 2 green 42/180/102, lamp 1 yellow), and they reached
  the characters twice:
  - FFNx's `draw_3d_model` still calls the game's `generic_light_polygon_set` in per-pixel mode, baking the green
    into the vertex colours;
  - `FFNx.lighting.frag` multiplies the same lamps in again.
  The 3D path's brighter characters made it obvious.
- **onna_4 is not this:** its magenta is the field's entry tint fade (FADE 200,0,200, 1.2-5.2 s after entry),
  which the original shows too.
- **Fix:**
  - Engine (in DLL 15:19): `game_light_saturation` caps the field's model light (global + 3 lamps, not the
    scripted/tint colour) both in the shader and in the vertex colours, keeping brightness. The original view is
    untouched.
  - Pipeline: `atmos.game_light` sets it to 0.25 (`ibl.CHAR_SAT_MAX`) only where the party's own model light is
    more colourful than that. Merged 16:1x (backups scratchpad `f15m/`); written into 54 fields' sections (6 more
    get it when built).
- **In game:** hill's green is gone (Tifa's face 44/49/16 -> 57/45/28, same brightness; Cloud blond again).
  hill2's Tifa is fixed; Cloud's hair stays a little cool, from the game's own tint fade. Controls are unchanged.
  Side effect: nivl_b1's fire red is muted on the characters (`ff7-3d\dev\f15\an\f15b\`).

**F22. FIXED 2026-11-03 16:3x (F22 agent, second run): characters in front of or behind the wrong scenery.**
All in `build.py`:
- **sango1, bigwheel, sbwy4_3:** up-facing tops under an always-in-front tile (z 1-2) were snapped to the walkway
  as floor, and floor never covers. New `FRONT_TILE` 0.5 stops that.
- **onna_52:** the bed top was sunk behind Cloud. Where the original's tile covers, the floor now covers bodies
  clearly behind its walkway.
- **elm:** no 3 px fringe round always-in-front tiles; the lintel's fringe cut Cloud's hair.
- **kuro_4:** props no script places stand at the origin. A flat, still prop gets a ray-cast disc footprint
  (`prop_depths`, `FLAT_PROP`). The clock hands are whole.
- **sbwy4_4:** not a pipeline issue; the jump lands off the walkmesh. Jumps to sbwy4_3/4 land at the origin.
- **Controls:** ancnt2 and mds7pb_1 unchanged. mds7st1 and blin3_1 improved (Cloud no longer cut by the fringe).
  hyou2 and elminn_1 now hide the party as 2D does.
- **Roll-out:** merged 16:3x (backup scratchpad `f22m/`). The 7 issue fields were already rebuilt by the agent.
  Every change is a mesh change: 243 fields visibly, 297 slightly. 511 are queued in the Studio, and 29 are held
  back until F20/F23 finish (they're testing those): scratchpad `f22m/held.json`.
- **F20 warning (16:5x):** LIT_PUSH_CAP 8.5 makes some scenes several times too wide on rebuild (the extent sets
  the shadow map, clouds and relight reach). nivl_b1 would go 2925 -> 10054 and psdun_4 2072 -> 15892; psdun_4's
  sky glow came out blown white. nivl_b1 was pulled from the queue (POST /api/queue/remove) and psdun_4 restored by
  F20. F20 is checking the rest of the queue.
- **The user's possible call:** without the fringe, a character just behind an always-in-front tile shows over the
  AI art's 1-3 px of extra outline, as AI 2D does.

**F23. FIXED 2026-11-03 16:58 (F23 agent, second run): particles, onna_5, glow edges; cargoin was timing.**
- **Particles:**
  - New engine keys: `mask` over the black around the scene (gaiin_1, niv_ti1); `mask_main` keeps snmin2's snow
    outdoors; `near` fades near dust (sininb41/42's blobs).
  - In the pipeline: `life.void`, `life.framed` (nivl_e3's photograph gets no life effects or particles), and
    `atmos._outdoors`.
  - Checked in game; controls hyou8_1, kuro_1 and mds7pb_1 look the same.
- **cargoin:** not a bug. FADE type 2 to black over ~8.5 s, starting ~5 s in; 3D and AI 2D darken in step. The
  watcher's shots caught different fade levels.
- **onna_5's left strip:** the keyhole panel is an unscripted layer 3 the game draws fixed to the screen at depth
  1. `glow.drawn_layers` now hands it to the game; the strip is gone and the characters are hidden as in 2D.
- **nvmkin21/22's square lamp:** `life.screens` lit a whole Qwen "screen" box. It's now weighted by pixel
  brightness, fading at the edges (also changes the screens in 275 fields).
- **mtnvl4's square/strip:** a Studio palette-replay bug. The game holds entry 255 as the file's entry 0 from load,
  so page 16's frame hashes were wrong and its frames never loaded.
  - Studio fix: `palettes.loaded_palettes`, applied to `ff7bg/palettes.py` (Studio repo, uncommitted; backup
    scratchpad `f23m/`).
  - It changes the hashes of mtnvl4, mtnvl2/5/6/6b, cosmo2, md_e1, rootmap, sninn_2 and bugin3. Their cached
    palette states were moved to scratchpad `f23m/palette_states_before/`.
  - **Still to do:** re-assemble those 10 in the Studio after a restart (it needs the queue empty), then rebuild
    their 3D (5 are in the current queue anyway). 92 fields weren't replayed by the agent (`ff7-3d\dev\f23\scratch2\h1_not_replayed.txt`).
- **Merged 16:58** (backups scratchpad `f23m/`).
  - **Still to do:** a look re-apply of every built field not rebuilt after 16:58: the particle mask, near dust
    and screens are look-step changes.
  - onna_5 is queued for a rebuild.

**F20. FIXED 2026-11-03 17:11 (F20 agent, second run): glow buried behind the 3D scene.**
- **Cause:** switched, static and overlay light layers with tiles far behind the scene (z ~4000, 9-60x the scene's
  depth) were hidden by the depth test; `push_under` stops at LIT_PUSH_CAP.
- **Fix:**
  - Engine (since DLL 14:06): `glow_over_scene` draws the listed (page, palette) layers over the scene.
  - Pipeline: `glow.over_scene` lists the layers still at least 20% hidden (not ones that move apart from the
    scene, not `the_picture` layers). `over_vis` cuts their frames and overlay art to the 2D visibility, and the
    build doesn't push the scene back under them. The list lives in report.json and is written by the look step.
- **Extent fix:** the extent is measured before the glow push. crater_2 18434 -> 2578, gon_i 8569 -> 433 (its
  characters' shadows are back), hyou5_2, nivl_b1. It also shrinks zz4, blin3_1 and eals_1's extents (not checked
  in game).
- **In game:** 30 fields show their glow as AI 2D, among them subin_1a/1b, goson, rkt_w's fire, las0_1's rope
  ladder, ujunon4, nvmkin1 (pulse back), icedun_2, gninn (orange flame) and lastmap (animated)
  (`ff7-3d\dev\f20\evidence\`). Controls zz4, blin3_1, corel1, eals_1, anfrst_1 and crater_2 have identical plans.
- **Merged 17:11** (backups scratchpad `f20m/`). 108 fields queued or already queued for a rebuild: the checked 30,
  the 66 survey-listed, the 7 extent fixes, and the 29 held earlier.
- **Open:**
  - psdun_4 stays out of rebuilds: at cap 8.5 the far push makes hazy pale tile patches in its sky (an F19
    follow-up). It's left at a cap-6 dev build.
  - blin65_2's room lights are on only during an event.
  - Faint tile-edge rectangles where the cut follows a tile (lastmap's flash).

**F16. The train lockdown's darkness missing in 3D** (found 2026-11-03 10:4x; tin_2, tin_3, tin_4).
- During "Car #N: Locked Down", the original and AI 2D show the whole car very dark: only the red alarm lamps stay
  bright, and the characters are silhouettes. 3D keeps the car fully lit; the characters are dark in all three.
- No animated palettes. Many switched params (1-8, 312 overlay tiles).
- **Cause (F14 agent, 11:1x):** a "tint fade".
  - On PC, FADE type 1/2 with a colour component between 20 and 220 (also type 7/8 and NFADE 12) draws no fade
    quad. It writes a colour to 0x906A70, which the game multiplies into every background tile vertex
    (0x6465FB, after the tile picks) and every model.
  - tin_2's alarm alternates a red flash (type 9, a normal quad) with `FADE type 2 (210,170,170)`. In game the
    tint read FF695555 (0.41, 0.33, 0.33).
  - The 3D scene stands in for the tiles but never got the colour. The 3D shot times that tint matches AI 2D
    (mean RGB 14/7/4 vs 16/7/5).
- **Fix (engine):** `markBackgroundPicked` stores 0x906A70.
  - The scene, its glow and the particles take it: the `sceneTint` uniform, a gamma-space multiply in
    FFNx.lighting.frag / FFNx.frag.
  - With no fade running it's 0x80FFFFFF, so nothing changes. New status line `tint <ARGB>`.
  - Diff: `ff7-3d\dev\f14\patches\engine_F16.diff`. **Built and installed 11:01; verified in game:** tin_2's car darkens in 3D as in AI 2D (tint FF695555, 4:3 mean RGB 12/12/22 vs AI 2D 11/11/21; before 19/25/42), nvmin1_1 unchanged (scratchpad `f16/check/grid.jpg`). Backups in
    scratchpad `f16/`.
- **Affected:** 54 fields use a tint fade somewhere (`ff7-3d\dev\f14\f14\survey_tint.txt`), mostly cutscenes:
  tin_1-4, losin*/lost*, fship_2/22-25, kuro_82, ithos, corel2, itown*, rcktin5/6, snmayor, smkin_5, nvmkin1,
  trnad_4/52, zmind1/3, onna_4/5/52, hill2.

**F17. tunnel_6: the lit tunnel comes out as scattered blocks** (found 2026-11-03 11:0x).
- AI 2D shows the lit train tunnel beyond the frame; 3D shows it mostly black, with blocky 16 px fragments.
- Param 5 (205 solid tiles) and param 4 (21) are switched states the render lacks (Studio defaults `{}`).
- **Cause (F17 agent):** right. Param 5 is the shutter opening, and the lit tunnel is the Studio's repaint (the
  original's open tunnel is dark, 8-16 levels off the closed one, so only 6.6% passes OVERLAY_SAME).
  `repaint_seams` couldn't call it whole because the state's art stops short of its tile edges: 0 edge samples.
- **Fix:** `glow.seamless`. A spot with too few seam samples shows whole when its outer edge is long enough and
  little of it is seam, or when it covers 99% of the canvas (junin7, junele2). Spots with enough samples are
  judged as before. blin63_t, del1 and corel1 plans are identical; F1's cut-block spots are unchanged.
- tunnel_6 rebuilt by the agent and checked in game: black in the opening 66% -> 19% (AI 2D 12%)
  (`ff7-3d\dev\f17\evidence\tunnel_6_before_after.jpg`). Merged 11:38 onto the cover fix (backup scratchpad
  `f17/`). Pages only: the 141 fields' overlay pages were rewritten with `apply_pages.py` 11:4x (about 23
  visibly: colne_1, junin7, junele2, losinn, tunnel_4, sininb41, gldgate, itown1b, rckt2, shpin_2 ...). Left on
  purpose: 19 fields REPAINT_CUT judges as cut blocks (trnad_51, tin_1-3, del1 ...).

**F18. Lit-window glow paints pale rectangles** (found 2026-11-03 11:2x; uttmpin1).
- 3D shows four flat, pale, translucent rectangles where Qwen's "lit window" boxes are (the shoji wall, walkway,
  house wall); AI 2D has none.
- `life.windows()` gives every pixel in a box a weight of at least 0.35 x lit (lit at least 0.35), and its warm
  test takes beige paper for lit glass.
- Of 332 window boxes in 54 built evening fields, 82 have under 3% warm pixels.
- **Fix (F18 agent):** `life.pane_light` scores each pixel as lamp-lit glass (yellow, orange or near white, bright
  enough for its colour; cream paper and plaster fail). `windows()` weights each pixel by that, with a grey
  opening (frame lines drop out) and a brighter-than-surroundings test. Boxes under `WINDOW_LIT` 0.03 are
  dropped, and `point_lights`' window lights come only from the kept panes.
  - Of 332 boxes, 237 are kept. 12 fields lose all window glow (uttmpin1, md8_2, gaia_32 ...), 13 lose some
    (church 1/5, nivl_b1 3/16, mds7 4/8 ...), and 29 keep all with the glow on the panes only.
  - In game (`ff7-3d\dev\f18\shots\`): uttmpin1's rectangles are gone, md8_5's glow is on the panes with
    the mullions showing, mds7st3/junonl1 are unchanged, ujunon1 keeps its lit window, and church/md8_2 lose
    their pale washes.
  - Merged 2026-11-03 ~12:1x (backup scratchpad `f18/life.py.before`). Looks re-applied for the affected fields;
    the 7 agent-tested ones were already done.
  - **elm (Kalm) left as it was, for the user:** 19 of its 20 window boxes are dark blue glass, and their
    candlelight is made up. My notes say the user confirmed Kalm is daytime and candlelit windows are for
    evenings only, so it's a side effect, but it is a visible look change. elm is on the live look until the
    user decides; it changes on its next look re-apply or rebuild.

**F19. Light glows weaker in 3D than AI 2D** (found 2026-11-03 11:5x).
- zz7's big white crystal glow, zz5's yellow light pool and zz4's crystal glow are faint in 3D.
- woa_1-3's pale lifestream light and fog are weaker.
- Possibly also whitein's searchlight beam and las4_3's radial glow.
- **Result (F19 agent):** mostly AI 2D is too bright, not 3D too weak. Glow-region luma, 3D / AI 2D / original:
  zz7 172-220 / 250 constant / 187-219; zz8 135-213 / 254 / 168-232; ancnt1 134-165 / 197 / 158-180.
  - **Studio bug:** `ff7bg/ai.py animated_pages` draws toggled and parallax additive tiles with the original's art
    in the field file's stored palette, which is at or above every state the scripts set. So AI 2D never dims.
    Fields over 300 frames get no frames at all (whitein lights every searchlight beam at once). In zz7 the AI
    also painted the glow in, so AI 2D adds it twice.
    3D follows the game's states, as the original does. Covers zz4/5/7/8, woa_1-3, ancnt1/2, whitein, subin_1a.
    Studio diff ready (`ff7-3d\dev\f19\patches\studio_ff7bg_ai.py.diff`): it changes AI 2D in 106 fields
    plus 5, which needs a re-assemble. **The user's call; not applied.**
  - **Real 3D bugs, fixed:** (1) anfrst_1-4's palette-animated layer-3 shafts were treated as painted, but the game
    draws them up to 560 px away (`glow.moves_apart`). (2) Toggled glow hidden behind the scene by depth:
    `LIT_PUSH_CAP` 6 -> 8.5 (zz4 kept only 58% of its pulse). (3) las4_3's radial glow was painted out by the
    AI (`glow.painted_out`).
  - In game: anfrst_1's green shaft, anfrst_2's white shaft, anfrst_3's right shaft, jail1's green well glow and
    zz4's pulsing crystal are back (`ff7-3d\dev\f19\evidence\`).
  - Merged 12:4x (backups in scratchpad `f19/`). Pages rewritten: anfrst_1-4, las4_3, pillar_2, jail1, crcin_1/2,
    elmin3_1. Rebuilt for the cap: zz4, fship_4, junonr1, kuro_5, tunnel_5, hyou5_2, ghotin_2, gon_i, crater_2,
    wcrimb_1, rcktbas2.
  - **Still open:** about 22 fields need more than 8.5x (subin_1a/1b, gninn, goson, lastmap, psdun_4, blin65_2,
  - **Retakes 12:57-13:08:** zz4's crystal pulses as in AI 2D. anfrst_1-3's shafts are back, and jail1's green
    well glow. chrin_1a, chrin_1b and chrin_2 are captured and fine. **crater_2 regressed:** 3D is now blown
    white with AI 2D's pale-cyan rectangles (before the cap change it showed the ice and looked better); the
    original is bright but has no rectangles. Sent back to the F19 agent 13:1x.
  - **crater_2 fixed (13:3x):** its ice field is 34 switched glow layers covering 70% of the canvas. The render has
    them on and the AI painted them in, so at cap 8.5 their full light (plus `surface_light`) went over a
    painting that already held it. `glow.the_picture`: a switched glow layer on in the render covering at least
    50% of the canvas (`PICTURE_SHARE`) and shown by the render adds only its shimmer. In game, 4:3 luma went
    from 164-167 to 130-140 (original 137-142), with no rectangle beside Cloud. It matches only crater_2 and
    mtcrl_8. Merged; pages rewritten for both (backup scratchpad `crater2/`).
  - Other cap fields against the original: tunnel_5's red lamp halos are a little larger and brighter (65-71 vs
    50-57; not blown out), ghotin_2 is as AI 2D, and fship_4 is negligible.
    losin2/3 ...). The proposed engine change draws listed glow pages over the scene with depth ALWAYS
    (`glow_over_scene` in `[field_<f>]`). Designed, not built.

**F6. Depth fit**: watch `fit.clamped` > 20% in report.json (blue_1 had 37%; harmless since the camera fix).

**F9. Model rounding hid eyes** (user, 23:15: "Tifa has no eyes", "mds7pb_1 has many missing eyes"). FFNx-3d's
PN-triangle rounding (`ffnx3d_shape.cpp`, char_round) bulges a head outward; models that draw eyes as separate small
pieces just in front of the face (the bar's NPCs; Cloud's are in his face texture and were fine) lose them under
it. A/B in the bar (scratchpad `eyes/`): round on = blank faces, round 0 = eyes back, smoothing alone fine.
Mitigated 23:2x: `char_round = 0.0` (now in config.toml's [fields]; smoothing kept). 2026-11-02: left off for good
unless the user wants rounder silhouettes back. The eyes are separate draws from the face, so making rounding safe
means knowing at draw time which small pieces sit on which surface; the smoothing gives most of the gain.

**F10 (rest). More game-wide settings, not done yet**: glow strength (a multiplier on the baked glow), particle
amount, character lamps (`point_light_strength`; the relit look's wall lamps hang off the same setting) and
reflections (F5 work changes how they're chosen) could follow the characters' settings (Done) with a small fork
change each.

**F5. Minor / taste**
- **ancnt3**: the reflection on the altar's front face: fixed by the floor-mirror mask (see Done) once the fork
  build with it is installed; check then.
- **church**: a small pink spot remains at the bottom: the glow adds the original's light colour (`atmos._glow`).
- **del3** (FIXED 2026-11-03 by F13's layer-3 depth rule): the surf: 3D shows a white foam band with a hard, straight top edge; AI 2D's foam is wider and soft
  (the wave layer animates, so partly timing; check the band's edge).
- **delmin12**: the lamp's red light on the left wall is much stronger in 3D than in AI 2D (lamp light / glow).
- **eleout** (the Shinra building's glass lift, moving): in 3D the tube's glass area is tinted strongly green/yellow
  with a hard vertical edge at its left; AI 2D shows it dark blue-green. The lift scrolls, so the views differ in
  timing; to check.
- **gaiin_1**: snow particles float over the black void outside the cave walls (particles not limited to the scene).
- **Gaea's Cliff fields** (gaia_1, gaia_2, gaia_31, gaiin_4): translucent rectangular bands along the top/bottom and
  a darker band down gaiin_4's right edge, in both views (the blizzard's scrolled layer or the art; not 3D).
- **gidun_3**: the far end of the cave tunnel (top middle) is pale and lit in 3D; AI 2D has it dark with small red
  lights (gidun_3 is among the fields repaint_seams changes: check that first).
- **junair** (Junon airport): in 3D the truss under the upper deck's edge is dark and the lamp's light cone on
  the right is missing; AI 2D shows both lit (the original has them dark: the AI lit them). Relit shading or a
  switched light layer (params 1-4, 364 overlay tiles); check.
- **junbin21** (FIXED by F13, 18:31): the window sky is bright orange sunset in 3D, grey/stormy in AI 2D (the window backdrop: see the
  boardroom's through_glass fix; or relight on the sky).
- **jtmpin2**: a black strip with a jagged top edge along the bottom of the 3D view.
- **junonr2, junonl2**: the locomotive's chimney smoke (AI 2D) is missing in 3D (an animated/switched smoke layer).
- **kuro_1** (Temple of the Ancients maze, purple mist): a rectangular mist block with hard edges (left/bottom) in
  both views; it's the mist layer's tile shape (art), slightly harder-edged in 3D.
- **las4_4** (the crater's core, "Where are we?"): AI 2D is black around the party (its pages not shown at that
  moment?) while 3D shows the crystal field; **lastmap** (the ending): 3D shows the scene cut into a jagged grey
  block mask over black, AI 2D is a white-out. Both are scripted ending/cutscene fields; check with the original.
- **loslake1**: the bright white water spray (right of the tower) is much weaker in 3D.
- **md8_b2** (FIXED by F13, 18:32): the background city/train lights (top left) are a bright sharp band in 3D, faint in AI 2D.
- **move_d** (glacier): a translucent horizontal band across the lower third in both views (the blizzard's scrolled
  layer edge; art/2D, as hyou8_1).
- **mrkt3** (Honey Bee Inn front): the two doll figures beside the sign (upper left and right) in AI 2D are
  missing in 3D (switched tiles).
- **cos_btm**: a faint grey halo around the torch at the cliff edge, over the black void (glow on the void).
- **nivl_e3** (the Nibelheim photograph, framed on black): in 3D, chimney smoke and sparks rise from the two
  chimneys painted in the photo, up past its edge into the black. Qwen's finds take the photo as a real town
  (`life.json`: 2 chimneys, a fire, a lamp, light beams). Life effects should be off here; a picture-in-picture
  test or a per-field switch would do it.
- **niv_ti1**: dust particles float over the black outside the house (as gaiin_1).
- **Glow frames with tile edges**: the palette-animated glow layers show hard tile edges in 3D where AI 2D's glow
  is soft. Check `glow_frames` for these pages.
  - **mtnvl4** (Mt. Nibel, the mist below the rock): a pale square one tile big and a pale strip along the bottom
    edge at the bottom right. The mist is 445 static additive tiles on layer 1 (4 glow layers).
  - **nvmkin21, nvmkin22** (the Jenova door): the cyan lamp above the door is a hard-edged square; AI 2D's is a
    round, soft glow.
- **woa_1, woa_2, woa_3** (the Whirlwind Maze's lifestream bridges): 3D's lifestream glow and white mist are
  weaker than AI 2D's. AI 2D bathes the cliffs in pale white-cyan light and fog, while 3D shows the cliffs
  clearer and the glow a stronger saturated green (woa_2/3). Either a toggled light layer drawn softer (glow
  frames `painted=False` blur, as blin3_1) or relit shading; taste, check.
- **whitein** (the Highwind's bubble at the crater): AI 2D's searchlight beam at the top right is missing in 3D
  (a blended beam layer, switched or animated?).
- **ujunon3** (Junon below the crane, the dolphin): AI 2D has a bright white glare on the water at the bottom left,
  with a stair-edged copy of the crane's shadow in it. 3D has a softer sparkle there. The dolphin is a model,
  caught at a different moment. Possibly a blended glare layer, or AI 2D's art; check.
- **snmin2** (Icicle Inn house, cut away with snow outside): 3D's snowfall falls over the whole room inside as well
  as outside; AI 2D has none. A field half indoors and half out needs the snow kept to the outdoor part (the
  sky or snow mask), as with gaiin_1's particles over the void.
- **sininb41, sininb42** (the mansion's library stacks): the dust particles nearest the camera show as large, soft
  white blobs at the top left (out of proportion in a tight space). Size particles by their distance on screen, or
  keep them off the near plane.
- **onna_4** (Honey Bee Inn's special room): the characters are tinted strongly magenta and red by the room's
  coloured light in 3D; AI 2D shows their own colours (taste: characters' light).
- **onna_5** (the keyhole): a red and yellow vertical strip, about 20 px wide, at the left edge in 3D only. 3D was
  bright where the 2D views were mid-fade (timing).

## Done

- **The watcher's 3D and 2D shots framed differently** (nvdun1-4, nvmin1_1, nvmkin1: unlike 64-97%). A view switch
  enters the field again, and that entry can start the camera somewhere other than the jump's entry does. Tested in
  nvmin1_1: 3D right after the jump sits 45 px off AI 2D, while 3D after switching back lines up with AI 2D exactly.
  So the 3D was right all along. `watch3d.capture_held` now takes the compared 3D shot after the 2D ones, with a
  switch back (the jump's shot is kept if that fails). Earlier "camera moves between shots" notes (colne_2/4/5,
  niv_*, nivinn_2, las1_1) were likely this. The Nibel batch is being retaken.

- **The 3D colours didn't match the AI image** (F11: the Da-chao statues olive/acid yellow, Gaea's Cliff blue,
  warm patches in the crater caves, sector 8 green; F11 agent). Not the Relit light: in game the 3D shot over its
  colour texture is a flat 0.75 (the brightness) in linear light, sun or cave. The scene used the Studio render's raw
  colours, while AI 2D shows the render after the Studio's colour pass (`keep_colors`, the user's 0.8), applied
  before it cuts the pages. `glow.studio_pass` / `scene_colours` (the same fit as `ff7bg.ai.keep_colors`, with the k
  each version was built with; it reproduces the pages exactly) now feed the scene texture and its IBLs;
  `page_colours` / `repaint_seams` compare against them. In game, mean Lab a/b difference to AI 2D: datiao_1 8.5 ->
  1.7, gaia_32 12.9 -> 2.4, gaia_31 10.6 -> 3.8, md8_1 6.5 -> 2.2, las3_1 5.5 -> 1.6; chroma x1.3-1.5 -> x1.04-1.09.
  Every field moves toward AI 2D (controls by dE 3-6: cosin1, ancnt4, md1_2 lose saturation they had over AI 2D;
  farm barely changes). Not done: the characters' grade, floor light and haze still read the render's colours.
  Merged ~16:10; 154 built scenes with page art rebuilt and 132 looks re-applied (scratchpad
  `merge_f11/apply.py`, all ok, finished 17:43), the rest of the run builds with it. `ff7-3d/dev/f11/scratch/cap/cmp_<field>.jpg`.
- **rootmap's build failed** (18:46, job 1117: `atmos.particles` drew floor particles from a walkmesh with no
  triangle in front of the camera; `rng.choice` on nothing). Now a walkmesh the camera can't see gets no floor
  particles (patched 18:53 at a safe moment; backup scratchpad `rootmap/atmos.py.before`); rootmap rebuilt (42 s,
  walkmesh covers 0%: a map-like field). Not reviewed yet.
- **Builds failing on walkmeshes just off the picture** (gldelev, junone3, md0: 2.6-44 px below the canvas):
  `depth.off_picture_fit` anchors DA3 where the walkmesh comes nearest the picture, with the median depth spread of
  the 271 good fits (no bend; `depth_fit.json` says `fallback`). All three built and enterable (md0 isn't a movie).

- **mds7pb_1's table** (user: "the table is overlapped by the floor"): rebuilt in this run (15:58) from the
  current version; in game the table stands whole on the floor (`report/review/mds7pb_1/3d.jpg`).

- **Black shapes near the screen edge** (F12: hyou11, junbin3, junone7's "fish"; F12 agent): the game clips its
  field projection at a near plane (512 camera units in some fields: hyou10-12, icedun_1/2, junbin3, mds7pb_1; 50 in
  most; read from the running game, no rule found in the field data) and a far plane (~32000); FFNx-3d draws the
  scene with that projection, so scenery nearer than 512 or deeper than 32000 was clipped and the clear colour showed
  (the clip region matched the black shapes at 0.96-0.98). `build.clear_near_plane` moves scenery nearer than 532
  back along its camera rays (the picture unchanged; only to just in front of any character who could stand
  behind it) and `clear_far_plane` brings depths beyond 18000 in smoothly (order kept) under 28000; only the mesh
  uses the moved depth. junone7's left-edge sliver: the sky drift pulled from past the picture's edge
  (`life.sky_mask` now fades it out there). Merged 14:3x.
- **Junon's sea blocky** (jundoc1b, junone7; F12 agent): the sea frames cycle in a loop in the wave entity's
  script 1, and the Studio's `scripts.analyze` only reads script 0, so neither the Studio nor the 3D knew it
  animates; the 3D showed each frame only where it differed from the original by > OVERLAY_SAME: specks of sunlit sea.
  `glow.script_loops` finds such loops (a backward jump over a WAIT that switches states) and `entry_states` adds
  them: 20 fields. junone7 now matches AI 2D, flaws included: its states pass painted the sea as sky (a second sun,
  a hard horizon line, a doubled rock edge): re-rendering junone7's states would fix both views (Studio art).
  Studio side, not done: `scripts.analyze` should read every script for animations too.
- After the merge: 36 fields this run had already built are being rebuilt and 14 looks re-applied (scratchpad
  `merge_f12/apply.py`); 43 more get it from the run itself.

- **icedun_2's build broke every field's lighting for ~8 minutes** (12:58-13:06). Its depth fit is poor (a 1464,
  b -322; median error 26%) and put the nearest scenery behind the camera; `depth.anchor`'s log made that NaN, its
  blur spread it over the whole grid, the NaN extent went into its config section as `shadowmap_area = nan.0`, and
  that line made all of lighting/config.toml unreadable: FFNx-3d then loads an empty config (every field on FFNx's
  defaults until it parses again). Fixed: `depth.camera_depth` clamps the near depth before the bend too and refuses
  non-finite depth; `ibl.write_section` never writes a config.toml that doesn't parse. The config repaired by hand
  13:06 (backup scratchpad `config.toml.with-nan`); icedun_2 queued again; the 9 captures taken meanwhile
  (icedun_2, ithill, ithos, itmin1, itmin2, itown12, itown1b, itown2, itown_i) are being retaken. icedun_2's fit
  stays poor (floor error 41%): check its 3D after the rebuild.

- **Highwind bridge** (fship_22, fship_24, fship_2; F1 agent): the 480 sky tiles (param 1, a 6-frame animation) are
  on layer 2, the backdrop the game draws fixed to the screen, but were planned as overlays drawn over the scene with
  holes cut where the canvas has the dome, railing and frame; off the spot where screen and canvas line up, the sky
  covered the bridge and the holes showed the black "off" art as arcs. `glow.overlay_plan` / `entry_off_art`:
  switched solid layer-2 tiles are never overlays (the scene keeps the render's sky); a backdrop animation the
  render has none of gets its first frame baked in (`plan["baked"]`, fship_2). Lost: layer-2 tiles a script switches
  on later and that don't animate (a proper fix: the engine drawing backdrop tiles behind the scene, screen-fixed).
  Merged ~12:1x; fship_2/22/24 rebuilt and checked in game by the agent (`ff7-3d/dev/f1/scratch/shots/fship_2*`);
  hill, hill2, rcktin7, trnad_1, trnad_2 get it in the run (still queued). No change in del1, blin65_2, ancnt1, blin2,
  blin3_1, corel1 (pixel-identical plans).

- **blin3_1's sign** (F1/F4; user: "re-render blin3_1"): re-rendered with its blended message layers off in the
  AI input, so AI 2D cycles the original's messages on a blank board. In 3D they were hidden behind the board: the
  message tiles (param 4, page 15, switched and palette-animated, so glow layers, not overlays) sit at tile z ~3810
  (4 x 3810 = 15240 deep) behind the board's mesh (~3700), and push_under only handled overlays. Fix (F1 agent):
  `glow.overlay_plan` returns `plan["lit"]` (switched glow-layer tiles, with 2D visibility like overlays) and
  `build.push_under` pushes the scene behind them too (LIT_PUSH_CAP 6; blin3_1 needs x4.4). Verified in game: the
  board cycles red kanji, car, flash, motorbike. Merged 11:0x (during del2's build); ancnt1 and blin65_2 unchanged,
  blin2_i gets a small sign pushed (rebuilt in the run). Evidence `ff7-3d/dev/f1/scratch/shots/blin3_1_*.jpg`.
- **corel1's sky blocks**: confirmed gone in game with the engine brightness match: the step at the block edge is
  +0.6 levels (was +10). `ff7-3d/dev/f1/scratch/shots/corel1_engine.jpg`.

- **Models cut by the floor** (F2: ancnt2's boot, ancnt3's kneeling Cloud and lying Aerith; F2 agent): not the
  hiding match; the chibi boots and kneeling/lying models reach further below the walkmesh than the 15-unit sink
  (ancnt2's spot is a 25-degree ramp; ancnt3 draws at double scale). `snap_to_walkmesh` sinks walkable ground by
  `sink` 15 + `sink_deep` 15 + `sink_slope` 35 x the walkmesh's slope, all x the model scale (the floor's edge keeps
  15: wall bases don't move), judges walkable against the walkmesh itself, and hands the snapped-floor mask to
  `match_original_hiding` (floor never covers).
- **bigwheel's gondola half hidden** (F2 agent): a prop model (`fieldbg_v2_kanra`, ~1000 units tall) on its own
  one-triangle walkmesh was treated as a 90-unit character. New `field_models` / `placed_models` /
  `placed_layers` read what scripts place (CHAR, XYZI/XYZ/XYI with constant coordinates, OFST) with sizes from
  char.lgp (props from their .p mesh, PROP_UNIT 4) and feed `match_original_hiding` per-layer sizes (on a window
  of the grid: fast). 51 fields have big props.
- **bwhlin** (F6, F2 agent): the edge strips were the camera-math error (gone on rebuild); the 34% median is DA3's
  fit before the floor bend (floor error after it 4.6%). Cloud's sword poking into a bench the original never
  draws over characters: new reach rule (REACH_SIDE 1.1, REACH_DOWN 0.4 x body height: where the original has no
  tile in front of a character, non-floor scene around them goes behind them; `walk_clearance.reached`).
- Regression check (F2 agent): elm and mds7st1 rebuilt with the old and the new code, 13 spots in game: hiding by
  the original's tiles unchanged; elm tri 5 better (boots show). Evidence `ff7-3d/dev/f2/evidence/`.

- **blin63_t's dark panels** (F1 agent): the Studio's states pass repaints whatever a state changes, 1-level speckle
  included, so lit panels were painted where the original changes by only 4-24 and OVERLAY_SAME dropped them.
  New `glow.repaint_seams`: along a state's edge pixels where the original repeats its surroundings, the share
  where the version's page steps off the render by more than OVERLAY_SAME (blin63_t 0-11%, del1's ship 56-77%,
  other blocky cases 27%+); under REPAINT_CUT 0.2 (with REPAINT_EDGE 32 px to judge) the spot shows whole, as in
  AI 2D. 58 spots change over 15 fields (simulated closer to AI 2D or unchanged); del1 pixel-identical. Merged
  ~10:25. `ff7-3d/dev/f1/scratch/shots/blin63_t_before_after.jpg`.
- **corel1's sky blocks** (F1 agent + main): (1) `glow.page_colours` now fits on the best 80% of samples
  (corel1's static tiles under the smoke hold the Studio's all-off repaint): corel1 6 -> 0.6 levels, ~15 more fields
  get a mapping; (2) FFNx-3d draws the field's tiles over a relit scene at its brightness
  (`deferred.cpp match_scene_brightness`: vertex colours x relight[2]^(1/2.2)); they were 14% brighter than the
  scene (0.75 linear). Installed ~10:35 with the floor-mirror shader; corel1 rebuilt to check.

- **Boardroom window dull and ghosted** (F3: blin66_2, blin66_6; F3 agent). The window is a layer-2 backdrop (the
  nebula) seen through a static 50% averaging glass layer. The AI painted it as the original shows it (muted, glass
  streaks) and that render is the 3D texture, while AI 2D shows the Studio's own backdrop art without the glass.
  On top, Qwen had tagged the window as a screen and as mist (screen glow, scan bar, a purple point light, mist
  puffs), and Relit lit it like a wall. Fix: `glow.through_glass` (the scene takes the version pages' backdrop art
  there; only these two fields in the game have it), `relight.keep_painted` (not relit there, like sky),
  `life._off_backdrop` (screen / mist / window finds over it dropped). Merged 2026-11-02 ~10:10; evidence
  `ff7-3d/dev/f3/evidence/blin66_2_window_before_after_ai2d.png`, `blin66_6_...`.
- **Reflections on floors that aren't shiny** (F5; F3 agent). `life.reflections`: no floor reflection where the
  walkable floor is painted as carpet (strongly saturated): blin60_2. `life.floor_mirror` in the relit sun map's A
  (up-facing by Marigold's normals, not carpet) + a fork shader change (`FFNx.lighting.frag`: the reflection pass
  multiplies by it): ancnt3's altar front stops mirroring (in the fork, not installed yet). blinele: its
  `features.json` scene `polished` set to false by hand (no image test separates its grey metal plate floor);
  takes effect with its next look apply.
- **Church corner colour patches** (F5; F3 agent): not point lights; the static light layer added
  `static_glow` 0.8 - `light_kept` 0.54 = 26% of the original's teal orb and pink floor glow, in its tile shapes,
  over what the AI had painted. `glow.light_added` fits each light on its own: one the render already shows at
  least 0.35 of gets nothing added. Small reductions in 10 more fields on re-apply (colne_2, blin64, blin2_i,
  blin671b, blin67_1, blin66_1, convil_1, blin70_1, blin70_4, blin62_1).
- **blin63_1's tinted lift shaft** (F5; F3 agent): the window/screen light spill (`life.lights`) put the door
  lights' cyan and red on the black shaft; no spill on near-black painting now (luma under 16, full from 32).

- **The characters' settings are game-wide** (F10; user: "some settings would be better as global settings, e.g.
  character outlines"; "Fix your findings" 2026-11-02 taken as the go, game-wide only). FFNx-3d now looks a
  setting up in the field's section, then a new `[fields]` section (fields only, so battles and the world map are
  untouched), then the top of the file. `[fields]` holds `build.GAMEWIDE` (`atmos.fields_settings`): character
  shadows, haze on characters, colour match (`char_match`: the engine takes that much of a field's whole grade,
  `char_grade_full`), light where they stand, outlines (`char_outline` x the field's `char_outline_scale`),
  softness, the shadow under them, finish (material + `char_exposure`), brightness (`char_ambient`), and
  `char_round = 0`. Written by every look step and by any All-fields change in the 3D tab (`build.set_shared`), so a
  change shows in the game at once with no re-apply; a field can't have its own (set_options drops them). The 3D tab
  gets a group "The characters, in every field" after the Studio's next restart (it was running a build of the
  user's). Existing config converted (`scratchpad f10/migrate.py`, backup `config.toml.before-gamewide`): 272
  sections. Old sections keep their `char_grade` (match and exposure baked in) until their look is applied again.
  Verified: ancnt4/anfrst_1 unchanged (mean diff about 1 level), outlines at 60% live via [fields] alone.
  Fork changes: `Lighting::getConfigEntry`, the grade and outline reads in `lighting.cpp`.
- **3D effects showed through the game's fades** (F7: cargoin, chrin_1b): the scene's glow and the particles were
  drawn after everything, the game's fade quad and text windows included. Now `ffnx3d::drawSceneExtras` draws them
  in the field's deferred replay right after the scene and its overlays, before the game's own draws (fallback at
  the frame's end, once a frame). Verified: chrin_1b's black screen is black. Side effect: a mote in front of a
  character is now behind him (characters draw after).
- **Studio 3D preview camera** (fix agent's optional patch): applied to studio_plugin.py / studio_view3d.js;
  takes effect on the Studio's next restart.

- **Camera-math error: geometry off its rays far from the world origin** (blue_1's thick black edge lines and black
  strip; fix agent). `Field.unproject` used R^T as R's inverse, but R is stored to 1/4096 and isn't quite a
  rotation: points sat off their camera rays by (R R^T - I)(P - T), up to 10 px near the camera in blue_1 (camera
  ~10900 from the origin, its thin walkmesh put 37% of the scene very near). Fix: `Field.Rinv` (exact inverse) in
  common.unproject, build's crack copies, life's camera position. Applied live 23:33 (backups scratchpad
  `fixagent/before/`). blue_1 rebuilt by the agent (`report/fix_blue1_before_after_ai2d.jpg`); rebuilds queued:
  ancnt3, blin66_5, bwhlin, bwhlin2, clsin2_2; fields not built yet get it (md1stin was 19 px off, gaiin_4,
  gaia_31, fship_2/22/24, fr_e...).
- **Switched light layers lit the scene for good** (blin65_2: yellow glow ring and yellow tile blocks in the black
  pit; fix agent). `atmos.light_map` skips switched layers that were off when the render was made; new
  `glow.surface_light`: a switched light layer that tracks the original's surfaces (corr > 0.6) lights the AI
  painting by the original's brightening ratio (max 3x, near-black stays black) instead of adding its own art.
  Applied live 23:33; its 20 fields' looks re-applied 2026-11-02 09:4x (scratchpad `dropown/relook2.py`). The
  lights-on state is verified only in a simulation (the event never fired in game).
  `report/fix_blin65_2_before_after.jpg`.
- **blin2's billboard** (blown to white): fine after the look re-apply (it had the old Painted look).
- **Characters dark and dull, blending in** (user, 22:50; chose fix 1 of 2). With Relit, shading 100%, brightness
  75%, every field had sun 6.0 / ambient 0.28 (`build.lights`); the relit scene hardly depends on that, the
  characters do. A/B (scratchpad `chars/`, via config.devtools.toml): ambient 3.0 lifted anfrst_1's Cloud +45% with
  the scene unchanged; floor light, character IBL, grade exposure, a balanced split each did little; no grade +15%
  on ancnt4. Fix: FFNx-3d `char_ambient` (characters' own ambient in fields, `Renderer::setLightingUniforms`, not
  the scene), option `char_fill` (default 1.0 = `CHAR_FILL` 4.0 x brightness = 3.0) written by every look step,
  3D-tab slider "Character brightness" (shows after the Studio's next restart). `char_ambient = 3.0` added to all
  272 field sections at once (backup scratchpad `jumpfix/config.toml.before-charfill`). Verified
  (`report/char_fill_after.jpg`). Not done (option 2): char_light 80% -> 40%; in green scenes the brighter
  characters take a green tint from it.
- **Sparkly dotted fringes along depth edges** (ancnt1, ancnt3): fixed 21:48 by `surface.steady`. A/B in game on
  ancnt1: gone with a flat normal map; not caused by specular, point lights, reflection, water motion, scene_fx or
  glow, nor by only the saturated normal-map texels. The fix fades the map out on surfaces turned 72-77 degrees
  from the camera (STEEP tan 3-4.5) and within 3-6 texels of a depth break (NEAR_BREAK), in every look step.
  Fields built earlier got it from a look re-apply.
- **Own settings dropped** (user, ~21:45): 249 fields kept their own look settings over All fields; all dropped
  (Shape kept: sway in bonevil, chrin_1a, eals_1, mtcrl_0; backups scratchpad `dropown/`). Fields rebuilt earlier
  got their look re-applied (scratchpad `dropown/relook.py`, 37 fields, during a build's first stage so the
  lighting config is never written twice at once).
- **Builds crashing on fields with no up-facing walkable floor** (bugin1b, bugin3: "index -1 is out of bounds" in
  `depth.camera_depth`'s floor stats): guarded ~22:25, both queued again at the end of the run. Check they built.
- **Jumps held by events** (astage_b's play wedged every later jump): fixed in FFNx-3d `field.cpp` (opcode thunks,
  arrival check), installed ~21:45.

## Game memory (2026-11-03 11:49-11:52)

- The game's virtual size went 1.9 -> 2.3 -> 2.9 -> 3.3 -> 3.9 GB over ancnt3, ancnt4, anfrst_1 and anfrst_2 (it
  had stayed 1.6-2.1 GB all morning). Near the 32-bit limit its textures stopped loading: those 3D shots are flat
  grey/white, untextured (the scene textures on disk are fine). Then it hung, and the watcher restarted it at
  11:52.
- Cause not known yet. It started with the first-run fields being rebuilt and shot, while the run's builds used
  the GPU (Qwen). No game file changed for those fields beyond their own builds.
- `watch3d.capture_held` now restarts the game before a capture when it's over `MEMORY_LIMIT_MB` 2600.
- Retaken 11:56-11:58: all textured and right, with memory flat at 1.7-1.8 GB on the same fields. So it wasn't
  the fields. It happened while the F17 page rewrite (141 fields' pages, 11:40-11:50) was writing files the
  game might reload, and while ComfyUI was busy; neither is proven. If it climbs again, note what ran then.

## Game crash (2026-11-03 12:23, entering chrin_1a)

- FFNx's "Game crashed" dialog: an access violation in `bgfx::createFrameBuffer` <- `Renderer::prepareShadowMap`
  <- `Lighting::initParamsFromConfig` <- `Lighting::draw` (FFNx.log). Memory was 2062 MB virtual.
- `initParamsFromConfig` runs on every field change, and each time `prepareShadowMap` destroyed the 64 MB
  shadow depth target (4096^2 D32F) and made a new one. In the 32-bit game that churn fragments the address space.
  It likely also caused the 1.9 -> 3.9 GB climb at 11:49 and the untextured shots.
- **Engine fix:** `prepareShadowMap` keeps the target while its resolution is unchanged (renderer.cpp; backup
  scratchpad `shadowfix/`). **Installed 12:33**; jumped through 10 fields (ancnt3, ancnt4, anfrst_1/2, chorace,
  mtcrl_5, blin1, tin_2, nvmin1_1, elm): virtual memory flat at 1.6-1.8 GB. The 11:49 climb didn't reproduce at
  11:56 either, so this isn't proof; watch memory_mb.
- **Watcher:** a jump timeout with the frame counter still for 3 s now restarts the game at once. The 20 s hang test
  restarted per watcher run, so a crashed game was retried 9 times. Game restarted 12:29.

## Game crash 2 (2026-11-03 16:59, crater_2's glow frames)

- Shooting crater_2 through view switches, memory went 1.94 -> 3.97 GB in ~25 s, then an access violation in
  `loadPng` <- `load_animated_texture` (crash log `report/review/crash_logs/FFNx_20261103_165938.log`).
- crater_2 has 608 3D glow frames (512^2, ~1 MB each decoded). Others: bugin3 13741, rootmap 2431, ancnt2 1220,
  whitein 720 ... The morning's climb went through ancnt2/3/4 (244-1220 frames each), so it's likely the same
  cause: animated frames kept per entry, and each view switch re-enters the field.
- The game restarted 16:59. Agent F24 (17:0x): engine handling of animated frames, plus a per-field frame budget
  in the pipeline. (Fixed 2026-11-06 by F24: see the top of this file.)

## Can't be looked at

- blackbg1, blackbg4, blackbgb: black backgrounds (skipped).
- bwhlin2: entering it moves the game on to bwhlin.
- blin67_4: the game ends up in blin67_3. The watcher now checks the field after the 3D shot.
- clsin2_1/2/3: entering starts the coliseum battle, which needs keys (the game got stuck there 22:45; restarted).
  The watcher skips them.
- gaiin_5: entering it leaves field mode.
- gidun_4: entering it leaves field mode.
- junonl1: entering it moves the game on to junone7.
- junonr4: entering it moves the game on to junin1.
- fr_e: entering it leaves field mode (a movie or the world map).
- cosmo2: its entry event moves the game on to bugin3, then md_e1 (checked 2026-11-02).
- mtcrl_8: entering it doesn't stay (the game ends up in mtcrl_6).
- sininb36: entering it moves the game on to sininb35.
- slfrst_1: entering it moves the game on to sea.
- cosmo2: entering it moves the game on to bugin3. hyou5_2: its entry re-enters itself (can't be shot).
- nmkin_4: entering it moves the game on to eals_1. nrthmk: moves on to nmkin_1. nvmkin23: moves on to nvmkin21.

## Recapture

- (las4_2, las4_3, las4_4, lastmap, life, losin1 retaken; las4_42 moves on to lastmap.) las4_3 took three tries
  (a freeze at 15:07, a jump timeout at 17:01, captured 17:21).

- (none open) blin60_2 was recaptured after its slow fade-in (the watcher now waits out fades); colne_2/4/5 after
  the 22:53-22:56 lock collision (fixed with tools/gamelock.py).

## Looked at, fine

ancnt1 (apart from the dots), ancnt4, anfrst_1 (3D), anfrst_2, anfrst_4, anfrst_5, astage_a, astage_b (3D), blin1,
blin2_i, blin59, blin60_1, blin62_1, blin62_2, blin62_3, blin63_1, blin64, blin65_1, blin66_1, blin66_3, blin66_4,
blin66_5, blin671b, blin60_2 (3D), blin673b, blin67_1, blin67_2, blin67_3, blin68_1, blin68_2, blin69_1, blin70_1,
blin70_2, blin70_3, blin70_4, blinele (see F5), blinst_1, blinst_2, blinst_3, blue_2, bonevil, bonevil2, bugin1a,
bugin2, canon_1, canon_2, chorace, chrin_1a, chrin_2, chrin_3a, chrin_3b, colne_1, colne_3, colne_6, convil_2,
convil_4, corel2, corelin, cosin1, cosin1_1, cosin2, cosin3, cosin4, cosin5, cosmin2, cosmin3, cosmin4, cosmin6,
cosmin7, cosmo, cos_btm2; second run: bugin1b, bugin3 (a white-out, both views), crcin_2, datiao_7, datiao_8, del1, del2, delinn, delmin1, delmin2, delpb, desert1, desert2, dyne, ealin_1, ealin_12, ealin_2, eals_1, elevtr1, elmin1_1, elmin1_2, elmin2_1, elmin2_2, elmin3_1, elmin3_2, elmin4_1, elmin4_2, elminn_1, elminn_2, elmpb, elmtow, elm_i, elm_wa, farm, frcyo, fship_1, fship_42, fship_3/4/5 (entry scrolls the camera; nothing wrong seen), gaiin_2, gaiin_3, gaiin_6, gaiin_7, games, games_1, games_2, ghotel, ghotin_1, ghotin_2, ghotin_3, gidun_1, gidun_2, gldgate, gldinfo, gldst, gninn, gnmk, gnmkf, gomin, gongaga, gonjun1, gonjun2, gon_i, gon_wa1, gon_wa2, goson, hekiga, hideway1, hideway3, hill/hill2 (3D's Highwind cleaner than AI 2D's pixelated backdrop art), holu_1, holu_2, hyou1, hyou10, hyou12, hyou13_2, hyou2/hyou3/hyou4/hyou5_1 (3D replaces the blizzard grain with snow particles, as meant; clearer than AI 2D), hyou5_3, hyou5_4, hyou6, hyou7, hyou8_2, hyou9, hyoumap, icedun_1, ithill, ithos, itmin1, itmin2, itown12, itown1b, itown2, itown_i, itown_m (the latter retaken after the config repair), itown_w, jail1, jail2, jail3, jail4, jailin1, jailin2, jailin3, jailin4, jailpb, jet (its screen animates), jetin1, jtempl, jtemplb, jtemplc, jtmpin1, jumin, junair2, junbin1, junbin12, junbin22, junbin4, junbin5, jundoc1a, junele1, junele2, junin1, junin1a, junin2, junin3, junin4, junin5, junin6, junin7, juninn, junmin1, junmin2, junmin3, junmin4, junmin5, junon (entry scrolls the camera), junone2, junone22, junone4, junone5, junone6, junonl2, junonl3, junonr1, junonr2, junonr3, junpb_1, junpb_2, junpb_3, junsbd1, jun_a, jun_i1, jun_i2, jun_m, jun_w, jun_wa, kuro_10, blin66_3, kuro_11, kuro_12, kuro_3, kuro_5 (3D; a random battle started during its view switch), kuro_6, kuro_7, kuro_8, kuro_82, kuro_9, las0_3, las0_4, las0_5, las0_6, las0_7, las0_8, las1_2, las1_3, las1_4 (the crater: 3D warmer/more saturated, F11), las2_1, las2_2, las2_3 (3D cleaner than AI 2D's tile patches), las4_0, las4_1, life, losin1, losin2, losin3, losinn, loslake2, loslake3, lost1, lost2, lost3, md1stin, md1_1, md1_2, md8brdg, md8brdg2, md8_32, md8_4, md8_5, md8_52 (moves to its tunnel part during the AI 2D switch), md8_6, md8_b1, mds5_1, mds5_2, mds5_3, mds5_5, mds5_dk, mds5_i, mds5_m, mds5_w, mds6_2, mds6_22, mds6_3, mds7, mds7pb_1, mds7pb_2, mds7plr1, mds7plr2 (its entry cutscene moves the camera), midgal (the flashback; the camera moves), min51_1, min51_2, min71, mktinn, mktpb, mkt_ia, mkt_m, mkt_mens, mkt_s1, mkt_s2, mkt_s3, mkt_w, mogu_1 (3D's door cleaner than AI 2D's), move_f, move_i, move_r, move_s, move_u (glacier: 3D's snow particles and blue tone vs AI 2D's grain and white), mrkt1, mrkt2, mrkt4, mtcrl_0, mtcrl_1, mtcrl_2 (3D's mako glow green as in the original; AI 2D painted it
cyan-white), mtcrl_3, mtcrl_6, mtcrl_7, mtcrl_9, mtnvl2, mtnvl3, mtnvl5, mtnvl6, mtnvl6b, las4_2, ncoin1, ncoin2, ncoin3, ncoinn, ncorel, ncorel2,
ncorel3 (the NPCs move, so the views differ there), nivgate (the flashback's white-out, both views), nivgate2 and
nivinn_2 (flashback cutscenes: characters and camera move between shots), nivinn_1, nivinn_3, nivl_3, nivl_b1
(white-out, both), fship_2 (retaken), hyou13_1 (retaken: 3D's rocks browner and bluer than AI 2D's whitened
blizzard grain; both have the blizzard layer's band, F5 move_d), las1_1 (retaken: the camera still scrolls on
entry, so the views show different parts; 3D's part looks right), las4_3 (3D; AI 2D's black is under F4),
nivl_e1, niv_cl, niv_ti1, niv_ti2 (flashback: the camera follows young Cloud elsewhere between shots; 3D's part
looks right), niv_ti3, niv_ti4, niv_w (flashback, as niv_ti1), nmkin_1, nmkin_2, nmkin_3, nmkin_5, nvdun1, nvdun2, nvdun3 (retaken with the
watcher fix: they match AI 2D), nvdun31, nvdun4, nvmin1_1, nvmkin1, nvmkin31, nvmkin32, onna_2, pillar_1, pillar_2, pillar_3, prisila,
psdun_1, psdun_2, blin66_6 (rebuilt with F13: the nebula window in place, the hologram whole), psdun_3,
psdun_4, qa, qb, qc, bwhlin2 (F13: the scenery passing the gondola's windows differs by timing), qd, rckt, rckt2,
rckt3, rckt32, rcktbas1, rcktbas2, rcktin1-8, rktinn1, rktinn2, rktmin1, rktmin2, rktsid, rkt_i, roadend (3D's
lamps glow warmer and redder than AI 2D's; taste), ropest, sandun_1, sandun_2, sango2, sango3, sbwy4_1, sbwy4_2, sbwy4_22, sbwy4_5, sbwy4_6, sea, semkin_1, rootmap (after the particles fix), semkin_2-8 (semkin_8's pointer arrow is a turning model), seto1 (cutscene), ship_1, ship_2 (3D; its AI 2D shot caught ship_1: the view switch's re-entry moved on), shpin_2, shpin_22, shpin_3, sichi, sinbil_1, sinbil_2, sinin1_1, sinin1_2, sinin2_1, sinin2_2, sinin3, sininb1, sininb2, sininb31 (the Shinra Mansion), sininb32, sininb34, sininb35 (cutscene), sininb41, sininb42, sininb51, sininb52, slfrst_2, smkin_1-5, sninn_1, sninn_2, sninn_b1, snmayor, snmin1, snow, snw_w (Icicle Inn), sky (after the 16-bit page fix: sky and mountains behind the plane), gldst, southmk1, southmk2, spgate, spipe_1, spipe_2, startmap, subin_1a, subin_1b (the console lamps glow softer in 3D than AI 2D's white-hot ones; taste), crater_2 (3D), datiao_3, datiao_4, elm, mds7st1, subin_2a, subin_2b, subin_3, tin_1, tower5, trackin (black narration, both views), trackin2, trnad_1, trnad_4 (3D a little clearer, less teal haze than AI 2D; taste), trnad_51, trnad_52 (white-out, both), trnad_53, tunnel_1-5, ujun_w, ujunon1, ujunon2, ujunon4, uta_im, uta_wa, utapb, utmin1, utmin2, uttmpin2, uttmpin3, uttmpin4, uutai1 (3D's stream calmer than AI 2D's glittering water), uutai2, wcrimb_1, wcrimb_2, elmpb, mds7st32, rcktin5, mogu_1 (retaken after the frames fix), del3 (the surf now wide and soft), white1, white2 (Holy/Meteor cutscene: the views catch different moments, the original black), whitebg3, yougan, yougan2, yougan3, yufy1, yufy2, zcoal_1, zcoal_2, zcoal_3 (the coal-train chase: 3D's edges cleaner than AI 2D's stair-stepped cut-outs), zmind1 (black, both views), zmind2, zmind3, trnad_2 (retaken 11:39 after the cover fix: no deep blue, its sky pale as AI 2D), white1 (retaken), tunnel_6 (retaken after F17: the lit tunnel whole, as AI 2D), ztruck, zz1, zz2, zz3, zz6, zz4/zz5/zz7 (apart from F19), zz8 (apart from F19), ancnt1/ancnt2 (apart from F19's light shafts), anfrst_3 (both views have the shaft layer's faint tile edge at the right: F4), anfrst_4, anfrst_5, astage_b, bigwheel, ancnt3, ancnt4, anfrst_1/anfrst_2 (retaken; their light shafts weaker: F19), blin1, blin2, blin2_i, blin59, blin60_2, blin61 (now enterable), blin62_1, blin62_2, blin62_3, blin63_1, blin63_t (the lit panels right), blin64 (3D a little dimmer), blin65_1, blin65_2 (its room lights look as AI 2D at this spot), blin66_1, blin66_4, blin66_5 (3D's glass shield a softer green), blin671b, blin673b, blin67_1, blin67_2, blin67_3, blin68_1, blin68_2, blin70_1, blin70_2, blin70_3, blin70_4, blinele, blinst_1, blinst_2, blinst_3, blue_1 (3D cleaner than AI 2D's dark tile outlines), blue_2, bonevil, bonevil2, bugin1a, bugin2, canon_1, canon_2, chorace, chrin_3a, chrin_3b, church (F18's window change kept it right), colne_1-5, colne_6 (F1 latent), colne_b1, colne_b3, coloin1, coloin2, coloss, condor1, condor2, convil_1, convil_2, convil_4, corel1, corel2, corel3 (F1's doubled-fire art, both views), corelin (cutscene), cos_btm, cos_btm2, cos_top, cosin1, cosin1_1, cosin2, cosin3, cosin4, cosin5, cosmin2, cosmin3, cosmin4, cosmin6, cosmin7, cosmo (Cosmo Canyon: all as AI 2D), chrin_1a, chrin_1b (black narration, both), chrin_2, crater_1, hill, hill2 (after the layer-3 fix) (colne_2/4/5: entry cutscenes scroll the camera, so 3D and AI 2D show different parts; nothing wrong seen).
