"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(url, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET") };
  if (opts.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(url, init);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error((data && data.message) || `${url}: ${r.status}`);
  return data;
}

// Per-viewer conveniences (which tab, which edit target); the page works without them.
function pref(key, fallback) {
  try { return localStorage.getItem(`ff7studio.${key}`) ?? fallback; } catch (e) { return fallback; }
}
function setPref(key, value) {
  try { localStorage.setItem(`ff7studio.${key}`, value); } catch (e) { /* storage blocked */ }
}

let toastTimer = null;
function toast(msg, ms = 4500) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

const LAYER_NAMES = { 0: "L0 floor & walls", 1: "L1 objects", 2: "L2 back", 3: "L3 front" };
const FAMILIES = {
  flux1: { short: "Flux.1", hint: "Flux.1: repaints the original while an edge lock keeps every outline in place. Works with style images. About 20 s per field, with lots of settings to tune." },
  krea2: { short: "Krea 2", hint: "Krea 2: fast and stylish, and works with style images. Keep the repaint low (about 0.2–0.3), or it starts changing the layout." },
  qwen21: { short: "Qwen 2.1", hint: "Qwen 2.1: redraws the whole scene in the new style while keeping its layout. The nicest results in testing; about 10 s per field with Turbo." },
  flux3k: { short: "FLUX 3", hint: "FLUX 3: redraws the scene while keeping the layout the most exactly. Cleaner, and closer to the original colours. Slow: about a minute per field with Base." },
  mageflow: { short: "Mage-Flow", hint: "Mage-Flow: a small, very fast model (a few seconds per field) for quick drafts and big batches. Keeps the original colours." },
  fibo2: { short: "FIBO 2", hint: "FIBO 2: a clean, realistic redraw that keeps the layout very well, but can lose the original's mood (Keep original colours brings it back). Non-commercial use only." },
  moss: { short: "MOSS", hint: "MOSS: a bold, painterly repaint, with a depth lock that keeps the scene in place. Doesn't use style images. Turbo is about twice as fast as SFT." },
  upscale: { short: "Upscale", hint: "Upscale: no AI restyling. The original art is enlarged with the upscaler under Output, for a faithful HD version." },
};
const usesRepaint = (f) => f === "flux1" || f === "krea2" || f === "moss";
const EDIT = new Set(["qwen21", "flux3k", "mageflow", "fibo2"]);  // edit models: no repaint strength or detail pass
// Fast and full versions of the edit models; mirrors EDIT_VARIANTS in ff7bg/comfy.py
const VARIANTS = {
  qwen21: { turbo: { label: "Turbo · 8 steps", unet: "qwen_image_2.1_turbo_int8_convrot.safetensors", steps: 8, cfg: 1 },
            full: { label: "Full · 25 steps", unet: "qwen_image_2.1_int8_convrot.safetensors", steps: 25, cfg: 1 } },
  flux3k: { turbo: { label: "Distilled · 16 steps", unet: "flux3-image-klein-int8-convrot.safetensors", steps: 16, cfg: 1 },
            full: { label: "Base · 50 steps", unet: "flux3-image-klein-base-int8-convrot.safetensors", steps: 50, cfg: 1 } },
  fibo2: { turbo: { label: "Edit Turbo · 4 steps", unet: "fibo2_edit_turbo_int8_convrot.safetensors", steps: 4, cfg: 1 },
           full: { label: "Dev · 30 steps", unet: "fibo2_dev_int8_convrot.safetensors", steps: 30, cfg: 5 } },
  moss: { turbo: { label: "Turbo · 8 steps", unet: "moss_image_sft_turbo_int8_convrot.safetensors", steps: 8, cfg: 1 },
          full: { label: "SFT · 25 steps", unet: "moss_image_sft_int8_convrot.safetensors", steps: 25, cfg: 2.5 } },
  mageflow: { turbo: { label: "Turbo · 4 steps", unet: "mage_flow_edit_turbo_int8_convrot.safetensors", steps: 4, cfg: 1 },
              full: { label: "Full · 30 steps (redesigns)", unet: "mage_flow_edit_int8_convrot.safetensors", steps: 30, cfg: 5 } },
};
const famShort = (f) => (FAMILIES[f] || {}).short || "";
const pct = (v) => `${Math.round(v * 100)}%`;

// Sliders. Each explains itself in the same three parts: what it does, what higher and lower change, and
// what to use (rec; recValue adds a "use it" link). by.<workflow> overrides any of those for one workflow.
const SL = {
  denoise: { label: "Repaint strength", min: 0.1, max: 0.95, step: 0.01,
    what: "How much the AI is allowed to change the picture.",
    up: "more restyling and new detail, but buildings and objects can change shape or move",
    down: "stays closer to the original picture",
    rec: "0.2–0.3", recValue: 0.2,
    by: { moss: { rec: "0.5 with the depth lock on; about 0.3 with it off", recValue: 0.5 },
          krea2: { rec: "0.2–0.3 (Krea 2 changes the layout quickly above that)" } } },
  structure: { label: "Edge lock", min: 0, max: 1.5, step: 0.05,
    what: "Holds the outlines of the original in place while Flux repaints, so walls, doors and walkable areas don't move.",
    up: "outlines stay exactly put, with less freedom to reshape things",
    down: "Flux may redraw shapes, and characters can end up walking through scenery",
    rec: "1.2", recValue: 1.2 },
  structure_end: { label: "Edge lock duration", min: 0.3, max: 1, step: 0.05, fmt: pct,
    what: "How long the edge lock stays on during the render. Letting go near the end lets the finest details be drawn freely, so they don't look traced.",
    up: "outlines held to the very end",
    down: "more freedom for the final details",
    rec: "75%", recValue: 0.75 },
  canny_low: { label: "Edge detail", min: 0.05, max: 0.5, step: 0.01,
    what: "Which outlines the edge lock holds on to.",
    up: "only the strongest outlines are held; small details are free to change",
    down: "fine lines and textures are held too, which can look traced",
    rec: "0.2", recValue: 0.2 },
  guidance: { label: "Prompt strength", min: 1.5, max: 5, step: 0.1,
    what: "How closely Flux follows your prompt.",
    up: "follows the prompt more literally; colours can turn harsh",
    down: "looser and more natural, closer to the original",
    rec: "5", recValue: 5 },
  depth_lock: { label: "Depth lock", min: 0, max: 1.5, step: 0.05,
    what: "Keeps the 3D shape of the scene (what's near, what's far) the same as the original, so buildings and objects stay put even when the picture is repainted strongly.",
    up: "holds the scene more strictly",
    down: "more freedom to change shapes; 0 turns it off",
    rec: "1", recValue: 1,
    by: { moss: { up: "holds the scene more strictly; well above 1 the picture starts to look scratchy" },
          krea2: { rec: "0 until the Krea2 Control nodes are installed, then about 0.6–1", recValue: undefined },
          flux1: { what: "A second lock on top of the edge lock: keeps the 3D shape of the scene (what's near, what's far) the same as the original. It matters when you repaint strongly.",
                   rec: "0 at the usual low repaint; about 0.6 if you raise Repaint strength above 0.35", recValue: undefined },
          qwen21: { what: "Keeps the 3D shape of the scene the same as the original. Qwen then stops shifting or swapping objects, but it also restyles less.",
                    rec: "0 normally; 0.5–1 for fields where Qwen moves or changes things", recValue: 0 } } },
  depth_lock_end: { label: "Depth lock duration", min: 0.3, max: 1, step: 0.05, fmt: pct,
    what: "How long the depth lock stays on during the render.",
    up: "the scene is held to the very end",
    down: "the final details are drawn freely",
    rec: "100%", recValue: 1 },
  keep_colors: { label: "Keep original colours", min: 0, max: 1, step: 0.05, fmt: pct,
    what: "Brings the colours back toward the original's, keeping the new detail. Useful when a model changed the mood, for example turning a night scene into day.",
    up: "colours closer to the original",
    down: "the model's own colours; 0 is off",
    rec: "0 if you like the new colours; 50–70% to bring back the original mood. Rebuild a version to try a value without rendering again" },
  text_repaint: { label: "Sign repaint", min: 0, max: 0.5, step: 0.01,
    what: "How much the protected signs and lettering are repainted. It fades smoothly into the rest of the scene.",
    up: "signs take on more of the new style, but letters start to warp",
    down: "signs stay closer to the original; 0 keeps them as they are",
    rec: "0.15", recValue: 0.15,
    // the edit models redraw everything else from scratch: no Repaint strength to compare with
    by: Object.fromEntries(["qwen21", "flux3k", "mageflow", "fibo2"].map((f) => [f, { label: "Sign change",
      what: "How much the model may change the protected signs and lettering. Everything else is redrawn from scratch, as always with this workflow (which is why it has no Repaint strength)." }])) },
  tries: { label: "Renders per field", min: 1, max: 4, step: 1, fmt: (v) => v > 1 ? `best of ${v}` : "1",
    what: "Final renders only: paints each field several times, each from a different random start, and keeps the one that matches the original layout best. The others are deleted.",
    up: "a better chance of a clean result, but it takes that many times as long",
    down: "1 is a single render",
    rec: "1 while trying things out; 2 for big batches if you have the time" },
  input_scale: { label: "Enlarge by", min: 0, max: 8, step: 1, fmt: (v) => v ? `${v}×` : "Auto",
    what: "How much the original art is enlarged before the AI paints over it. The AI paints at that size.",
    up: "more detail but much slower (on Kalm, 3× took about 2.5 times as long as 2×); above about 4×, models start repeating details",
    down: "faster, softer",
    rec: "Auto (follows Render size)", recValue: 0 },
  style_ref_strength: { label: "Style image strength", min: 0, max: 1.2, step: 0.05,
    what: "How much your selected style images shape the look.",
    up: "the look follows the style images more closely",
    down: "follows the prompt and the original more; 0 ignores them",
    rec: "0.5", recValue: 0.5,
    by: Object.fromEntries(["qwen21", "flux3k", "mageflow", "fibo2"].map((f) => [f, {
      what: "How much your selected style images shape the look. This model tends to copy things out of them, not just their style (Qwen once painted a different town into a field), so it's off by default.",
      rec: "0", recValue: 0 }])) },
  out_scale: { label: "Output scale", min: 2, max: 10, step: 1, fmt: (v) => `${v}×`,
    what: "How big the final game textures are, compared to the original art.",
    up: "sharper on big screens, but bigger files and slower",
    down: "smaller and faster",
    rec: "4× for a 1080p screen, 6× for 1440p, 8× for 4K" },
  detail_denoise: { label: "Detail strength", min: 0.1, max: 0.5, step: 0.01,
    what: "How much the detail pass repaints while it adds fine texture at full size.",
    up: "more new texture; above about 0.45 neighbouring tiles disagree and seams can show",
    down: "subtler",
    rec: "0.15–0.4" },
  cfg: { label: "Prompt strength", min: 1, max: 8, step: 0.5,
    what: "How closely the model follows your prompt. Anything above 1 makes each render take about twice as long.",
    up: "follows the prompt more strongly; too high looks overcooked",
    down: "1 is the fastest, and what Turbo versions are made for",
    rec: "the Turbo/Full switch above sets it: 1 for Turbo, more for Full", recValue: undefined,
    by: { moss: { rec: "2.5 for SFT, 1 for Turbo (the switch above sets it)" },
          qwen21: { rec: "1", recValue: 1 }, flux3k: { rec: "1", recValue: 1 } } },
  steps: { label: "Render steps", min: 4, max: 40, step: 1,
    what: "How many times the AI goes over the picture.",
    up: "a little more detail, takes longer",
    down: "faster; too few looks unfinished",
    rec: "the Turbo/Full switch above sets it",
    by: { flux1: { rec: "20", recValue: 20 }, krea2: { rec: "8", recValue: 8 },
          moss: { rec: "25 for SFT, 8 for Turbo (the switch above sets it)" } } },
  work_mp: { label: "Render size", min: 0.8, max: 4, step: 0.1, fmt: (v) => `${v} MP`,
    what: "How big a picture the AI paints (in megapixels) before it's enlarged to the final size.",
    up: "finer detail, slower; far above the recommended size, models start repeating details",
    down: "faster, softer",
    rec: "1.4", recValue: 1.4,
    by: { qwen21: { rec: "3", recValue: 3 }, flux3k: { rec: "4", recValue: 4 },
          mageflow: { rec: "2", recValue: 2 }, fibo2: { rec: "2", recValue: 2 } } },
  edit_margin: { label: "Border padding", min: 0, max: 0.15, step: 0.01, fmt: pct,
    what: "Edit models zoom in a little. This adds a blurred border around the scene, so the zoom eats into the border instead of the scene.",
    up: "safer, a little slower",
    down: "the scene's edges may get cut off (they're then patched from the original art)",
    rec: "8%", recValue: 0.08 },
  detail_steps: { label: "Detail steps", min: 2, max: 20, step: 1,
    what: "How many times the detail pass goes over each tile.",
    up: "slightly richer, slower", down: "faster",
    rec: "8", recValue: 8, by: { krea2: { rec: "4", recValue: 4 } } },
  detail_structure: { label: "Detail edge lock", min: 0, max: 1, step: 0.05,
    what: "Holds outlines in place during the detail pass too.",
    up: "outlines stay exactly put", down: "0 is off",
    rec: "0 (it made no visible difference in testing)", recValue: 0 },
  state_denoise: { label: "States repaint", min: 0.1, max: 1, step: 0.05,
    what: "How much doors, lights and animation frames are repainted to match the new style. Each one starts from the new scene with only what moves or changes painted in, so it only needs a light touch.",
    up: "they take on more of the new style, but frames can turn into made-up objects and stop matching each other",
    down: "frames match each other and the scene, but the moving bits keep more of the original art",
    rec: "0.5", recValue: 0.5,
    by: Object.fromEntries(["flux1", "krea2", "moss"].map((f) => [f, {
      up: "they take on more of the new style, but frames can turn into made-up objects and stop matching each other (at 0.7, blin1's flickering hologram became a different staircase in every frame)",
      rec: "0.3", recValue: 0.3 }])) },
  state_denoise_boost: { label: "Hidden parts extra repaint", min: 0, max: 0.3, step: 0.01,
    what: "Some animated parts never appear in the main picture, like a fan that's always spinning in front of an empty window. They start as original art pasted into the new scene and are repainted at your Repaint strength plus this much, so they blend in. Everything else uses States repaint.",
    up: "blends in better, but shapes can drift from the original",
    down: "keeps their shapes, but they may look pasted on",
    rec: "0.1", recValue: 0.1 },
  lora_strength: { label: "Extra LoRA strength", min: 0, max: 1.5, step: 0.05,
    what: "How strongly the extra LoRA (an add-on that pushes a particular style) is applied.",
    up: "a stronger style", down: "a weaker one",
    rec: "0.8", recValue: 0.8 },
};

