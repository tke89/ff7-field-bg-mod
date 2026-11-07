/* FF7 Field Studio plug-in: the 3D tab, "Build 3D" for a selection, and 3D marks in the field list.
   Server side: studio_plugin.py (routes under /api/3d). Uses the Studio's own helpers ($, api, esc, toast, S, setTab,
   fmtWhen, helpText, JOB_LABELS). */
(() => {
  const T = { state: null, built: {} };
  const VIEWS = { "3d": "3D scene", ai2d: "AI images (2D)", original: "Original" };

  JOB_LABELS["3d"] = () => "3D scene";
  JOB_LABELS["3dlook"] = () => "3D look";

  const css = document.createElement("style");
  css.textContent = `
    .b3d { display: inline-block; font-size: 10px; padding: 0 4px; border-radius: 3px; margin-left: 4px;
           background: #1f3a2c; color: var(--ok); vertical-align: 1px; }
    .b3d.stale { background: #3a3322; color: var(--warn); }
    .t3-line { margin: 4px 0; }
    .t3-stats { color: var(--muted); margin: 6px 0; line-height: 1.5; }
    .t3-bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; margin: 6px 0; }
    .t3-bar i { display: block; height: 100%; background: var(--accent); }
    .t3-shot { width: 100%; border-radius: 6px; margin-top: 8px; display: block; background: #000; }
    .t3-card .seg { margin: 6px 0; }
    .t3-card select { width: 100%; }
    .t3-group { border-top: 1px solid var(--line); margin: 0 -12px; padding: 0 12px; }
    .t3-group > summary { cursor: pointer; padding: 9px 0; list-style: none; display: flex; align-items: center; gap: 8px;
                          font-weight: 600; }
    .t3-group > summary::-webkit-details-marker { display: none; }
    .t3-group > summary::before { content: "▸"; color: var(--muted); width: 10px; }
    .t3-group[open] > summary::before { content: "▾"; }
    .t3-group .t3-changed { font-weight: 400; font-size: 11px; color: var(--field); }
    .t3-gbody { padding-bottom: 10px; }
    .gamebar { display: flex; align-items: center; gap: 6px; padding: 3px 8px; border: 1px solid var(--line);
               border-radius: 8px; background: #14171c; white-space: nowrap; }
    .gamebar .g-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--faint); flex: none; }
    .gamebar .g-dot.on { background: var(--ok); }
    .gamebar .g-dot.wait { background: var(--warn); animation: pulse 1s infinite alternate; }
    .gamebar .g-label { color: var(--muted); }
    .gamebar .g-status { max-width: 150px; overflow: hidden; text-overflow: ellipsis; }
    .gamebar .g-field { color: var(--accent); cursor: pointer; text-decoration: underline; }
    .gamebar .g-follow { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 3px; }
    .gamebar .seg button { padding: 2px 8px; }
    .t3-skip { margin-top: 6px; font-size: 12px; }`;
  document.head.appendChild(css);

  // the tab
  const tabBtn = document.createElement("button");
  tabBtn.dataset.tab = "3d";
  tabBtn.textContent = "3D";
  tabBtn.onclick = () => { setTab("3d"); refresh(); };
  $("#panelTabs").appendChild(tabBtn);
  const tab = document.createElement("div");
  tab.className = "tab";
  tab.dataset.tab = "3d";
  tab.hidden = true;
  tab.innerHTML = ["t3Scene", "t3Options"].map((id) => `<section class="card t3-card" id="${id}"></section>`).join("");
  $(".panel-body").appendChild(tab);
  if (S.tab === "3d") setTab("3d");  // the saved tab, now that it exists

  // a batch: "Build 3D" next to Generate final / Preview
  const selBtn = document.createElement("button");
  selBtn.className = "small";
  selBtn.textContent = "Build 3D";
  selBtn.title = "Build the 3D scene of every selected field from the version it has in the game";
  selBtn.onclick = async () => {
    const r = await api("/api/3d/build", { body: { names: [...S.sel] } });
    toast(r.added ? `${r.added} 3D scene${r.added === 1 ? "" : "s"} queued` : "Already queued");
  };
  $("#selBar .row-btns").insertBefore(selBtn, $("#selClear"));

  // ...and "Skip the 3D scenes" for the batch's renders (remembered; hidden while the 3D setting is off anyway)
  const SKIP_KEY = "ff3d.batchSkip3d";
  const skipRow = document.createElement("label");
  skipRow.className = "check t3-skip";
  skipRow.title = "Ticked: Generate final and Preview of the selected fields (and Generate all missing) only make the " +
    "2D images. No 3D scene is built or updated, so the batch finishes sooner. Fields that already have a 3D scene " +
    "keep their old one, marked out of date, until you use Build 3D.\nUnticked: the 3D scenes setting in Settings " +
    "decides, as usual.\nRecommended: unticked, unless you're trying out looks and will build the 3D scenes later.";
  skipRow.innerHTML = `<input type="checkbox"> Skip the 3D scenes`;
  const skipBox = skipRow.querySelector("input");
  try { skipBox.checked = localStorage.getItem(SKIP_KEY) === "1"; } catch (e) { /* no storage */ }
  skipBox.onchange = () => { try { localStorage.setItem(SKIP_KEY, skipBox.checked ? "1" : "0"); } catch (e) { /* no storage */ } };
  if (typeof BATCH_OPTIONS !== "undefined") {  // a Studio that sends batch options
    BATCH_OPTIONS.skip_3d = () => skipBox.checked;
    $("#selBar").appendChild(skipRow);
  }
  const showSkip = (auto) => { skipRow.hidden = auto === "off"; };

  // field list filters (a Studio without the LIST_FILTERS hook just doesn't get them)
  const FILTERS = [
    ["3d", "With a 3D scene", (f) => f.name in T.built],
    ["3d_stale", "3D scene out of date", (f) => T.built[f.name] === false],
    ["3d_none", "Without a 3D scene", (f) => !(f.name in T.built)],
  ];
  if (typeof LIST_FILTERS !== "undefined") {
    const group = document.createElement("optgroup");
    group.label = "3D";
    for (const [value, label, shown] of FILTERS) {
      group.appendChild(new Option(label, value));
      LIST_FILTERS[value] = shown;
    }
    $("#filter").appendChild(group);
  }
  const filtering3d = () => FILTERS.some(([value]) => value === $("#filter").value);

  /* ---------------------------------------------------------- data */

  async function refresh() {
    const name = S.current;
    if (!name || S.tab !== "3d") return;
    try {
      const st = await api(`/api/3d/field/${name}`);
      if (name === S.current) { T.state = st; render(); }
    } catch (e) { /* server restarting */ }
  }

  async function refreshBuilt() {
    try {
      T.built = await api("/api/3d/built");
      if (filtering3d()) renderList();  // the list follows builds that finish while it's filtered
      else markList();
    } catch (e) { /* ignore */ }
  }

  // this field's 3D job: running ({stage, progress}) or queued ({queued: true}), else null
  function job3d() {
    const q = S.queue;
    if (!q) return null;
    if (q.current && q.current.name === S.current && String(q.current.kind).startsWith("3d")) return q.current;
    return q.queued.some((j) => j.name === S.current && String(j.kind).startsWith("3d")) ? { queued: true } : null;
  }

  // a card's HTML is only replaced when it changes (an open menu stays open)
  function fill(id, html, wire) {
    const el = $(`#${id}`);
    if (el.dataset.html === html) return;
    el.innerHTML = html;
    el.dataset.html = html;
    wire(el);
  }

  const mb = (b) => `${(b / 2 ** 20).toFixed(b < 10 * 2 ** 20 ? 1 : 0)} MB`;
  const tris = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : `${Math.round(n / 1e3)}k`);

  /* ---------------------------------------------------------- cards */

  function render() {
    const st = T.state;
    if (!st || st.name !== S.current) return;
    renderScene(st);
    renderOptions(st);
  }

  function renderScene(st) {
    const j = job3d();
    const hasRender = S.detail && S.detail.versions.length > 0;
    let status;
    if (j && j.queued) status = `<div class="t3-line">Queued.</div>`;
    else if (j) status = `<div class="t3-line">Building: ${esc(j.stage)}</div><div class="t3-bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div>`;
    else if (st.error) status = `<div class="t3-line bad">The last build failed: ${esc(st.error)}</div>`;
    else if (!st.built) status = `<div class="t3-line">${hasRender ? "No 3D scene yet." : "No 3D scene yet. Generate a render first: the scene is built from it."}</div>`;
    else if (st.up_to_date) status = `<div class="t3-line"><span class="ok">Up to date</span>: built from the version in the game.</div>`;
    else status = `<div class="t3-line"><span class="warn">Out of date</span>: ` +
      (st.current ? "the game now uses a different version of this field." : "built before this field had a version in the game.") + `</div>`;
    let stats = "";
    const r = st.report;
    if (st.built && r) {
      const fewer = r.full_triangles && r.triangles ? Math.round(r.full_triangles / r.triangles) : 0;
      stats = `<div class="t3-stats">${tris(r.triangles)} triangles${fewer > 1 ? ` (${fewer}× fewer than full detail)` : ""}` +
        (r.bytes ? ` · ${mb(r.bytes)}` : "") +
        (r.fit != null ? `<br>Depth matches where the game lets you walk to within ${(r.fit * 100).toFixed(1)}%` : "") +
        (r.built ? `<br>Built ${fmtWhen(r.built, true)}${r.seconds ? ` in ${Math.round(r.seconds)} s` : ""}` : "") + `</div>`;
    }
    const label = st.built ? "Rebuild 3D scene" : "Build 3D scene";
    const shot = st.shot ? `<img class="t3-shot" src="/api/3d/shot/${st.name}.jpg?t=${st.shot}" alt="">` +
      `<div class="hint">Screenshot from the game (📷 in the game bar), ${fmtWhen(st.shot, true)}</div>` : "";
    const html = `<h2>3D scene</h2>${status}${stats}` +
      `<div class="row-btns tight"><button class="primary small" data-a="build" ${hasRender && !j ? "" : "disabled"}>${label}</button></div>` +
      `<div class="hint">Turns the version the game uses (★) into a 3D scene for FFNx-3d. It goes in the queue and takes about half a minute.</div>${shot}`;
    fill("t3Scene", html, (el) => {
      el.querySelector('[data-a="build"]').onclick = async () => {
        const r2 = await api("/api/3d/build", { body: { names: [st.name] } });
        toast(r2.added ? "3D scene queued" : "Already queued");
        refresh();
      };
    });
  }

  // the look's light and colour (server: build.LOOK_LIGHT and its defaults per look)
  const pctFmt = (x) => `${Math.round(x * 100)}%`;
  const timesFmt = (x) => `${x.toFixed(2)}×`;
  const LIGHT = {
    shine: { label: "Shine", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "How much surfaces reflect light and the sky. Water keeps its own shine.",
      up: "glossier; on dark scenes the reflections grey everything out", down: "matte, like the painting",
      rec: { painted: "0% (the painting has its own highlights)", relit: "35%" } },
    light_tint: { label: "Light colour from the scene", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Whether the light shaping the scene is white or takes on the scene's own colours.",
      up: "the light takes the scene's colours: a green scene gets greener and its other colours fade",
      down: "white light: the painting's colours stay as painted",
      rec: { painted: "0% (the painting has its light's colour already)", relit: "50%" } },
    shading: { label: "3D shading", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "How strongly the sun shapes the scene. The overall brightness stays about the same.",
      up: "brighter sunlit sides, darker shade, stronger shadows", down: "flatter and closer to the painting; shadows fade",
      rec: { painted: "50%", relit: "50%" } },
    brightness: { label: "Brightness", min: 0.3, max: 2, step: 0.05, fmt: timesFmt,
      what: "All of the scene's light together.", up: "brighter", down: "darker",
      rec: { painted: "1× (the painting's own brightness)", relit: "1×" } },
    saturation: { label: "Colour strength", min: 0.5, max: 1.6, step: 0.05, fmt: timesFmt,
      what: "How colourful the scene's texture is.", up: "more vivid", down: "more muted",
      rec: { painted: "1× (the painting's own colours)", relit: "1×" } },
    sun_turn: { label: "Sun: turn around", min: -180, max: 180, step: 10, fmt: (x) => `${x > 0 ? "+" : ""}${Math.round(x)}°`,
      relitOnly: true,
      what: "Moves the sun around the scene from where the painter put it. Sunlit sides darken, shaded ones light up, " +
        "and shadows fall the new way, the characters' too. Outdoor scenes only: indoors there's no sun to move.",
      up: "the sun swings one way round", down: "the other way round; at 0° it's the painting's own light",
      rec: { painted: "0°", relit: "0° (the painting's light); small turns look most natural" } },
    sun_height: { label: "Sun: higher or lower", min: -60, max: 60, step: 5, fmt: (x) => `${x > 0 ? "+" : ""}${Math.round(x)}°`,
      relitOnly: true,
      what: "Raises or lowers the sun from where the painter put it.",
      up: "a higher sun: shorter shadows, tops of things brighter", down: "a lower sun: longer shadows, like late afternoon",
      rec: { painted: "0°", relit: "0° (the painting's light)" } },
  };

  // around the characters (server: EFFECTS in studio_plugin.py, ff3d/atmos.py)
  const EFFECTS = {
    char_shadow: { label: "Character shadows", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "How dark the shadows of Cloud, the party and everyone else are on the ground they walk on. They're what keeps them standing on it.",
      up: "darker, more solid shadows", down: "fainter; at 0% none, and characters look pasted on",
      rec: "50%" },
    char_haze: { label: "Haze on characters", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Characters further away take on the painting's haze, as the scene around them does: at most a fifth, at the far end of where they can walk.",
      up: "distant characters fade more into the scene", down: "everyone stays as crisp as up close",
      rec: "100%" },
    char_match: { label: "Character colour match", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "FF7's characters were coloured to fit the original backgrounds. This changes their colours the way the new " +
        "art changed the background: brighter or darker, warmer or cooler, more or less colourful.",
      up: "characters follow the new art's colours more closely", down: "they keep the game's own colours; at 0% unchanged",
      rec: "75%" },
    char_light: { label: "Character light from the scene", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "The soft light around the characters takes on the painting's colours (sky above, ground below), so a warm room " +
        "warms them and a green reactor tints them. The scene itself keeps its own light.",
      up: "stronger tint from the scene's colours", down: "more neutral, white light; at 0% the same as the scene's",
      rec: "60%" },
    char_local: { label: "Light where they stand", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Characters get brighter or darker with the light on the floor where they are in the painting: brighter " +
        "under a lamp, darker in a shadowy corner.",
      up: "stronger changes as they walk", down: "evener; at 0% the same everywhere in the field", rec: "60%" },
    char_soft: { label: "Softer characters", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "The characters' textures are a little blurred, so they look less like sharp 3D models against the painted scene.",
      up: "softer, more painterly", down: "crisper; at 0% as the models are", rec: "35%" },
    char_outline: { label: "Character outlines", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "A dark outline around the characters, in the colour of the painting's own line work. For art styles with " +
        "outlines (anime, comic); leave it off for painterly or realistic ones.",
      up: "thicker outlines", down: "thinner; at 0% none", rec: "0% for painterly looks, about 50% for outlined ones" },
    contact_shadow: { label: "Shadow right under them", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "A soft dark patch on the floor right under each character, wherever the sun is, so their feet look " +
        "planted. It's part of their shadow, so it goes when Character shadows is at 0%.",
      up: "darker", down: "fainter; at 0% none", rec: "50%" },
    char_fill: { label: "Character brightness", min: 0, max: 2, step: 0.05, fmt: pctFmt,
      what: "How much light the characters get from all around them, whatever the scene's sun does. With the sun " +
        "taking most of the light (Shading high), characters would be dark wherever it doesn't hit them. The scene " +
        "itself isn't changed.",
      up: "brighter characters that stand out more", down: "darker; at 0% only the scene's own light, as before",
      rec: "100%" },
    char_finish: { label: "Character finish", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "How shiny the characters' skin, hair and clothes look under the scene's lights. Matte suits the painted " +
        "scenes around them; full is FFNx's usual sheen, which makes everyone look like painted plastic or metal.",
      up: "glossier, more like plastic or metal", down: "more matte, like cloth and skin", rec: "20%" },
    char_lamps: { label: "Lit by nearby lights", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Characters pick up the colour and light of the fires, lamps, lit windows, screens and glowing tanks they " +
        "walk past: orange by a furnace, cyan beside a screen. The lights are placed where the painting has them.",
      up: "stronger, more colourful light on the characters", down: "subtler; at 0% none", rec: "50%" },
    reflections: { label: "Reflections", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Characters are mirrored in the scene's water, and faintly in its floors when they're polished (marble, " +
        "shiny metal). Only scenes with water or a polished floor get them.",
      up: "clearer reflections", down: "fainter; at 0% none", rec: "50%" },
    glow: { label: "Light glow", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "The lights the original game marked as glowing (signs, screens, lamps, light shafts) glow softly, over " +
        "characters standing in front of them too. Fields without such lights (most daylight scenes) get none.",
      up: "bigger, brighter glow", down: "subtler; at 0% none", rec: "50%" },
  };

  // life in the scene (server: EFFECTS and PARTICLES in studio_plugin.py; ff3d/atmos.py particles)
  const LIFE = {
    particle_amount: { label: "How many particles", min: 0, max: 2, step: 0.1, fmt: pctFmt,
      what: "How many of the particles chosen above float or fall through the scene.",
      up: "more, busier", down: "fewer; at 0% none", rec: "100%" },
    water_motion: { label: "Moving water", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Water in the painting ripples gently, so rivers, pools and waterfalls aren't frozen. Only what the build " +
        "recognised as water moves.",
      up: "stronger ripples", down: "calmer; at 0% still", rec: "50%" },
    grass: { label: "Grass in the wind", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Grass and leaves in the painting sway gently, with gusts rolling across them. Only green, textured " +
        "areas move; flowers have their own sway option.",
      up: "stronger wind", down: "calmer; at 0% still", rec: "50% for grassy and leafy scenes, 0% elsewhere" },
    clouds: { label: "Cloud shadows", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Soft cloud shadows drift across the ground, the buildings and the characters walking through them. For " +
        "sunny outdoor scenes and towns.",
      up: "darker shadows under the clouds", down: "fainter; at 0% none", rec: "50% outdoors in sunshine, 0% indoors and at night" },
    flicker: { label: "Firelight flicker", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Warm lights (fires, torches, candles, lanterns, lit windows) flicker, each on its own. Electric lights " +
        "stay steady. Works on the lights Light glow finds and on Lit windows.",
      up: "livelier flames", down: "calmer; at 0% steady", rec: "100% where there's fire, 25% for candlelit windows" },
    // found in the painting by the AI when the scene is built (ff3d/life.py)
    smoke: { label: "Chimney smoke", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Smoke rises from the chimneys and furnace stacks found in the painting and drifts off with the wind. " +
        "Scenes without chimneys get none.",
      up: "thicker smoke", down: "thinner; at 0% none", rec: "50%" },
    fire: { label: "Sparks over fires", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Sparks fly up from the fires, furnaces, braziers and torches found in the painting, and the air just " +
        "above them shimmers with heat. Lanterns and candles only glow.",
      up: "more sparks, stronger shimmer", down: "fewer; at 0% none", rec: "50%" },
    windows: { label: "Lit windows", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Windows found in the painting glow with candlelight from inside, each flickering on its own (with " +
        "Firelight flicker), and their warm light falls on the ground in front of them. Works whatever Light glow is " +
        "set to.",
      up: "brighter windows, more light on the ground", down: "fainter; at 0% none",
      rec: "50% for evening and night scenes, 0% in daylight" },
    sky: { label: "Moving sky", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "The open sky in the painting slowly drifts, so clouds and sunsets aren't frozen. Roofs, towers and " +
        "trees against it stay still.",
      up: "faster drift", down: "slower; at 0% still", rec: "50% outdoors" },
    flags: { label: "Flags in the wind", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Flags, banners and pennants found in the painting wave gently in the wind.",
      up: "stronger waving", down: "calmer; at 0% still", rec: "50%" },
    blink: { label: "Screens and indicator lights", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Screens, monitors and lit signs shimmer with a scan line rolling down them, and the small indicator " +
        "lights on consoles and doors blink, each on its own rhythm. Only scenes with screens (offices, labs, reactors).",
      up: "brighter screens and lights", down: "subtler; at 0% still", rec: "50%" },
    motes: { label: "Dust in light shafts", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Specks of dust drift and glint inside the scene's beams and shafts of light. Only scenes with light shafts.",
      up: "more dust", down: "less; at 0% none", rec: "50%" },
    mist: { label: "Drifting mist", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Mist and fog in the painting slowly roll along, in front of what's behind them. Only scenes with mist.",
      up: "thicker, busier mist", down: "fainter; at 0% none", rec: "50%" },
    bubbles: { label: "Bubbles in tanks", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Bubbles rise through tanks and tubes of liquid (specimen tanks, mako tanks). Only scenes with tanks.",
      up: "more bubbles", down: "fewer; at 0% none", rec: "50%" },
    birds: { label: "Birds", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Now and then a small flock of birds flies across the open sky, far off above the scenery. Only in scenes " +
        "with sky.",
      up: "more often", down: "rarer; at 0% none", rec: "30%" },
  };
  // the scene's surfaces for the game's light (server: EFFECTS in studio_plugin.py; ff3d/surface.py)
  const SURFACE = {
    occlusion: { label: "Shade in corners", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Corners, the floor under furniture and the gaps between things get a little less of the surrounding " +
        "light, so objects sit more firmly in the room. Paintings often have some of this already.",
      up: "deeper shade in corners and under things", down: "as painted; at 0% none added", rec: "50% indoors, 0% outdoors" },
    gloss: { label: "Shine", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Surfaces where the painting shows highlights (polished wood, bottles, metal, wet floors) reflect the " +
        "scene's light and surroundings.",
      up: "glossier, wetter-looking", down: "more matte; at 0% only water shines", rec: "50% for industrial and wet scenes, 0% elsewhere" },
    metal: { label: "Metal", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Of the shiny surfaces, the grey ones reflect like metal. Needs some Shine.",
      up: "more like bare metal", down: "like painted surfaces; at 0% none", rec: "0%, or 50% in machinery-heavy scenes" },
    bump: { label: "Surface relief", min: 0, max: 1, step: 0.05, fmt: pctFmt,
      what: "Fine bumps from the painting's own texture (wood grain, cobbles, rock) for the game's light to catch. " +
        "Subtle in the Painted look, where the texture's shading is already painted in; more visible in Relit.",
      up: "rougher-looking surfaces", down: "smoother; at 0% none", rec: "0% Painted, about 50% Relit" },
  };
  const PARTICLE_KINDS = { auto: "Automatic", none: "None", dust: "Dust in the air", embers: "Embers rising",
    fireflies: "Fireflies", snow: "Snow falling", blizzard: "Blizzard", rain: "Rain" };

  // what the AI found in this scene for the options below (life.json from the last build or look switch)
  function foundLine(life) {
    if (!life) return `<div class="ctl-help t3-found">Chimneys, fires, windows, flags and sky are looked for the next ` +
      `time the scene is built or its look is applied.</div>`;
    const n = (k, one, many) => life[k] ? `${life[k]} ${life[k] === 1 ? one : many}` : "";
    const parts = [n("chimneys", "chimney", "chimneys"), n("fires", "fire or lantern", "fires and lanterns"),
      n("windows", "lit window", "lit windows"), n("flags", "flag", "flags"), life.sky ? "open sky" : "",
      n("screens", "screen", "screens"), n("leds", "indicator light", "indicator lights"), n("tanks", "tank", "tanks"),
      life.mist ? "mist" : "", life.beams ? "light shafts" : "", n("lamps", "light for the characters", "lights for the characters"),
      life.reflections && life.reflections[0] > 0 ? "water to reflect in" : "",
      life.reflections && life.reflections[1] > 0 ? "a polished floor" : ""].filter(Boolean);
    return `<div class="ctl-help t3-found">Found in this scene: ${parts.length ? parts.join(", ") : "none of these"}.</div>`;
  }

  function lightSlider(key, o, defs = LIGHT, mark = "") {
    const d = defs[key];
    const v = Number(o[key]);
    return `<div class="slider ${mark}" data-k="${key}" ${mark === "overridden" ? `title="${OWN_TIP}"` : ""}>` +
      `<div class="top"><span>${d.label}</span><b>${d.fmt(v)}</b></div>` +
      `<input type="range" min="${d.min}" max="${d.max}" step="${d.step}" value="${v}">` +
      `<div class="help"><div>${d.what}</div><div class="help-range"><span><b>Higher:</b> ${d.up}.</span>` +
      `<span><b>Lower:</b> ${d.down}.</span></div><div class="help-rec">Recommended: ${typeof d.rec === "string" ? d.rec : d.rec[o.look]}.</div></div></div>`;
  }

  // the options in groups that open and close (remembered); a group says how many settings are changed in it
  // the characters' settings that are the same in every field (server: build.GAMEWIDE): set under All fields only,
  // they show in the game at once (FFNx-3d reads them from the lighting config's [fields])
  const GAMEWIDE = ["char_shadow", "char_haze", "char_match", "char_local", "char_outline", "char_soft",
    "contact_shadow", "char_finish", "char_fill"];
  const PER_FIELD_EFFECTS = Object.keys(EFFECTS).filter((k) => !GAMEWIDE.includes(k));
  const GROUPS = [
    ["light", "Lighting"], ["game", "The characters, in every field"], ["chars", "Around the characters"],
    ["life", "Life in the scene"], ["surf", "Surfaces"], ["shape", "Shape (changing these rebuilds the scene)"],
  ];
  const GROUP_KEYS = {
    light: ["look", ...Object.keys(LIGHT)], game: GAMEWIDE, chars: ["sun", ...PER_FIELD_EFFECTS],
    life: ["particles", ...Object.keys(LIFE)], surf: Object.keys(SURFACE), shape: ["sway", "reveal"],
  };
  const groupOpen = (id) => pref(`t3open.${id}`, id === "light" ? "1" : "0") === "1";
  const OWN_TIP = "This field changes this setting for itself; switch to This field only to see or edit its value";
  // what the options edit: the settings every field follows, or this field's own changes on top of them
  const scope = () => pref("t3scope", "all");

  function renderOptions(st) {
    const all = scope() === "all";
    const o = all ? st.shared : st.options, ld = all ? st.shared_light_defaults : st.light_defaults,
      ed = st.effect_defaults;
    const own = new Set(st.own_keys), shared = new Set(st.shared_keys);
    // • changed (all fields: from the built-in defaults; this field: from all fields), ◆ this field has its own
    const mk = (k) => (all ? (own.has(k) ? "overridden" : shared.has(k) ? "changed" : "") : own.has(k) ? "changed" : "");
    const changed = Object.fromEntries(Object.entries(GROUP_KEYS).map(([id, keys]) =>
      [id, keys.filter((k) => (all ? shared : own).has(k)).length]));
    const n = st.own_keys.length;
    const scopeBar = `<div class="seg small" data-a="scope">` +
      `<button data-v="all" class="${all ? "on" : ""}">All fields</button>` +
      `<button data-v="field" class="${all ? "" : "on"}">This field only${n ? ` (${n})` : ""}</button></div>` +
      `<div class="scope-note ${all ? "" : "field"}">` + (all
        ? `Changes apply to every 3D scene. Up-to-date scenes show them in the game in a few seconds; the others when ` +
          `they're rebuilt. • = changed from the built-in defaults.` +
          (n ? `<br><span style="color:var(--field)">◆ ${esc(st.name)} changes ${n} setting${n > 1 ? "s" : ""} for itself.</span>` : "")
        : n ? `Changes apply to ${esc(st.name)} only. • = ${n} setting${n > 1 ? "s" : ""} different from all fields.`
          : `Changes apply to ${esc(st.name)} only, on top of the settings for all fields.`) + `</div>`;
    const body = {
      light: `<div class="lbl ${mk("look")}">Lighting style</div><div class="seg small" data-a="look">` +
        `<button data-v="painted" class="${o.look === "painted" ? "on" : ""}">Painted</button>` +
        `<button data-v="relit" class="${o.look === "relit" ? "on" : ""}">Relit</button></div>` +
        `<div class="ctl-help">${helpText("Painted keeps the painting's own colours and light, with soft 3D shading and real shadows on top. " +
          "Relit keeps the painting but lets the game's light change it: move the sun and the scene's light and " +
          "shadows follow, and lamps flicker on the walls. With the sun where the painter put it, it looks like the painting.", "Painted")}</div>` +
        Object.keys(LIGHT).filter((k) => !LIGHT[k].relitOnly || o.look === "relit").map((k) => lightSlider(k, o, LIGHT, mk(k))).join(""),
      chars: `<div class="lbl ${mk("sun")}">Sun direction</div><div class="seg small" data-a="sun">` +
        `<button data-v="painting" class="${o.sun === "painting" ? "on" : ""}">From the painting</button>` +
        `<button data-v="default" class="${o.sun === "default" ? "on" : ""}">FFNx's default</button></div>` +
        `<div class="ctl-help">${helpText("Where the sun is, so characters' shadows fall the way the painted shadows do. From the painting " +
          "works it out from the painting's shading; where that's unclear (most interiors) the sun is high, so shadows stay " +
          "short under the characters. FFNx's default puts it in the same place for every field.", "From the painting")}</div>` +
        PER_FIELD_EFFECTS.map((k) => lightSlider(k, o, EFFECTS, mk(k))).join(""),
      game: all
        ? `<div class="ctl-help">${helpText("These are the same in every field and show in the game straight away; no scene " +
            "is rebuilt or re-applied.", "")}</div>` +
          // a setting the server doesn't know yet (added since the Studio started) shows after its restart
          GAMEWIDE.filter((k) => o[k] !== undefined).map((k) => lightSlider(k, o, EFFECTS, mk(k))).join("")
        : `<div class="hint">These are the same in every field: switch to All fields to change them.</div>`,
      life: `<div class="ctl"><div class="lbl ${mk("particles")}">Particles</div><select data-a="particles">` +
        Object.entries(PARTICLE_KINDS).map(([v, n]) => `<option value="${v}" ${o.particles === v ? "selected" : ""}>` +
          `${v === "auto" ? `Automatic: ${PARTICLE_KINDS[st.particles_auto].toLowerCase()}` : n}</option>`).join("") +
        `</select><div class="ctl-help">${helpText("Small things moving in the air: dust catching the light indoors, embers " +
          "over fires, fireflies in dark forests, falling snow or rain. Automatic picks from where the scene is in the game " +
          "or its description; most outdoor daytime scenes get none. On the Great Glacier, Blizzard replaces the grain of the game's " +
          "own flat snowstorm with snow blown past in 3D (its gusts of white stay); any other choice brings the original's back.", "Automatic")}</div></div>` +
        foundLine(st.life) + Object.keys(LIFE).map((k) => lightSlider(k, o, LIFE, mk(k))).join(""),
      surf: Object.keys(SURFACE).map((k) => lightSlider(k, o, SURFACE, mk(k))).join(""),
      shape: `<label class="check ctl ${mk("sway")}"><input type="checkbox" data-a="sway" ${o.sway ? "checked" : ""}><span>Flowers sway in the wind` +
        `<div class="ctl-help">${helpText("Flowers and plants the build finds move gently. It keeps them at full detail, so the scene gets bigger; " +
          "changing it rebuilds the scene.", "off, except in fields with flower beds")}</div></span></label>` +
        `<label class="check ctl ${mk("reveal")}"><input type="checkbox" data-a="reveal" ${o.reveal ? "checked" : ""}><span>Hide characters as the original game did` +
        `<div class="ctl-help">${helpText("Characters and items are hidden behind things exactly where the original game hid them, " +
          "using the depth Square gave every piece of the original backgrounds. Without it the 3D scene decides on its own, and " +
          "its depth is a little off in places: characters walk through objects, or vanish into openings the new art painted " +
          "closed. The original's rough spots come along (it lets characters show over a few low fences). The picture doesn't " +
          "change, only what hides what. Changing it rebuilds the scene.", "on")}</div></span></label>`,
    };
    const reset = all
      ? ["Back to defaults", "This group's settings back to the built-in defaults, for all fields"]
      : ["Same as all fields", "This field stops changing this group's settings for itself and follows all fields again"];
    const html = `<h2>3D options</h2>${scopeBar}` +
      GROUPS.map(([id, title]) => `<details class="t3-group" data-g="${id}" ${groupOpen(id) ? "open" : ""}>` +
        `<summary><span>${title}</span>${changed[id] ? `<span class="t3-changed" title="${all
          ? "Settings changed from the built-in defaults, for all fields" : "Settings this field changes for itself"}">` +
        `${changed[id]} changed</span>` : ""}</summary><div class="t3-gbody">${body[id]}` +
        `<div class="row-btns tight"><button class="small ghost" data-reset="${id}" ${changed[id] ? "" : "disabled"} ` +
        `title="${reset[1]}">${reset[0]}</button></div></div></details>`).join("") +
      (all ? `<div class="row-btns tight"><button class="small" data-a="rebuildAll">Rebuild every 3D scene…</button></div>` +
        `<div class="hint">Builds every field's 3D scene again with these settings, from the image each field has in the ` +
        `game. No images are made again.</div>` : "");
    const defaults = (defs) => Object.fromEntries(Object.keys(defs).map((k) => [k, ed[k]]));
    const RESET = all ? {
      light: { look: "painted", reset_light: true },
      game: Object.fromEntries(GAMEWIDE.map((k) => [k, ed[k]])),
      chars: { sun: "painting", ...Object.fromEntries(PER_FIELD_EFFECTS.map((k) => [k, ed[k]])) },
      life: { particles: "auto", ...defaults(LIFE) },
      surf: defaults(SURFACE),
      shape: { sway: false, reveal: true },
    } : Object.fromEntries(Object.entries(GROUP_KEYS).map(([id, keys]) => [id, { follow: keys }]));
    fill("t3Options", html, (el) => {
      for (const d of el.querySelectorAll("details.t3-group")) d.ontoggle = () => setPref(`t3open.${d.dataset.g}`, d.open ? "1" : "0");
      for (const b of el.querySelectorAll('[data-a="scope"] button')) b.onclick = () => { setPref("t3scope", b.dataset.v); render(); };
      const rb = el.querySelector('[data-a="rebuildAll"]');
      if (rb) rb.onclick = rebuildAll;
      for (const b of el.querySelectorAll("[data-reset]")) b.onclick = () => setOption(RESET[b.dataset.reset]);
      for (const b of el.querySelectorAll('[data-a="look"] button')) b.onclick = () => setOption({ look: b.dataset.v });
      for (const b of el.querySelectorAll('[data-a="sun"] button')) b.onclick = () => setOption({ sun: b.dataset.v });
      el.querySelector('[data-a="particles"]').onchange = (e) => setOption({ particles: e.target.value });
      for (const s of el.querySelectorAll(".slider[data-k]")) {
        const d = LIGHT[s.dataset.k] || EFFECTS[s.dataset.k] || LIFE[s.dataset.k] || SURFACE[s.dataset.k],
          input = s.querySelector("input");
        input.oninput = () => { s.querySelector("b").textContent = d.fmt(Number(input.value)); };
        input.onchange = () => setOption({ [s.dataset.k]: Number(input.value) });
      }
      el.querySelector('[data-a="sway"]').onchange = (e) => setOption({ sway: e.target.checked });
      el.querySelector('[data-a="reveal"]').onchange = (e) => setOption({ reveal: e.target.checked });
    });
  }

  async function setOption(body) {
    const all = scope() === "all";
    const r = await api(`/api/3d/options/${S.current}`, { body: { ...body, scope: all ? "all" : "field" } });
    if (all) {
      toast(r.queued ? `Saved for all fields: ${r.queued} up-to-date scene${r.queued === 1 ? "" : "s"} updating in the queue, ` +
        "the others when they're rebuilt" : "Saved for all fields: scenes take it when they're rebuilt");
    } else if (r.queued) {
      toast(body.sway !== undefined || body.reveal !== undefined || !r.field.up_to_date ? "Rebuilding the 3D scene in the queue"
        : body.look ? "Switching the lighting style in the queue (a few seconds)" : "Updating the scene in the queue (a few seconds)");
    }
    T.state = r.field;
    render();
  }

  // every 3D scene built again with the settings for all fields (from the images in the game: none made again)
  async function rebuildAll() {
    const a = await api("/api/3d/all");
    const own = Object.entries(a.own);
    const shapeOwn = own.filter(([, keys]) => keys.some((k) => GROUP_KEYS.shape.includes(k))).map(([n]) => n);
    const hours = a.seconds / 3600;
    const time = hours >= 1 ? `about ${hours.toFixed(hours < 10 ? 1 : 0)} hours` : `about ${Math.max(1, Math.round(a.seconds / 60))} minutes`;
    const checks = [];
    if (own.length) {
      checks.push({ key: "drop_own", checked: true,
        label: `Use exactly these settings everywhere: ${own.length} field${own.length === 1 ? "" : "s"} drop their own changes` });
      if (shapeOwn.length) {
        checks.push({ key: "keep_shape", checked: true,
          label: `…but keep their Shape settings (flower sway in ${shapeOwn.slice(0, 4).join(", ")}${shapeOwn.length > 4 ? "…" : ""})` });
      }
    }
    const msg = `Rebuild all ${a.scenes} 3D scenes with the settings for all fields? Each is built from the image its ` +
      `field has in the game; no images are made again. It takes ${time} in the queue (their last builds), using ComfyUI ` +
      `for depth and surfaces where a field's image changed since its scene was built (${a.stale} of them). Scenes stay ` +
      `playable meanwhile: each is swapped in when its build finishes.`;
    const r = await dialog(msg, { ok: `Rebuild ${a.scenes} scenes`, checks: checks.length ? checks : null });
    if (!r) return;
    const opts = r === true ? {} : r;
    const done = await api("/api/3d/rebuild_all", { body: { drop_own: !!opts.drop_own, keep_shape: !!opts.keep_shape } });
    toast(done.added ? `${done.added} 3D scene${done.added === 1 ? "" : "s"} queued` : "Already queued");
    refresh();
  }

  /* ---------------------------------------------------------- the game bar (in the header, on every tab) */

  const VIEW_BTNS = { "3d": ["3D", "The 3D scene"], ai2d: ["AI 2D", "The AI images, flat as in the 2D game"],
    original: ["Original", "The game as it was: original backgrounds, none of the 3D effects"] };
  const G = { game: null, lastField: null, lastView: "3d" };
  const bar = document.createElement("div");
  bar.className = "gamebar";
  const slot = $("#topbarSlot");
  if (slot) slot.appendChild(bar);
  else $(".topbar").insertBefore(bar, $(".topbar .spacer"));

  async function gameAct(body, msg) {
    try {
      const r = await api("/api/3d/game", { body });
      if (msg) toast(msg);
      G.game = r.game;
      renderBar();
      return true;
    } catch (e) { toast(e.message); return false; }
  }

  // Play this field: start the game there, or take the running game there (or re-enter it: pages and glow reload)
  function play() {
    const g = G.game, name = S.current;
    if (!g || !name) return;
    if (g.running) gameAct({ action: "show", name }, g.field === name ? "Re-entering this field in the game" : `The game goes to ${name}`);
    else if (!g.starting) gameAct({ action: "start", name }, `Starting the game; it opens straight into ${name}.`);
  }

  function setView(v) {
    if (G.game && G.game.running) gameAct({ action: "view", view: v });
  }

  // B: the game as it was, and back to the new art (whichever view that was)
  function beforeAfter() {
    const g = G.game;
    if (!g || !g.running) return;
    if (g.view === "original") setView(G.lastView || "3d");
    else { G.lastView = g.view || "3d"; setView("original"); }
  }

  function openGameField() {
    const f = G.game && G.game.field;
    if (!f || !S.byName || !S.byName[f]) return;
    if (f !== S.current) selectField(f);
    const li = document.querySelector(`#fieldList li[data-name="${CSS.escape(f)}"]`);
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  function renderBar() {
    const g = G.game || { running: false };
    const here = g.running && g.field === S.current;
    const follow = pref("followGame", "0") === "1";
    const status = g.running
      ? (g.field ? `in <a class="g-field" title="Open ${esc(g.field)} in the Studio">${esc(g.field)}</a>` : "in a menu")
      : g.starting ? "starting…" : "not running";
    const label = g.running ? (here ? "Re-enter field" : "Go to this field") : g.starting ? "Starting…" : "▶ Play this field";
    const tip = g.running ? (here ? "Re-enter this field in the game, where you stand: its textures and glow reload"
      : `Take the game to ${S.current || "the selected field"}`) : "Start the game, straight into the selected field";
    const views = Object.entries(VIEW_BTNS).map(([v, [l, t]]) =>
      `<button data-v="${v}" class="${g.running && g.view === v ? "on" : ""}" ${g.running ? "" : "disabled"} title="${t}">${l}</button>`).join("");
    const html = `<span class="g-dot ${g.running ? "on" : g.starting ? "wait" : ""}"></span>` +
      `<span class="g-label">Game</span><span class="g-status">${status}</span>` +
      `<button class="small primary" data-a="play" ${S.current && !(g.starting && !g.running) ? "" : "disabled"} title="${tip} (G)">${label}</button>` +
      `<div class="seg small" data-a="view" title="What the game shows. B flips between Original and the new art. ` +
      `Switching re-enters the field (it restarts a cutscene).">${views}</div>` +
      `<button class="small ghost" data-a="shot" ${g.running && g.field ? "" : "disabled"} ` +
      `title="Screenshot of the game, kept with its field (3D tab)">📷</button>` +
      `<label class="g-follow" title="Open whichever field the game goes into"><input type="checkbox" data-a="follow" ${follow ? "checked" : ""}>Follow</label>`;
    if (bar.dataset.html === html) return;
    bar.innerHTML = html;
    bar.dataset.html = html;
    const b = (a) => bar.querySelector(`[data-a="${a}"]`);
    b("play").onclick = play;
    for (const v of bar.querySelectorAll('[data-a="view"] button')) v.onclick = () => setView(v.dataset.v);
    b("shot").onclick = async () => {
      if (await gameAct({ action: "shot", name: G.game.field }, `Screenshot of ${G.game.field} saved: it's in the 3D tab`)) refresh();
    };
    b("follow").onchange = (e) => {
      setPref("followGame", e.target.checked ? "1" : "0");
      if (e.target.checked) openGameField();
      renderBar();
    };
    const link = bar.querySelector(".g-field");
    if (link) link.onclick = openGameField;
  }

  async function pollGame() {
    try {
      const g = await api("/api/3d/game");
      G.game = g;
      // Follow: when the game goes into another field, open it here (browsing other fields meanwhile is fine)
      if (g.running && g.field && g.field !== G.lastField) {
        if (G.lastField !== null && pref("followGame", "0") === "1") openGameField();
        G.lastField = g.field;
      }
      renderBar();
    } catch (e) { /* server restarting */ }
    setTimeout(pollGame, 1500);
  }

  // G: play this field (or go to it, or re-enter it); B: before and after
  window.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector(".dialog-modal") || !$("#looksModal").hidden) return;
    if (e.key === "g" || e.key === "G") play();
    else if (e.key === "b" || e.key === "B") beforeAfter();
  });

  /* ---------------------------------------------------------- the 3D scenes setting (in Settings) */

  const AUTO = {
    final: "Build its 3D scene with every final render",
    built: "Only rebuild 3D scenes that already exist",
    off: "Leave the 3D scenes alone",
    all: "Build its 3D scene with any render, previews too",
  };
  async function renderAuto() {
    const box = $("#settingsPlugins");
    if (!box) return;
    let cur = "final";
    try { cur = (await api("/api/3d/settings")).auto; } catch (e) { /* older plug-in server */ }
    showSkip(cur);
    const opts = Object.entries(AUTO).filter(([v]) => v !== "all" || cur === "all");
    box.innerHTML = `<div class="pop-head"><b>3D scenes</b></div><div class="ctl">` +
      `<label class="lbl">When a field's version in the game changes</label>` +
      `<select data-a="auto">${opts.map(([v, l]) => `<option value="${v}" ${cur === v ? "selected" : ""}>${l}</option>`).join("")}</select>` +
      `<div class="ctl-help">${helpText("A new final render, or Use this version, changes what the game shows. With every final render, " +
        "Generate final also builds the 3D scene, straight after the render (in a batch, field by field), and scenes that " +
        "already exist follow any new version. For every field, not just this one. To leave the 3D scenes out of " +
        "one batch, tick Skip the 3D scenes under the selected fields.", "with every final render")}</div></div>`;
    box.querySelector('[data-a="auto"]').onchange = async (e) => {
      await api("/api/3d/settings", { body: { auto: e.target.value } });
      showSkip(e.target.value);
      toast("3D scenes setting saved");
    };
  }
  renderAuto();

  /* ---------------------------------------------------------- field list */

  // after the name (the list sets the name's text on every redraw, so the mark goes back on each time)
  function markList() {
    for (const li of $("#fieldList").children) {
      const name = li.querySelector(".name");
      if (!name) continue;
      let tag = name.querySelector(".b3d");
      const st = T.built[li.dataset.name];
      if (st === undefined) { if (tag) tag.remove(); continue; }
      if (!tag) {
        tag = document.createElement("span");
        name.appendChild(tag);
      }
      const cls = `b3d${st ? "" : " stale"}`;
      if (tag.className !== cls) {
        tag.className = cls;
        tag.textContent = "3D";
        tag.title = st ? "Has a 3D scene, up to date" : "Has a 3D scene, built from another version";
      }
    }
  }

  /* ---------------------------------------------------------- wiring */

  document.addEventListener("studio:field", () => { refresh(); renderBar(); });
  document.addEventListener("studio:list", markList);
  document.addEventListener("studio:jobs", (e) => {
    if (e.detail.some((r) => String(r.kind).startsWith("3d") || r.name === S.current)) { refreshBuilt(); refresh(); }
  });
  setInterval(() => { if (S.tab === "3d") { refresh(); } }, 2000);
  setInterval(refreshBuilt, 15000);
  refreshBuilt();
  refresh();
  pollGame();

  // the preview's 3D view: a mode next to Original / New / Split
  const view3d = document.createElement("script");
  view3d.src = "/api/3d/view3d.js";
  document.head.appendChild(view3d);
})();
