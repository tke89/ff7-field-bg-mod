"""Painted textures for battle models, untextured ones included (FFNx-3d's battle atlas, 2026-11-07): one atlas image
per model and a UV file per part, so every triangle can show a painting (ff7-3d/dev/battle-atlas/FORMAT.md; this is
its "Data" side).

Why: the user wants battles to look like a 2D anime (a repaint of formation 469's frame, work/models/battle/target2d:
drawn, flat fills, painted shadow shapes, thin coloured lines); shaders and screen filters were rejected. The drawing
has to live in the models' textures, drawn with flat light, but FF7's battle models are mostly vertex-coloured:
Midgar Zolom ("dd", the game's ENEMY081) has no texture at all.

What the engine reads (mesh/battle/atlas/ under the game's external_mesh_path, "mesh"):
- <MODEL>.png: one RGBA atlas per model (<MODEL> the game's name, battle.game_name: ENEMY081), every triangle's whole
  colour (what the game shows now, or a painting of it);
- <MODEL>.P<nn>.uv, one per part as the game names it ("FFUV", u32 version 1, u32 numverts, u32 numpolys, then per
  polygon of the .p file in its own order 3 x (f32 u, f32 v), v = 0 at the image's top). numverts and numpolys are the
  .p file's header counts: FFNx-3d ignores a file whose counts don't match.

Part names: the game loads a battle model's parts as <MODEL>.P<nn> with nn the bone index, i.e. battle.lgp's file
<id> + battle.part_role(nn) (role "am" + nn), and skips bones without a part. Checked 2026-11-07 against FFNx.log's
"reading p file: battle/..." lines: ENEMY019 (the Grunt, "at") loads P00 P01 P02 P05 P06 P08 P10 P12 P14 P17 P18 P20
P22 P24 P26 P28 P29 P30 P32 P33 P34, exactly the 21 bones whose header has a part (and whose atam.. file exists);
ENEMY081 ("dd") loads the 39 bones with a part, P00-P43 without P07 P09 P11 P36 P40, again exactly.

The atlas: atlas.build on the model in part space (each part's own coordinates: parts are rigid, so the atlas holds
for every pose and animation), size ATLAS_SIZE, filled with what the game draws now: vertex colours (Gouraud) for
untextured triangles, texture x vertex colour for textured ones. identity() checks it: the atlas-textured model
rendered from a battle camera against the original.

Painting (paint(); the Models "projected" method, as field models are painted): the model placed where a formation has
it (scene.bin: its position, facing the party on +z), seen from that formation's battle cameras (the battle scene
behind it), then all round and close up (ORBIT, CLOSE; a plain gradient of the battle's sky behind, their darks
lifted, a few words each on what they show), each view restyled with a Studio look and projected back into the atlas
(paint.Painter: later views repaint only what they see first or better, Text2Tex; the battle cameras' paint only
when seen KEEP_BATTLE times better). What's behind is only context for the AI (the 2D target was painted from a whole
battle frame); only the model's own pixels go back, less the ones the ground hides and those at its outline and
occlusion edges (projectable()). Each model has its own recipe (model_views: Zolom's, the party's): the party stands
where the game's frame shows it (PARTY_POSITIONS) and is painted from close views first; moving parts (Zolom's tongue)
are painted on their own (MOVING); the result gets the game's hues back (colour_lock) and, for Zolom, the target's
colours (target_grade). make() does all of it in one call (the CLI's paint).
The recipes and what each variant showed: docs/models-plan.md, "Painted textures for untextured battle models".

Builds: work/models/battle/atlas/<id>/<version>/ (<MODEL>.png, <MODEL>.P<nn>.uv, info.json, preview/).
install() copies a version's files into the game with a manifest (work/models/battle/atlas/installed.json), and
uninstall() removes exactly those (left alone where something else changed them since).

  python -m ff7bg.models.battleatlas build <id> [--version V] [--weapon N]   the unpainted atlas (the game's own colours)
  python -m ff7bg.models.battleatlas paint <id> [--version V] [--look L] [--strength S] [--words W] [--lift L]
                                               [--grade G] [--weapon N] [--allow-game]
  python -m ff7bg.models.battleatlas check <id> [--version V]
  python -m ff7bg.models.battleatlas install <id> [--version V]
  python -m ff7bg.models.battleatlas uninstall <id>
  python -m ff7bg.models.battleatlas status
"""
import hashlib
import json
import math
import struct
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import atlas as atlasmod
from . import battle, pfile, render
from . import paint as paintmod

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "work" / "models" / "battle" / "atlas"
MANIFEST = WORK / "installed.json"
GAME = battle.GAME_WORKDIR

ATLAS_SIZE = 2048     # Zolom's 53.8 M units^2 of surface at ~0.21 texels per unit: ~1.6x what a 720-line battle
                      # view shows of it (the game is 32-bit: kept modest). The party's: PARTY_ATLAS_SIZE
UV_MAGIC = b"FFUV"
UV_VERSION = 1
ENEMY_YAW = 180.0     # enemies face the party (+z); models face -z (battle.load_model)
PARTY_YAW = 0.0       # ...and the party faces the enemies (-z) as its models do
WEAPON_BONE = -2      # PartFile.bone of a party member's weapon (battle.BattleMesh.part_bones' mark for it)

# The 2D anime target (work/models/battle/target2d/target2d_55.png): the Studio's painterly look at repaint 0.55
# plus these words (FORMAT.md). {what} is the model's own description.
LOOK = "painterly"
STRENGTH = 0.55
WORDS = ("A frame from a hand-drawn 2D anime: {what}, drawn flat in cel animation style, clean shapes, flat colours "
         "with simple painted shadow shapes, thin coloured line art, no 3D shading.")
# Zolom's: v1-v5 also said "red-brown stripes (plates) down its sides", and the AI put brown patches all over its
# sides and coils; the model's own side plates stay reddish without it (v6)
WHAT = {"dd": "Midgar Zolom, a giant green serpent monster with a cobra hood and a pale green segmented belly, rearing "
              "up from its coils, with a tail of dark green spikes"}

# views all round the placed model, after its battle cameras: (label, yaw from its front, pitch above the horizon)
ORBIT = [("front, turned left", 40, 10), ("front, turned right", -40, 10), ("left side", 90, 8),
         ("right side", -90, 8), ("back", 180, 12), ("from above", 0, 55)]
# close views: (label, region (the model's top share by height), yaw, pitch)
CLOSE = [("head, front", "head", 0, 5), ("head, turned left", "head", 50, 8), ("head, turned right", "head", -50, 8)]
# what each view shows, for the AI (paint() adds it to the words): the back view of v1 got a face painted on the back
# of the hood
VIEW_HINTS = {"left side": "Seen from its side.", "right side": "Seen from its side.",
              "back": "Seen from behind: the back of its hood, neck and coils, its face is hidden.",
              "from above": "Seen from above.", "head, turned left": "A close view of its head from the side.",
              "head, turned right": "A close view of its head from the side.",
              "head, front": "A close view of its head."}
# what's behind the views (paint(background=)): "scene" the battle scene, "sky" a plain gradient of the battle
# camera's sky, None black. The battle cameras always have the scene. v1-v2 (2026-11-07) had the scene behind every
# view: seen from round and above, the olive ground was behind the coils, and the AI painted them in its blotches
ORBIT_BACKGROUND = "sky"
VIEW_SIZE = (1280, 960)     # the battle cameras' views (4:3, as the game)
ORBIT_SIZE = (1024, 1024)
VIEW_FOV = 30.0
HEAD_SHARE = 0.25           # "head": the top quarter of the model's height

# The party (2026-11-07): Cloud ("rt", CLOUD) and Barret ("sc", BARRETT2: the Barret the game loads in formation 469,
# FFNx.log "reading p file: battle/BARRETT2.P00".. and BARRETT2_T00-T02; sb, sd, se are his other forms). They're
# small from the battle cameras (Barret ~180 px tall at 720 lines, Cloud ~175), so they're painted from close views all
# round (party_close: the body from 9 sides framed without the weapon, the head from 4, the arms and hands, the
# weapon), the battle cameras last, as an extra (paint(battle_cameras=)). Their words name shapes, not colours (the
# colours come from the model; Zolom's colour words spread patches).
WHAT.update({
    "rt": "Cloud, a young swordsman with spiky blond hair, a sleeveless dark top, one metal pauldron on his left "
          "shoulder only and his right shoulder and arm bare, belts, baggy trousers, gloves and boots, holding a huge "
          "broadsword",
    "sc": "Barret, a big muscular man with dark skin and short dark hair, a sleeveless leather vest with straps, "
          "cargo trousers and heavy boots, a gatling gun fixed to one arm in place of his hand",
})
WHAT.update({k: WHAT["sc"] for k in ("sb", "sd", "se")})
WHAT["si"] = WHAT["rt"]
PARTY_ATLAS_SIZE = 1024  # Cloud's 1.0 M units^2 of body at 0.73 texels per unit, Barret's 2.5 M at 0.48: ~3x what a
                         # 720-line battle view shows. (At 2048 a battle camera sees >50 texels per pixel, which
                         # paint.quality reads as a perfect view: they repainted the close views' work.)
PARTY_ORBIT = [("front, turned left", 35, 8), ("front, turned right", -35, 8), ("front", 0, 4), ("left side", 90, 5),
               ("right side", -90, 5), ("back, turned left", 145, 10), ("back, turned right", -145, 10),
               ("back", 180, 12), ("from above", 0, 50)]
PARTY_HEAD_SHARE = 0.2  # ("band", 0, this): a party member's head and hair, by height (turntable)
# their bones (battle.BattleMesh.part_bones; WEAPON_BONE the weapon), from renders in false colours
# (work/models/battle/atlas/party_scratch/labels_rt.jpg, labels_sc.jpg): the head, the arms with their hands, Barret's
# gun arm (its upper arm, forearm and the gun)
PARTY_BONES = {"rt": dict(head=[2], arms=[5, 6, 7, 10, 11, 12]),
               "sc": dict(head=[2], arms=[5, 6, 7], gun=[10, 11, WEAPON_BONE])}
PARTY_ALIAS = {"sb": "sc", "sd": "sc", "se": "sc", "si": "rt"}


def atlas_size(id):
    """The atlas size build() makes for a model: PARTY_ATLAS_SIZE for the party, ATLAS_SIZE for the rest."""
    return PARTY_ATLAS_SIZE if is_party(id) else ATLAS_SIZE


