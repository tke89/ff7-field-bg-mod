"""Named looks: reusable generation settings, assigned to fields individually or by name pattern.

A look stores what differs from the built-in defaults in two layers:
  settings  - shared by every workflow: prompt, style images, output size, upscaler, the chosen
              workflow ("family"), ...
  profiles  - per workflow (flux1 / krea2 / qwen21): model files, steps, repaint strength, structure
              lock, ... so switching a look between workflows never loses another workflow's tuning.
A field can add its own override with the same two layers on top of its look.

Which look a field uses: its explicit choice, else the first assignment pattern matching its name
(or the name of a map sharing its background), else the default look.
"""
import copy
import fnmatch
import json
import re
import threading
import time
from pathlib import Path

from .comfy import DEFAULT_SETTINGS, FAMILY_PRESETS, SEEDVR2
from .upscalers import is_local

FAMILIES = ("flux1", "krea2", "qwen21", "flux3k", "mageflow", "fibo2", "moss", "upscale")

# Settings that belong to a workflow (stored per profile); everything else is shared by the look.
FAMILY_KEYS = {
    "model_source", "checkpoint", "unet", "unet_dtype", "clip1", "clip2", "clip_type", "vae",
    "controlnet", "lora", "lora_strength", "steps", "cfg", "denoise", "structure", "structure_end",
    "canny_low", "canny_high", "guidance", "style_ref_strength", "detail_pass", "detail_denoise",
    "detail_steps", "detail_structure", "detail_style", "align", "edit_margin", "work_mp",
    "state_denoise_boost", "state_denoise", "input_upscaler", "input_scale",
}
SHARED_KEYS = (set(DEFAULT_SETTINGS) - FAMILY_KEYS) | {"family"}


def family_defaults(family):
    """Built-in settings for a workflow (before any look)."""
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(FAMILY_PRESETS.get(family, {}))
    s["family"] = family
    return s


def clean(values):
    """Known keys only, coerced to the default's type."""
    out = {}
    for k, v in values.items():
        if k == "family":
            if v in FAMILIES:
                out[k] = v
            continue
        if k not in DEFAULT_SETTINGS:
            continue
        ref = DEFAULT_SETTINGS[k]
        if isinstance(ref, bool):
            out[k] = bool(v)
        elif isinstance(ref, int):
            out[k] = int(v)
        elif isinstance(ref, float):
            out[k] = float(v)
        elif isinstance(ref, list):
            out[k] = [str(x) for x in v]
        else:
            out[k] = str(v)
    return out


def empty_layer():
    return {"settings": {}, "profiles": {}}


def layer_family(layers, fallback="flux1"):
    """The workflow chosen by the top-most layer that sets one."""
    for layer in reversed(layers):
        fam = (layer or {}).get("settings", {}).get("family")
        if fam:
            return fam
    return fallback


def resolve(layers, family=None):
    """Effective settings from stacked layers (look, then field override), for one workflow."""
    fam = family or layer_family(layers)
    s = family_defaults(fam)
    for layer in layers:
        if not layer:
            continue
        s.update({k: v for k, v in layer.get("settings", {}).items() if k != "family"})
        s.update(layer.get("profiles", {}).get(fam, {}))
    s["family"] = fam
    return s


def patch_layer(layer, patch, family, below, below_family):
    """Write `patch` into `layer` (shared keys into settings, workflow keys into that workflow's
    profile), then drop anything equal to what the layers below already give.

    below: effective settings without this layer, for `family`.
    below_family: the workflow the layers below would pick on their own."""
    for k, v in clean(patch).items():
        if k in FAMILY_KEYS:
            layer.setdefault("profiles", {}).setdefault(family, {})[k] = v
        else:
            layer.setdefault("settings", {})[k] = v
    # prune redundant values
    settings = layer.get("settings", {})
    for k in list(settings):
        if k == "family":
            if settings[k] == below_family:
                del settings[k]
        elif below.get(k) == settings[k]:
            del settings[k]
    prof = layer.get("profiles", {}).get(family, {})
    for k in list(prof):
        if below.get(k) == prof[k]:
            del prof[k]
    if family in layer.get("profiles", {}) and not layer["profiles"][family]:
        del layer["profiles"][family]
    return layer


def prune(layer, below_layers=()):
    """Drop values a layer repeats from the built-in defaults (and any layers below it)."""
    for fam in list(layer.get("profiles", {})):
        below = resolve(list(below_layers), fam)
        prof = layer["profiles"][fam]
        for k in list(prof):
            if below.get(k) == prof[k]:
                del prof[k]
        if not prof:
            del layer["profiles"][fam]
    below = resolve(list(below_layers), layer_family([*below_layers, layer]))
    settings = layer.get("settings", {})
    for k in list(settings):
        if k != "family" and below.get(k) == settings[k]:
            del settings[k]
    return layer


def layer_keys(layer, family):
    """Setting names a layer changes for one workflow (for the 'changed' markers)."""
    if not layer:
        return []
    keys = set(layer.get("settings", {})) | set(layer.get("profiles", {}).get(family, {}))
    return sorted(keys)