// The help under a slider: what it does, higher vs lower, the recommended value
function sliderHelp(key, v) {
  const sd = { ...SL[key], ...((SL[key].by || {})[v.family] || {}) };
  const fmt = sd.fmt || String;
  let html = `<div>${sd.what}</div>`;
  if (sd.up || sd.down) {
    html += `<div class="help-range">${sd.up ? `<span><b>Higher:</b> ${sd.up}.</span>` : ""}` +
      `${sd.down ? `<span><b>Lower:</b> ${sd.down}.</span>` : ""}</div>`;
  }
  if (sd.rec) {
    const use = sd.recValue !== undefined && Math.abs(Number(v[key]) - sd.recValue) > 1e-9
      ? ` <button class="linkish use-rec" data-v="${sd.recValue}" title="Set ${fmt(sd.recValue)}">use it</button>` : "";
    html += `<div class="help-rec">Recommended: ${sd.rec}.${use}</div>`;
  }
  return html;
}

// Help for a checkbox or a list: what it does, then what to use
const helpText = (what, rec) => `<div>${what}</div>` + (rec ? `<div class="help-rec">Recommended: ${rec}.</div>` : "");

const LOOK_SLIDERS = {
  flux1: ["denoise", "structure", "structure_end", "depth_lock", "canny_low", "guidance", "style_ref_strength"],
  krea2: ["denoise", "depth_lock", "style_ref_strength"],
  qwen21: ["depth_lock", "style_ref_strength"],
  flux3k: ["style_ref_strength"],
  mageflow: ["style_ref_strength"],
  fibo2: ["style_ref_strength"],
  moss: ["denoise", "depth_lock", "depth_lock_end"],
  upscale: [],
};
const KEY_LABELS = {
  family: "workflow", prompt: "prompt", style_refs: "style images", seed: "seed", upscaler: "upscaler",
  describe_scene: "scene description", retry: "retry", protect_text: "sign protection",
  seedvr2_input: "SeedVR2 input", detail_pass: "detail pass", states_pass: "states pass", align: "auto-align",
  model_source: "model source", checkpoint: "checkpoint", unet: "diffusion model", clip1: "text encoder",
  clip2: "text encoder 2", clip_type: "encoder type", vae: "VAE", controlnet: "ControlNet", lora: "LoRA",
  canny_high: "edge sensitivity", unet_dtype: "weight type", detail_style: "detail style images",
  seedvr2_unet: "SeedVR2 model", seedvr2_vae: "SeedVR2 VAE", seedvr2_color: "SeedVR2 color", input_upscaler: "original art upscaler",
};
const keyLabel = (k) => (SL[k] && SL[k].label.toLowerCase()) || KEY_LABELS[k] || k;

const fmtDuration = (sec) => sec < 90 ? `${Math.round(sec)} s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;

function fmtWhen(ts, long = false) {
  if (!ts) return "";
  const dt = new Date(ts * 1000);
  const time = dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (!long && dt.toDateString() === new Date().toDateString()) return time;
  return `${dt.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

const S = {
  fields: [], byName: {}, current: null, detail: null,
  mode: "new", userMode: "new", zoom: 1, panX: 0, panY: 0, split: 0.5,
  layers: new Set([0, 1, 2, 3]), fx: true, params: new Set(),
  overlay: false, pixel: true,
  imgs: { orig: null, new: null, left: null, overlay: null },
  animate: true, strips: [],   // strips: [{group, rect, frames, wait, orig, new, left}]
  view: null, left: "orig",    // version shown as New / on the left of Split
  sel: new Set(), anchor: null,  // fields picked in the list for a batch
  target: pref("target", "look"), tab: pref("tab", "style"),
  looks: null, styles: [], models: null, rules: [], rulesDirty: false,
  queue: null, queueKey: "", running: null, lastFinished: null, ready: false,
};

// States the game shows on entry, as "param.state" keys
function defaultParams(d) {
  const on = new Set();
  for (const [p, mask] of Object.entries(d.defaults || {})) {
    for (const s of d.params[p] || []) if (mask & s) on.add(`${p}.${s}`);
  }
  return on;
}

/* ------------------------------------------------------------ versions */

function modeLabel(v) {
  if (v.mode === "imported") return /^earlier/.test(v.note || "") ? "earlier render" : "imported";
  return v.try ? "try" : v.mode;
}
// "Krea 2 final", "Qwen 2.1 try", "earlier render" (workflow not recorded)
const verKind = (v, html = false) => {
  const fam = famShort(v.family);
  return fam ? (html ? `<b>${fam}</b> ${esc(modeLabel(v))}` : `${fam} ${modeLabel(v)}`) : (html ? `<b>${esc(modeLabel(v))}</b>` : modeLabel(v));
};
const verName = (v) => `${verKind(v)} · ${fmtWhen(v.created, true)}${v.current ? " ★" : ""}`;
const verStamp = (v) => (v ? `${v.id}-${(v.result && v.result.built_at) || 0}` : "");

function viewedVersion() {
  const d = S.detail;
  if (!d || !d.versions.length) return null;
  return d.versions.find((v) => v.id === S.view) || d.versions.find((v) => v.current) || d.versions[0];
}

function leftVersion() {
  if (S.left === "orig" || !S.detail) return null;
  return S.detail.versions.find((v) => v.id === S.left) || null;
}

// Short upscaler names for the version cards
function shortUp(u, s = {}) {
  if (!u) return "plain";
  if (u === "seedvr2") return /7b/i.test(s.seedvr2_unet || "") ? "SeedVR2 7B" : "SeedVR2";
  if (u === "magick") return "Lanczos";
  if (u.startsWith("upscayl:")) return UPSCAYL_NAMES[u.slice(8)] || u.slice(8);
  return u.replace(/\.(safetensors|pth|pt)$/i, "").replace(/^\dx[-_]?/i, "");
}

// What sets a version apart, as [key, text, short text for the card] (older versions recorded fewer settings)
function verBits(v) {
  const s = v.settings || {}, f = v.family, out = [];
  if (f === "upscale") {
    if (s.upscaler !== undefined) {
      out.push(["up", s.upscaler === "seedvr2" && !s.seedvr2_input && s.input_upscaler
        ? `${shortUp(s.input_upscaler, s)} → ${shortUp("seedvr2", s)}` : shortUp(s.upscaler, s)]);
    }
    return out;
  }
  if (s.steps !== undefined) {
    const variant = Object.values(VARIANTS[f] || {}).find((x) => x.unet === s.unet);
    out.push(["steps", `${variant ? variant.label.split(" · ")[0] + " " : ""}${s.steps} steps`]);
  }
  if (usesRepaint(f) && s.denoise !== undefined) out.push(["denoise", `repaint ${s.denoise}`]);
  else if (s.cfg > 1) out.push(["cfg", `prompt strength ${s.cfg}`]);
  if (s.depth_lock > 0) out.push(["depth", `depth lock ${s.depth_lock}`, `depth ${s.depth_lock}`]);
  if (s.input_upscaler !== undefined) {
    const t = `${s.input_scale ? `${s.input_scale}×` : "auto"} ${shortUp(s.input_upscaler, s)}`;
    out.push(["orig", `original art ${t}`, `orig ${t}`]);
  }
  if (s.upscaler !== undefined) out.push(["up", `output upscaler ${shortUp(s.upscaler, s)}`, `out ${shortUp(s.upscaler, s)}`]);
  const kc = (v.result && v.result.keep_colors) ?? s.keep_colors;
  if (kc > 0) out.push(["colours", `original colours ${Math.round(kc * 100)}%`, `colours ${Math.round(kc * 100)}%`]);
  if (s.detail_pass) out.push(["detail", `detail pass ${s.detail_denoise}`, `detail ${s.detail_denoise}`]);
  if (s.describe_scene) out.push(["scene", "scene description", "scene text"]);
  if (s.protect_text && !(f === "qwen21" && !(s.depth_lock > 0))) out.push(["signs", `signs protected at ${s.text_repaint}`, `signs ${s.text_repaint}`]);
  return out;
}

// Said on a card that lacks a setting the viewed version has
const VER_OFF = { depth: "no depth lock", colours: "colours off", detail: "no detail pass", scene: "no scene text",
                  signs: "signs not protected" };

// The card's summary; what differs from the version being viewed stands out
function verBitsHtml(v, viewed) {
  const mine = verBits(v);
  const theirs = viewed && viewed.id !== v.id && viewed.family === v.family ? new Map(verBits(viewed).map((b) => [b[0], b[1]])) : null;
  const diff = (t) => `<span class="d" title="Differs from the version you're viewing">${esc(t)}</span>`;
  const html = mine.map(([k, t, short]) => theirs && theirs.has(k) && theirs.get(k) !== t ? diff(short || t) : esc(short || t));
  if (theirs) {
    const have = new Set(mine.map((b) => b[0]));
    for (const k of theirs.keys()) if (VER_OFF[k] && !have.has(k)) html.push(diff(VER_OFF[k]));
  }
  return html.join(" · ");
}

function verTip(v) {
  const s = v.settings || {}, lines = [verName(v)];
  if (v.look) lines.push(`look: ${v.look}`);
  const bits = verBits(v).map(([, t]) => t);
  if (s.seed !== undefined) bits.push(`seed ${s.seed}`);
  if (s.style_refs) bits.push(s.style_refs.length ? `${s.style_refs.length} style image${s.style_refs.length === 1 ? "" : "s"}` : "no style images");
  if (s.tries > 1) bits.push(`best of ${s.tries}`);
  if (bits.length) lines.push(...bits);
  if (!v.built) lines.push("not cut into game textures yet");
  lines.push("Click to view · Shift+click to compare on the left of Split");
  return lines.join("\n");
}

function pickVersion(id, asLeft = false) {
  if (asLeft) {
    S.left = S.left === id ? "orig" : id;
    S.mode = "split";
  } else {
    S.view = id;
    if (S.mode === "orig") S.mode = "new";
  }
  S.userMode = S.mode;
  renderVersions();
  loadImages(false);
}

function renderSplitPick() {
  const d = S.detail;
  const show = S.mode === "split" && d && d.versions.length > 0;
  $("#splitPick").hidden = !show;
  $("#viewHint").hidden = !!show;
  if (!show) return;
  const opt = (v) => `<option value="${v.id}">${esc(verName(v))}</option>`;
  $("#leftSel").innerHTML = `<option value="orig">Original</option>` + d.versions.map(opt).join("");
  $("#leftSel").value = leftVersion() ? S.left : "orig";
  $("#rightSel").innerHTML = d.versions.map(opt).join("");
  $("#rightSel").value = viewedVersion().id;
}

function renderVersions() {
  const d = S.detail, box = $("#verStrip");
  renderSplitPick();
  if (!d) { box.innerHTML = ""; renderVersionHead(); return; }
  const nv = viewedVersion(), lv = leftVersion();
  const html = [];
  const q = S.queue || { queued: [] };
  const run = q.current && q.current.name === d.name && q.current.kind !== "build" ? q.current : null;
  if (run) {
    html.push(`<div class="vcard running" title="Rendering now"><div class="vthumb"></div>` +
      `<div class="vl"><b>${famShort(run.family)}</b> ${run.try ? "try" : run.kind}</div>` +
      `<div class="vt" id="runStage">${esc(run.stage)}</div><div class="vbar"><i id="runBar" style="width:${Math.round(run.progress * 100)}%"></i></div></div>`);
  }
  for (const j of q.queued.filter((j) => j.name === d.name && j.kind !== "build")) {
    html.push(`<div class="vcard running" title="Queued"><div class="vthumb" style="animation:none"></div>` +
      `<div class="vl"><b>${famShort(j.family)}</b> ${j.try ? "try" : j.kind}</div><div class="vt">queued · ~${fmtDuration(j.est || 0)}</div></div>`);
  }
  for (const v of d.versions) {
    const cls = ["vcard", nv && v.id === nv.id ? "viewing" : "", lv && v.id === lv.id ? "left" : "", v.built ? "" : "raw"].join(" ");
    html.push(`<div class="${cls}" data-id="${v.id}" title="${esc(verTip(v))}">` +
      `<img loading="lazy" alt="" src="/api/vthumb/${d.name}/${v.id}.jpg">` +
      `<div class="vl">${verKind(v, true)}</div>` +
      `<div class="vs">${verBitsHtml(v, nv)}</div>` +
      `<div class="vt">${fmtWhen(v.created)}${v.result ? ` · ${v.result.scale}×` : ""}</div>` +
      (v.current ? `<span class="vbadge star" title="The version the game uses">★</span>` : "") +
      (lv && v.id === lv.id ? `<span class="vbadge lft">L</span>` : "") +
      (v.built ? "" : `<span class="vbadge rawb">raw</span>`) + `</div>`);
  }
  html.push(`<div class="vcard add"><label title="Add an image made elsewhere (from this field's input image) as a version">+ Import image` +
    `<input type="file" id="importFile" accept="image/*" hidden></label>` +
    `<a href="/api/input/${d.name}.png" title="The image the generators get, to make a version elsewhere">input image ↓</a></div>`);
  box.innerHTML = html.join("");
  for (const el of box.querySelectorAll(".vcard[data-id]")) {
    el.onclick = (e) => pickVersion(el.dataset.id, e.shiftKey || e.altKey || e.ctrlKey);
  }
  $("#importFile").onchange = importImage;
  const viewing = box.querySelector(".vcard.viewing");
  if (viewing) box.scrollLeft = Math.max(0, Math.min(box.scrollLeft, viewing.offsetLeft - 10, viewing.offsetLeft + viewing.offsetWidth - box.clientWidth + 10)) || box.scrollLeft;
  renderVersionHead();
}

function renderVersionHead() {
  const d = S.detail, v = viewedVersion();
  const btns = ["#btnUse", "#btnRebuild", "#btnRedoStates", "#btnReview", "#btnDeleteVer"];
  if (!d || !v) {
    $("#verTitle").innerHTML = d ? "No versions yet: <b>Preview</b> or <b>Generate final</b> makes one." : "";
    $("#verInfo").innerHTML = "";
    btns.forEach((b) => { $(b).hidden = true; });
    $("#btnUseWorkflow").hidden = true;
    return;
  }
  btns.forEach((b) => { $(b).hidden = false; });
  $("#verTitle").innerHTML = `${S.mode === "orig" ? "Selected" : "Viewing"}: <b>${fmtWhen(v.created, true)}</b> · ${esc(verKind(v))}` +
    (v.look ? ` · look "${esc(v.look)}"` : "") + (v.current ? ` · <span class="ok">★ in game</span>` : "");
  const r = v.result, s = v.settings || {}, parts = [];
  if (!v.built) parts.push(`<span class="warn">raw render, not cut into game textures yet (Use this version or Rebuild)</span>`);
  if (r) {
    parts.push(`${r.scale}× (${d.w * r.scale}×${d.h * r.scale})`);
    if (r.edges_kept !== undefined) parts.push(`<span class="${r.edges_kept < 0.8 ? "warn" : ""}">original outlines kept ${Math.round(r.edges_kept * 100)}%</span>`);
    if (r.aligned) parts.push(`auto-aligned (drift ${r.drift_before}px → ${r.drift_after}px)`);
    if (r.missing > 0) parts.push(`${(r.missing * 100).toFixed(1)}% filled from original`);
    if (d.state_panels) {
      parts.push(r.state_panels ? `${r.state_panels} state panels ${v.family === "upscale" ? "upscaled" : "restyled"}`
        : `<span class="warn">states not ${v.family === "upscale" ? "upscaled" : "restyled"} (original art, color-matched)</span>`);
    }
    if (r.flag) parts.push(`<span class="warn">check: ${esc(r.flag)}</span>`);
  }
  parts.push(...verBits(v).map(([, t]) => esc(t)));
  if (s.seed !== undefined) parts.push(`seed ${s.seed}`);
  if (s.style_refs) parts.push(s.style_refs.length ? `${s.style_refs.length} style image${s.style_refs.length === 1 ? "" : "s"}` : "no style images");
  if (v.mode === "imported" && v.note && !/^earlier/.test(v.note)) parts.push(esc(v.note));
  $("#verInfo").innerHTML = parts.join(" · ");
  $("#btnUse").disabled = v.current;
  $("#btnUse").textContent = v.current ? "★ In game" : "Use this version";
  $("#btnReview").disabled = !v.review;
  $("#btnRedoStates").hidden = !d.state_panels;
  $("#btnRedoStates").disabled = !v.built;
  $("#btnDeleteVer").disabled = v.current;
  $("#btnDeleteVer").title = v.current ? "The version the game uses can't be deleted; use another one first" : "Delete this version";
  const uw = $("#btnUseWorkflow");
  uw.hidden = !v.family || v.family === values().family;
  uw.textContent = `Use ${famShort(v.family)} for ${S.target === "look" ? `look "${d.look.name}"` : "this field"}`;
  uw.title = "Switch the workflow the next renders use to this version's";
}

async function importImage(e) {
  const file = e.target.files[0];
  if (!file || !S.current) return;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`/api/versions/${S.current}/import`, { method: "POST", body: fd });
  if (!r.ok) { toast("Import failed"); return; }
  const data = await r.json();
  S.view = data.id;
  if (S.mode === "orig") S.mode = "new";
  applyDetail(data.field, true);
  toast("Imported. Cutting it into game textures in the queue; Use this version puts it in game.");
}

