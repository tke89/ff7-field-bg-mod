"""The Studio's looks applied to rendered views of a model or battle scene: paint.paint's `restyle`.

The first view is restyled whole with the look's own workflow (comfy.build_workflow); later views repaint only what
they're the first to see well, around the paint so far (comfy.build_states_workflow, the states pass's masked
repaint), so the views make one painting. Views are rendered at the AI's working size: no enlarging on the way in
or out (out_scale 1). One colour fit (keep_colors) from the first view is used for all of them."""
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .. import ai, comfy as C, looks

STUDIO = Path(__file__).resolve().parents[2]
STYLES = STUDIO / "studio" / "styles"
TMP = STUDIO / "work" / "models" / "restyle"

# what the picture is, ahead of the look's own description (its prompt is written for field backgrounds)
HINTS = {
    "scene": "A JRPG battle arena seen from the battle camera, the whole picture is the place itself.",
    "model": "A JRPG character or object model on a plain black background: paint its own surfaces (skin, cloth, "
             "hair, metal, wood) in the same style, keep its shape, colours and markings, add nothing around it.",
}
# ...and for a texture painted flat (Restyler.sheet: a battle scene's textures). Not "ground or walls": oo's strip of
# sky and far mountains, painted with that, got a big rock mesa in the middle (2026-11-07)
SHEET_HINT = ("A flat texture sheet from a JRPG battle arena (ground, walls, rock, or a strip of sky and far scenery), "
              "seen straight on: paint its surface in the same style, keep everything exactly where it is (the layout, "
              "shapes, outlines, horizon and colours) and add nothing; its edges continue into each other.")
PANORAMA_HINT = ("A wide panorama of the sky and far scenery all round a JRPG battle arena (clouds, distant mountains, "
                 "far walls), seen from its middle: paint it in the same style, keep the horizon, outlines and colours "
                 "exactly where they are and add nothing; its left and right edges continue into each other.")
SHEET_PAD = 8       # Restyler.sheet: wrap padding, this fraction of the sheet's size on each side
MAX_SHEET_SIDE = 8192  # ...and the longest side its detail may take it to (pixels)
SHEET_REPAINT = 0.85  # ...and its repaint, this much of the look's: a sheet has no scene around it to keep it honest


def look_settings(look_id=None, family=None):
    """A look's effective settings (the Default look unless look_id), read without changing looks.json."""
    data = json.loads((STUDIO / "studio" / "looks.json").read_text(encoding="utf-8"))
    look = data["looks"].get(look_id or data["default"]) or data["looks"][data["default"]]
    return looks.resolve([look], family)


def _png(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path)
    return path


DEFAULT_UPSCALER = "4x-UltraSharp.safetensors"


def upscale(rgb, w, h, upscaler=DEFAULT_UPSCALER, s=None, url="http://127.0.0.1:8000"):
    """An RGB image enlarged to exactly w x h by any of the Studio's upscalers (the look's list): a ComfyUI upscale
    model by its file name ("4x-UltraSharp.safetensors"), "seedvr2" (SeedVR2, with the look's seedvr2 settings in s),
    "magick" or "upscayl:<model>" (outside ComfyUI, ff7bg.upscalers), or "" / "lanczos" for a plain resize. The SYW
    pack is the fields' own art, nothing for model textures: a plain resize too."""
    from .. import upscalers
    ih, iw = rgb.shape[:2]
    if (iw, ih) == (w, h):
        return rgb
    if not upscaler or upscaler in ("lanczos", upscalers.SYW) or (w <= iw and h <= ih):
        return np.asarray(Image.fromarray(rgb).resize((w, h), Image.LANCZOS))
    if upscalers.is_local(upscaler):
        return upscalers.upscale(rgb, upscaler, w, h)
    s = {**C.DEFAULT_SETTINGS, **(s or {}), "input_upscaler": upscaler, "out_scale": 4}  # (the look has its own)
    comfy = C.Comfy(url)
    src = comfy.upload(_png(np.ascontiguousarray(rgb), TMP / "upscale_in.png"))
    wf = {"src": {"class_type": "LoadImage", "inputs": {"image": src}}}
    out = C._resize(wf, "work", s, ["src", 0], w, h)  # the Studio's own chain: SeedVR2 or a model, then exact size
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": out, "filename_prefix": "ff7studio/models/upscale"}}
    img = np.asarray(Image.open(io.BytesIO(comfy.run(wf))).convert("RGB"))
    return img if img.shape[:2] == (h, w) else np.asarray(Image.fromarray(img).resize((w, h), Image.LANCZOS))