def party_close(id, weapon=True):
    """A party model's views (CLOSE entries): its body all round (PARTY_ORBIT, framed without the weapon), its head
    from four sides, its arms and hands (Barret: his hand, and his gun arm), and with `weapon` its weapon from both
    sides (Cloud's sword: the gun is Barret's gun arm)."""
    key = PARTY_ALIAS.get(id.lower(), id.lower())
    bones = PARTY_BONES.get(key, {})
    out = [(label, ("body",), yaw, pitch) for label, yaw, pitch in PARTY_ORBIT]
    head = ("bones", bones["head"]) if "head" in bones else ("band", 0.0, PARTY_HEAD_SHARE)
    out += [("head, front", head, 0, 5), ("head, turned left", head, 50, 6), ("head, turned right", head, -50, 6),
            ("head, back", head, 180, 12)]
    if "arms" in bones:
        out += [("arms and hands, front", ("bones", bones["arms"]), -20, 12),
                ("arms and hands, side", ("bones", bones["arms"]), -75, 8)]
    if "gun" in bones:
        out += [("gun arm", ("bones", bones["gun"]), 60, 10), ("gun arm, below", ("bones", bones["gun"]), 20, -25)]
    elif weapon:
        out += [("sword", ("bones", [WEAPON_BONE]), 90, 5), ("sword, other side", ("bones", [WEAPON_BONE]), -90, 5)]
    return out


PARTY_HINTS = {
    "front": "Seen from the front.", "front, turned left": "Seen from the front, a little from the side.",
    "front, turned right": "Seen from the front, a little from the side.",
    "left side": "Seen from his side, in profile.", "right side": "Seen from his side, in profile.",
    "back, turned left": "Seen from behind and a little from the side: his back, the back of his head, his face hidden.",
    "back, turned right": "Seen from behind and a little from the side: his back, the back of his head, his face hidden.",
    "back": "Seen from behind: his back and the back of his head and hair, his face is hidden.",
    "from above": "Seen from above.",
    "head, front": "A close view of his face: two eyes, one nose, one closed mouth.",
    "head, turned left": "A close view of his head from the side, in profile: one eye.",
    "head, turned right": "A close view of his head from the side, in profile: one eye.",
    "head, back": "A close view of the back of his head: hair only, no face.",
    "arms and hands, front": "A close view of his arms and gloved hands.",
    "arms and hands, side": "A close view of his arms and gloved hands.",
    "gun arm": "A close view of his arm and the gun fixed to it.",
    "gun arm, below": "A close view of his arm and the gun fixed to it.",
    "sword": "A close view of his huge broadsword: a wide flat steel blade.",
    "sword, other side": "A close view of his huge broadsword: a wide flat steel blade.",
}

# Zolom's regions by bone (rendered in false colours: work/models/battle/atlas/party_scratch/labels_dd.jpg): 5 the
# hood's top and 4 its wings, 6 the head and face, 8 the upper fangs, 10 the lower jaw, 12 the lower fangs, 13-16 the
# tongue, 3 and 2 the neck, 0-1 and 17-28 the coils, 29-31 the tail and 32-43 its spikes. ("head" by height took the
# tail's spikes in too: they rise above the hood.)
DD_FACE = ("bones", [6, 8, 10, 12])
DD_HEAD = ("bones", [4, 5, 6, 8, 10, 12])
DD_HOOD = ("bones", [4, 5])
DD_NECK = ("bones", [2, 3, 4])
DD_COILS = ("bones", [0, 1] + list(range(17, 29)))
DD_TAIL = ("bones", list(range(29, 44)))
# v8 (2026-11-07): v6's views plus more and closer ones of the sides and back, each saying what it shows (v6's sides
# and back were olive with brown patches, faceted)
DD_ORBIT = [("front, turned left", 40, 10), ("front, turned right", -40, 10), ("left side", 90, 8),
            ("right side", -90, 8), ("back, turned left", 135, 12), ("back, turned right", -135, 12), ("back", 180, 12),
            ("from above", 0, 55), ("from above, behind", 180, 50)]
DD_CLOSE = [("head, front", DD_FACE, 0, 5), ("head, turned left", DD_HEAD, 50, 8), ("head, turned right", DD_HEAD, -50, 8),
            ("back of the hood", DD_HOOD, 180, 10), ("back of the hood, left", DD_HOOD, 125, 15),
            ("back of the hood, right", DD_HOOD, -125, 15), ("neck, back", DD_NECK, 180, 5),
            ("neck, left side", DD_NECK, 90, 5), ("neck, right side", DD_NECK, -90, 5),
            ("coils, left side", DD_COILS, 90, 25), ("coils, right side", DD_COILS, -90, 25),
            ("coils, back", DD_COILS, 180, 30), ("coils, behind left", DD_COILS, 135, 25),
            ("coils, behind right", DD_COILS, -135, 25), ("tail", DD_TAIL, -70, 10), ("tail, behind", DD_TAIL, 150, 10)]
_SCALES = "plain green scales"
_CLOSED = "its mouth closed, no tongue"
DD_HINTS = {
    "battle camera": "Its mouth is closed: two small white fangs at its tip, no tongue.",
    "front, turned left": f"Seen from the front, a little from the side: {_CLOSED}.",
    "front, turned right": f"Seen from the front, a little from the side: {_CLOSED}.",
    "left side": f"Seen from its side: the side of its hood, neck and coils, {_SCALES}.",
    "right side": f"Seen from its side: the side of its hood, neck and coils, {_SCALES}.",
    "back, turned left": f"Seen from behind and a little from the side: the back of its hood, neck and coils, {_SCALES}, "
                         "its face hidden.",
    "back, turned right": f"Seen from behind and a little from the side: the back of its hood, neck and coils, {_SCALES}, "
                          "its face hidden.",
    "back": f"Seen from behind: the back of its hood, neck and coils, {_SCALES}, no face.",
    "from above": "Seen from above.",
    "from above, behind": "Seen from above and behind: the top and back of its hood and its coils, no face.",
    "head, front": f"A close view of its face: two eyes, {_CLOSED}, two small white fangs at the tip.",
    "head, turned left": f"A close view of its head from the side: one eye, {_CLOSED}.",
    "head, turned right": f"A close view of its head from the side: one eye, {_CLOSED}.",
    "back of the hood": f"A close view of the back of its hood: {_SCALES}, no face, no eyes.",
    "back of the hood, left": f"A close view of the back of its hood, from the side: {_SCALES}, no face, no eyes.",
    "back of the hood, right": f"A close view of the back of its hood, from the side: {_SCALES}, no face, no eyes.",
    "neck, back": f"A close view of the back of its neck: {_SCALES}, no face.",
    "neck, left side": "A close view of the side of its neck: scales and the edge of its belly plates.",
    "neck, right side": "A close view of the side of its neck: scales and the edge of its belly plates.",
    "coils, left side": f"A close view of its coiled body: thick round coils of {_SCALES}.",
    "coils, right side": f"A close view of its coiled body: thick round coils of {_SCALES}.",
    "coils, back": f"A close view of its coiled body from behind: thick round coils of {_SCALES}.",
    "coils, behind left": f"A close view of its coiled body: thick round coils of {_SCALES}.",
    "coils, behind right": f"A close view of its coiled body: thick round coils of {_SCALES}.",
    "tail": "A close view of its tail and the long spikes on it.",
    "tail, behind": "A close view of its tail and the long spikes on it, from behind.",
}
# Separately animated parts (2026-11-07, user: two tongues in game): Zolom's tongue (bones 13-16, ENEMY081.P13-P16)
# flicks out in animations 1, 10 and 11, and its jaw (P10) opens in 4; in the pose painted from (animation 0's frame
# 0) the tongue is inside the head and the mouth is shut. v6's AI painted a tongue down its face and fangs anyway (the
# fangs, P08, are pinkish), so the game showed two. paint() leaves a moving part out of every view of the model
# (hide), then paints it on its own from a pose that shows it, projecting only onto that part (its views see the
# head as painted). Per model: [dict(name, bones, animation, views [(label, yaw, pitch)], hint)].
MOVING = {"dd": [dict(name="tongue", bones=[13, 14, 15, 16], animation=10,
                      views=[("tongue, front", 0, 0), ("tongue, side", 75, 0)],
                      hint="A close view of its long thin forked snake tongue flicking out of its closed mouth.")]}
FANG = (232, 226, 210)
DD_COLOURS = {8: FANG, 12: FANG}  # the fangs (upper P08, lower P12) as the AI is to see them: white, not pink
MODEL_VIEWS = {
    # v10: the round views show the model matched to the battle camera's painting (match_input; v6-v9's lift left
    # them near black and the AI painted them murky with brown patches); the fangs shown white (v6-v9 got a tongue)
    "dd": dict(orbit=DD_ORBIT, close=DD_CLOSE, hints=DD_HINTS, match=True, lift_share=None, colours=DD_COLOURS,
               lock="hue"),
    # the party (2026-11-07): Cloud v4, the first view his front (painted from his back first, v3, his face came out
    # less clean); Barret v4, a later view repaints only what it sees 2x better (the default 1.3, v3, left his vest and
    # trousers patchier where views met)
    "rt": dict(first="front, turned right"),
    "sc": dict(better=2.0),
}


def model_views(id):
    """paint()'s defaults for a model: dict(orbit, close, hints, strengths ({view label: repaint}), battle_cameras
    ("first": painted from first and kept, enemies; "last": an extra after the close views, the party), lift_share,
    moving (MOVING), match, colours, first ("battle": the body view nearest the resting battle camera painted first,
    the party: it's the side the player sees, and the first view is painted whole, the rest around it), whole, and
    make()'s lock (colour_lock: "hue" Zolom, "hsv" the party)). MODEL_VIEWS overrides; enemies otherwise get ORBIT,
    CLOSE, VIEW_HINTS, the party PARTY_*."""
    id = id.lower()
    if is_party(id):
        out = dict(orbit=[], close=party_close(id), hints=PARTY_HINTS, strengths={}, battle_cameras="last", first="battle",
                   lock="hsv",
                   lift_share=None, moving=[])
    else:
        out = dict(orbit=ORBIT, close=CLOSE, hints=VIEW_HINTS, strengths={}, battle_cameras="first",
                   lift_share=LIFT_SHARE, moving=MOVING.get(id, []))
    out.update(MODEL_VIEWS.get(PARTY_ALIAS.get(id, id) if is_party(id) else id, {}))
    return out


# --- parts and their files --------------------------------------------------------------------------------------------

@dataclass
class PartFile:
    file: str         # battle.lgp's file ("ddam")
    game: str         # the name the game loads it by ("ENEMY081.P00")
    bone: int
    numverts: int     # the .p header's counts (what FFNx-3d checks a .uv file against)
    numpolys: int


def game_part(id, bone):
    """The game's name for a model's part ("dd", 5 -> "ENEMY081.P05"): its bone index (see the module notes)."""
    g = battle.game_name(id)
    if not g:
        raise ValueError(f"no game name known for {id}")
    return f"{g}.P{bone:02d}"


def game_weapon(id, weapon):
    """The game's name for a party member's weapon part ("rt", 0 -> "CLOUD.W00"): the exe loads the weapon a member
    holds as <MODEL>.W<nn>. Seen in FFNx.log (2026-11-07): "reading p file: battle/CLOUD.W00" and BARRETT2.W00 with
    their first weapons (the Buster Sword, the Gatling Gun) held, i.e. battle.lgp's rtck and scck (weapon 0, role
    "ck"); that nn is the weapon's index for the others is assumed, not seen."""
    g = battle.game_name(id)
    if not g:
        raise ValueError(f"no game name known for {id}")
    return f"{g}.W{weapon:02d}"


