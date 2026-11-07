/* FF7 Field Studio plug-in: the Models section (battle scenes, battle characters and enemies, field characters and
   objects). Server side: studio/plugins/models.py (routes under /api/modelpaint). Uses the Studio's own helpers ($,
   api, esc, toast, dialog, ask, tell, helpText, fmtDuration, fmtWhen, pref, setPref, famShort, openLooks, S,
   JOB_LABELS) and its help markup, so the ⓘ compact help works here too. */
(() => {
  const API = "/api/modelpaint";
  const KINDS = {
    scene: { tab: "Battle scenes", one: "Battle scene", job: "battle scene", search: "Search: oq, location 2, Guard Hound…",
      hint: "The places battles are fought in. Each one's textures (ground, walls, sky) are painted flat in your look, and you can start one of its battles in your game to see it." },
    battle: { tab: "Battle characters & enemies", one: "Battle character or enemy", job: "battle model", search: "Search: Cloud, Grunt, rt, 2d painted…",
      hint: "The party and the enemies as they fight. Paint in 2D style paints any of them as a hand-drawn 2D character. Most enemies have no textures (their colour is in the model), so the Paint card only paints the ones with textures." },
    field: { tab: "Field characters & objects", one: "Field character or object", job: "field model", search: "Search: cloud, aeris, save point, AAAA…",
      hint: "The people and things that walk around the fields: the game's original models, not the chibi mod you play with." },
  };
  const JOBS = { model: "final", model_preview: "preview" };
  const M = {
    open: pref("models.open", "0") === "1", kind: KINDS[pref("models.kind", "scene")] ? pref("models.kind", "scene") : "scene",
    lists: {}, id: null, item: null, mode: pref("models.mode", "orig"), ver: null,
    cam: 0, yaw: 0, pitch: 0, zoom: 1, split: 0.5, looks: null, viewError: "", hideBare: pref("models.hideBare", "0") === "1",
    // "Try it in a battle": the running game (/game), its settings (remembered), the last answer
    game: null, god: pref("models.god", "1") === "1", auto: pref("models.auto", "0") === "1", battleBusy: false,
    battleResult: null,
    // the battle look (battle scenes): what the Studio last said about it, an install or uninstall under way
    bl: null, blBusy: false, blSeq: 0,
    // paint in 2D style (battle models): what the Studio last said about it, the version shown, a click under way
    p2d: null, p2dSeq: 0, p2dVer: null, p2dBusy: false,
  };
  if (!["orig", "new", "split"].includes(M.mode)) M.mode = "orig";

  JOB_LABELS.model = (j) => `painting the final${j.family ? ` · ${famShort(j.family)}` : ""}`;
  JOB_LABELS.model_preview = (j) => `painting a preview${j.family ? ` · ${famShort(j.family)}` : ""}`;
  JOB_LABELS.model_look = () => "building the battle look";
  JOB_LABELS.model_2d = (j) => `painting in 2D style${j.family ? ` · ${famShort(j.family)}` : ""}`;

  const css = document.createElement("style");
  css.textContent = `
    .mdl-switch { margin-left: 2px; }
    .mdl-page { position: fixed; top: 46px; left: 0; right: 0; bottom: 0; z-index: 20; display: grid;
                grid-template-columns: 300px 1fr 360px; background: var(--bg); }
    .mdl-kinds { display: grid; gap: 4px; }
    .mdl-kinds button { text-align: left; }
    .mdl-kinds button.on { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
    .mdl-kind-hint { line-height: 1.4; }
    .mdl-list li { grid-template-columns: 72px 1fr auto; }
    .mdl-list li.bare .name, .mdl-list li.bare img { opacity: .55; }
    .mdl-list .sub { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; }
    .mdl-vp { cursor: grab; }
    .mdl-vp.dragging { cursor: grabbing; }
    .mdl-vp.split-hover { cursor: ew-resize; }
    .mdl-vp canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
    .mdl-vp .empty { display: flex; pointer-events: none; }
    .mdl-note { top: auto; bottom: 10px; left: 50%; transform: translateX(-50%); max-width: 80%; font-weight: 400;
                text-align: center; white-space: normal; }
    .mdl-lines { line-height: 1.5; color: var(--muted); margin: 0; }
    .mdl-lines div + div { margin-top: 3px; }
    .mdl-title { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
    .mdl-sub { color: var(--faint); font-size: 12px; margin-bottom: 6px; }
    .mdl-line { margin: 4px 0; }
    .mdl-bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; margin: 6px 0; }
    .mdl-bar i { display: block; height: 100%; background: var(--accent); }
    .mdl-card select { width: 100%; }
    .vbadge.inst { left: 3px; color: var(--ok); }
    .mdl-sheet .modal-card { width: min(1100px, 96vw); }
    .mdl-sheet img { width: 100%; display: block; border-radius: 6px; background: #000; }
    .mdl-cam-row { display: flex; gap: 6px; align-items: center; }
    .mdl-cam-row select { flex: 1; min-width: 0; }
    .mdl-sub-row { display: flex; justify-content: space-between; align-items: baseline; }
    .mdl-sub-row .linkish { font-size: 11px; text-transform: none; letter-spacing: 0; }
    .mdl-sub-row .linkish:disabled { color: var(--faint); text-decoration: none; cursor: default; }
    .mdl-auto { font-size: 12px; margin: 2px 0 2px; }
    .mdl-card textarea { font-size: 12px; }
    .mdl-quote { font-size: 12px; color: var(--muted); background: #12151a; border: 1px solid var(--line); border-radius: 6px;
                 padding: 6px 8px; max-height: 96px; overflow-y: auto; line-height: 1.4; }
    .mdl-used { font-size: 12px; color: var(--muted); margin-bottom: 2px; }
    .mdl-off > .lbl, .mdl-off > select { opacity: .5; }
    .mdl-bl-prev { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin: 8px 0 4px; }
    .mdl-bl-prev figure { margin: 0; cursor: zoom-in; min-width: 0; }
    .mdl-bl-prev img { width: 100%; aspect-ratio: 4 / 3; object-fit: contain; display: block; border-radius: 4px;
                       background: #000; border: 1px solid var(--line); }
    .mdl-bl-prev figcaption { font-size: 11px; color: var(--faint); margin-top: 2px; line-height: 1.3; }
    .mdl-bl-part .hint { margin-top: 1px; line-height: 1.4; }
    .mdl-swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; vertical-align: -1px;
                  border: 1px solid var(--line); margin: 0 2px; }
    .mdl-bl-probs { font-size: 11px; color: var(--faint); margin: 4px 0 0; padding-left: 16px; }
    .mdl-sheet .mdl-bl-big { max-height: 75vh; object-fit: contain; }
    .mdl-list .mdl-2d { font-size: 11px; color: var(--faint); }
    .mdl-list .mdl-2d.painted { color: var(--gen); }
    .mdl-list .mdl-2d.game { color: var(--ok); }
    .mdl-p2d-vers { display: grid; gap: 4px; margin: 6px 0; max-height: 260px; overflow-y: auto; }
    .mdl-p2d-ver { display: grid; grid-template-columns: 64px 1fr; gap: 8px; align-items: center; padding: 4px;
                   border: 1px solid var(--line); border-radius: 6px; cursor: pointer; }
    .mdl-p2d-ver:hover { border-color: var(--accent); }
    .mdl-p2d-ver.viewing { border-color: var(--accent); background: rgba(255,255,255,.04); }
    .mdl-p2d-ver img, .mdl-p2d-ver .noimg { width: 64px; height: 48px; object-fit: cover; object-position: right center;
                   border-radius: 4px; background: #000; display: block; }
    .mdl-p2d-ver .vl { font-size: 12px; }
    .mdl-p2d-ver .vs, .mdl-p2d-ver .vt { font-size: 11px; color: var(--faint); line-height: 1.35; }
    .mdl-p2d-prev { display: grid; gap: 6px; margin: 8px 0 4px; }
    .mdl-p2d-prev figure { margin: 0; cursor: zoom-in; min-width: 0; }
    .mdl-p2d-prev img { width: 100%; max-height: 200px; object-fit: contain; display: block; border-radius: 4px;
                        background: #000; border: 1px solid var(--line); }
    .mdl-p2d-prev figcaption { font-size: 11px; color: var(--faint); margin-top: 2px; line-height: 1.3; }
    .mdl-sheet .mdl-p2d-views img + img { margin-top: 8px; }
    @media (max-width: 1150px) { .mdl-page { grid-template-columns: 220px minmax(0, 1fr) 300px; } }
    @media (max-width: 860px) { .mdl-page { grid-template-columns: 180px minmax(0, 1fr) 260px; } }`;
  document.head.appendChild(css);

  /* ---------------------------------------------------------- the page */

  // Fields | Models, next to the Studio's name
  const sw = document.createElement("div");
  sw.className = "seg small mdl-switch";
  sw.innerHTML = `<button data-s="fields" title="The field backgrounds (the Studio's usual page)">Fields</button>` +
    `<button data-s="models" title="Battle scenes, battle characters and enemies, field characters and objects: paint their textures with your looks">Models</button>`;
  $(".topbar .brand").after(sw);
  for (const b of sw.children) b.onclick = () => setOpen(b.dataset.s === "models");

  const page = document.createElement("main");
  page.className = "mdl-page";
  page.hidden = true;
  page.innerHTML = `
    <aside class="sidebar">
      <div class="sidebar-tools">
        <div class="mdl-kinds" id="mdlKinds">${Object.entries(KINDS).map(([k, v]) =>
          `<button data-k="${k}" title="${esc(v.hint)}">${esc(v.tab)}</button>`).join("")}</div>
        <div class="hint mdl-kind-hint" id="mdlKindHint"></div>
        <input id="mdlSearch" type="search" autocomplete="off">
        <div class="count-row"><span class="count" id="mdlCount"></span>
          <label class="check" id="mdlBareBox" title="Leave out the models that have no textures (nothing to paint on them yet)"><input type="checkbox" id="mdlHideBare"> Only with textures</label></div>
      </div>
      <ul class="field-list mdl-list" id="mdlList"></ul>
    </aside>
    <section class="stage">
      <div class="stage-toolbar">
        <div class="seg" id="mdlModes">
          <button data-m="orig" title="The model as the game draws it now (1)">Original</button>
          <button data-m="syw" title="SYW Unified Battle Textures' ready-made 4× of the original, where the pack has this one's textures (5)" hidden>SYW</button>
          <button data-m="new" title="With the painted textures of the version picked below (2)">Painted</button>
          <button data-m="split" title="Original on the left, painted on the right; drag the line to move it (3)">Split</button>
        </div>
        <span class="hint">drag to turn · wheel to zoom · R resets</span>
        <div class="spacer"></div>
        <button id="mdlReset" title="Back to the starting angle and zoom (R)">Reset view</button>
      </div>
      <div class="viewport mdl-vp" id="mdlVp">
        <canvas id="mdlCanvas"></canvas>
        <div class="badge" id="mdlBadge" hidden></div>
        <div class="badge side-l" id="mdlBadgeL" hidden></div>
        <div class="badge side-r" id="mdlBadgeR" hidden></div>
        <div class="empty" id="mdlEmpty"></div>
        <div class="badge mdl-note" id="mdlNote" hidden></div>
      </div>
      <div class="versions">
        <div class="ver-head">
          <div class="ver-title" id="mdlVerTitle"></div>
          <div class="spacer"></div>
          <button id="mdlUse" class="primary small" title="Make this the version Install puts in the game. It doesn't change the game by itself.">Use this version</button>
          <button id="mdlSheet" class="small" title="Every angle the AI painted from: the original next to the painted result">Views sheet</button>
          <button id="mdlDelete" class="small ghost danger" title="Delete this version's painted textures and pictures">Delete</button>
        </div>
        <div class="ver-info" id="mdlVerInfo"></div>
        <div class="ver-strip" id="mdlStrip"></div>
      </div>
      <div class="stage-status" id="mdlStatus"></div>
    </section>
    <aside class="panel">
      <div class="panel-body"><div class="tab">
        <section class="card mdl-card" id="mdlAbout"></section>
        <section class="card mdl-card" id="mdlView"></section>
        <section class="card mdl-card" id="mdlP2d" hidden></section>
        <section class="card mdl-card" id="mdlPaint"></section>
        <section class="card mdl-card" id="mdlGame"></section>
        <section class="card mdl-card" id="mdlBl" hidden></section>
      </div></div>
      <section class="gen-foot">
        <div class="estimate" id="mdlEstimate"></div>
        <div class="row-btns">
          <button id="mdlPreview" title="A quick painting from fewer angles, at a smaller size">Preview</button>
          <button id="mdlFinal" class="primary" title="The full painting from every angle, at full size">Paint final</button>
        </div>
        <div class="row-btns tight">
          <button id="mdlCancel" class="small ghost" title="Stop the Studio's queue, as Cancel queue on the fields' page does: the running job stops at its next step and everything queued is dropped, fields' renders too">Cancel queue</button>
        </div>
        <div class="progress" id="mdlProgress"><div class="bar"></div><span class="ptext"></span></div>
      </section>
    </aside>`;
  document.body.appendChild(page);
  const vp = $("#mdlVp"), canvas = $("#mdlCanvas"), ctx = canvas.getContext("2d");

  // a card's HTML is only replaced when it changes (an open select stays open, a hovered button keeps its tip)
  function fill(el, html, wire) {
    if (el.dataset.html === html) return;
    el.innerHTML = html;
    el.dataset.html = html;
    if (wire) wire(el);
  }

  function setOpen(on) {
    M.open = on;
    setPref("models.open", on ? "1" : "0");
    page.hidden = !on;
    for (const b of sw.children) b.classList.toggle("on", (b.dataset.s === "models") === on);
    if (on) {
      setKind(M.kind, true);
      resize();
    }
  }

  function setKind(kind, keep = false) {
    const changed = kind !== M.kind;
    M.kind = kind;
    setPref("models.kind", kind);
    for (const b of $("#mdlKinds").children) b.classList.toggle("on", b.dataset.k === kind);
    $("#mdlKindHint").textContent = KINDS[kind].hint;
    $("#mdlSearch").placeholder = KINDS[kind].search;
    $("#mdlBareBox").hidden = kind !== "battle";
    if (changed || !keep) $("#mdlSearch").value = pref(`models.search.${kind}`, "");
    loadList();
  }

  /* ---------------------------------------------------------- the list */

  async function loadList() {
    const kind = M.kind;
    let r;
    try { r = await api(`${API}/list/${kind}`); } catch (e) { $("#mdlCount").textContent = e.message; return; }
    if (r.loading) {
      $("#mdlCount").textContent = r.error ? `Couldn't read the game's models: ${r.error}` : "Reading the game's models…";
      setTimeout(() => { if (M.open && M.kind === kind) loadList(); }, 1500);
      return;
    }
    M.lists[kind] = r.items;
    if (kind !== M.kind) return;
    renderList(true);
    const last = pref(`models.sel.${kind}`, "");
    const want = M.id && M.item && M.item.kind === kind ? M.id : last;
    const item = r.items.find((x) => x.id === want) || r.items.find((x) => x.textured) || r.items[0];
    if (item && (item.id !== M.id || !M.item || M.item.kind !== kind)) select(item.id);
  }

  // a battle model's 2D painting in the list (models.py p2d_status): the line under its name, its tip
  const P2D_STATUS = {
    "": { text: "2D: not painted", tip: "Not painted in 2D style yet" },
    built: { text: "2D: not painted", tip: "Not painted in 2D style yet (only a check with the game's own colours was made)" },
    painted: { text: "2D: painted", tip: "Painted in 2D style, not in the game" },
    game: { text: "2D: in the game", tip: "Its 2D painting is in the game" },
  };

  function dotClass(x) {
    if (runningJob(M.kind, x.id) || (M.kind === "battle" && p2dJobs(x.id).any)) return "running";
    if (x.error) return "error";
    if (x.installed || x.p2d === "game") return "built";
    if (x.mode === "final" || x.p2d === "painted") return "generated";
    if (x.mode === "preview") return "preview";
    return "";
  }

  function dotTip(x) {
    let tip;
    if (x.error) tip = `The last paint failed: ${x.error}`;
    else if (x.installed) tip = "Painted and in the game";
    else if (x.mode === "final") tip = "Painted (final), not in the game";
    else if (x.mode === "preview") tip = "Preview painted";
    else tip = "Not painted yet";
    if (M.kind === "battle") tip = `Its textures: ${tip.charAt(0).toLowerCase()}${tip.slice(1)}\n${(P2D_STATUS[x.p2d || ""] || P2D_STATUS[""]).tip}`;
    return tip;
  }

  function renderList(rebuild = false) {
    const ul = $("#mdlList"), items = M.lists[M.kind] || [];
    if (rebuild || ul.dataset.kind !== M.kind) {
      ul.dataset.kind = M.kind;
      ul.innerHTML = items.map((x) => `<li data-id="${esc(x.id)}" class="${x.textured ? "" : "bare"}">` +
        `<img loading="lazy" alt=""><div><div class="name"></div><div class="sub"></div>` +
        `${M.kind === "battle" ? `<div class="mdl-2d"></div>` : ""}</div><span class="sdot"></span></li>`).join("");
      for (const li of ul.children) li.onclick = () => select(li.dataset.id);
    }
    const q = $("#mdlSearch").value.trim().toLowerCase();
    const byId = new Map(items.map((x) => [x.id, x]));
    let shown = 0, painted = 0;
    for (const li of ul.children) {
      const x = byId.get(li.dataset.id);
      if (!x) continue;
      const src = `${API}/thumb/${M.kind}/${encodeURIComponent(x.id)}.jpg` + (x.current ? `?v=new&t=${x.current}` : "");
      const img = li.querySelector("img");
      if (img.dataset.src !== src) { img.src = src; img.dataset.src = src; }
      // only real changes touch the page (this runs every second while a job runs)
      const name = li.querySelector(".name"), sub = li.querySelector(".sub"), dot = li.querySelector(".sdot");
      if (name.textContent !== x.title) name.textContent = x.title;
      if (sub.textContent !== x.sub) { sub.textContent = x.sub; sub.title = x.sub; }
      const p2 = li.querySelector(".mdl-2d");
      if (p2) {
        const st = P2D_STATUS[x.p2d || ""] || P2D_STATUS[""], c2 = `mdl-2d ${x.p2d || ""}`;
        if (p2.textContent !== st.text) { p2.textContent = st.text; p2.title = st.tip; }
        if (p2.className !== c2) p2.className = c2;
      }
      const cls = `sdot ${dotClass(x)}`, tip = dotTip(x);
      if (dot.className !== cls) dot.className = cls;
      if (dot.title !== tip) dot.title = tip;
      li.classList.toggle("active", x.id === M.id);
      const pass = (!q || x.search.includes(q)) && !(M.kind === "battle" && M.hideBare && !x.textured);
      li.style.display = pass ? "" : "none";
      if (pass) shown++;
      if (x.current || x.p2d === "painted" || x.p2d === "game") painted++;
    }
    const bare = M.kind === "battle" ? items.filter((x) => !x.textured).length : 0;
    $("#mdlCount").textContent = `${shown} shown · ${painted} painted` + (bare ? ` · ${bare} without textures` : "");
  }

  function scrollToActive() {
    const li = $("#mdlList li.active");
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  /* ---------------------------------------------------------- the selected one */

  async function select(id) {
    if (id !== M.id) {
      M.id = id;
      M.ver = null;
      M.p2dVer = null;
      M.cam = 0;
      resetView(false);
      R.shown = {};
      M.item = null;
      setPref(`models.sel.${M.kind}`, id);
    }
    renderList();
    await loadItem();
    scrollToActive();
  }

  async function loadItem() {
    const kind = M.kind, id = M.id;
    if (!id) return;
    let it;
    try { it = await api(`${API}/item/${kind}/${encodeURIComponent(id)}`); } catch (e) { toast(e.message); return; }
    if (kind !== M.kind || id !== M.id) return;
    setItem(it);
  }

  function setItem(it) {
    M.item = it;
    const ids = it.versions.map((v) => v.id);
    if (!ids.includes(M.ver)) M.ver = it.current || ids[ids.length - 1] || null;
    if (M.cam >= Math.max(1, it.cameras.length)) M.cam = 0;
    if (M.bl && (M.bl.id !== it.id || it.kind !== "scene")) M.bl = null;
    if (M.p2d && (M.p2d.id !== it.id || it.kind !== "battle")) M.p2d = null;
    loadLooks();
    renderAll();
    requestRender(false);
    loadBattleLook();
    loadP2d();
  }

  function renderAll() {
    renderModes();
    renderAbout();
    renderView();
    renderP2d();
    renderPaint();
    renderGame();
    renderBattleLook();
    renderVersions();
    renderFoot();
    renderStatus();
  }

  const viewed = () => (M.item && M.item.versions.find((v) => v.id === M.ver)) || null;
  const verKindName = (v) => (v.mode === "final" ? "Final" : "Preview");
  const verLabel = (v) => `${verKindName(v)}${v.family ? ` · ${famShort(v.family)}` : ""}`;

  const hasSyw = () => !!(M.item && M.item.syw && M.item.syw.have);

  function renderModes() {
    if (M.mode === "syw" && M.item && !hasSyw()) M.mode = "orig";
    for (const b of $("#mdlModes").children) {
      b.classList.toggle("on", b.dataset.m === M.mode);
      if (b.dataset.m === "syw") b.hidden = !hasSyw();
    }
  }

  function setMode(m) {
    M.mode = m;
    setPref("models.mode", m);
    renderModes();
    draw();
    requestRender(false);
  }

  function renderAbout() {
    const it = M.item;
    if (!it) return;
    const notes = [];
    if (!it.paintable) notes.push(`<div class="model-warn">${esc(it.why_not)}</div>`);
    if (it.error) notes.push(`<div class="model-warn" style="color:var(--bad)">The last paint failed: ${esc(it.error)}</div>`);
    fill($("#mdlAbout"), `<div class="mdl-title">${esc(it.title)}</div>` +
      `<div class="mdl-sub">${esc(KINDS[it.kind].one)} · ${esc(it.id)}</div>` +
      `<div class="mdl-lines">${it.lines.map((l) => `<div>${esc(l)}</div>`).join("")}</div>${notes.join("")}`);
  }

  function renderView() {
    const it = M.item;
    if (!it) return;
    const cams = it.kind === "scene" && it.cameras.length
      ? `<div class="ctl"><label class="lbl" for="mdlCam">Battle camera</label>` +
        `<div class="mdl-cam-row"><select id="mdlCam">${it.cameras.map((c, i) =>
          `<option value="${i}" ${i === M.cam ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></div>` +
        `<div class="ctl-help">${helpText("Which of the game's battle cameras to look from. Camera 1 is the one most battles " +
          "here start on; the others are the angles the game cuts to during a fight. You can still drag to turn away from it.",
          "camera 1")}</div></div>`
      : "";
    const html = `<h2>View</h2>` +
      `<div class="ctl"><span class="lbl">Original, Painted or Split</span>` +
      `<div class="ctl-help">${helpText("What the picture shows. Original: the model as the game draws it now. Painted: with the " +
        "painted textures of the version picked under the picture. Split: the original on the left and the painted one on the " +
        "right; drag the line between them to move it. Either way it's shaded the way the game shades it." +
        (hasSyw() ? " SYW: the SYW Unified Battle Textures pack's ready-made 4× of the original, to compare." : ""), "Split, to see what changed")}</div></div>` +
      cams +
      `<div class="ctl"><span class="lbl">Turning it around</span>` +
      `<div class="ctl-help">${helpText("Drag the picture to turn around " + (it.kind === "scene" ? "the arena" : "the model") +
        ", scroll to zoom in and out. Reset view (or R) goes back to the starting angle. Up and down arrows pick the next one in the list.", "")}</div></div>`;
    fill($("#mdlView"), html, (el) => {
      const sel = el.querySelector("#mdlCam");
      if (sel) sel.onchange = () => { M.cam = Number(sel.value); resetView(); };
    });
  }

  async function loadLooks() {
    try { M.looks = await api("/api/looks"); } catch (e) { return; }
    renderPaint();
    renderP2d();
    renderFoot();
  }

  function lookName(id) {
    const L = M.looks;
    const l = L && L.looks.find((x) => x.id === id);
    return l ? l.name : id;
  }

  function lookInUse() {
    const L = M.looks, it = M.item;
    if (!L || !it) return null;
    return L.looks.find((x) => x.id === (it.look && L.looks.some((y) => y.id === it.look) ? it.look : L.default)) || null;
  }

  // a model's paint settings in words (versions, the estimate)
  const fmtX = (x) => (x === 0.5 ? "½×" : `${x}×`);
  const DEFAULT_UP = "4x-UltraSharp.safetensors";
  // an upscaler's name as the Studio's look editor shows it (app.js upName), short
  const upShort = (u) => (!u ? "" : u === "lanczos" ? "plain resize" : u === "seedvr2" ? "SeedVR2" : u === "syw" ? "SYW Unified"
    : typeof upName === "function" ? upName(u) : u.replace(/\.safetensors$|\.pth$/, ""));
  const METHOD_NAMES = { flat: "Flat", projected: "Projected", hybrid: "Hybrid" };
  // the size the ground textures come out at with this ground detail (Paint final; Preview's when it differs)
  function detailSize(d, PF, PP) {
    const px = (pl) => (pl.detail_eff < d ? `capped at ${pl.ground_px} px, the same as ${pl.detail_eff}×` :
      `${pl.ground_px} px${pl.ground_px >= 8192 ? " (the most)" : ""}`);
    return `${d}× here: ${px(PF)}` + (PP.ground_px && PP.ground_px !== PF.ground_px ? ` (Preview: ${px(PP)})` : "");
  }
  // a battle scene version's extras: "sky as a panorama · 2 ground textures with detail 2× · seams blended"
  function extrasText(v, long = false) {
    if (!v) return "";
    const ex = v.extras || {}, bits = [];
    if (v.panorama) bits.push(long ? "sky as a panorama" : "sky panorama");
    if (v.detail_n) bits.push(long ? `${v.detail_n} ground texture${v.detail_n === 1 ? "" : "s"} with detail ${ex.detail || 2}×` : `ground ${ex.detail || 2}×`);
    if (long && ex.seams) bits.push("seams blended");
    return bits.join(" · ");
  }

  function settingsText(kind, ps, long = false, method = null) {
    if (!ps) return method ? (long ? `painted ${(METHOD_NAMES[method] || method).toLowerCase()}` : METHOD_NAMES[method] || method) : "";
    const bits = [];
    const m = ps.method || method;
    if (m) bits.push(long ? `painted ${(METHOD_NAMES[m] || m).toLowerCase()}` : METHOD_NAMES[m] || m);
    if (ps.strength != null) bits.push(`strength ${Number(ps.strength).toFixed(2)}`);
    else if (ps.strength_used != null) bits.push(`strength ${Number(ps.strength_used).toFixed(2)} (auto)`);
    if (kind === "field") bits.push(`atlas ${fmtX(ps.output || 1)}`);
    else if (ps.input) bits.push(ps.output && ps.output !== ps.input ? `in ${ps.input}× → out ${ps.output}×` : `${ps.input}×`);
    // the upscalers: on a card only when not the default; in full, every one that was used
    const ups = [["up_in", "before painting"], ["up_out", "for the game size"]].filter(([k]) => ps[k] && (long || ps[k] !== DEFAULT_UP));
    for (const [k, what] of ups) bits.push(long ? `upscaler ${what}: ${upShort(ps[k])}` : `${upShort(ps[k])} ${k === "up_in" ? "first" : "after"}`);
    if (long) bits.push(ps.describe ? "described automatically" : "no automatic description");
    return bits.join(" · ");
  }

  // the Paint card: the look, then this model's own settings (strength, input and output scale, its words)
  function renderPaint() {
    const it = M.item, L = M.looks;
    if (!it) return;
    const box = $("#mdlPaint");
    // typing in the card (its words): leave it as it is until you leave the box, so the cursor stays put
    if (box.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") {
      M.paintStale = true;
      return;
    }
    M.paintStale = false;
    const def = L ? L.looks.find((x) => x.id === L.default) : null;
    const opts = [`<option value="" ${!it.look ? "selected" : ""}>The default look${def ? ` (${esc(def.name)})` : ""}</option>`]
      .concat((L ? L.looks : []).map((l) => `<option value="${esc(l.id)}" ${it.look === l.id ? "selected" : ""}>` +
        `${esc(l.name)}${l.family ? ` · ${esc(famShort(l.family))}` : ""}</option>`))
      .concat([`<option value="__manage">Manage looks…</option>`]);
    const views = it.view_counts;
    const ps = it.paint, A = it.paint_auto, D = it.paint_defaults, field = it.kind === "field", scene = it.kind === "scene";
    const method = it.method, P = it.plans || {};
    const ring = scene ? "its battle cameras and views all round from the middle of the arena, looking out" : "all round it: front, both sides, back and a little from above";
    const what = method === "flat"
      ? `Each of its ${P.final ? P.final.sheets : it.sheet_count} textures is painted flat, one at a time, in your look.` +
        (scene ? " The view from the main battle camera tells the AI what kind of place it is." : "") +
        " Preview and Paint final paint the same textures; with the default input scale, Preview works at 2× and Paint final at 4×."
      : method === "hybrid"
        ? `The textures it repeats are painted flat (${P.final ? P.final.sheets : 0} here), the rest from views of ${ring}` +
          (P.final && P.final.views ? ` (Paint final: ${P.final.views} views, Preview: ${P.preview.views}).` : ": here every texture is a repeated one, so no views are needed.")
        : `It's painted from views of ${ring}: Paint final uses ${P.final ? P.final.views : views.final}, Preview ` +
          `${P.preview ? P.preview.views : views.preview}, smaller.`;
    const allDefault = Object.keys(D).every((k) => (ps[k] ?? null) === (D[k] ?? null));
    const ch = (k) => ((ps[k] ?? null) !== (D[k] ?? null) ? "changed" : "");
    // the painting method: flat, projected or hybrid (field models: projected only)
    const hf = P.final ? P.final.hybrid_flat : 0, usedN = P.final ? P.final.used : 0;
    const methodOpt = (m) => {
      const dflt = m === it.default_method;
      const off = field && m !== "projected";
      const extra = m === "hybrid" && !field && usedN ? ` (here: ${hf} of ${usedN} textures flat)` : "";
      return `<option value="${dflt ? "" : m}" ${method === m ? "selected" : ""} ${off ? "disabled" : ""}>` +
        `${METHOD_NAMES[m]}${dflt ? " (default)" : ""}${extra}${off ? ": not for field models" : ""}</option>`;
    };
    const methodSel = `<div class="ctl ${ch("method")}"><label class="lbl" for="mdlMethod">Painting method</label>` +
      `<select id="mdlMethod" ${field ? "disabled" : ""}>${["flat", "projected", "hybrid"].map(methodOpt).join("")}</select>` +
      (field
        ? `<div class="hint">Flat and Hybrid aren't offered for field models: their atlas is cut-up pieces of the model laid out ` +
          `side by side, so painted flat the AI can't tell what's what; and nothing in it repeats, so Hybrid would be the same as Projected.</div>`
        : `<div class="hint">Hybrid here: ${hf} of ${usedN} texture${usedN === 1 ? "" : "s"} flat, ${usedN - hf} from views.</div>`) +
      `<div class="ctl-help"><div>How the AI paints ${scene ? "the scene" : "the model"}.</div><div class="help-range">` +
      `<span><b>Flat:</b> paints each texture on its own: the same from every angle, fast, and a gentle look; big markings ` +
      `split across textures can come out garbled.</span>` +
      `<span><b>Projected:</b> paints pictures of the whole ${scene ? "scene" : "model"} and maps them back: more context and a ` +
      `stronger painted look, but only right where the views saw it (other angles show smears or the original), and slower.</span>` +
      `<span><b>Hybrid:</b> the textures it repeats (floor and wall tiles, mirrored halves) flat and seamless, the one-off ` +
      `textures from the views.</span></div>` +
      `<div class="help-rec">Recommended: Flat for battle scenes, Projected for models.</div></div></div>`;
    const PF = P.final || {}, PP = P.preview || {};
    // battle scenes painted Flat or Hybrid: the sky as one panorama, the ground with detail, the seams blended
    const parts = it.parts || { far: 0, ground: 0 };
    const exOn = scene && (method === "flat" || method === "hybrid");
    const exWhy = !scene ? "These are for battle scenes only." : !exOn
      ? "These work when the painting method is Flat or Hybrid; Projected paints from views instead." : "";
    const panoOff = !exOn || !parts.far, detOff = !exOn || !parts.ground;
    const extras = `<div class="subhead">For battle scenes painted Flat or Hybrid</div>` +
      (exWhy ? `<div class="hint">${exWhy}</div>` : "") +
      `<label class="check ctl ${ch("panorama")} ${panoOff ? "mdl-off" : ""}"><input type="checkbox" id="mdlPano" ` +
      `${ps.panorama !== false ? "checked" : ""} ${panoOff ? "disabled" : ""}><span>Paint the sky as one panorama` +
      (exOn && !parts.far ? `<div class="hint">This scene has no sky or far scenery.</div>` : "") +
      `<div class="ctl-help">${helpText("The sky and far mountains are painted together as one picture all round the arena, " +
        "so the bands of sky meet without a line and the mountains stay where they are. Off: each of their textures is " +
        "painted on its own, and lines can show where they meet.", "on")}</div></span></label>` +
      `<div class="ctl ${ch("detail")} ${detOff ? "mdl-off" : ""}"><label class="lbl" for="mdlDetail">Ground detail</label>` +
      `<select id="mdlDetail" ${detOff ? "disabled" : ""}>` +
      [[1, "Off"], [2, "2× (default)"], [4, "4×"], [8, "8×"], [16, "16×"]].map(([v, l]) =>
        `<option value="${v}" ${(ps.detail || 2) === v ? "selected" : ""}>${l}</option>`).join("") +
      `</select>` + (scene && parts.ground ? `<div class="hint">${parts.ground} ground texture${parts.ground === 1 ? "" : "s"} here` +
          (exOn && (ps.detail || 2) > 1 && PF.ground_px ? `; ${detailSize(ps.detail || 2, PF, PP)}` : "") + `.</div>`
        : exOn ? `<div class="hint">No ground texture found in this scene.</div>` : "") +
      `<div class="ctl-help"><div>The floor is a small texture stretched over the whole arena, so it's painted bigger and ` +
      `the detail pass adds grass, stones and cracks. Nothing goes over 8192 pixels: higher settings stop there.</div>` +
      `<div class="help-range"><span><b>Higher:</b> sharper on the floor up close, but much slower (the detail pass repaints the ` +
      `whole enlarged floor in tiles: about 1 minute per ground texture at 4×, about 4 minutes at 8×) and very big files the ` +
      `game has to hold in memory; above 4096 pixels it's rarely visible at 1080p.</span>` +
      `<span><b>Lower:</b> faster and smaller; Off paints the floor at the size of the other textures, softer up close.</span></div>` +
      `<div class="help-rec">Recommended: 2×–4×.</div></div></div>` +
      `<label class="check ctl ${ch("seams")} ${exOn ? "" : "mdl-off"}"><input type="checkbox" id="mdlSeams" ` +
      `${ps.seams !== false ? "checked" : ""} ${exOn ? "" : "disabled"}><span>Blend seams` +
      `<div class="ctl-help">${helpText("Where two textures, or two parts of one, meet in the scene, their colours are " +
        "blended so no line shows. Off: each texture keeps exactly the colours it was painted with.", "on")}</div></span></label>`;
    // paint strength: Automatic (the look's own) or the user's number
    const auto = ps.strength == null;
    const sv = auto ? (scene ? A.sheets : A.views) : ps.strength;
    const autoText = scene ? `Automatic: ${A.sheets.toFixed(2)} for texture sheets (the look's ${A.views.toFixed(2)}, a little gentler)`
      : `Automatic: ${A.views.toFixed(2)}, the look's own`;
    const strength = `<div class="slider ${ch("strength")} ${A.uses ? "" : "dim"}" id="mdlStrength">` +
      `<div class="top"><span>Paint strength</span><b>${auto ? `auto ${sv.toFixed(2)}` : sv.toFixed(2)}</b></div>` +
      `<input type="range" min="0.2" max="0.9" step="0.05" value="${sv}" ${A.uses ? "" : "disabled"}>` +
      `<label class="check mdl-auto"><input type="checkbox" id="mdlStrengthAuto" ${auto ? "checked" : ""} ${A.uses ? "" : "disabled"}> ${esc(autoText)}</label>` +
      `<div class="help">` +
      (A.uses ? "" : `<div class="help-note">This look's workflow (${esc(famShort(A.family))}) always redraws fully, so paint strength doesn't change it. Pick a look with MOSS, Flux.1 or Krea 2 to use it.</div>`) +
      `<div>How much the AI may change what's there. Automatic uses your look's own strength.</div>` +
      `<div class="help-range"><span><b>Higher:</b> it paints more of its own: more painterly, but it starts to make things up, and logos, ` +
      `signs and small shapes change.</span><span><b>Lower:</b> closer to the original: shapes and logos kept, less restyled.</span></div>` +
      `<div class="help-rec">Recommended: 0.45–0.55, or Automatic.</div></div></div>`;
    // input scale
    const dIn = it.default_inputs;
    const inSel = `<div class="ctl ${ch("input")}"><label class="lbl" for="mdlInput">Input scale</label>` +
      (field
        ? `<select id="mdlInput" disabled><option>The atlas' own size</option></select>` +
          `<div class="hint">A field model is painted on its own texture sheet (its atlas), which is made at its final size ` +
          `already: there's nothing to enlarge first.</div>`
        : `<select id="mdlInput"><option value="" ${ps.input == null ? "selected" : ""}>Preview ${dIn.preview}×, Final ${dIn.final}× (default)</option>` +
          [1, 2, 4, 8].map((x) => `<option value="${x}" ${ps.input === x ? "selected" : ""}>${x}×${x === 1 ? " (as the game has them)" : ""}</option>`).join("") +
          `</select><div class="ctl-help"><div>How many times the original textures are enlarged (by the AI upscaler) before the ` +
          `AI paints them. They're small (128–256 pixels), so a bigger canvas gives the painting room for detail.` +
          (scene ? " Each step up gives the AI four times the pixels to paint, so it takes about four times as long." : "") + `</div>` +
          `<div class="help-range"><span><b>Higher:</b> finer detail, slower; 8× is rarely sharper in the game.</span>` +
          `<span><b>Lower:</b> faster, blurrier.</span></div><div class="help-rec">Recommended: 4×.</div></div>`) + `</div>`;
    // output scale
    const outSel = `<div class="ctl ${ch("output")}"><label class="lbl" for="mdlOutput">Output scale</label><select id="mdlOutput">` +
      (field
        ? [["", "1× the atlas (default)"], ["0.5", "½× the atlas (smaller)"], ["2", "2× the atlas (larger)"]]
          .map(([v, l]) => `<option value="${v}" ${String(ps.output ?? "") === v ? "selected" : ""}>${l}</option>`).join("")
        : `<option value="" ${ps.output == null ? "selected" : ""}>Same as input (default)</option>` +
          [1, 2, 4, 8, 16].map((x) => `<option value="${x}" ${ps.output === x ? "selected" : ""}>${x}× the original</option>`).join("")) +
      `</select>` + (PF.out_capped || PP.out_capped
        ? `<div class="hint">Some of its textures would be over 8192 pixels at this size: they're capped at 8192, the most the game can safely hold.</div>` : "") +
      `<div class="ctl-help"><div>The size of the textures that go into the game, as times the original's. ` +
      (field ? "For field models: times the atlas they're painted on." : "Different from the input scale, the painting is enlarged (by the AI upscaler) or shrunk to it after painting. " +
        "16× makes 4096-pixel files from 256-pixel textures: sharper only very close up, and slower to load in the game. " +
        "Nothing goes over 8192 pixels.") +
      `</div><div class="help-range"><span><b>Higher:</b> sharper up close, but bigger files that load slower in the game.</span>` +
      `<span><b>Lower:</b> smaller and quicker to load, softer.</span></div>` +
      `<div class="help-rec">Recommended: ${field ? "1×" : "same as input, or 4×–8×"}.</div></div></div>`;
    // the upscalers: the Studio's own list (app.js upscalerChoices: ComfyUI's upscale models, SeedVR2, ImageMagick,
    // Upscayl's models; never SYW, which is field art), a saved choice that's gone marked "(missing)"
    const upSel = (key, label, used, why, what, syw = null) => {
      const cur = ps[key] || "";
      const { ups, labels, local } = typeof upscalerChoices === "function" ? upscalerChoices(cur, { seedvr2: true, syw: false })
        : { ups: [], labels: {}, local: null };
      const online = S.models && S.models.online;
      const list = ups.filter((u) => u && u !== DEFAULT_UP && u !== "syw");
      if (cur && cur !== "lanczos" && cur !== "syw" && !list.includes(cur)) list.unshift(cur);
      const known = new Set(ups);
      const opts = [`<option value="" ${cur === "" ? "selected" : ""}>${esc(upShort(DEFAULT_UP))} (default)</option>`]
        .concat(syw ? [syw.option] : [])
        .concat([`<option value="lanczos" ${cur === "lanczos" ? "selected" : ""}>Plain resize (no upscaler)</option>`])
        .concat(list.map((u) => `<option value="${esc(u)}" ${u === cur ? "selected" : ""}>${esc(labels[u] || upShort(u))}` +
          `${known.has(u) ? "" : "  (missing)"}</option>`));
      if (local && !local.upscayl) opts.push(`<option disabled>Upscayl (not installed)</option>`);
      const notes = [];
      if (!online) notes.push("ComfyUI isn't reachable, so its upscalers (and SeedVR2) aren't listed.");
      if (local && !local.upscayl) notes.push("Upscayl isn't installed: install it (or put upscayl-bin in tools/upscayl) to list its models here.");
      if (syw && syw.note) notes.unshift(syw.note);
      const extra = typeof isOutside === "function" && isOutside(cur) && typeof outsideHint === "function" ? " " + outsideHint(cur, local) : "";
      return `<div class="ctl ${ch(key)} ${used ? "" : "mdl-off"}"><label class="lbl" for="mdl_${key}">${label}</label>` +
        `<select id="mdl_${key}" ${used ? "" : "disabled"}>${opts.join("")}</select>` +
        (used ? "" : `<div class="hint">${why}</div>`) +
        `<div class="ctl-help"><div>${what}${extra}</div>` +
        `<div class="help-range">` + (syw ? `<span><b>SYW Unified:</b> a ready-made, faithful 4× of the original by the SYW ` +
          `team, cleaner than any upscaler, with readable signs: a cleaner start for the AI. Textures the pack doesn't have get ` +
          `4x-UltraSharp. It's Square Enix's art upscaled by the SYW team: fine to use on this PC, not to share.</span>` : "") +
        `<span><b>4x-UltraSharp:</b> sharp and fast.</span>` +
        `<span><b>SeedVR2:</b> slower and smoother; it can look better on photo-like textures, such as skies and rock.</span>` +
        `<span><b>Plain resize:</b> no upscaler: keeps the original's blockiness for the AI to repaint.</span></div>` +
        `<div class="help-rec">Recommended: ${syw && syw.have ? "SYW Unified (the pack has " + (syw.have === syw.total ? "all of it" : "some of it") + "), else 4x-UltraSharp" : "4x-UltraSharp"}.</div></div>` +
        (notes.length ? `<div class="hint">${notes.join(" ")}</div>` : "") + `</div>`;
    };
    const inputs = it.inputs, minIn = Math.min(inputs.preview, inputs.final);
    const Y = it.syw || { pack: null, have: 0, total: 0, missing: [] };
    const sywCur = ps.up_in === "syw";
    const sywChoice = field ? null : !Y.pack
      ? { have: 0, total: Y.total, option: `<option value="syw" ${sywCur ? "selected" : "disabled"}>SYW Unified (battle textures): not installed` +
            `${sywCur ? " (4x-UltraSharp is used instead)" : ""}</option>`,
          note: "SYW Unified Battle Textures wasn't found in 7th Heaven's library: install it there to use it here." }
      : !Y.have
        ? { have: 0, total: Y.total, option: `<option value="syw" ${sywCur ? "selected" : "disabled"}>SYW Unified (battle textures): not in the pack` +
              `${sywCur ? " (4x-UltraSharp is used instead)" : ""}</option>`,
            note: "" }
        : { have: Y.have, total: Y.total, note: "",
            option: `<option value="syw" ${sywCur ? "selected" : ""}>SYW Unified (battle textures): ` +
              (Y.have === Y.total ? `all ${Y.total} texture${Y.total === 1 ? "" : "s"}` : `${Y.have} of ${Y.total}: the rest 4x-UltraSharp`) + `</option>` };
    const upIn = field
      ? upSel("up_in", "Upscaler before painting", false, "A field model is painted on its atlas at the atlas' own size: " +
          "nothing is enlarged first.", "Enlarges the original textures to the input scale before the AI paints them.")
      : upSel("up_in", "Upscaler before painting", Math.max(inputs.preview, inputs.final) > 1 || sywCur,
          "Not used at 1×: the textures are painted at the size the game has them, so nothing is enlarged first.",
          "Enlarges the original textures to the input scale before the AI paints them: a sharper start gives the AI more " +
          "to work from. Enlarged textures are kept, so it runs once per texture, scale and upscaler.", sywChoice);
    const outUsed = field ? (ps.output || 1) > 1 : !!ps.output && ps.output > minIn;
    const upOut = upSel("up_out", "Upscaler for the game size", outUsed, field
        ? "Not used now: the game gets the atlas at the size it's painted, or smaller. It's used when Output scale is 2×."
        : "Not used now: the game gets the textures at the size they're painted, or smaller (a plain resize). It's used when " +
          "Output scale is bigger than Input scale.",
      "Enlarges the painted textures when the game gets them bigger than they were painted (Output scale above Input scale)." +
        (!field && ps.output && ps.output > minIn && ps.output <= Math.max(inputs.preview, inputs.final)
          ? ` With the default input scale that's only for Preview (it paints at ${inputs.preview}×).` : ""));
    // the prompt: the user's words and the automatic description
    const descOn = ps.describe == null ? A.describe : ps.describe;
    const words = `<div class="ctl ${ch("prompt")}"><label class="lbl" for="mdlPrompt">Your words</label>` +
      `<textarea id="mdlPrompt" rows="3" maxlength="400" spellcheck="false" placeholder="e.g. rainy marshland at dusk, moss on the rocks">${esc(ps.prompt)}</textarea>` +
      `<div class="ctl-help">${helpText("What you'd like the AI to know about " + (scene ? "this place" : "it") + ": what it is, the " +
        "mood, the weather, the time of day. They go into its prompt after your look's own words, for every " +
        (scene ? "texture" : "view") + ". A few plain words work best; the AI can still get them wrong.", "blank, or a few words")}</div></div>` +
      `<label class="check ctl ${ch("describe")}"><input type="checkbox" id="mdlDescribe" ${descOn ? "checked" : ""}><span>Also describe it automatically` +
      `<div class="ctl-help">${helpText("Before painting, the AI looks at " + (scene ? "the scene from its battle camera" : "the model") +
        " and writes down what it sees; that description goes into the prompt too, after your words. Off: only your look's " +
        "words and yours.", `${A.describe ? "on" : "off"}, as your look has it`)}</div></span></label>` +
      `<div class="ctl"><span class="lbl">What the AI was told about it last time, automatically</span>` +
      `<div class="mdl-quote">${it.last_words ? esc(it.last_words) : `<span class="hint">${it.versions.length ? "Nothing: the last paint didn't describe it." : "Nothing yet: it's described when it's first painted."}</span>`}</div></div>`;
    const html = `<h2>Paint</h2>` +
      `<div class="ctl"><label class="lbl" for="mdlLook">Look</label><select id="mdlLook">${opts.join("")}</select>` +
      `<div class="ctl-help">${helpText("Which of your looks paints it: the same workflow, prompt and repaint strength your fields use. " +
        "The AI is told first that the picture is " + (scene ? "a battle arena" : "a character or object model") +
        ", so a look made for backgrounds works here too. Remembered for this one.", "the default look")}</div></div>` +
      `<div class="subhead mdl-sub-row"><span>Settings for ${esc(it.title)}</span>` +
      `<button class="linkish" id="mdlPaintReset" ${allDefault ? "disabled" : ""} title="Paint strength, input and output scale, your words and the automatic description back to their defaults">Back to defaults</button></div>` +
      methodSel + extras + `<div class="subhead">Painting</div>` + strength + inSel + upIn + outSel + upOut +
      (Math.max(PF.max_side || 0, PP.max_side || 0) > 4096
        ? `<div class="model-warn">Very big textures (up to ${Math.max(PF.max_side || 0, PP.max_side || 0)} pixels): the game may run short of memory with many of them.</div>` : "") +
      words +
      `<div class="ctl"><span class="lbl">Preview and Paint final</span>` +
      `<div class="ctl-help">${helpText(what + " Both use the settings above, go in the Studio's queue with the fields' renders, " +
        "and each makes a new version under the picture. Neither changes your game: that's Install, below.",
        "a Preview first, then Paint final when you like it")}</div></div>`;
    fill(box, html, (el) => {
      el.querySelector("#mdlLook").onchange = async (e) => {
        const v = e.target.value;
        if (v === "__manage") {
          e.target.value = it.look || "";
          if (typeof openLooks === "function") openLooks();
          return;
        }
        try {
          setItem(await api(`${API}/look`, { body: { kind: it.kind, id: it.id, look: v || null } }));
          toast(`${it.title} now paints with ${v ? `"${lookName(v)}"` : "the default look"}`);
        } catch (err) { toast(err.message); }
      };
      const range = el.querySelector("#mdlStrength input[type=range]"), autoBox = el.querySelector("#mdlStrengthAuto");
      range.oninput = () => { el.querySelector("#mdlStrength .top b").textContent = Number(range.value).toFixed(2); autoBox.checked = false; };
      range.onchange = () => saveSettings({ strength: Number(range.value) });
      autoBox.onchange = () => saveSettings({ strength: autoBox.checked ? null : Number(range.value) });
      const pano = el.querySelector("#mdlPano"), det = el.querySelector("#mdlDetail"), seams = el.querySelector("#mdlSeams");
      if (!pano.disabled) pano.onchange = () => saveSettings({ panorama: pano.checked });
      if (!det.disabled) det.onchange = () => saveSettings({ detail: Number(det.value) });
      if (!seams.disabled) seams.onchange = () => saveSettings({ seams: seams.checked });
      const methodEl = el.querySelector("#mdlMethod");
      if (!field) methodEl.onchange = () => saveSettings({ method: methodEl.value || it.default_method });
      const inSelEl = el.querySelector("#mdlInput");
      if (!field) inSelEl.onchange = () => saveSettings({ input: inSelEl.value ? Number(inSelEl.value) : null });
      const outEl = el.querySelector("#mdlOutput");
      outEl.onchange = () => saveSettings({ output: outEl.value ? Number(outEl.value) : null });
      for (const k of ["up_in", "up_out"]) {
        const sel = el.querySelector(`#mdl_${k}`);
        if (sel && !sel.disabled) sel.onchange = () => saveSettings({ [k]: sel.value || null });
      }
      const ta = el.querySelector("#mdlPrompt");
      ta.oninput = () => {
        clearTimeout(M.promptTimer);
        M.promptTimer = setTimeout(() => saveSettings({ prompt: ta.value }, true), 700);
      };
      ta.onblur = () => {
        if (M.promptTimer) { clearTimeout(M.promptTimer); M.promptTimer = null; saveSettings({ prompt: ta.value }); }
        else if (M.paintStale) renderPaint();
      };
      el.querySelector("#mdlDescribe").onchange = (e) => saveSettings({ describe: e.target.checked === A.describe ? null : e.target.checked });
      el.querySelector("#mdlPaintReset").onclick = () => saveSettings({ reset: true });
    });
  }

  // save one of the model's paint settings (quiet: while typing, no toast)
  async function saveSettings(change, quiet = false) {
    const it = M.item;
    if (!it) return;
    M.promptTimer = null;
    try {
      const r = await api(`${API}/settings`, { body: { kind: it.kind, id: it.id, ...change } });
      if (M.item && M.item.id === r.id && M.item.kind === r.kind) {
        M.item = r;
        renderPaint();
        renderFoot();
      }
      if (!quiet) toast(change.reset ? `${it.title}: paint settings back to their defaults` : `Saved for ${it.title}`);
    } catch (e) { toast(e.message); }
  }

  function renderGame() {
    const it = M.item;
    if (!it) return;
    let html = `<h2>In the game</h2>`;
    if (it.kind === "field") {
      html += `<div class="mdl-line">Not in the game: field models can't be installed yet.</div>` +
        `<div class="row-btns tight"><button class="small" disabled title="Field models can't be installed yet">Install</button></div>` +
        `<div class="model-warn">The game's mod loader needs a change first: in some fields it would draw the model at the ` +
        `wrong size. Installing would also replace the chibi field models you play with now. Painting and looking at them ` +
        `here is safe: nothing in your game changes.</div>`;
      fill($("#mdlGame"), html);
      return;
    }
    const inst = it.installed, cur = it.versions.find((v) => v.current);
    if (inst) {
      const v = it.versions.find((x) => x.id === inst.version);
      const same = cur && cur.id === inst.version;
      html += `<div class="mdl-line"><span class="ok">In the game</span>: ${v ? `${esc(verLabel(v))} from ${fmtWhen(v.created, true)}` : "a version since deleted"}` +
        ` (${inst.files} texture${inst.files === 1 ? "" : "s"}, since ${fmtWhen(inst.at, true)}).</div>` +
        (same ? "" : `<div class="mdl-line warn">That isn't the version marked ★: Install again to put the ★ one in.</div>`);
    } else if (!it.paintable) {
      html += it.kind === "battle"
        ? `<div class="mdl-line">Nothing to put in the game from here: it has no textures. Its 2D painting has its own Install, in Paint in 2D style.</div>`
        : `<div class="mdl-line">Nothing to put in the game: it can't be painted (see above).</div>`;
    } else {
      html += `<div class="mdl-line">${cur ? "Not in the game: the game shows the original." : "Nothing painted yet: Preview or Paint final first."}</div>`;
    }
    const canInstall = it.can_install && !!cur;
    html += `<div class="ctl"><span class="lbl">Install and Uninstall</span>` +
      `<div class="row-btns tight"><button class="small primary" data-a="install" ${canInstall ? "" : "disabled"}>${inst ? "Install again" : "Install"}</button>` +
      `<button class="small" data-a="uninstall" ${inst ? "" : "disabled"}>Uninstall</button></div>` +
      `<div class="ctl-help">${helpText("Install copies the painted textures of the version marked ★ into your game's texture " +
        `folder (${esc(it.dest || "")}), so the game shows them the next time it loads ` + (it.kind === "scene" ? "a battle here" : "this model in a battle") +
        ". A file from another mod that it would replace is kept aside, and Uninstall takes out exactly what Install copied and " +
        "puts those back: the game looks as it did before. Nothing goes in by itself. If you also have SYW Unified Battle " +
        "Textures turned on in 7th Heaven, turn it off, or 7th Heaven's versions may show instead of these.", "install a Final you like; uninstall to compare with the original in the game")}</div></div>` +
      (it.game_files && it.game_files.length ? `<div class="hint" title="${esc(it.game_files.join("\n"))}">Its files in the game: ` +
        `${esc(it.game_files.slice(0, 3).join(", "))}${it.game_files.length > 3 ? ` and ${it.game_files.length - 3} more` : ""}.</div>` : "") +
      (it.mod_note ? `<div class="model-warn">${esc(it.mod_note)}</div>` : "") +
      (it.kind === "scene" && it.paintable ? `<div id="mdlBattle"></div>` : "");
    fill($("#mdlGame"), html, (el) => {
      el.querySelector('[data-a="install"]').onclick = installIt;
      el.querySelector('[data-a="uninstall"]').onclick = uninstallIt;
    });
    renderBattle();
  }

  /* ---------------------------------------------------------- try it in a battle (battle scenes) */

  function renderBattle() {
    const it = M.item, box = $("#mdlBattle");
    if (!it || !box) return;
    const inst = it.installed, cur = it.versions.find((v) => v.current);
    const iv = inst && it.versions.find((v) => v.id === inst.version);
    const installed = inst
      ? `<span class="ok">This scene's painted textures are in the game</span>${iv ? ` (${esc(verLabel(iv))} from ${fmtWhen(iv.created, true)})` : ""}` +
        `, so the battle shows them${cur && cur.id !== inst.version ? "; the ★ version isn't the one installed" : ""}.`
      : `<span class="warn">This scene's painted textures aren't in the game</span>: the battle shows the original. ` +
        "Install a version above first to see the painting.";
    const key = `models.formation.${it.id}`;
    const want = Number(pref(key, it.formations.length ? it.formations[0].id : 0));
    const fid = it.formations.some((f) => f.id === want) ? want : (it.formations[0] || {}).id;
    const html = `<div class="subhead">Try it in a battle</div><div class="mdl-line">${installed}</div>` +
      (it.formations.length
        ? `<div class="ctl"><label class="lbl" for="mdlFormation">Battle</label><select id="mdlFormation">` +
          it.formations.map((f) => `<option value="${f.id}" ${f.id === fid ? "selected" : ""}>${esc(f.label)} · ${f.id}</option>`).join("") +
          `</select><div class="ctl-help">${helpText("Which of the battles fought here to start: its enemies and how many, and how the " +
            "fight begins when it isn't the usual way (a back attack, a side attack…). The battles with this scene's most common " +
            "enemies come first.", "the first one, the battle you'd most often meet here")}</div></div>`
        : `<div class="hint">No battle with named enemies is fought in this scene, so there's none to start from here.</div>`) +
      `<label class="check ctl"><input type="checkbox" id="mdlGod" ${M.god ? "checked" : ""}><span>Keep the party alive (god mode)` +
      `<div class="ctl-help">${helpText("Keeps your party's HP and MP full all through the battle, so you can't lose while you look " +
        "around. Off: they take damage as usual.", "on")}</div></span></label>` +
      `<label class="check ctl"><input type="checkbox" id="mdlAuto" ${M.auto ? "checked" : ""}><span>Auto-attack` +
      `<div class="ctl-help">${helpText("Your party attacks by itself, so the battle plays out without you pressing anything. " +
        "Off: you fight it yourself, or run away.", "off, unless you only want to watch")}</div></span></label>` +
      `<div class="ctl"><span class="lbl">Start it in your game</span>` +
      `<div class="row-btns tight"><button class="small primary" id="mdlStartBattle">Start this battle</button></div>` +
      `<div class="ctl-help">${helpText("Starts a real battle in your running game, here in this scene, seen from the battle's own " +
        "camera, so you see the scene the way the game shows it (the painting only if it's installed, see above). Your game " +
        "has to be walking around a field, not in a menu, a battle or a movie, and goes back there when the battle ends. The " +
        "battle plays out normally: win it or run away. The experience and items you get from it do count on your save, so if " +
        "you don't want them, don't save afterwards. Nothing is sent to your game until you press it.",
        "god mode on, with the painted version installed")}</div></div>` +
      `<div class="hint" id="mdlBattleStatus"></div><div class="mdl-line" id="mdlBattleResult"></div>`;
    fill(box, html, (el) => {
      const sel = el.querySelector("#mdlFormation");
      if (sel) sel.onchange = () => setPref(key, sel.value);
      el.querySelector("#mdlGod").onchange = (e) => { M.god = e.target.checked; setPref("models.god", M.god ? "1" : "0"); };
      el.querySelector("#mdlAuto").onchange = (e) => { M.auto = e.target.checked; setPref("models.auto", M.auto ? "1" : "0"); };
      el.querySelector("#mdlStartBattle").onclick = startBattle;
    });
    battleStatus();
  }

  // the running game, as the Studio last heard (every 2 s while a battle scene is shown), and the button with it
  function battleStatus() {
    const it = M.item, el = $("#mdlBattleStatus"), btn = $("#mdlStartBattle"), res = $("#mdlBattleResult");
    if (!it || !el || !btn) return;
    const g = M.game;
    let text, ready = false;
    if (!g) text = "Checking whether your game is running…";
    else if (!g.running) {
      text = g.age == null ? "Your game isn't running with FFNx-3d (it hasn't written its status file), so this is off."
        : `Your game isn't running with FFNx-3d (no news from it for ${fmtDuration(g.age)}), so this is off.`;
    } else if (g.mode === 1) {
      text = `Your game is in a battle now${g.battle ? ` (battle ${g.battle.split(" ")[0]})` : ""}: finish it or run away, then start another.`;
    } else if (g.mode === 0) {
      text = `Your game is running${g.field ? `, in ${g.field}` : ""}: ready.`;
      ready = true;
    } else {
      text = "Your game is running, but not walking around a field (a menu or a movie?): it starts a battle only from a field.";
      ready = true;  // the game says for itself if it can't
    }
    if (M.battleBusy) text = "Sending it to your game…";
    if (el.textContent !== text) el.textContent = text;
    el.classList.toggle("warn", !ready && !M.battleBusy);
    const off = !ready || M.battleBusy || !it.formations.length;
    if (btn.disabled !== off) btn.disabled = off;
    btn.title = !it.formations.length ? "No battle to start in this scene" : !ready ? text : "Start the battle picked above in your running game";
    const r = M.battleResult && M.battleResult.id === it.id ? M.battleResult : null;
    const rhtml = r ? `<span class="${r.ok ? "ok" : r.ok === false ? "bad" : "warn"}">${esc(r.message)}</span>` : "";
    if (res.dataset.html !== rhtml) { res.innerHTML = rhtml; res.dataset.html = rhtml; }
  }

  async function pollGame() {
    if (M.open && M.item && M.item.kind === "scene" && M.item.paintable) {
      try { M.game = await api(`${API}/game`); } catch (e) { M.game = null; }
      battleStatus();
    }
    setTimeout(pollGame, 2000);
  }

  async function startBattle() {
    const it = M.item, sel = $("#mdlFormation");
    if (!it || !sel || M.battleBusy) return;
    const fid = Number(sel.value), label = sel.options[sel.selectedIndex].text;
    M.battleBusy = true;
    M.battleResult = null;
    battleStatus();
    let r;
    try {
      r = await api(`${API}/battle`, { body: { id: it.id, formation: fid, god: M.god, autoattack: M.auto } });
    } catch (e) {
      r = { ok: false, message: e.message };
    }
    M.battleBusy = false;
    M.battleResult = { id: it.id, ok: r.ok, message: r.ok ? `${r.message} (${label}; god mode ${M.god ? "on" : "off"}, ` +
      `auto-attack ${M.auto ? "on" : "off"})` : r.message };
    toast(r.ok ? `Battle starting in your game: ${label}` : r.message, 7000);
    try { M.game = await api(`${API}/game`); } catch (e) { /* the next poll */ }
    battleStatus();
  }

  async function installIt() {
    const it = M.item, cur = it.versions.find((v) => v.current);
    if (!cur) return;
    const ok = await ask(`Put ${it.title}'s painted textures (${verLabel(cur)} from ${fmtWhen(cur.created, true)}) into the game?\n\n` +
      `They're copied to ${it.dest}. The game shows them the next time it loads ` +
      (it.kind === "scene" ? "a battle here" : "this model in a battle") + `. Uninstall takes them out again.` +
      (it.mod_note ? `\n\n${it.mod_note}` : "") +
      (it.syw && it.syw.pack && it.syw.have ? "\n\nIf SYW Unified Battle Textures is turned on in 7th Heaven, turn it off, or 7th Heaven's versions of these textures may show instead." : ""),
      it.installed ? "Install again" : "Install");
    if (!ok) return;
    try {
      const r = await api(`${API}/install`, { body: { kind: it.kind, id: it.id } });
      setItem(r.item);
      reloadListSoon();
      toast(`Installed: ${r.copied} texture${r.copied === 1 ? "" : "s"} copied into the game` +
        (r.replaced ? `; ${r.replaced} file${r.replaced === 1 ? "" : "s"} from another mod kept aside for Uninstall` : ""), 7000);
    } catch (e) { toast(e.message); }
  }

  async function uninstallIt() {
    const it = M.item;
    if (!it.installed) return;
    const ok = await ask(`Take ${it.title}'s painted textures out of the game?\n\nThe ${it.installed.files} file` +
      `${it.installed.files === 1 ? "" : "s"} Install copied are removed` +
      (it.installed.replaced ? `, and the ${it.installed.replaced === 1 ? "file it replaced is" : `${it.installed.replaced} files it replaced are`} put back` : "") +
      `. The game goes back to the original look the next time it loads them.`, "Uninstall");
    if (!ok) return;
    try {
      const r = await api(`${API}/uninstall`, { body: { kind: it.kind, id: it.id } });
      setItem(r.item);
      reloadListSoon();
      toast(`Uninstalled: ${r.removed} file${r.removed === 1 ? "" : "s"} removed` + (r.restored ? `, ${r.restored} put back` : ""));
    } catch (e) { toast(e.message); }
  }

  /* ---------------------------------------------------------- the battle look (battle scenes) */

  // The four parts (models.py LOOK_GROUPS): what each does, on and off, what to pick. The ticked ones (remembered per
  // scene) are what Build works out and Install puts in.
  const BL = [
    { key: "light", name: "Light from the painting", ai: false, rec: "on",
      help: "Lights the party, the enemies and the scene the way the painting is lit: the light from all round is taken " +
        "from the painted scene, and a sun shines from the brightest side of the painted sky. A dim factory then lights " +
        "them dimly, a sunny meadow warmly. Off: battles here keep the game's usual plain light, the same in every battle." },
    { key: "haze", name: "Haze & colour match", ai: false, rec: "on",
      help: "Distant things fade a little into the colour of the painting's air, and the party's and the enemies' colours " +
        "are matched to the painting, so they look like they belong in it instead of pasted on top. Off: the characters " +
        "keep their own colours and nothing fades with distance." },
    { key: "life", name: "Life", ai: true, rec: "on, built with the game closed",
      help: "A little movement: the painted sky slowly drifts, grass sways where there is grass, and dust, embers, snow " +
        "or fireflies drift in the air where the painting suggests them. The AI looks at the scene to tell what's sky, " +
        "what's grass and which of these fit, so the game has to be closed while it builds. Without the AI it's worked " +
        "out less well: only sky that's clearly sky drifts, grass is found by its colour alone, and the particles come " +
        "from the scene's description. Off: the scene stays still." },
    { key: "normals", name: "Surface relief", ai: true, rec: "on",
      help: "Gives the painted ground and walls bumps and grooves that catch the light, so stones, bricks and cracks " +
        "look raised instead of flat (the sky stays as it is). The AI works it out from each painted texture, so the " +
        "game has to be closed while it builds; it's the slowest part. It shows only on painted textures that are in " +
        "the game, so install the painting too. Off: the surfaces look flat, as painted." },
  ];
  const BL_NAME = Object.fromEntries(BL.map((p) => [p.key, p.name]));
  const BL_AI = new Set(BL.filter((p) => p.ai).map((p) => p.key));
  // the preview pictures a build makes: their caption and what they show
  const BL_PICS = {
    "preview_light.png": { cap: "The light", title: "the light from the painting",
      text: "Top: the painted scene all round from the middle of the fight, as the lighting sees it (the middle looks " +
        "toward the enemies, the edges back past the party; the sky is at the top). Bottom: the light that reaches the " +
        "characters from each direction, blurred: brighter where more light comes from." },
    "preview_view.jpg": { cap: "What the AI saw", title: "the scene the AI looked at",
      text: "The painted scene from the main battle camera: what the AI looked at to choose the drifting particles." },
    "preview_normals.jpg": { cap: "Surface relief", title: "surface relief",
      text: "Painted textures, each next to its relief lit from one side: raised parts catch the light and grooves fall " +
        "into shade. In the game the relief catches the scene's own light." },
  };
  const blKey = (id) => `models.blparts.${id}`;
  function blParts(id) {
    try {
      const v = JSON.parse(pref(blKey(id), "null"));
      if (Array.isArray(v)) return BL.map((p) => p.key).filter((k) => v.includes(k));
    } catch (e) { /* not set yet */ }
    return BL.map((p) => p.key);
  }
  // "Light from the painting, Life and Surface relief"
  const blList = (keys) => {
    const n = keys.map((k) => BL_NAME[k] || k);
    return n.length > 1 ? `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}` : n.join("");
  };
  const verName = (id) => {
    const v = M.item && M.item.versions.find((x) => x.id === id);
    return v ? `${verLabel(v)} from ${fmtWhen(v.created, true)}` : "a version since deleted";
  };
  const blPic = (id, vid, name, t) =>
    `${API}/battlelook/${encodeURIComponent(id)}/${encodeURIComponent(vid)}/${name}?t=${Math.round(t || 0)}`;

  async function loadBattleLook() {
    const it = M.item;
    if (!it || it.kind !== "scene" || !it.paintable) return;
    const seq = ++M.blSeq;
    let r;
    try { r = await api(`${API}/battlelook/${encodeURIComponent(it.id)}`); } catch (e) { r = { id: it.id, error: e.message }; }
    if (seq !== M.blSeq || !M.item || M.item.id !== it.id) return;
    M.bl = r;
    renderBattleLook();
  }

  // this scene's battle look builds in the Studio's queue
  function lookJobs(id = M.id) {
    const q = S.queue, name = `${KINDS.scene.job} ${id}`;
    if (!q) return { running: null, queued: [] };
    const mine = (j) => j && j.name === name && j.kind === "model_look";
    return { running: mine(q.current) ? q.current : null, queued: q.queued.filter(mine) };
  }

  function renderBattleLook() {
    const it = M.item, box = $("#mdlBl");
    const on = !!it && it.kind === "scene" && it.paintable;
    if (box.hidden === on) box.hidden = !on;
    if (!on) return;
    const L = M.bl && M.bl.id === it.id ? M.bl : null;
    const head = `<h2>Battle look</h2><div class="mdl-lines"><div>Battles here can look like the painting beyond its ` +
      `textures: lit by its light, in its air, with a little movement, and relief on the ground and walls. It's worked ` +
      `out from the ★ painted version; nothing in your game changes until you install it.</div></div>`;
    if (!L) { fill(box, head + `<div class="hint">Reading it…</div>`); return; }
    if (L.error) { fill(box, head + `<div class="model-warn">${esc(L.error)}</div>`); return; }
    const b = L.build, I = L.installed, cur = L.current;
    const jobs = lookJobs(it.id), queued = !!(jobs.running || jobs.queued.length);
    const picked = blParts(it.id);
    const s = (n) => (n === 1 ? "" : "s");
    // what's built for the ★ version
    let state;
    if (!cur) {
      state = `<div class="mdl-line">Nothing painted yet: the battle look is worked out from a painted version, so ` +
        `Preview or Paint final first.</div>`;
    } else if (b) {
      state = `<div class="mdl-line"><span class="ok">Built</span> for the ★ version (${esc(verName(cur))}) on ${fmtWhen(b.built, true)}.</div>` +
        (b.stale.length ? `<div class="model-warn">The ★ version's painted textures changed after it was built ` +
          `(${b.stale.length}: ${esc(b.stale.slice(0, 3).join(", "))}${b.stale.length > 3 ? "…" : ""}): Build again so the ` +
          `look matches them.</div>` : "");
    } else {
      const others = L.builds.filter((v) => v !== cur);
      state = `<div class="mdl-line">Not built yet for the ★ version (${esc(verName(cur))}).` +
        (others.length ? ` It was built for ${esc(others.map(verName).join("; "))}: build again for the ★ one.` : "") + `</div>`;
    }
    const last = L.last;
    if (last && !last.ok && last.message !== "cancelled" && !(b && b.built > last.at)) {
      state += `<div class="model-warn" style="color:var(--bad)">The last build failed: ${esc(last.message)}</div>`;
    }
    const pics = b && b.previews.length ? `<div class="mdl-bl-prev">${b.previews.map((n) =>
      `<figure data-p="${n}" title="${esc(BL_PICS[n].text)}"><img alt="" loading="lazy" src="${blPic(it.id, cur, n, b.built)}">` +
      `<figcaption>${BL_PICS[n].cap}</figcaption></figure>`).join("")}</div>` : "";
    // the parts: what each has, built and in the game
    const inGame = new Set(I ? I.groups : []);
    const parts = BL.map((p) => {
      const built = !!(b && b.groups[p.key]);
      let what = !b ? "" : built ? `Built. ${esc(b.lines[p.key] || "")}` : esc(b.lines[p.key] || "Not built yet.");
      if (built && p.key === "haze" && b.haze_color) {
        what = `<span class="mdl-swatch" title="The painting's air colour" style="background:rgb(` +
          `${b.haze_color.map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)).join(",")})"></span> ${what}`;
      }
      if (inGame.has(p.key)) what += ` <span class="ok">In the game.</span>`;
      return `<label class="check ctl mdl-bl-part"><input type="checkbox" data-bl="${p.key}" ${picked.includes(p.key) ? "checked" : ""}>` +
        `<span>${esc(p.name)}${p.ai ? ` <span class="hint">· uses the AI</span>` : ""}` +
        (what ? `<div class="hint">${what}</div>` : "") +
        `<div class="ctl-help">${helpText(p.help, p.rec)}</div></span></label>`;
    }).join("");
    // build
    const sec = L.seconds || {}, has = (k) => picked.includes(k);
    const est = (sec.load || 0) + (has("light") || has("haze") ? sec.view || 0 : 0) + (has("light") ? sec.light || 0 : 0) +
      (has("haze") ? sec.haze || 0 : 0) + (has("life") ? sec.life_ai || 0 : 0) + (has("normals") ? sec.normals || 0 : 0);
    const aiPicked = picked.filter((k) => BL_AI.has(k)), g = M.game;
    const canBuild = !!cur && picked.length > 0 && !queued;
    const buildTip = !cur ? "Paint a version first" : !picked.length ? "Tick at least one part" : queued
      ? "It's in the queue already (see below)" : `Work out ${blList(picked)} from the ★ painted version`;
    const build = `<div class="ctl"><span class="lbl">Build</span>` +
      `<div class="row-btns tight"><button class="small primary" id="mdlBlBuild" ${canBuild ? "" : "disabled"} ` +
      `title="${esc(buildTip)}">${b ? "Build again" : "Build"}</button></div>` +
      (cur && picked.length && est ? `<div class="hint">The ticked parts take about ${fmtDuration(est)}` +
        `${has("normals") && sec.normals > est / 2 ? ", most of it the surface relief" : ""}.</div>` : "") +
      (g && g.running && aiPicked.length ? `<div class="model-warn">Your game is running: ${esc(blList(aiPicked))} ` +
        `${aiPicked.length === 1 ? "uses" : "use"} the AI, which waits until the game is closed. Build asks what to do.</div>` : "") +
      `<div class="mdl-line" id="mdlBlProg"></div>` +
      `<div class="ctl-help">${helpText("Works out the ticked parts from the ★ painted version and keeps them here: nothing " +
        "in your game changes until you install. It goes in the Studio's queue with the other jobs. Building again " +
        "replaces only the ticked parts; the others stay as they were built. Build again when you mark a new painted " +
        "version ★: the look belongs to the painting it was worked out from.", "all four, with the game closed")}</div></div>`;
    // install and uninstall
    let where;
    if (I) {
      where = `<div class="mdl-line"><span class="ok">In the game</span>: ${esc(blList(I.groups) || "nothing")} ` +
        `(built for ${esc(verName(I.version))}), since ${fmtWhen(I.at, true)}.</div>` +
        (cur && I.version !== cur ? `<div class="mdl-line warn">That's the look of another painted version, not the ★ one: ` +
          `Install again to put the ★ one's in.</div>` : "") +
        (I.normals_skipped ? `<div class="model-warn">Surface relief for ${I.normals_skipped} texture${s(I.normals_skipped)} ` +
          `wasn't put in: ${I.normals_skipped === 1 ? "its painted texture wasn't" : "their painted textures weren't"} in the ` +
          `game. Install the painting (above), then Install this again.</div>` : "") +
        (I.groups.includes("normals") && !L.painting_installed
          ? `<div class="model-warn">The surface relief is in the game but this scene's painting isn't, and the relief only ` +
            `shows on the painted textures: install the painting (above).</div>`
          : L.painting_installed && L.painting_installed !== I.version
            ? `<div class="model-warn">The painting in the game (${esc(verName(L.painting_installed))}) isn't the version ` +
              `this look was built from, so the light and the relief may not quite match it.</div>` : "") +
        (L.problems && L.problems.length ? `<div class="model-warn">Something in the game isn't as Install left it:</div>` +
          `<ul class="mdl-bl-probs">${L.problems.slice(0, 6).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : "");
    } else {
      where = `<div class="mdl-line">Not in the game: battles here have the game's usual light.</div>` +
        (has("normals") && b && b.groups.normals && !L.painting_installed
          ? `<div class="hint">Surface relief only shows on painted textures that are in the game, and this scene's ` +
            `painting isn't: install it (above) first, or the relief is left out.</div>` : "");
    }
    const usable = b ? picked.filter((k) => b.groups[k]) : [];
    const canInstall = !!b && usable.length > 0 && !jobs.running && !M.blBusy;
    const instTip = !b ? "Build it first" : !usable.length ? "None of the ticked parts is built yet" : jobs.running
      ? "It's being built right now" : `Put ${blList(usable)} in the game`;
    const inst = `<div class="ctl"><span class="lbl">Install and Uninstall</span>${where}` +
      `<div class="row-btns tight"><button class="small primary" id="mdlBlInstall" ${canInstall ? "" : "disabled"} ` +
      `title="${esc(instTip)}">${I ? "Install again" : "Install"}</button>` +
      `<button class="small" id="mdlBlUninstall" ${I && !M.blBusy ? "" : "disabled"} title="Take this scene's battle look out of the game">Uninstall</button></div>` +
      `<div class="ctl-help">${helpText("Install puts the ticked parts that are built into your game, for battles in this " +
        "scene: its lighting settings (only this scene's own entry in the game's lighting settings), its light files, the " +
        "drifting-particles file and the relief next to the painted textures. The next battle here shows them. If a file " +
        "or setting it would write belongs to something else, it installs nothing and tells you which. Uninstall takes out " +
        "exactly what Install put in, nothing else. Nothing goes in by itself.",
        "install all four with the painting installed; uninstall to compare with the game's usual light")}</div></div>`;
    const html = head + state + pics + `<div class="subhead">Parts</div>` +
      `<div class="hint">The ticked parts are what Build works out and Install puts in.</div>` + parts + build +
      `<div class="subhead">In your game</div>` + inst;
    fill(box, html, (el) => {
      for (const c of el.querySelectorAll("[data-bl]")) {
        c.onchange = () => {
          if (!M.item) return;
          setPref(blKey(M.item.id), JSON.stringify([...el.querySelectorAll("[data-bl]")].filter((x) => x.checked).map((x) => x.dataset.bl)));
          renderBattleLook();
        };
      }
      for (const f of el.querySelectorAll("[data-p]")) f.onclick = () => showLookPicture(f.dataset.p);
      el.querySelector("#mdlBlBuild").onclick = () => buildLook(true);
      el.querySelector("#mdlBlInstall").onclick = installLook;
      el.querySelector("#mdlBlUninstall").onclick = uninstallLook;
    });
    blProgress();
  }

  // the build's progress (every second while the page is open), outside the card's HTML so the card stays put
  function blProgress() {
    const el = $("#mdlBl #mdlBlProg"), it = M.item;
    if (!el || !it) return;
    const j = lookJobs(it.id);
    const html = j.running
      ? `Building: ${esc(j.running.stage)}<div class="mdl-bar"><i style="width:${Math.round(j.running.progress * 100)}%"></i></div>`
      : j.queued.length ? `<span class="hint">Queued: it starts when the jobs before it are done, then takes about ` +
        `${fmtDuration(j.queued[0].est || 0)}.</span>` : "";
    if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  }

  async function buildLook(ai = true) {
    const it = M.item;
    if (!it) return;
    const groups = blParts(it.id);
    if (!groups.length) { toast("Tick at least one part to build"); return; }
    let r;
    try {
      r = await api(`${API}/battlelook/build`, { body: { id: it.id, parts: groups, ai } });
    } catch (e) { toast(e.message, 7000); return; }
    if (r.game_running) {
      const life = r.ai_parts.includes("life"), relief = r.ai_parts.includes("normals");
      const rest = groups.filter((k) => k !== "normals"), plain = groups.filter((k) => !BL_AI.has(k));
      if (!rest.length) {
        await tell("Your game is running. Surface relief is worked out by the AI, which shares the graphics card with the " +
          "game, so it has to wait until the game is closed. Close the game, then press Build again.");
        return;
      }
      const msg = `Your game is running. ${life && relief ? "Life and Surface relief are" : life ? "Life is" : "Surface relief is"} ` +
        `worked out by the AI, which shares the graphics card with the game, so ${life && relief ? "they wait" : "it waits"} ` +
        "until the game is closed.\n\n" +
        "Recommended: close the game and press Build again.\n\nOr build now without the AI." +
        (life ? " Life is then worked out less well: only sky that's clearly sky drifts, grass is found by its colour " +
          "alone (dry or yellow grass is missed), and the drifting particles come from the scene's description." : "") +
        (relief ? " Surface relief is left out, as it can't be done without the AI (any relief built before is kept)." : "") +
        (plain.length ? ` ${blList(plain)} ${plain.length === 1 ? "doesn't" : "don't"} need the AI and ` +
          `${plain.length === 1 ? "comes" : "come"} out the same either way.` : "");
      if (await ask(msg, "Build without the AI")) await buildLook(false);
      return;
    }
    const aiPart = groups.some((k) => BL_AI.has(k));
    toast(r.added ? `Battle look of ${it.title} queued${aiPart && !r.ai ? " (without the AI)" : ""}` : "Already queued");
    renderBattleLook();
  }

  async function installLook() {
    const it = M.item, L = M.bl, b = L && L.build;
    if (!it || !b || M.blBusy) return;
    const picked = blParts(it.id), use = picked.filter((k) => b.groups[k]), missing = picked.filter((k) => !b.groups[k]);
    if (!use.length) { toast("None of the ticked parts is built yet: Build first"); return; }
    const reliefOut = use.includes("normals") && !L.painting_installed;
    const ok = await ask(`Put the battle look of ${it.title} into your game: ${blList(use)}?\n\n` +
      `It's the look built for the ★ version (${verName(b.version)}). The next battle here shows it, and Uninstall takes ` +
      `it out again.${L.installed ? " It replaces the battle look in the game now." : ""}` +
      (missing.length ? `\n\n${blList(missing)} ${missing.length === 1 ? "isn't built, so it's" : "aren't built, so they're"} left out.` : "") +
      (reliefOut ? "\n\nSurface relief only shows on painted textures that are in the game, and this scene's painting " +
        "isn't installed, so the relief is left out: install the painting first if you want it." : ""),
      L.installed ? "Install again" : "Install");
    if (!ok) return;
    M.blBusy = true;
    renderBattleLook();
    let err = null;
    try {
      const r = await api(`${API}/battlelook/install`, { body: { id: it.id, parts: use } });
      if (M.item && M.item.id === it.id) M.bl = r.look;
      reloadListSoon();
      toast(`Battle look in the game: ${blList(r.installed)} (${r.files} file${r.files === 1 ? "" : "s"})` +
        (r.normals_skipped ? `; the relief left out for ${r.normals_skipped} texture${r.normals_skipped === 1 ? "" : "s"} whose painting isn't in the game` : ""), 7000);
    } catch (e) { err = e.message; }
    M.blBusy = false;
    if (err) {
      await loadBattleLook();  // a refused install has taken the earlier one out first: show what's there now
      renderBattleLook();
      await tell(err);
    } else renderBattleLook();
  }

  async function uninstallLook() {
    const it = M.item, I = M.bl && M.bl.installed;
    if (!it || !I || M.blBusy) return;
    if (!await ask(`Take the battle look of ${it.title} out of your game?\n\nThe ${I.files} file${I.files === 1 ? "" : "s"} ` +
      "and the lighting settings Install put in are removed, so battles here go back to the game's usual light. Nothing " +
      "else is touched.", "Uninstall")) return;
    M.blBusy = true;
    renderBattleLook();
    let err = null;
    try {
      const r = await api(`${API}/battlelook/uninstall`, { body: { id: it.id } });
      if (M.item && M.item.id === it.id) M.bl = r.look;
      toast(`Battle look taken out of the game: ${r.removed} file${r.removed === 1 ? "" : "s"} removed` +
        (r.sections ? ", and its lighting settings" : ""), 6000);
      if (r.left.length) {
        err = `${r.left.length === 1 ? "One file was" : `${r.left.length} files were`} left in the game, because something ` +
          `changed ${r.left.length === 1 ? "it" : "them"} after Install put ${r.left.length === 1 ? "it" : "them"} there ` +
          `(in your game's folder):\n\n${r.left.join("\n")}`;
      }
    } catch (e) { err = e.message; }
    M.blBusy = false;
    renderBattleLook();
    if (err) await tell(err);
  }

  function showLookPicture(name) {
    const it = M.item, b = M.bl && M.bl.build, p = BL_PICS[name];
    if (!it || !b || !p) return;
    const url = blPic(it.id, b.version, name, b.built);
    openModal(`<div class="modal-head"><h2>${esc(it.title)} · battle look: ${esc(p.title)}</h2>` +
      `<button class="ghost small" data-a="close">Close</button></div><p class="hint">${esc(p.text)} ` +
      `<a href="${url}" target="_blank" rel="noopener">Open on its own</a></p><img class="mdl-bl-big" alt="" src="${url}">`);
  }

  /* ---------------------------------------------------------- paint in 2D style (battle models) */

  // A battle model painted as a hand-drawn 2D character (models.py p2d_*, ff7bg/models/battleatlas.py): its settings,
  // Paint (a queue job), its versions with their pictures (the ★ one is what Install puts in), Install and Uninstall.
  // A version's pictures: caption, title and what they show
  const P2D_PICS = {
    "battle.jpg": { cap: "in battle: as the game draws it | painted", title: "in battle",
      text: "The model where a battle puts it, from that battle's own camera, with the battle scene behind: on the left " +
        "as the game draws it now, on the right painted, with the flat light the game gives the painting." },
    "turntable.jpg": { cap: "all round", title: "all round",
      text: "The painted model from eight sides and two views of its head, with the battle scene behind: what you see " +
        "when the battle camera swings round. The sides and back are painted from fewer views than the front, so they " +
        "can be patchier." },
    "vs_target.jpg": { cap: "against the 2D picture this style aims for", title: "against the 2D picture",
      text: "Top: the battle the 2D picture was made from, as the game draws it. Middle: the same with this painting. " +
        "Bottom: the 2D picture itself, the look this style aims for. For a party member, below that: their part of each, " +
        "bigger." },
    "moving.jpg": { cap: "the parts that move on their own", title: "the parts that move on their own",
      text: "Parts the game moves on their own (such as Midgar Zolom's tongue), close up: first with the part hidden, to " +
        "check none of it got painted onto what's behind it, then with it showing, as painted." },
    "identity.png": { cap: "check: the game's own colours on the new texture", title: "the check",
      text: "The game's own colours put on the new painted texture, to check it fits the model: left as the game draws " +
        "it, middle on the new texture, right the differences (black where they're the same; thin bright lines between " +
        "parts are normal)." },
  };
  const p2dPic = (id, vid, name, t) =>
    `${API}/paint2d/${encodeURIComponent(id)}/${encodeURIComponent(vid)}/${name}?t=${Math.round(t || 0)}`;

  async function loadP2d() {
    const it = M.item;
    if (!it || it.kind !== "battle") return;
    const seq = ++M.p2dSeq;
    let r;
    try { r = await api(`${API}/paint2d/${encodeURIComponent(it.id)}`); } catch (e) { r = { id: it.id, error: e.message }; }
    if (seq !== M.p2dSeq || !M.item || M.item.id !== it.id || M.item.kind !== "battle") return;
    setP2d(r);
  }

  function setP2d(r) {
    M.p2d = r;
    const ids = (r.versions || []).map((v) => v.id);
    if (!ids.includes(M.p2dVer)) M.p2dVer = r.current || ids[ids.length - 1] || null;
    renderP2d();
  }

  // this model's 2D paints in the Studio's queue
  function p2dJobs(id = M.id) {
    const q = S.queue, name = `${KINDS.battle.job} ${id}`;
    if (!q) return { running: null, queued: [], any: false };
    const mine = (j) => j && j.name === name && j.kind === "model_2d";
    const running = mine(q.current) ? q.current : null, queued = q.queued.filter(mine);
    return { running, queued, any: !!(running || queued.length) };
  }

  // a version in a few words: "Painterly · strength 0.55 · 2048 px"
  function p2dLine(v) {
    if (!v.painted) return "the game's own colours on the new texture (not painted): a check";
    return [v.look ? lookName(v.look) : "", v.strength != null ? `strength ${Number(v.strength).toFixed(2)}` : "",
      v.size ? `${v.size} px` : ""].filter(Boolean).join(" · ");
  }

  function p2dTip(v) {
    const bits = [`${v.id}: ${p2dLine(v)}`];
    if (v.seconds) bits.push(`the AI took ${fmtDuration(v.seconds)}${v.views ? ` for ${v.views} views` : ""}`);
    if (v.graded) bits.push("its colours matched to the 2D picture afterwards");
    if (v.words) bits.push(`your words: ${v.words}`);
    if (v.prompt) bits.push(`what the AI was told: ${v.prompt}`);
    if (v.described) bits.push(`what the AI saw: ${v.described}`);
    if (!v.studio) bits.push("painted outside the Studio");
    return bits.join("\n");
  }

  function renderP2d() {
    const it = M.item, box = $("#mdlP2d");
    const on = !!it && it.kind === "battle";
    if (box.hidden === on) box.hidden = !on;
    if (!on) return;
    // typing your words: the card stays as it is until you leave the box, so the cursor stays put
    if (box.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") {
      M.p2dStale = true;
      p2dProgress();
      return;
    }
    M.p2dStale = false;
    const P = M.p2d && M.p2d.id === it.id ? M.p2d : null;
    const head = `<h2>Paint in 2D style</h2><div class="mdl-lines"><div>Paints the whole model as a hand-drawn 2D anime ` +
      `character: flat colours, simple painted shadows and thin lines. The AI paints it from several angles, and the ` +
      `paintings become one painted texture over every part of it, so models without textures can be painted too. ` +
      `Nothing in your game changes until you install it.</div></div>`;
    if (!P) { fill(box, head + `<div class="hint">Reading it…</div>`); return; }
    if (!P.settings) { fill(box, head + `<div class="model-warn">${esc(P.error || "It couldn't be read.")}</div>`); return; }
    const ps = P.settings, D = P.defaults, E = P.effective, L = M.looks;
    const jobs = p2dJobs(it.id);
    const ch = (k) => (ps[k] != null && ps[k] !== "" ? "changed" : "");
    const allDefault = ["look", "strength", "size"].every((k) => ps[k] == null) && !ps.words;
    // the look: battleatlas' own (Painterly) unless picked
    const dl = L && L.looks.find((x) => x.id === D.look);
    const dName = dl ? dl.name : D.look;
    const lookOpts = [`<option value="" ${!ps.look ? "selected" : ""}>${esc(dName)} (default)</option>`]
      .concat((L ? L.looks : []).filter((l) => l.id !== D.look).map((l) => `<option value="${esc(l.id)}" ${ps.look === l.id ? "selected" : ""}>` +
        `${esc(l.name)}${l.family ? ` · ${esc(famShort(l.family))}` : ""}</option>`))
      .concat([`<option value="__manage">Manage looks…</option>`]);
    const look = `<div class="ctl ${ch("look")}"><label class="lbl" for="mdlP2dLook">Look</label><select id="mdlP2dLook">${lookOpts.join("")}</select>` +
      `<div class="ctl-help">${helpText("Which of your looks paints it: its workflow and its own settings. The AI is also told " +
        "that the picture is a frame from a hand-drawn 2D anime, so a look made for backgrounds works here too. Painterly, " +
        "with those words, made the 2D picture of a battle this style aims for.", esc(dName))}</div></div>`;
    // repaint strength
    const sv = ps.strength ?? D.strength;
    const strength = `<div class="slider ${ch("strength")} ${P.uses_strength ? "" : "dim"}" id="mdlP2dStrength">` +
      `<div class="top"><span>Repaint strength</span><b>${sv.toFixed(2)}${ps.strength == null ? " (default)" : ""}</b></div>` +
      `<input type="range" min="${P.strength_range[0]}" max="${P.strength_range[1]}" step="0.05" value="${sv}" ${P.uses_strength ? "" : "disabled"}>` +
      `<div class="help">` +
      (P.uses_strength ? "" : `<div class="help-note">This look's workflow (${esc(famShort(P.family))}) always redraws fully, so ` +
        `repaint strength doesn't change it. Pick a look with MOSS, Flux.1 or Krea 2 to use it.</div>`) +
      `<div>How much the AI may change the model as it paints it.</div><div class="help-range">` +
      `<span><b>Higher:</b> more like a drawing and more of the AI's own ideas, but it starts inventing things that aren't ` +
      `there: at 0.65 Midgar Zolom got an open mouth full of fangs on its closed face.</span>` +
      `<span><b>Lower:</b> closer to the model as the game draws it, and less like a drawing.</span></div>` +
      `<div class="help-rec">Recommended: ${Number(D.strength).toFixed(2)}.</div></div></div>`;
    // the user's words
    const words = `<div class="ctl ${ch("words")}"><label class="lbl" for="mdlP2dWords">Your words</label>` +
      `<textarea id="mdlP2dWords" rows="2" maxlength="400" spellcheck="false" placeholder="e.g. glowing yellow eyes, a scar across its snout">${esc(ps.words)}</textarea>` +
      `<div class="ctl-help">${helpText("What you'd like the AI to know about it: colours, details to keep, the mood. They're " +
        "added after the 2D style's own words (a frame from a hand-drawn 2D anime: flat colours, simple painted shadows, " +
        "thin coloured lines, no 3D shading) and what it is, for every view. A few plain words work best; the AI can still " +
        "get them wrong.", "blank, or a few words")}</div></div>`;
    // the painting's size
    const sz = ps.size || D.size;
    const size = `<div class="ctl ${ch("size")}"><label class="lbl" for="mdlP2dSize">Painting size</label><select id="mdlP2dSize">` +
      P.sizes.map((x) => `<option value="${x === D.size ? "" : x}" ${sz === x ? "selected" : ""}>${x} × ${x} pixels${x === D.size ? " (default)" : ""}</option>`).join("") +
      `</select>` +
      (sz >= 4096 ? `<div class="model-warn">At 4096 this one painting takes about 85 MB of the game's memory, four times as ` +
        `much as at 2048. The game is a 32-bit program that can use at most 4 GB in all, and it has run out before: with ` +
        `several big paintings in one battle it may crash.</div>` : "") +
      `<div class="ctl-help"><div>How big the picture is that holds the painting of the whole model.</div><div class="help-range">` +
      `<span><b>Higher:</b> sharper when the battle camera comes close, but it takes more of the game's memory and a little ` +
      `longer to paint.</span><span><b>Lower:</b> lighter on memory, softer up close.</span></div>` +
      `<div class="help-rec">Recommended: ${D.size}.</div></div></div>`;
    // paint
    const painted = P.versions.some((v) => v.painted);
    const canPaint = P.can && !jobs.any;
    const paintTip = !P.can ? P.why_not : jobs.any ? "It's in the queue already (see below the button)" : P.game_running
      ? "Your game is running: close it first, painting needs the graphics card" : "Paint it in 2D style with the settings above";
    const paint = `<div class="ctl"><span class="lbl">Paint</span>` +
      `<div class="row-btns tight"><button class="small primary" id="mdlP2dPaint" ${canPaint ? "" : "disabled"} ` +
      `title="${esc(paintTip)}">${painted ? "Paint again" : "Paint"}</button></div>` +
      (P.can ? `<div class="hint">Takes about ${fmtDuration(P.estimate)}: ${esc(lookName(E.look))}, strength ` +
        `${Number(E.strength).toFixed(2)}, ${E.size} px${E.words ? ", your words" : ""}.</div>`
        : `<div class="model-warn">${esc(P.why_not)}</div>`) +
      (P.game_running && P.can ? `<div class="model-warn">Your game is running. The AI shares the graphics card with it, so ` +
        `painting has to wait until the game is closed: close it, then press Paint.</div>` : "") +
      (P.error && !jobs.running ? `<div class="model-warn" style="color:var(--bad)">The last paint failed: ${esc(P.error)}</div>` : "") +
      `<div class="mdl-line" id="mdlP2dProg"></div>` +
      `<div class="ctl-help">${helpText("The AI paints the model where a battle puts it: first from that battle's own cameras, " +
        "with the battle scene behind it, then all round and close up to its head. The paintings are put together into one " +
        "painted texture over the whole model; a later view only repaints what the views before it saw less well. It goes " +
        "in the Studio's queue with the other jobs and makes a new version below, marked ★. The AI needs the graphics " +
        "card, so your game has to be closed: Paint tells you if it isn't. Cancel queue stops it after the view it's " +
        "painting, and nothing is kept. Nothing in your game changes until you install.",
        "the settings as they are, with the game closed")}</div></div>`;
    // the versions, newest first, and the picked one's pictures
    const sel = P.versions.find((v) => v.id === M.p2dVer) || null;
    let versions;
    if (!P.versions.length) {
      versions = `<div class="hint">No 2D versions yet: Paint makes one.</div>`;
    } else {
      versions = `<div class="mdl-p2d-vers">` + [...P.versions].reverse().map((v) =>
        `<div class="mdl-p2d-ver ${sel && v.id === sel.id ? "viewing" : ""}" data-v="${esc(v.id)}" title="${esc(p2dTip(v))}">` +
        (v.pictures.includes("battle.jpg") ? `<img alt="" loading="lazy" src="${p2dPic(it.id, v.id, "battle.jpg", v.at)}">` : `<div class="noimg"></div>`) +
        `<div><div class="vl"><b>${esc(v.id)}</b>${v.current ? ` <span title="The version Install puts in the game">★</span>` : ""}` +
        `${v.installed ? ` · <span class="ok">in the game</span>` : ""}</div>` +
        `<div class="vs">${esc(p2dLine(v))}${v.words ? ` · “${esc(v.words)}”` : ""}</div>` +
        `<div class="vt">${fmtWhen(v.at, true)}${v.seconds ? ` · ${fmtDuration(v.seconds)}` : ""}</div></div></div>`).join("") + `</div>`;
      if (sel) {
        const pics = sel.pictures.filter((n) => n !== "identity.png" || !sel.painted);
        versions += (pics.length ? `<div class="mdl-p2d-prev">${pics.map((n) => `<figure data-p2d="${n}" title="${esc(P2D_PICS[n].text)}">` +
          `<img alt="" loading="lazy" src="${p2dPic(it.id, sel.id, n, sel.at)}"><figcaption>${esc(sel.id)}: ${esc(P2D_PICS[n].cap)}</figcaption></figure>`).join("")}</div>`
          : `<div class="hint">${esc(sel.id)} has no pictures.</div>`) +
          `<div class="row-btns tight"><button class="small primary" id="mdlP2dUse" ${sel.current ? "disabled" : ""} ` +
          `title="${sel.current ? "This is the ★ version already" : "Make this the version Install puts in the game. It doesn't change the game by itself."}">Use this version</button>` +
          `<button class="small" id="mdlP2dViews" ${sel.view_pictures.length ? "" : "disabled"} title="Every view the AI painted, what it was shown next to what it painted">Every view</button>` +
          (sel.painted && sel.pictures.includes("identity.png") ? `<button class="small ghost" id="mdlP2dCheck" title="${esc(P2D_PICS["identity.png"].text)}">Check</button>` : "") + `</div>`;
      }
    }
    versions += `<div class="ctl-help">${helpText("Each paint makes a version here (the ones painted outside the Studio show too). " +
      "Click one to see its pictures, and a picture to see it bigger. Use this version marks it ★: the one Install puts in " +
      "the game (nothing changes in the game until you install). Every view shows each angle the AI painted.", "")}</div>`;
    // in the game
    const I = P.installed, cur = P.versions.find((v) => v.current) || null;
    const iv = I && P.versions.find((v) => v.id === I.version);
    let where;
    if (I) {
      where = `<div class="mdl-line"><span class="ok">In the game</span>: ${esc(I.version)}` +
        (iv ? ` (${esc(p2dLine(iv))})` : " (a version since deleted)") + (I.at ? `, since ${fmtWhen(I.at, true)}` : "") + `.</div>` +
        (cur && cur.id !== I.version ? `<div class="mdl-line warn">That isn't the ★ version (${esc(cur.id)}): Install again to put the ★ one in.</div>` : "") +
        (P.problems.length ? `<div class="model-warn">Something in the game isn't as Install left it:</div>` +
          `<ul class="mdl-bl-probs">${P.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : "");
    } else {
      where = `<div class="mdl-line">Not in the game: battles show it as the game draws it.</div>`;
    }
    if (P.party) {
      where += `<div class="model-warn">${esc(it.title)} is in the party, so once installed the painting shows in every battle ` +
        `${esc(it.title)} fights in, not just one. The weapons aren't part of it: they keep the game's look.</div>`;
    }
    if (P.textures_installed) {
      where += `<div class="hint">Its painted textures from the Paint card are in the game too. The 2D painting covers the ` +
        `whole model, so while it's in, they don't show.</div>`;
    }
    const canInstall = !!cur && !M.p2dBusy;
    const inst = `<div class="ctl"><span class="lbl">Install and Uninstall</span>${where}` +
      `<div class="row-btns tight"><button class="small primary" id="mdlP2dInstall" ${canInstall ? "" : "disabled"} ` +
      `title="${esc(!cur ? "Paint it first" : `Put ${cur.id}, the ★ version, in the game`)}">${I ? "Install again" : "Install"}</button>` +
      `<button class="small" id="mdlP2dUninstall" ${I && !M.p2dBusy ? "" : "disabled"} title="Take this model's 2D painting out of the game">Uninstall</button></div>` +
      `<div class="ctl-help">${helpText(`Install copies the ★ version's painting into your game's folder (${esc(P.dest)}), so ` +
        `the next battle with ${esc(it.title)} shows it. FFNx-3d, the game's 3D add-on, draws it with flat light by ` +
        "itself, so the painted shadows do the shading. It replaces this model's earlier 2D painting in the game. If a " +
        "file it would write belongs to something else, nothing is installed and it tells you which. Uninstall takes " +
        "out exactly what Install put in, nothing else. Nothing goes in by itself.",
        "install a version you like; uninstall to compare with the original in a battle")}</div></div>`;
    const html = head + `<div class="subhead mdl-sub-row"><span>Settings for ${esc(it.title)}</span>` +
      `<button class="linkish" id="mdlP2dReset" ${allDefault ? "disabled" : ""} title="Look, repaint strength, your words and ` +
      `painting size back to their defaults">Back to defaults</button></div>` +
      look + strength + words + size + paint + `<div class="subhead">Versions</div>` + versions +
      `<div class="subhead">In your game</div>` + inst;
    fill(box, html, (el) => {
      el.querySelector("#mdlP2dLook").onchange = (e) => {
        const v = e.target.value;
        if (v === "__manage") {
          e.target.value = (M.p2d && M.p2d.settings.look) || "";
          if (typeof openLooks === "function") openLooks();
          return;
        }
        saveP2d({ look: v || null });
      };
      const range = el.querySelector("#mdlP2dStrength input[type=range]");
      range.oninput = () => { el.querySelector("#mdlP2dStrength .top b").textContent = Number(range.value).toFixed(2); };
      range.onchange = () => saveP2d({ strength: Number(range.value) });
      const ta = el.querySelector("#mdlP2dWords");
      ta.oninput = () => {
        clearTimeout(M.p2dTimer);
        M.p2dTimer = setTimeout(() => saveP2d({ words: ta.value }, true), 700);
      };
      ta.onblur = () => {
        if (M.p2dTimer) { clearTimeout(M.p2dTimer); M.p2dTimer = null; saveP2d({ words: ta.value }); }
        else if (M.p2dStale) renderP2d();
      };
      el.querySelector("#mdlP2dSize").onchange = (e) => saveP2d({ size: e.target.value ? Number(e.target.value) : null });
      el.querySelector("#mdlP2dReset").onclick = () => saveP2d({ reset: true });
      el.querySelector("#mdlP2dPaint").onclick = paintP2d;
      for (const r of el.querySelectorAll("[data-v]")) r.onclick = () => { M.p2dVer = r.dataset.v; renderP2d(); };
      for (const f of el.querySelectorAll("[data-p2d]")) f.onclick = () => showP2dPicture(f.dataset.p2d);
      const use = el.querySelector("#mdlP2dUse"), views = el.querySelector("#mdlP2dViews"), check = el.querySelector("#mdlP2dCheck");
      if (use) use.onclick = useP2d;
      if (views) views.onclick = showP2dViews;
      if (check) check.onclick = () => showP2dPicture("identity.png");
      el.querySelector("#mdlP2dInstall").onclick = installP2d;
      el.querySelector("#mdlP2dUninstall").onclick = uninstallP2d;
    });
    p2dProgress();
  }

  // the paint's progress (every second while the page is open), outside the card's HTML so the card stays put
  function p2dProgress() {
    const el = $("#mdlP2d #mdlP2dProg"), it = M.item;
    if (!el || !it) return;
    const j = p2dJobs(it.id);
    const html = j.running
      ? `Painting: ${esc(j.running.stage)}<div class="mdl-bar"><i style="width:${Math.round(j.running.progress * 100)}%"></i></div>`
      : j.queued.length ? `<span class="hint">Queued: it starts when the jobs before it are done, then takes about ` +
        `${fmtDuration(j.queued[0].est || 0)}.</span>` : "";
    if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  }

  // save one of the model's 2D settings (quiet: while typing, no toast)
  async function saveP2d(change, quiet = false) {
    const it = M.item;
    if (!it || it.kind !== "battle") return;
    M.p2dTimer = null;
    try {
      const r = await api(`${API}/paint2d/settings`, { body: { id: it.id, ...change } });
      if (M.item && M.item.id === r.id && M.item.kind === "battle") setP2d(r);
      if (!quiet) toast(change.reset ? `${it.title}: 2D settings back to their defaults` : `Saved for ${it.title}`);
    } catch (e) { toast(e.message); }
  }

  async function paintP2d() {
    const it = M.item;
    if (!it || it.kind !== "battle") return;
    let r;
    try {
      r = await api(`${API}/paint2d/paint`, { body: { id: it.id } });
    } catch (e) {
      await tell(e.message);  // the game running, and the like: nothing was queued
      loadP2d();
      return;
    }
    toast(r.added ? `${it.title}: painting in 2D style queued (about ${fmtDuration(r.est || 0)})` : "Already queued");
    renderP2d();
  }

  async function useP2d() {
    const it = M.item, v = M.p2d && M.p2d.versions.find((x) => x.id === M.p2dVer);
    if (!it || !v) return;
    try {
      setP2d(await api(`${API}/paint2d/use`, { body: { id: it.id, version: v.id } }));
      toast(M.p2d.installed && M.p2d.installed.version !== v.id
        ? `${v.id} marked ★. The game still has ${M.p2d.installed.version}: Install again to put ${v.id} in.`
        : `${v.id} marked ★: Install puts this one in the game.`, 6000);
    } catch (e) { toast(e.message); }
  }

  async function installP2d() {
    const it = M.item, P = M.p2d, cur = P && P.versions.find((v) => v.current);
    if (!it || !cur || M.p2dBusy) return;
    const ok = await ask(`Put ${it.title}'s 2D painting into your game: ${cur.id} (${p2dLine(cur)})?\n\n` +
      `It's copied to your game's ${P.dest} folder. The next battle with ${it.title} shows it, and Uninstall takes it out again.` +
      (P.installed ? ` It replaces the 2D painting in the game now (${P.installed.version}).` : "") +
      (cur.painted ? "" : "\n\nThis version isn't painted: it's the game's own colours on the new texture, a check that the game draws it right.") +
      (P.party ? `\n\n${it.title} is in the party: the painting shows in every battle ${it.title} fights in.` : ""),
      P.installed ? "Install again" : "Install");
    if (!ok) return;
    M.p2dBusy = true;
    renderP2d();
    let err = null;
    try {
      const r = await api(`${API}/paint2d/install`, { body: { id: it.id } });
      if (M.item && M.item.id === it.id) M.p2d = r.p2d;
      reloadListSoon();
      toast(`${it.title}'s 2D painting is in the game: ${r.version} (${r.files} file${r.files === 1 ? "" : "s"})`, 7000);
    } catch (e) { err = e.message; }
    M.p2dBusy = false;
    if (err) {
      await loadP2d();  // a refused install has taken the earlier one out first: show what's there now
      reloadListSoon();
      await tell(err);
    } else renderP2d();
  }

  async function uninstallP2d() {
    const it = M.item, I = M.p2d && M.p2d.installed;
    if (!it || !I || M.p2dBusy) return;
    if (!await ask(`Take ${it.title}'s 2D painting (${I.version}) out of your game?\n\nThe ${I.files} file${I.files === 1 ? "" : "s"} ` +
      `Install put in are removed, so battles show ${it.title} as the game draws it again. Nothing else is touched.`, "Uninstall")) return;
    M.p2dBusy = true;
    renderP2d();
    let err = null;
    try {
      const r = await api(`${API}/paint2d/uninstall`, { body: { id: it.id } });
      if (M.item && M.item.id === it.id) M.p2d = r.p2d;
      reloadListSoon();
      toast(`${it.title}'s 2D painting is out of the game: ${r.removed} file${r.removed === 1 ? "" : "s"} removed`, 6000);
      if (r.left.length) {
        err = `${r.left.length === 1 ? "One file was" : `${r.left.length} files were`} left in the game, because something ` +
          `changed ${r.left.length === 1 ? "it" : "them"} after Install put ${r.left.length === 1 ? "it" : "them"} there ` +
          `(in your game's folder):\n\n${r.left.join("\n")}`;
      }
    } catch (e) { err = e.message; }
    M.p2dBusy = false;
    renderP2d();
    if (err) await tell(err);
  }

  function showP2dPicture(name) {
    const it = M.item, v = M.p2d && M.p2d.versions.find((x) => x.id === M.p2dVer), p = P2D_PICS[name];
    if (!it || !v || !p) return;
    const url = p2dPic(it.id, v.id, name, v.at);
    openModal(`<div class="modal-head"><h2>${esc(it.title)} · 2D ${esc(v.id)}: ${esc(p.title)}</h2>` +
      `<button class="ghost small" data-a="close">Close</button></div><p class="hint">${esc(p.text)} ` +
      `<a href="${url}" target="_blank" rel="noopener">Open on its own</a></p><img class="mdl-bl-big" alt="" src="${url}">`);
  }

  function showP2dViews() {
    const it = M.item, v = M.p2d && M.p2d.versions.find((x) => x.id === M.p2dVer);
    if (!it || !v || !v.view_pictures.length) return;
    openModal(`<div class="modal-head"><h2>${esc(it.title)} · 2D ${esc(v.id)}: every view the AI painted</h2>` +
      `<button class="ghost small" data-a="close">Close</button></div><p class="hint">In the order they were painted: on ` +
      `the left what the AI was shown, on the right what it painted. The first are from the battle's own cameras, the rest ` +
      `all round and close up; each later one only repaints what the views before it saw less well.</p>` +
      `<div class="mdl-p2d-views">${v.view_pictures.map((n) => `<img alt="" loading="lazy" src="${p2dPic(it.id, v.id, n, v.at)}">`).join("")}</div>`);
  }

  // is the game running (every 4 s while a battle model is shown): the card's warning, the Paint button's tip
  async function pollP2dGame() {
    const it = M.item;
    if (M.open && it && it.kind === "battle" && M.p2d && M.p2d.id === it.id && M.p2d.settings) {
      try {
        const r = await api(`${API}/paint2d/game`);
        if (M.p2d && M.p2d.game_running !== r.running) { M.p2d.game_running = r.running; renderP2d(); }
      } catch (e) { /* the next poll */ }
    }
    setTimeout(pollP2dGame, 4000);
  }

  /* ---------------------------------------------------------- jobs */

  function myJobs(kind = M.kind, id = M.id) {
    const q = S.queue, name = `${KINDS[kind].job} ${id}`;
    if (!q) return { running: null, queued: [] };
    const mine = (j) => j && j.name === name && JOBS[j.kind];
    return { running: mine(q.current) ? q.current : null, queued: q.queued.filter(mine) };
  }

  function runningJob(kind, id) {
    const j = myJobs(kind, id);
    return j.running || j.queued.length ? j : null;
  }

  function renderFoot() {
    const it = M.item;
    if (!it) return;
    const look = lookInUse();
    const lookText = look ? `look "${esc(look.name)}"${look.family ? ` (${esc(famShort(look.family))})` : ""}` : "your look";
    const ps = it.paint || {};
    const outFor = (m) => (it.kind === "field" ? ` · atlas ${fmtX(ps.output || 1)}` : ps.output && ps.output !== it.inputs[m] ? `, game gets ${ps.output}×` : "");
    const line = (m, label) => {
      const pl = (it.plans || {})[m] || { sheets: it.sheet_count, views: 0 };
      const what = [pl.panorama ? "sky panorama" : "", pl.sheets ? `${pl.sheets} texture${pl.sheets === 1 ? "" : "s"} flat` +
          (pl.ground ? ` (${pl.ground} ground ${pl.detail_eff || pl.detail}×)` : "") : "",
        pl.views ? `${pl.views} view${pl.views === 1 ? "" : "s"}` : ""].filter(Boolean).join(" + ") || "nothing to paint";
      return `<div><b>${label}</b> ${METHOD_NAMES[pl.method || it.method] || ""}: ${what}` +
        `${it.kind === "field" ? "" : ` at ${it.inputs[m]}×`}${outFor(m)} · ~${fmtDuration(it.estimate[m])}</div>`;
    };
    const est = !it.paintable ? `<div class="hint">${esc(it.why_not)}</div>`
      : `<div title="Times once ComfyUI has its model loaded; the first paint after starting it takes about half a minute more">` +
        `Paints with ${lookText}${ps.prompt ? `, your words` : ""}</div>` + line("final", "Final") + line("preview", "Preview");
    fill($("#mdlEstimate"), est);
    const j = myJobs();
    const queuedModes = new Set(j.queued.map((x) => JOBS[x.kind]));
    const runMode = j.running ? JOBS[j.running.kind] : null;
    $("#mdlPreview").disabled = !it.paintable || queuedModes.has("preview") || runMode === "preview";
    $("#mdlFinal").disabled = !it.paintable || queuedModes.has("final") || runMode === "final";
    const bar = $("#mdlProgress .bar"), txt = $("#mdlProgress .ptext");
    let width = "0", text;
    if (j.running) {
      width = `${Math.round(j.running.progress * 100)}%`;
      text = `${runMode === "final" ? "Final" : "Preview"}: ${j.running.stage}`;
    } else if (j.queued.length) {
      text = `${j.queued.map((x) => (JOBS[x.kind] === "final" ? "Final" : "Preview")).join(" and ")} queued`;
    } else {
      const q = S.queue;
      text = q && q.current ? `Queue busy: ${q.current.name}` : "Idle";
    }
    if (bar.style.width !== width) bar.style.width = width;
    if (txt.textContent !== text) txt.textContent = text;
  }

  async function paintIt(mode) {
    const it = M.item;
    if (!it) return;
    try {
      const r = await api(`${API}/paint`, { body: { kind: it.kind, id: it.id, mode } });
      toast(r.added ? `${mode === "final" ? "Final" : "Preview"} of ${it.title} queued` : "Already queued");
    } catch (e) { toast(e.message); }
  }

  /* ---------------------------------------------------------- versions */

  // "5 painted as sheets, 0 from views" (battle scenes), else the angles the views came from
  const howPainted = (v) => (v.sheets ? `${v.sheets} painted as sheets, ${v.from_views} from views` : `${v.views} angles`);

  function verTip(v) {
    const s = v.settings || {};
    const bits = [`${verLabel(v)}, look "${v.look_name || v.look}"`,
      `${v.sheets ? `textures: ${howPainted(v)}` : `${v.views} angles`}, ${v.scale}× the original`];
    if (s.denoise != null) bits.push(`repaint strength ${s.denoise}`);
    if (v.seconds) bits.push(`took ${fmtDuration(v.seconds)}`);
    bits.push(settingsText(M.item.kind, v.paint, true, v.method));
    if (extrasText(v, true)) bits.push(extrasText(v, true));
    if (v.paint && v.paint.prompt) bits.push(`your words: ${v.paint.prompt}`);
    if (v.words) bits.push(`described automatically as: ${v.words}`);
    else if (v.scene && !v.paint) bits.push(`described as: ${v.scene}`);
    if (v.stand_in) bits.push("made by a stand-in painter (a test, not the AI)");
    return bits.join("\n");
  }

  function renderVersions() {
    const it = M.item, box = $("#mdlStrip");
    if (!it) { box.innerHTML = ""; return; }
    const html = [];
    const j = myJobs();
    if (j.running) {
      html.push(`<div class="vcard running" title="Painting now"><div class="vthumb"></div>` +
        `<div class="vl"><b>${JOBS[j.running.kind] === "final" ? "Final" : "Preview"}</b> ${esc(famShort(j.running.family))}</div>` +
        `<div class="vt" id="mdlRunStage">${esc(j.running.stage)}</div><div class="vbar"><i id="mdlRunBar" style="width:${Math.round(j.running.progress * 100)}%"></i></div></div>`);
    }
    for (const q of j.queued) {
      html.push(`<div class="vcard running" title="Queued"><div class="vthumb" style="animation:none"></div>` +
        `<div class="vl"><b>${JOBS[q.kind] === "final" ? "Final" : "Preview"}</b> ${esc(famShort(q.family))}</div><div class="vt">queued · ~${fmtDuration(q.est || 0)}</div></div>`);
    }
    for (const v of [...it.versions].reverse()) {
      html.push(`<div class="vcard ${v.id === M.ver ? "viewing" : ""}" data-id="${esc(v.id)}" title="${esc(verTip(v))}">` +
        `<img loading="lazy" alt="" src="${API}/vthumb/${it.kind}/${encodeURIComponent(it.id)}/${encodeURIComponent(v.id)}.jpg">` +
        `<div class="vl">${esc(verLabel(v))}</div>` +
        `<div class="vs">${esc(v.look_name || v.look || "")}${v.stand_in ? " · test" : ""}` +
        `<br>${esc(settingsText(it.kind, v.paint, false, v.method))}` +
        `${extrasText(v) ? `<br>${esc(extrasText(v))}` : ""}` +
        `${v.paint && v.paint.prompt ? `<br>“${esc(v.paint.prompt)}”` : ""}</div>` +
        `<div class="vt">${fmtWhen(v.created)} · ${esc(howPainted(v))}</div>` +
        (v.current ? `<span class="vbadge star" title="The version Install puts in the game">★</span>` : "") +
        (v.installed ? `<span class="vbadge inst" title="This version is in the game">in game</span>` : "") + `</div>`);
    }
    if (!it.versions.length && !j.running && !j.queued.length) {
      html.push(`<div class="hint" style="padding:10px">${it.paintable ? "No painted versions yet: Preview or Paint final makes one." : esc(it.why_not)}</div>`);
    }
    const key = JSON.stringify([it.kind, it.id, M.ver, it.versions.map((v) => [v.id, v.current, v.installed]), j.running && j.running.id, j.queued.map((q) => q.id)]);  // settings are fixed per version
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      box.innerHTML = html.join("");
      for (const el of box.querySelectorAll(".vcard[data-id]")) el.onclick = () => pickVersion(el.dataset.id);
    } else if (j.running) {
      const b = $("#mdlRunBar"), s = $("#mdlRunStage");
      if (b) b.style.width = `${Math.round(j.running.progress * 100)}%`;
      if (s) s.textContent = j.running.stage;
    }
    renderVersionHead();
  }

  function renderVersionHead() {
    const v = viewed(), it = M.item;
    $("#mdlVerTitle").innerHTML = v ? `Viewing <b>${esc(verLabel(v))}</b> · ${fmtWhen(v.created, true)}${v.current ? " ★" : ""}` +
      (v.sheets ? ` · ${esc(howPainted(v))}` : "") +
      `${v.installed ? ` · <span class="ok">in the game</span>` : ""}` : (it && it.versions.length ? "" : "Versions");
    $("#mdlUse").disabled = !v || v.current;
    $("#mdlSheet").disabled = !v;
    $("#mdlDelete").disabled = !v || v.installed;
    $("#mdlDelete").title = v && v.installed ? "This version is in the game: uninstall it first" : "Delete this version's painted textures and pictures";
    const info = $("#mdlVerInfo");
    const used = v ? `<div class="mdl-used">Painted with look "${esc(v.look_name || v.look)}" · ` +
      `${esc(settingsText(it.kind, v.paint, true, v.method))}${extrasText(v, true) ? ` · ${esc(extrasText(v, true))}` : ""}` +
      `${v.paint && v.paint.prompt ? ` · your words: “${esc(v.paint.prompt)}”` : ""}</div>` : "";
    const html = used + `<div class="ctl-help">${helpText("Each Preview and Paint final makes a version here. Click one to see it as Painted. " +
      "Use this version marks it ★: the one Install puts in the game (nothing changes in the game until you install). " +
      "Views sheet shows " + (it && it.kind === "scene" ? "the scene from its battle cameras" : "every angle the AI painted") +
      ", original next to painted. Delete removes a version for good." + (it && it.kind === "scene"
        ? " A battle scene's textures are painted flat as sheets: the version says how many." : ""), "")}</div>`;
    fill(info, html);
  }

  function pickVersion(id) {
    M.ver = id;
    if (M.mode === "orig") M.mode = "new";
    setPref("models.mode", M.mode);
    renderModes();
    renderVersions();
    requestRender(false);
  }

  async function useVersion() {
    const v = viewed(), it = M.item;
    if (!v) return;
    try {
      setItem(await api(`${API}/use`, { body: { kind: it.kind, id: it.id, version: v.id } }));
      reloadListSoon();
      toast(it.installed ? "Marked ★. The game still has the installed one: Install again to put this one in." : "Marked ★: Install puts this one in the game.");
    } catch (e) { toast(e.message); }
  }

  async function deleteVersion() {
    const v = viewed(), it = M.item;
    if (!v) return;
    if (!await ask(`Delete the ${verLabel(v)} version of ${it.title} from ${fmtWhen(v.created, true)}?\n\nIts painted textures and pictures are removed.`, "Delete")) return;
    try {
      setItem(await api(`${API}/delete`, { body: { kind: it.kind, id: it.id, version: v.id } }));
      R.shown = {};
      reloadListSoon();
      requestRender(false);
    } catch (e) { toast(e.message); }
  }

  function showSheet() {
    const v = viewed(), it = M.item;
    if (!v) return;
    const url = `${API}/sheet/${it.kind}/${encodeURIComponent(it.id)}/${encodeURIComponent(v.id)}.jpg?t=${Date.now()}`;
    openModal(`<div class="modal-head"><h2>${esc(it.title)} · ${esc(verLabel(v))}: ` +
      `${it.kind === "scene" ? "from its battle cameras" : "every angle"}, original | painted</h2>` +
      `<button class="ghost small" data-a="close">Close</button></div><p class="hint">Left: the original as the game shades it. ` +
      `Right: with this version's painted textures. <a href="${url}" target="_blank" rel="noopener">Open on its own</a></p>` +
      `<img alt="" src="${url}">`);
  }

  // a picture in a box over the page (its [data-a=close] button, Escape or a click beside it closes it)
  function openModal(inner) {
    const wrap = document.createElement("div");
    wrap.className = "modal dialog-modal mdl-sheet";
    wrap.innerHTML = `<div class="modal-card">${inner}</div>`;
    const close = () => { window.removeEventListener("keydown", keys, true); wrap.remove(); };
    const keys = (e) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } };
    wrap.onclick = (e) => { if (e.target === wrap) close(); };
    wrap.querySelector('[data-a="close"]').onclick = close;
    window.addEventListener("keydown", keys, true);
    document.body.appendChild(wrap);
  }

  function renderStatus() {
    const it = M.item;
    if (!it) return;
    const parts = [`<b>${esc(it.title)}</b>`, `${it.versions.length} version${it.versions.length === 1 ? "" : "s"}`];
    const cur = it.versions.find((v) => v.current);
    if (cur) parts.push(`★ ${esc(verLabel(cur))} from ${fmtWhen(cur.created, true)}`);
    parts.push(it.kind === "field" ? "field models stay out of the game for now" : it.installed ? `<span class="ok">in the game</span>` : "not in the game");
    if (M.viewError) parts.push(`<span class="bad">${esc(M.viewError)}</span>`);
    $("#mdlStatus").innerHTML = parts.join(" · ");
  }

  /* ---------------------------------------------------------- the viewer */

  const R = { busy: false, want: null, shown: {} };  // shown: {orig|new: {url, bmp}}

  function needed() {
    return M.mode === "split" ? ["orig", "new"] : [M.mode];
  }

  function renderSize(fast) {
    const w = canvas.width, h = canvas.height;
    const max = fast ? 220000 : 1000000;  // pixels: dragging stays smooth, a still view is sharp
    const k = Math.min(1, Math.sqrt(max / Math.max(1, w * h)));
    return { w: Math.max(64, Math.round(w * k)), h: Math.max(48, Math.round(h * k)) };
  }

  function requestRender(fast = false) {
    R.want = { fast };
    pump();
  }

  async function bitmap(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).message || msg; } catch (e) { /* not JSON */ }
      throw new Error(r.status === 404 ? "not found" : msg);
    }
    return createImageBitmap(await r.blob());
  }

  async function pump() {
    if (R.busy || !R.want) return;
    const it = M.item;
    if (!it || !M.open) { R.want = null; draw(); return; }
    const { fast } = R.want;
    R.want = null;
    R.busy = true;
    try {
      const size = renderSize(fast);
      const params = { cam: M.cam, yaw: M.yaw.toFixed(1), pitch: M.pitch.toFixed(1), zoom: M.zoom.toFixed(3), w: size.w, h: size.h };
      const tasks = [];
      for (const which of needed()) {
        const ver = which === "orig" ? "orig" : which === "syw" ? "syw" : M.ver;
        if (!ver) continue;
        const url = `${API}/view/${it.kind}/${encodeURIComponent(it.id)}.jpg?` + new URLSearchParams({ ver, ...params });
        if (R.shown[which] && R.shown[which].url === url) continue;
        tasks.push(bitmap(url).then((bmp) => { R.shown[which] = { url, bmp, ver, key: `${it.kind}/${it.id}` }; }));
      }
      await Promise.all(tasks);
      M.viewError = "";
    } catch (e) {
      M.viewError = `The picture couldn't be drawn: ${e.message}`;
    } finally {
      R.busy = false;
      draw();
      renderStatus();
      if (R.want) pump();
    }
  }

  function shownFor(which) {
    const s = R.shown[which], it = M.item;
    if (!s || !it || s.key !== `${it.kind}/${it.id}`) return null;
    if (which === "new" && s.ver !== M.ver) return null;
    return s.bmp;
  }

  function draw() {
    const W = canvas.width, H = canvas.height;
    ctx.fillStyle = M.item && M.item.kind === "scene" ? "#000" : "#111317";
    ctx.fillRect(0, 0, W, H);
    const it = M.item, empty = $("#mdlEmpty");
    const bL = $("#mdlBadgeL"), bR = $("#mdlBadgeR"), b = $("#mdlBadge");
    bL.hidden = bR.hidden = b.hidden = true;
    if (!it) { empty.textContent = M.id ? "Loading…" : "Pick a model on the left."; return; }
    const v = viewed();
    const orig = shownFor("orig"), neu = shownFor("new");
    let msg = "";
    if ((M.mode === "new" || M.mode === "split") && !v) {
      msg = it.paintable ? "No painted version yet: Preview (on the right) paints one." : it.why_not;
    }
    if (M.mode === "syw") {
      const sb = shownFor("syw"), Y = it.syw || {};
      if (sb) ctx.drawImage(sb, 0, 0, W, H);
      b.hidden = false;
      b.innerHTML = `SYW Unified · ready-made 4×` + (Y.have < Y.total ? ` <span class="hint">(${Y.have} of ${Y.total} textures; the rest original)</span>` : "");
      empty.textContent = sb || M.viewError ? "" : "Drawing…";
      empty.style.display = empty.textContent ? "flex" : "none";
      $("#mdlNote").hidden = true;
      return;
    }
    if (M.mode === "orig" || (M.mode !== "orig" && !v)) {
      if (orig) ctx.drawImage(orig, 0, 0, W, H);
      b.hidden = false;
      b.textContent = "Original";
    } else if (M.mode === "new") {
      if (neu) ctx.drawImage(neu, 0, 0, W, H);
      b.hidden = false;
      b.innerHTML = `Painted · <b>${esc(verLabel(v))}</b>`;
    } else {
      const x = Math.round(W * M.split);
      if (orig) ctx.drawImage(orig, 0, 0, W, H);
      if (neu) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x, 0, W - x, H);
        ctx.clip();
        ctx.drawImage(neu, 0, 0, W, H);
        ctx.restore();
      }
      ctx.fillStyle = "rgba(255,255,255,.85)";
      ctx.fillRect(x - 1, 0, 2, H);
      bL.hidden = bR.hidden = false;
      bL.innerHTML = "<b>Original</b>";
      bR.innerHTML = `<b>Painted</b> · ${esc(verLabel(v))}`;
    }
    const note = $("#mdlNote");
    note.hidden = !msg;
    note.textContent = msg;
    empty.textContent = !orig && !neu && !M.viewError ? "Drawing…" : "";
    empty.style.display = empty.textContent ? "flex" : "none";
  }

  function resize() {
    const r = vp.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
    if (canvas.width === w && canvas.height === h) return;
    canvas.width = w;
    canvas.height = h;
    R.shown = {};
    draw();
    clearTimeout(resize.t);
    resize.t = setTimeout(() => requestRender(false), 120);
  }
  new ResizeObserver(() => { if (M.open) resize(); }).observe(vp);

  function resetView(render = true) {
    M.yaw = 0;
    M.pitch = 0;
    M.zoom = 1;
    if (render) requestRender(false);
  }

  // drag: turn (or move Split's line when grabbed near it); wheel: zoom
  let drag = null;
  const nearSplit = (e) => {
    if (M.mode !== "split" || !viewed()) return false;
    const r = vp.getBoundingClientRect();
    return Math.abs(e.clientX - r.left - r.width * M.split) < 8;
  };
  vp.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !M.item) return;
    drag = { x: e.clientX, y: e.clientY, split: nearSplit(e), yaw: M.yaw, pitch: M.pitch };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("dragging");
  });
  vp.addEventListener("pointermove", (e) => {
    if (!drag) { vp.classList.toggle("split-hover", nearSplit(e)); return; }
    if (drag.split) {
      const r = vp.getBoundingClientRect();
      M.split = Math.min(0.98, Math.max(0.02, (e.clientX - r.left) / r.width));
      draw();
      return;
    }
    M.yaw = drag.yaw - (e.clientX - drag.x) * 0.3;
    M.pitch = Math.max(-70, Math.min(80, drag.pitch + (e.clientY - drag.y) * 0.25));
    requestRender(true);
  });
  const endDrag = () => {
    if (!drag) return;
    const was = drag;
    drag = null;
    vp.classList.remove("dragging");
    if (!was.split) requestRender(false);
  };
  vp.addEventListener("pointerup", endDrag);
  vp.addEventListener("pointercancel", endDrag);
  vp.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (!M.item) return;
    M.zoom = Math.max(0.3, Math.min(8, M.zoom * Math.pow(1.0015, -e.deltaY)));
    requestRender(true);
    clearTimeout(vp.wheelTimer);
    vp.wheelTimer = setTimeout(() => requestRender(false), 250);
  }, { passive: false });

  /* ---------------------------------------------------------- wiring */

  for (const b of $("#mdlKinds").children) b.onclick = () => setKind(b.dataset.k);
  for (const b of $("#mdlModes").children) b.onclick = () => setMode(b.dataset.m);
  $("#mdlSearch").oninput = () => { setPref(`models.search.${M.kind}`, $("#mdlSearch").value); renderList(); };
  $("#mdlHideBare").checked = M.hideBare;
  $("#mdlHideBare").onchange = (e) => { M.hideBare = e.target.checked; setPref("models.hideBare", M.hideBare ? "1" : "0"); renderList(); };
  $("#mdlReset").onclick = () => resetView();
  $("#mdlPreview").onclick = () => paintIt("preview");
  $("#mdlFinal").onclick = () => paintIt("final");
  $("#mdlUse").onclick = useVersion;
  $("#mdlDelete").onclick = deleteVersion;
  $("#mdlSheet").onclick = showSheet;
  $("#mdlCancel").onclick = async () => {
    const q = S.queue;
    if (!q || (!q.current && !q.queued.length)) { toast("Nothing is running or queued"); return; }
    const n = q.queued.length + (q.current ? 1 : 0);
    if (n > 1 && !await ask(`Stop the queue? ${n} jobs are running or queued (fields' renders too); all of them stop.`, "Stop them all")) return;
    await api("/api/queue/cancel", { body: {} });
    toast("Queue cancelled");
  };

  let listTimer = null;
  function reloadListSoon() {
    clearTimeout(listTimer);
    listTimer = setTimeout(async () => {
      try {
        const r = await api(`${API}/list/${M.kind}`);
        if (!r.loading) { M.lists[M.kind] = r.items; renderList(); }
      } catch (e) { /* server restarting */ }
    }, 300);
  }

  // the Studio's queue (polled by app.js into S.queue): this model's job, the list's running dots
  setInterval(() => {
    if (!M.open || !M.item) return;
    if (M.modelsSeen !== S.models) { M.modelsSeen = S.models; renderPaint(); }
    renderFoot();
    renderVersions();
    renderList();
    renderBattleLook();
    renderP2d();
  }, 1000);
  // a 2D paint finished: say so, and show the version it made
  document.addEventListener("studio:jobs", (e) => {
    const ours = e.detail.filter((r) => r.kind === "model_2d");
    if (!ours.length) return;
    for (const r of ours) {
      if (r.ok) toast(`${r.name}: ${r.message || "painted in 2D style"}`, 6000);
      else if (r.message && r.message !== "cancelled") toast(`${r.name}: the 2D painting wasn't made. ${r.message}`, 10000);
    }
    reloadListSoon();
    const it = M.item;
    if (it && it.kind === "battle" && ours.some((r) => r.name === it.job_name)) {
      const made = ours.filter((r) => r.name === it.job_name && r.ok && r.version).pop();
      if (made) M.p2dVer = made.version;
      loadP2d();
    }
  });
  // a battle look build finished: say so, and show what it made
  document.addEventListener("studio:jobs", (e) => {
    const ours = e.detail.filter((r) => r.kind === "model_look");
    if (!ours.length) return;
    for (const r of ours) {
      if (r.ok) toast(`${r.name}: ${r.message || "battle look built"}`, 6000);
      else if (r.message && r.message !== "cancelled") toast(`${r.name}: the battle look wasn't built. ${r.message}`, 10000);
    }
    const it = M.item;
    if (it && ours.some((r) => r.name === it.job_name)) loadBattleLook();
  });
  document.addEventListener("studio:jobs", (e) => {
    const ours = e.detail.filter((r) => JOBS[r.kind]);
    if (!ours.length) return;
    for (const r of ours) {
      if (!r.ok && r.message && r.message !== "cancelled") toast(`${r.name}: ${r.message}`, 8000);
    }
    reloadListSoon();
    const it = M.item;
    if (it && ours.some((r) => r.name === it.job_name)) {
      const made = ours.filter((r) => r.name === it.job_name && r.ok && r.version).pop();
      if (made) { M.ver = made.version; if (M.mode === "orig") M.mode = "split"; R.shown = {}; }
      loadItem();
    }
  });

  // keys while the Models page is open: its own, and none reach the field page underneath
  window.addEventListener("keydown", (e) => {
    if (!M.open || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector(".dialog-modal") || !$("#looksModal").hidden) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "Escape") return;  // the Studio's: closes its menus
    e.stopImmediatePropagation();
    if (e.key === "1") setMode("orig");
    else if (e.key === "5") { if (hasSyw()) setMode("syw"); }
    else if (e.key === "2") setMode("new");
    else if (e.key === "3") setMode("split");
    else if (e.code === "Space") { e.preventDefault(); setMode(M.mode === "orig" ? "new" : "orig"); }
    else if (e.key === "r" || e.key === "R") resetView();
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const shown = [...$("#mdlList").children].filter((li) => li.style.display !== "none");
      const i = shown.findIndex((li) => li.dataset.id === M.id);
      const next = shown[Math.max(0, Math.min(shown.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
      if (next && next.dataset.id !== M.id) select(next.dataset.id);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const it = M.item;
      if (!it || !it.versions.length) return;
      e.preventDefault();
      const order = [...it.versions].reverse().map((v) => v.id);  // as the strip shows them, newest first
      const i = Math.max(0, order.indexOf(M.ver));
      const j = Math.max(0, Math.min(order.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)));
      if (order[j] !== M.ver) pickVersion(order[j]);
    }
  }, true);

  setOpen(M.open);
  pollGame();
  pollP2dGame();
})();