SYW = "syw"  # the upscaler name for the SYW Unified packs (fields: ff7bg.syw; battle textures: syw_battle_*)
_syw_battle = {}  # pack path -> (stamp, {texture name "STAGE10_T00_00": where in the pack})


def syw_battle_pack(pref=None):
    """SYW Unified Battle Textures (Tsunamods, for 7th Heaven): `pref` if it exists, else the newest such archive in
    7th Heaven's library (~/7th Heaven), or None. A ready-made 4x of 87 of the 90 battle scenes' textures (and two
    enemies'), named as the game loads them (FF7SYWU/Base/battle/STAGE10_T00_00.dds): the same names as our
    texture_key. Square Enix's art upscaled by the SYW team: for use on this PC, as the fields' pack."""
    if pref and Path(pref).exists():
        return Path(pref)
    lib = Path.home() / "7th Heaven"
    found = sorted(lib.glob("*SYW_Unified_Battle_Textures*.iro"), key=lambda p: p.stat().st_mtime) if lib.is_dir() else []
    return found[-1] if found else None


def syw_battle_index(pack):
    """{texture name ("STAGE10_T00_00", upper case): where} of the battle pack, read once."""
    from .. import syw
    pack = Path(pack)
    stamp = pack.stat().st_mtime
    hit = _syw_battle.get(str(pack))
    if hit and hit[0] == stamp:
        return hit[1]
    out = {}
    for name, offset, length in syw._iro_entries(pack):
        if name.lower().endswith(".dds") and "/battle/" in name.lower():
            out[name.rsplit("/", 1)[-1][:-4].upper()] = (offset, length)
    _syw_battle[str(pack)] = (stamp, out)
    return out


def syw_battle_texture(ref, pack=None):
    """A mesh texture (mesh.TexRef, battle) as SYW Unified has it (RGBA at 4x), or None where the pack hasn't it."""
    from .. import syw
    pack = pack or syw_battle_pack()
    if pack is None or not ref.key.lower().startswith("battle/"):
        return None
    where = syw_battle_index(pack).get(ref.name.split("/", 1)[1].upper())
    return None if where is None else np.array(syw._decoded(str(pack), where))


def _cache_tag(upscaler):
    return "".join(c if c.isalnum() else "_" for c in (upscaler or "lanczos"))[:40]


def enlarge_textures(mesh, scale=4, url="http://127.0.0.1:8000", upscaler=DEFAULT_UPSCALER, s=None):
    """The mesh's textures enlarged `scale` times by `upscaler` (upscale(); RGBA per texture, the alpha kept hard):
    the views and sheets the AI paints are then sharp, as the fields' enlarged art is. Lanczos-enlarged 128-256 px
    textures made a blurry picture the look's repaint kept blurry (oq, MOSS 0.55: grain, no painting). Cached per
    texture, scale and upscaler."""
    from . import paint
    out = []
    for t in mesh.textures:
        rgba = t.tex.rgba(t.palette)
        h, w = rgba.shape[:2]
        cache = TMP.parent / "enlarged" / f"{t.name.replace('/', '_')}_{scale}x_{_cache_tag(upscaler)}.png"
        if cache.exists():
            out.append(np.asarray(Image.open(cache).convert("RGBA")))
            continue
        rgb = None
        if upscaler == SYW:  # SYW Unified's ready-made 4x where it has this texture, resized to the scale asked for
            got = syw_battle_texture(t)
            if got is not None:
                rgb = np.asarray(Image.fromarray(np.ascontiguousarray(got[..., :3])).resize((w * scale, h * scale), Image.LANCZOS))
        if rgb is None:  # (SYW without it: the default upscaler, as fields the pack lacks get theirs)
            rgb = upscale(rgba[..., :3].copy(), w * scale, h * scale, DEFAULT_UPSCALER if upscaler == SYW else upscaler, s, url)
        big = np.dstack([rgb, paint.enlarge(rgba, scale)[..., 3]])
        _png(big, cache)
        out.append(big)
    return out


