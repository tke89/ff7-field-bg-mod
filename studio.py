"""FF7 Field Studio: browse every field, restyle it with ComfyUI, and compare original vs. new
exactly as the game will compose it.

    .venv\\Scripts\\python studio.py          (then open http://127.0.0.1:8765)

Settings live in named looks (studio/looks.json), assigned to fields one by one or by name
pattern; a field can override any setting on top of its look. Every render is kept as a version
(ai/versions/<map>/<id>); the field's current version is what dist/ and the game get.
"""
import argparse
import base64
import io
import itertools
import json
import math
import os
import random
import shutil
import sys
import threading
import time
import tomllib
import traceback
import webbrowser
from collections import OrderedDict, deque
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from PIL import Image

from ff7bg import ai, looks, palettes, protect, requirements, signfix, signs, states, syw, touchup, upscalers
from ff7bg.comfy import (
    DEFAULT_SETTINGS, FAMILIES, FAMILY_PRESETS, Comfy, ComfyError, build_caption_workflow, build_states_workflow,
    build_workflow, clean_caption, edit_keep, edit_padding, tile_count, work_size,
)
from ff7bg.field import parse_background
from ff7bg.lgp import LgpArchive
from ff7bg.lzs import decompress
from ff7bg.sheets import PageCache, canvas_rect, page_tile_source, render_composite
from ff7bg.upscalers import UpscalerError, is_local
from ff7bg.versions import VersionStore

HERE = Path(__file__).resolve().parent
AI = HERE / "ai"
STUDIO = HERE / "studio"
STYLES = STUDIO / "styles"
FIELDS_META = AI / "fields"
VERSIONS = AI / "versions"
DIST = HERE / "dist" / "mods" / "Textures"
GAME_WORKDIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir"
)
LOOKS_FILE = STUDIO / "looks.json"
PREFS_FILE = AI / "prefs.json"  # the Studio's own settings: {"auto_install": bool, "syw_pack": path (optional)}
PLUGINS = STUDIO / "plugins"
LEGACY_SETTINGS = STUDIO / "settings.json"
EDIT_FAMILIES = {name for name, f in FAMILIES.items() if f.get("edit")}
KEEP_IN_GAME = 0.6  # a new version below this share of original outlines doesn't go in game by itself
FLAG_KEPT = 0.8     # below this share of outlines a field is flagged (and "retry" tries again)
REPAINT_FAMILIES = {"flux1", "krea2", "moss"}  # the ones with a repaint strength to lower on a retry
AVG_STATE_RUNS = 488 / 670  # states-pass sheets per field, game-wide (batch estimates)
# version settings shown in the version strip
SUMMARY_KEYS = ("denoise", "structure", "seed", "style_refs", "style_ref_strength", "detail_pass",
                "detail_denoise", "out_scale", "upscaler", "states_pass", "align", "work_mp", "unet", "steps",
                "cfg", "depth_lock", "input_upscaler", "input_scale", "keep_colors", "describe_scene", "tries",
                "seedvr2_unet", "seedvr2_input", "protect_text", "text_repaint", "protect_anim", "anim_repaint",
                "protect_flat", "flat_repaint")

app = Flask(__name__, static_folder=None)

# Plug-ins (studio/plugins/*.py, each with register(studio) returning its UI script's URL or None) can add job
# kinds (kind -> {"run": fn(worker, job), "est": fn(name) -> seconds}) and hear when a field's version in the
# game changes (fn(name, version id, options): a batch's options from the page's BATCH_OPTIONS, else {}).
JOB_KINDS = {}
ON_CURRENT = []
PLUGIN_SCRIPTS = []


# ---------------------------------------------------------------- project data