def part_files(id, battle_lgp=battle.BATTLE_LGP, weapon=None):
    """The model's parts as the game loads them (bones with a part, in bone order), then with `weapon` (a party
    member's weapon index, None for none: the default) that weapon's part, named as the game loads it (game_weapon;
    FFNx-3d takes a .uv for any .p file under battle/, and the model's atlas by the name before the dot)."""
    id = id.lower()
    hd = battle.header(id, battle_lgp)
    if hd.kind == 1:
        raise ValueError(f"{id} is a battle scene, not a model")
    out = []
    for i, b in enumerate(hd.bones):
        name = id + battle.part_role(i)
        if not b.has_part or not battle.has_file(name, battle_lgp):
            continue
        data = battle.read_file(name, battle_lgp)
        h = struct.unpack_from("<16I", data, 0)
        out.append(PartFile(name, game_part(id, i), i, h[3], h[9]))
    if weapon is not None and weapon >= 0:
        name = id + battle.weapon_role(weapon)
        if not battle.has_file(name, battle_lgp):
            raise ValueError(f"{id} has no weapon {weapon} ({name})")
        h = struct.unpack_from("<16I", battle.read_file(name, battle_lgp), 0)
        out.append(PartFile(name, game_weapon(id, weapon), WEAPON_BONE, h[3], h[9]))
    return out


def is_party(id, battle_lgp=battle.BATTLE_LGP):
    """Is the id a party member's battle model (its header's kind 2: Cloud "rt", Barret "sb".."se")?"""
    try:
        return battle.header(id.lower(), battle_lgp).kind == 2
    except (KeyError, ValueError):
        return False


def model_yaw(id):
    """Which way a model is turned in battle (placed()'s yaw): enemies face the party, the party the enemies."""
    return PARTY_YAW if is_party(id) else ENEMY_YAW


def poly_index(part):
    """Per triangle pfile.Part.triangles() gives (group order), its polygon's index in the .p file's polygon table."""
    out = []
    for g in part.groups:
        hi = min(g.offpoly + g.numpoly, len(part.polys))
        if hi > g.offpoly:
            out.append(np.arange(g.offpoly, hi))
    return np.concatenate(out).astype(np.int64) if out else np.zeros(0, np.int64)


# --- the atlas --------------------------------------------------------------------------------------------------------

@dataclass
class BattleAtlas:
    id: str
    name: str             # the game's model name (ENEMY081)
    atlas: object         # atlas.Atlas built on the model in part space (its image: the game's own colours)
    posed: object         # the posed model (battle.BattleMesh) with the atlas UVs: atlas.mesh's triangles, posed
    original: object      # the posed model as the game draws it (vertex colours, its own textures)
    parts: list           # [PartFile] per mesh part
    poly: np.ndarray      # (t,) per triangle its polygon index in its part's .p file

    @property
    def size(self):
        return self.atlas.size

    def uv(self, part):
        """(numpolys, 3, 2) float32 the atlas u, v of a part's polygons (.p order; polygons no group draws: 0)."""
        pf = self.parts[part]
        out = np.zeros((pf.numpolys, 3, 2), np.float32)
        sel = np.nonzero(self.posed.part == part)[0]
        out[self.poly[sel]] = self.posed.uv[sel]
        return out


def part_space(m):
    """A posed BattleMesh with every triangle back in its part's own coordinates (part_matrices inverted)."""
    pos = m.pos.astype(np.float64).copy()
    for pi, M in enumerate(m.part_matrices):
        sel = m.part == pi
        Mi = np.linalg.inv(np.asarray(M, np.float64))
        p = pos[sel].reshape(-1, 3)
        pos[sel] = (p @ Mi[:3, :3].T + Mi[:3, 3]).reshape(-1, 3, 3)
    return replace(m, pos=pos.astype(np.float32))


PARTY_WEAPON = 0  # build(weapon="auto"): the party's first weapon (Cloud's Buster Sword, Barret's Gatling Gun)


def build(id, size=None, animation=0, battle_lgp=battle.BATTLE_LGP, weapon="auto"):
    """BattleAtlas of a battle model ("dd"): its parts in part space charted and packed into one size x size atlas
    (None: atlas_size(id), ATLAS_SIZE but for the party), filled with the game's own colours; posed at frame 0 of
    `animation` for views. weapon: a party member's weapon (its index: 0 the first, Cloud's Buster Sword) in the atlas
    too, held as that animation holds it ("auto", the default: PARTY_WEAPON for the party, none for the rest; None or
    -1: the body alone)."""
    id = id.lower()
    size = atlas_size(id) if size is None else size
    if weapon == "auto":
        weapon = PARTY_WEAPON if is_party(id) else None
    w = -1 if weapon is None else int(weapon)
    m = battle.load_model(id, animation, weapon=w, battle_lgp=battle_lgp)
    if w >= 0 and m.weapon != w:
        raise ValueError(f"{id}: weapon {w} isn't drawn in animation {animation}")
    files = {pf.file: pf for pf in part_files(id, battle_lgp, None if w < 0 else w)}
    parts, poly = [], []
    for pi, fname in enumerate(m.parts):
        pf = files.get(fname)
        if pf is None:
            raise ValueError(f"{fname} isn't one of {id}'s parts")
        idx = poly_index(pfile.read(battle.read_file(fname, battle_lgp)))
        n = int((m.part == pi).sum())
        if len(idx) != n:
            raise ValueError(f"{fname}: {n} triangles, {len(idx)} polygon indices")
        if len(np.unique(idx)) != len(idx):
            raise ValueError(f"{fname}: a polygon is in two groups")
        parts.append(pf)
        poly.append(idx)
    poly = np.concatenate(poly)
    a = atlasmod.build(part_space(m), size=size, max_size=size, name=battle.game_name(id))
    posed = replace(a.mesh, pos=m.pos)
    return BattleAtlas(id, battle.game_name(id), a, posed, m, parts, poly)


def uv_bytes(ba, part):
    pf = ba.parts[part]
    uv = ba.uv(part)
    return UV_MAGIC + struct.pack("<3I", UV_VERSION, pf.numverts, pf.numpolys) + uv.astype("<f4").tobytes()


def read_uv(data):
    """(numverts, numpolys, uv (numpolys, 3, 2)) of a .uv file's bytes."""
    if data[:4] != UV_MAGIC:
        raise ValueError("not a .uv file")
    ver, nv, npoly = struct.unpack_from("<3I", data, 4)
    if ver != UV_VERSION:
        raise ValueError(f".uv version {ver}")
    if len(data) != 16 + npoly * 24:
        raise ValueError(".uv size doesn't match its polygon count")
    return nv, npoly, np.frombuffer(data, "<f4", offset=16).reshape(npoly, 3, 2)


def with_image(ba, image, mesh=None):
    """The posed atlas model (or `mesh`, the same triangles placed elsewhere) showing another atlas image."""
    m = ba.posed if mesh is None else mesh
    ref = battle.meshmod.TexRef(m.textures[ba.atlas.index].key, atlasmod.as_tex(np.ascontiguousarray(image)), 0)
    textures = list(m.textures)
    textures[ba.atlas.index] = ref
    return replace(m, textures=textures)


def atlas_only(m, index):
    """The mesh with the atlas as its only texture (what gets painted: paint.Painter paints every texture it has)."""
    return replace(m, textures=[m.textures[index]], tex=np.where(m.tex == index, 0, -1).astype(np.int32))


# --- placing a model in a battle --------------------------------------------------------------------------------------

def placed(m, position, yaw=ENEMY_YAW):
    """A model's mesh moved into battle space: turned `yaw` degrees round the vertical, at `position` (scene.bin's
    x, y, z for the formation's enemy)."""
    R = battle.rotation(0, yaw, 0)
    p = m.pos.reshape(-1, 3).astype(np.float64) @ R.T + np.asarray(position, np.float64)
    return replace(m, pos=p.reshape(m.pos.shape).astype(np.float32))


def formations(id, scene_bin=battle.SCENE_BIN):
    """[(formation, its enemy position for this model)] of every formation the model fights in (the first of it in
    each), from scene.bin."""
    from . import scenebin
    k = battle.id_index(id)
    out = []
    for f in scenebin.load(scene_bin).formations:
        pos = next((p for e, p in f.enemies if e == k), None)
        if pos is not None:
            out.append((f, pos))
    return out


# Where the party stands, per formation and model id (battle space, on the ground): scene.bin doesn't say (the game
# places the party itself). Formation 469 (Midgar Zolom; Cloud and Barret, the target's frame) fitted to the game's
# frame (target2d/ingame_469_oo_light.png): each one's soles cast from the picture onto the ground (y = 0) through
# the formation's resting camera, then the models rendered there and laid over the picture. Cloud's outline lands on
# the game's within a few pixels; Barret's soles and middle do too, but the game shows him a little bigger at the
# shoulders (his idle pose: only frame 0 is decoded, with his arms lower). From this camera both are seen from
# behind; Barret's head is hidden behind his shoulders in the game's frame and ours alike.
PARTY_POSITIONS = {469: {"rt": (-1255.0, 0.0, 915.0), "sc": (924.0, 0.0, 590.0)}}
PARTY_FORMATION = 469


def party_position(id, formation=PARTY_FORMATION):
    """Where a party model stands in a formation (PARTY_POSITIONS): Barret's forms (sb-se) where Barret stands, Cloud's
    fine model (si) where Cloud does, anyone else between them."""
    id = id.lower()
    places = PARTY_POSITIONS.get(formation) or PARTY_POSITIONS[PARTY_FORMATION]
    alias = {"sb": "sc", "sd": "sc", "se": "sc", "si": "rt"}
    p = places.get(id) or places.get(alias.get(id, ""))
    if p is None:
        p = tuple(np.mean(list(places.values()), 0))
    return tuple(float(x) for x in p)


def battle_views(id, formation=None, size=VIEW_SIZE, scene_bin=battle.SCENE_BIN):
    """[(label, BattleCamera, position, scene id)]: a formation's cameras (slot 0, the one battles rest on, first;
    slots 1-2 when they're real views, not scene.bin's placeholder), with where it puts the model. formation None:
    the first formation the model is in whose resting camera isn't the placeholder. A party model: formation
    (PARTY_FORMATION when None) with the model where party_position() has it."""
    if is_party(id):
        from . import scenebin
        fid = PARTY_FORMATION if formation is None else formation
        f = next((x for x in scenebin.load(scene_bin).formations if x.id == fid), None)
        found = [] if f is None else [(f, party_position(id, fid))]
    else:
        found = formations(id, scene_bin)
    if formation is not None:
        found = [x for x in found if x[0].id == formation]
    else:
        real = [x for x in found if x[0].cameras and
                (x[0].cameras[0].eye, x[0].cameras[0].target) != battle.PLACEHOLDER_CAMERA]
        found = real[:1] or found[:1]
    out = []
    for f, pos in found:
        sid = battle.location_id(f.location)
        for slot, c in enumerate(f.cameras):
            if (c.eye, c.target) == battle.PLACEHOLDER_CAMERA:
                continue
            cam = battle.game_camera(c.eye, c.target, *size, uses=[(f.id, slot)])
            out.append((f"formation {f.id} camera {slot}", cam, pos, sid))
    return out