/* ------------------------------------------------------------ field list */

async function loadFields() {
  const fields = await api("/api/fields");
  S.fields = fields;
  S.byName = Object.fromEntries(fields.map((f) => [f.name, f]));
  renderList();
}

function statusClass(f) {
  if (S.running === f.name) return "running";
  if (f.error) return "error";
  if ((f.status === "built" || f.status === "preview") && f.flag) return "flag";
  if (f.status === "built") return "built";
  if (f.status === "preview") return "preview";
  return f.status === "generated" ? "generated" : "";
}

function lookName(id) {
  const l = S.looks && S.looks.looks.find((x) => x.id === id);
  return l ? l.name : id;
}

function renderList() {
  const ul = $("#fieldList");
  const q = $("#search").value.trim().toLowerCase();
  const filter = $("#filter").value;
  const lookFilter = $("#lookFilter").value;
  const existing = new Map([...ul.children].map((li) => [li.dataset.name, li]));
  const defLook = S.looks ? S.looks.default : "default";
  let shown = 0;
  for (const f of S.fields) {
    let li = existing.get(f.name);
    if (!li) {
      li = document.createElement("li");
      li.dataset.name = f.name;
      li.innerHTML = `<input type="checkbox" class="pick" title="Select for a batch (Ctrl+click toggles, Shift+click selects a range)">` +
        `<img loading="lazy" alt=""><div><div class="name"></div><div class="sub"></div></div><span class="sdot"></span>`;
      li.onclick = (e) => onListClick(e, f.name);
      li.querySelector(".pick").onclick = (e) => {
        e.stopPropagation();
        if (e.shiftKey && S.anchor) pickRange(S.anchor, f.name);
        else togglePick(f.name, e.target.checked);
      };
      ul.appendChild(li);
    }
    const thumbSrc = f.current ? `/api/thumb/${f.name}.jpg?v=new&t=${f.current}` : `/api/thumb/${f.name}.jpg`;
    const img = li.querySelector("img");
    if (img.dataset.src !== thumbSrc) { img.src = thumbSrc; img.dataset.src = thumbSrc; }
    li.querySelector(".name").textContent = f.name;
    const tags = (f.look !== defLook ? `<span class="tag" title="Look">${esc(lookName(f.look))}</span> ` : "") +
      (f.custom ? `<span class="tag custom" title="This field changes some settings for itself">field changes</span>` : "");
    const sub = `${f.w}×${f.h}` + (f.copies.length ? ` · +${f.copies.length} copies` : "") + (tags ? `<br>${tags}` : "");
    const subEl = li.querySelector(".sub");
    if (subEl.dataset.html !== sub) { subEl.innerHTML = sub; subEl.dataset.html = sub; }
    li.querySelector(".sdot").className = `sdot ${statusClass(f)}`;
    li.classList.toggle("active", f.name === S.current);
    const picked = S.sel.has(f.name);
    li.classList.toggle("picked", picked);
    const box = li.querySelector(".pick");
    if (box.checked !== picked) box.checked = picked;
    const text = (f.name + " " + f.copies.join(" ")).toLowerCase();
    const pass =
      (!q || text.includes(q)) &&
      (!lookFilter || f.look === lookFilter) &&
      (filter === "all" ||
        (filter === "none" && f.status === "none") ||
        (filter === "built" && f.status === "built") ||
        (filter === "preview" && f.status === "preview") ||
        (filter === "flagged" && (f.flag || f.error)) ||
        (filter === "override" && f.custom));
    li.style.display = pass ? "" : "none";
    if (pass) shown++;
  }
  const done = S.fields.filter((f) => f.status === "built").length;
  const previews = S.fields.filter((f) => f.status === "preview").length;
  $("#fieldCount").textContent = `${shown} shown · ${done}/${S.fields.length} final` + (previews ? ` · ${previews} previews` : "");
}

/* ------------------------------------------------------------ multi-select */

function onListClick(e, name) {
  if (e.ctrlKey || e.metaKey) togglePick(name, !S.sel.has(name));
  else if (e.shiftKey) pickRange(S.anchor || S.current, name);
  else selectField(name);
}

function togglePick(name, on) {
  on ? S.sel.add(name) : S.sel.delete(name);
  S.anchor = name;
  selChanged();
}

// Every visible field between a and b (in list order), added to the selection
function pickRange(a, b) {
  const visible = [...$("#fieldList").children].filter((li) => li.style.display !== "none").map((li) => li.dataset.name);
  let i = visible.indexOf(a), j = visible.indexOf(b);
  if (i < 0) i = j;
  if (j < 0) return;
  for (const n of visible.slice(Math.min(i, j), Math.max(i, j) + 1)) S.sel.add(n);
  S.anchor = b;
  selChanged();
}

let selEstTimer = null;
function selChanged() {
  for (const n of S.sel) if (!S.byName[n]) S.sel.delete(n);
  renderList();
  const n = S.sel.size;
  $("#selBar").hidden = !n;
  if (!n) return;
  $("#selCount").textContent = `${n} field${n === 1 ? "" : "s"} selected`;
  $("#selEst").textContent = "";
  clearTimeout(selEstTimer);
  selEstTimer = setTimeout(async () => {
    const names = [...S.sel];
    try {
      const e = await api("/api/estimate", { body: { names, mode: "final" } });
      if (names.length === S.sel.size) $("#selEst").textContent = `· final about ${fmtDuration(e.seconds)}`;
    } catch (err) { /* estimate is optional */ }
  }, 250);
}

function clearPicks() {
  S.sel.clear();
  S.anchor = null;
  selChanged();
}

async function queuePicked(mode) {
  const names = [...S.sel];
  if (!names.length) return;
  const e = await api("/api/estimate", { body: { names, mode } });
  const label = mode === "final" ? "final renders" : "previews";
  if (names.length > 3 && !confirm(`Queue ${label} of ${names.length} fields, each with its own look?\n\n` +
    `Estimated time: about ${fmtDuration(e.seconds)}. You can keep browsing while they run; Cancel queue stops them.`)) return;
  const r = await api("/api/generate", { body: { names, mode } });
  const skipped = names.length - r.added;
  toast(`${r.added} ${label} queued` + (skipped ? ` (${skipped} already queued)` : ""));
  clearPicks();
}

function renderLookFilter() {
  const sel = $("#lookFilter");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All looks</option>` +
    S.looks.looks.map((l) => `<option value="${esc(l.id)}">Look: ${esc(l.name)} (${l.fields})</option>`).join("");
  sel.value = S.looks.looks.some((l) => l.id === cur) ? cur : "";
}

/* ------------------------------------------------------------ field selection */

async function selectField(name, keepView = false) {
  S.current = name;
  renderList();
  let d;
  try { d = await api(`/api/field/${name}`); } catch (e) { return; }  // server restarting
  if (!d || S.current !== name) return;
  const first = !keepView || !S.detail || S.detail.name !== name;
  if (first) {
    S.view = null;
    S.left = "orig";
    S.layers = new Set(d.layers);
    S.params = defaultParams(d);  // open on what the game shows when you enter the field
    S.fx = true;
    S.mode = d.versions.length ? S.userMode : "orig";
  }
  S.detail = d;
  renderLayers();
  renderStyle();
  renderStatus();
  renderVersions();
  loadRequirements();
  await loadImages(first);
}

// What each workflow needs that ComfyUI doesn't have, for this field's settings (server: ff7bg/requirements.py)
let reqTimer = null;
function loadRequirements() {
  clearTimeout(reqTimer);
  reqTimer = setTimeout(async () => {
    const name = S.current;
    if (!name) return;
    try {
      const r = await api(`/api/requirements/${name}`);
      if (S.current !== name) return;
      S.req = { name, ...r };
      if (S.detail) renderStyle();
    } catch (e) { /* server restarting */ }
  }, 250);
}

function missingFor(fam) {
  const r = S.req && S.detail && S.req.name === S.detail.name && S.req.families && S.req.families[fam];
  return r && (r.files.length || r.nodes.length || r.tools.length) ? r : null;
}

const fmtGB = (gb) => gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(gb * 1000))} MB`;

function renderRequirements(fam) {
  const box = $("#reqBox"), r = missingFor(fam);
  box.hidden = !r;
  if (!r) { box.innerHTML = ""; return; }
  const link = (url, text) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
  const items = r.files.map((f) =>
    `<li>${f.url ? link(f.url, f.file) : `<b>${esc(f.file)}</b>`}${f.gb ? ` · ${fmtGB(f.gb)}` : ""}${f.what ? ` · for ${esc(f.what)}` : ""}` +
    `<br><span class="hint">Put it in ComfyUI's <code>models\\${esc(f.folder)}</code> folder` +
    `${f.note ? `; ${esc(f.note)}` : ""}${f.url ? "" : ". There's no download link for it: it's a file picked in Advanced"}.</span></li>`);
  for (const n of r.nodes) {
    items.push(n.pack
      ? `<li>The ${link(n.url, n.pack)} node pack. In ComfyUI, open the Manager, search for it and install it, then restart ComfyUI.</li>`
      : `<li>A newer ComfyUI (yours lacks the <code>${esc(n.node)}</code> node): update ComfyUI Desktop.</li>`);
  }
  for (const t of r.tools) items.push(`<li>${link(t.url, t.name)}: ${esc(t.note)}.</li>`);
  const gb = r.files.reduce((a, f) => a + (f.gb || 0), 0);
  box.innerHTML = `<div class="req-head">⚠ ${esc(famShort(fam))} can't run with these settings yet. It needs:</div><ul>${items.join("")}</ul>` +
    `<div class="hint">${gb ? `About ${fmtGB(gb)} to download. ComfyUI Desktop keeps models in <code>Documents\\ComfyUI\\models</code>. ` : ""}` +
    `Once everything is in place: <button class="small ghost">Check again</button></div>`;
  box.querySelector("button").onclick = loadModels;
}

// Take a fresh field detail from the server (after a settings change, version action, ...).
function applyDetail(nd, reload = false) {
  const prev = S.detail;
  if (!nd || nd.name !== S.current) return;
  S.detail = nd;
  const f = S.byName[nd.name];
  if (f) {
    for (const k of ["status", "flag", "error", "custom", "look", "look_how", "current", "family"]) f[k] = nd[k];
  }
  renderList();
  renderStyle();
  renderStatus();
  renderVersions();
  loadRequirements();
  const key = (d) => d.versions.map(verStamp).join(",") + "|" + d.current;
  if (reload || !prev || key(prev) !== key(nd)) loadImages(false);
}

// Groups currently played as animations (drawn from strips instead of the static render)
function playingGroups() {
  const d = S.detail;
  return S.animate && d ? Object.keys(d.animations || {}).filter((p) => d.params[p]) : [];
}

function renderQuery(excludeGroups = []) {
  const params = [...S.params].filter((k) => !excludeGroups.includes(k.split(".")[0]));
  return new URLSearchParams({ layers: [...S.layers].join(","), fx: S.fx ? "1" : "0", params: params.join(",") }).toString();
}

function loadImg(src) {
  return new Promise((res) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => res(null);
    im.src = src;
  });
}