class Project:
    def __init__(self, lgp_path):
        self.lgp = LgpArchive(lgp_path)
        self.lgp_lock = threading.Lock()
        self.meta_lock = threading.RLock()
        self.bg_cache = OrderedDict()
        self.jobs = {}
        self._state_runs = {}
        self.ready = False
        self.status = "starting"
        FIELDS_META.mkdir(parents=True, exist_ok=True)
        self.looks = looks.LookStore(LOOKS_FILE)
        self.looks.load(LEGACY_SETTINGS)
        self.versions = VersionStore(VERSIONS, DIST)

    @property
    def comfy_url(self):
        return self.looks.data.get("comfy_url", "http://127.0.0.1:8000")

    def prepare(self):
        """Make sure every map has an AI input image and a job entry (first run only)."""
        jobs_file = AI / "jobs.json"
        if not jobs_file.exists():
            self.status = "preparing field images (first run, about a minute)"
            names = [n for n in self.lgp.names() if "." not in n and n != "maplist"]

            def fields():
                for n in names:
                    with self.lgp_lock:
                        raw = decompress(self.lgp.read(n))
                    yield n, raw

            ai.prep(fields(), AI)
        self.jobs = json.loads(jobs_file.read_text())
        self.migrate()
        self.migrate_input_upscaler()
        self.ready = True
        self.status = "ready"

    def migrate(self):
        """One time: earlier renders (ai/output, ai/history, ai/states, ai/review) become versions,
        and flat per-field overrides become layers on top of the field's look."""
        marker = VERSIONS / ".migrated"
        if marker.exists():
            return
        self.status = "moving earlier renders into the version history (one time)"
        VERSIONS.mkdir(parents=True, exist_ok=True)
        for name, job in self.jobs.items():
            meta = self.meta(name)
            new = {k: meta[k] for k in ("error", "look", "current") if k in meta}
            ov = meta.get("override")
            if ov and not ("settings" in ov or "profiles" in ov):
                # tuned for the workflow the field last rendered with
                fam = (meta.get("last_settings") or {}).get("family", "flux1")
                look_id, _ = self.looks.resolve_look(name, job["copies"])
                ov = looks.prune(looks.split_flat(ov, fam), [self.looks.get(look_id)])
            if ov and not looks.is_empty(ov):
                new["override"] = ov
            inp = AI / "input" / f"{name}.png"
            vid = self.versions.migrate(name, meta, AI, job["copies"], job.get("defaults", {}),
                                        inp.stat().st_mtime if inp.exists() else 0)
            if vid and self.versions.has_pages(name, vid):
                new["current"] = vid
            if new != meta:
                self.save_meta(name, new)
        for d in ("output", "history", "states", "review"):
            d = AI / d
            if d.exists() and not any(p.is_file() for p in d.rglob("*")):
                shutil.rmtree(d)
        marker.write_text(time.ctime())

    def migrate_input_upscaler(self):
        """One time (2026-10-21): the original art now always goes through "input_upscaler"; looks and
        field overrides that relied on the output upscaler (or "seedvr2_input") for it keep their result."""
        with self.looks.lock:
            done = self.looks.data.setdefault("migrated", [])
            if "input_upscaler" in done:
                return
            for look in self.looks.looks().values():
                looks.pin_input_upscaler(look, DEFAULT_SETTINGS)
        for name in self.jobs:
            meta = self.meta(name)
            ov = meta.get("override")
            if ov:
                look_id, _ = self.look_of(name, meta)
                if looks.pin_input_upscaler(ov, looks.resolve([self.looks.get(look_id)])):
                    self.update_meta(name, override=ov)
        with self.looks.lock:
            done.append("input_upscaler")
            self.looks.save()

    def field_raw(self, name):
        with self.lgp_lock:
            return decompress(self.lgp.read(name))

    def background(self, name):
        if name in self.bg_cache:
            self.bg_cache.move_to_end(name)
            return self.bg_cache[name]
        with self.lgp_lock:
            raw = decompress(self.lgp.read(name))
        bg = parse_background(name, raw)
        self.bg_cache[name] = bg
        if len(self.bg_cache) > 24:
            self.bg_cache.popitem(last=False)
        return bg

    # ---- per-field state (look choice, override layer, current version, last error)

    def meta(self, name):
        p = FIELDS_META / f"{name}.json"
        with self.meta_lock:
            return json.loads(p.read_text()) if p.exists() else {}

    def save_meta(self, name, meta):
        p = FIELDS_META / f"{name}.json"
        with self.meta_lock:
            if meta:
                p.write_text(json.dumps(meta, indent=1))
            elif p.exists():
                p.unlink()

    def update_meta(self, name, **changes):
        """Set fields; None removes one."""
        with self.meta_lock:
            m = self.meta(name)
            for k, v in changes.items():
                if v is None:
                    m.pop(k, None)
                else:
                    m[k] = v
            self.save_meta(name, m)
            return m

    # ---- settings

    def look_of(self, name, meta=None):
        meta = self.meta(name) if meta is None else meta
        return self.looks.resolve_look(name, self.jobs[name]["copies"], meta.get("look"))

    def layers(self, name, meta=None):
        meta = self.meta(name) if meta is None else meta
        look_id, _ = self.look_of(name, meta)
        return [self.looks.get(look_id), meta.get("override")]

    def effective(self, name, family=None):
        with self.looks.lock:
            return looks.resolve(self.layers(name), family)

    def job_settings(self, name, kind, family=None):
        s = self.effective(name, family)
        if kind == "preview":
            s.update(out_scale=min(4, s["out_scale"]), detail_pass=False, tries=1, retry=False)
        if s["out_scale"] <= 1:  # the detail pass adds texture at a size bigger than the render: none at 1x
            s["detail_pass"] = False
        return s

    def syw_pack(self):
        """The SYW Unified pack (ff7bg/syw.py): prefs' "syw_pack", else 7th Heaven's library; or None."""
        return syw.find_pack(prefs().get("syw_pack"))

    def syw_has(self, name):
        """Whether the SYW pack is there and has this field."""
        pack = self.syw_pack()
        return bool(pack and syw.index(pack).get(name.lower()))

    def syw_input(self, name):
        """The field's input (its view at entry, padded as ai/input has it) from SYW's 4x pages, or None when the
        pack or the field isn't there. Put together once (ai/input_syw)."""
        pack = self.syw_pack()
        if pack is None:
            return None
        job = self.jobs[name]
        inp = AI / "input" / f"{name}.png"
        stamp = json.dumps([job.get("defaults"), inp.stat().st_mtime if inp.exists() else 0])
        return syw.view(pack, name, self.background(name), ai.shows(job.get("defaults")), tuple(job["pad"]),
                        AI / "input_syw", stamp)

    def enlarged_input(self, name, s, size, src=None, pad=(0, 0, 0, 0), cancelled=lambda: False):
        """The field's original art enlarged to `size` by s's input upscaler, when that one runs here (ImageMagick,
        Upscayl, SYW). src: the 1x image (default the field's input); pad: the edit models' margin around it."""
        if s["input_upscaler"] == syw.NAME:
            big = self.syw_input(name)
            if big is not None:
                if any(pad):
                    big = ai._pad(big, *(syw.SCALE * p for p in pad))
                return np.asarray(Image.fromarray(big).resize(size, Image.LANCZOS))
        img = np.asarray(Image.open(src or AI / "input" / f"{name}.png").convert("RGB"))
        return upscalers.upscale(img, s["input_upscaler"], *size, cancelled)

    def input_settings(self, name, s):
        """s, with SYW's fallback upscaler where it can't be used: a field the pack doesn't have, or no pack."""
        if s.get("input_upscaler") == syw.NAME and self.syw_input(name) is None:
            return dict(s, input_upscaler=syw.FALLBACK)
        return s

    def describe(self, name, comfy, s):
        """Make (or remake) the field's description from its original art, enlarged the way workflow s
        enlarges it for the AI; returns it."""
        w, h = self.jobs[name]["canvas"][2:]
        src = AI / "input" / f"{name}.png"
        s = self.input_settings(name, s)
        if is_local(s["input_upscaler"]):  # ImageMagick / Upscayl / SYW here; the graph's resize then changes nothing
            src = AI / "input_up" / f"{name}_describe.png"
            src.parent.mkdir(exist_ok=True)
            Image.fromarray(self.enlarged_input(name, s, work_size(s, w, h))).save(src)
        text = clean_caption(comfy.run_text(build_caption_workflow(comfy.upload(src), s, w, h)))
        self.update_meta(name, caption=text)
        return text

    def scene(self, name, s, comfy):
        """The field's description for the prompt ("" unless describe_scene), made the first time."""
        if not s.get("describe_scene") or s.get("family") == "upscale":
            return ""
        return self.meta(name).get("caption") or self.describe(name, comfy, s)

    def find_signs(self, name, comfy, s):
        """Look for the field's signs (replacing the ones found before; the user's own stay); returns all."""
        w, h = self.jobs[name]["canvas"][2:]
        src = AI / "input" / f"{name}.png"
        s = self.input_settings(name, s)
        if is_local(s["input_upscaler"]):  # ImageMagick / Upscayl / SYW run here, before ComfyUI
            src = AI / "input_up" / f"{name}_describe.png"
            src.parent.mkdir(exist_ok=True)
            Image.fromarray(self.enlarged_input(name, s, work_size(s, w, h))).save(src)
        found = signs.parse_boxes(comfy.run_text(signs.build_find_workflow(comfy.upload(src), s, w, h)), w, h)
        # the user's own areas stay, and so does any area they gave words to (Find again dropped Kalm's three) or
        # fitted the corners of; new finds over those are the same signs again
        mine = [b for b in self.meta(name).get("text_boxes") or []
                if not b.get("auto") or (b.get("say") or "").strip() or b.get("quad")]
        found = [b for b in found if not any(signs._overlap(b, m) > 0.3 for m in mine)]
        self.update_meta(name, text_boxes=mine + found)
        return mine + found

    @staticmethod
    def wants_moving(s):
        """Protect moving parts: not for Upscale (nothing's repainted), nor Qwen without its depth lock (it
        shifts the picture: the pinned parts wouldn't line up)."""
        if not s.get("protect_anim") or s.get("family") == "upscale":
            return False
        return not (s.get("family") == "qwen21" and s["depth_lock"] <= 0)

    def moving_boxes(self, name, s):
        """The field's looping animations' moving parts (states.animated_boxes) when these settings protect
        them, else []."""
        job = self.jobs[name]
        if not self.wants_moving(s) or not job.get("animations"):
            return []
        defaults = {int(p): m for p, m in (job.get("defaults") or {}).items()}
        return states.animated_boxes(self.background(name), defaults, {int(p) for p in job["animations"]})

    @staticmethod
    def wants_signs(s):
        if not s.get("protect_text") or s.get("family") == "upscale":
            return False
        return not (s.get("family") == "qwen21" and s["depth_lock"] <= 0)

    def sign_boxes(self, name, s, comfy):
        """The field's sign areas when these settings protect text ([] when not), found the first time.
        Not for Qwen without its depth lock: it shifts the picture, so pinned signs wouldn't line up."""
        if not self.wants_signs(s):
            return []
        boxes = self.meta(name).get("text_boxes")
        return self.find_signs(name, comfy, s) if boxes is None else boxes

    def start_image(self, name, vid):
        """A version to render from instead of the original art: its render on the original's geometry, cut to
        the canvas, at its own scale (as its game textures were cut). Returns (RGB, scale, the states it shows)."""
        vmeta = self.versions.meta(name, vid)
        s = self.effective(name, vmeta.get("family"))
        s.update(vmeta.get("settings") or {})
        defaults = vmeta.get("rendered_defaults", {})
        main = ai.prepare_main(self.background(name), self.versions.render_path(name, vid), self.jobs[name], defaults,
                               do_align=s["align"], frame=vmeta.get("frame"))
        return main["ai_hi"], main["scale"], defaults

    def render_k(self, name, s):
        """How many times its size the AI renders this field at."""
        w, h = self.jobs[name]["canvas"][2:]
        return work_size(s, w, h)[0] // w

    def state_runs(self, name, k):
        """(ComfyUI runs, panels) the states pass needs for this field at render scale k."""
        key = (name, k)
        if key not in self._state_runs:
            bg = self.background(name)
            defaults = {int(p): m for p, m in self.jobs[name].get("defaults", {}).items()}
            self._state_runs[key] = states.count_runs(bg, canvas_rect(bg), defaults, k)
        return self._state_runs[key]

    def estimate(self, name, s, preview=False, exact_states=True):
        """Seconds on the RTX 5090 this was tuned on. Batches pass exact_states=False to use the
        game-wide average for fields whose states haven't been counted yet."""
        w, h = self.jobs[name]["canvas"][2:]
        fam = s.get("family")
        edit = fam in EDIT_FAMILIES
        no_detail = edit or fam == "upscale" or s["out_scale"] <= 1
        outside = is_local(s["upscaler"])  # ImageMagick / Upscayl, after ComfyUI
        out_px = w * h * s["out_scale"] ** 2
        pad = edit_padding(w, h, s["edit_margin"]) if edit else (0, 0, 0, 0)
        in_w, in_h = w + pad[0] + pad[2], h + pad[1] + pad[3]
        ww, wh = work_size(s, in_w, in_h)
        mp = ww * wh / 1e6
        size = mp / 1.23  # Flux.1 and Krea 2 were measured on Kalm at 1.23 MP
        sheet_share = min(1.0, states.MAX_SHEET_MP / mp)  # states sheets stay within that size
        if fam == "upscale" and outside:
            # measured with Lanczos on elm: 3.1 s at 4x, 7.7 s at 8x, with 26 state panels
            up = upscalers.seconds(s["upscaler"], w * h, out_px)
            first, tile, sheet = 2 + up, 0, 0.5 * up  # states sheets are about half a field
        elif fam == "upscale":
            # just the upscaler: an ESRGAN pass takes about a second; SeedVR2 is added below
            pre = s["upscaler"] == "seedvr2" and not s["seedvr2_input"] and s["input_upscaler"]
            first = 2 + (1 if s["upscaler"] and (s["upscaler"] != "seedvr2" or pre) else 0)
            tile, sheet = 0, first
            if s["upscaler"] == "seedvr2":  # states sheets are about half a field
                sheet += 0.5 * 3.4 * (s["out_scale"] / 4) ** 2.75
        elif fam == "krea2":
            # measured: 9 s at 8 steps without style images, 19 s with two (reference latents)
            per_step = 0.64 + 0.6 * min(len(s["style_refs"]), 3)
            first, tile, sheet = 4 + per_step * size * s["steps"], 1.0 + 0.6 * s["detail_steps"], 4 + 1.2 * per_step * s["steps"]
            if s["depth_lock"] > 0:  # the depth map, and twice the input channels; a guess until measured
                first, sheet = first + 1.5 + 0.1 * per_step * size * s["steps"], sheet + 1.5
        elif fam == "moss":
            # measured on Kalm at 1.23 MP: about 7 s with 25 steps at CFG 2.5 (the sampler always runs
            # every step, whatever the repaint strength), plus about 1 s for the depth lock
            per = 0.085 * mp * s["steps"] * (2 if s["cfg"] > 1 else 1)
            first = 1.5 + per + (1 if s["depth_lock"] > 0 else 0)
            tile, sheet = 1.0 + 0.18 * s["detail_steps"] * (2 if s["cfg"] > 1 else 1), 3 + 0.8 * per * sheet_share
        elif edit:
            evals = s["steps"] * (2 if s["cfg"] > 1 else 1)  # CFG above 1 runs the model twice per step
            if fam == "qwen21":
                # Turbo, measured: ~13 s at 1.5 MP, ~21 s at 2.9 MP (with style images)
                per = 6 * mp if "turbo" in s["unet"] else 0.25 * mp * evals  # full: ~10 s at 1.6 MP, 25 steps
            elif fam == "flux3k":
                per = 0.25 * mp * evals  # about 4 s per megapixel at 16 steps on a 5090
            elif fam == "fibo2":
                per = 0.35 * mp * evals  # 8B; Kalm (1.6 MP, Edit Turbo): 13 s with SeedVR2 before and after
            else:  # mageflow; Turbo measured 1-3 s at 1.6 MP
                per = 0.5 * mp * evals
            first, tile, sheet = 4 + per, 0, 6 + 0.8 * per * sheet_share
        else:
            first = 5 + 0.55 * s["steps"] * size
            tile = 3.7 * s["detail_steps"] / 8 * (1.15 if s["detail_style"] else 1)
            sheet = 4 + 0.8 * s["steps"]
        if fam != "upscale":  # the original art, enlarged for the AI
            if s["input_upscaler"] == "seedvr2":
                first += 2 * size
            elif is_local(s["input_upscaler"]):
                first += upscalers.seconds(s["input_upscaler"], in_w * in_h, ww * wh)
        if s["upscaler"] == "seedvr2":  # measured: 3.4 s at 4x, 23 s at 8x
            first += 3.4 * (s["out_scale"] / 4) ** 2.75 + (2 if fam == "upscale" and s["seedvr2_input"] else 0)
        elif outside and fam != "upscale":
            up = upscalers.seconds(s["upscaler"], ww * wh, out_px)
            sheet += 0.5 * up  # states sheets are about half a field
            if not (s["detail_pass"] and not no_detail):  # the detail pass has a Lanczos resize in the graph
                first += up
        out = {"render": first * max(1, s["tries"]), "tiles": 0, "detail": 0, "sheets": 0, "states": 0,
               "scale": s["out_scale"]}
        if not preview:
            if s["detail_pass"] and not no_detail:
                out["tiles"] = tile_count(w * s["out_scale"], h * s["out_scale"])
                out["detail"] = out["tiles"] * tile
            if s["states_pass"]:
                k = self.render_k(name, s)
                runs = self.state_runs(name, k)[0] if exact_states or (name, k) in self._state_runs else AVG_STATE_RUNS
                out["sheets"], out["states"] = runs, runs * sheet
        out["total"] = out["render"] + out["detail"] + out["states"]
        return out

    # ---- versions

    def current(self, name, meta=None):
        meta = self.meta(name) if meta is None else meta
        vid = meta.get("current")
        return vid if vid and self.versions.dir(name, vid).exists() else None

    def make_current(self, name, vid, options=None):
        changed = self.current(name) != vid  # not when a rebuild republishes the same version
        self.versions.publish(name, vid, self.jobs[name]["copies"])
        self.update_meta(name, current=vid)
        if prefs()["auto_install"]:  # the game gets it straight away (a rebuild too)
            try:
                install_field(name, self.jobs[name]["copies"])
            except OSError:
                traceback.print_exc()
        for hook in ON_CURRENT if changed else ():
            try:
                hook(name, vid, options or {})
            except Exception:  # a plug-in's trouble mustn't stop the Studio
                traceback.print_exc()

    def should_replace(self, name, kind):
        """A new final always becomes current; a preview only replaces nothing or another preview."""
        if kind == "final":
            return True
        cur = self.current(name)
        return not cur or self.versions.meta(name, cur).get("mode") == "preview" or not self.versions.has_pages(name, cur)

    def unused_versions(self, needed=()):
        """Every version no field uses in the game, by kind: {"final" | "preview" | "try" | "imported" |
        "held_back": [(name, vid)]}. held_back: on fields with nothing in the game (their renders were held back
        by the checks). needed: (name, vid) pairs queued jobs still need, left out."""
        out = {k: [] for k in CLEANUP_KINDS}
        for name in self.jobs:
            cur = self.current(name)
            for vid in self.versions.ids(name):
                if vid == cur or (name, vid) in needed:
                    continue
                m = self.versions.meta(name, vid)
                out["imported" if m.get("mode") == "imported" else "held_back" if not cur else "try" if m.get("try")
                    else "preview" if m.get("mode") == "preview" else "final"].append((name, vid))
        return out

    def thumb(self, name, vid):
        """Small JPEG of a version's render (cached next to it)."""
        p = self.versions.dir(name, vid) / "thumb.jpg"
        if not p.exists():
            src = self.versions.render_path(name, vid)
            if not src:
                return None
            arr = ai._unpad(np.asarray(Image.open(src).convert("RGB")), self.jobs[name],
                            self.versions.meta(name, vid).get("frame"))
            im = Image.fromarray(arr)
            im.thumbnail((240, 180), Image.LANCZOS)
            im.save(p, quality=84)
        return p

    def version_list(self, name, meta=None):
        cur = self.current(name, meta)
        out = []
        for v in self.versions.listing(name):
            s = v.get("settings") or {}
            out.append({
                "id": v["id"], "created": v.get("created"), "mode": v.get("mode"), "family": v.get("family"),
                "look": v.get("look_name"), "try": bool(v.get("try")), "note": v.get("note", ""),
                "result": v.get("result"), "built": v["built"], "review": v["review"],
                "has_states": (self.versions.states_dir(name, v["id"]) / "states.json").exists(),
                "current": v["id"] == cur, "settings": {k: s[k] for k in SUMMARY_KEYS if k in s},
                "source": v.get("source"),
            })
        return out

    # ---- summaries for the UI

    def field_summary(self, name, meta=None):
        job = self.jobs[name]
        meta = self.meta(name) if meta is None else meta
        look_id, how = self.look_of(name, meta)
        cur = self.current(name, meta)
        vmeta = self.versions.meta(name, cur) if cur else {}
        if cur:
            status = "preview" if vmeta.get("mode") == "preview" else "built"
        else:
            status = "generated" if self.versions.ids(name) else "none"
        return {
            "name": name, "copies": job["copies"], "w": job["canvas"][2], "h": job["canvas"][3],
            "status": status, "flag": (vmeta.get("result") or {}).get("flag", ""),
            "error": meta.get("error", ""), "custom": not looks.is_empty(meta.get("override")),
            "look": look_id, "look_how": how, "current": cur or "", "family": vmeta.get("family"),
        }

    def settings_view(self, name):
        """What the Style tab shows: the field's settings and its look's, with what each changes."""
        meta = self.meta(name)
        look_id, how = self.look_of(name, meta)
        with self.looks.lock:
            look = self.looks.get(look_id)
            ov = meta.get("override")
            look_family = looks.layer_family([look])
            eff = looks.resolve([look, ov])
            look_values = looks.resolve([look])
            look_keys = looks.layer_keys(look, look_family)
            ov_keys = looks.layer_keys(ov, eff["family"])
        prev = dict(eff, out_scale=min(4, eff["out_scale"]), detail_pass=False)
        runs, panels = self.state_runs(name, self.render_k(name, eff))
        auto_id, auto_how = self.looks.resolve_look(name, self.jobs[name]["copies"])
        return {
            "look": {"id": look_id, "how": how, "name": look.get("name", look_id),
                     "explicit": meta.get("look") == look_id},
            "auto": {"id": auto_id, "how": auto_how},
            "effective": eff, "look_values": look_values, "look_keys": look_keys, "override_keys": ov_keys,
            "estimate": {"final": self.estimate(name, eff), "preview": self.estimate(name, prev, True)},
            "state_runs": runs, "state_panels": panels, "caption": meta.get("caption", ""),
            "text_boxes": meta.get("text_boxes"),
            "syw": self.syw_has(name),  # the viewer's SYW view: the pack has the field
        }

    def looks_view(self):
        counts = {}
        for n in self.jobs:
            lid, _ = self.look_of(n)
            counts[lid] = counts.get(lid, 0) + 1
        with self.looks.lock:
            d = self.looks.data
            items = []
            for lid, look in d["looks"].items():
                fam = looks.layer_family([look])
                items.append({"id": lid, "name": look.get("name", lid), "family": fam,
                              "keys": looks.layer_keys(look, fam), "fields": counts.get(lid, 0)})
            return {"default": d["default"], "looks": items, "assignments": list(d["assignments"])}