def _fit(points, target, yaw, pitch, w, h, fov=VIEW_FOV, margin=0.9, facing=ENEMY_YAW):
    """(camera eye, distance) at (yaw, pitch) from the model's front round `target` that sees every point within
    `margin` of a w x h frame (studio/plugins/models.py's fit_distance; the model's front faces `facing`)."""
    a, p = math.radians(yaw + facing) + math.pi, math.radians(pitch)
    back = np.array([math.sin(a) * math.cos(p), -math.sin(p), math.cos(a) * math.cos(p)])  # target -> eye
    f = -back
    r = np.cross(f, [0.0, -1.0, 0.0])
    r /= max(np.linalg.norm(r), 1e-9)
    u = np.cross(f, r)
    rel = points - target
    ty = math.tan(math.radians(fov) / 2) * margin
    tx = ty * w / h
    need = float((np.maximum(np.abs(rel @ r) / tx, np.abs(rel @ u) / ty) - rel @ f).max())
    return np.asarray(target, float) + back * need, need


def region_points(m, region, pts=None):
    """The points (n, 3) of a placed model in a CLOSE region:
    - "head": its top HEAD_SHARE by height;
    - ("band", a, b): between shares a and b of its height from the top (y down: 0 the top, 1 the soles);
    - ("box", (x0, x1), (y0, y1), (z0, z1)): inside those shares of its bounding box along battle x, y (from the top)
      and z (from -z);
    - ("bones", [bone, ...]): its parts on those bones (battle.BattleMesh.part_bones; WEAPON_BONE the weapon);
    - ("body",): all of it but a party member's weapon."""
    if pts is None:
        pts = np.unique(m.pos.reshape(-1, 3).astype(np.float64), axis=0)
    lo, hi = pts.min(0), pts.max(0)
    ext = np.maximum(hi - lo, 1e-9)
    if region == "head":
        return pts[pts[:, 1] <= lo[1] + ext[1] * HEAD_SHARE]
    kind = region[0] if isinstance(region, (tuple, list)) and region else None
    if kind == "band":
        s = (pts[:, 1] - lo[1]) / ext[1]
        return pts[(s >= region[1]) & (s <= region[2])]
    if kind == "box":
        s = (pts - lo) / ext
        keep = np.ones(len(s), bool)
        for a, (r0, r1) in enumerate(region[1:4]):
            keep &= (s[:, a] >= r0) & (s[:, a] <= r1)
        return pts[keep]
    if kind in ("bones", "body"):
        bones = getattr(m, "part_bones", None)
        if not bones:
            raise ValueError(f"a {kind} region needs a battle model (part_bones)")
        want = (lambda b: b != WEAPON_BONE) if kind == "body" else (lambda b: b in set(region[1]))
        parts = [i for i, b in enumerate(bones) if want(b)]
        return m.pos[np.isin(m.part, parts)].reshape(-1, 3).astype(np.float64)
    raise ValueError(f"unknown region {region}")


CLOSE_MARGIN = 0.85  # a close view frames its region within this share of the picture


def orbit_views(m, orbit=ORBIT, close=CLOSE, size=ORBIT_SIZE, facing=ENEMY_YAW):
    """[(label, render.Camera)] round a placed model (m: its mesh in battle space, facing `facing`): ORBIT round
    all of it, CLOSE round a region of it (region_points: the head, its top HEAD_SHARE; a band of its height; a box;
    its parts on some bones)."""
    pts = np.unique(m.pos.reshape(-1, 3).astype(np.float64), axis=0)
    out = []
    w, h = size
    lo, hi = pts.min(0), pts.max(0)
    for label, yaw, pitch in orbit:
        t = (lo + hi) / 2
        eye, d = _fit(pts, t, yaw, pitch, w, h, facing=facing)
        out.append((label, render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV,
                                         width=w, height=h, near=max(d * 0.01, 1.0))))
    for label, region, yaw, pitch in close:
        sel = region_points(m, region, pts)
        if not len(sel):
            continue
        t = (sel.min(0) + sel.max(0)) / 2
        eye, d = _fit(sel, t, yaw, pitch, w, h, margin=CLOSE_MARGIN, facing=facing)
        out.append((label, render.Camera(eye=eye, target=t, up=np.array([0.0, -1.0, 0.0]), fov_y=VIEW_FOV,
                                         width=w, height=h, near=max(d * 0.01, 1.0))))
    return out


_scenes = {}


def scene_behind(sid, cam):
    """(RGB, depth) of a battle scene from a camera (as the game draws it: its vertex colours times its textures,
    culled): the context a model's view is painted in."""
    if sid not in _scenes:
        _scenes[sid] = battle.load_scene(sid)
    s = _scenes[sid]
    r = render.rasterize(s, cam)
    return render.shade(s, r, modulate=True), r.depth


def sky_backdrop(sid, cam, size, top=0.5):
    """(RGB, depth inf) a plain vertical gradient, w x h = size, of the sky a battle camera sees above the horizon
    (the scene's rows from the top to `top` of the picture, each row averaged): behind the views from round and above,
    where the scene's ground would be behind the model."""
    img, _ = scene_behind(sid, cam)
    rows = img[:max(2, int(img.shape[0] * top))].astype(np.float32).mean(1)
    rows = cv2.GaussianBlur(rows[:, None, :], (1, 0), sigmaX=0.1, sigmaY=len(rows) / 6)[:, 0]  # no bands
    w, h = size
    y = np.linspace(0, len(rows) - 1, h)
    col = np.stack([np.interp(y, np.arange(len(rows)), rows[:, c]) for c in range(3)], -1)
    out = np.broadcast_to(col[:, None, :], (h, w, 3)).astype(np.uint8).copy()
    return out, np.full((h, w), np.inf, np.float32)


def lift(img, vis, floor, k=0.7):
    """The model's pixels (vis) of a view with their darks raised toward `floor` (mean RGB level), hue kept: k of the
    way, only brighter. For the views from round and behind: the model's vertex colours are lit from its front, and
    its back near black-green (paint(lift=))."""
    out = img.copy()
    f = img[vis].astype(np.float32)
    l = f.mean(-1, keepdims=True)
    want = np.where(l < floor, l + (floor - l) * k, l)
    out[vis] = np.clip(f * want / np.maximum(l, 1.0), 0, 255).astype(np.uint8)
    return out


MATCH_CHROMA = 1.0  # match_input(): the most a view's colour differences (Lab a, b spread) are scaled up


def lab_stats(rgb):
    """(mean, spread) per Lab channel of RGB pixels (n, 3)."""
    lab = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    lab = lab.astype(np.float32)
    return lab.mean(0), lab.std(0) + 1e-3


def match_input(img, vis, ref, chroma=MATCH_CHROMA):
    """The model's pixels (vis) of a view as the AI is to see them, matched to the first view's painting (ref: its
    model pixels' lab_stats): lightness to its mean and spread, colour moved to its mean with the view's own colour
    differences kept (scaled up at most `chroma` times). Zolom's back and sides are near black-green (its vertex
    colours are lit from the front), and the AI painted them murky olive with brown patches (v6, v8: lift() and more
    views didn't help; a full Lab match scaled their faint colour noise up 4-7 times into patches)."""
    if not vis.any():
        return img
    out = img.copy()
    lab = cv2.cvtColor(np.ascontiguousarray(img[vis]).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    lab = lab.astype(np.float32)
    m, sd = lab.mean(0), lab.std(0) + 1e-3
    rm, rsd = ref
    gain = np.array([rsd[0] / sd[0], *np.minimum(rsd[1:] / sd[1:], chroma)], np.float32)
    lab = (lab - m) * gain + rm
    rgb = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8).reshape(-1, 1, 3), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    out[vis] = rgb
    return out


def recolour(img, mesh, r, colours):
    """The pixels of the parts on some bones ({bone: RGB}) shown in that colour (their shading kept, a little) in the
    picture the AI paints from. Zolom's fangs (P08, P12) are pinkish in the game: the AI drew them as a long tongue
    hanging from its closed mouth (v6, v8), which the game then showed beside the real one."""
    if not colours:
        return img
    hit = r.tri >= 0
    bones = np.asarray(mesh.part_bones)[mesh.part[np.maximum(r.tri, 0)]]
    out = img.copy()
    for bone, rgb in colours.items():
        sel = hit & (bones == bone)
        if not sel.any():
            continue
        l = img[sel].astype(np.float32).mean(-1)
        k = np.clip(l / max(float(l.mean()), 1.0), 0.75, 1.15)[:, None]
        out[sel] = np.clip(np.asarray(rgb, np.float32) * k, 0, 255).astype(np.uint8)
    return out


def composite(fg, r, bg):
    """The model's pixels (fg, its raster r) over a background (RGB, depth) where it's nearer."""
    if bg is None:
        return fg
    img, depth = bg
    out = img.copy()
    hit = (r.tri >= 0) & (r.depth < depth)
    out[hit] = fg[hit]
    return out


# --- checks -----------------------------------------------------------------------------------------------------------

def identity(ba, cam, position=(0, 0, 0), yaw=ENEMY_YAW, image=None):
    """The atlas against the game's own colours from a camera: (original render, atlas render (bilinear, as FFNx
    filters), stats). Both are the model alone; stats over the pixels it covers: mean, p99 and max of the largest
    channel difference, and the share within 3 and 8 levels."""
    orig = placed(ba.original, position, yaw)
    am = placed(ba.posed, position, yaw)
    r = render.rasterize(orig, cam)
    a = render.rasterize(am, cam)
    o_img = render.shade(orig, r, modulate=True)
    imgs = [None] * len(am.textures)
    imgs[ba.atlas.index] = ba.atlas.image if image is None else image
    a_img = paintmod.shade(am, a, [im if im is not None else t.tex.rgba(t.palette) for im, t in zip(imgs, am.textures)])
    hit = (r.tri >= 0) & (a.tri >= 0)
    d = np.abs(o_img.astype(int) - a_img.astype(int)).max(-1)[hit]
    stats = {"pixels": int(hit.sum()), "mean": round(float(d.mean()), 3), "p99": float(np.percentile(d, 99)),
             "max": int(d.max()), "within3": round(float((d <= 3).mean()), 4), "within8": round(float((d <= 8).mean()), 4)}
    return o_img, a_img, stats