async function loadImages(fit) {
  const d = S.detail;
  if (!d) return;
  const playing = playingGroups();
  const q = renderQuery(playing);
  const name = d.name;
  const nv = viewedVersion(), lv = leftVersion();
  const verSrc = (v) => `/api/render/${name}?v=new&ver=${v.id}&${q}&t=${verStamp(v)}`;
  const strip = (p, v) => loadImg(`/api/strip/${name}?group=${p}&${q}` + (v ? `&v=new&ver=${v.id}&t=${verStamp(v)}` : "&v=orig"));
  const [orig, neu, left, overlay, ...strips] = await Promise.all([
    loadImg(`/api/render/${name}?v=orig&${q}`),
    nv ? loadImg(verSrc(nv)) : null,
    lv ? loadImg(verSrc(lv)) : null,
    S.overlay ? (S.imgs.overlayFor === name && S.imgs.overlay ? S.imgs.overlay : loadImg(`/api/overlay/${name}.png`)) : null,
    ...playing.flatMap((p) => [strip(p, null), nv && nv.built ? strip(p, nv) : null, lv && lv.built ? strip(p, lv) : null]),
  ]);
  if (S.current !== name) return;
  S.imgs = { orig, new: neu, left, overlay, overlayFor: name };
  // Each frame as its own bitmap: drawing a slice of the strip image with smoothing pulls in a
  // sliver of the neighbouring frame along the edges, which showed as lines through the scene.
  const split = (img, n) => img ? Promise.all([...Array(n).keys()].map((k) => {
    const fw = img.naturalWidth / n;
    return createImageBitmap(img, Math.round(k * fw), 0, Math.round(fw), img.naturalHeight);
  })) : null;
  const built = [];
  for (const [i, p] of playing.entries()) {
    const a = d.animations[p];
    const frames = a.order.length > 1 ? a.order.length : 2;
    if (!strips[3 * i]) continue;
    const [o, n, l] = await Promise.all([split(strips[3 * i], frames), split(strips[3 * i + 1], frames), split(strips[3 * i + 2], frames)]);
    built.push({ group: p, rect: d.group_rects[p], tiles: (d.group_tiles || {})[p], frames, wait: a.wait, orig: o, new: n, left: l });
  }
  if (S.current !== name) return;
  S.strips = built;
  if (fit) fitView();
  draw();
  if (S.strips.length) startAnimation();
}

// Timer-driven (not requestAnimationFrame, which some embedded/unfocused windows throttle to a
// couple of frames a second); redraws only when some animation actually changes frame.
let animating = false;
function startAnimation() {
  if (animating) return;
  animating = true;
  let last = "";
  const tick = () => {
    if (!S.animate || !S.strips.length) { animating = false; return; }
    const now = performance.now();
    const key = S.strips.map((st) => Math.floor(now / (st.wait * 1000 / 30)) % st.frames).join(",");
    if (key !== last) { last = key; draw(); }
    setTimeout(tick, 15);
  };
  tick();
}

/* ------------------------------------------------------------ layers tab */

function chip(label, on, onclick, title = "") {
  const c = document.createElement("span");
  c.className = "chip" + (on ? " on" : "");
  c.textContent = label;
  c.title = title;
  c.onclick = onclick;
  return c;
}

let reloadTimer = null;
function reloadSoon() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => loadImages(false), 120);
}

function renderLayers() {
  const d = S.detail;
  const box = $("#layerToggles");
  box.innerHTML = "";
  for (const l of d.layers) {
    box.appendChild(chip(LAYER_NAMES[l], S.layers.has(l), () => {
      S.layers.has(l) ? S.layers.delete(l) : S.layers.add(l);
      renderLayers(); reloadSoon();
    }));
  }
  if (d.has_fx) {
    box.appendChild(chip("Glow effects", S.fx, () => { S.fx = !S.fx; renderLayers(); reloadSoon(); },
      "Static translucent effects (light beams, glows)"));
  }
  const pg = $("#paramGroups");
  pg.innerHTML = "";
  const groups = Object.entries(d.params);
  if (!groups.length) return;
  const intro = document.createElement("div");
  intro.className = "lbl";
  intro.innerHTML = `Toggled by the game: doors, lights, animation frames. <span class="dflt-key">●</span> = on when you enter`;
  pg.appendChild(intro);
  const dmask = (p) => (d.defaults || {})[p] || 0;
  for (const [p, states] of groups) {
    const g = document.createElement("div");
    g.className = "param-group";
    const head = document.createElement("div");
    head.className = "pg-head";
    const anim = (d.animations || {})[p];
    const kind = anim
      ? `animated · ${anim.order.length > 1 ? anim.order.length + " frames" : "blinks"} @ ${(30 / anim.wait).toFixed(1)} fps`
      : (dmask(p) ? "on at entry, switched by events" : "switched by events");
    head.innerHTML = `<span>Group ${p} <span class="kind">${kind}</span></span>` +
      `<span><a data-a="default">default</a> · <a data-a="all">all</a> · <a data-a="none">none</a></span>`;
    const set = (fn) => () => { fn(); renderLayers(); reloadSoon(); };
    head.querySelector('[data-a="default"]').onclick = set(() => states.forEach((s) =>
      (dmask(p) & s) ? S.params.add(`${p}.${s}`) : S.params.delete(`${p}.${s}`)));
    head.querySelector('[data-a="all"]').onclick = set(() => states.forEach((s) => S.params.add(`${p}.${s}`)));
    head.querySelector('[data-a="none"]').onclick = set(() => states.forEach((s) => S.params.delete(`${p}.${s}`)));
    g.appendChild(head);
    const row = document.createElement("div");
    row.className = "toggles";
    if (anim && S.animate) {
      const note = document.createElement("span");
      note.className = "chip playing";
      note.textContent = "▶ playing (turn off Animate to pick frames)";
      row.appendChild(note);
    } else {
      for (const s of states) {
        const key = `${p}.${s}`;
        row.appendChild(chip(`state ${s}` + ((dmask(p) & s) ? " ●" : ""), S.params.has(key), set(() =>
          S.params.has(key) ? S.params.delete(key) : S.params.add(key))));
      }
    }
    g.appendChild(row);
    pg.appendChild(g);
  }
}

/* ------------------------------------------------------------ style tab */

// What the controls show: the look's own settings, or this field's (look + its changes).
const values = () => (S.target === "look" ? S.detail.look_values : S.detail.effective);

function mark(key) {
  const d = S.detail;
  const own = new Set(S.target === "look" ? d.look_keys : d.override_keys);
  const keys = key === "canny_low" ? ["canny_low", "canny_high"] : [key];
  if (keys.some((k) => own.has(k))) return "changed";
  if (S.target === "look" && keys.some((k) => d.override_keys.includes(k))) return "overridden";
  return "";
}

const OVERRIDDEN_TIP = "This field changes this setting for itself; switch to This field only to see or edit its value";

function slider(key, v, opts = {}) {
  const sd = SL[key];
  const m = mark(key);
  const w = document.createElement("div");
  w.className = `slider ${m} ${opts.dim ? "dim" : ""}`;
  if (m === "overridden") w.title = OVERRIDDEN_TIP;
  const fmt = sd.fmt || ((x) => String(x));
  const label = ((sd.by || {})[v.family] || {}).label || sd.label;
  w.innerHTML = `<div class="top"><span>${label}</span><b>${fmt(Number(v[key]))}</b></div>` +
    `<input type="range" min="${sd.min}" max="${sd.max}" step="${sd.step}" value="${v[key]}">` +
    `<div class="help">${opts.help ? `<div class="help-note">${opts.help}</div>` : ""}${sliderHelp(key, v)}</div>`;
  const input = w.querySelector("input");
  const commit = (val) => {
    const patch = { [key]: val };
    if (key === "canny_low") patch.canny_high = Math.min(0.95, +(val * 3).toFixed(2));
    saveSetting(patch);
  };
  input.oninput = () => { w.querySelector("b").textContent = fmt(Number(input.value)); };
  input.onchange = () => commit(Number(input.value));
  const rec = w.querySelector(".use-rec");
  if (rec) rec.onclick = () => commit(Number(rec.dataset.v));
  return w;
}

function check(key, label, v, help = "") {
  const m = mark(key);
  const el = document.createElement("label");
  el.className = `check ctl ${m}`;
  if (m === "overridden") el.title = OVERRIDDEN_TIP;
  el.innerHTML = `<input type="checkbox" ${v[key] ? "checked" : ""}><span>${label}${help ? `<div class="ctl-help">${help}</div>` : ""}</span>`;
  el.querySelector("input").onchange = (e) => saveSetting({ [key]: e.target.checked });
  return el;
}

function selectRow(key, label, options, v, { hint = "", none = null, labels = {} } = {}) {
  const cur = v[key] ?? "";
  let opts = options.slice();
  if (cur && !opts.includes(cur)) opts = [cur, ...opts];  // keep a saved choice visible even if the file is gone
  const html = (none !== null ? `<option value="">${none}</option>` : "") +
    opts.map((o) => `<option value="${esc(o)}" ${o === cur ? "selected" : ""}>${esc(labels[o] || o)}${options.includes(o) ? "" : "  (missing)"}</option>`).join("");
  const m = mark(key);
  const el = document.createElement("div");
  el.className = `ctl ${m}`;
  if (m === "overridden") el.title = OVERRIDDEN_TIP;
  el.innerHTML = `<label class="lbl">${label}</label><select>${html}</select>${hint ? `<div class="ctl-help">${hint}</div>` : ""}`;
  el.querySelector("select").onchange = (e) => saveSetting({ [key]: e.target.value });
  return el;
}

function renderLookBar() {
  const d = S.detail, L = S.looks;
  const sel = $("#lookSel");
  const how = d.auto.how === "default" ? "default look" : `rule ${d.auto.how.slice(8)}`;
  sel.innerHTML = `<option value="">Automatic: ${esc(lookName(d.auto.id))} (${esc(how)})</option>` +
    (L ? L.looks : []).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}${l.id === L.default ? " (default)" : ""}</option>`).join("") +
    `<option value="__manage">Manage looks…</option>`;
  sel.value = d.look.explicit ? d.look.id : "";
  const n = d.override_keys.length;
  $("#targetLook").textContent = `Edit look "${d.look.name}"`;
  $("#targetField").textContent = n ? `This field only (${n})` : "This field only";
  for (const b of $("#targetSeg").children) b.classList.toggle("on", b.dataset.target === S.target);
  const note = $("#scopeNote");
  const count = L ? (L.looks.find((l) => l.id === d.look.id) || {}).fields : null;
  if (S.target === "look") {
    note.className = "scope-note";
    note.innerHTML = `Changes apply to ${count != null ? `all <b>${count}</b> fields` : "every field"} using "${esc(d.look.name)}". • = changed from the built-in defaults.` +
      (n ? `<br><span style="color:var(--field)">◆ ${esc(d.name)} changes ${n} setting${n > 1 ? "s" : ""} for itself: ${d.override_keys.map(keyLabel).join(", ")}.</span>` : "");
  } else {
    note.className = "scope-note field";
    note.textContent = n
      ? `Changes apply to ${d.name} only. • = ${n} setting${n > 1 ? "s" : ""} different from its look.`
      : `Changes apply to ${d.name} only, on top of look "${d.look.name}".`;
  }
  $("#btnResetField").disabled = !n;
}

function renderStyle() {
  const d = S.detail;
  if (!d) return;
  renderLookBar();
  const v = values();
  const fam = v.family;

  const seg = $("#familySeg");
  seg.innerHTML = Object.entries(FAMILIES).map(([k, f]) => `<button data-fam="${k}" class="${k === fam ? "on" : ""}">${f.short}</button>`).join("");
  for (const b of seg.children) b.onclick = () => { if (b.dataset.fam !== fam) saveSetting({ family: b.dataset.fam }); };
  if (S.target === "look" && d.override_keys.includes("family") && d.effective.family !== fam) {
    const fb = seg.querySelector(`[data-fam="${d.effective.family}"]`);
    if (fb) { fb.classList.add("overridden"); fb.title = `${d.name} uses ${famShort(d.effective.family)} (This field only)`; }
  }
  const nodes = (S.models && S.models.nodes) || {};
  for (const b of seg.children) {
    if (nodes[b.dataset.fam] === false || missingFor(b.dataset.fam)) {
      b.textContent += " ⚠";
      b.title = "Missing files or nodes with these settings: pick it to see what's needed and where to get it";
    }
  }
  $("#familyHint").textContent = FAMILIES[fam].hint;
  renderRequirements(fam);
  renderVariants(v);
  const up = fam === "upscale";
  $("#genControls").hidden = up;
  $("#upscaleNote").hidden = !up;
  if (up) {
    $("#upscaleNote").innerHTML = `Upscaling with <b>${esc(upscalerLabel(v))}</b> to ${v.out_scale}× (change it under Output)` +
      (isOutside(v.upscaler) ? ", on this PC without ComfyUI. " : ". ") +
      `Doors, lights and animation frames go through the same upscaler when the states pass is on. ` +
      `Prompt, style images and the model files don't apply.`;
  }

  const ta = $("#prompt");
  if (document.activeElement !== ta) ta.value = v.prompt;
  $('label[for="prompt"]').className = `lbl ${mark("prompt")}`;
  renderScene(v);
  renderSigns(v);

  $("#refsLbl").className = `lbl ${mark("style_refs")}`;
  $("#refsLbl").textContent = fam === "moss" ? "Style reference images (MOSS doesn't use them)" : "Style reference images";
  const refs = $("#styleRefs");
  refs.classList.toggle("unused", fam === "moss");
  refs.innerHTML = "";
  for (const f of S.styles) {
    const el = document.createElement("div");
    el.className = "ref" + (v.style_refs.includes(f) ? " on" : "");
    el.title = "Click to use or stop using this style image";
    el.innerHTML = `<img src="/styles/${encodeURIComponent(f)}" alt=""><span>${esc(f)}</span>`;
    el.onclick = () => {
      const cur = new Set(values().style_refs);
      cur.has(f) ? cur.delete(f) : cur.add(f);
      saveSetting({ style_refs: [...cur] });
    };
    refs.appendChild(el);
  }

  const box = $("#lookSliders");
  box.innerHTML = "";
  const lockNode = ((S.models && S.models.lock_nodes) || {})[fam];
  for (const k of LOOK_SLIDERS[fam]) {
    const noRefs = k === "style_ref_strength" && !v.style_refs.length, noNode = k === "depth_lock" && lockNode === false;
    box.appendChild(slider(k, v, { dim: noRefs || noNode,
      help: noRefs ? "No style images selected."
        : noNode ? "Needs the comfyui-krea2-controlnet node pack: install it with ComfyUI Manager and restart ComfyUI. Until then, keep this at 0." : "" }));
  }

  const seed = $("#seedRow");
  seed.className = `seed-row ${mark("seed")}`;
  seed.innerHTML = `<span>Seed</span><input type="number" min="0" max="999999" value="${v.seed}"><button class="small ghost">New seed</button><span class="hint">The same seed and settings give the same picture; New seed gives a different take.</span>`;
  seed.querySelector("input").onchange = (e) => saveSetting({ seed: Number(e.target.value) });
  seed.querySelector("button").onclick = () => saveSetting({ seed: Math.floor(Math.random() * 99999) });

  renderOutput(v);
  renderAdvanced(v);
  renderEstimate();
  renderVersionHead();
}