# ---------------------------------------------------------------- generation queue

def make_job(name, kind, family=None, version=None, current="auto", is_try=False, run_states=False, fresh=False,
             source=None, options=None, settings=None):
    """kind: final | preview (render a new version and build it) or build (cut an existing version
    into game textures). current: True, False, or "auto" (Project.should_replace). fresh: describe the
    scene and look for its signs again first (batches). source: a version to render from instead of the
    original art. options: the batch's plug-in options, handed to ON_CURRENT hooks. settings: render
    settings for this job only, over the field's look (a try: the look and the field stay as they are)."""
    job = {"name": name, "kind": kind, "family": family, "version": version, "current": current,
           "try": is_try, "states": run_states, "fresh": fresh, "source": source, "options": options or {},
           "settings": settings or {}}
    if kind in JOB_KINDS:
        job["est"] = JOB_KINDS[kind]["est"](name)
    elif kind == "build":
        s = project.effective(name)
        job["est"] = 4 + (project.estimate(name, s, exact_states=False)["states"] if run_states else 0)
    else:
        s = project.job_settings(name, kind, family)
        s.update({k: v for k, v in job["settings"].items() if k in s})
        job["est"] = project.estimate(name, s, kind == "preview", exact_states=False)["total"]
        job["family"] = s["family"]
    return job


class Worker(threading.Thread):
    def __init__(self, project):
        super().__init__(daemon=True)
        self.p = project
        self.queue = deque()
        self.lock = threading.Condition()
        self.current = None
        self.recent = deque(maxlen=50)
        self.cancel_flag = False
        self.ids = itertools.count(1)

    def needed_versions(self):
        """(name, version) pairs that queued or running jobs still need: to build, or to start from."""
        with self.lock:
            jobs = list(self.queue) + ([self.current["job"]] if self.current else [])
        return {(j["name"], v) for j in jobs for v in (j.get("version"), j.get("source")) if v}

    @staticmethod
    def _key(job):
        return (job["name"], job["kind"], job.get("family"), job.get("version"), job.get("try"), job.get("source"),
                json.dumps(job.get("settings") or {}, sort_keys=True))

    @staticmethod
    def _public(job):
        return {k: job.get(k) for k in ("id", "name", "kind", "family", "version", "est", "try", "source")}

    def enqueue(self, jobs, front=False):
        """Add jobs (not ones already queued or running); front: run them next, in their order."""
        with self.lock:
            busy = {self._key(j) for j in self.queue}
            if self.current:
                busy.add(self._key(self.current["job"]))
            added = 0
            for j in jobs:
                if self._key(j) in busy:
                    continue
                j["id"] = next(self.ids)
                if front:
                    self.queue.insert(added, j)
                else:
                    self.queue.append(j)
                busy.add(self._key(j))
                added += 1
            self.lock.notify()
        return added

    def remove(self, job_id):
        with self.lock:
            self.queue = deque(j for j in self.queue if j["id"] != job_id)

    def cancel(self):
        with self.lock:
            self.queue.clear()
            self.cancel_flag = True

    def state(self):
        with self.lock:
            cur = None
            if self.current:
                cur = {k: v for k, v in self.current.items() if k != "job"}
                cur.update(self._public(self.current["job"]))
            return {"current": cur, "queued": [self._public(j) for j in self.queue],
                    "recent": list(self.recent)[-15:]}

    def run(self):
        while True:
            with self.lock:
                while not self.queue:
                    self.lock.wait()
                job = self.queue.popleft()
                self.cancel_flag = False
                self.current = {"job": job, "stage": "starting", "progress": 0.0, "started": time.time()}
            name = job["name"]
            ok, msg, vid = True, "", job.get("version")
            try:
                if job["kind"] in JOB_KINDS:
                    self.band = (0.0, 1.0)
                    JOB_KINDS[job["kind"]]["run"](self, job)
                    vid = job.get("version")  # the one it made (signs, touch-up): the page shows it, not its source
                elif job["kind"] == "build":
                    self.band = (0.0, 1.0)
                    self.build(name, vid, run_states=job.get("states", False), overrides=job.get("settings"))
                    if job["current"] is True:
                        self.p.make_current(name, vid)
                else:
                    vid = self.render(job)
            except (ComfyError, UpscalerError, InterruptedError) as e:
                ok, msg = False, str(e)
            except Exception as e:  # keep the queue alive; surface the error in the UI
                ok, msg = False, f"{type(e).__name__}: {e}"
                traceback.print_exc()
            if name not in self.p.jobs:
                pass  # a job for every field (cleanup): no field's error to set
            elif ok:
                self.p.update_meta(name, error=None)
            elif msg != "cancelled":
                self.p.update_meta(name, error=msg)
            with self.lock:
                self.recent.append({**self._public(job), "version": vid, "ok": ok, "message": msg or job.get("note", ""),
                                    "seconds": round(time.time() - self.current["started"], 1),
                                    "finished": time.time()})
                self.current = None

    band = (0.0, 1.0)  # share of the progress bar the current phase reports into

    def _stage(self, stage, progress):
        lo, hi = self.band
        with self.lock:
            if self.current:
                self.current.update(stage=stage, progress=lo + (hi - lo) * progress)

    def render(self, job):
        name, kind = job["name"], job["kind"]
        preview = kind == "preview"
        s = self.p.job_settings(name, kind, job.get("family"))
        s.update({k: v for k, v in (job.get("settings") or {}).items() if k in s})  # a try's own (make_job)
        with_states = not preview and s["states_pass"]
        est = self.p.estimate(name, s, preview)
        split = (est["render"] + est["detail"]) / est["total"] if with_states and est["states"] else 0.97
        if job.get("fresh"):
            self.refresh_texts(name, s)
        tries = max(1, s["tries"])
        if tries == 1 and not s["retry"]:
            self.band = (0.0, split)
            vid = self.generate(name, s, job)
        else:
            vid, s = self.best_of(name, s, job, tries, split)
        self.band = (split, 0.97)
        self.build(name, vid, s, run_states=with_states)
        self.band = (0.0, 1.0)
        if job["current"] is True or (job["current"] == "auto" and self.p.should_replace(name, kind)):
            kept = (self.p.versions.meta(name, vid).get("result") or {}).get("edges_kept", 1)
            if kept < KEEP_IN_GAME:
                # the AI replaced the scene rather than restyling it: keep it out of the game
                self.p.versions.update_meta(name, vid, held_back=True)
                raise ComfyError(f"not put in game: only {kept:.0%} of the original outlines kept "
                                 "(the AI changed the scene); it's in the version strip if you want it anyway")
            self.p.make_current(name, vid, job.get("options"))
        return vid

    def refresh_texts(self, name, s):
        """A batch starts each field afresh: its scene described and its signs looked for again (the signs
        the user drew stay)."""
        describe = s.get("describe_scene") and s.get("family") != "upscale"
        if not describe and not self.p.wants_signs(s):
            return
        comfy = Comfy(self.p.comfy_url)
        if not comfy.online():
            raise ComfyError(f"ComfyUI isn't reachable at {self.p.comfy_url}; start ComfyUI and try again")
        if describe:
            self._stage("describing the scene", 0.0)
            self.p.describe(name, comfy, s)
        if self.p.wants_signs(s):
            self._stage("looking for signs", 0.01)
            self.p.find_signs(name, comfy, s)

    def best_of(self, name, s, job, tries, split):
        """Render with `tries` seeds (and once more at a lower repaint when retry is on and all keep under
        FLAG_KEPT of the original outlines); keep the version that keeps the most, delete the others."""
        cands = []
        for i in range(tries + (1 if s["retry"] else 0)):
            if i == tries:  # the retry
                if max(c[0] for c in cands) >= FLAG_KEPT:
                    break
                si = dict(s, seed=s["seed"] + i)
                if s["family"] in REPAINT_FAMILIES:
                    si["denoise"] = round(max(0.1, s["denoise"] - 0.1), 2)
            else:
                si = dict(s, seed=s["seed"] + i)
            self.band = (split * i / (tries + 1), split * (i + 1) / (tries + 1))
            vid = self.generate(name, si, job)
            cands.append((self.outlines_kept(name, vid, si), vid, si))
        kept, vid, best = max(cands, key=lambda c: c[0])
        for _, v, _ in cands:
            if v != vid:
                self.p.versions.delete(name, v)
        self.p.versions.update_meta(name, vid, tries=[
            {"seed": c[2]["seed"], "denoise": c[2]["denoise"], "kept": round(c[0], 3)} for c in cands])
        return vid, best

    def outlines_kept(self, name, vid, s):
        vmeta = self.p.versions.meta(name, vid)
        main = ai.prepare_main(self.p.background(name), self.p.versions.render_path(name, vid), self.p.jobs[name],
                               vmeta.get("rendered_defaults", {}), do_align=s["align"], frame=vmeta.get("frame"))
        return ai.edges_kept(main["orig"], main["ai_1x"])

    def generate(self, name, s, job):
        """Run the main render; returns the new version's id."""
        started = time.time()
        fjob = self.p.jobs[name]
        w, h = fjob["canvas"][2], fjob["canvas"][3]
        source, pad = AI / "input" / f"{name}.png", (0, 0, 0, 0)
        cancelled = lambda: self.cancel_flag
        start = None
        if job.get("source") and s.get("family") != "upscale":
            # render from a version: it goes in at its own resolution, so no enlarging it, just the resize
            self._stage("preparing the version to start from", 0.01)
            start = self.p.start_image(name, job["source"])
            s = dict(s, input_upscaler="")
        s = self.p.input_settings(name, s)  # SYW where the pack has the field, else its fallback
        if s.get("family") == "upscale" and is_local(s["upscaler"]):
            # ImageMagick / Upscayl on the original: no ComfyUI needed
            self._stage("upscaling", 0.1)
            img = np.asarray(Image.open(source).convert("RGB"))
            data = _png(Image.fromarray(upscalers.upscale(img, s["upscaler"], w * s["out_scale"], h * s["out_scale"], cancelled)), 4)
            return self._new_version(name, s, job, fjob, data, pad, started)
        comfy = Comfy(self.p.comfy_url)
        if not comfy.online():
            raise ComfyError(f"ComfyUI isn't reachable at {self.p.comfy_url}; start ComfyUI and try again")
        check_requirements(comfy, s)
        scene = self.p.scene(name, s, comfy)
        if scene:
            s = dict(s, scene=scene)
        self._stage("uploading", 0.02)
        if s.get("family") in EDIT_FAMILIES:
            # edit models reframe slightly: give them a blurred margin, mostly cropped off inside the workflow
            pad = edit_padding(w, h, s["edit_margin"])
        sign_boxes = self.p.sign_boxes(name, s, comfy)
        edit = s.get("family") in EDIT_FAMILIES  # these repaint everything: strength 1
        relative = lambda v: v / (1.0 if edit else max(s["denoise"], 1e-3))
        inside = relative(s["text_repaint"])
        moving = self.p.moving_boxes(name, s)
        boxes = [dict(b, inside=inside) for b in sign_boxes] + [dict(b, inside=relative(s["anim_repaint"])) for b in moving]
        boxes = [b for b in boxes if b["inside"] < 1]
        flat = None  # dark, empty areas: the repainting models invent there (ff7bg/protect.py)
        if s.get("protect_flat") and s.get("family") in REPAINT_FAMILIES and relative(s["flat_repaint"]) < 1:
            flat = protect.flat_dark(np.asarray(Image.open(AI / "input" / f"{name}.png").convert("RGB")))
            flat = flat if flat.max() > 0.05 else None
        if boxes or flat is not None:  # signs, moving parts, dark empty areas: a lighter repaint, fading into the rest
            l, t, r, b = pad  # edit models get the padded original: boxes move with it
            pw, ph = w + l + r, h + t + b
            k = work_size(s, pw, ph)[0] // pw
            moved = [signs.shifted(x, l, t) for x in boxes]
            repaint, keep = signs.masks(moved, pw, ph, k, inside)
            if flat is not None:
                f = np.zeros((ph * k, pw * k), np.float32)
                f[t * k:(t + h) * k, l * k:(l + w) * k] = np.asarray(
                    Image.fromarray(flat, "F").resize((w * k, h * k), Image.BILINEAR), np.float32)
                rel = min(1.0, relative(s["flat_repaint"]))
                repaint = np.minimum(repaint, np.round((1 - f * (1 - rel)) * 255).astype(np.uint8))
                keep = np.maximum(keep, np.round(f * 255).astype(np.uint8))  # and kept through the detail pass
                s = dict(s, flat_share=round(float((flat > 0.5).mean()), 3))
            up = AI / "input_up"
            up.mkdir(exist_ok=True)
            Image.fromarray(repaint).save(up / f"{name}_signs.png")
            s = dict(s, text_mask=comfy.upload(up / f"{name}_signs.png"), signs=len(sign_boxes), moving_parts=len(moving))
            if s["detail_pass"] and not edit:
                Image.fromarray(keep).save(up / f"{name}_signs_keep.png")
                s["text_keep"] = comfy.upload(up / f"{name}_signs_keep.png")
        if start is not None:
            img, k, _ = start  # the margin scales with the picture, so the shape is the original's
            source = AI / "input_start" / f"{name}.png"
            source.parent.mkdir(exist_ok=True)
            Image.fromarray(ai._pad(img, *(m * k for m in pad)) if s.get("family") in EDIT_FAMILIES else img).save(source)
        elif s.get("family") in EDIT_FAMILIES:
            padded = ai._pad(np.asarray(Image.open(source).convert("RGB")), *pad)
            source = AI / "input_edit" / f"{name}.png"
            source.parent.mkdir(exist_ok=True)
            Image.fromarray(padded).save(source)
        if is_local(s["input_upscaler"]) and (s["family"] != "upscale" or (
                s["upscaler"] == "seedvr2" and not s["seedvr2_input"])):
            # ImageMagick / Upscayl / SYW enlarge the original art here; the graph's resize then changes nothing
            self._stage("upscaling input", 0.03)
            iw, ih = Image.open(source).size
            tw, th = (iw * s["out_scale"], ih * s["out_scale"]) if s["family"] == "upscale" else work_size(s, iw, ih)
            big = self.p.enlarged_input(name, s, (tw, th), source, pad, cancelled)
            source = AI / "input_up" / f"{name}.png"
            source.parent.mkdir(exist_ok=True)
            Image.fromarray(big).save(source)
        inp = comfy.upload(source)
        refs = [comfy.upload(STYLES / r, "ff7studio_styles") for r in s["style_refs"] if (STYLES / r).exists()]
        wf = build_workflow(s, inp, w, h, f"ff7studio/{name}", refs, pad)

        detail = s["detail_pass"] and s["family"] not in EDIT_FAMILIES and s["family"] != "upscale"
        n_tiles = tile_count(w * s["out_scale"], h * s["out_scale"]) if detail else 0
        # progress bands; the detail pass dominates when it's on
        sample_hi = 0.35 if n_tiles else 0.85
        stages = {"up1": ("upscaling input", 0.04, 0.08), "sample": ("rendering", 0.08, sample_hi),
                  "up2": ("upscaling result", sample_hi, sample_hi + 0.05)}
        tiles_done = [0]

        def progress(node, value, maximum):
            frac = value / maximum if maximum else 0
            if node == "tsample" and n_tiles:
                if maximum and value == maximum:
                    tiles_done[0] += 1
                done = min(tiles_done[0] + (0 if value == maximum else frac), n_tiles)
                self._stage(f"adding detail, tile {min(tiles_done[0] + 1, n_tiles)}/{n_tiles}",
                            sample_hi + 0.05 + (0.97 - sample_hi - 0.05) * done / n_tiles)
            elif node in stages:
                label, lo, hi = stages[node]
                self._stage(f"{label} {value}/{maximum}" if node == "sample" and maximum else label,
                            lo + (hi - lo) * frac)
            elif node in ("ckpt", "cnl"):
                self._stage("loading models", 0.02)
            elif node.endswith("_svr_ks") or node.endswith("_up"):
                self._stage("upscaling", 0.5)

        data = comfy.run(wf, progress, cancelled)
        if is_local(s["upscaler"]):  # ImageMagick / Upscayl take the render to the output size
            kl, kt, kr, kb = edit_keep(pad)
            ow, oh = (w + kl + kr) * s["out_scale"], (h + kt + kb) * s["out_scale"]
            img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
            if img.shape[:2] != (oh, ow):
                self._stage("upscaling result", 0.9)
                data = _png(Image.fromarray(upscalers.finish(img, s, ow, oh, cancelled)), 4)
        return self._new_version(name, s, job, fjob, data, pad, started, start[2] if start else None)

    def _new_version(self, name, s, job, fjob, data, pad, started, start_states=None):
        self._stage("saving", 0.99)
        look_id, _ = self.p.look_of(name)
        vid = self.p.versions.create(name, data, {
            "mode": job["kind"], "family": s["family"], "try": bool(job.get("try")),
            "look": look_id, "look_name": self.p.looks.get(look_id).get("name", look_id),
            "settings": {k: s[k] for k in DEFAULT_SETTINGS},
            # states on in the image sent to the generator (a version started from keeps its own)
            "rendered_defaults": fjob.get("defaults", {}) if start_states is None else start_states,
            "source": job.get("source") if start_states is not None else None,  # the version it started from
            "frame": list(edit_keep(pad)),  # margin returned around the canvas
            "scene": s.get("scene", ""),  # the field description added to the prompt
            "signs": s.get("signs", 0),  # sign areas repainted lightly
            "moving_parts": s.get("moving_parts", 0),  # animated parts repainted lightly
            "render_seconds": round(time.time() - started, 1),
        })
        self.p.thumb(name, vid)
        return vid

    def build(self, name, vid, s=None, run_states=False, overrides=None):
        """Cut a version's render into game textures (inside the version); optionally run the states
        pass first. Without it, restyled states saved with the version are reused. overrides: settings
        for this build only (a states redo with its own prompt; "scene" too, "" to leave it out)."""
        vs = self.p.versions
        out = vs.render_path(name, vid) if vid else None
        if not out:
            raise ComfyError("this version has no image")
        vmeta = vs.meta(name, vid)
        if s is None:  # rebuild with the settings it was made with
            s = self.p.effective(name, vmeta.get("family"))
            current_states = {k: s[k] for k in ("state_denoise", "state_denoise_boost", "anim_repaint")}
            s.update(vmeta.get("settings") or {})
            if run_states:  # redoing the states: with today's states settings
                s.update(current_states)
        if vmeta.get("scene"):
            s = dict(s, scene=vmeta["scene"])
        if overrides:
            s = dict(s, **{k: v for k, v in overrides.items() if k in s or k == "scene"})
        bg = self.p.background(name)
        fjob = self.p.jobs[name]
        self._stage("preparing", 0.02)
        main = ai.prepare_main(bg, out, fjob, vmeta.get("rendered_defaults", {}), do_align=s["align"],
                               frame=vmeta.get("frame"))
        sdir = vs.states_dir(name, vid)
        if run_states:
            comfy, refs = None, []  # the Upscale workflow with ImageMagick / Upscayl needs no ComfyUI
            if not (s.get("family") == "upscale" and is_local(s["upscaler"])):
                comfy = Comfy(self.p.comfy_url)
                if not comfy.online():
                    raise ComfyError(f"ComfyUI isn't reachable at {self.p.comfy_url}; start ComfyUI and try again")
                check_requirements(comfy, s)
                refs = [comfy.upload(STYLES / r, "ff7studio_styles") for r in s["style_refs"] if (STYLES / r).exists()]
            hires = None  # a render from SYW's pages: its states' art from them too
            if s.get("input_upscaler") == syw.NAME and self.p.syw_pack():
                tile = syw.tile_source(self.p.syw_pack(), name, bg)
                hires = (tile, syw.SCALE) if tile else None
            # each state as a whole render of the version's own recipe, when it can be re-made (WholeStates)
            whole, method = None, "painted"
            if comfy is not None and s.get("family") != "upscale" and bg_has_states(bg):
                ws = WholeStates(self, name, vid, comfy)
                if ws.reason is None and ws.check():
                    whole, method = ws, "whole renders"
                else:
                    method = f"painted ({ws.reason})"
            panels = states.run(
                comfy, bg, main["rect"], main, s, refs, sdir,
                progress=lambda i, n, k: self._stage(f"restyling states, sheet {i + 1}/{n} ({k} panels)", 0.05 + 0.9 * i / n),
                cancelled=lambda: self.cancel_flag,
                animated={int(p): a.get("order", []) for p, a in fjob.get("animations", {}).items()}, hires=hires,
                whole=whole)
            if whole is not None and whole.notes:
                method += "; " + "; ".join(sorted(set(whole.notes)))
            vs.update_meta(name, vid, states_method=method)
        else:
            panels = states.load(sdir, main["scale"])
        # a finishing step, so a Rebuild applies today's value
        keep = self.p.effective(name, s.get("family"))["keep_colors"]
        ai.keep_colors(main, panels, keep)
        self._stage("building game textures and animations", 0.97)
        shutil.rmtree(vs.pages_root(name, vid), ignore_errors=True)
        r = ai.build_pages(bg, main, fjob, vs.pages_root(name, vid), vs.review_dir(name, vid), panels,
                           raw=self.p.field_raw(name), anim_cache=AI / "palette_states" / f"{name}.json")
        b, a = r["drift_before"], r["drift_after"]
        vs.update_meta(name, vid, result={
            "scale": r["scale"], "pages": r["pages"], "edges_kept": round(r["edges_kept"], 3),
            "aligned": r["aligned"], "drift_before": round(b.median, 2), "drift_after": round(a.median, 2),
            "missing": round(r["missing"], 4), "flag": ", ".join(ai.flags_for(r)), "built_at": time.time(),
            "state_panels": r["state_panels"], "keep_colors": keep, "animated_frames": r["animated_frames"],
            "animation_left_still": r["animation_left_still"],
        })
        if self.p.current(name) == vid:  # rebuilt the one the game uses
            self.p.make_current(name, vid)


