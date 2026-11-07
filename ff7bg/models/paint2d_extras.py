"""Extras for "Paint in 2D style" (battleatlas; the Studio's card in studio/plugins/models.py): variants, the AI check
and the touch-up. Added 2026-11-07, after the 2D paints needed a lot of hand-iterating: the AI invents things (a second
pauldron on Cloud's bare shoulder, a tongue on Zolom's face where its real tongue is a part of its own, brown patches
on Zolom's sides, an open fanged mouth at higher strength), and the card made one paint per click.

Variants: one paint job paints several versions, each with its own seed (the look's "seed", comfy.DEFAULT_SETTINGS
2107, which the Restyler's workflows give the sampler: KSampler's seed, the Qwen turbo's RandomNoise). battleatlas
reads the look through restyle.look_settings, so seeded() swaps the seed in there for one make() call; battleatlas
itself is untouched (fieldatlas.paint reads the look the same way).

The check (check()): the painted model and the original rendered from the same angles (check_views: all round, the
head close up, the shoulders, and each shoulder close up where the recipe knows the arms), each pair shown to the
Studio's describer (Qwen3.5 9B through ComfyUI's TextGenerate, comfy.CAPTION_CLIP: the model behind describe_scene /
restyle.Restyler.prepare) with two questions: COMPARE (anything there the original doesn't have, the same number of
eyes, arms and shoulder pads, the same colours per part; the model's own words, battleatlas.WHAT, and the recipe's for
that view, model_views' hints, go with it) and COUNT (shoulder pads, open mouths, tongues, heads and arms counted in
each picture, compared here). verdict(): a part counted more in the painting, or COMPARE agreeing in COMPARE_AGREE
views, fails it, with a plain reason; the rest are remarks. It's advice: nothing is thrown away.

The touch-up (touch_up()): one of a version's views (touch_views: the battle camera, the turntable's eight sides and
two of the head; or one taken from the Studio's viewer, in any pose: Battle.placed_camera; the cameras stored with the
version, views.json) rendered from the painted texture, the user's marked areas repainted with the version's look,
words and strength (ff7bg/touchup.py's repaint: the states pass's masked repaint, faded into what's around), then only
the changed pixels projected back onto the painted texture through battleatlas.paint's projection (paint.Painter.add,
its edge weights), onto the texels the camera sees there (project()). export_touchup() makes a new version of it with
the usual pictures and files.

pn_round(): a mesh rounded as FFNx-3d's char_round rounds field models (PN triangles), for the Studio's viewer.

  python -m ff7bg.models.paint2d_extras check <id> <version> [--field] [--url U]     the AI check of a version
  python -m ff7bg.models.paint2d_extras pairs <id> <version> [--field]              its pictures only (no AI)
"""
import contextlib
import json
import math
import random
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import atlas as atlasmod
from . import battleatlas as BA
from . import paint as paintmod
from . import render

REPO = Path(__file__).resolve().parents[2]
TMP = REPO / "work" / "models" / "restyle"

# --- variants ---------------------------------------------------------------------------------------------------------

SEED_MAX = 2 ** 31 - 1
_seed_lock = threading.RLock()


def new_seed():
    return random.randrange(1, SEED_MAX)


@contextlib.contextmanager
def seeded(seed):
    """battleatlas.paint (fieldatlas.paint too) painting with `seed` instead of the look's: restyle.look_settings,
    which they read the look through, returns the seed swapped in while this is open (one seeded paint at a time: a
    lock). None: as it is."""
    if seed is None:
        yield
        return
    from . import restyle
    with _seed_lock:
        orig = restyle.look_settings

        def look_settings(look_id=None, family=None):
            return dict(orig(look_id, family), seed=int(seed))
        restyle.look_settings = look_settings
        try:
            yield
        finally:
            restyle.look_settings = orig


# --- the model, as the check and the touch-up see it ------------------------------------------------------------------

PLAIN = ((150, 160, 172), (196, 196, 190))  # the check's plain background: top and bottom of a vertical gradient


def plain_backdrop(w, h, colours=PLAIN):
    top, bottom = (np.asarray(c, np.float32) for c in colours)
    k = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    col = top * (1 - k) + bottom * k
    return np.broadcast_to(col[:, None, :], (h, w, 3)).astype(np.uint8).copy(), np.full((h, w), np.inf, np.float32)


def camera_json(cam):
    return {"eye": [float(x) for x in cam.eye], "target": [float(x) for x in cam.target],
            "up": [float(x) for x in cam.up], "fov_y": float(cam.fov_y), "width": int(cam.width),
            "height": int(cam.height), "near": float(cam.near),
            "focal": None if cam.focal is None else float(cam.focal)}


def camera_from(d):
    return render.Camera(eye=np.array(d["eye"], float), target=np.array(d["target"], float),
                         up=np.array(d["up"], float), fov_y=float(d["fov_y"]), width=int(d["width"]),
                         height=int(d["height"]), near=float(d["near"]), focal=d.get("focal"))


@dataclass
class View:
    key: str          # "battle", "yaw045", "head1"; "cam" + 8 hex digits: one taken from the Studio's viewer
    label: str        # for the user
    camera: object    # render.Camera
    background: str   # "scene" (the battle scene), "sky" (paint()'s plain sky gradient) or "plain"
    animation: int = 0  # the model's pose: the first frame of this animation (0: as painted)

    def json(self):
        return {"key": self.key, "label": self.label, "background": self.background, "camera": camera_json(self.camera),
                "animation": int(self.animation)}

    @staticmethod
    def of(d):
        return View(d["key"], d["label"], camera_from(d["camera"]), d.get("background", "plain"),
                    int(d.get("animation") or 0))


TURN_LABELS = {0: "front", 45: "front, turned left", 90: "left side", 135: "back, turned left", 180: "back",
               225: "back, turned right", 270: "right side", 315: "front, turned right"}
TOUCH_SIZE = (1024, 1024)  # the turntable's views for the touch-up, bigger than its 480 px cells (same framing)