def check(ba, files):
    """Problems with a build's files ({name: path}) against the model's .p files: [] when all is well."""
    probs = []
    png = files.get(f"{ba.name}.png")
    if png is None or not Path(png).exists():
        probs.append(f"{ba.name}.png missing")
    else:
        im = Image.open(png)
        if im.size[0] != im.size[1] or im.size[0] > 4096 or im.mode != "RGBA":
            probs.append(f"{ba.name}.png is {im.size} {im.mode}")
    for i, pf in enumerate(ba.parts):
        p = files.get(f"{pf.game}.uv")
        if p is None or not Path(p).exists():
            probs.append(f"{pf.game}.uv missing")
            continue
        nv, npoly, uv = read_uv(Path(p).read_bytes())
        if (nv, npoly) != (pf.numverts, pf.numpolys):
            probs.append(f"{pf.game}.uv counts {nv}/{npoly}, the .p file's {pf.numverts}/{pf.numpolys}")
        if not np.isfinite(uv).all() or uv.min() < 0 or uv.max() > 1:
            probs.append(f"{pf.game}.uv has u, v outside 0..1")
        if not np.allclose(uv, ba.uv(i)):
            probs.append(f"{pf.game}.uv isn't this atlas' layout")
    return probs


# --- painting ---------------------------------------------------------------------------------------------------------

def _model_words(id, words):
    what = WHAT.get(id.lower())
    if what is None:
        from . import scenebin
        names = scenebin.load(battle.SCENE_BIN).names
        what = names.get(battle.id_index(id), id)
    return words.replace("{what}", what)


LIFT_SHARE = 1.0   # paint(): the views from round and behind show the model's darks raised toward its median level
                   # in the battle view (its vertex colours are lit from the front; its back is near black)
KEEP_BATTLE = 2.0  # paint(): what the battle cameras painted, other views need to see this much better to repaint
MIN_NEW = 0.01   # a later view runs when it's the first to see (or sees better) this share of the model it shows
EDGE_PX = 3.0    # a view's pixels this close to the model's outline or an occlusion edge in it count for less...
EDGE_MIN = 0.34  # ...and nothing under this weight (paint.Painter.add marks every texel it takes as seen)
DEPTH_JUMP = 0.01  # an occlusion edge: neighbouring pixels' depths differ by more than this share


def projectable(r, bg_depth=None):
    """(visible, weight) for a view's raster of the model: the pixels where the model shows (in front of the scene
    behind it: the ground hides the coils' undersides, and the AI paints ground there), and per pixel a weight rising
    from 0 at the model's outline and at occlusion edges (a near part over a far one) to 1 EDGE_PX inside. Projected
    as they were, the views' edge pixels put the background's colour, and the near part's colour, on the far side."""
    vis = r.tri >= 0
    if bg_depth is not None:
        vis &= r.depth < bg_depth
    z = np.where(vis, r.depth, np.inf).astype(np.float64)
    cut = ~vis
    for axis in (0, 1):
        a = np.swapaxes(z, 0, axis)
        with np.errstate(invalid="ignore"):
            d = np.abs(a[1:] - a[:-1])
        jump = ~(d <= DEPTH_JUMP * np.minimum(a[1:], a[:-1]))  # inf - inf (both off the model) is nan: a jump too
        c = np.swapaxes(cut, 0, axis)
        c[1:] |= jump
        c[:-1] |= jump
    dist = cv2.distanceTransform((~cut).astype(np.uint8), cv2.DIST_L2, 3)
    return vis, np.clip(dist / EDGE_PX, 0, 1).astype(np.float32) * vis


class _Painter(paintmod.Painter):
    """paint.Painter with its own BETTER (self.better) for view_mask (its module's is shared)."""
    better = paintmod.BETTER

    def view_mask(self, cam, r):
        q, ti, uv = paintmod.quality(self.mesh, r, cam, self.sizes)
        mask = np.zeros(q.shape, bool)
        for k in np.unique(ti[ti >= 0]):
            if k in self.fixed:
                continue
            sel = ti == k
            h, w = self.best[k].shape
            x = (uv[sel][:, 0] * w).astype(int).clip(0, w - 1)
            y = (uv[sel][:, 1] * h).astype(int).clip(0, h - 1)
            prev = self.best[k][y, x]
            mask[sel] = (q[sel] > 0.05) & ((prev <= 0) | (q[sel] > prev * self.better))
        m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        return (m > 0) & (r.tri >= 0)


def _stop_check(cancelled):
    """comfy's cancelled(): true when `cancelled` returns true or raises InterruptedError (the Studio's does)."""
    def check():
        try:
            return bool(cancelled())
        except InterruptedError:
            return True
    return check


def _styled(R, stop, orig, cur, mask):
    """R(orig, cur, mask), a cancel (ComfyUI interrupted) raised as InterruptedError."""
    try:
        return R(orig, cur, mask)
    except Exception as e:
        if stop is not None and stop():
            raise InterruptedError("cancelled") from e
        raise


def hidden(m, bones):
    """The mesh with its parts on these bones left out of every view: their triangles collapsed onto their centres (the
    rasterizer skips them), the triangles' order (and so the atlas) kept."""
    if not bones:
        return m
    parts = [i for i, b in enumerate(m.part_bones) if b in set(bones)]
    sel = np.isin(m.part, parts)
    if not sel.any():
        return m
    pos = m.pos.copy()
    pos[sel] = pos[sel].mean(1, keepdims=True)
    return replace(m, pos=pos)


def posed_as(ba, animation):
    """The atlas model (ba.posed) at frame 0 of another of its animations: the same parts, so the same triangles."""
    m = battle.load_model(ba.id, animation, weapon=ba.original.weapon)
    if m.pos.shape != ba.posed.pos.shape:
        raise ValueError(f"{ba.id}: animation {animation} poses other parts")
    return replace(ba.posed, pos=m.pos)


def paint(ba, look=LOOK, strength=STRENGTH, words=WORDS, formation=None, orbit=None, close=None,
          url="http://127.0.0.1:8000", describe=None, background=ORBIT_BACKGROUND, keep_battle=KEEP_BATTLE,
          hints=None, lift_share="auto", progress=None, log_dir=None, battle_cameras=None, strengths=None,
          hide=None, moving=None, match=None, colours=None, extra_words="", on_progress=None, cancelled=None,
          first=None, better=None, whole=None):
    """The atlas painted from restyled views (the Models projected method): the battle cameras of a formation, and
    views all round (orbit) and close up (close), the model placed where that formation has it. Behind it (context for
    the AI only): the battle scene for the battle cameras, and for the rest `background` (ORBIT_BACKGROUND: "scene",
    "sky" or None for black). Each view after the first repaints only what it's the first to see, or sees BETTER than
    before (paint.Painter.view_mask), around the paint so far.

    None (the defaults) takes the model's own (model_views(): Zolom's in MODEL_VIEWS, the party's PARTY_*):
    - orbit, close: the views (ORBIT, CLOSE: (label, yaw, pitch) and (label, region_points region, yaw, pitch));
    - hints: per view label, words added for that view ("battle camera": for the battle cameras' views);
    - battle_cameras: "first" (enemies): painted first, and what they painted is kept unless a view sees it
      keep_battle x BETTER (the player sees the model from there); "last" (the party, small from there): an extra after
      the close views; "none";
    - lift_share ("auto": the model's): the views after the first, battle cameras aside, show the model's darks raised
      toward this share of its median level in the first view (lift()), None not;
    - strengths: per view label, its repaint instead of `strength` (later views only);
    - moving: separately animated parts (MOVING), each painted on its own after the rest from a pose that shows it,
      projected only onto that part; hide: bones left out of every other view (default: the moving parts');
    - match: the views after the first, battle cameras aside, show the model matched to the first view's painting
      (match_input: its lightness and colour), instead of lift();
    - colours: {bone: RGB} parts shown to the AI in that colour (recolour(): Zolom's fangs);
    - first: "battle" (the party): of the round views, the body view nearest the resting battle camera goes first (it's
      painted whole, the rest around it); a view label: that one; False: as listed;
    - better: a later view repaints what it sees this many times better than before (paint.BETTER, 1.3);
    - whole: the round views are restyled whole, as the first is (with the paint so far nowhere in the picture), and
      only what they see first or better goes back; False: they repaint only that, around the paint so far.
    extra_words: the user's own words, after the model's and before each view's. on_progress: ComfyUI's progress
    within a view (restyle.Restyler's). cancelled: a callable, true (or raising InterruptedError) to stop: checked
    before each view and while ComfyUI paints one (it's interrupted); raises InterruptedError.

    Returns (atlas RGBA at the atlas' size, [per view dict(label, kind, new share, image, orig)], the
    restyle.Restyler)."""
    from . import restyle
    mv = model_views(ba.id)
    orbit = mv["orbit"] if orbit is None else orbit
    close = mv["close"] if close is None else close
    hints = (mv["hints"] if hints is None else hints) or {}
    strengths = (mv["strengths"] if strengths is None else strengths) or {}
    order = mv["battle_cameras"] if battle_cameras is None else battle_cameras
    moving = mv["moving"] if moving is None else moving
    hide = sorted({b for part in moving for b in part["bones"]}) if hide is None else hide
    if isinstance(lift_share, str):
        lift_share = mv["lift_share"]
    match = mv.get("match", False) if match is None else match
    first = mv.get("first") if first is None else first
    whole = mv.get("whole", False) if whole is None else whole
    colours = (mv.get("colours", {}) if colours is None else colours) or {}
    stop = None if cancelled is None else _stop_check(cancelled)
    yaw = model_yaw(ba.id)
    bviews = battle_views(ba.id, formation)
    if not bviews:
        raise ValueError(f"{ba.id} is in no formation with a battle camera")
    pos, sid = bviews[0][2], bviews[0][3]
    pm_battle = hidden(atlas_only(placed(ba.posed, pos, yaw), ba.atlas.index), hide)
    bv = [] if order == "none" else [(label, cam, "battle", None) for label, cam, _, _ in bviews]
    rv = [(label, cam, "round", None) for label, cam in orbit_views(pm_battle, orbit, close, facing=yaw)]
    if isinstance(first, str) and first != "battle":
        k = next((j for j, v in enumerate(rv) if v[0] == first), None)
        if k is not None:
            rv = [rv[k]] + rv[:k] + rv[k + 1:]
    elif first == "battle" and rv and bviews:  # the round view nearest the resting battle camera's first
        c0 = bviews[0][1]
        ldir = np.asarray(c0.eye, float) - np.asarray(c0.target, float)
        ldir /= np.linalg.norm(ldir)

        def near(v):
            d = np.asarray(v[1].eye, float) - np.asarray(v[1].target, float)
            return -float(d @ ldir / np.linalg.norm(d))
        body = [v for v in rv if (dict((c[0], c[1]) for c in close).get(v[0]) == ("body",))]
        if body:
            k = rv.index(min(body, key=near))
            rv = [rv[k]] + rv[:k] + rv[k + 1:]
    views = bv + rv if order == "first" else rv + bv
    for part in moving:  # each moving part from its own pose, projected onto it alone
        pk = atlas_only(placed(posed_as(ba, part["animation"]), pos, yaw), ba.atlas.index)
        only = [i for i, b in enumerate(pk.part_bones) if b in set(part["bones"])]
        region = ("bones", part["bones"])
        for label, cam in orbit_views(pk, [], [(l, region, y, p) for l, y, p in part["views"]], facing=yaw):
            views.append((label, cam, "moving", (pk, only, part.get("hint", ""))))
    s = restyle.look_settings(look)
    prompt = " ".join(x for x in (_model_words(ba.id, words), (extra_words or "").strip()) if x)
    R = restyle.Restyler(s, url=url, kind="battle_model", name=f"battleatlas_{ba.id}",
                         prompt=prompt, strength=strength, describe=describe, on_progress=on_progress)
    if stop is not None:  # ComfyUI interrupted mid-view (Restyler's calls don't pass comfy's cancelled on)
        run, run_text = R.comfy.run, R.comfy.run_text
        R.comfy.run = lambda wf, on_progress=None, cancelled=None: run(wf, on_progress, stop)
        R.comfy.run_text = lambda wf, cancelled=None: run_text(wf, stop)
    base_denoise = R.denoise
    ref = None  # the first view's painting (lab_stats of its model pixels), for match
    P = _Painter(pm_battle, 1, [ba.atlas.image])
    better = mv.get("better") if better is None else better
    P.better = paintmod.BETTER if better is None else float(better)
    log = []
    scene_words, floor = None, None
    for i, (label, cam, kind, extra) in enumerate(views):
        if stop is not None and stop():
            raise InterruptedError("cancelled")
        if order == "first" and i == len(bv) and keep_battle != 1:
            P.best = [b * keep_battle for b in P.best]  # the battle cameras' paint: others must see it this much better
        mesh = pm_battle if extra is None else extra[0]
        P.mesh = mesh  # (Painter.add and view_mask raster its mesh: a moving part's views have their own pose)
        r = render.rasterize(mesh, cam)
        if not (r.tri >= 0).any():
            continue
        how = "scene" if kind == "battle" else background
        bg = (scene_behind(sid, cam) if how == "scene" else
              sky_backdrop(sid, bviews[0][1], (cam.width, cam.height)) if how == "sky" else None)
        vis, edge = projectable(r, None if bg is None else bg[1])
        hint = extra[2] if extra is not None else hints.get(label, hints.get("battle camera", "") if kind == "battle" else "")
        if scene_words is not None:  # this view's own words after the model's (prepare set them on the first view)
            R.s["scene"] = " ".join(x for x in (scene_words, hint) if x)
        shown = P.images()
        fg = recolour(paintmod.shade(mesh, r, [ba.atlas.image]), mesh, r, colours)
        if kind != "battle" and match and ref is not None:
            fg = match_input(fg, vis, ref)
        elif kind != "battle" and floor is not None:
            fg = lift(fg, vis, floor)
        orig = composite(fg, r, bg)
        on_model = r.tri >= 0
        if extra is not None:  # only the moving part's own pixels go back
            vis = vis & np.isin(mesh.part[np.maximum(r.tri, 0)], extra[1]) & on_model
            edge = edge * vis
        if not any((w > 0).any() for w in P.wsum):
            mask = vis
            R.prompt = " ".join(x for x in (prompt, hint) if x)  # the first view's own words go in with prepare's
            out = _styled(R, stop, orig, None, None)
            R.prompt = prompt
            inner = cv2.erode(vis.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            if inner.sum() > 100:
                ref = lab_stats(out[inner])
            scene_words = " ".join(x for x in (restyle.HINTS.get(R.kind, ""), prompt, R.words) if x)
            if lift_share:
                floor = lift_share * float(np.median(fg[vis].astype(np.float32).mean(-1)))
            weight = edge
        else:
            mask = P.view_mask(cam, r) & vis
            new = float(mask.sum() / max(vis.sum(), 1))
            if new < MIN_NEW:
                log.append(dict(label=label, kind=kind, new=new, image=None))
                continue
            cur = composite(paintmod.shade(mesh, r, shown), r, bg)
            grown = cv2.dilate(mask.astype(np.uint8), np.ones((2 * paintmod.MASK_GROW + 1,) * 2, np.uint8)) > 0
            R.denoise = strengths.get(label, base_denoise)
            if whole and kind == "round":  # restyled whole (its own repaint), only the mask goes back
                R.prompt, keep = " ".join(x for x in (prompt, hint) if x), R.s.get("denoise")
                R.s["denoise"] = R.denoise
                out = _styled(R, stop, orig, None, None)
                R.prompt, R.s["denoise"] = prompt, keep
            else:
                # (a moving part: the AI repaints round it too, to meet the paint there; only its own pixels go back)
                out = _styled(R, stop, orig, cur, grown & (on_model if extra is not None else vis))
            R.denoise = base_denoise
            weight = paintmod.feather(mask) * edge
        P.add(cam, r, out, np.where(weight >= EDGE_MIN, weight, 0).astype(np.float32))
        log.append(dict(label=label, kind=kind, new=float(mask.sum() / max(vis.sum(), 1)), image=out, orig=orig))
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.hstack([orig, out])).save(Path(log_dir) / f"view_{i:02d}.jpg", quality=88)
        if progress:
            progress(i + 1, len(views), label)
    P.mesh = pm_battle
    if scene_words is not None:
        R.s["scene"] = scene_words
    P.fill_holes()
    img = P.images()[0]
    img = atlasmod.fill(img, ba.atlas.used())
    return img, log, R