def check_requirements(comfy, s):
    """Stop before rendering when ComfyUI lacks a file or node these settings need."""
    if s.get("input_upscaler") == syw.NAME and project.syw_pack() is None:
        raise ComfyError("the SYW Unified Field Textures pack isn't in 7th Heaven's library (~/7th Heaven): add it "
                         "there, or pick another original art upscaler")
    miss = requirements.missing(s, comfy)
    if miss["files"] or miss["nodes"] or miss["tools"]:
        raise ComfyError(f"missing {requirements.summary(miss)}; the Look card has the download links")


# ---------------------------------------------------------------- rendering helpers

render_cache = OrderedDict()
render_lock = threading.Lock()
strip_cache = OrderedDict()  # palette effect strips: big, so only a few
palette_previews = {}  # name -> palettes.preview (the field's scripts never change)


def _include(layers, fx, params):
    def inc(t):
        if t.layer not in layers:
            return False
        if t.param:
            return (t.param, t.state) in params
        return fx or not t.blending
    return inc


def _load_pages(name, vid):
    pages = {}
    for f in project.versions.pages_dir(name, vid).glob(f"{name}_*_00.png"):
        tex = int(f.stem[len(name) + 1 :].split("_")[0])
        pages[tex] = np.asarray(Image.open(f).convert("RGBA"))
    return pages


def _png(img, level=1):
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=level)
    return buf.getvalue()


def _jpeg(img):
    # 8x views are ~20 MP: near-lossless JPEG (no chroma subsampling) keeps them quick to send
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def _view_args():
    layers = {int(x) for x in request.args.get("layers", "0,1,2,3").split(",") if x != ""}
    fx = request.args.get("fx", "1") == "1"
    params = set()
    for item in request.args.get("params", "").split(","):
        if "." in item:
            p, st = item.split(".")
            params.add((int(p), int(st)))
    return layers, fx, params


def _version_arg(name):
    """The version a 'new' view asks for (default: the current one) and when it was last built."""
    vid = request.args.get("ver") or project.current(name)
    if not vid or not project.versions.render_path(name, vid):
        abort(404)
    built = (project.versions.meta(name, vid).get("result") or {}).get("built_at", 0)
    return vid, built if project.versions.has_pages(name, vid) else 0


# ---------------------------------------------------------------- routes: static + status

@app.get("/")
def index():
    return send_from_directory(STUDIO, "index.html")


@app.get("/static/<path:f>")
def static_files(f):
    return send_from_directory(STUDIO, f)


@app.get("/styles/<path:f>")
def style_file(f):
    return send_from_directory(STYLES, f)


@app.get("/api/plugins")
def api_plugins():
    return jsonify(PLUGIN_SCRIPTS)


def game_window():
    """How tall the game draws its picture, from FFNx.toml: the window's height, or the screen's when
    fullscreen (only the browser knows that one). None without FFNx."""
    try:
        with open(GAME_WORKDIR / "FFNx.toml", "rb") as f:
            t = tomllib.load(f)
    except (OSError, ValueError):
        return None
    return {"fullscreen": bool(t.get("fullscreen")), "height": int(t.get("window_size_y") or 0)}


@app.get("/api/status")
def api_status():
    url = project.comfy_url
    return jsonify(ready=project.ready, status=project.status, comfy_online=Comfy(url).online(),
                   comfy_url=url, game_mods=str(GAME_WORKDIR / "mods" / "Textures"), game_window=game_window())


# ---------------------------------------------------------------- routes: fields

@app.get("/api/fields")
def api_fields():
    if not project.ready:
        return jsonify([])
    return jsonify([project.field_summary(n) for n in project.jobs])


def field_detail(name):
    bg = project.background(name)
    min_x, min_y, _, _ = canvas_rect(bg)
    params, rects, spots = {}, {}, {}
    for t in bg.tiles:
        if t.param:
            params.setdefault(t.param, set()).add(t.state)
            x, y = t.dst_x - min_x, t.dst_y - min_y
            spots.setdefault(t.param, set()).add((x, y, t.size))
            r = rects.setdefault(t.param, [x, y, x + t.size, y + t.size])
            r[:] = [min(r[0], x), min(r[1], y), max(r[2], x + t.size), max(r[3], y + t.size)]
    meta = project.meta(name)
    job = project.jobs[name]
    return {
        **project.field_summary(name, meta),
        **project.settings_view(name),
        "layers": sorted({t.layer for t in bg.tiles}),
        "has_fx": any(t.blending and not t.param for t in bg.tiles),
        "params": {str(p): sorted(st) for p, st in sorted(params.items())},
        "group_rects": {str(p): [r[0], r[1], r[2] - r[0], r[3] - r[1]] for p, r in rects.items()},
        # where each group's tiles are: the viewer draws animation frames only there
        "group_tiles": {str(p): sorted(v) for p, v in spots.items()},
        "defaults": job.get("defaults", {}),
        "animations": job.get("animations", {}),
        "versions": project.version_list(name, meta),
    }