// The field's description, added to the prompt when describe_scene is on
function renderScene(v) {
  const box = $("#sceneBox"), d = S.detail;
  const old = box.querySelector("textarea");
  if (old && document.activeElement === old) return;  // being edited
  box.innerHTML = "";
  box.appendChild(check("describe_scene", "Describe the scene in the prompt", v, helpText(
    "Adds a short description of this field to the prompt (what's in it, what it's made of, the light), so the AI knows what it's painting: cobblestones stay cobblestones instead of turning into water. It's written once, automatically, and you can edit it below.",
    "on, especially for MOSS, Krea 2 and Flux.1")));
  if (!v.describe_scene) return;
  const w = document.createElement("div");
  w.className = "scene";
  w.innerHTML = `<textarea rows="3" spellcheck="false" placeholder="Made on the next render, or press Describe now."></textarea>` +
    `<div class="row-btns tight"><button class="small ghost">${d.caption ? "Describe again" : "Describe now"}</button>` +
    `<button class="small ghost" title="Delete the scene description of every field">Clear all descriptions</button></div>` +
    `<div class="hint">Only for this field. Fix anything it got wrong: it's plain text added to your prompt.</div>`;
  const t = w.querySelector("textarea"), [b, clear] = w.querySelectorAll("button");
  clear.onclick = async () => {
    const { count } = await api("/api/captions");
    if (!count) { toast("No field has a scene description yet."); return; }
    if (!confirm(`Clear ${count === 1 ? "the one scene description there is" : `the scene descriptions of all ${count} fields`}? Any you edited are lost too. ` +
      "Each field gets a new one the next time it renders with descriptions on.")) return;
    try {
      const r = await api("/api/captions/clear", { body: { field: d.name } });
      if (r.field) applyDetail(r.field);
      toast(`Cleared ${r.cleared} scene description${r.cleared === 1 ? "" : "s"}.`);
    } catch (e) { toast(`Couldn't clear them: ${e.message}`); }
  };
  t.value = d.caption || "";
  t.onchange = async () => {
    try { applyDetail(await api(`/api/field/${d.name}/caption`, { body: { text: t.value } })); }
    catch (e) { toast(`Couldn't save: ${e.message}`); }
  };
  b.onclick = async () => {
    b.disabled = true;
    b.textContent = "Describing…";
    try { applyDetail(await api(`/api/field/${d.name}/caption`, { body: { regenerate: true } })); }
    catch (e) { toast(`Couldn't describe: ${e.message}`); b.disabled = false; b.textContent = "Describe now"; }
  };
  box.appendChild(w);
}

// Signs and lettering, repainted lightly (server: ff7bg/signs.py); their areas are drawn on the image
function renderSigns(v) {
  const box = $("#signBox"), d = S.detail;
  box.innerHTML = "";
  const fam = v.family, usable = fam !== "upscale" && v.protect_text;
  if (S.signEdit && (!usable || S.signEditField !== d.name)) setSignEdit(false, false);
  if (fam === "upscale") return;
  box.appendChild(check("protect_text", "Protect signs and text", v, helpText(
    usesRepaint(fam)
      ? "Repaints signs, lettering and logos only lightly, so the text stays readable while the rest of the scene is repainted more strongly. The signs are found automatically, once per field, and you can add or remove areas on the image."
      : "Keeps signs, lettering and logos close to the original, so the model doesn't turn them into made-up letters while it redraws the rest. The signs are found automatically, once per field, and you can add or remove areas on the image.",
    usesRepaint(fam) ? "on for fields with signs, whenever Repaint strength is above about 0.15"
      : fam === "mageflow" ? "on for fields with signs: Mage-Flow garbles lettering"
      : "on for fields whose signs come out garbled; this model often keeps big lettering readable by itself")));
  if (!v.protect_text) return;
  if (fam === "qwen21" && !(v.depth_lock > 0)) {
    const n = document.createElement("div");
    n.className = "model-warn";
    n.textContent = "Turn on Qwen's Depth lock to use this. Without it Qwen shifts the picture by a few pixels, and the protected signs wouldn't line up with the rest (they came out doubled). Until then, signs aren't protected.";
    box.appendChild(n);
  }
  box.appendChild(slider("text_repaint", v));
  const boxes = d.text_boxes, n = boxes ? boxes.length : 0, mine = boxes ? boxes.filter((b) => !b.auto).length : 0;
  const active = !(fam === "qwen21" && !(v.depth_lock > 0));
  const status = boxes == null ? "Not looked for yet: the next render finds them, or press Find signs."
    : n ? `${n} sign area${n === 1 ? "" : "s"} ${active ? "protected" : "marked"}${mine ? ` (${mine} drawn by you)` : ""}.`
    : "No signs found. If it missed some, draw them on the image.";
  const row = document.createElement("div");
  row.className = "signs";
  row.innerHTML = `<div class="hint">${status}</div><div class="row-btns tight">` +
    `<button class="small ${S.signEdit ? "primary" : "ghost"}">${S.signEdit ? "Done editing" : "Show and edit on the image"}</button>` +
    `<button class="small ghost" title="Look for signs again; areas you drew stay">${boxes == null ? "Find signs" : "Find again"}</button></div>`;
  const [edit, find] = row.querySelectorAll("button");
  edit.onclick = () => setSignEdit(!S.signEdit);
  find.onclick = async () => {
    find.disabled = true;
    find.textContent = "Looking…";
    try { applyDetail(await api(`/api/field/${d.name}/signs`, { body: { find: true } })); }
    catch (e) { toast(`Couldn't look for signs: ${e.message}`); find.disabled = false; find.textContent = "Find again"; }
  };
  box.appendChild(row);
}

function setSignEdit(on, rerender = true) {
  S.signEdit = on;
  S.signEditField = on && S.detail ? S.detail.name : null;
  S.signDrag = null;
  vp.classList.toggle("sign-edit", on);
  const hint = $("#signHint");
  hint.hidden = !on;
  if (on) {
    hint.innerHTML = `<b>Sign areas</b> · drag to add one · click one to remove it · Shift+drag moves the picture` +
      `<button class="small primary">Done</button>`;
    hint.querySelector("button").onclick = () => setSignEdit(false);
  }
  if (rerender) { renderStyle(); draw(); }
}

const imagePoint = (e) => {
  const r = vp.getBoundingClientRect();
  return { x: (e.clientX - r.left - S.panX) / S.zoom, y: (e.clientY - r.top - S.panY) / S.zoom };
};

let signSave = null;
function saveSigns(boxes) {
  const d = S.detail;
  d.text_boxes = boxes;
  draw();
  renderStyle();
  clearTimeout(signSave);
  signSave = setTimeout(async () => {
    try { applyDetail(await api(`/api/field/${d.name}/signs`, { body: { boxes } })); }
    catch (e) { toast(`Couldn't save the sign areas: ${e.message}`); }
  }, 300);
}

// Turbo / full switch for the edit models
function renderVariants(v) {
  const seg = $("#variantSeg");
  const vs = VARIANTS[v.family];
  seg.hidden = !vs;
  if (!vs) return;
  const have = S.models && S.models.online ? new Set(S.models.unets || []) : null;
  seg.innerHTML = Object.entries(vs).map(([k, x]) =>
    `<button data-var="${k}" class="${x.unet === v.unet ? "on" : ""}" title="${esc(x.unet)}${have && !have.has(x.unet) ? " (not downloaded yet)" : ""}">` +
    `${x.label}${have && !have.has(x.unet) ? " ⚠" : ""}</button>`).join("");
  for (const b of seg.children) {
    b.onclick = () => {
      const x = vs[b.dataset.var];
      if (x.unet !== v.unet) saveSetting({ unet: x.unet, steps: x.steps, cfg: x.cfg });
    };
  }
}

// Upscalers that run outside ComfyUI (ff7bg/upscalers.py): ImageMagick's Lanczos and Upscayl's models
const isOutside = (u) => u === "magick" || (u || "").startsWith("upscayl:");
const UPSCAYL_NAMES = {
  "upscayl-standard-4x": "Standard", "upscayl-lite-4x": "Lite", "high-fidelity-4x": "High Fidelity",
  "digital-art-4x": "Digital Art", "remacri-4x": "Remacri", "ultramix-balanced-4x": "UltraMix Balanced",
  "ultrasharp-4x": "UltraSharp",
};
function upName(u) {
  if (u === "magick") return "Lanczos (ImageMagick)";
  if (u.startsWith("upscayl:")) return `Upscayl ${UPSCAYL_NAMES[u.slice(8)] || u.slice(8)}`;
  return u.replace(/\.safetensors$|\.pth$/, "");
}

// Every upscaler there is to pick: ComfyUI's ESRGAN models, SeedVR2, ImageMagick and Upscayl
function upscalerChoices(cur, { seedvr2 = true } = {}) {
  const m = S.models, online = m && m.online, local = (m && m.local) || null;  // null: an older Studio
  const svrReady = online && (m.unets || []).some((u) => /seedvr2/i.test(u)) && (m.vaes || []).some((u) => /seedvr2/i.test(u));
  const ups = online ? m.upscalers.slice() : [];
  if (seedvr2 && (svrReady || cur === "seedvr2")) ups.unshift("seedvr2");
  if (local) ups.push("magick", ...local.upscayl_models.map((x) => `upscayl:${x}`));
  const labels = { seedvr2: "SeedVR2 (most natural detail)" };
  for (const u of [...ups, cur]) if (u) labels[u] = labels[u] || upName(u);
  return { ups, labels, local };
}

function outsideHint(u, local) {
  if (u === "magick") {
    return "Lanczos is a plain, soft enlargement that adds no detail of its own." + (local && local.magick ? ""
      : " ImageMagick isn't installed, so a built-in copy of the same resize is used; the result is identical.");
  }
  return `Upscayl's ${esc(upName(u).slice(8))} model is a sharpening upscaler. It enlarges 4×, and other sizes are resized after it.`;
}

// What the Upscale workflow runs, in words
function upscalerLabel(v) {
  if (v.upscaler === "seedvr2") {
    return v.seedvr2_input || !v.input_upscaler ? "SeedVR2 from the original" : `${upName(v.input_upscaler)}, then SeedVR2`;
  }
  return v.upscaler ? upName(v.upscaler) : "a plain resize";
}

function renderOutput(v) {
  const box = $("#outputBox");
  box.innerHTML = "";
  const fam = v.family, m = S.models;
  box.appendChild(slider("out_scale", v));
  const online = m && m.online;
  const { ups, labels, local } = upscalerChoices(v.upscaler);
  const about = (fam === "upscale" ? "Enlarges the original art to the final texture size. " : "Enlarges the AI's picture to the final texture size. ") +
    (isOutside(v.upscaler) ? outsideHint(v.upscaler, local) + (fam === "upscale" ? " Works without ComfyUI." : "")
      : "SeedVR2 adds the most natural detail; 4x-UltraSharp is crisp but can look grainy; (none) is soft and adds nothing.") +
    (fam !== "upscale" ? " (The original art has its own upscaler, under Advanced → Original art.)" : "");
  const hint = online || isOutside(v.upscaler)
    ? helpText(about, fam === "upscale" ? "SeedVR2 for the most detail, 4x-UltraSharp for the most faithful" : "SeedVR2, or 4x-UltraSharp if you want speed")
    : "ComfyUI isn't reachable, so only the saved choice is listed.";
  box.appendChild(selectRow("upscaler", "Upscaler", ups, v, { none: "(none: plain, soft resize)", labels, hint }));
  if (local && !local.upscayl) {
    const n = document.createElement("div");
    n.className = "hint";
    n.textContent = "Upscayl isn't installed. Install it (or put upscayl-bin in tools/upscayl) and press Refresh models to list its models here.";
    box.appendChild(n);
  }
  if (v.upscaler === "seedvr2" && fam === "upscale") {
    box.appendChild(check("seedvr2_input", "SeedVR2 works from the original pixels", v, helpText(
      "On: SeedVR2 works straight from the original pixels. More new detail, but it can invent texture (Kalm's cracked plaza became cobblestones). Off: a regular upscaler goes first and SeedVR2 cleans it up, which stays closer to the original.",
      "off, for faithful results")));
  }
  if (fam === "upscale") {
    box.appendChild(check("states_pass", "States pass (Final only)", v, helpText(
      "Also enlarges doors, lights and animation frames the same way, so they match the scene.", "on")));
    return;
  }
  if (!EDIT.has(fam)) {
    box.appendChild(check("detail_pass", "Detail pass", v, helpText(
      "After the main render, paints extra fine texture (stone grain, grime, lettering) at full size. Much slower: about a minute per field at 8×." +
      (isOutside(v.upscaler) ? ` While it's on, ${esc(upName(v.upscaler))} isn't used: a plain enlargement goes first.` : ""),
      "off at 4×; worth trying at 6× and above")));
    if (v.detail_pass) box.appendChild(slider("detail_denoise", v));
  }
  box.appendChild(check("states_pass", "States pass (Final only)", v, helpText(
    "Also repaints doors, lights and animation frames to match the new style. Without it they keep the original art, which can look out of place. Adds time.",
    "on")));
  box.appendChild(slider("keep_colors", v));
  box.appendChild(slider("tries", v));
  box.appendChild(check("retry", "Retry if the layout changed (Final)", v, helpText(
    "If the best render keeps less than 80% of the original outlines (a sign that things moved), it tries once more from a new random start" +
    (usesRepaint(fam) ? ", with a little less repaint." : "."), "on for big batches")));
  const alignRec = EDIT.has(fam) ? "on (this model zooms or shifts a little)"
    : fam === "flux1" ? "off (the edge lock already holds the layout)" : "off; turn it on if a render looks shifted";
  box.appendChild(check("align", "Auto-align to the original layout", v, helpText(
    "Fixes a slight overall zoom or shift in the new picture, so it lines up with where characters can walk. It moves the whole picture at once and never bends it.",
    alignRec)));
}

const looksFlux1 = (name) => /flux/i.test(name || "") && !/flux[._-]?2|flux2/i.test(name || "");
const looksKrea2 = (name) => /krea[._-]?2/i.test(name || "");
const looksQwen21 = (name) => /qwen[._-]?image[._-]?2[._-]?1/i.test(name || "");
const looksFlux3 = (name) => /flux[._-]?3/i.test(name || "");
const looksMage = (name) => /mage[._-]?flow/i.test(name || "");