class Battle:
    """A battle model (battleatlas.BattleAtlas) as the check and the touch-up draw it: placed where its formation has
    it (the resting battle camera's formation), turned as in battle, its moving parts (battleatlas.MOVING: Zolom's
    tongue) left out as paint() leaves them out."""
    kind = "battle"

    def __init__(self, ba, formation=None):
        self.ba, self.id, self.atlas = ba, ba.id, ba.atlas
        bv = BA.battle_views(ba.id, formation)
        if not bv:
            raise ValueError(f"{ba.id} is in no formation with a battle camera")
        self.label0, self.cam0, self.pos, self.sid = bv[0]
        self.formation = formation
        self.yaw = BA.model_yaw(ba.id)
        mv = BA.model_views(ba.id)
        self.hide = sorted({b for part in mv["moving"] for b in part["bones"]})
        self.painted = BA.hidden(BA.atlas_only(BA.placed(ba.posed, self.pos, self.yaw), ba.atlas.index), self.hide)
        self.original = BA.hidden(BA.placed(ba.original, self.pos, self.yaw), self.hide)
        self._poses = {0: (self.painted, self.original)}
        self.hints = mv.get("hints") or {}
        try:
            self.what = BA._model_words(ba.id, "{what}")
        except Exception:
            self.what = BA.WHAT.get(ba.id, "")
        self.head = BA.head_region(ba.id)
        self.upper = ("band", 0.0, 0.5)

    def region(self, name):
        return {"head": self.head, "upper": self.upper}.get(name, name) if isinstance(name, str) else name

    def cameras(self, specs, size):
        """[(label, render.Camera)] for (label, region, yaw, pitch) specs; region "all": the whole model framed (as
        battleatlas' orbit views), "head", "upper" or any battleatlas.region_points region (framed close)."""
        out = []
        for label, region, yaw, pitch in specs:
            if region == "all":
                got = BA.orbit_views(self.painted, [(label, yaw, pitch)], [], size=size, facing=self.yaw)
            else:
                got = BA.orbit_views(self.painted, [], [(label, self.region(region), yaw, pitch)], size=size,
                                     facing=self.yaw)
            out += got
        return out

    def backdrop(self, cam, background):
        if background == "scene":
            return BA.scene_behind(self.sid, cam)
        if background == "sky":
            return BA.sky_backdrop(self.sid, self.cam0, (cam.width, cam.height))
        return plain_backdrop(cam.width, cam.height)

    def meshes(self, animation=0):
        """(painted mesh, original mesh) placed, in a pose: 0 as painted (the moving parts left out, as paint() has
        them), else the first frame of that animation with every part (Zolom's tongue out in animation 10)."""
        animation = int(animation or 0)
        if animation not in self._poses:
            from . import battle
            m = BA.posed_as(self.ba, animation)
            o = battle.load_model(self.id, animation, weapon=self.ba.original.weapon)
            self._poses[animation] = (BA.atlas_only(BA.placed(m, self.pos, self.yaw), self.ba.atlas.index),
                                      BA.placed(o, self.pos, self.yaw))
        return self._poses[animation]

    def draw(self, image, cam, background="plain", animation=0):
        """(RGB, raster, the background's depth) of the original (image None: as the game draws it) or the model
        showing a painted texture, over a background, in a pose (meshes())."""
        painted, original = self.meshes(animation)
        m = original if image is None else painted
        r = render.rasterize(m, cam)
        fg = render.shade(m, r, modulate=True) if image is None else paintmod.shade(m, r, [image])
        bg = self.backdrop(cam, background)
        return BA.composite(fg, r, bg), r, bg[1]

    def placed_camera(self, cam):
        """A camera round the model where battle.load_model has it (the Studio's viewer: model space) moved with the
        model to where the check and the touch-up have it (battleatlas.placed: turned, then moved)."""
        from . import battle
        R = battle.rotation(0, self.yaw, 0)
        eye = R @ np.asarray(cam.eye, float) + np.asarray(self.pos, float)
        target = R @ np.asarray(cam.target, float) + np.asarray(self.pos, float)
        return render.Camera(eye=eye, target=target, up=R @ np.asarray(cam.up, float), fov_y=cam.fov_y,
                             width=cam.width, height=cam.height, near=cam.near, focal=cam.focal)

    def touch_views(self):
        """The views a version can be touched up in: the resting battle camera (the scene behind), the turntable's
        eight sides and its two of the head (battleatlas.turntable's angles, bigger; the battle's sky behind, as
        paint()'s round views)."""
        out = [View("battle", "in battle", BA.battle_views(self.id, self.formation, BA.VIEW_SIZE)[0][1], "scene")]
        specs = [(TURN_LABELS[y], "all", y, 12) for y in sorted(TURN_LABELS)]
        for (label, cam), y in zip(self.cameras(specs, TOUCH_SIZE), sorted(TURN_LABELS)):
            out.append(View(f"yaw{y:03d}", label, cam, "sky"))
        heads = self.cameras([("head, a little from the left", "head", 20, 10),
                              ("head, from the right", "head", -60, 10)], TOUCH_SIZE)
        for i, (label, cam) in enumerate(heads):
            out.append(View(f"head{i + 1}", label, cam, "sky"))
        return out

    def lock(self, image, mode):
        return BA.colour_lock(self.ba, image, mode)

    def shoulder_views(self):
        """Each shoulder close up, for the check (a party member's: its upper arms from PARTY_BONES, each with the
        chest, bone 1), or none."""
        if not BA.is_party(self.id):
            return []
        bones = BA.PARTY_BONES.get(BA.PARTY_ALIAS.get(self.id, self.id), {})
        return shoulder_specs((bones.get("arms") or [])[::3] + (bones.get("gun") or [])[:1])


def shoulder_specs(uppers, chest=1):
    """The check's views of each shoulder close up: the chest with one upper arm, from the front turned that way."""
    labels = ("one shoulder, close", "the other shoulder, close")
    return [(labels[min(i, 1)], ("bones", [chest, u]), 35 if i == 0 else -35, 15) for i, u in enumerate(uppers[:2])]


class Field:
    """A field model (fieldatlas.FieldAtlas) as the check draws it: standing as painted, on a plain background."""
    kind = "field"

    def __init__(self, fa):
        from . import fieldatlas as F
        self.F, self.fa, self.id, self.atlas = F, fa, fa.id, fa.atlas
        self.painted = BA.atlas_only(fa.seen(fa.posed), fa.atlas.index)
        self.original = fa.seen(fa.original)
        self.hints = F.HINTS
        self.what = F.WHAT.get(fa.name, "a small chibi JRPG character")
        rc = F.recipe(fa.name)
        self.head = ("bones", rc.head) if rc.head else ("band", 0.0, 0.3)
        self.upper = ("band", 0.25, 0.65)  # chibi: the head is the top half

    def region(self, name):
        return {"all": ("body",), "head": self.head, "upper": self.upper}.get(name, name) if isinstance(name, str) else name

    def cameras(self, specs, size):
        return self.F.views(self.painted, [(label, self.region(region), yaw, pitch)
                                           for label, region, yaw, pitch in specs], size)

    def meshes(self, animation=0):
        return self.painted, self.original

    def draw(self, image, cam, background="plain", animation=0):
        m = self.original if image is None else self.painted
        r = render.rasterize(m, cam)
        fg = self.F.original_shade(m, r) if image is None else paintmod.shade(m, r, [image])
        bg = plain_backdrop(cam.width, cam.height)
        return BA.composite(fg, r, bg), r, bg[1]

    def touch_views(self):
        specs = [(TURN_LABELS[y], "all", y, 20) for y in sorted(TURN_LABELS)]
        out = [View(f"yaw{y:03d}", label, cam, "plain") for (label, cam), y in zip(self.cameras(specs, TOUCH_SIZE),
                                                                                    sorted(TURN_LABELS))]
        heads = self.cameras([("head, a little from the left", "head", 25, 10),
                              ("head, from the right", "head", -60, 10)], TOUCH_SIZE)
        return out + [View(f"head{i + 1}", label, cam, "plain") for i, (label, cam) in enumerate(heads)]

    def lock(self, image, mode):
        return self.F.colour_lock(self.fa, image, mode)

    def shoulder_views(self):
        rc = self.F.recipe(self.fa.name)
        return shoulder_specs((rc.arms or [])[::3])