def _field_or_404(name):
    if not project.ready or name not in project.jobs:
        abort(404)


@app.get("/api/field/<name>")
def api_field(name):
    _field_or_404(name)
    return jsonify(field_detail(name))


@app.post("/api/field/<name>/settings")
def api_field_settings(name):
    """Change settings for this field only (stored as an override on top of its look)."""
    _field_or_404(name)
    values = request.get_json(force=True).get("values", {})
    with project.looks.lock, project.meta_lock:
        meta = project.meta(name)
        look_id, _ = project.look_of(name, meta)
        look = project.looks.get(look_id)
        ov = meta.get("override") or looks.empty_layer()
        fam = values.get("family") if values.get("family") in FAMILIES else looks.layer_family([look, ov])
        looks.patch_layer(ov, values, fam, looks.resolve([look], fam), looks.layer_family([look]))
        project.update_meta(name, override=None if looks.is_empty(ov) else ov)
    return jsonify(field_detail(name))


@app.post("/api/field/<name>/caption")
def api_field_caption(name):
    """The field's description for the prompt: {"text": ...} replaces it, {"regenerate": true} makes it again."""
    _field_or_404(name)
    body = request.get_json(force=True)
    if body.get("regenerate"):
        comfy = Comfy(project.comfy_url)
        if not comfy.online():
            return jsonify(message="ComfyUI isn't reachable"), 503
        try:
            project.describe(name, comfy, project.job_settings(name, "final"))
        except ComfyError as e:
            return jsonify(message=str(e)), 502
    elif "text" in body:
        project.update_meta(name, caption=" ".join(str(body["text"]).split()) or None)
    return jsonify(field_detail(name))


@app.post("/api/field/<name>/signs")
def api_field_signs(name):
    """The field's sign areas: {"boxes": [...]} saves the user's edits, {"find": true} looks again (keeping
    the user's own), {"clear": true} forgets them (they're looked for again on the next render)."""
    _field_or_404(name)
    body = request.get_json(force=True)
    w, h = project.jobs[name]["canvas"][2:]
    if body.get("find"):
        comfy = Comfy(project.comfy_url)
        if not comfy.online():
            return jsonify(message="ComfyUI isn't reachable"), 503
        try:
            project.find_signs(name, comfy, project.job_settings(name, "final"))
        except ComfyError as e:
            return jsonify(message=str(e)), 502
    elif body.get("clear"):
        project.update_meta(name, text_boxes=None)
    elif "boxes" in body:
        project.update_meta(name, text_boxes=signs.clean_boxes(body["boxes"], w, h))
    elif body.get("paint"):  # paint the words the user gave onto a version's signs (signfix)
        vid = body.get("version") or project.current(name)
        if not vid or not project.versions.render_path(name, vid):
            return jsonify(message="render the field first"), 400
        if not sign_words(name):
            return jsonify(message="type what a sign should say first"), 400
        return jsonify(added=worker.enqueue([make_job(name, "signs", version=vid)]), **field_detail(name))
    return jsonify(field_detail(name))


@app.post("/api/field/<name>/touchup")
def api_field_touchup(name):
    """Repaint the areas the user marked on a version: {"version", "mask": a PNG data URL over the canvas (its
    alpha: the marks), "hint": what should be there (optional), "strength": how much to change, 0.2-1}."""
    _field_or_404(name)
    body = request.get_json(force=True)
    vid = body.get("version") or project.current(name)
    if not vid or not project.versions.render_path(name, vid):
        return jsonify(message="render the field first"), 400
    data = str(body.get("mask") or "")
    if not data.startswith("data:image/png;base64,"):
        return jsonify(message="nothing was marked"), 400
    img = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA")
    if not img.getchannel("A").getbbox():
        return jsonify(message="nothing was marked"), 400
    path = AI / "input" / f"{name}_touchup_marks_{int(time.time() * 1000)}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    job = make_job(name, "touchup", version=vid)
    job.update(mask=str(path), hint=str(body.get("hint") or "")[:200],
               strength=min(1.0, max(0.2, float(body.get("strength", 0.6)))))
    added = worker.enqueue([job])
    if not added:
        path.unlink(missing_ok=True)
    return jsonify(added=added, **field_detail(name))


@app.get("/api/captions")
def api_captions():
    """How many fields have a scene description."""
    return jsonify(count=sum(1 for n in project.jobs if project.meta(n).get("caption")))


@app.post("/api/captions/clear")
def api_captions_clear():
    """Drop every field's scene description; each is made again the next time it's needed."""
    cleared = 0
    for n in project.jobs:
        if project.meta(n).get("caption"):
            project.update_meta(n, caption=None)
            cleared += 1
    name = request.get_json(force=True).get("field")
    return jsonify(cleared=cleared, field=field_detail(name) if name in project.jobs else None)


@app.post("/api/field/<name>/reset")
def api_field_reset(name):
    _field_or_404(name)
    project.update_meta(name, override=None)
    return jsonify(field_detail(name))


@app.post("/api/field/<name>/look")
def api_field_look(name):
    """Pick this field's look (null: automatic, by assignment rules or the default)."""
    _field_or_404(name)
    look_id = request.get_json(force=True).get("look")
    project.update_meta(name, look=look_id if look_id in project.looks.looks() else None)
    return jsonify(field_detail(name))


# ---------------------------------------------------------------- routes: looks

@app.get("/api/looks")
def api_looks():
    return jsonify(project.looks_view())


@app.post("/api/looks")
def api_look_create():
    """New look: blank, a copy of another look, or from a field's current settings (look + its
    override); with assign, that field switches to it and drops its override."""
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip() or "New look"
    field = body.get("from_field")
    if field in project.jobs:
        eff = project.effective(field)
        with project.looks.lock:
            lid = project.looks.create(name, project.layers(field))
            project.looks.get(lid).setdefault("settings", {})["family"] = eff["family"]
            looks.prune(project.looks.get(lid))
            project.looks.save()
        if body.get("assign"):
            project.update_meta(field, look=lid, override=None)
    elif body.get("from_look") in project.looks.looks():
        lid = project.looks.create(name, [project.looks.get(body["from_look"])])
    else:
        lid = project.looks.create(name)
    return jsonify(id=lid, **project.looks_view())


@app.post("/api/looks/<look_id>")
def api_look_update(look_id):
    if look_id not in project.looks.looks():
        abort(404)
    body = request.get_json(force=True)
    if body.get("name"):
        project.looks.rename(look_id, body["name"].strip())
    if body.get("default"):
        project.looks.set_default(look_id)
    return jsonify(project.looks_view())


@app.delete("/api/looks/<look_id>")
def api_look_delete(look_id):
    if not project.looks.delete(look_id):
        return jsonify(ok=False, message="The default look can't be deleted; make another one the default first."), 400
    for name in project.jobs:  # fields that picked it fall back to automatic
        if project.meta(name).get("look") == look_id:
            project.update_meta(name, look=None)
    return jsonify(ok=True, **project.looks_view())


@app.post("/api/looks/<look_id>/settings")
def api_look_settings(look_id):
    """Change a look's settings (every field using it); returns the given field's view."""
    if look_id not in project.looks.looks():
        abort(404)
    body = request.get_json(force=True)
    project.looks.patch(look_id, body.get("values", {}))
    field = body.get("field")
    return jsonify(field_detail(field) if field in project.jobs else project.looks_view())


@app.post("/api/assignments")
def api_assignments():
    project.looks.set_assignments(request.get_json(force=True).get("rows", []))
    return jsonify(project.looks_view())


# ---------------------------------------------------------------- routes: images

@app.get("/api/render/<name>")
def api_render(name):
    _field_or_404(name)
    which = request.args.get("v", "orig")
    layers, fx, params = _view_args()
    vid, built = _version_arg(name) if which == "new" else ("", 0)
    key = (name, which, vid, built, tuple(sorted(layers)), fx, tuple(sorted(params)))
    with render_lock:
        if key in render_cache:
            render_cache.move_to_end(key)
            data, mime, raw = render_cache[key]
            resp = send_file(io.BytesIO(data), mimetype=mime)
            resp.headers["X-Raw"] = raw
            return resp
    bg = project.background(name)
    rect = canvas_rect(bg)
    raw = "0"
    if which == "new" and built:
        pages = _load_pages(name, vid)
        scale = next(iter(pages.values())).shape[0] // 256
        img = render_composite(bg, rect, page_tile_source(pages, scale), scale, _include(layers, fx, params))
        data, mime = _jpeg(img), "image/jpeg"
    elif which == "syw":  # the SYW Unified pack's 4x of the same view (the viewer's SYW mode)
        pack = project.syw_pack()
        tile = syw.tile_source(pack, name, bg) if pack else None
        if tile is None:
            abort(404)
        img = render_composite(bg, rect, tile, syw.SCALE, _include(layers, fx, params))
        data, mime = _jpeg(img), "image/jpeg"
    elif which == "new":
        # not cut into game textures yet: the render as the generator returned it
        arr = ai._unpad(np.asarray(Image.open(project.versions.render_path(name, vid)).convert("RGB")),
                        project.jobs[name], project.versions.meta(name, vid).get("frame"))
        data, mime, raw = _jpeg(Image.fromarray(arr)), "image/jpeg", "1"
    else:
        img = render_composite(bg, rect, PageCache(bg).tile, 1, _include(layers, fx, params))
        data, mime = _png(img), "image/png"
    with render_lock:
        render_cache[key] = (data, mime, raw)
        while len(render_cache) > 40:
            render_cache.popitem(last=False)
    resp = send_file(io.BytesIO(data), mimetype=mime)
    resp.headers["X-Raw"] = raw
    return resp


@app.get("/api/strip/<name>")
def api_strip(name):
    """Every frame of an animated group, cropped to the group's area, side by side.

    The viewer draws the scene without the group and plays these frames over it, so playback
    doesn't need a server round trip per frame."""
    _field_or_404(name)
    which = request.args.get("v", "orig")
    group = int(request.args["group"])
    layers, fx, params = _view_args()
    base = {(p, st) for p, st in params if p != group}
    anim = project.jobs[name].get("animations", {}).get(str(group))
    if not anim:
        abort(404)
    frames = anim["order"] if len(anim["order"]) > 1 else [anim["order"][0], 0]  # one state = blinking
    bg = project.background(name)
    rect = canvas_rect(bg)
    min_x, min_y = rect[:2]
    ts = [t for t in bg.tiles if t.param == group]
    x0, y0 = min(t.dst_x for t in ts) - min_x, min(t.dst_y for t in ts) - min_y
    x1, y1 = max(t.dst_x + t.size for t in ts) - min_x, max(t.dst_y + t.size for t in ts) - min_y
    if which == "new":
        vid, built = _version_arg(name)
        if not built:
            abort(404)
        pages = _load_pages(name, vid)
        scale = next(iter(pages.values())).shape[0] // 256
        source = page_tile_source(pages, scale)
    elif which == "syw":  # the SYW view: the pack's own pages
        pack = project.syw_pack()
        scale, source = syw.SCALE, syw.tile_source(pack, name, bg) if pack else None
        if source is None:
            abort(404)
    else:
        scale, source = 1, PageCache(bg).tile
    group_states = {t.state for t in ts}
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    strip = Image.new("RGB", (w * len(frames), h))
    for i, frame in enumerate(frames):
        shown = base | {(group, s) for s in group_states if s & frame}
        img = render_composite(bg, rect, source, scale, _include(layers, fx, shown))
        strip.paste(img.crop((x0 * scale, y0 * scale, x1 * scale, y1 * scale)), (i * w, 0))
    if which in ("new", "syw"):
        resp = send_file(io.BytesIO(_jpeg(strip)), mimetype="image/jpeg")
    else:
        resp = send_file(io.BytesIO(_png(strip)), mimetype="image/png")
    resp.headers["X-Frames"] = json.dumps(frames)
    return resp


def palette_preview(name):
    if name not in palette_previews:
        palette_previews[name] = palettes.preview(project.field_raw(name), project.background(name))
    return palette_previews[name]


def _palette_tiles(bg, group):
    return [t for t in bg.tiles if t.palette in group["pals"] and bg.pages[t.tex].depth < 2]


def _frame_hashes(name, compute=False):
    """{(page, palette): {colours: hash}} of the field's animated textures, from the states build_pages keeps
    (compute: replay the field's scripts for them when no build has yet, a few seconds)."""
    path = AI / "palette_states" / f"{name}.json"
    if not path.exists() and not compute:
        return {}
    anim = palettes.animated_textures_cached(project.field_raw(name), project.background(name), path)
    return {k: {c: h for h, c in v.items()} for k, v in anim.items()}


@app.get("/api/palette_anim/<name>")
def api_palette_anim(name):
    """The field's palette effects (water, fire, lights that cycle colour) for the viewer to play: where each
    group's tiles are, how many states it has, and when it changes ([frame, state], a loop `length` frames long)."""
    _field_or_404(name)
    bg = project.background(name)
    min_x, min_y = canvas_rect(bg)[:2]
    out = []
    for g in palette_preview(name):
        ts = _palette_tiles(bg, g)
        x0, y0 = min(t.dst_x for t in ts) - min_x, min(t.dst_y for t in ts) - min_y
        x1, y1 = max(t.dst_x + t.size for t in ts) - min_x, max(t.dst_y + t.size for t in ts) - min_y
        out.append({"rect": [x0, y0, x1 - x0, y1 - y0], "count": len(g["states"]), "times": g["times"],
                    "length": g["length"], "tiles": sorted({(t.dst_x - min_x, t.dst_y - min_y, t.size) for t in ts})})
    return jsonify(out)


