# Battle look: test results (main session, 2026-11-07)

Build: the battle-look patch (TESTPLAN.md), installed 10:15; then two fixes of mine (below), installed 10:40 and
after. Test data: testdata/ (strong, subtle, bat310), removed again (`install_testdata.py off`). Shots and scripts:
the main session's scratchpad `battletest/` (not kept).

| Test | Result |
|---|---|
| T0 fields unchanged | pass (md1_1 mean diff 0.02; farm 1.49 = its moving grass) |
| T1 battle, no data | pass: `battle_stage 10`, FFNx's eye (9186, -904, -2001) and the scene's (9186, -914, -2001) agree; scene 192 lit 192 |
| T2 sun direction | pass, but the convention was wrong in FORMAT.md: H 0 lights from **+z** (the party's side, screen right from 310's camera), 90 from +x (the camera's side). Lit sides and shadows flip together. FORMAT.md corrected |
| T3 haze | pass. haze_scene 0 clears the reactor, characters keep theirs; haze_strength 0 clears all. The railings near 310's camera stay dark under the strong haze: right (they're nearer than haze_start), not a bug |
| T4 grade | pass: characters graded, the reactor not. **Fixed**: battles graded the characters after the haze, so a blue haze turned pink on red-graded characters; now graded first (FFNx.lighting.frag, battles only; fields unchanged) |
| T5 normal maps | pass, sign right: the domes' bright sides follow the sun with the characters' (H 0/90/180/270 at V 60). Invisible at the test's V 30: oq's floor is all in its walls' shadow below ~40 degrees (swept 20-60: 0% lit to 35, all lit by 60) |
| T6 floor light | works, weak on the characters at this distance (Barret 41 vs 44 grey levels with/without) |
| T7 drift, sway | pass: status scroll 80 sway 72; 0 when removed |
| T8 particles | pass: `battle_particles 3 ~480`; dust count 0 hot-reloads (98 left: smoke and sparks); smoke rises and drifts to -z (screen left): orientation right. `view original`: none drawn |
| T9 group order, IBL | pass: [bat_310] turns 310's haze orange over [battle_STAGE10]; a battle_STAGE10_s/_d.dds pair hot-loads and changes the light |
| T10 subtle | looks right (shot for the user) |
| T11 outlines | pass: rims on the party and Grunts, none on the reactor |
| T12 other scene | pass: `battle 469` shows `battle_stage 8` |
| T12 back to the field | **not checked in game**: FF7 reads no input with its window in the background, so the reward screens can't be passed without taking focus (the new `ok [n]` live command sets FFNx's simulated OK; once in 2 runs it got through, the other time nothing in 2 minutes). Code read instead: Lighting::draw re-reads the settings on any mode change (battle -> field included). The one time it got back, it found the bug below |

## Found on the way
- **God mode let a one-hit attack through**: Midgar Zolom's Beta (formation 469, oo) killed Barret and Cloud between
  frames, Game Over. Fixed in ffnx3d_live.cpp: god mode also sets the battle's Defense and MDefense to 511 (hits do
  ~1). Peerless (status 0x01000000) stopped the damage too but pulses the party gold, useless for looking at a look.
- **`battle <formation>` fought the battle again** three frames after the field came back (MODE_FIELD at frame
  4164, MODE_SWIRL at 4167): FF7's BATTLE opcode runs twice (start, then finish when the field resumes; the field
  file isn't re-read after a battle, so the script in memory is the one we changed), and field.cpp put the original
  script back right after the first run. Fixed: the injected BATTLE stays until its second run finishes
  (`battle_injected`, `battle_resume`), then the script and position go back. Built and installed; not yet seen
  working end to end (the reward screens, above). This affected the Studio's "Try in a battle" too.
- **restart_game froze the game in front** (tools/watch3d.py): when the window in front before a restart was the old
  game's, the hand-back failed and its Alt tap left the new game in Windows' menu mode (no frames until it lost
  focus). `hand_back` now falls back to the Claude app's window and checks.
- **oo with battlelight's build** (formation 469, before/after): looks right; uninstalled again.
- **battlelight.py's sun was mirrored front to back** (it followed FORMAT.md's -cos V cos H); fixed, and its painted
  sun is now raised until the floor round the fight sees it (`clear_sun`, a ray test that matches the game's
  shadows on oq). oq's sun went from V 35 (floor 0% lit) to V 55 (96%).