def resize_textures(images, sizes, url="http://127.0.0.1:8000", upscaler=DEFAULT_UPSCALER, s=None):
    """Painted textures (RGBA) to the sizes the game gets (output scale): larger through `upscaler` (upscale()),
    smaller or the same by Lanczos alone; the alpha kept hard. sizes: per image (w, h)."""
    out = []
    for img, (w, h) in zip(images, sizes):
        ih, iw = img.shape[:2]
        if (iw, ih) == (w, h):
            out.append(img)
            continue
        rgb = upscale(np.ascontiguousarray(img[..., :3]), w, h, upscaler, s, url)
        a = np.asarray(Image.fromarray(img[..., 3]).resize((w, h), Image.NEAREST))
        out.append(np.dstack([rgb, a]))
    return out


def _wrap_blend(out, P, Hc, Wc, wrap):
    """The painted sheet (padded by P each side) cut to its Hc x Wc middle, its wrapping edges made to meet: the AI
    painted each edge twice, once in the middle and once in the far side's padding, differently (oo's sky panorama
    showed a vertical line where its two ends met). Each edge goes half and half with its other painting right at the
    edge, fading to its own over P pixels."""
    inner = out[P:P + Hc, P:P + Wc].astype(np.float32)
    full = out.astype(np.float32)
    ramp = np.linspace(0.5, 0.0, P, dtype=np.float32)  # weight of the other painting: 0.5 at the edge, 0 inside
    res = inner.copy()
    if wrap[0]:
        w = ramp[None, :, None]
        res[:, :P] = inner[:, :P] * (1 - w) + full[P:P + Hc, P + Wc:P + Wc + P] * w          # left edge
        res[:, Wc - P:] = inner[:, Wc - P:] * (1 - w[:, ::-1]) + full[P:P + Hc, 0:P] * w[:, ::-1]  # right edge
    if wrap[1]:
        w = ramp[:, None, None]
        top = full[P + Hc:P + Hc + P, P:P + Wc]
        bottom = full[0:P, P:P + Wc]
        res[:P] = res[:P] * (1 - w) + top * w
        res[Hc - P:] = res[Hc - P:] * (1 - w[::-1]) + bottom * w[::-1]
    return np.clip(res, 0, 255).astype(np.uint8)