def battle_model(id, version, formation=None):
    """(Battle adapter, the version's painted texture RGBA) of a battle 2D version (battleatlas' build folder)."""
    d = BA.version_dir(id, version)
    ba = BA.build_of(id, version)
    img = np.asarray(Image.open(d / f"{ba.name}.png").convert("RGBA"))
    return Battle(ba, formation), img


def field_model(id, version):
    from . import fieldatlas as F
    d = F.version_dir(id, version)
    fa = F.build_of(id, version)
    img = np.asarray(Image.open(d / "preview" / "atlas.png").convert("RGBA"))
    return Field(fa), img


# --- the AI check -----------------------------------------------------------------------------------------------------

# (label, region, yaw from the front, pitch): all round, the head close up, the shoulders. The labels are the recipe's
# own where it has words for them (battleatlas' hints), so those go with the question. A model whose arms the recipe
# knows (battleatlas.PARTY_BONES, fieldatlas' recipes) is also looked at shoulder by shoulder (shoulder_views): from
# further away the AI counted Cloud's bare shoulder as a shoulder pad in the original too.
CHECK_VIEWS = [("front, turned left", "all", 35, 8), ("front, turned right", "all", -35, 8),
               ("left side", "all", 90, 8), ("right side", "all", -90, 8), ("back", "all", 180, 12),
               ("head, front", "head", 0, 5), ("head, turned left", "head", 50, 8),
               ("head, turned right", "head", -50, 8),
               ("shoulders, front", "upper", 0, 10), ("shoulders, back", "upper", 180, 15)]
CHECK_SIZE = (512, 512)
CHECK_TOKENS = 120
# Each pair is asked two things. COMPARE: the two pictures compared in words (what's added, the counts, the colours).
# COUNT: some parts counted in each picture, compared here (more in the painting: invented). Measured on versions with
# known problems (docs/models-plan.md, "The AI check"): COMPARE alone flagged a view or two of clean versions
# ("markings on the face", "a mouth and teeth" where the original's are faint) and many views of broken ones; COUNT's
# "more in picture 2" never fired on a clean version and caught Zolom's invented open mouths and tongues.
CHECK_ASK = (
    "Picture 1 and picture 2 show the same 3D character from a video game, from the same angle. Picture 1 is the "
    "character as the game draws it. Picture 2 is the same character after an AI repainted it as a hand-drawn 2D "
    "anime drawing. The repaint may change the style (flat colours, painted shadows, outlines, cleaner shapes), but it "
    "must not add anything, remove anything or change the colour of any part.\n{notes}"
    "Compare picture 2 with picture 1 and answer in exactly three lines:\n"
    "EXTRA: does picture 2 show anything that picture 1 doesn't have, for example armour, a shoulder pad, eyes, a "
    "mouth, teeth, a tongue, straps, or markings and patches? Write 'no', or 'yes:' and what and where in a few words.\n"
    "COUNT: does picture 2 have the same number of eyes, arms, hands and shoulder pads as picture 1? Write 'same', or "
    "'different:' and what.\n"
    "COLOURS: is each part (hair, skin, clothes, armour, scales) the same colour in both, ignoring shading and "
    "brightness? Write 'same', or 'different:' and which part.")
COUNT_ASK = (
    "Picture 1 and picture 2 show the same character from a video game, from the same angle: picture 1 as the game "
    "draws it, picture 2 repainted by an AI as a 2D drawing.\n"
    "Count what you can see in each picture and answer in exactly two lines, numbers only:\n"
    "PICTURE 1: shoulder pads=N, open mouths=N, tongues=N, heads=N, arms=N\n"
    "PICTURE 2: shoulder pads=N, open mouths=N, tongues=N, heads=N, arms=N")
COUNT_PARTS = ("shoulder pads", "open mouths", "tongues", "heads", "arms")
COUNT_WORDS = {"shoulder pads": "an extra shoulder pad", "open mouths": "an open mouth", "tongues": "a tongue",
               "heads": "an extra head", "arms": "an extra arm"}
# What fails a version: a part counted more often in the painting than in the original in any view (COUNT), or
# COMPARE finding something added (or a different count) in at least COMPARE_AGREE views. The rest (COMPARE in fewer
# views; colours: colour_lock has put the game's own hues back, what's left is mostly light and shade) is reported,
# not failed.
COMPARE_AGREE = 4
# SHOULDERS (models whose arms the recipe knows, on the views that show the shoulders): what covers each shoulder,
# asked of each picture, compared here. Cloud's invented pauldron got past COMPARE and COUNT (the AI took his bare
# shoulder in the game's low-detail model for a pad); asked what is on each shoulder, it says skin there. Bare skin or
# cloth in the original and armour in the painting, in SHOULDER_AGREE views or more, fails it.
SHOULDER_ASK = (
    "Picture 1 and picture 2 show the same character from a video game, from the same angle: picture 1 as the game "
    "draws it, picture 2 repainted by an AI as a 2D drawing.\n"
    "Look at the character's two shoulders in each picture. What is on each one: bare skin, cloth, or armour (a "
    "metal shoulder pad)? Answer in exactly two lines:\n"
    "PICTURE 1: shoulder on the left of the picture=skin|cloth|armour|hidden, shoulder on the right of the "
    "picture=skin|cloth|armour|hidden\n"
    "PICTURE 2: shoulder on the left of the picture=skin|cloth|armour|hidden, shoulder on the right of the "
    "picture=skin|cloth|armour|hidden")
SHOULDER_VIEWS = ("shoulders, front", "head, front", "front, turned right", "one shoulder, close",
                  "the other shoulder, close")
SHOULDER_AGREE = 2
# COLOURS in this share of the views or more fails it too ("the colours changed"): with colour_lock most of the
# remarks are light and shade, a view or few; a version whose colours are all off (painted without the lock, or a
# painting that doesn't fit the model any more) gets them from nearly every view.
COLOUR_SHARE = 0.8
_KEYS = {"extra": ("extra",), "count": ("count",), "colours": ("colours", "colors", "colour", "color")}
_BAD = {"extra": ("yes",), "count": ("different", "no"), "colours": ("different", "no")}