const online0 = (m) => !!(m && m.online);

function renderAdvanced(v) {
  const box = $("#advBox");
  box.innerHTML = "";
  const m = S.models, fam = v.family;
  const sub = (t) => { const h = document.createElement("div"); h.className = "subhead"; h.textContent = t; box.appendChild(h); };
  const add = (el) => box.appendChild(el);

  if (fam === "upscale") {
    if (v.upscaler === "seedvr2" && online0(m)) {
      sub("SeedVR2 upscaler");
      add(selectRow("seedvr2_unet", "SeedVR2 model", (m.unets || []).filter((u) => /seedvr2/i.test(u)), v, { hint: helpText("3B is smoother and closest to the original; 7B gives crisper texture (brick, cobbles) and is a little slower.", "3B for faithful, 7B for crisp") }));
      add(selectRow("seedvr2_vae", "SeedVR2 VAE", (m.vaes || []).filter((u) => /seedvr2/i.test(u)), v));
      add(selectRow("seedvr2_color", "Color correction", ["lab", "wavelet", "adain", "none"], v,
        { hint: helpText("Keeps SeedVR2's colours matched to the picture it enlarges.", "lab") }));
      if (!v.seedvr2_input) {
        const { ups, labels, local } = upscalerChoices(v.input_upscaler, { seedvr2: false });
        add(selectRow("input_upscaler", "Upscaler before SeedVR2", ups, v, { none: "(none: plain, soft resize)", labels,
          hint: helpText("Enlarges the original first; SeedVR2 then cleans it up." +
            (isOutside(v.input_upscaler) ? " " + outsideHint(v.input_upscaler, local) : ""), "4x-UltraSharp") }));
      }
    } else {
      const n = document.createElement("div");
      n.className = "hint";
      n.textContent = isOutside(v.upscaler) ? "Nothing else to set: the upscaler under Output is the whole workflow, and it runs without ComfyUI."
        : online0(m) ? "Nothing else to set: the upscaler under Output is the whole workflow."
        : "ComfyUI isn't reachable, so the installed models can't be listed. Start it and press Refresh models.";
      add(n);
    }
    const row0 = document.createElement("div");
    row0.className = "row-btns tight";
    row0.innerHTML = `<button class="small ghost" title="Re-read the model lists from ComfyUI (after adding files)">Refresh models</button>`;
    row0.querySelector("button").onclick = loadModels;
    add(row0);
    return;
  }
  sub("Rendering");
  const fixedSchedule = fam === "qwen21" && /turbo/i.test(v.unet || "");  // Qwen Turbo: fixed 8 steps
  if (!fixedSchedule) add(slider("steps", v));
  if ((EDIT.has(fam) || fam === "moss") && !fixedSchedule) add(slider("cfg", v));
  add(slider("work_mp", v, v.input_scale ? { dim: true, help: "Not used right now: Original art → Enlarge by sets the size." } : {}));
  if (EDIT.has(fam)) add(slider("edit_margin", v));
  if (v.detail_pass && !EDIT.has(fam)) {
    add(slider("detail_steps", v));
    if (fam === "flux1") {
      add(slider("detail_structure", v));
      add(check("detail_style", "Use the style images in the detail pass", v, helpText(
        "Uses the style images in the detail pass too. About 15% slower, and faint seams can show between tiles.", "off")));
    }
  }
  if (v.states_pass) {
    add(slider("state_denoise", v));
    if (!EDIT.has(fam)) add(slider("state_denoise_boost", v));
  }

  sub("Original art");
  {
    const { ups, labels, local } = upscalerChoices(v.input_upscaler);
    const hint = helpText("Enlarges the original art before the AI paints over it. A sharper start gives the AI more to work with." +
      (isOutside(v.input_upscaler) ? " " + outsideHint(v.input_upscaler, local)
        : v.input_upscaler === "seedvr2" ? " SeedVR2 adds detail of its own, but can invent texture (Kalm's cracked plaza became cobblestones)." : ""),
      "4x-UltraSharp");
    add(selectRow("input_upscaler", "Upscaler", ups, v, { none: "(none: plain, soft resize)", labels, hint }));
    add(slider("input_scale", v));
    // what that comes to for this field
    const d = S.detail, edge = (n) => EDIT.has(fam) ? Math.round(n * v.edit_margin / 16) * 16 : 0;
    const pw = d.w + 2 * edge(d.w), ph = d.h + 2 * edge(d.h);
    const k = v.input_scale || Math.max(1, Math.floor(Math.sqrt(v.work_mp * 1e6 / (pw * ph))));
    const mp = pw * k * ph * k / 1e6;
    const n = document.createElement("div");
    n.className = mp > 4.5 ? "model-warn" : "hint";
    n.textContent = `${d.name} will be painted at ${pw * k}×${ph * k} (${k}× the original${EDIT.has(fam) ? ", plus the border padding" : ""}).` +
      (mp > 4.5 ? " That's large: much slower, and models start repeating details at this size." : "");
    add(n);
  }

  sub(`Model files: ${famShort(fam)}`);
  const online = m && m.online;
  if (!online) {
    const n = document.createElement("div");
    n.className = "hint";
    n.textContent = "ComfyUI isn't reachable, so the installed models can't be listed. Start it and press Refresh models.";
    add(n);
  } else {
    const separate = v.model_source === "separate";
    const src = document.createElement("div");
    src.className = `ctl ${mark("model_source")}`;
    src.innerHTML = `<label class="lbl">Base model comes from</label><div class="seg small">` +
      `<button data-src="checkpoint" class="${separate ? "" : "on"}">One checkpoint file</button>` +
      `<button data-src="separate" class="${separate ? "on" : ""}">Separate files</button></div>`;
    for (const b of src.querySelectorAll("button")) b.onclick = () => saveSetting({ model_source: b.dataset.src });
    add(src);
    if (!separate) {
      add(selectRow("checkpoint", "Checkpoint", m.checkpoints, v, { hint: "The AI model, as one all-in-one file. The workflow sets it; change it only if you downloaded another version." }));
    } else {
      const types = [...new Set([...(v.clip2 ? m.clip_types_dual : m.clip_types_single), ...m.clip_types_dual])];
      add(selectRow("unet", "Diffusion model", m.unets, v, { hint: "The main AI model file. The workflow and the Turbo/Full switch set it; change it only if you downloaded another version." }));
      add(selectRow("unet_dtype", "Weight type", m.unet_dtypes, v, { hint: "Leave on default." }));
      add(selectRow("clip1", "Text encoder 1", m.clips, v, {
        hint: "The part of the model that reads your prompt. Leave it as the workflow set it (" +
          ({ krea2: "qwen3vl_4b", qwen21: "qwen3vl_8b", flux3k: "qwen3vl_4b", mageflow: "qwen3vl_4b", fibo2: "qwen3vl_4b bf16",
             moss: "qwen3.5_9b" }[fam] || "clip_l and t5xxl") + ")." }));
      add(selectRow("clip2", "Text encoder 2", m.clips, v, { none: "(none: single encoder)" }));
      add(selectRow("clip_type", "Encoder type", types, v, {
        hint: "Must match the model; the workflow sets it." }));
      add(selectRow("vae", "VAE", m.vaes, v, { hint: "Turns the model's internal format into pictures. Leave it as the workflow set it." }));
    }
    if (fam === "flux1") {
      add(selectRow("controlnet", "ControlNet (edge lock)", m.controlnets, v,
        { none: "(none: no edge lock)", hint: "The model behind the edge lock (and Flux's depth lock). Leave it as set; (none) turns the locks off." }));
    }
    add(selectRow("lora", "Extra LoRA", m.loras, v, { none: "(none)", hint: "An optional add-on that pushes a particular style. (none) is the normal choice." }));
    if (v.lora) add(slider("lora_strength", v));

    const base = separate ? v.unet : v.checkpoint;
    const warns = [];
    if (fam === "krea2") {
      if (base && !looksKrea2(base)) warns.push(`"${base}" doesn't look like a Krea 2 model.`);
      if (v.style_refs.length > 2) warns.push(`Krea 2's style-reference LoRA was trained on 1-2 images; ${v.style_refs.length} are selected (only the first 3 are used).`);

    } else if (fam === "qwen21") {
      if (base && !looksQwen21(base)) warns.push(`"${base}" doesn't look like a Qwen-Image 2.1 model.`);

    } else if (fam === "flux3k") {
      if (base && !looksFlux3(base)) warns.push(`"${base}" doesn't look like a FLUX 3 Klein model.`);

    } else if (fam === "mageflow") {
      if (base && !looksMage(base)) warns.push(`"${base}" doesn't look like a Mage-Flow Edit model.`);
    } else if (fam === "fibo2") {
      if (base && !/fibo/i.test(base)) warns.push(`"${base}" doesn't look like a FIBO 2.0 model.`);
    } else if (fam === "moss") {
      if (base && !/moss/i.test(base)) warns.push(`"${base}" doesn't look like a MOSS-Image model.`);

    } else if (base && !looksFlux1(base)) {
      warns.push(`"${base}" doesn't look like a Flux.1 model; the Flux.1 workflow uses prompt guidance, the Flux ControlNet and USO style images.`);
    }
    if (separate && (!v.unet || !v.clip1 || !v.vae)) warns.push("Pick a diffusion model, at least one text encoder and a VAE.");
    if (warns.length) {
      const w = document.createElement("div");
      w.className = "model-warn";
      w.innerHTML = warns.map(esc).join("<br>");
      add(w);
    }

    if (v.upscaler === "seedvr2" || v.input_upscaler === "seedvr2") {
      sub("SeedVR2 upscaler");
      add(selectRow("seedvr2_unet", "SeedVR2 model", (m.unets || []).filter((u) => /seedvr2/i.test(u)), v, { hint: helpText("3B is smoother and closest to the original; 7B gives crisper texture (brick, cobbles) and is a little slower.", "3B for faithful, 7B for crisp") }));
      add(selectRow("seedvr2_vae", "SeedVR2 VAE", (m.vaes || []).filter((u) => /seedvr2/i.test(u)), v));
      add(selectRow("seedvr2_color", "Color correction", ["lab", "wavelet", "adain", "none"], v,
        { hint: helpText("Keeps SeedVR2's colours matched to the picture it enlarges.", "lab") }));
    }
  }
  const row = document.createElement("div");
  row.className = "row-btns tight";
  row.innerHTML = `<button class="small ghost" title="Re-read the model lists from ComfyUI (after adding files)">Refresh models</button>`;
  row.querySelector("button").onclick = loadModels;
  add(row);
}

async function saveSetting(patch) {
  const d = S.detail;
  if (!d) return;
  const name = d.name;
  const url = S.target === "look"
    ? `/api/looks/${encodeURIComponent(d.look.id)}/settings`
    : `/api/field/${name}/settings`;
  try {
    const nd = await api(url, { body: { values: patch, field: name } });
    applyDetail(nd);
    if (S.target === "look" || "family" in patch) loadLooks();
  } catch (e) {
    toast(`Couldn't save: ${e.message}`);
  }
}

function setTarget(t) {
  S.target = t;
  setPref("target", t);
  renderStyle();
}

/* ------------------------------------------------------------ looks */

async function loadLooks() {
  try { S.looks = await api("/api/looks"); } catch (e) { return; }
  renderLookFilter();
  if (S.detail) { renderLookBar(); renderStatus(); }
  if (!$("#looksModal").hidden) renderLooksModal();
  renderList();
}

async function afterLooksChange() {
  await loadLooks();
  await loadFields();
  if (S.current) applyDetail(await api(`/api/field/${S.current}`));
}

function openLooks() {
  S.rules = ((S.looks && S.looks.assignments) || []).map((r) => ({ ...r }));
  S.rulesDirty = false;
  renderLooksModal();
  $("#looksModal").hidden = false;
  loadLooks();
}

function renderLooksModal() {
  const L = S.looks;
  if (!L) return;
  const cur = S.detail && S.detail.look.id;
  $("#looksTable").innerHTML = `<tr><th>Name</th><th>Workflow</th><th>Fields</th><th>Default</th><th></th></tr>` +
    L.looks.map((l) => `<tr data-id="${esc(l.id)}" class="${l.id === cur ? "current" : ""}" title="${l.id === cur ? "The look the selected field uses" : ""}">` +
      `<td><input type="text" value="${esc(l.name)}"></td><td>${famShort(l.family)}</td><td class="num">${l.fields}</td>` +
      `<td><input type="radio" name="deflook" ${l.id === L.default ? "checked" : ""} title="Used by every field no rule or choice covers"></td>` +
      `<td class="acts"><button class="small ghost" data-a="dup">Duplicate</button>` +
      `<button class="small ghost danger" data-a="del" ${l.id === L.default ? 'disabled title="The default look can\'t be deleted"' : ""}>Delete</button></td></tr>`).join("");
  for (const tr of $("#looksTable").querySelectorAll("tr[data-id]")) {
    const id = tr.dataset.id;
    const url = `/api/looks/${encodeURIComponent(id)}`;
    tr.querySelector('input[type="text"]').onchange = async (e) => {
      if (!e.target.value.trim()) return;
      await api(url, { body: { name: e.target.value } });
      afterLooksChange();
    };
    tr.querySelector('input[type="radio"]').onchange = async () => {
      await api(url, { body: { default: true } });
      afterLooksChange();
    };
    tr.querySelector('[data-a="dup"]').onclick = async () => {
      const name = prompt("Name for the copy:", `${lookName(id)} copy`);
      if (!name) return;
      await api("/api/looks", { body: { name, from_look: id } });
      afterLooksChange();
    };
    tr.querySelector('[data-a="del"]').onclick = async () => {
      const l = L.looks.find((x) => x.id === id);
      if (!confirm(`Delete look "${l.name}"?\n\nThe ${l.fields} field(s) using it go back to automatic (rules or the default look). Rules for it are removed. Versions made with it are kept.`)) return;
      try { await api(url, { method: "DELETE" }); } catch (e) { toast(e.message); return; }
      S.rules = S.rules.filter((r) => r.look !== id);
      afterLooksChange();
    };
  }
  renderRules();
}

