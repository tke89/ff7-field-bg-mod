# Batch producing battle scenes and 3D models: what's needed

Written 2026-11-07, after painting the first models by hand (Midgar Zolom; Cloud and Barret in battle; field Cloud). Status: **done**, **partly**, **to do**. The battle and model pipelines are in `docs/models-plan.md`; the engine formats are in `ff7-3d/dev/battle-atlas/FORMAT.md` and `ff7-3d/dev/battle-engine/FORMAT.md`.

## How big it is
| What | Count | One paint takes | All of it, once |
|---|---|---|---|
| Battle scenes (stages) | 90 (a few are empty) | ~1 min painting + ~5 min battle look (AI parts) | ~9 h |
| Battle models: enemies | 351 (most untextured) | ~2-3 min (2D style, one variant) | ~15 h |
| Battle models: party | 21 forms, plus their weapons | ~2-3 min each, more for weapons | ~2-3 h |
| Battle models: others | 18 | ~2-3 min | ~1 h |
| Field models (your chibi set) | 272 | ~2-3 min | ~12 h |

Three variants per model, with automatic checks and retries, roughly triples the model times, to about 80-90 h of GPU time. All of it is AI time with the game closed, so it suits overnight runs.

## 1. Coverage: what can't be painted yet
- **done** Untextured battle models get a painted texture: the battle atlas, in the engine and the data code.
- **done** Battle models are read the way the game reads them, `direct/` mods first. Your modded Barret is the case that needed it.
- **partly** Field models: engine support built, Cloud being painted and not yet seen in game.
- **to do** Party weapons beyond weapon 0. Each party member has many weapon models, and the file names for the others (`.W01`...) are assumed and unchecked.
- **to do** Models with no game name, so the game file can't be named (e.g. the Galian Beast forms).
- **to do** Decode every animation frame, not just frame 0. This is needed to find moving parts (tongues, jaws, wings, tails) automatically for hundreds of monsters, and to pick a good pose to paint from. Zolom's tongue was found by hand.
- **to do** Untextured parts of battle stages. The engine would take an atlas, but painted stage parts would get the models' flat light. Stages need their own light rule, and stages are never rounded.
- **to do (later)** Summon and limit-break models (magic.lgp) and world map models: not covered at all.

## 2. Recipes and defaults per kind of model
A one-click paint is only as good as its recipe. What the hand-painting taught, made automatic:
- **done** Keep the game's own colours: the colour lock after painting.
- **done** Paint moving parts on their own (Zolom's tongue chain).
- **done** The party: placed as in battle, painted from close views, front first.
- **to do** Pick the recipe from the model itself:
  - size (huge monsters like Zolom vs small enemies);
  - party vs enemy;
  - humanoid vs creature vs machine;
  - textured share.

  Keep per-model overrides (words, strength, views) with the model, so a rerun repeats them.
- **to do** One shared look for everything: the same look settings and style references, and colours that suit the scene each model fights in.
- **to do** Fix `paint.quality` rating far battle cameras as perfect views at 2048 (they painted over the close views). The party is held at 1024 because of it.

## 3. Batch machinery in the Studio
- **done** One model per click: "Paint in 2D style" on each battle model, with versions, pictures and Install/Uninstall.
- **in progress** Variants (2-4 paints per job), an automatic AI check with automatic repaint, and a touch-up brush for models.
- **to do** A batch page for models, like the fields' "Generate all missing":
  - "Paint all" by filter (kind, scene, painted or not, in the game or not);
  - skips what's done;
  - ordered by story order (the first formations met come first);
  - an overall time estimate.
- **to do** Unattended runs:
  - resume after a crash or restart;
  - a ComfyUI watchdog;
  - refuse to start while the game runs, and wait instead of failing;
  - clean up old versions and half-made ones;
  - a disk space check.
- **to do** Battle scenes in batch: paint each scene, then build its battle look, then install both, one job per scene.

## 4. Quality: finding the bad ones without looking at all of them
- **done** Identity check: the painted texture's layout matches the model, so nothing lands in the wrong place.
- **in progress** Automatic AI check per variant: extra or missing parts, faces, colours. Its measured reliability decides how much review is left to do by hand.
- **to do** Number checks per version:
  - colour drift from the original per part;
  - unpainted (unseen) area;
  - seams between views;
  - how far the silhouette reads as drawn.
- **to do** A review page:
  - one contact sheet of every new version with its check results;
  - the flagged ones first;
  - accept, repaint or touch up from there;
  - "install all accepted".

## 5. Checking in the game
- **done** Live control: `battle <formation>`, god mode (hits do ~1), `ok`, screenshots, restarts that hand focus back.
- **to do** An in-game pass: for each formation (about 1,000), start it, shoot it from the resting camera, restart, and make a contact sheet per scene. That's about 40 s per formation, roughly 10 h. It runs with no AI, so it can alternate with painting nights.
- **to do** Get past reward screens without focus (FF7 ignores input while in the background), or keep ending battles with a restart, as now.
- **to do** Watch for problems specific to the game: memory, load time, missing parts, a painting left over from a changed mod.

## 6. Installing, mods and memory
- **done** Install/uninstall per model with a manifest. The engine refuses a painting whose model doesn't match, so a changed mod can't break the game.
- **to do** After a 7th Heaven mod change, re-check every installed painting against the game's files and flag the stale ones. Today they're just silently refused.
- **to do** Memory budget: the game is 32-bit. Painted textures are loaded per battle and freed after, but a formation with several big monsters, plus a painted stage, plus the party, needs a measured ceiling. Sizes: 2048 for monsters, 1024 for the party; field textures are many small ones per part.
- **to do** "Install all" / "uninstall all" per kind, and an install summary.
- **to do (for sharing)** Package everything as a 7th Heaven mod (`ff7-3d/tools/iro.py` has the start of this). Players would also need FFNx-3d, which isn't published.

## Suggested order
1. Finish what's in flight: field Cloud in the game, then variants, the AI check and touch-up.
2. Decode animation frames, so moving parts are found automatically, and pick recipes by kind of model.
3. The batch page with resume, the review page, and number checks.
4. Battle scenes in batch (scene paint plus battle look).
5. The in-game verification pass.
6. Memory budget and mod-change re-checks.
7. Weapons, the remaining party forms, field NPCs.
8. Later: summons, limit breaks and the world map; packaging.