def check_views(model):
    return CHECK_VIEWS + list(getattr(model, "shoulder_views", lambda: [])())


def check_pairs(model, image, views=None, size=CHECK_SIZE):
    """[(label, original RGB, painted RGB)] from the check's angles (check_views), on a plain background."""
    out = []
    for label, cam in model.cameras(check_views(model) if views is None else views, size):
        o, _, _ = model.draw(None, cam)
        p, _, _ = model.draw(image, cam)
        out.append((label, o, p))
    return out


def notes(model, label):
    """The model's own words and the recipe's for this view, for the question."""
    bits = []
    if model.what:
        bits.append(f"The character is {model.what}.")
    hint = model.hints.get(label)
    if hint:
        bits.append(f"From this angle: {hint}")
    return (" ".join(bits) + "\n") if bits else ""


def parse_answer(text):
    """COMPARE's answer: {"extra"|"count"|"colours": None (fine) or the reason ("" when none was given), "answered":
    the keys found}."""
    import re
    out, found = {k: None for k in _KEYS}, []
    for line in str(text).splitlines():
        m = re.match(r"\s*[\d.)*-]*\s*\**\s*([A-Za-z]+)\**\s*[:\-]\s*(.*)", line)
        if not m:
            continue
        key = next((k for k, names in _KEYS.items() if m.group(1).lower() in names), None)
        if key is None or key in found:
            continue
        found.append(key)
        rest = m.group(2).strip()
        word = re.match(r"[A-Za-z]+", rest)
        word = word.group(0).lower() if word else ""
        if word in _BAD[key]:
            reason = re.sub(r"^[A-Za-z]+\s*[:,.\-]?\s*", "", rest).strip().rstrip(".")
            out[key] = reason
    out["answered"] = found
    return out


def parse_counts(text):
    """COUNT's answer: ({part: n} for picture 1, for picture 2), or (None, None) when it can't be read. Its lines come
    as "PICTURE 1: shoulder pads=1, ..." or as bare numbers in COUNT_PARTS' order."""
    import re
    rows = []
    for line in str(text).splitlines():
        body = re.sub(r"^\s*\**\s*(PICTURE\s*)?[12]\s*\**\s*:", "", line, flags=re.I)
        nums = [int(x) for x in re.findall(r"(?<![\w.])(\d+)(?![\w.])", re.sub(r"[a-z ]+=", " ", body, flags=re.I))]
        if len(nums) >= len(COUNT_PARTS):
            rows.append(dict(zip(COUNT_PARTS, nums[:len(COUNT_PARTS)])))
        if len(rows) == 2:
            return rows[0], rows[1]
    return None, None


def parse_shoulders(text):
    """SHOULDERS' answer: [[picture 1's left, right], [picture 2's left, right]] ("skin", "cloth", "armour",
    "hidden"), or None."""
    import re
    rows = []
    for line in str(text).splitlines():
        w = re.findall(r"\b(skin|cloth|armou?r|metal|hidden)\b", line, re.I)
        if len(w) >= 2:
            rows.append([x.lower().replace("armor", "armour").replace("metal", "armour") for x in w[:2]])
    return rows[:2] if len(rows) >= 2 else None


def _upload(comfy, img, name):
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / name
    Image.fromarray(np.ascontiguousarray(img)).save(p)
    return comfy.upload(p, "ff7studio_check")