class Restyler:
    """restyle(orig, cur, mask) for paint.paint, with the look's settings `s` on ComfyUI at `url`."""

    def __init__(self, s, url="http://127.0.0.1:8000", kind="scene", name="model", describe=None, denoise=None,
                 on_progress=None, prompt="", strength=None):
        """prompt: the user's own words for this model or scene, put ahead of the describer's in every view's and
        sheet's prompt. strength: the user's paint strength (the repaint, 0-1) for the views and the sheets alike;
        None: the look's own, and the sheets a SHEET_REPAINT share of it. describe: the automatic description on or
        off (None: the look's describe_scene)."""
        self.comfy = C.Comfy(url)
        if not self.comfy.online():
            raise C.ComfyError(f"ComfyUI isn't reachable at {url}")
        # at the working size in and out: nothing to enlarge (out_scale 1 skips the upscalers), no detail pass,
        # no signs or moving-parts masks (those are the fields'). input_scale 0: the views are rendered at the AI's
        # working size already; the look's input_scale (4 in the user's, for 1x field art) made a 1280x960 view a
        # 5120x3840 render (2026-11-07, battle scene oo: 5 minutes, and the AI can't paint coherently that big)
        self.s = dict(s, out_scale=1, detail_pass=False, input_upscaler="", input_scale=0, tries=1, retry=False)
        for k in ("text_mask", "text_keep"):
            self.s.pop(k, None)
        self.kind, self.name = kind, name
        self.describe = s.get("describe_scene") if describe is None else describe
        self.prompt = (prompt or "").strip()
        self.strength = None if strength is None else float(min(0.95, max(0.05, strength)))
        if self.strength is not None:
            self.s["denoise"] = self.strength
        self.denoise = denoise if denoise is not None else self.strength  # later views' repaint (None: the look's)
        self.on_progress = on_progress
        self.color = None       # keep_colors' fit, from the first view
        self.refs = None
        self.n = 0

    def _refs(self):
        if self.refs is None:
            self.refs = [self.comfy.upload(STYLES / r, "ff7studio_styles") for r in self.s.get("style_refs", [])
                         if (STYLES / r).exists()]
        return self.refs

    words = None  # the describer's words for the scene (prepare), shared by the views and the sheets

    def prepare(self, orig):
        """The scene's description, once, from its first view (describe_scene): the views' prompt gets HINTS[kind]
        and it, the sheets' SHEET_HINT and it."""
        if self.words is None:
            self.words = ""
            if self.describe and self.s.get("family") != "upscale":
                h, w = orig.shape[:2]
                name = self.comfy.upload(_png(orig, TMP / f"{self.name}_describe.png"))
                self.words = C.clean_caption(self.comfy.run_text(C.build_caption_workflow(name, self.s, w, h)))
        self.s["scene"] = " ".join(x for x in (HINTS.get(self.kind, ""), self.prompt, self.words) if x)

    def _finish(self, out, orig, keep, refit):
        """keep_colors: out pulled toward the original's colours by the look's amount (a fit from out to orig on the
        pixels `keep` marks; refit: fit afresh, else the first view's fit for all the views)."""
        k = float(self.s.get("keep_colors", 0) or 0)
        if k <= 0:
            return out
        M = ai.fit_color(out.reshape(-1, 3)[keep], orig.reshape(-1, 3)[keep]) if refit or self.color is None else self.color
        if not refit:
            self.color = M
        rgb = out.astype(np.float32)
        return np.clip(rgb * (1 - k) + (rgb @ M[:3] + M[3]) * k, 0, 255).astype(np.uint8)

    def sheet(self, img, name="texture", wrap=(True, True), hint=None, detail=1):
        """A texture painted flat (paint.paint's `sheets`), RGBA in and out, the alpha kept. Padded by 1/SHEET_PAD of
        its size: wrapped across the edges the scene joins (wrap: (left-right, top-bottom), paint.wraps), so they're
        painted continuing into each other and the tiles stay seamless; mirrored across the others. No depth lock: a
        flat sheet's estimated depth is made up, and with it oo's strip of far mountains came out as a rock mesa. Its
        own colour fit (a sheet isn't a view).

        hint: what it is (default SHEET_HINT). detail: > 1 paints it that many times bigger: the painting enlarged by
        the upscaler, then the Studio's detail pass (tiles repainted lightly at full size) adds what a texture that big
        can show (scenepaint: the floor stretched over the arena, blurry however it was painted at 4x)."""
        h, w = img.shape[:2]
        p = max(16, (max(w, h) // SHEET_PAD) // 16 * 16)
        rgb = img[..., :3]
        rgb = np.pad(rgb, ((0, 0), (p, p), (0, 0)), mode="wrap" if wrap[0] else "reflect")
        padded = np.pad(rgb, ((p, p), (0, 0), (0, 0)), mode="wrap" if wrap[1] else "reflect")
        PH, PW = padded.shape[:2]
        self.n += 1
        tag = f"{self.name}_sheet{self.n:02d}"
        s = dict(self.s, scene=" ".join(x for x in (hint or SHEET_HINT, self.prompt, self.words) if x), depth_lock=0)
        if self.strength is None and not C._family(s).get("edit"):
            # the look's own strength: a gentler repaint for the sheets (the edit models repaint fully); the user's
            # own paint strength is used as it is
            s["denoise"] = round(float(s["denoise"]) * SHEET_REPAINT, 3)
        k = max(1, int(detail))
        # no bigger than MAX_SHEET_SIDE: the game is a 32-bit program (a 16384 px texture is ~1 GB decoded; it has run
        # out of address space before), and the detail pass works on the whole enlarged picture
        k = min(k, max(1, MAX_SHEET_SIDE // max(w, h)))
        if k > 1 and not C._family(s).get("edit") and s.get("family") != "upscale":
            s.update(out_scale=k, detail_pass=True, upscaler=DEFAULT_UPSCALER)
        else:
            k = 1
        inp = self.comfy.upload(_png(padded, TMP / f"{tag}_in.png"))
        out = np.asarray(Image.open(io.BytesIO(self.comfy.run(
            C.build_workflow(s, inp, PW, PH, f"ff7studio/models/{tag}", self._refs()), self.on_progress))).convert("RGB"))
        if out.shape[:2] != (PH * k, PW * k):
            out = np.asarray(Image.fromarray(out).resize((PW * k, PH * k), Image.LANCZOS))
        out = _wrap_blend(out, p * k, h * k, w * k, wrap)
        ref = img[..., :3] if k == 1 else np.asarray(Image.fromarray(np.ascontiguousarray(img[..., :3])).resize((w * k, h * k), Image.LANCZOS))
        out = self._finish(out, ref, np.ones(h * w * k * k, bool), refit=True)
        _png(out, TMP / f"{tag}_out.png")
        a = img[..., 3] if k == 1 else np.asarray(Image.fromarray(img[..., 3]).resize((w * k, h * k), Image.NEAREST))
        return np.dstack([out, a])

    def panorama(self, rgb):
        """The sky and far scenery all round the arena (scenepaint.Panorama) painted: as a sheet, wrapping left to right
        (all the way round), PANORAMA_HINT. RGB in and out."""
        a = np.full(rgb.shape[:2] + (1,), 255, np.uint8)
        return self.sheet(np.concatenate([rgb, a], -1), "panorama", wrap=(True, False), hint=PANORAMA_HINT)[..., :3]

    def __call__(self, orig, cur, mask):
        h, w = orig.shape[:2]
        self.n += 1
        tag = f"{self.name}_{self.n:02d}"
        if cur is None:
            self.prepare(orig)
            inp = self.comfy.upload(_png(orig, TMP / f"{tag}_in.png"))
            wf = C.build_workflow(self.s, inp, w, h, f"ff7studio/models/{tag}", self._refs())
        else:
            ctx = self.comfy.upload(_png(cur, TMP / f"{tag}_ctx.png"))
            src = self.comfy.upload(_png(orig, TMP / f"{tag}_orig.png"))
            m = (mask.astype(np.uint8) * 255)
            msk = self.comfy.upload(_png(np.dstack([m, m, m]), TMP / f"{tag}_mask.png"))
            wf = C.build_states_workflow(self.s, ctx, src, msk, w, h, w, h, f"ff7studio/models/{tag}", self._refs(),
                                         denoise=self.denoise if self.denoise is not None else self.s.get("denoise"))
        data = self.comfy.run(wf, self.on_progress)
        out = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
        if out.shape[:2] != (h, w):
            out = np.asarray(Image.fromarray(out).resize((w, h), Image.LANCZOS))
        # toward the original's colours, the same pull for every view (fitted on the first)
        out = self._finish(out, orig, orig.reshape(-1, 3).sum(1) > 0, refit=False)
        _png(out, TMP / f"{tag}_out.png")
        return out