function globRe(p) {
  const body = p.toLowerCase().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${body}$`);
}

function ruleMatches(p) {
  if (!p.trim()) return 0;
  const re = globRe(p.trim());
  return S.fields.filter((f) => [f.name, ...f.copies].some((n) => re.test(n))).length;
}

function rulesDirty() {
  S.rulesDirty = true;
  $("#rulesState").textContent = "Unsaved changes";
  $("#btnSaveRules").disabled = false;
}

function renderRules() {
  const L = S.looks;
  const opts = (sel) => L.looks.map((l) => `<option value="${esc(l.id)}" ${l.id === sel ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  $("#rulesTable").innerHTML = `<tr><th>Field name pattern</th><th>Look</th><th>Matches</th><th></th></tr>` +
    (S.rules.length ? S.rules.map((r, i) => `<tr data-i="${i}"><td><input type="text" value="${esc(r.pattern)}" placeholder="e.g. md*"></td>` +
      `<td><select>${opts(r.look)}</select></td><td class="num">${ruleMatches(r.pattern)} fields</td>` +
      `<td class="acts"><button class="small ghost" data-a="up" ${i ? "" : "disabled"} title="Check this rule earlier">↑</button>` +
      `<button class="small ghost danger" data-a="rm">Remove</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="hint">No rules yet: every field uses the default look unless you pick one for it.</td></tr>`);
  for (const tr of $("#rulesTable").querySelectorAll("tr[data-i]")) {
    const i = Number(tr.dataset.i);
    tr.querySelector("input").oninput = (e) => {
      S.rules[i].pattern = e.target.value;
      tr.querySelector(".num").textContent = `${ruleMatches(e.target.value)} fields`;
      rulesDirty();
    };
    tr.querySelector("select").onchange = (e) => { S.rules[i].look = e.target.value; rulesDirty(); };
    tr.querySelector('[data-a="up"]').onclick = () => {
      [S.rules[i - 1], S.rules[i]] = [S.rules[i], S.rules[i - 1]];
      renderRules(); rulesDirty();
    };
    tr.querySelector('[data-a="rm"]').onclick = () => { S.rules.splice(i, 1); renderRules(); rulesDirty(); };
  }
  $("#rulesState").textContent = S.rulesDirty ? "Unsaved changes" : "";
  $("#btnSaveRules").disabled = !S.rulesDirty;
}

/* ------------------------------------------------------------ viewer */

const canvas = $("#canvas");
const ctx = canvas.getContext("2d");
const vp = $("#viewport");

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(vp.clientWidth * dpr);
  canvas.height = Math.round(vp.clientHeight * dpr);
  draw();
}

function fitView() {
  const d = S.detail;
  if (!d) return;
  setZoom(Math.min(vp.clientWidth / d.w, vp.clientHeight / d.h) * 0.96, true);
}

function setZoom(z, center = false, cx = vp.clientWidth / 2, cy = vp.clientHeight / 2) {
  const d = S.detail;
  if (!d) return;
  z = Math.max(0.1, Math.min(z, 40));
  if (center) {
    S.panX = (vp.clientWidth - d.w * z) / 2;
    S.panY = (vp.clientHeight - d.h * z) / 2;
  } else {
    S.panX = cx - (cx - S.panX) * (z / S.zoom);
    S.panY = cy - (cy - S.panY) * (z / S.zoom);
  }
  S.zoom = z;
  draw();
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const d = S.detail;
  const empty = $("#emptyMsg");
  for (const b of $("#modeSeg").children) b.classList.toggle("on", b.dataset.mode === S.mode);
  if (!d || !S.imgs.orig) {
    empty.style.display = "flex";
    empty.textContent = S.ready ? "Pick a field on the left." : "Preparing field images…";
    $("#modeBadge").textContent = "";
    $("#badgeL").hidden = $("#badgeR").hidden = true;
    return;
  }
  empty.style.display = "none";
  const W = d.w, H = d.h;
  ctx.setTransform(dpr * S.zoom, 0, 0, dpr * S.zoom, dpr * S.panX, dpr * S.panY);
  // Current frame of each playing animation, from its strip, over the group's area.
  // FF7 fields run at 30 fps; `wait` is how many game frames each animation frame lasts.
  const now = performance.now();
  const drawFrames = (side) => {
    for (const st of S.strips) {
      const frames = st[side];
      if (!frames) continue;
      const [x, y, w, h] = st.rect;
      const i = Math.floor(now / (st.wait * 1000 / 30)) % st.frames;
      // only on the group's own tiles, so a group spread over the scene doesn't paint a big
      // rectangle over everything else (or over another group's animation)
      ctx.save();
      if (st.tiles) {
        ctx.beginPath();
        for (const [tx, ty, ts] of st.tiles) ctx.rect(tx, ty, ts, ts);
        ctx.clip();
      }
      ctx.drawImage(frames[i], x, y, w, h);
      ctx.restore();
    }
  };
  const paint = (side) => {
    if (side !== "orig" && !S.imgs[side]) side = "orig";
    if (side === "orig") {
      ctx.imageSmoothingEnabled = !S.pixel;
    } else {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
    }
    ctx.drawImage(S.imgs[side], 0, 0, W, H);
    drawFrames(side);
  };
  const nv = viewedVersion(), lv = leftVersion();
  const label = (v, img) => `${verKind(v)} ${fmtWhen(v.created)}` +
    (img ? ` · ${Math.round(img.naturalWidth / W)}×` : "") + (v.built ? "" : " · raw render, not built");
  let badge;
  if (S.mode === "orig") {
    paint("orig");
    badge = "Original";
  } else if (S.mode === "new") {
    paint("new");
    badge = S.imgs.new ? label(nv, S.imgs.new) : "No versions yet (showing original)";
  } else {
    paint("new");
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W * S.split, H); ctx.clip();
    paint(lv ? "left" : "orig");
    ctx.restore();
    // outline each side in its colour from the version strip: the divider shows as a yellow | blue edge
    const css = getComputedStyle(document.documentElement), lw = 2 / S.zoom, x = W * S.split;
    ctx.lineWidth = lw;
    if (x > lw) { ctx.strokeStyle = css.getPropertyValue("--warn").trim(); ctx.strokeRect(lw / 2, lw / 2, x - lw, H - lw); }
    if (W - x > lw) { ctx.strokeStyle = css.getPropertyValue("--accent").trim(); ctx.strokeRect(x + lw / 2, lw / 2, W - x - lw, H - lw); }
    $("#badgeL").innerHTML = `<b>Left</b> · ${esc(lv ? label(lv, S.imgs.left) : "Original")}`;
    $("#badgeR").innerHTML = `<b>Right</b> · ${esc(S.imgs.new ? label(nv, S.imgs.new) : "no version yet")}`;
    badge = "";
  }
  $("#badgeL").hidden = $("#badgeR").hidden = S.mode !== "split";
  if (S.overlay && S.imgs.overlay) {
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = 0.9;
    ctx.drawImage(S.imgs.overlay, 0, 0, W, H);
    ctx.globalAlpha = 1;
  }
  if (S.signEdit && S.signEditField === d.name) {
    ctx.save();
    ctx.lineWidth = 2 / S.zoom;
    for (const b of d.text_boxes || []) {
      ctx.fillStyle = "rgba(255, 60, 200, 0.15)";
      ctx.fillRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
      ctx.strokeStyle = "#ff3cc8";
      ctx.setLineDash(b.auto ? [] : [6 / S.zoom, 4 / S.zoom]);  // dashed: drawn by you
      ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
    }
    const g = S.signDrag;
    if (g && g.moved) {
      ctx.strokeStyle = "#fff";
      ctx.setLineDash([4 / S.zoom, 4 / S.zoom]);
      ctx.strokeRect(Math.min(g.x0, g.x1), Math.min(g.y0, g.y1), Math.abs(g.x1 - g.x0), Math.abs(g.y1 - g.y0));
    }
    ctx.restore();
  }
  $("#modeBadge").textContent = badge;
  $("#modeBadge").hidden = !badge;
}

function setMode(m) {
  S.mode = m;
  S.userMode = m;
  renderSplitPick();
  renderVersionHead();
  draw();
}

let drag = null;
vp.addEventListener("mousedown", (e) => {
  if (S.signEdit && S.detail && !e.shiftKey && e.button === 0) {
    const p = imagePoint(e);
    S.signDrag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, sx: e.clientX, sy: e.clientY, moved: false };
    return;
  }
  const r = vp.getBoundingClientRect();
  const x = e.clientX - r.left;
  const divider = S.panX + S.zoom * (S.detail ? S.detail.w : 0) * S.split;
  drag = S.mode === "split" && Math.abs(x - divider) < 10
    ? { type: "split" }
    : { type: "pan", x: e.clientX, y: e.clientY, px: S.panX, py: S.panY };
  vp.classList.add("dragging");
});
window.addEventListener("mousemove", (e) => {
  if (S.signDrag) {
    const p = imagePoint(e), g = S.signDrag;
    g.x1 = p.x;
    g.y1 = p.y;
    g.moved = g.moved || Math.hypot(e.clientX - g.sx, e.clientY - g.sy) > 4;
    draw();
    return;
  }
  const r = vp.getBoundingClientRect();
  const x = e.clientX - r.left;
  if (!drag) {
    if (S.mode === "split" && S.detail) {
      const divider = S.panX + S.zoom * S.detail.w * S.split;
      vp.classList.toggle("split-hover", Math.abs(x - divider) < 10 && e.target === canvas);
    }
    return;
  }
  if (drag.type === "split") {
    S.split = Math.max(0, Math.min(1, (x - S.panX) / (S.zoom * S.detail.w)));
  } else {
    S.panX = drag.px + (e.clientX - drag.x);
    S.panY = drag.py + (e.clientY - drag.y);
  }
  draw();
});
window.addEventListener("mouseup", () => {
  const g = S.signDrag, d = S.detail;
  if (g && d) {
    S.signDrag = null;
    const boxes = (d.text_boxes || []).slice();
    const clampX = (v) => Math.max(0, Math.min(d.w, v)), clampY = (v) => Math.max(0, Math.min(d.h, v));
    if (g.moved) {  // a new area
      const b = { x1: clampX(Math.min(g.x0, g.x1)), y1: clampY(Math.min(g.y0, g.y1)),
                  x2: clampX(Math.max(g.x0, g.x1)), y2: clampY(Math.max(g.y0, g.y1)), text: "", auto: false };
      if (b.x2 - b.x1 < 1 || b.y2 - b.y1 < 1) { draw(); return; }
      boxes.push(b);
    } else {  // a click removes the smallest area under it
      const area = (b) => (b.x2 - b.x1) * (b.y2 - b.y1);
      const hits = boxes.map((b, i) => [b, i]).filter(([b]) => g.x0 >= b.x1 && g.x0 <= b.x2 && g.y0 >= b.y1 && g.y0 <= b.y2);
      if (!hits.length) { draw(); return; }
      hits.sort((a, b) => area(a[0]) - area(b[0]));
      boxes.splice(hits[0][1], 1);
    }
    saveSigns(boxes);
    return;
  }
  drag = null;
  vp.classList.remove("dragging");
});
vp.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = vp.getBoundingClientRect();
  setZoom(S.zoom * Math.pow(1.0015, -e.deltaY), false, e.clientX - r.left, e.clientY - r.top);
}, { passive: false });

window.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (!$("#looksModal").hidden) { if (e.key === "Escape") $("#looksModal").hidden = true; return; }
  if (e.key === "Escape") {
    if (S.signEdit) setSignEdit(false);
    else if (!$("#queuePop").hidden) $("#queuePop").hidden = true;
    else if (S.sel.size) clearPicks();
    return;
  }
  if (e.code === "Space") { e.preventDefault(); setMode(S.mode === "orig" ? "new" : "orig"); }
  else if (e.key === "1") setMode("orig");
  else if (e.key === "2") setMode("new");
  else if (e.key === "3") setMode("split");
  else if (e.key === "f" || e.key === "F") fitView();
  else if (e.key === "o" || e.key === "O") $("#optOverlay").click();
  else if (e.key === "a" || e.key === "A") $("#optAnimate").click();
  else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    const d = S.detail, v = viewedVersion();
    if (!d || !v) return;
    e.preventDefault();
    const i = d.versions.findIndex((x) => x.id === v.id);
    const j = Math.max(0, Math.min(d.versions.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)));
    if (j !== i) pickVersion(d.versions[j].id);
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const visible = [...$("#fieldList").children].filter((li) => li.style.display !== "none");
    const i = visible.findIndex((li) => li.dataset.name === S.current);
    const next = visible[Math.max(0, Math.min(visible.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) { selectField(next.dataset.name); next.scrollIntoView({ block: "nearest" }); }
  }
});

/* ------------------------------------------------------------ status, estimate, queue */

function renderStatus() {
  const d = S.detail;
  if (!d) return;
  const parts = [`<b>${d.name}</b> ${d.w}×${d.h}`];
  if (d.copies.length) parts.push(`also used by ${d.copies.join(", ")}`);
  parts.push(`look "${esc(d.look.name)}"${d.custom ? " + field changes" : ""} · ${famShort(d.effective.family)}`);
  const cur = d.versions.find((v) => v.current);
  parts.push(cur ? `in game: ${esc(verKind(cur))} from ${fmtWhen(cur.created, true)}` : `<span class="warn">nothing in game yet</span>`);
  parts.push(`${d.versions.length} version${d.versions.length === 1 ? "" : "s"}`);
  if (d.error) parts.push(`<span class="bad">last run failed: ${esc(d.error)}</span>`);
  $("#stageStatus").innerHTML = parts.join(" · ");
}

function renderEstimate() {
  const d = S.detail, el = $("#estimate");
  if (!d) return;
  const s = d.effective, e = d.estimate.final, p = d.estimate.preview;
  const size = (k) => `${d.w * k}×${d.h * k} (${k}×)`;
  const parts = [`render ~${fmtDuration(e.render)}`];
  if (e.tiles) parts.push(`detail ${e.tiles} tiles`);
  if (e.sheets) parts.push(`states: ${d.state_panels} in ${e.sheets} sheet${e.sheets > 1 ? "s" : ""}`);
  let html = `<div>Renders with <b>${famShort(s.family)}</b>, look "${esc(d.look.name)}"` +
    (d.custom ? ` <span style="color:var(--field)">+ ${d.override_keys.length} field change${d.override_keys.length > 1 ? "s" : ""}</span>` : "") + `</div>`;
  html += `<div><b>Final</b> ${size(e.scale)} · ${parts.join(" + ")} · <b>~${fmtDuration(e.total)}</b></div>`;
  html += `<div><b>Preview</b> ${size(p.scale)} · render only · ~${fmtDuration(p.total)}</div>`;
  if (!e.tiles && !e.sheets && e.scale === p.scale) html += `<div class="hint">With the detail and states passes off, Final and Preview give the same result.</div>`;
  el.innerHTML = html;
}