def ask(url, items, max_length=CHECK_TOKENS, cancelled=None, tag="p2dcheck"):
    """The describer (comfy.CAPTION_CLIP) on [(pictures [RGB, same size], question)]: one answer each, all in one
    ComfyUI prompt (the model loads once). Pictures go in as a batch (several images in one question)."""
    from .. import comfy as C
    comfy = C.Comfy(url)
    if not comfy.online():
        raise C.ComfyError(f"ComfyUI isn't reachable at {url}")
    clip, clip_type = C.CAPTION_CLIP
    wf = {"clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": clip_type}}}
    uploaded = {}
    for i, (imgs, question) in enumerate(items):
        node = None
        for j, img in enumerate(imgs):
            name = uploaded.get(id(img))
            if name is None:  # (a picture asked about twice is uploaded once)
                name = uploaded[id(img)] = _upload(comfy, img, f"{tag}_{i}_{j}.png")
            wf[f"img_{i}_{j}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            if node is None:
                node = [f"img_{i}_{j}", 0]
            else:  # several pictures: one batch (the describer takes each as its own image)
                wf[f"batch_{i}_{j}"] = {"class_type": "ImageBatch", "inputs": {"image1": node,
                                                                               "image2": [f"img_{i}_{j}", 0]}}
                node = [f"batch_{i}_{j}", 0]
        wf[f"gen_{i}"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["clip", 0], "prompt": question, "max_length": max_length, "sampling_mode": "off",
            "image": node, "thinking": False}}
        wf[f"show_{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"gen_{i}", 0]}}
    if not items:
        return []
    outs = comfy._execute(wf, None, cancelled or (lambda: False))["outputs"]
    return ["".join(outs.get(f"show_{i}", {}).get("text", [])).strip() for i in range(len(items))]


def _short(text, n=110):
    r = " ".join(str(text or "").split())
    return r if len(r) <= n else r[:n - 3].rsplit(" ", 1)[0] + "…"


def _and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def verdict(results, agree=COMPARE_AGREE):
    """(ok, reason, problems) from the views' readings. Fails: a part counted more in the painting (any view);
    COMPARE finding something added or a different count in `agree` views or more; armour on a shoulder bare in the
    original in SHOULDER_AGREE views or more; the colours different in COLOUR_SHARE of the views. problems: every
    remark in plain words, {"view", "kind" ("more", "shoulder", "extra", "count", "colours"), "text", "fails"}."""
    problems = []
    compare_views = [r["label"] for r in results if r.get("extra") is not None or r.get("count") is not None]
    compare_fails = len(compare_views) >= agree
    padded = [r["label"] for r in results if r.get("armoured")]
    colour_views = [r["label"] for r in results if r.get("colours") is not None]
    colour_fails = bool(results) and len(colour_views) >= COLOUR_SHARE * len(results)
    for res in results:
        if res.get("armoured"):
            problems.append({"view": res["label"], "kind": "shoulder", "fails": len(padded) >= SHOULDER_AGREE,
                             "text": f"an extra shoulder pad, where the original's shoulder is bare ({res['label']})"})
        more = res.get("more") or []
        if more:
            problems.append({"view": res["label"], "kind": "more", "fails": True,
                             "text": f"{_and(COUNT_WORDS[k] for k in more)} ({res['label']})"})
        for key in ("extra", "count", "colours"):
            if res.get(key) is None:
                continue
            what = {"extra": "something added", "count": "a different number of parts",
                    "colours": "a colour changed"}[key]
            fails = colour_fails if key == "colours" else compare_fails
            problems.append({"view": res["label"], "kind": key, "fails": fails,
                             "text": f"{_short(res[key]) or what} ({res['label']})"})
    # (the reason: a failing one, a counted part first, from a view of the head where there is one: a mouth or a
    # tongue reads best there)
    order = {"more": 0, "shoulder": 1, "extra": 2, "count": 2, "colours": 3}
    problems.sort(key=lambda p: (not p["fails"], order[p["kind"]], "head" not in p["view"]))
    fails = [p for p in problems if p["fails"]]
    reason = ""
    if fails:
        reason = fails[0]["text"]
        if fails[0]["kind"] == "colours":
            reason = f"the colours changed in {len(colour_views)} of {len(results)} views, e.g. {reason}"
        elif fails[0]["kind"] in ("extra", "count") and len(compare_views) > 1:
            reason += f", and something in {len(compare_views) - 1} more view{'s' if len(compare_views) > 2 else ''}"
    return not fails, reason, problems


def check_sheet(pairs, results, path, cell=256):
    """A picture of what the AI looked at: per view the original and the painted model, a bar under each pair
    (red: something that fails it, amber: a remark, green: nothing) and the view's name."""
    rows, per = [], 2
    for i in range(0, len(pairs), per):
        row = []
        for (label, o, p), res in zip(pairs[i:i + per], results[i:i + per]):
            a = cv2.resize(o, (cell, cell), interpolation=cv2.INTER_AREA)
            b = cv2.resize(p, (cell, cell), interpolation=cv2.INTER_AREA)
            bad = bool(res.get("fails"))
            warn = any(res.get(k) is not None for k in ("extra", "count", "colours")) or res.get("armoured")
            col = (200, 60, 60) if bad else (215, 160, 40) if warn else (60, 170, 90)
            bar = np.zeros((22, 2 * cell, 3), np.uint8)
            bar[:] = col
            im = Image.fromarray(np.vstack([np.hstack([a, b]), bar]))
            ImageDraw.Draw(im).text((6, cell + 5), label, fill=(255, 255, 255))
            row.append(np.asarray(im))
        while len(row) < per:
            row.append(np.zeros_like(row[0]))
        rows.append(np.hstack([np.pad(r, ((0, 6), (0, 6), (0, 0))) for r in row]))
    Image.fromarray(np.vstack(rows)).save(path, quality=88)


def check(model, image, url=BA.DEFAULT_URL, out_dir=None, cancelled=None, views=None, ask_fn=None):
    """The AI check of a painted texture on a model (Battle or Field): the painted model against the original from
    the check's angles (check_views), each pair asked CHECK_ASK and COUNT_ASK (verdict()). Returns {"ok", "reason",
    "problems", "views": [per view: label, the answers, their reading], "seconds", "model"}; with out_dir, also
    out_dir/check.json and out_dir/preview/check.jpg (what the AI looked at). ask_fn: ask()'s stand-in (tests)."""
    from .. import comfy as C
    t0 = time.time()
    pairs = check_pairs(model, image, views)
    shoulders = bool(list(getattr(model, "shoulder_views", lambda: [])()))
    items, where = [], []  # where: per view the index of its answers (COMPARE, COUNT, SHOULDERS or None)
    for label, o, p in pairs:
        at = len(items)
        items.append(([o, p], CHECK_ASK.format(notes=notes(model, label))))
        items.append(([o, p], COUNT_ASK))
        asks_shoulders = shoulders and label in SHOULDER_VIEWS
        if asks_shoulders:
            items.append(([o, p], SHOULDER_ASK))
        where.append((at, at + 1, at + 2 if asks_shoulders else None))
    answers = (ask_fn or ask)(url, items, cancelled=cancelled)

    def got(i):
        return answers[i] if i is not None and i < len(answers) else ""
    results = []
    for (label, _, _), (a, b, c) in zip(pairs, where):
        text, counted, covered = got(a), got(b), got(c)
        res = parse_answer(text)
        c1, c2 = parse_counts(counted)
        more = [p for p in COUNT_PARTS if c1 and c2 and c2.get(p, 0) > c1.get(p, 0)]
        sh = parse_shoulders(covered) if c is not None else None
        armoured = bool(sh) and any(o in ("skin", "cloth") and n == "armour" for o, n in zip(sh[0], sh[1]))
        results.append({"label": label, "answer": text, "counted": counted,
                        **{key: res[key] for key in ("extra", "count", "colours")}, "answered": res["answered"],
                        "counts": [c1, c2] if c1 else None, "more": more,
                        **({"shoulders": sh, "covered": covered, "armoured": armoured} if c is not None else {})})
    ok, reason, problems = verdict(results)
    for r in results:
        r["fails"] = any(p["fails"] and p["view"] == r["label"] for p in problems)
    unread = [r["label"] for r in results if len(r["answered"]) < 2 and not r["counts"]]
    out = {"ok": ok, "reason": reason, "problems": problems, "views": results, "unread": unread,
           "seconds": round(time.time() - t0, 1), "model": C.CAPTION_CLIP[0], "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "rule": {"compare_agree": COMPARE_AGREE, "shoulder_agree": SHOULDER_AGREE, "colour_share": COLOUR_SHARE}}
    if out_dir is not None:
        out_dir = Path(out_dir)
        (out_dir / "preview").mkdir(parents=True, exist_ok=True)
        check_sheet(pairs, results, out_dir / "preview" / "check.jpg")
        save_check(out_dir, out)
    return out


def save_check(out_dir, result):
    p = Path(out_dir) / "check.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1), encoding="utf-8")
    tmp.replace(p)


def load_check(out_dir):
    try:
        return json.loads((Path(out_dir) / "check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- views stored with a version ----------------------------------------------------------------------------------------

VIEWS_FILE = "views.json"


def save_views(out_dir, views):
    p = Path(out_dir) / VIEWS_FILE
    p.write_text(json.dumps({"views": [v.json() for v in views]}, indent=1), encoding="utf-8")


def load_views(out_dir):
    try:
        return [View.of(d) for d in json.loads((Path(out_dir) / VIEWS_FILE).read_text(encoding="utf-8"))["views"]]
    except (OSError, ValueError, KeyError):
        return None


def views_of(model, out_dir):
    """The version's touch-up views: views.json when it has one (stored when it was made), else worked out now and
    stored (the cameras then are the code's of today: the same while battleatlas' framing hasn't changed)."""
    got = load_views(out_dir)
    if got:
        return got
    got = model.touch_views()
    save_views(out_dir, got)
    return got


# --- the touch-up -----------------------------------------------------------------------------------------------------

TOUCH_UNIT = 256  # touchup.repaint's "original pixel": a view's longest side over this (1024 px: 4, its marks joined
                  # within 96 px, crops at least 512 px, faded over 12 px)


def touch_settings(look, strength, scene, hint="", seed=None):
    """The look's settings for repainting marked parts of a view (the Restyler's for model views: no enlarging, no
    detail pass), the version's words as its description, the user's hint first (touchup.hint_prompt)."""
    from . import restyle
    from .. import touchup
    s = dict(restyle.look_settings(look), out_scale=1, detail_pass=False, input_upscaler="", input_scale=0, tries=1,
             retry=False)
    for k in ("text_mask", "text_keep"):
        s.pop(k, None)
    s["denoise"] = float(strength)
    s["scene"] = scene or ""
    if seed is not None:
        s["seed"] = int(seed)
    return touchup.hint_prompt(s, hint)


def repaint_from(render, source, mask, scale, paint, progress=lambda i, n: None):
    """ff7bg/touchup.py's repaint (its crops, joins, fades and colour match), with the AI starting inside the marks
    from `source` instead of the render: (render with each marked area repainted, areas). paint(ctx crop, source crop,
    model mask, gen size) -> the crop repainted inside the model mask. Here the source is the model as the game colours
    it (the unpainted texture, as paint() shows the AI), so what the painting invented there isn't what the AI
    starts from: v1 of the touch-up started from the painting and turned Cloud's invented grey pauldron into a blue
    and gold one."""
    from .. import touchup as T
    out = render.copy()
    H, W = out.shape[:2]
    boxes = T.areas(mask, scale)
    f = max(2.0, T.FEATHER * scale)
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        progress(i, len(boxes))
        bw, bh = x2 - x1, y2 - y1
        mx = max(T.MIN_CONTEXT * scale, bw * T.CONTEXT, (T.MIN_CROP * scale - bw) / 2)
        my = max(T.MIN_CONTEXT * scale, bh * T.CONTEXT, (T.MIN_CROP * scale - bh) / 2)
        X1, Y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
        X2, Y2 = int(min(W, x2 + mx)), int(min(H, y2 + my))
        crop, src, m = out[Y1:Y2, X1:X2], source[Y1:Y2, X1:X2], mask[Y1:Y2, X1:X2]
        ch, cw = crop.shape[:2]
        if cw < 8 or ch < 8:
            continue
        k = math.sqrt(T.CROP_MP * 1e6 / (cw * ch))
        gw, gh = max(256, round(cw * k / 32) * 32), max(256, round(ch * k / 32) * 32)
        marks = (m > 0.5).astype(np.uint8)
        pasted = cv2.dilate(marks, T._disc(f))
        weight = np.clip(cv2.GaussianBlur(pasted.astype(np.float32), (0, 0), f / 2), 0, 1)
        model = cv2.GaussianBlur(cv2.dilate(marks, T._disc(2.5 * f)).astype(np.float32), (0, 0), f / 2)
        up = cv2.INTER_AREA if k < 1 else cv2.INTER_LANCZOS4
        new = np.asarray(paint(cv2.resize(crop, (gw, gh), interpolation=up), cv2.resize(src, (gw, gh), interpolation=up),
                               np.round(np.clip(cv2.resize(model, (gw, gh)), 0, 1) * 255).astype(np.uint8),
                               (gw, gh)), np.float32)
        if new.shape[:2] != (ch, cw):
            new = cv2.resize(new, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
        ring = (cv2.dilate(pasted, T._disc(3 * f)) > 0) & (model < 0.05)
        if ring.sum() > 100:
            new += np.clip(crop[ring].astype(np.float32).mean(0) - new[ring].mean(0), -T.MAX_SHIFT, T.MAX_SHIFT)
        a = weight[..., None]
        out[Y1:Y2, X1:X2] = np.round(crop * (1 - a) + np.clip(new, 0, 255) * a).astype(np.uint8)
    return out, len(boxes)


def repaint_marks(before, source, marks, s, url=BA.DEFAULT_URL, cancelled=None, progress=None, paint_fn=None):
    """(the view with its marked areas repainted, areas): repaint_from with the look's masked repaint (the states
    pass's workflow) at s's strength, starting from `source` inside the marks. marks: 0..1 at the view's size.
    paint_fn(ctx crop, source crop, model mask, gen size): the repaint's stand-in (tests)."""
    from .. import comfy as C
    from . import restyle
    H, W = before.shape[:2]
    if paint_fn is None:
        if C._family(s).get("upscale"):
            raise ValueError("touch-up needs a look that paints: this look's workflow only enlarges")
        comfy = C.Comfy(url)
        if not comfy.online():
            raise C.ComfyError(f"ComfyUI isn't reachable at {url}")
        refs = [comfy.upload(restyle.STYLES / r, "ff7studio_styles") for r in s.get("style_refs", [])
                if (restyle.STYLES / r).exists()]
        stop = cancelled or (lambda: False)

        def paint_fn(ctx, src, model_mask, size):
            names = [_upload(comfy, a, f"p2dtouch_{k}.png") for k, a in
                     (("ctx", ctx), ("orig", src), ("mask", np.dstack([model_mask] * 3)))]
            h, w = ctx.shape[:2]
            wf = C.build_states_workflow(s, *names, size[0], size[1], w, h, "ff7studio/models/p2dtouch", refs,
                                         denoise=s["denoise"])
            import io
            res = np.asarray(Image.open(io.BytesIO(comfy.run(wf, None, stop))).convert("RGB"))
            return res if res.shape[:2] == (h, w) else cv2.resize(res, (w, h), interpolation=cv2.INTER_LANCZOS4)
    scale = max(W, H) / TOUCH_UNIT
    return repaint_from(before, source, marks.astype(np.float32), scale, paint_fn,
                        progress=progress or (lambda i, n: None))


def texel_points(model_mesh, atlas, tris):
    """(ys, xs, points (n, 3)) of the atlas texels of triangles `tris`: where each texel's middle is on the mesh (its
    triangle's corners weighted by where the texel sits in the triangle's u, v; v down, as the atlas image)."""
    sel = np.isin(atlas.tri, tris)
    ys, xs = np.nonzero(sel)
    t = atlas.tri[ys, xs]
    uv = np.nan_to_num(model_mesh.uv[t].astype(np.float64)) * atlas.size
    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    den = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    px, py = xs + 0.5 - uv[:, 0, 0], ys + 0.5 - uv[:, 0, 1]
    w1 = (px * d2[:, 1] - py * d2[:, 0]) / den
    w2 = (py * d1[:, 0] - px * d1[:, 1]) / den
    b = np.clip(np.stack([1 - w1 - w2, w1, w2], -1), 0, None)
    b /= np.maximum(b.sum(1, keepdims=True), 1e-12)
    pts = (b[:, :, None] * model_mesh.pos[t].astype(np.float64)).sum(1)
    return ys, xs, pts


def project(model, image, cam, view_img, sel, bg_depth=None, animation=0):
    """The painted texture with a view's pixels `sel` (bool) put back onto it through battleatlas.paint's projection
    (paint.Painter.add, weighted by projectable()'s edge weights: nothing from the model's outline or occlusion edges,
    nothing the background is in front of; the gaps between its samples filled from around, Painter.fill_holes).
    Only texels that show in those pixels change: of the triangles the pixels are on, the texels whose middle the
    camera sees inside `sel` (a texel the samples missed takes the view's colour there). animation: the model's pose
    (Battle.meshes). Returns (texture, the texels changed (bool))."""
    m = model.meshes(animation)[0]
    r = render.rasterize(m, cam)
    vis, edge = BA.projectable(r, bg_depth)
    w = np.where(sel & vis, edge, 0).astype(np.float32)
    w = np.where(w >= BA.EDGE_MIN, w, 0).astype(np.float32)
    if not (w > 0).any():
        return image, np.zeros(image.shape[:2], bool)
    P = paintmod.Painter(m, 1, [image])
    P.add(cam, r, view_img, w)
    P.fill_holes()
    # which texels: their middles seen by the camera in the marked pixels (not hidden: the raster's depth there)
    ys, xs, pts = texel_points(m, model.atlas, np.unique(r.tri[w > 0]))
    c = cam.to_camera(pts)
    pix = cam.project(c)
    H, W = sel.shape
    ix = np.floor(pix[:, 0]).astype(int)
    iy = np.floor(pix[:, 1]).astype(int)
    inside = (ix >= 0) & (iy >= 0) & (ix < W) & (iy < H) & (c[:, 2] > cam.near)
    ix, iy = np.clip(ix, 0, W - 1), np.clip(iy, 0, H - 1)
    # (in sel and on the model there, edges included: their colour comes from the samples around, fill_holes)
    seen = inside & (np.abs(r.depth[iy, ix] - c[:, 2]) <= 0.01 * np.maximum(c[:, 2], 1e-6)) & sel[iy, ix] & vis[iy, ix]
    ys, xs, ix, iy = ys[seen], xs[seen], ix[seen], iy[seen]
    have = P.wsum[0][ys, xs] > 0
    paint = P.acc[0][ys, xs] / np.maximum(P.wsum[0][ys, xs], 1e-12)[:, None]
    col = np.where(have[:, None], paint, view_img[iy, ix].astype(np.float64))
    out = image.copy()
    out[ys, xs, :3] = np.clip(np.round(col), 0, 255).astype(np.uint8)
    take = np.zeros(image.shape[:2], bool)
    take[ys, xs] = True
    return atlasmod.fill(out, model.atlas.used()), take


def outline(img, mask, colour=(255, 70, 70)):
    """img with the outline of mask drawn on it."""
    m = (mask > 0).astype(np.uint8)
    edge = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    out = img.copy()
    out[edge] = colour
    return out


def touch_up(model, image, view, marks, look, strength, scene, hint="", lock=None, url=BA.DEFAULT_URL, seed=None,
             cancelled=None, progress=None, paint_fn=None):
    """A painted texture touched up in one view: the view rendered from it, the marked areas repainted
    (repaint_marks: the AI starts there from the model as the game colours it, the painting around it), the pixels that
    changed projected back (project). lock: the version's colour lock mode
    (colour_lock: the game's own hues), put back on the changed texels, as make() puts it on the whole painting (with
    words too: asked for "his bare shoulder, skin, no armour", the unlocked repaint ringed Cloud's shoulder in red).
    Returns (texture, info {areas, texels, seed}, before, after)."""
    seed = new_seed() if seed is None else seed
    before, r, bg_depth = model.draw(image, view.camera, view.background, view.animation)
    source, _, _ = model.draw(model.atlas.image, view.camera, view.background, view.animation)  # the game's colours
    H, W = before.shape[:2]
    m = np.asarray(marks, np.float32)
    if m.shape != (H, W):
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)
    s = touch_settings(look, strength, scene, hint, seed)
    after, n = repaint_marks(before, source, m, s, url, cancelled, progress, paint_fn)
    if not n:
        raise ValueError("nothing was marked")
    changed = np.abs(after.astype(np.int16) - before.astype(np.int16)).max(-1) > 0
    out, take = project(model, image, view.camera, after, changed, bg_depth, view.animation)
    if lock and take.any():
        locked = model.lock(out, lock)
        out = out.copy()
        out[take] = locked[take]
        out = atlasmod.fill(out, model.atlas.used())
    info = {"areas": int(n), "texels": int(take.sum()), "seed": int(seed), "locked": bool(lock)}
    return out, info, before, after


def export_touchup(model, image, src_dir, out_dir, info_extra, before, after, marks, view):
    """A touched-up battle version: the files (battleatlas.export), the pictures (battleatlas.previews, the source's
    identity check, preview/touchup.jpg: before with the marks outlined | the touched-up view as the new texture
    draws it) and views.json, info.json from the source's with "touchup" added. Raises ValueError when
    battleatlas.check finds a problem with the files."""
    if model.kind != "battle":
        raise ValueError("touch-up versions are made for battle models only")
    ba, src_dir, out_dir = model.ba, Path(src_dir), Path(out_dir)
    (out_dir / "preview").mkdir(parents=True, exist_ok=True)
    try:
        info = json.loads((src_dir / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    for k in ("id", "model", "atlas", "parts", "files", "at", "touchup"):
        info.pop(k, None)
    info["touchup"] = info_extra
    for name in ("identity.png",):
        if (src_dir / "preview" / name).exists():
            shutil.copy2(src_dir / "preview" / name, out_dir / "preview" / name)
    BA.previews(ba, image, out_dir, model.formation)
    redrawn, _, _ = model.draw(image, view.camera, view.background, view.animation)
    m = np.asarray(marks, np.float32)
    if m.shape != before.shape[:2]:
        m = cv2.resize(m, (before.shape[1], before.shape[0]))
    sheet = np.hstack([outline(before, m > 0.5), redrawn])
    k = min(1.0, 1600 / sheet.shape[1])
    Image.fromarray(sheet).resize((round(sheet.shape[1] * k), round(sheet.shape[0] * k)), Image.LANCZOS).save(
        out_dir / "preview" / "touchup.jpg", quality=90)
    if (src_dir / VIEWS_FILE).exists():
        shutil.copy2(src_dir / VIEWS_FILE, out_dir / VIEWS_FILE)
    files = BA.export(ba, image, out_dir, info)
    probs = BA.check(ba, files)
    if probs:
        raise ValueError(f"{out_dir.name}: " + "; ".join(probs))
    return files


# --- rounding (the viewer: field models as the engine rounds them) ----------------------------------------------------

ROUND_CREASE = 70.0  # degrees: faces meeting at a sharper angle keep their edge straight (FFNx-3d's char_round)


def pn_round(m, crease=ROUND_CREASE, weld=1e-4):
    """The mesh with every triangle split in four, the middles of its edges pushed onto the curve the surface's
    smoothed normals describe (PN triangles), as FFNx-3d's char_round rounds field models (ffnx3d_shape.cpp: shading
    flows across faces meeting at under `crease` degrees; sharp and open edges stay straight, so parts don't crack).
    Per part: corners at the same place (within `weld` of the part's size) are one vertex, its normal the average of
    the faces round it. u, v and colours are interpolated linearly; per-triangle arrays repeat for the four. A
    triangle with no area (FieldAtlas.seen's collapsed twins) stays as it is, four times over."""
    from dataclasses import fields as dc_fields, replace as dc_replace
    pos = m.pos.astype(np.float64)
    T = len(pos)
    if not T:
        return m
    e1, e2 = pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0]
    fn = np.cross(e1, e2)
    area = np.linalg.norm(fn, axis=1)
    ok = area > 1e-12
    fn = np.where(ok[:, None], fn / np.maximum(area, 1e-30)[:, None], 0.0)
    span = float(np.ptp(pos.reshape(-1, 3), axis=0).max()) or 1.0
    q = np.round(pos.reshape(-1, 3) / (span * weld)).astype(np.int64)
    key = np.concatenate([np.repeat(np.asarray(m.part, np.int64), 3)[:, None], q], 1)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(T, 3)
    nv = int(vid.max()) + 1
    vn = np.zeros((nv, 3))
    for c in range(3):
        np.add.at(vn, vid[:, c], fn * area[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-30)
    # edges: the welded vertex pairs; smooth when exactly two faces share one at under the crease angle
    pairs = [(0, 1), (1, 2), (2, 0)]
    ek = np.stack([np.sort(np.stack([vid[:, a], vid[:, b]], 1), 1) for a, b in pairs], 1)  # (T, 3, 2)
    flat = ek.reshape(-1, 2)
    _, eid, cnt = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    eid = eid.reshape(T, 3)
    face_of = np.repeat(np.arange(T), 3)
    first = np.full(len(cnt), -1)
    second = np.full(len(cnt), -1)
    for i, e in zip(face_of, eid.reshape(-1)):
        if first[e] < 0:
            first[e] = i
        elif second[e] < 0:
            second[e] = i
    cos_crease = np.cos(np.radians(crease))
    two = (cnt == 2) & (second >= 0)
    smooth_e = np.zeros(len(cnt), bool)
    smooth_e[two] = (fn[first[two]] * fn[second[two]]).sum(1) > cos_crease
    smooth_e &= ok[np.maximum(first, 0)] & ok[np.maximum(second, 0)]
    mids = np.zeros((T, 3, 3))
    for k, (a, b) in enumerate(pairs):
        Pa, Pb = pos[:, a], pos[:, b]
        Na, Nb = vn[vid[:, a]], vn[vid[:, b]]
        wab = ((Pb - Pa) * Na).sum(1, keepdims=True)
        wba = ((Pa - Pb) * Nb).sum(1, keepdims=True)
        curved = (Pa + Pb) / 2 - (wab * Na + wba * Nb) / 8
        straight = (Pa + Pb) / 2
        mids[:, k] = np.where((smooth_e[eid[:, k]] & ok)[:, None], curved, straight)
    # the four: (v0, m01, m20), (m01, v1, m12), (m20, m12, v2), (m01, m12, m20); m01 = mids[0], m12 = 1, m20 = 2
    corners = [(("v", 0), ("m", 0), ("m", 2)), (("m", 0), ("v", 1), ("m", 1)), (("m", 2), ("m", 1), ("v", 2)),
               (("m", 0), ("m", 1), ("m", 2))]
    mid_of = {0: (0, 1), 1: (1, 2), 2: (2, 0)}

    def corner_values(arr, kind_k, mids_arr=None):
        kind, k = kind_k
        if kind == "v":
            return arr[:, k]
        if mids_arr is not None:
            return mids_arr[:, k]
        a, b = mid_of[k]
        return (arr[:, a].astype(np.float64) + arr[:, b].astype(np.float64)) / 2

    def split_corners(arr, mids_arr=None):
        out = np.stack([np.stack([corner_values(arr, c, mids_arr) for c in tri], 1) for tri in corners], 1)
        return out.reshape((T * 4,) + arr.shape[1:]).astype(arr.dtype)

    changes = {"pos": split_corners(pos, mids).astype(m.pos.dtype)}
    for f in dc_fields(m):
        v = getattr(m, f.name)
        if f.name == "pos" or not isinstance(v, np.ndarray) or v.shape[:1] != (T,):
            continue
        if v.ndim >= 2 and v.shape[1] == 3 and f.name in ("uv", "col"):
            changes[f.name] = split_corners(v)
        else:
            changes[f.name] = np.repeat(v, 4, axis=0)
    return dc_replace(m, **changes)


# --- the command line -------------------------------------------------------------------------------------------------

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m ff7bg.models.paint2d_extras")
    ap.add_argument("cmd", choices=("check", "pairs"))
    ap.add_argument("id")
    ap.add_argument("version")
    ap.add_argument("--field", action="store_true", help="a field model's version (fieldatlas)")
    ap.add_argument("--url", default=BA.DEFAULT_URL)
    ap.add_argument("--out", help="where the check's files go (default: the version's own folder)")
    a = ap.parse_args(argv)
    if a.field:
        from . import fieldatlas as F
        model, img = field_model(a.id, a.version)
        d = F.version_dir(a.id, a.version)
    else:
        model, img = battle_model(a.id, a.version)
        d = BA.version_dir(a.id, a.version)
    out = Path(a.out) if a.out else d
    if a.cmd == "pairs":
        pairs = check_pairs(model, img)
        out.mkdir(parents=True, exist_ok=True)
        check_sheet(pairs, [{} for _ in pairs], out / "pairs.jpg")
        print(out / "pairs.jpg")
        return 0
    res = check(model, img, a.url, out)
    print(json.dumps({k: res[k] for k in ("ok", "reason", "seconds")}, indent=1))
    for v in res["views"]:
        print(f"--- {v['label']}\n{v['answer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