PALETTE_STRIP_PIXELS = 16_000_000  # most pixels in one palette strip: big effects play at a lower resolution


@app.get("/api/palette_strip/<name>")
def api_palette_strip(name):
    """Every state of a palette effect group (api_palette_anim), cropped to its area, in a grid (X-Grid: cols,
    rows, count). A version shows its animated textures, as the game does; 404 when it has none for the group."""
    _field_or_404(name)
    which = request.args.get("v", "orig")
    groups = palette_preview(name)
    gi = int(request.args["group"])
    if gi >= len(groups):
        abort(404)
    layers, fx, params = _view_args()
    vid, built = _version_arg(name) if which == "new" else ("", 0)
    if which == "new" and not built:
        abort(404)
    pack = project.syw_pack() if which == "syw" else None
    if which == "syw" and not (pack and syw.frames(pack).get(name.lower())):
        abort(404)  # no frames of its own: the SYW view shows the effect still
    key = (name, gi, which, vid, built, tuple(sorted(layers)), fx, tuple(sorted(params)))
    with render_lock:
        if key in strip_cache:
            strip_cache.move_to_end(key)
            data, mime, grid = strip_cache[key]
            resp = send_file(io.BytesIO(data), mimetype=mime)
            resp.headers["X-Grid"] = grid
            return resp
    g = groups[gi]
    bg = project.background(name)
    ts = _palette_tiles(bg, g)
    x0, y0 = min(t.dst_x for t in ts), min(t.dst_y for t in ts)
    x1, y1 = max(t.dst_x + t.size for t in ts), max(t.dst_y + t.size for t in ts)
    include = _include(layers, fx, params)
    # only what's drawn over or under the group's area, on a canvas every one of those tiles fits on
    near = [t for t in bg.tiles if include(t) and t.dst_x < x1 and t.dst_x + t.size > x0
            and t.dst_y < y1 and t.dst_y + t.size > y0]
    if not near:
        abort(404)
    drawn = set(map(id, near))
    sx, sy = min(t.dst_x for t in near), min(t.dst_y for t in near)
    sub = (sx, sy, max(t.dst_x + t.size for t in near) - sx, max(t.dst_y + t.size for t in near) - sy)
    animated = lambda t: t.palette in g["pals"] and bg.pages[t.tex].depth < 2
    n = len(g["states"])
    if which == "new":
        pages = _load_pages(name, vid)
        full = next(iter(pages.values())).shape[0] // 256
        scale = max(1, min(full, int((PALETTE_STRIP_PIXELS / (n * (x1 - x0) * (y1 - y0))) ** 0.5)))

        def sized(px):
            return px if scale == full else np.asarray(Image.fromarray(px, "RGBA").resize((256 * scale,) * 2, Image.BOX))
        pages = {tex: sized(px) for tex, px in pages.items()}
        hashes, folder, found = _frame_hashes(name), project.versions.pages_dir(name, vid), set()

        def source(state):
            frames = {}
            for t in near:
                k = (t.tex, t.palette)
                if animated(t) and k not in frames:
                    h = hashes.get(k, {}).get(state[t.palette])
                    f = folder / f"{name}_{t.tex:02d}_{t.palette:02d}_{h:x}.png" if h is not None else None
                    frames[k] = sized(np.asarray(Image.open(f).convert("RGBA"))) if f and f.exists() else None
                    found.add(frames[k] is not None)

            def tile(t):
                pg = frames.get((t.tex, t.palette)) if animated(t) else None
                s = t.size * scale
                return (pages[t.tex] if pg is None else pg)[t.src_y * scale : t.src_y * scale + s,
                                                            t.src_x * scale : t.src_x * scale + s]
            return tile
    elif which == "syw":
        scale = max(1, min(syw.SCALE, int((PALETTE_STRIP_PIXELS / (n * (x1 - x0) * (y1 - y0))) ** 0.5)))
        hashes, frames_of, found = _frame_hashes(name, compute=True), syw.frames(pack)[name.lower()], set()
        frame = {}  # this state's frame of each animated page (None: the page as it is)
        tiles = syw.tile_source(pack, name, bg, scale, lambda t: frame.get((t.tex, t.palette)) if animated(t) else None)

        def source(state):
            frame.clear()
            for t in near:
                k = (t.tex, t.palette)
                if animated(t) and k not in frame:
                    h = hashes.get(k, {}).get(state[t.palette])
                    where = frames_of.get(k, {}).get(h) if h is not None else None
                    frame[k] = syw.page(pack, where, scale) if where is not None else None
                    found.add(frame[k] is not None)
            return tiles
    else:
        scale, still = 1, PageCache(bg)

        def source(state):
            moving = PageCache(bg)
            for tex, pal in {(t.tex, t.palette) for t in near if animated(t)}:
                moving.set_page(tex, pal, palettes.page_rgba(bg, tex, pal, state[pal]))
            return lambda t: moving.tile(t) if animated(t) else still.tile(t)
    fw, fh = (x1 - x0) * scale, (y1 - y0) * scale
    cols = max(1, min(n, round((n * fh / fw) ** 0.5)))
    rows = -(-n // cols)
    grid = Image.new("RGB", (fw * cols, fh * rows))
    box = ((x0 - sx) * scale, (y0 - sy) * scale, (x1 - sx) * scale, (y1 - sy) * scale)
    for i, state in enumerate(g["states"]):
        img = render_composite(bg, sub, source(state), scale, lambda t: id(t) in drawn)
        grid.paste(img.crop(box), ((i % cols) * fw, (i // cols) * fh))
    if which in ("new", "syw") and True not in found:
        abort(404)  # built before animated textures, or too many states: the game leaves it still too
    data, mime = (_jpeg(grid), "image/jpeg") if which in ("new", "syw") else (_png(grid), "image/png")
    info = json.dumps({"cols": cols, "rows": rows, "count": n})
    with render_lock:
        strip_cache[key] = (data, mime, info)
        while len(strip_cache) > 12:
            strip_cache.popitem(last=False)
    resp = send_file(io.BytesIO(data), mimetype=mime)
    resp.headers["X-Grid"] = info
    return resp


@app.get("/api/overlay/<name>.png")
def api_overlay(name):
    _field_or_404(name)
    bg = project.background(name)
    rect = canvas_rect(bg)
    edges = ai._l1_cutout_edges(bg, rect, 1)
    rgba = np.zeros(edges.shape + (4,), np.uint8)
    rgba[edges] = (60, 255, 90, 255)
    return send_file(io.BytesIO(_png(Image.fromarray(rgba, "RGBA"))), mimetype="image/png")


@app.get("/api/thumb/<name>.jpg")
def api_thumb(name):
    _field_or_404(name)
    cur = project.current(name) if request.args.get("v") == "new" else None
    p = project.thumb(name, cur) if cur else None
    if p:
        resp = send_file(p, mimetype="image/jpeg")
    else:
        src = AI / "input" / f"{name}.png"
        if not src.exists():
            abort(404)
        im = Image.open(src).convert("RGB")
        im.thumbnail((200, 150), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "max-age=3600"
    return resp


@app.get("/api/vthumb/<name>/<vid>.jpg")
def api_version_thumb(name, vid):
    _field_or_404(name)
    p = project.thumb(name, vid)
    if not p:
        abort(404)
    resp = send_file(p, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "max-age=86400"
    return resp


@app.get("/api/review/<name>.jpg")
def api_review(name):
    _field_or_404(name)
    vid = request.args.get("ver") or project.current(name)
    p = project.versions.review_dir(name, vid) / f"{name}.jpg" if vid else None
    if not p or not p.exists():
        abort(404)
    return send_file(p, mimetype="image/jpeg")


@app.get("/api/input/<name>.png")
def api_input(name):
    """The image the generators get (for making a version outside the studio)."""
    _field_or_404(name)
    return send_file(AI / "input" / f"{name}.png", mimetype="image/png", as_attachment=True,
                     download_name=f"{name}_input.png")


# ---------------------------------------------------------------- routes: versions

@app.post("/api/versions/<name>/<vid>/use")
def api_version_use(name, vid):
    """Make a version the one the game gets. Built versions switch at once; the states pass (if
    the field uses it and the version never had one) and unbuilt versions go through the queue."""
    _field_or_404(name)
    vs = project.versions
    if not vs.render_path(name, vid):
        abort(404)
    s = project.effective(name, vs.meta(name, vid).get("family"))
    need_states = (s["states_pass"] and project.state_runs(name, project.render_k(name, s))[0] > 0
                   and not (vs.states_dir(name, vid) / "states.json").exists())
    switched = False
    if vs.has_pages(name, vid):
        project.make_current(name, vid)
        switched = True
    queued = 0
    if need_states or not switched:
        queued = worker.enqueue([make_job(name, "build", version=vid, current=True, run_states=need_states)])
    return jsonify(switched=switched, queued=queued, field=field_detail(name))


@app.delete("/api/versions/<name>/<vid>")
def api_version_delete(name, vid):
    _field_or_404(name)
    if project.current(name) == vid:
        return jsonify(ok=False, message="This is the version the game uses; pick another one first."), 400
    project.versions.delete(name, vid)
    return jsonify(ok=True, field=field_detail(name))


CLEANUP_KINDS = ("final", "preview", "try", "imported", "held_back")
ALL_FIELDS = "all fields"  # the name of jobs for every field


def _du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def run_cleanup(w, job):
    """Delete every version not in the game (Project.unused_versions), except the kinds in job["keep"]."""
    w._stage("finding versions not in use", 0.0)
    groups = w.p.unused_versions(w.needed_versions())
    todo = [nv for k, vs in groups.items() if k not in job.get("keep", ()) for nv in vs]
    freed = 0
    for i, (name, vid) in enumerate(todo):
        if w.cancel_flag:
            raise InterruptedError(f"cancelled after {i} versions, {freed / 1e9:.1f} GB freed")
        w._stage(f"deleting {name} {vid}", i / max(1, len(todo)))
        d = w.p.versions.dir(name, vid)
        freed += _du(d)
        w.p.versions.delete(name, vid)
    job["note"] = f"{len(todo)} versions deleted, {freed / 1e9:.1f} GB freed"


JOB_KINDS["cleanup"] = {"run": run_cleanup, "est": lambda name: 30}


def sign_words(name):
    """The field's sign areas the user gave words to."""
    return [b for b in project.meta(name).get("text_boxes") or [] if (b.get("say") or "").strip()]


def run_signfix(w, job):
    """Paint the user's words onto the signs of a version (default the current one) with Qwen-Image 2.1
    (ff7bg/signfix.py): a new version that starts from it, built, and put in the game if it was."""
    name = job["name"]
    vs = w.p.versions
    vid = job.get("version") or w.p.current(name)
    src = vs.render_path(name, vid) if vid else None
    if not src:
        raise ComfyError("there's no version to paint the signs on yet: render the field first")
    boxes = sign_words(name)
    if not boxes:
        raise ComfyError("no sign has words yet: type what a sign should say first")
    comfy = Comfy(w.p.comfy_url)
    if not comfy.online():
        raise ComfyError(f"ComfyUI isn't reachable at {w.p.comfy_url}; start ComfyUI and try again")
    s = signfix.settings(w.p.effective(name, "qwen21"))
    check_requirements(comfy, s)
    vmeta = vs.meta(name, vid)
    fjob = w.p.jobs[name]
    started = time.time()
    render = np.asarray(Image.open(src).convert("RGB"))
    frame = vmeta.get("frame") or (0, 0, 0, 0)
    margins = tuple(a + b for a, b in zip(fjob["pad"], frame))
    cw, ch = fjob["canvas"][2:]
    scale = render.shape[1] / (cw + margins[0] + margins[2])
    w.band = (0.0, 0.85)

    def paint(crop, say):
        p = AI / "input" / f"{name}_sign.png"
        Image.fromarray(crop).save(p)
        wf = signfix.build_workflow(s, comfy.upload(p), say, crop.shape[1], crop.shape[0], f"ff7studio/{name}_sign")
        data = comfy.run(wf, None, lambda: w.cancel_flag)
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))

    new = signfix.repaint(render, boxes, (cw, ch), margins, scale, paint,
                          progress=lambda i, n, say: w._stage(f"painting sign {i + 1} of {n}: {say}", i / n))
    meta = {k: v for k, v in vmeta.items() if k not in ("id", "created", "result", "held_back", "try")}
    meta.update(mode="signs", source=vid, note="signs: " + ", ".join(b["say"] for b in boxes),
                render_seconds=round(time.time() - started, 1))
    nvid = vs.create(name, _png(Image.fromarray(new)), meta)
    if vs.states_dir(name, vid).exists():  # its restyled states come along (a build reuses them)
        shutil.copytree(vs.states_dir(name, vid), vs.states_dir(name, nvid))
    w.p.thumb(name, nvid)
    w.band = (0.85, 1.0)
    w.build(name, nvid)
    if w.p.current(name) == vid:
        w.p.make_current(name, nvid)
    job["version"] = nvid
    job["note"] = f"{len(boxes)} sign{'s' if len(boxes) != 1 else ''} painted"


# about 40 s a sign (Qwen-Image 2.1, 25 steps at 1 MP) and the build
JOB_KINDS["signs"] = {"run": run_signfix, "est": lambda name: 20 + 40 * len(sign_words(name))}


TOUCHUP_MARKS = "touchup_marks.png"  # in a touched-up version's folder: the marks it repainted (since 2026-11-12)


def touchup_settings(p, name, vmeta):
    """The settings a version was rendered with (as a rebuild has them), for repainting parts of it; a version from
    the Upscale workflow, which paints nothing, gets the field's own look's instead."""
    s = p.effective(name, vmeta.get("family"))
    s.update(vmeta.get("settings") or {})
    if vmeta.get("scene"):
        s = dict(s, scene=vmeta["scene"])
    if FAMILIES.get(s.get("family"), {}).get("upscale"):
        s = p.effective(name)
        if FAMILIES.get(s.get("family"), {}).get("upscale"):
            raise ComfyError("touch-up needs a workflow that paints: this field's look only upscales")
    return s


def run_touchup(w, job):
    """Repaint the areas the user marked on a version (ff7bg/touchup.py) with the settings it was rendered with:
    a new version that starts from it, built, and put in the game if it was."""
    name = job["name"]
    vs = w.p.versions
    vid = job.get("version") or w.p.current(name)
    src = vs.render_path(name, vid) if vid else None
    if not src:
        raise ComfyError("there's no version to touch up yet: render the field first")
    comfy = Comfy(w.p.comfy_url)
    if not comfy.online():
        raise ComfyError(f"ComfyUI isn't reachable at {w.p.comfy_url}; start ComfyUI and try again")
    vmeta = vs.meta(name, vid)
    seed = random.randrange(2 ** 31)  # a new try every time: the same marks again give a different repaint
    s = touchup.hint_prompt(dict(touchup_settings(w.p, name, vmeta), seed=seed), job.get("hint"))
    check_requirements(comfy, s)
    refs = [comfy.upload(STYLES / r, "ff7studio_styles") for r in s.get("style_refs", []) if (STYLES / r).exists()]
    fjob = w.p.jobs[name]
    started = time.time()
    render = np.asarray(Image.open(src).convert("RGB"))
    frame = vmeta.get("frame") or (0, 0, 0, 0)
    margins = tuple(a + b for a, b in zip(fjob["pad"], frame))
    cw, ch = fjob["canvas"][2:]
    scale = render.shape[1] / (cw + margins[0] + margins[2])
    marks = np.asarray(Image.open(job["mask"]).convert("RGBA").getchannel("A"), np.float32) / 255
    mask = touchup.full_mask(marks, (cw, ch), margins, (render.shape[1], render.shape[0]))
    strength = float(job.get("strength", 0.6))
    w.band = (0.0, 0.85)

    def paint(crop, model_mask, size):
        names = []
        for kind, arr in (("ctx", crop), ("orig", crop), ("mask", model_mask)):
            p = AI / "input" / f"{name}_touchup_{kind}.png"
            Image.fromarray(arr).save(p)
            names.append(comfy.upload(p, "ff7studio_touchup"))
            p.unlink()
        h, wd = crop.shape[:2]
        wf = build_states_workflow(s, *names, size[0], size[1], wd, h, f"ff7studio/{name}_touchup", refs,
                                   denoise=strength)
        res = np.asarray(Image.open(io.BytesIO(comfy.run(wf, None, lambda: w.cancel_flag))).convert("RGB"))
        return upscalers.finish(res, s, wd, h, lambda: w.cancel_flag)

    try:
        new, n = touchup.repaint(render, mask, scale, paint,
                                 progress=lambda i, k: w._stage(f"repainting marked area {i + 1} of {k}", i / max(k, 1)))
    finally:
        Path(job["mask"]).unlink(missing_ok=True)
    if not n:
        raise ComfyError("nothing was marked")
    hint = (job.get("hint") or "").strip()
    meta = {k: v for k, v in vmeta.items() if k not in ("id", "created", "result", "held_back", "try")}
    meta.update(mode="touchup", source=vid, render_seconds=round(time.time() - started, 1),
                note=f"touched up: {n} area{'s' if n != 1 else ''} at {strength:.0%}" + (f", \"{hint}\"" if hint else ""),
                touchup={"areas": n, "strength": strength, "hint": hint, "seed": seed})
    nvid = vs.create(name, _png(Image.fromarray(new)), meta)
    # its marks, kept so a states pass can make the touch-up again on a state's render (WholeStates)
    Image.fromarray(np.round(marks * 255).astype(np.uint8)).save(vs.dir(name, nvid) / TOUCHUP_MARKS)
    if vs.states_dir(name, vid).exists():  # its restyled states come along (a build reuses them)
        shutil.copytree(vs.states_dir(name, vid), vs.states_dir(name, nvid))
    w.p.thumb(name, nvid)
    w.band = (0.85, 1.0)
    w.build(name, nvid)
    if w.p.current(name) == vid:
        w.p.make_current(name, nvid)
    job["version"] = nvid
    job["note"] = f"{n} area{'s' if n != 1 else ''} touched up"


# about 45 s an area (the field's own model at 1 MP) and the build
JOB_KINDS["touchup"] = {"run": run_touchup, "est": lambda name: 70}


# States as whole renders (2026-11-12, the user: "layer the original state over the original background, then
# generate the whole image using the same seed as the non-state background"): each state is the version's main
# render recipe (its settings, seed, description, the field's sign and moving-part boxes, its input enlargement and
# final upscaler) run again on the original with that state on. states.run takes only where a state changes the
# picture from it, onto the version itself, so fix-ups made on top of the render (the sign fixer, touch-ups) stay
# everywhere else; where one falls inside a state's change it's made again on that state's render.
WHOLE_MAX_DIFF = 6.0  # the recipe re-run with the render's own states must come this close to it (mean, 0-255)
WHOLE_FIX_PAD = 8     # a fix-up counts as inside a state's change within this many original pixels of it


def bg_has_states(bg):
    """The field has toggled states (tiles of a group)."""
    return any(t.param for t in bg.tiles)


def version_chain(name, vid):
    """[(vid, meta)]: the version, then each version it was fixed up from (signs, touch-ups), down to the render (or
    the last one still kept: a cleanup may have deleted the render a fix-up started from)."""
    chain = []
    while vid:
        try:
            m = project.versions.meta(name, vid)
        except (OSError, ValueError):
            break
        if not m:
            break
        chain.append((vid, m))
        vid = m.get("source") if m.get("mode") in ("signs", "touchup") else None
    return chain


class WholeStates:
    """whole(states) for states.run: the version's states as whole renders, made once each (see above). `reason`
    says why a version can't have them (then the states pass paints its states itself)."""

    def __init__(self, w, name, vid, comfy):
        self.w, self.p, self.name, self.comfy = w, w.p, name, comfy
        self.chain = version_chain(name, vid)
        # the render the fix-ups started from; when it's gone, the oldest fix-up kept (its meta carries the render's
        # recipe; its own fix is made again on the states too)
        self.root_vid, self.root = self.chain[-1]
        self.fixes = [(v, m) for v, m in reversed(self.chain) if m.get("mode") in ("signs", "touchup")]  # oldest first
        self.cache = {}
        self.notes = []
        self.reason = self._unusable()
        if self.reason is None:
            s = self.p.effective(name, self.root.get("family"))
            s.update(self.root.get("settings") or {})
            s = self.p.input_settings(name, s)
            if self.root.get("scene"):
                s = dict(s, scene=self.root["scene"])  # its own description, not a new one
            self.s = s
            check_requirements(comfy, s)
            self.refs = [comfy.upload(STYLES / r, "ff7studio_styles") for r in s["style_refs"] if (STYLES / r).exists()]

    def _unusable(self):
        r, fjob = self.root, self.p.jobs[self.name]
        if r.get("mode") not in ("final", "preview", "signs", "touchup") or not r.get("settings"):
            return "the version wasn't rendered here (imported)"
        if r.get("source") and r.get("mode") not in ("signs", "touchup"):  # (a fix-up's source is what it fixed)
            return "its render started from another version"
        if r.get("family") not in REPAINT_FAMILIES:
            return f"{r.get('family')} renders aren't re-run per state"
        if any(fjob["pad"]) or any(r.get("frame") or ()):
            return "renders with a margin aren't re-run per state"
        return None

    def _key(self, states):
        return tuple(sorted((int(k), int(v)) for k, v in states.items()))

    def check(self):
        """The recipe re-run with the render's own states must give the render back (else a model or setting has
        changed since: its states wouldn't match it). Sets `reason` when it doesn't."""
        root_states = {int(k): v for k, v in (self.root.get("rendered_defaults") or {}).items()}
        self.w._stage("checking the render can be re-made for its states", 0.03)
        again = self._render(root_states)
        before = np.asarray(Image.open(self.p.versions.render_path(self.name, self.root_vid)).convert("RGB"))
        if again.shape != before.shape:
            self.reason = "re-making its render gives another size"
            return False
        d = float(np.abs(again.astype(np.int16) - before).mean())
        if d > WHOLE_MAX_DIFF:
            self.reason = f"re-making its render now gives a different picture ({d:.1f} apart)"
            return False
        self.cache[self._key(root_states)] = self._geometry(before, root_states)
        return True

    def __call__(self, states):
        key = self._key(states)
        if key not in self.cache:
            if self.w.cancel_flag:
                raise InterruptedError("cancelled")
            self.w._stage(f"rendering the field with states {dict(key)}", 0.05)
            img = self._render(dict(key))
            img = self._fix(img, dict(key))
            self.cache[key] = self._geometry(img, dict(key))
        return self.cache[key]

    def _geometry(self, img, states):
        """The render as states.run takes the main render: on the original's geometry, cut to the canvas."""
        path = AI / "input_states" / f"{self.name}_render.png"
        path.parent.mkdir(exist_ok=True)
        Image.fromarray(img).save(path, compress_level=1)
        s = self.s
        main = ai.prepare_main(self.p.background(self.name), path, self.p.jobs[self.name],
                               {str(k): v for k, v in states.items()}, do_align=s["align"], frame=self.root.get("frame"))
        return main["ai_hi"]

    def _render(self, states):
        """The root's recipe on the original with these states (as Worker.generate makes a render)."""
        p, name, comfy = self.p, self.name, self.comfy
        fjob, bg = p.jobs[name], p.background(name)
        w, h = fjob["canvas"][2], fjob["canvas"][3]
        s = dict(self.s)
        cancelled = lambda: self.w.cancel_flag  # noqa: E731
        shows = {str(k): v for k, v in states.items()}
        img1x, _ = ai.default_view(bg, shows)
        tmp = AI / "input_states"
        tmp.mkdir(exist_ok=True)
        src = tmp / f"{name}.png"
        Image.fromarray(img1x).save(src)
        sign_boxes = p.sign_boxes(name, s, comfy)
        relative = lambda v: v / max(s["denoise"], 1e-3)  # noqa: E731
        inside = relative(s["text_repaint"])
        moving = p.moving_boxes(name, s)
        boxes = [dict(b, inside=inside) for b in sign_boxes] + [dict(b, inside=relative(s["anim_repaint"])) for b in moving]
        boxes = [b for b in boxes if b["inside"] < 1]
        flat = None
        if s.get("protect_flat") and relative(s["flat_repaint"]) < 1:
            flat = protect.flat_dark(img1x)
            flat = flat if flat.max() > 0.05 else None
        if boxes or flat is not None:
            k = work_size(s, w, h)[0] // w
            repaint, _ = signs.masks(boxes, w, h, k, inside)
            if flat is not None:
                f = np.asarray(Image.fromarray(flat, "F").resize((w * k, h * k), Image.BILINEAR), np.float32)
                rel = min(1.0, relative(s["flat_repaint"]))
                repaint = np.minimum(repaint, np.round((1 - f * (1 - rel)) * 255).astype(np.uint8))
            Image.fromarray(repaint).save(tmp / f"{name}_signs.png")
            s = dict(s, text_mask=comfy.upload(tmp / f"{name}_signs.png"))
        source = src
        if is_local(s["input_upscaler"]):
            tw, th = work_size(s, w, h)
            big = None
            if s["input_upscaler"] == syw.NAME and p.syw_pack():
                big = syw.view(p.syw_pack(), name, bg, ai.shows(shows), tuple(fjob["pad"]))  # not cached: the
                # field's own cache holds its main input
            big = (np.asarray(Image.fromarray(big).resize((tw, th), Image.LANCZOS)) if big is not None
                   else upscalers.upscale(img1x, s["input_upscaler"], tw, th, cancelled))
            source = tmp / f"{name}_up.png"
            Image.fromarray(big).save(source)
        wf = build_workflow(s, comfy.upload(source), w, h, f"ff7studio/{name}_state", self.refs, (0, 0, 0, 0))
        img = np.asarray(Image.open(io.BytesIO(comfy.run(wf, None, cancelled))).convert("RGB"))
        if is_local(s["upscaler"]):
            ow, oh = w * s["out_scale"], h * s["out_scale"]
            if img.shape[:2] != (oh, ow):
                img = upscalers.finish(img, s, ow, oh, cancelled)
        return img

    def _fix(self, img, states):
        """The version's fix-ups made again on a state's render, where they fall inside that state's change: its
        sign words (the sign fixer), its touch-ups (their marks, kept since 2026-11-12; older ones can't be)."""
        if not self.fixes:
            return img
        p, name = self.p, self.name
        fjob, bg = p.jobs[name], p.background(name)
        cw, ch = fjob["canvas"][2:]
        root_states = {str(k): v for k, v in (self.root.get("rendered_defaults") or {}).items()}
        a, _ = ai.default_view(bg, root_states)
        b, _ = ai.default_view(bg, {str(k): v for k, v in states.items()})
        change = cv2.dilate(np.any(a != b, axis=2).astype(np.uint8), np.ones((2 * WHOLE_FIX_PAD + 1,) * 2, np.uint8))
        scale = img.shape[1] / cw
        for fvid, fmeta in self.fixes:
            if fmeta.get("mode") == "signs":
                boxes = [bx for bx in sign_words(name)
                         if change[max(0, int(bx["y1"])):int(math.ceil(bx["y2"])), max(0, int(bx["x1"])):int(math.ceil(bx["x2"]))].any()]
                if not boxes:
                    continue
                s = signfix.settings(p.effective(name, "qwen21"))

                def paint(crop, say):
                    path = AI / "input_states" / f"{name}_sign.png"
                    Image.fromarray(crop).save(path)
                    wf = signfix.build_workflow(s, self.comfy.upload(path), say, crop.shape[1], crop.shape[0],
                                                f"ff7studio/{name}_state_sign")
                    return np.asarray(Image.open(io.BytesIO(self.comfy.run(wf, None, lambda: self.w.cancel_flag))).convert("RGB"))

                self.w._stage(f"painting {len(boxes)} sign{'s' if len(boxes) != 1 else ''} on a state", 0.05)
                img = signfix.repaint(img, boxes, (cw, ch), (0, 0, 0, 0), scale, paint)
            elif fmeta.get("mode") == "touchup":
                marks_file = p.versions.dir(name, fvid) / TOUCHUP_MARKS
                if not marks_file.exists():
                    self.notes.append("a touch-up from before its marks were kept isn't made again on states")
                    continue
                marks = np.asarray(Image.open(marks_file).convert("L"), np.float32) / 255
                inside = cv2.resize(change.astype(np.float32), (marks.shape[1], marks.shape[0]), interpolation=cv2.INTER_NEAREST)
                marks = marks * inside
                if marks.max() <= 0:
                    continue
                t = fmeta.get("touchup") or {}
                s = touchup.hint_prompt(dict(touchup_settings(p, name, fmeta), seed=t.get("seed", 0)), t.get("hint"))
                mask = touchup.full_mask(marks, (cw, ch), (0, 0, 0, 0), (img.shape[1], img.shape[0]))

                def paint_area(crop, model_mask, size, s=s):
                    names = []
                    for kind, arr in (("ctx", crop), ("orig", crop), ("mask", model_mask)):
                        path = AI / "input_states" / f"{name}_touchup_{kind}.png"
                        Image.fromarray(arr).save(path)
                        names.append(self.comfy.upload(path, "ff7studio_touchup"))
                    hh, ww = crop.shape[:2]
                    wf = build_states_workflow(s, *names, size[0], size[1], ww, hh, f"ff7studio/{name}_state_touchup",
                                               self.refs, denoise=float(t.get("strength", 0.6)))
                    res = np.asarray(Image.open(io.BytesIO(self.comfy.run(wf, None, lambda: self.w.cancel_flag))).convert("RGB"))
                    return upscalers.finish(res, s, ww, hh, lambda: self.w.cancel_flag)

                self.w._stage("making a touch-up again on a state", 0.05)
                img, _ = touchup.repaint(img, mask, scale, paint_area)
        return img


@app.get("/api/cleanup")
def api_cleanup_list():
    """What a cleanup would delete: {kind: {count, bytes}}, and how many versions are in the game."""
    if not project.ready:
        abort(503)
    groups = project.unused_versions(worker.needed_versions())
    return jsonify(groups={k: {"count": len(vs), "bytes": sum(_du(project.versions.dir(n, v)) for n, v in vs)}
                           for k, vs in groups.items()},
                   in_game=sum(1 for n in project.jobs if project.current(n)))


@app.post("/api/cleanup")
def api_cleanup():
    """Queue the cleanup (body "keep": kinds to leave alone); it runs between renders, never during one."""
    keep = [k for k in request.get_json(force=True).get("keep", []) if k in CLEANUP_KINDS]
    job = make_job(ALL_FIELDS, "cleanup")
    job["keep"] = keep
    return jsonify(added=worker.enqueue([job]))


@app.post("/api/versions/<name>/import")
def api_version_import(name):
    """Add an image made outside the studio (from the field's input image) as a version."""
    _field_or_404(name)
    f = request.files.get("file")
    if not f:
        abort(400)
    buf = io.BytesIO()
    Image.open(f.stream).convert("RGB").save(buf, "PNG")
    vid = project.versions.create(name, buf.getvalue(), {
        "mode": "imported", "note": Path(f.filename).name, "family": None,
        "settings": {"align": True},  # outside images drift; align them to the original
        "rendered_defaults": project.jobs[name].get("defaults", {}),
    })
    worker.enqueue([make_job(name, "build", version=vid, current=False)])
    return jsonify(id=vid, field=field_detail(name))


# ---------------------------------------------------------------- routes: models, styles

@app.get("/api/models")
def api_models():
    """Model files ComfyUI can load, per loader, for the Advanced panel."""
    comfy = Comfy(project.comfy_url)
    requirements.refresh_models()  # files may have been added: look again
    local = upscalers.available()  # ImageMagick and Upscayl, outside ComfyUI
    local["syw"] = syw.status(project.syw_pack())  # the SYW Unified pack, for the original art
    if not comfy.online():
        return jsonify(online=False, local=local)
    lists = {
        "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
        "unets": ("UNETLoader", "unet_name"),
        "unet_dtypes": ("UNETLoader", "weight_dtype"),
        "clips": ("DualCLIPLoader", "clip_name1"),
        "clip_types_dual": ("DualCLIPLoader", "type"),
        "clip_types_single": ("CLIPLoader", "type"),
        "vaes": ("VAELoader", "vae_name"),
        "controlnets": ("ControlNetLoader", "control_net_name"),
        "upscalers": ("UpscaleModelLoader", "model_name"),
        "loras": ("LoraLoaderModelOnly", "lora_name"),
        "depth_models": ("LoadDA3Model", "model_name"),
        "model_patches": ("ModelPatchLoader", "name"),
    }
    out = {"online": True, "presets": FAMILY_PRESETS, "local": local,
           "nodes": {f: comfy.has_node(spec["node"]) for f, spec in FAMILIES.items() if spec.get("node")},
           "lock_nodes": {f: comfy.has_node(spec["lock_node"]) for f, spec in FAMILIES.items() if spec.get("lock_node")}}
    for key, (node, field) in lists.items():
        try:
            out[key] = comfy.models(node, field)
        except Exception:
            out[key] = []
    return jsonify(out)


@app.get("/api/requirements/<name>")
def api_requirements(name):
    """Per workflow, what this field's settings need that ComfyUI doesn't have, with download links."""
    _field_or_404(name)
    comfy = Comfy(project.comfy_url)
    if not comfy.online():
        return jsonify(online=False)
    return jsonify(online=True, families={
        f: requirements.missing(project.job_settings(name, "final", f), comfy) for f in looks.FAMILIES})


@app.get("/api/styles")
def api_styles():
    files = sorted(p.name for p in STYLES.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return jsonify(files)


@app.post("/api/styles")
def api_styles_upload():
    f = request.files.get("file")
    if not f:
        abort(400)
    name = Path(f.filename).stem.replace(" ", "_") + ".png"
    Image.open(f.stream).convert("RGB").save(STYLES / name)
    return jsonify(name=name)


# ---------------------------------------------------------------- routes: queue

def _scope_names(body):
    scope = body.get("scope")
    if scope == "missing":  # everything without a final-quality current version
        return [n for n in project.jobs if project.field_summary(n)["status"] != "built"]
    if scope == "all":
        return list(project.jobs)
    return [n for n in body.get("names", []) if n in project.jobs]


def start_version(names, body):
    """The version a single-field render starts from (body "source"), if it exists."""
    vid = body.get("source")
    return vid if vid and len(names) == 1 and project.versions.render_path(names[0], vid) else None


@app.post("/api/generate")
def api_generate():
    """mode final / preview (new versions; `families` makes one try per workflow that doesn't
    change the current version) or build (re-cut a version, default the current one)."""
    body = request.get_json(force=True)
    mode = body.get("mode", "final")
    names = _scope_names(body)
    if mode == "build":
        jobs = []
        for n in names:
            vid = body.get("version") or project.current(n)
            if vid and project.versions.render_path(n, vid):
                settings = body.get("settings") if isinstance(body.get("settings"), dict) else None
                jobs.append(make_job(n, "build", version=vid, current=bool(body.get("current")) and vid == project.current(n),
                                     run_states=bool(body.get("states")), settings=settings))
    elif mode in ("final", "preview"):
        fams = [f for f in body.get("families") or [] if f in FAMILIES]
        skipped = []
        if fams:
            # leave out workflows that miss a file or node (e.g. FLUX 3 before its gated download)
            comfy = Comfy(project.comfy_url)
            if names and comfy.online():
                for f in list(fams):
                    miss = requirements.missing(project.job_settings(names[0], mode, f), comfy)
                    if miss["files"] or miss["nodes"] or miss["tools"]:
                        fams.remove(f)
                        skipped.append(f)
            source = start_version(names, body)
            jobs = [make_job(n, mode, family=f, current=False, is_try=True, source=source) for n in names for f in fams]
        elif isinstance(body.get("settings"), dict):  # a try with its own settings: never made current
            source = start_version(names, body)
            fam = body.get("family") if body.get("family") in FAMILIES else None
            jobs = [make_job(n, mode, family=fam, current=False, is_try=True, source=source, settings=body["settings"])
                    for n in names]
        else:
            source = start_version(names, body)
            options = body.get("options") if isinstance(body.get("options"), dict) else {}
            jobs = [make_job(n, mode, fresh=bool(body.get("fresh")), source=source, options=options) for n in names]
        return jsonify(added=worker.enqueue(jobs), skipped=skipped)
    else:
        abort(400)
    return jsonify(added=worker.enqueue(jobs))


@app.post("/api/estimate")
def api_estimate():
    """Fields and seconds a batch would take, with each field's own look."""
    body = request.get_json(force=True)
    names = _scope_names(body)
    mode = body.get("mode", "final")
    total, by_look = 0, {}
    for n in names:
        s = project.job_settings(n, mode)
        total += project.estimate(n, s, mode == "preview", exact_states=False)["total"]
        lid, _ = project.look_of(n)
        by_look[lid] = by_look.get(lid, 0) + 1
    return jsonify(count=len(names), seconds=total, by_look=by_look)


@app.get("/api/queue")
def api_queue():
    return jsonify(worker.state())


@app.post("/api/queue/cancel")
def api_cancel():
    worker.cancel()
    return jsonify(ok=True)


@app.post("/api/queue/remove")
def api_queue_remove():
    worker.remove(int(request.get_json(force=True).get("id", 0)))
    return jsonify(worker.state())


def prefs():
    p = {"auto_install": True}
    if PREFS_FILE.exists():
        p.update(json.loads(PREFS_FILE.read_text(encoding="utf-8")))
    return p


def install_field(name, copies=()):
    """Copy one field's current textures (and its copies') from dist into the game's FFNx folder, as Install does
    for every field. Copies only: textures from other mods in the same folders stay. Returns how many."""
    dest = GAME_WORKDIR / "mods" / "Textures"
    count = 0
    for n in (name, *copies):
        src_dir = DIST / "field" / n
        if not src_dir.exists():
            continue
        for src in src_dir.rglob("*.png"):
            target = dest / src.relative_to(DIST)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            count += 1
    return count


@app.get("/api/prefs")
def api_prefs():
    return jsonify(prefs())


@app.post("/api/prefs")
def api_prefs_set():
    body = request.get_json(force=True)
    p = prefs()
    if "auto_install" in body:
        p["auto_install"] = bool(body["auto_install"])
    PREFS_FILE.write_text(json.dumps(p, indent=1), encoding="utf-8")
    return jsonify(p)


@app.post("/api/install")
def api_install():
    dest = GAME_WORKDIR / "mods" / "Textures"
    if not (DIST / "field").exists():
        return jsonify(ok=False, message="nothing built yet")
    count = 0
    for src in (DIST / "field").rglob("*.png"):
        target = dest / src.relative_to(DIST)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        count += 1
    return jsonify(ok=True, message=f"copied {count} textures to {dest}")


# ---------------------------------------------------------------- main

def load_plugins():
    """Optional extensions: every studio/plugins/*.py with a register(studio) function (studio: this module)."""
    import importlib.util
    for path in sorted(PLUGINS.glob("*.py")) if PLUGINS.exists() else []:
        try:
            spec = importlib.util.spec_from_file_location(f"studio_plugin_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            script = mod.register(sys.modules[__name__])
            if script:
                PLUGIN_SCRIPTS.append(script)
            print(f"plug-in {path.stem} loaded")
        except Exception:
            print(f"plug-in {path.stem} didn't load:")
            traceback.print_exc()


def main():
    global project, worker
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lgp", default=str(GAME_WORKDIR / "data" / "field" / "flevel.lgp"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    STYLES.mkdir(parents=True, exist_ok=True)
    project = Project(args.lgp)
    threading.Thread(target=project.prepare, daemon=True).start()
    worker = Worker(project)
    worker.start()
    load_plugins()

    url = f"http://127.0.0.1:{args.port}"
    print(f"FF7 Field Studio: {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