function jobLabel(j) {
  if (j.kind === "build") return "building textures";
  return `${j.try ? "try" : j.kind} ${famShort(j.family)}`;
}

function renderQueuePop() {
  const q = S.queue || { queued: [], recent: [] };
  const items = [];
  if (q.current) {
    items.push(`<li class="now"><span class="what"><b class="go" data-go="${q.current.name}">${q.current.name}</b> · ${jobLabel(q.current)} · ${esc(q.current.stage)}</span>` +
      `<span>${Math.round(q.current.progress * 100)}%</span></li>`);
  }
  for (const j of q.queued) {
    items.push(`<li><span class="what"><b class="go" data-go="${j.name}">${j.name}</b> · ${jobLabel(j)}</span>` +
      `<span>~${fmtDuration(j.est || 0)}</span><span class="x" data-rm="${j.id}" title="Remove from the queue">×</span></li>`);
  }
  $("#queueList").innerHTML = items.join("") || "<li>Nothing queued.</li>";
  $("#recentList").innerHTML = q.recent.slice().reverse().map((r) =>
    `<li class="${r.ok ? "" : "bad"}"><span class="what">${r.ok ? "✓" : "✗"} <b class="go" data-go="${r.name}">${r.name}</b> · ${jobLabel(r)} · ${r.seconds}s` +
    `${r.ok ? "" : " · " + esc(r.message)}</span></li>`).join("") || "<li>None yet.</li>";
  for (const el of $("#queuePop").querySelectorAll("[data-rm]")) {
    el.onclick = async () => { S.queue = await api("/api/queue/remove", { body: { id: Number(el.dataset.rm) } }); renderQueuePop(); renderVersions(); };
  }
  for (const el of $("#queuePop").querySelectorAll("[data-go]")) {
    el.onclick = () => { selectField(el.dataset.go); const li = $(`#fieldList li[data-name="${el.dataset.go}"]`); if (li) li.scrollIntoView({ block: "nearest" }); };
  }
}

async function pollQueue() {
  try {
    const q = await api("/api/queue");
    S.queue = q;
    const cur = q.current;
    S.running = cur ? cur.name : null;
    const elapsed = cur ? Date.now() / 1000 - cur.started : 0;
    const left = q.queued.reduce((t, j) => t + (j.est || 0), 0) + (cur ? Math.max(0, (cur.est || 0) - elapsed) : 0);
    const sum = $("#queueSummary");
    sum.textContent = cur
      ? `${cur.name} · ${jobLabel(cur)}: ${cur.stage}` + (q.queued.length ? ` · ${q.queued.length} queued · ~${fmtDuration(left)} left` : "")
      : (q.queued.length ? `${q.queued.length} queued` : "Idle");
    sum.classList.toggle("busy", !!cur);
    const bar = $("#progress .bar"), txt = $("#progress .ptext");
    if (cur) {
      bar.style.width = `${Math.round(cur.progress * 100)}%`;
      txt.textContent = `${cur.name} · ${jobLabel(cur)}: ${cur.stage} (${Math.round(elapsed)}s)`;
    } else {
      bar.style.width = "0";
      txt.textContent = q.queued.length ? `${q.queued.length} queued` : "Idle";
    }
    // the version strip shows this field's running and queued renders
    const key = JSON.stringify([S.current, cur && cur.id, q.queued.filter((j) => j.name === S.current).map((j) => j.id)]);
    if (key !== S.queueKey) {
      S.queueKey = key;
      renderVersions();
    } else if (cur && cur.name === S.current) {
      const rb = $("#runBar"), rs = $("#runStage");
      if (rb) rb.style.width = `${Math.round(cur.progress * 100)}%`;
      if (rs) rs.textContent = cur.stage;
    }
    if (!$("#queuePop").hidden) renderQueuePop();

    const newest = Math.max(0, ...q.recent.map((r) => r.finished));
    if (S.lastFinished === null) S.lastFinished = newest;  // ignore jobs finished before this page loaded
    if (newest > S.lastFinished) {
      const fresh = q.recent.filter((r) => r.finished > S.lastFinished);
      S.lastFinished = newest;
      await loadFields();
      loadLooks();
      const mine = fresh.filter((r) => r.name === S.current);
      if (mine.length) {
        const made = mine.filter((r) => r.ok && r.kind !== "build" && r.version).pop();
        if (made) {  // show what just finished
          S.view = made.version;
          if (S.mode === "orig") S.mode = "new";
          S.userMode = S.mode;
        }
        await selectField(S.current, true);
      }
    }
    renderList();
  } catch (e) { /* server restarting */ }
  setTimeout(pollQueue, 1000);
}

async function pollStatus() {
  try {
    const s = await api("/api/status");
    $("#comfyStatus").classList.toggle("online", s.comfy_online);
    $("#comfyStatus").title = `ComfyUI at ${s.comfy_url}: ${s.comfy_online ? "connected" : "not reachable; start ComfyUI"}`;
    $("#btnInstall").title = `Copy the current versions' textures into ${s.game_mods}`;
    if (s.ready && !S.ready) {
      S.ready = true;
      await loadLooks();
      await loadFields();
      if (!S.current && S.fields.length) selectField(S.byName.elm ? "elm" : S.fields[0].name);
    }
    if (!s.ready) $("#emptyMsg").textContent = s.status;
  } catch (e) { /* ignore */ }
  setTimeout(pollStatus, S.ready ? 5000 : 1500);
}

async function loadModels() {
  try { S.models = await api("/api/models"); } catch (e) { S.models = { online: false }; }
  if (S.detail) renderStyle();
  loadRequirements();
}

async function queue(body, what) {
  if (!S.current) return;
  const r = await api("/api/generate", { body });
  const skipped = (r.skipped || []).map(famShort);
  toast((r.added ? `${what} queued` : "Already queued") +
    (skipped.length ? `. Skipped ${skipped.join(", ")}: missing files (pick a workflow to see what it needs).` : ""));
}

/* ------------------------------------------------------------ wiring */

function setTab(t) {
  S.tab = t;
  setPref("tab", t);
  for (const b of $("#panelTabs").children) b.classList.toggle("on", b.dataset.tab === t);
  $("#tabStyle").hidden = t !== "style";
  $("#tabLayers").hidden = t !== "layers";
}

async function init() {
  S.styles = await api("/api/styles");
  loadModels();
  setTab(S.tab);
  $("#advanced").open = pref("advanced", "0") === "1";
  $("#advanced").addEventListener("toggle", () => setPref("advanced", $("#advanced").open ? "1" : "0"));
  resizeCanvas();
  window.addEventListener("resize", () => { resizeCanvas(); fitView(); });

  for (const b of $("#modeSeg").children) b.onclick = () => setMode(b.dataset.mode);
  for (const b of $("#panelTabs").children) b.onclick = () => setTab(b.dataset.tab);
  for (const b of $("#targetSeg").children) b.onclick = () => setTarget(b.dataset.target);
  $("#search").oninput = renderList;
  $("#selectShown").onclick = () => {
    for (const li of $("#fieldList").children) if (li.style.display !== "none") S.sel.add(li.dataset.name);
    selChanged();
  };
  $("#selFinal").onclick = () => queuePicked("final");
  $("#selPreview").onclick = () => queuePicked("preview");
  $("#selClear").onclick = clearPicks;
  $("#filter").onchange = renderList;
  $("#lookFilter").onchange = renderList;
  $("#optOverlay").onchange = (e) => { S.overlay = e.target.checked; loadImages(false); };
  $("#optAnimate").onchange = (e) => { S.animate = e.target.checked; S.strips = []; if (S.detail) renderLayers(); loadImages(false); };
  $("#optPixel").onchange = (e) => { S.pixel = e.target.checked; draw(); };
  $("#btnFit").onclick = fitView;
  $("#btn1x").onclick = () => setZoom(1);
  $("#btnNative").onclick = () => setZoom((S.imgs.new ? S.imgs.new.naturalWidth / S.detail.w : 4) / (window.devicePixelRatio || 1));
  $("#leftSel").onchange = (e) => { S.left = e.target.value; renderVersions(); loadImages(false); };
  $("#rightSel").onchange = (e) => { S.view = e.target.value; renderVersions(); loadImages(false); };

  // look bar
  $("#lookSel").onchange = async (e) => {
    const sel = e.target;
    if (sel.value === "__manage") {
      sel.value = S.detail.look.explicit ? S.detail.look.id : "";
      openLooks();
      return;
    }
    applyDetail(await api(`/api/field/${S.current}/look`, { body: { look: sel.value || null } }));
    loadLooks();
  };
  $("#btnSaveLook").onclick = async () => {
    const d = S.detail;
    if (!d) return;
    const name = prompt(`Name for a new look made from ${d.name}'s settings (its look "${d.look.name}"` +
      (d.custom ? " plus its field changes" : "") + `).\n\n${d.name} will use it; assign it to more fields in Looks.`, `${d.name} look`);
    if (!name) return;
    await api("/api/looks", { body: { name, from_field: d.name, assign: true } });
    S.target = "look";
    setPref("target", "look");
    await afterLooksChange();
    toast(`Look "${name}" created; ${d.name} uses it.`);
  };
  $("#btnResetField").onclick = async () => {
    const d = S.detail;
    if (!confirm(`Drop ${d.name}'s own changes (${d.override_keys.map(keyLabel).join(", ")}) and follow look "${d.look.name}" again?`)) return;
    applyDetail(await api(`/api/field/${d.name}/reset`, { body: {} }));
  };

  let promptTimer = null;
  $("#prompt").oninput = (e) => {
    clearTimeout(promptTimer);
    promptTimer = setTimeout(() => saveSetting({ prompt: e.target.value }), 700);
  };
  $("#styleUpload").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const r = await (await fetch("/api/styles", { method: "POST", body: fd })).json();
    S.styles = await api("/api/styles");
    await saveSetting({ style_refs: [...values().style_refs, r.name] });
    e.target.value = "";
  };

  // generate
  $("#btnGenerate").onclick = () => queue({ names: [S.current], mode: "final" }, "Final");
  $("#btnPreview").onclick = () => queue({ names: [S.current], mode: "preview" }, "Preview");
  $("#btnTryAll").onclick = () => queue({ names: [S.current], mode: "preview", families: Object.keys(FAMILIES) },
    "A preview with each workflow");
  $("#btnCancel").onclick = () => api("/api/queue/cancel", { body: {} });
  $("#btnCancel2").onclick = () => api("/api/queue/cancel", { body: {} });
  $("#queueSummary").onclick = () => { const p = $("#queuePop"); p.hidden = !p.hidden; if (!p.hidden) renderQueuePop(); };
  document.addEventListener("mousedown", (e) => {
    const p = $("#queuePop");
    if (!p.hidden && !p.contains(e.target) && e.target !== $("#queueSummary")) p.hidden = true;
  });

  // versions
  $("#btnUse").onclick = async () => {
    const v = viewedVersion();
    const r = await api(`/api/versions/${S.current}/${v.id}/use`, { body: {} });
    applyDetail(r.field);
    loadFields();
    if (r.queued) toast(r.switched ? "In game now; its doors, lights and frames are being restyled in the queue." : "Cutting it into game textures in the queue; it goes in game when done.");
  };
  $("#btnUseWorkflow").onclick = () => saveSetting({ family: viewedVersion().family });
  $("#btnRebuild").onclick = () => queue({ names: [S.current], mode: "build", version: viewedVersion().id }, "Rebuild");
  $("#btnRedoStates").onclick = () => queue({ names: [S.current], mode: "build", version: viewedVersion().id, states: true }, "Redo states");
  $("#btnReview").onclick = () => window.open(`/api/review/${S.current}.jpg?ver=${viewedVersion().id}&t=${Date.now()}`, "_blank");
  $("#btnDeleteVer").onclick = async () => {
    const v = viewedVersion();
    if (!confirm(`Delete the ${verName(v)} version of ${S.current}?\n\nIts image, textures and review sheet are removed.`)) return;
    try {
      const r = await api(`/api/versions/${S.current}/${v.id}`, { method: "DELETE" });
      if (S.view === v.id) S.view = null;
      if (S.left === v.id) S.left = "orig";
      applyDetail(r.field, true);
      loadFields();
    } catch (e) { toast(e.message); }
  };

  // looks
  $("#btnLooks").onclick = openLooks;
  $("#btnCloseLooks").onclick = () => { $("#looksModal").hidden = true; };
  $("#looksModal").onclick = (e) => { if (e.target === $("#looksModal")) $("#looksModal").hidden = true; };
  $("#btnNewLook").onclick = async () => {
    const name = prompt("Name for the new look (starts as a copy of the default look):", "New look");
    if (!name) return;
    await api("/api/looks", { body: { name, from_look: S.looks.default } });
    afterLooksChange();
  };
  $("#btnAddRule").onclick = () => {
    const L = S.looks;
    S.rules.push({ pattern: "", look: (L.looks.find((l) => l.id !== L.default) || L.looks[0]).id });
    renderRules();
    rulesDirty();
    const inputs = $("#rulesTable").querySelectorAll('input[type="text"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
  };
  $("#btnSaveRules").onclick = async () => {
    S.looks = await api("/api/assignments", { body: { rows: S.rules } });
    S.rules = S.looks.assignments.map((r) => ({ ...r }));
    S.rulesDirty = false;
    await afterLooksChange();
    toast("Rules saved");
  };

  $("#btnQueueMissing").onclick = async () => {
    const e = await api("/api/estimate", { body: { scope: "missing", mode: "final" } });
    if (!e.count) { toast("Every field already has a final-quality version."); return; }
    const looks = Object.entries(e.by_look).sort((a, b) => b[1] - a[1]).map(([id, n]) => `  ${lookName(id)}: ${n}`).join("\n");
    if (!confirm(`Queue ${e.count} fields for final-quality generation, each with its own look?\n\n${looks}\n\n` +
      `Estimated time: about ${fmtDuration(e.seconds)}. You can keep browsing while it runs; Cancel queue stops it.`)) return;
    const r = await api("/api/generate", { body: { scope: "missing" } });
    toast(`${r.added} fields queued`);
  };
  $("#btnInstall").onclick = async () => {
    if (!confirm(`Copy every field's current version into the game's FFNx folder?\n${$("#btnInstall").title.replace("Copy the current versions' textures into ", "")}\n\nThey are only used when the game runs through FFNx.`)) return;
    const r = await api("/api/install", { body: {} });
    alert(r.message);
  };

  pollStatus();
  pollQueue();
}

init();
