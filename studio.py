"""FF7 Field Studio: browse every field, restyle it with ComfyUI, and compare original vs. new
exactly as the game will compose it.

    .venv\\Scripts\\python studio.py          (then open http://127.0.0.1:8765)

Settings live in named looks (studio/looks.json), assigned to fields one by one or by name
pattern; a field can override any setting on top of its look. Every render is kept as a version
(ai/versions/<map>/<id>); the field's current version is what dist/ and the game get.
"""
import argparse
import io
import itertools
import json
import shutil
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict, deque
from pathlib import Path

import numpy as np
from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from PIL import Image

from ff7bg import ai, looks, states, upscalers
from ff7bg.comfy import (
    DEFAULT_SETTINGS, FAMILIES, FAMILY_PRESETS, Comfy, ComfyError, build_workflow, edit_keep, edit_padding,
    tile_count, work_size,
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
LEGACY_SETTINGS = STUDIO / "settings.json"
EDIT_FAMILIES = {name for name, f in FAMILIES.items() if f.get("edit")}
KEEP_IN_GAME = 0.6  # a new version below this share of original outlines doesn't go in game by itself
AVG_STATE_RUNS = 488 / 670  # states-pass sheets per field, game-wide (batch estimates)
# version settings shown in the version strip
SUMMARY_KEYS = ("denoise", "structure", "seed", "style_refs", "style_ref_strength", "detail_pass",
                "detail_denoise", "out_scale", "upscaler", "states_pass", "align", "work_mp")

app = Flask(__name__, static_folder=None)


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
            s.update(out_scale=min(4, s["out_scale"]), detail_pass=False)
        return s

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
        no_detail = edit or fam == "upscale"
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
        elif fam == "moss":
            # measured on Kalm at 1.23 MP: about 7 s with 25 steps at CFG 2.5 (the sampler always runs
            # every step, whatever the repaint strength), plus about 1 s for the depth lock
            per = 0.085 * mp * s["steps"] * (2 if s["cfg"] > 1 else 1)
            first = 1.5 + per + (1 if s["structure"] > 0 else 0)
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
        out = {"render": first, "tiles": 0, "detail": 0, "sheets": 0, "states": 0, "scale": s["out_scale"]}
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

    def make_current(self, name, vid):
        self.versions.publish(name, vid, self.jobs[name]["copies"])
        self.update_meta(name, current=vid)

    def should_replace(self, name, kind):
        """A new final always becomes current; a preview only replaces nothing or another preview."""
        if kind == "final":
            return True
        cur = self.current(name)
        return not cur or self.versions.meta(name, cur).get("mode") == "preview" or not self.versions.has_pages(name, cur)

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
            "state_runs": runs, "state_panels": panels,
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

def make_job(name, kind, family=None, version=None, current="auto", is_try=False, run_states=False):
    """kind: final | preview (render a new version and build it) or build (cut an existing version
    into game textures). current: True, False, or "auto" (Project.should_replace)."""
    job = {"name": name, "kind": kind, "family": family, "version": version, "current": current,
           "try": is_try, "states": run_states}
    if kind == "build":
        s = project.effective(name)
        job["est"] = 4 + (project.estimate(name, s, exact_states=False)["states"] if run_states else 0)
    else:
        s = project.job_settings(name, kind, family)
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

    @staticmethod
    def _key(job):
        return job["name"], job["kind"], job.get("family"), job.get("version"), job.get("try")

    @staticmethod
    def _public(job):
        return {k: job.get(k) for k in ("id", "name", "kind", "family", "version", "est", "try")}

    def enqueue(self, jobs):
        with self.lock:
            busy = {self._key(j) for j in self.queue}
            if self.current:
                busy.add(self._key(self.current["job"]))
            added = 0
            for j in jobs:
                if self._key(j) in busy:
                    continue
                j["id"] = next(self.ids)
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
                if job["kind"] == "build":
                    self.band = (0.0, 1.0)
                    self.build(name, vid, run_states=job.get("states", False))
                    if job["current"] is True:
                        self.p.make_current(name, vid)
                else:
                    vid = self.render(job)
            except (ComfyError, UpscalerError, InterruptedError) as e:
                ok, msg = False, str(e)
            except Exception as e:  # keep the queue alive; surface the error in the UI
                ok, msg = False, f"{type(e).__name__}: {e}"
                traceback.print_exc()
            if ok:
                self.p.update_meta(name, error=None)
            elif msg != "cancelled":
                self.p.update_meta(name, error=msg)
            with self.lock:
                self.recent.append({**self._public(job), "version": vid, "ok": ok, "message": msg,
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
        with_states = not preview and s["states_pass"]
        est = self.p.estimate(name, s, preview)
        split = (est["render"] + est["detail"]) / est["total"] if with_states and est["states"] else 0.97
        self.band = (0.0, split)
        vid = self.generate(name, s, job)
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
            self.p.make_current(name, vid)
        return vid

    def generate(self, name, s, job):
        """Run the main render; returns the new version's id."""
        started = time.time()
        fjob = self.p.jobs[name]
        w, h = fjob["canvas"][2], fjob["canvas"][3]
        source, pad = AI / "input" / f"{name}.png", (0, 0, 0, 0)
        cancelled = lambda: self.cancel_flag
        if s.get("family") == "upscale" and is_local(s["upscaler"]):
            # ImageMagick / Upscayl on the original: no ComfyUI needed
            self._stage("upscaling", 0.1)
            img = np.asarray(Image.open(source).convert("RGB"))
            data = _png(Image.fromarray(upscalers.upscale(img, s["upscaler"], w * s["out_scale"], h * s["out_scale"], cancelled)), 4)
            return self._new_version(name, s, job, fjob, data, pad, started)
        comfy = Comfy(self.p.comfy_url)
        if not comfy.online():
            raise ComfyError(f"ComfyUI isn't reachable at {self.p.comfy_url}; start ComfyUI and try again")
        self._stage("uploading", 0.02)
        if s.get("family") in EDIT_FAMILIES:
            # edit models reframe slightly: give them a blurred margin, mostly cropped off inside the workflow
            pad = edit_padding(w, h, s["edit_margin"])
            padded = ai._pad(np.asarray(Image.open(source).convert("RGB")), *pad)
            source = AI / "input_edit" / f"{name}.png"
            source.parent.mkdir(exist_ok=True)
            Image.fromarray(padded).save(source)
        if is_local(s["input_upscaler"]) and (s["family"] != "upscale" or (
                s["upscaler"] == "seedvr2" and not s["seedvr2_input"])):
            # ImageMagick / Upscayl enlarge the original art here; the graph's resize then changes nothing
            self._stage("upscaling input", 0.03)
            img = np.asarray(Image.open(source).convert("RGB"))
            iw, ih = img.shape[1], img.shape[0]
            tw, th = (iw * s["out_scale"], ih * s["out_scale"]) if s["family"] == "upscale" else work_size(s, iw, ih)
            source = AI / "input_up" / f"{name}.png"
            source.parent.mkdir(exist_ok=True)
            Image.fromarray(upscalers.upscale(img, s["input_upscaler"], tw, th, cancelled)).save(source)
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
        return self._new_version(name, s, job, fjob, data, pad, started)

    def _new_version(self, name, s, job, fjob, data, pad, started):
        self._stage("saving", 0.99)
        look_id, _ = self.p.look_of(name)
        vid = self.p.versions.create(name, data, {
            "mode": job["kind"], "family": s["family"], "try": bool(job.get("try")),
            "look": look_id, "look_name": self.p.looks.get(look_id).get("name", look_id),
            "settings": {k: s[k] for k in DEFAULT_SETTINGS},
            "rendered_defaults": fjob.get("defaults", {}),  # states on in the image sent to the generator
            "frame": list(edit_keep(pad)),  # margin returned around the canvas
            "render_seconds": round(time.time() - started, 1),
        })
        self.p.thumb(name, vid)
        return vid

    def build(self, name, vid, s=None, run_states=False):
        """Cut a version's render into game textures (inside the version); optionally run the states
        pass first. Without it, restyled states saved with the version are reused."""
        vs = self.p.versions
        out = vs.render_path(name, vid) if vid else None
        if not out:
            raise ComfyError("this version has no image")
        vmeta = vs.meta(name, vid)
        if s is None:  # rebuild with the settings it was made with
            s = self.p.effective(name, vmeta.get("family"))
            current_states = {k: s[k] for k in ("state_denoise", "state_denoise_boost")}
            s.update(vmeta.get("settings") or {})
            if run_states:  # redoing the states: with today's states settings
                s.update(current_states)
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
                refs = [comfy.upload(STYLES / r, "ff7studio_styles") for r in s["style_refs"] if (STYLES / r).exists()]
            panels = states.run(
                comfy, bg, main["rect"], main, s, refs, sdir,
                progress=lambda i, n, k: self._stage(f"restyling states, sheet {i + 1}/{n} ({k} panels)", 0.05 + 0.9 * i / n),
                cancelled=lambda: self.cancel_flag)
        else:
            panels = states.load(sdir, main["scale"])
        self._stage("building game textures", 0.97)
        shutil.rmtree(vs.pages_root(name, vid), ignore_errors=True)
        r = ai.build_pages(bg, main, fjob, vs.pages_root(name, vid), vs.review_dir(name, vid), panels)
        b, a = r["drift_before"], r["drift_after"]
        vs.update_meta(name, vid, result={
            "scale": r["scale"], "pages": r["pages"], "edges_kept": round(r["edges_kept"], 3),
            "aligned": r["aligned"], "drift_before": round(b.median, 2), "drift_after": round(a.median, 2),
            "missing": round(r["missing"], 4), "flag": ", ".join(ai.flags_for(r)), "built_at": time.time(),
            "state_panels": r["state_panels"],
        })
        if self.p.current(name) == vid:  # rebuilt the one the game uses
            self.p.make_current(name, vid)


# ---------------------------------------------------------------- rendering helpers

render_cache = OrderedDict()
render_lock = threading.Lock()


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


@app.get("/api/status")
def api_status():
    url = project.comfy_url
    return jsonify(ready=project.ready, status=project.status, comfy_online=Comfy(url).online(),
                   comfy_url=url, game_mods=str(GAME_WORKDIR / "mods" / "Textures"))


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
    else:
        scale, source = 1, PageCache(bg).tile
    group_states = {t.state for t in ts}
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    strip = Image.new("RGB", (w * len(frames), h))
    for i, frame in enumerate(frames):
        shown = base | {(group, s) for s in group_states if s & frame}
        img = render_composite(bg, rect, source, scale, _include(layers, fx, shown))
        strip.paste(img.crop((x0 * scale, y0 * scale, x1 * scale, y1 * scale)), (i * w, 0))
    if which == "new":
        resp = send_file(io.BytesIO(_jpeg(strip)), mimetype="image/jpeg")
    else:
        resp = send_file(io.BytesIO(_png(strip)), mimetype="image/png")
    resp.headers["X-Frames"] = json.dumps(frames)
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
    local = upscalers.available()  # ImageMagick and Upscayl, outside ComfyUI
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
    }
    out = {"online": True, "presets": FAMILY_PRESETS, "local": local,
           "nodes": {f: comfy.has_node(spec["node"]) for f, spec in FAMILIES.items() if spec.get("node")}}
    for key, (node, field) in lists.items():
        try:
            out[key] = comfy.models(node, field)
        except Exception:
            out[key] = []
    return jsonify(out)


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
                jobs.append(make_job(n, "build", version=vid, current=False, run_states=bool(body.get("states"))))
    elif mode in ("final", "preview"):
        fams = [f for f in body.get("families") or [] if f in FAMILIES]
        skipped = []
        if fams:
            # leave out workflows whose diffusion model isn't installed (e.g. FLUX 3 before its gated download)
            try:
                have = set(Comfy(project.comfy_url).models("UNETLoader", "unet_name"))
            except Exception:
                have = None
            if have is not None and names:
                comfy = Comfy(project.comfy_url)
                for f in list(fams):
                    s = project.effective(names[0], f)
                    node = FAMILIES[f].get("node")
                    if (s["model_source"] == "separate" and s["unet"] not in have) or (node and not comfy.has_node(node)):
                        fams.remove(f)
                        skipped.append(f)
            jobs = [make_job(n, mode, family=f, current=False, is_try=True) for n in names for f in fams]
        else:
            jobs = [make_job(n, mode) for n in names]
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

    url = f"http://127.0.0.1:{args.port}"
    print(f"FF7 Field Studio: {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