def is_empty(layer):
    return not layer or (not layer.get("settings") and not any(layer.get("profiles", {}).values()))


def split_flat(flat, family):
    """Old flat settings dict -> layer."""
    layer = empty_layer()
    fam = flat.get("family", family)
    for k, v in clean(flat).items():
        if k == "family":
            layer["settings"]["family"] = v
        elif k in FAMILY_KEYS:
            layer["profiles"].setdefault(fam, {})[k] = v
        else:
            layer["settings"][k] = v
    return layer


def pin_input_upscaler(layer, below):
    """Until 2026-10-21 the original art went through the output upscaler when that was an ESRGAN model,
    through SeedVR2 with "seedvr2_input", else through input_upscaler; now always through input_upscaler.
    Pin the old choice in a layer that relied on it (below: effective settings under the layer).
    Returns whether the layer changed."""
    st = layer.get("settings", {})
    if "input_upscaler" in st or not ({"upscaler", "seedvr2_input"} & set(st)):
        return False
    s = {**below, **st}
    up = s["upscaler"]
    if up == SEEDVR2:
        old = SEEDVR2 if s["seedvr2_input"] else s["input_upscaler"]
    else:
        old = s["input_upscaler"] if is_local(up) else up
    if old == s["input_upscaler"]:
        return False
    st["input_upscaler"] = old
    # the Upscale workflow reads input_upscaler as "the upscaler before SeedVR2": keep its old one
    layer.setdefault("profiles", {}).setdefault("upscale", {}).setdefault("input_upscaler", s["input_upscaler"])
    return True


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "look"


class LookStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = None

    def load(self, legacy_settings=None):
        with self.lock:
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
            else:
                default = empty_layer()
                if legacy_settings and legacy_settings.exists():
                    flat = json.loads(legacy_settings.read_text())
                    flat.pop("comfy_url", None)
                    # a "separate files" setup without a diffusion model can't render; don't carry it over
                    if flat.get("model_source") == "separate" and not flat.get("unet"):
                        for k in ("model_source", "unet", "unet_dtype", "clip1", "clip2", "clip_type", "vae"):
                            flat.pop(k, None)
                    default = prune(split_flat(flat, flat.get("family", "flux1")))
                    legacy_settings.rename(legacy_settings.with_name("settings.pre-looks.json"))
                default["name"] = "Default"
                self.data = {"default": "default", "looks": {"default": default}, "assignments": [],
                             "comfy_url": "http://127.0.0.1:8000"}
                self.save()
            return self.data

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)

    # ---- lookup

    def looks(self):
        return self.data["looks"]

    def get(self, look_id):
        return self.data["looks"].get(look_id) or self.data["looks"][self.data["default"]]

    def resolve_look(self, name, copies=(), explicit=None):
        """(look id, how it was chosen: 'field' | 'pattern:<p>' | 'default')."""
        with self.lock:
            if explicit and explicit in self.data["looks"]:
                return explicit, "field"
            for a in self.data["assignments"]:
                if a["look"] in self.data["looks"] and any(
                        fnmatch.fnmatch(n, a["pattern"].lower()) for n in (name, *copies)):
                    return a["look"], f"pattern:{a['pattern']}"
            return self.data["default"], "default"

    def look_settings(self, look_id, family=None):
        return resolve([self.get(look_id)], family)

    # ---- editing

    def patch(self, look_id, patch, family=None):
        with self.lock:
            look = self.get(look_id)
            fam = patch.get("family") if patch.get("family") in FAMILIES else (family or layer_family([look]))
            patch_layer(look, patch, fam, family_defaults(fam), DEFAULT_SETTINGS["family"])
            self.save()

    def create(self, name, source_layers=None):
        with self.lock:
            base, i = slug(name), 2
            look_id = base
            while look_id in self.data["looks"]:
                look_id, i = f"{base}-{i}", i + 1
            layer = empty_layer()
            for src in source_layers or []:  # merge look + field override into one look
                if not src:
                    continue
                layer["settings"].update(copy.deepcopy(src.get("settings", {})))
                for fam, prof in src.get("profiles", {}).items():
                    layer["profiles"].setdefault(fam, {}).update(copy.deepcopy(prof))
            layer["name"] = name
            layer["created"] = time.time()
            self.data["looks"][look_id] = layer
            self.save()
            return look_id

    def rename(self, look_id, name):
        with self.lock:
            self.data["looks"][look_id]["name"] = name
            self.save()

    def delete(self, look_id):
        with self.lock:
            if look_id == self.data["default"] or look_id not in self.data["looks"]:
                return False
            del self.data["looks"][look_id]
            self.data["assignments"] = [a for a in self.data["assignments"] if a["look"] != look_id]
            self.save()
            return True

    def set_default(self, look_id):
        with self.lock:
            if look_id in self.data["looks"]:
                self.data["default"] = look_id
                self.save()

    def set_assignments(self, rows):
        with self.lock:
            self.data["assignments"] = [
                {"pattern": r["pattern"].strip(), "look": r["look"]}
                for r in rows if r.get("pattern", "").strip() and r.get("look") in self.data["looks"]]
            self.save()