# --- the game's colours ------------------------------------------------------------------------------------------------

LOCK_MIN_SAT = 0.08  # colour_lock(): texels whose original is greyer than this keep the painting's hue


def colour_lock(ba, image, mode="hue", amount=1.0):
    """The painted atlas with the game's own colours put back, the painting's light and dark kept (its drawing: lines,
    shadow shapes): per texel, in HSV, the hue ("hue"), or the hue and saturation ("hsv"), of the original atlas (the
    game's colours, ba.atlas.image) where that's coloured at all (LOCK_MIN_SAT), blended in by `amount`.
    - Zolom ("hue"): the AI painted brown patches over its sides and coils (v6-v11, whatever the views and words); the
      game's own hue puts them back to green, its red-brown plates and hood markings stay.
    - The party ("hsv"): the AI turned Cloud's gold hair pale and his navy clothes black, drew a second pauldron in
      silver on his bare shoulder (now skin-coloured), and Barret's olive vest brown."""
    if not mode or amount <= 0:
        return image
    p = cv2.cvtColor(np.ascontiguousarray(image[..., :3]), cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    o = cv2.cvtColor(np.ascontiguousarray(ba.atlas.image[..., :3]), cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    ok = o[..., 1] >= LOCK_MIN_SAT * 255
    out = p.copy()
    out[..., 0] = np.where(ok, o[..., 0], p[..., 0])
    if mode == "hsv":
        out[..., 1] = np.where(ok, o[..., 1], p[..., 1])
    rgb = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB_FULL).astype(np.float32)
    res = image.copy()
    res[..., :3] = np.clip(image[..., :3] * (1 - amount) + rgb * amount, 0, 255).astype(np.uint8)
    return atlasmod.fill(res, ba.atlas.used())


# --- the target's colours ----------------------------------------------------------------------------------------------

# The 2D target (target2d_55.png, 1040 x 405) is the game's 1280 x 720 frame of formation 469 (its 4:3 picture at x
# 160-1120) cropped to TARGET_CROP and repainted. Zolom in it: TARGET_BOX of the crop (above Barret's head), not sky.
TARGET = REPO / "work" / "models" / "battle" / "target2d" / "target2d_55.png"
TARGET_MODEL = "dd"
TARGET_FORMATION = 469
TARGET_CROP = (120, 0, 1160, 405)   # x0, y0, x1, y1 in the game's 1280 x 720 frame
TARGET_BOX = (330, 0, 700, 270)     # ...and Zolom's part of the crop


def _placed_view(ba, image, pos, cam):
    """(RGB, raster) of a model placed at pos (turned as model_yaw), the original (image None) or its atlas."""
    yaw = model_yaw(ba.id)
    if image is None:
        m = placed(ba.original, pos, yaw)
        r = render.rasterize(m, cam)
        return render.shade(m, r, modulate=True), r
    m = atlas_only(placed(ba.posed, pos, yaw), ba.atlas.index)
    r = render.rasterize(m, cam)
    return paintmod.shade(m, r, [image]), r


def formation_ids(formation=TARGET_FORMATION, scene_bin=battle.SCENE_BIN):
    """The models a formation shows: its enemies' ids, then the party's (PARTY_POSITIONS' for it)."""
    from . import scenebin
    f = next((x for x in scenebin.load(scene_bin).formations if x.id == formation), None)
    out = [] if f is None else [battle.id_name(e) for e, _ in f.enemies]
    return list(dict.fromkeys(out + list(PARTY_POSITIONS.get(formation, {}))))


def game_frame(ba, image=None, formation=TARGET_FORMATION, others=None):
    """(RGB, model mask) of a formation's battle view as the game frames it at 1280 x 720 (the 4:3 picture in the
    middle, black bars), the scene behind: the original model (image None) or the atlas showing `image`. others: more
    models in the same frame, each where the formation has it: an id (drawn as the game does) or (BattleAtlas, image
    or None); the mask is then where this model shows in front of them."""
    label, cam, pos, sid = battle_views(ba.id, formation, (960, 720))[0]
    fg, r = _placed_view(ba, image, pos, cam)
    bg = scene_behind(sid, cam)
    vis = (r.tri >= 0) & (r.depth < bg[1])
    pic = composite(fg, r, bg)
    depth = np.where(vis, r.depth, bg[1])
    for o in others or []:
        oba, oimg = (o, None) if isinstance(o, str) else o
        if isinstance(oba, str):
            oba = BattleAtlas(oba, battle.game_name(oba), None, None,
                              battle.load_model(oba, 0, weapon=0 if is_party(oba) else -1), [], None)
        opos = battle_views(oba.id, formation, (960, 720))[0][2]
        ofg, orr = _placed_view(oba, oimg, opos, cam)
        hit = (orr.tri >= 0) & (orr.depth < depth)
        pic[hit] = ofg[hit]
        depth = np.where(hit, orr.depth, depth)
        vis &= ~hit
    img = np.zeros((720, 1280, 3), np.uint8)
    mask = np.zeros((720, 1280), bool)
    img[:, 160:1120] = pic
    mask[:, 160:1120] = vis
    return img, mask


def target_pixels(path=TARGET, box=TARGET_BOX):
    """The target's model pixels (n, 3) RGB: TARGET_BOX, less the sky (bluer than green, or grey)."""
    tgt = np.asarray(Image.open(path).convert("RGB"))
    t = tgt.astype(int)
    m = np.zeros(tgt.shape[:2], bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    sat = cv2.cvtColor(tgt, cv2.COLOR_RGB2HSV)[..., 1]
    m &= ~((t[..., 2] > t[..., 1] + 5) | (sat < 40))
    return tgt[m]


def lab_grade(image, src, ref, amount=1.0):
    """image (RGB or RGBA) graded so the colours `src` (n, 3 RGB: the model as painted, in some view) take the mean and
    spread of `ref` (the target's) in Lab, channel by channel (Reinhard); amount blends it in. Returns (image,
    {src, ref: Lab means and spreads})."""
    def lab(x):
        return cv2.cvtColor(np.ascontiguousarray(x, np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    ls, lr = lab(src), lab(ref)
    ms, ss, mr, sr = ls.mean(0), ls.std(0) + 1e-3, lr.mean(0), lr.std(0) + 1e-3
    rgb = image[..., :3]
    li = lab(rgb)
    out = (li - ms) / ss * sr + mr
    out = li + (out - li) * amount
    back = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8).reshape(-1, 1, 3), cv2.COLOR_LAB2RGB).reshape(rgb.shape)
    res = image.copy()
    res[..., :3] = back
    return res, {"src": [ms.round(2).tolist(), ss.round(2).tolist()], "ref": [mr.round(2).tolist(), sr.round(2).tolist()]}


# The party in the target (crop coordinates): Barret seen from behind (the target drew him a head, which the game's
# frame and ours hide behind his shoulders), Cloud from behind with his sword; both cut at the waist by the crop.
TARGET_BOXES = {"dd": TARGET_BOX, "sc": (250, 270, 460, 405), "rt": (690, 290, 860, 405)}
TARGET_ERODE = 4  # target_grade, the party: our render's outline shrunk this many pixels picks the target's pixels


def target_box(id):
    """The model's box in the target's crop (TARGET_BOXES; Barret's and Cloud's other forms as theirs), or None."""
    id = id.lower()
    return TARGET_BOXES.get(id) or TARGET_BOXES.get({"sb": "sc", "sd": "sc", "se": "sc", "si": "rt"}.get(id, ""))


def target_grade(ba, image, amount=1.0, path=TARGET):
    """The painted atlas graded to the target's colours: the model's pixels in the target's frame (game_frame cropped
    to TARGET_CROP, inside its box) against the target's. Zolom: the target's in TARGET_BOX less its sky
    (target_pixels). The party: the target's pixels inside our render's outline of the model (the formation's other
    models in front of it), shrunk TARGET_ERODE pixels (the target keeps the frame's layout; the poses differ a
    little). Returns (atlas, stats)."""
    party = is_party(ba.id)
    others = [o for o in formation_ids() if o != ba.id] if party else None
    img, mask = game_frame(ba, image, others=others)
    x0, y0, x1, y1 = TARGET_CROP
    img, mask = img[y0:y1, x0:x1], mask[y0:y1, x0:x1]
    bx0, by0, bx1, by1 = target_box(ba.id) or TARGET_BOX
    box = np.zeros(mask.shape, bool)
    box[by0:by1, bx0:bx1] = True
    if party:
        inner = cv2.erode(mask.astype(np.uint8), np.ones((2 * TARGET_ERODE + 1,) * 2, np.uint8)) > 0
        ref = np.asarray(Image.open(path).convert("RGB"))[inner & box]
    else:
        ref = target_pixels(path)
    out, st = lab_grade(image, img[mask & box], ref, amount)
    return atlasmod.fill(out, ba.atlas.used()), st


# --- builds -----------------------------------------------------------------------------------------------------------

def _next_version(id):
    d = WORK / id.lower()
    n = [int(p.name[1:]) for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()] if d.exists() else []
    return f"v{max(n, default=0) + 1}"


def export(ba, image, out_dir, info=None):
    """<MODEL>.png (the atlas image, RGBA) and <MODEL>.P<nn>.uv per part into out_dir, plus info.json. Returns
    {file name: path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = np.ascontiguousarray(image)
    if image.shape[-1] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    files = {}
    p = out_dir / f"{ba.name}.png"
    Image.fromarray(image, "RGBA").save(p)
    files[p.name] = p
    for i, pf in enumerate(ba.parts):
        p = out_dir / f"{pf.game}.uv"
        p.write_bytes(uv_bytes(ba, i))
        files[p.name] = p
    meta = {"id": ba.id, "model": ba.name, "atlas": {"size": ba.size, "density": round(ba.atlas.density, 4),
                                                     "charts": len(ba.atlas.charts)},
            "parts": [{"file": pf.file, "game": pf.game, "bone": pf.bone, "numverts": pf.numverts,
                       "numpolys": pf.numpolys} for pf in ba.parts],
            "files": sorted(files), "at": time.strftime("%Y-%m-%d %H:%M:%S"), **(info or {})}
    (out_dir / "info.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return files


def build_files(out_dir):
    """{file name: path} of a build folder's game files (the PNG and .uv files)."""
    out_dir = Path(out_dir)
    return {p.name: p for p in sorted(out_dir.iterdir()) if p.suffix.lower() in (".png", ".uv") and p.is_file()}


def version_dir(id, version=None):
    """A build's folder (the newest when version is None)."""
    d = WORK / id.lower()
    if version:
        return d / version
    vs = sorted((p for p in d.glob("*") if (p / "info.json").exists()), key=lambda p: p.stat().st_mtime) if d.exists() else []
    if not vs:
        raise FileNotFoundError(f"no build of {id} in {d}")
    return vs[-1]


# --- install ----------------------------------------------------------------------------------------------------------

_lock = threading.RLock()


def atlas_dir():
    """The game's mesh/battle/atlas (under FFNx's external_mesh_path)."""
    from . import battlelight
    return battlelight.mesh_dir() / "battle" / "atlas"


def _hash(p):
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


def installed():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(data):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def install(id, version=None):
    """Copy a build's <MODEL>.png and .uv files into the game's mesh/battle/atlas/, recorded in the manifest. Replaces
    this model's earlier install; refuses (writing nothing) where a file it would write is there and isn't ours.
    Returns the manifest entry."""
    id = id.lower()
    src_dir = version_dir(id, version)
    files = build_files(src_dir)
    if not files:
        raise FileNotFoundError(f"nothing to install in {src_dir}")
    dest = atlas_dir()
    with _lock:
        data = installed()
        kept = []  # folders the earlier install made that are still there (something else is in them): still ours
        if id in data:
            before = data[id].get("folders", [])
            uninstall(id)
            data = installed()
            kept = [d for d in before if Path(d).exists()]
        ours = {f["path"].lower() for e in data.values() for f in e["files"]}
        clash = [str(dest / n) for n in files if (dest / n).exists() and str(dest / n).lower() not in ours]
        if clash:
            raise FileExistsError("not ours, left alone: " + ", ".join(clash))
        made, done = list(kept), []
        try:
            for d in reversed([dest, *dest.parents]):
                if not d.exists():
                    d.mkdir()
                    made.append(str(d))
            for n, src in files.items():
                dst = dest / n
                dst.write_bytes(Path(src).read_bytes())
                done.append({"path": str(dst), "sha1": _hash(dst)})
        except Exception:
            for f in done:
                Path(f["path"]).unlink(missing_ok=True)
            for d in reversed(made[len(kept):]):
                try:
                    Path(d).rmdir()
                except OSError:
                    pass
            raise
        entry = {"version": src_dir.name, "from": str(src_dir), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "files": done, "folders": made}
        data[id] = entry
        _save_manifest(data)
        return entry


def uninstall(id):
    """Take a model's install out of the game: exactly the files it wrote (left where something changed them since),
    and the folders it made if they're empty. A folder that can't go yet stays recorded: in another model's entry when
    its files are in it, else in this model's (files [], "left": the changed files), so a later uninstall or install
    of it takes it away. Returns (files removed, files left)."""
    id = id.lower()
    with _lock:
        data = installed()
        entry = data.get(id)
        if not entry:
            return 0, []
        removed, left = 0, []
        for f in entry["files"]:
            p = Path(f["path"])
            if not p.exists():
                continue
            if _hash(p) != f.get("sha1"):
                left.append(str(p))
                continue
            p.unlink()
            removed += 1
        kept = []
        for d in reversed(entry.get("folders", [])):
            if not Path(d).exists():
                continue
            try:
                Path(d).rmdir()
            except OSError:  # another model's files are in it: that one takes it away later
                heir = next((e for k, e in data.items() if k != id and any(
                    Path(f["path"]).is_relative_to(d) for f in e["files"])), None)
                if heir is not None:
                    if d not in heir.setdefault("folders", []):
                        heir["folders"].insert(0, d)
                else:  # a changed file of ours (or something else) is in it: still ours to take away later
                    kept.insert(0, d)
        if kept or left:
            data[id] = {"version": entry.get("version"), "from": entry.get("from"), "at": entry.get("at"),
                        "files": [], "folders": kept, "left": left + entry.get("left", [])}
        else:
            data.pop(id)
        _save_manifest(data)
        return removed, left


def status():
    out = {}
    if WORK.exists():
        for d in sorted(p for p in WORK.iterdir() if p.is_dir()):
            vs = sorted(v.name for v in d.iterdir() if (v / "info.json").exists())
            if vs:
                out[d.name] = {"built": vs}
    for k, e in installed().items():
        out.setdefault(k, {})["installed"] = {"version": e["version"], "files": len(e["files"]),
                                              **({"left": e["left"]} if e.get("left") else {})}
    return out


# --- previews ---------------------------------------------------------------------------------------------------------

def battle_frame(ba, image=None, formation=None, size=VIEW_SIZE, background=True):
    """The model as a formation's resting battle camera shows it, with the battle scene behind: the original (vertex
    colours) when image is None, else the atlas showing `image` with flat light (what FFNx-3d draws with toon_shadow
    white). RGB."""
    label, cam, pos, sid = battle_views(ba.id, formation, size)[0]
    fg, r = _placed_view(ba, image, pos, cam)
    return composite(fg, r, scene_behind(sid, cam) if background else None)


def head_region(id):
    """A model's head for close views (region_points): Zolom's hood and face, a party member's head bone (PARTY_BONES),
    else "head"."""
    id = id.lower()
    if id == "dd":
        return DD_HEAD
    if is_party(id):
        bones = PARTY_BONES.get(PARTY_ALIAS.get(id, id), {})
        return ("bones", bones["head"]) if "head" in bones else ("band", 0.0, PARTY_HEAD_SHARE)
    return "head"


def turntable(ba, image, path, formation=None):
    """A sheet of the painted model all round (8 views, 12 degrees up) and two of its head, with the battle scene
    behind: what the battle cameras show when they swing round."""
    _, cam0, pos, sid = battle_views(ba.id, formation)[0]
    yaw = model_yaw(ba.id)
    pm = atlas_only(placed(ba.posed, pos, yaw), ba.atlas.index)
    orbit = [(f"yaw {y}", y, 12) for y in (0, 45, 90, 135, 180, 225, 270, 315)]
    head = head_region(ba.id)
    cells = []
    for _, cam in orbit_views(pm, orbit, [("head", head, 20, 10), ("head", head, -60, 10)], size=(480, 480),
                              facing=yaw):
        r = render.rasterize(pm, cam)
        cells.append(composite(paintmod.shade(pm, r, [image]), r, scene_behind(sid, cam)))
    Image.fromarray(np.vstack([np.hstack(cells[i:i + 5]) for i in (0, 5)])).save(path, quality=88)


ZOOM = 2.5  # vs_target's close-up of a party member's box


def vs_target(ba, image, path):
    """The original and the painted model in the target's framing (game_frame cropped to TARGET_CROP), above the
    target. A party member: the formation's other models drawn too (as the game does), and below, its box in each of
    the three ZOOM times larger, side by side."""
    x0, y0, x1, y1 = TARGET_CROP
    tgt = np.asarray(Image.open(TARGET).convert("RGB"))
    others = [o for o in formation_ids() if o != ba.id] if is_party(ba.id) else None
    rows = [game_frame(ba, None, others=others)[0][y0:y1, x0:x1], game_frame(ba, image, others=others)[0][y0:y1, x0:x1],
            tgt]
    sheet = np.vstack(rows)
    box = target_box(ba.id) if is_party(ba.id) else None
    if box is not None:
        bx0, by0, bx1, by1 = box
        w, h = int((bx1 - bx0) * ZOOM), int((by1 - by0) * ZOOM)
        cells = [np.asarray(Image.fromarray(np.ascontiguousarray(r[by0:by1, bx0:bx1])).resize((w, h), Image.LANCZOS))
                 for r in rows]
        zoom = np.hstack(cells)
        W = max(sheet.shape[1], zoom.shape[1])
        pad = lambda a: np.pad(a, ((0, 0), (0, W - a.shape[1]), (0, 0)))
        sheet = np.vstack([pad(sheet), pad(zoom)])
    Image.fromarray(sheet).save(path, quality=92)


def moving_check(ba, image, path, formation=None):
    """The model's separately animated parts (MOVING) as painted: per part, its head close up from the front, side and
    below, in the pose painted from with the part hidden (nothing of it may be painted on what's left: Zolom's v6 had
    a tongue painted down its face), then in the part's own pose with it showing. None when the model has none."""
    parts = MOVING.get(ba.id.lower(), [])
    if not parts:
        return None
    _, _, pos, _ = battle_views(ba.id, formation)[0]
    yaw = model_yaw(ba.id)
    region = head_region(ba.id)
    rows = []
    for part in parts:
        cells = []
        for m in (hidden(ba.posed, part["bones"]), posed_as(ba, part["animation"])):
            pm = atlas_only(placed(m, pos, yaw), ba.atlas.index)
            for _, cam in orbit_views(pm, [], [("f", region, 0, 0), ("s", region, 80, 0), ("b", region, 20, -35)],
                                      size=(400, 400), facing=yaw):
                cells.append(paintmod.shade(pm, render.rasterize(pm, cam), [image]))
        rows.append(np.hstack(cells))
    Image.fromarray(np.vstack(rows)).save(path, quality=90)
    return path


def previews(ba, image, out_dir, formation=None):
    """preview/battle.jpg (original | painted from the resting battle camera), turntable.jpg, vs_target.jpg for the
    target's models (Zolom, and the party with a close-up of each), and moving.jpg (moving_check) for models with
    moving parts."""
    d = Path(out_dir) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.hstack([battle_frame(ba, None, formation), battle_frame(ba, image, formation)])).save(
        d / "battle.jpg", quality=90)
    turntable(ba, image, d / "turntable.jpg", formation)
    if (ba.id == TARGET_MODEL or target_box(ba.id) is not None) and TARGET.exists():
        vs_target(ba, image, d / "vs_target.jpg")
    moving_check(ba, image, d / "moving.jpg", formation)


def _save_identity(ba, out_dir, formation=None):
    label, cam, pos, sid = battle_views(ba.id, formation)[0]
    o, a, st = identity(ba, cam, pos, model_yaw(ba.id))
    d = np.abs(o.astype(int) - a.astype(int)).max(-1)
    heat = np.clip(d * 16, 0, 255).astype(np.uint8)
    Image.fromarray(np.hstack([o, a, np.dstack([heat] * 3)])).save(Path(out_dir) / "identity.png")
    return st


def build_of(id, version=None):
    """The BattleAtlas a build was made with (version None: the newest), its atlas size and weapon from its info.json:
    check() needs the same layout."""
    d = version_dir(id, version)
    try:
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    w = info.get("weapon")
    return build(id, (info.get("atlas") or {}).get("size"), weapon=-1 if w is None else w)


def paint_info(ba, look, strength, words, lift_share, log, R, seconds, **kw):
    """info.json's "painted" for a paint() run."""
    mv = model_views(ba.id)
    lift_share = mv["lift_share"] if isinstance(lift_share, str) else lift_share
    out = {"look": look, "strength": strength, "words": _model_words(ba.id, words), "describer": R.words,
           "prompt": R.s.get("scene"), "lift_share": lift_share, "background": ORBIT_BACKGROUND,
           "keep_battle": KEEP_BATTLE, "battle_cameras": kw.get("battle_cameras") or mv["battle_cameras"],
           "moving": [p["name"] for p in (mv["moving"] if kw.get("moving") is None else kw["moving"])],
           "strengths": kw.get("strengths") or mv["strengths"], "seconds": round(seconds),
           "views": [dict(label=x["label"], kind=x.get("kind"), new=round(x["new"], 3)) for x in log]}
    return out


PARTY_GRADE = 0.0  # make(): the party's grade to the target (Cloud's v1 there: lightness 59.8 against 59.3, a, b within
                   # 4; only its contrast differs, and the target's figures are tiny: not graded)
DEFAULT_URL = "http://127.0.0.1:8000"


def default_grade(id):
    """make()'s grade to the 2D target's colours: 1 for the target's model (Zolom), PARTY_GRADE for the party, else
    0."""
    id = id.lower()
    return 1.0 if id == TARGET_MODEL else PARTY_GRADE if target_box(id) is not None else 0.0


def make(id, size=None, look=None, strength=None, extra_words="", url=None, progress=None, cancelled=None,
         out_dir=None, words=WORDS, grade=None, weapon="auto", formation=None, lift_share="auto", on_progress=None,
         lock=None, **paint_kw):
    """A painted build, end to end (the CLI's `paint`): the atlas laid out (build), the identity check, paint() with
    the model's own recipe (model_views), the game's colours put back (colour_lock; lock None: the model's
    model_views()["lock"], False none), the grade to the 2D target (grade None: default_grade), the previews, the
    files (export) and check(). Into out_dir (default: WORK/<id>/<the next version>). Returns (version folder, info).

    look, strength: LOOK and STRENGTH when None. extra_words: the user's own words (paint()'s). progress(i, n, label)
    after each view; on_progress: ComfyUI's within a view; cancelled: a callable, true (or raising InterruptedError)
    to stop, checked between the steps and while ComfyUI paints (it's interrupted): raises InterruptedError. Raises
    ValueError when check() finds a problem with the files. paint_kw: more of paint()'s settings."""
    def stop():
        if cancelled is not None and _stop_check(cancelled)():
            raise InterruptedError("cancelled")

    look = LOOK if look is None else look
    strength = STRENGTH if strength is None else strength
    ba = build(id, size, weapon=weapon)
    stop()
    out = Path(out_dir) if out_dir else WORK / ba.id / _next_version(ba.id)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    info = {"identity": _save_identity(ba, out / "preview", formation),
            "weapon": ba.original.weapon if ba.original.weapon >= 0 else None}
    stop()
    t = time.time()
    img, log, R = paint(ba, look, strength, words, formation, url=url or DEFAULT_URL, lift_share=lift_share,
                        log_dir=out / "preview", progress=progress, extra_words=extra_words, on_progress=on_progress,
                        cancelled=cancelled, **paint_kw)
    info["painted"] = paint_info(ba, look, strength, words, lift_share, log, R, time.time() - t,
                                 **{k: paint_kw[k] for k in ("battle_cameras", "moving", "strengths") if k in paint_kw})
    info["painted"]["extra_words"] = extra_words or ""
    stop()
    lock = model_views(ba.id).get("lock") if lock is None else lock
    if lock:
        Image.fromarray(img).save(out / "preview" / "unlocked.png")
        img = colour_lock(ba, img, lock)
        info["locked"] = lock
    grade = default_grade(ba.id) if grade is None else grade
    if grade > 0 and TARGET.exists():
        Image.fromarray(img).save(out / "preview" / "ungraded.png")
        img, gst = target_grade(ba, img, grade)
        info["graded"] = dict(amount=grade, **gst)
    stop()
    previews(ba, img, out, formation)
    stop()
    files = export(ba, img, out, info)
    probs = check(ba, files)
    if probs:
        raise ValueError(f"{out.name}: " + "; ".join(probs))
    return out, info


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.battleatlas")
    ap.add_argument("cmd", choices=("build", "paint", "check", "install", "uninstall", "status"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--version")
    ap.add_argument("--size", type=int, default=None, help="the atlas' size (default: atlas_size(id))")
    ap.add_argument("--weapon", type=int, default=None,
                    help="a party member's weapon in the atlas too (default: PARTY_WEAPON for the party; -1 none)")
    ap.add_argument("--look", default=LOOK)
    ap.add_argument("--strength", type=float, default=STRENGTH)
    ap.add_argument("--words", default=WORDS)
    ap.add_argument("--formation", type=int)
    ap.add_argument("--lift", default="auto", help="lift_share: a number, 'none', or 'auto' (the model's)")
    ap.add_argument("--grade", type=float, default=None,
                    help="grade to the 2D target's colours, 0-1 (default: default_grade(id): 1 for dd, else 0)")
    ap.add_argument("--allow-game", action="store_true")
    a = ap.parse_args(argv)
    lift_share = a.lift if a.lift == "auto" else None if a.lift.lower() == "none" else float(a.lift)
    if a.cmd in ("build", "paint"):
        weapon = "auto" if a.weapon is None else a.weapon
        out = WORK / a.id.lower() / (a.version or _next_version(a.id))
        if a.cmd == "paint":
            from . import battlelight
            if battlelight.game_running() and not a.allow_game:
                raise SystemExit("the game is running (it shares the GPU with ComfyUI): close it first")
            out, info = make(a.id, a.size, a.look, a.strength, out_dir=out, words=a.words, grade=a.grade,
                             weapon=weapon, formation=a.formation, lift_share=lift_share,
                             progress=lambda i, n, label: print(f"view {i}/{n}: {label}", flush=True))
            print(json.dumps({"out": str(out), "identity": info["identity"], "graded": "graded" in info}, indent=1))
            return 0
        ba = build(a.id, a.size, weapon=weapon)
        (out / "preview").mkdir(parents=True, exist_ok=True)
        st = _save_identity(ba, out / "preview", a.formation)
        info = {"identity": st, "weapon": ba.original.weapon if ba.original.weapon >= 0 else None}
        img = ba.atlas.image
        files = export(ba, img, out, info)
        previews(ba, img, out, a.formation)
        print(json.dumps({"out": str(out), "identity": st, "files": len(files)}, indent=1))
    elif a.cmd == "check":
        ba = build_of(a.id, a.version) if a.size is None and a.weapon is None else build(a.id, a.size, weapon=a.weapon)
        p = check(ba, build_files(version_dir(a.id, a.version)))
        print("\n".join(p) if p else "ok")
        return 1 if p else 0
    elif a.cmd == "install":
        print(json.dumps(install(a.id, a.version), indent=1))
    elif a.cmd == "uninstall":
        print(uninstall(a.id))
    else:
        print(json.dumps(status(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
