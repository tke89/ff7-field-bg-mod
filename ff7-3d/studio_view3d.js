/* The preview's 3D view (FF7 Field Studio plug-in, loaded by studio_plugin.js): the field's 3D scene drawn with
   WebGL2 from the game's camera, framed like the 2D view so switching between New and 3D lines up. Drag to turn
   around the scene, right-drag (or shift-drag) to move, wheel to go closer, double-click for the game's camera.
   Server side: /api/3d/view/<field> and /api/3d/scene/<field>/<part> in studio_plugin.py. */
(() => {
  const MODE = "3d";
  const LOOKS = { painted: "Painted", lit: "Painted, with light", clay: "Shape only" };
  const V = { name: null, info: null, gpu: null, token: 0, look: pref("view3d.look", "painted"),
              yaw: 0, pitch: 0, dist: 1, pan: [0, 0, 0] };

  const css = document.createElement("style");
  css.textContent = `
    .v3d { position: absolute; inset: 0; z-index: 2; background: #0a0b0d; cursor: grab; }
    .v3d.dragging { cursor: grabbing; }
    .v3d canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
    .v3d-msg { position: absolute; inset: 0; display: none; flex-direction: column; gap: 10px; align-items: center;
               justify-content: center; color: var(--muted); text-align: center; padding: 30px; }
    .v3d-bar { position: absolute; left: 10px; bottom: 10px; display: flex; gap: 8px; align-items: center;
               padding: 5px 8px; border-radius: 6px; background: rgba(0,0,0,.6); cursor: default; }`;
  document.head.appendChild(css);

  const btn = document.createElement("button");
  btn.dataset.mode = MODE;
  btn.textContent = "3D";
  btn.title = "The field's 3D scene, as built for the game (4)";
  btn.onclick = () => setMode(MODE);
  $("#modeSeg").appendChild(btn);

  const box = document.createElement("div");
  box.className = "v3d";
  box.hidden = true;
  box.innerHTML = `<canvas></canvas><div class="badge"></div><div class="v3d-msg"></div>
    <div class="v3d-bar">
      <select title="Painted: the scene's own colours, as in the game's painted look. With light: lit from one
side, so its shape shows. Shape only: grey, to judge the geometry.">${Object.entries(LOOKS).map(([k, l]) =>
        `<option value="${k}">${l}</option>`).join("")}</select>
      <button class="small" title="Back to the game's camera (double-click)">Game camera</button>
      <span class="hint">drag: turn · right-drag: move · wheel: closer</span>
    </div>`;
  vp.appendChild(box);
  const cv = box.querySelector("canvas"), badge = box.querySelector(".badge"), msg = box.querySelector(".v3d-msg");
  const lookSel = box.querySelector("select");
  lookSel.value = LOOKS[V.look] ? V.look : "painted";
  lookSel.onchange = () => { V.look = lookSel.value; setPref("view3d.look", V.look); render(); };
  box.querySelector(".v3d-bar button").onclick = () => { resetView(); render(); };

  function message(html) {
    msg.innerHTML = html;
    msg.style.display = html ? "flex" : "none";
  }

  /* ---------------------------------------------------------- WebGL */

  const gl = cv.getContext("webgl2", { antialias: true });
  const VS = `#version 300 es
    layout(location = 0) in vec3 pos;
    layout(location = 1) in vec3 nrm;
    layout(location = 2) in vec2 uv;
    uniform mat4 view, proj;
    out vec3 vW, vN;
    out vec2 vUv;
    void main() {
      vW = pos; vN = nrm; vUv = uv;
      gl_Position = proj * (view * vec4(pos, 1.0));
    }`;
  const FS = `#version 300 es
    precision highp float;
    in vec3 vW, vN;
    in vec2 vUv;
    uniform sampler2D tex;
    uniform int look;
    uniform vec3 eye, light;
    out vec4 color;
    void main() {
      vec3 c = texture(tex, vUv).bgr;  // the scene's texture is BGRA, uploaded as it is
      vec3 n = normalize(vN);
      if (dot(n, eye - vW) < 0.0) n = -n;  // both sides
      float l = max(dot(n, light), 0.0);
      if (look == 1) c *= 0.55 + 0.6 * l;
      else if (look == 2) c = vec3(0.78) * (0.22 + 0.78 * l);
      color = vec4(c, 1.0);
    }`;
  let prog = null;
  if (gl) {
    const shader = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    prog = gl.createProgram();
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
  }
  const uni = (n) => gl.getUniformLocation(prog, n);

  function accessor(g, bin, i) {
    const a = g.accessors[i], bv = g.bufferViews[a.bufferView];
    const n = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
    const Arr = { 5126: Float32Array, 5125: Uint32Array, 5123: Uint16Array, 5121: Uint8Array }[a.componentType];
    return new Arr(bin, (bv.byteOffset || 0) + (a.byteOffset || 0), a.count * n);
  }

  function release() {
    const g = V.gpu;
    if (!g) return;
    for (const b of g.buffers) gl.deleteBuffer(b);
    gl.deleteTexture(g.tex);
    gl.deleteVertexArray(g.vao);
    V.gpu = null;
  }

  // the mesh as it is in the file (the sway's bones at rest) and its colour texture
  function upload(gltf, bin, dds) {
    release();
    const prim = gltf.meshes[0].primitives[0];
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const buffers = [];
    const attr = (loc, i, size) => {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, accessor(gltf, bin, i), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
      buffers.push(b);
    };
    attr(0, prim.attributes.POSITION, 3);
    attr(1, prim.attributes.NORMAL, 3);
    attr(2, prim.attributes.TEXCOORD_0, 2);
    const idx = accessor(gltf, bin, prim.indices);
    const ib = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    buffers.push(ib);
    gl.bindVertexArray(null);

    // uncompressed DDS (gltfout.write_dds): 128-byte header, or 148 with a DX10 one
    const head = new DataView(dds);
    const h = head.getUint32(12, true), w = head.getUint32(16, true);
    const off = head.getUint32(84, true) === 0x30315844 ? 148 : 128;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(dds, off, w * h * 4));
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const aniso = gl.getExtension("EXT_texture_filter_anisotropic");
    if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);

    // bounds (for the depth range) and the pivot: the middle of the picture at the scene's median depth
    const pos = accessor(gltf, bin, prim.attributes.POSITION);
    const { R, T, zoom, rect } = V.info;
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    const depths = [];
    for (let i = 0; i < pos.length; i += 3) {
      for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], pos[i + k]); hi[k] = Math.max(hi[k], pos[i + k]); }
      if (i % 30 === 0) depths.push(R[2][0] * pos[i] + R[2][1] * pos[i + 1] + R[2][2] * pos[i + 2] + T[2]);
    }
    depths.sort((a, b) => a - b);
    const d = depths[depths.length >> 1];
    const pc = [(rect[0] + rect[2] / 2) * d / zoom, (rect[1] + rect[3] / 2) * d / zoom, d];
    V.gpu = {
      vao, buffers, tex, count: idx.length,
      centre: lo.map((v, k) => (v + hi[k]) / 2), radius: Math.hypot(...hi.map((v, k) => v - lo[k])) / 2,
      pivot: mulT(R, sub(pc, T)), depth: d,
    };
  }

  /* ---------------------------------------------------------- camera */

  const sub = (a, b) => a.map((v, i) => v - b[i]);
  const add = (a, b) => a.map((v, i) => v + b[i]);
  const scale = (a, s) => a.map((v) => v * s);
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const mulT = (R, v) => [0, 1, 2].map((c) => R[0][c] * v[0] + R[1][c] * v[1] + R[2][c] * v[2]);  // R^T v
  // v turned by angle t around the unit axis a (Rodrigues)
  const turn = (a, t, v) => add(add(scale(v, Math.cos(t)), scale(cross(a, v), Math.sin(t))),
    scale(a, dot(a, v) * (1 - Math.cos(t))));

  function resetView() {
    V.yaw = 0; V.pitch = 0; V.dist = 1; V.pan = [0, 0, 0];
  }

  // The camera after the viewer's turns: rows (right, down, forward) and position, in the field's world.
  function camera() {
    const { R, T } = V.info, P = V.gpu.pivot;
    const C0 = V.info.C || scale(mulT(R, T), -1);  // C: the exact camera position (R isn't quite a rotation)
    // the world's up: the axis the game camera's "down" points most against (z, in FF7's fields)
    const k = [0, 1, 2].reduce((m, i) => (Math.abs(R[1][i]) > Math.abs(R[1][m]) ? i : m), 0);
    const up = [0, 0, 0];
    up[k] = -Math.sign(R[1][k]);
    const right = turn(up, V.yaw, R[0]);
    const q = (v) => turn(right, V.pitch, turn(up, V.yaw, v));
    const rows = R.map(q);
    const pos = add(add(P, V.pan), scale(q(sub(C0, P)), V.dist));
    return { rows, pos };
  }

  function render() {
    if (box.hidden || !gl || !V.gpu || !V.info) return;
    const dpr = window.devicePixelRatio || 1;
    const W = box.clientWidth, H = box.clientHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    const { rows, pos } = camera();
    const t = rows.map((r) => -dot(r, pos));
    const view = new Float32Array(16);
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) view[c * 4 + r] = rows[r][c];
      view[12 + r] = t[r];
    }
    view[15] = 1;
    // background coordinates zoom * c.xy / c.z, placed where the 2D view puts the picture (S.zoom, S.panX/Y)
    const { zoom, rect } = V.info, Z = S.zoom;
    const far = Math.hypot(...sub(pos, V.gpu.centre)) + V.gpu.radius;
    const near = Math.max(far * 0.0005, far - 2 * V.gpu.radius, 1);
    const proj = new Float32Array(16);
    proj[0] = 2 * Z * zoom / W;
    proj[8] = 2 * (S.panX - rect[0] * Z) / W - 1;
    proj[5] = -2 * Z * zoom / H;
    proj[9] = 1 - 2 * (S.panY - rect[1] * Z) / H;
    proj[10] = (far + near) / (far - near);
    proj[14] = -2 * far * near / (far - near);
    proj[11] = 1;
    // light from the upper left, a little from the front, as the game camera sees it
    const light = mulT(V.info.R, [-0.45, -0.7, -0.55]);
    gl.viewport(0, 0, cv.width, cv.height);
    gl.clearColor(0.04, 0.043, 0.05, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.useProgram(prog);
    gl.uniformMatrix4fv(uni("view"), false, view);
    gl.uniformMatrix4fv(uni("proj"), false, proj);
    gl.uniform3fv(uni("eye"), pos);
    gl.uniform3fv(uni("light"), scale(light, 1 / Math.hypot(...light)));
    gl.uniform1i(uni("look"), Object.keys(LOOKS).indexOf(V.look));
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, V.gpu.tex);
    gl.uniform1i(uni("tex"), 0);
    gl.bindVertexArray(V.gpu.vao);
    gl.drawElements(gl.TRIANGLES, V.gpu.count, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
  }

  let pending = false;
  function requestRender() {
    if (pending) return;
    pending = true;
    setTimeout(() => { pending = false; render(); }, 0);
  }

  /* ---------------------------------------------------------- the field's scene */

  function label() {
    const i = V.info;
    const v = S.detail && S.detail.versions.find((x) => x.id === i.version);
    badge.textContent = "3D scene" + (v ? ` · from ${verKind(v)} ${fmtWhen(v.created)}` : "") +
      (i.up_to_date ? "" : " · out of date: the field has another version in the game now");
    badge.hidden = false;
  }

  async function load(name) {
    const token = ++V.token;
    V.name = name;
    V.info = null;
    release();
    badge.hidden = true;
    if (!gl) { message("This browser can't show 3D (no WebGL2)."); return; }
    message("Loading the 3D scene…");
    const info = await fetch(`/api/3d/view/${name}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (token !== V.token) return;
    if (!info || !info.built) {
      message(`No 3D scene for ${esc(name)} yet.` + (info && info.current
        ? `<button class="small">Build 3D</button>` : "<span>It's built from the version the game uses: make one first.</span>"));
      const b = msg.querySelector("button");
      if (b) b.onclick = async () => {
        const r = await api("/api/3d/build", { body: { names: [name] } });
        toast(r.added ? "3D scene queued" : "Already queued");
      };
      V.info = info;
      return;
    }
    try {
      const at = `/api/3d/scene/${name}/`, q = `?t=${info.stamp}`;
      const [gltf, bin, dds] = await Promise.all([
        fetch(at + "scene.gltf" + q).then((r) => r.json()),
        fetch(at + "scene.bin" + q).then((r) => r.arrayBuffer()),
        fetch(at + "colour.dds" + q).then((r) => r.arrayBuffer()),
      ]);
      if (token !== V.token) return;
      V.info = info;
      upload(gltf, bin, dds);
    } catch (e) {
      if (token === V.token) message(`The 3D scene didn't load: ${esc(String(e.message || e))}`);
      return;
    }
    message("");
    label();
    resetView();
    render();
  }

  // the scene may have been rebuilt (a 3D job, Automatic): reload it when its file changed
  async function recheck() {
    if (box.hidden || !V.name) return;
    const info = await fetch(`/api/3d/view/${V.name}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (!info || box.hidden || info.built !== !!(V.info && V.info.built) || (info.built && info.stamp !== V.info.stamp)) {
      if (info && !box.hidden) load(V.name);
      return;
    }
    if (info.built) { V.info = { ...V.info, ...info }; label(); }
  }

  function show(on) {
    box.hidden = !on;
    if (!on) return;
    if (V.name !== S.current) load(S.current);
    else if (V.gpu) { label(); render(); }
    else recheck();
  }

  /* ---------------------------------------------------------- input */

  let drag = null;
  box.addEventListener("mousedown", (e) => e.stopPropagation());  // not the 2D view's panning underneath
  cv.addEventListener("mousedown", (e) => {
    e.preventDefault();
    drag = { x: e.clientX, y: e.clientY, move: e.button !== 0 || e.shiftKey };
    box.classList.add("dragging");
  });
  window.addEventListener("mousemove", (e) => {
    if (!drag || !V.gpu) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (drag.move) {  // the picture follows the mouse at the pivot's depth
      const { rows } = camera();
      const s = V.dist * V.gpu.depth / (V.info.zoom * S.zoom);
      V.pan = add(V.pan, add(scale(rows[0], -dx * s), scale(rows[1], -dy * s)));
    } else {
      V.yaw -= dx * 0.005;
      V.pitch = Math.max(-1.3, Math.min(1.3, V.pitch + dy * 0.005));
    }
    requestRender();
  });
  window.addEventListener("mouseup", () => {
    drag = null;
    box.classList.remove("dragging");
  });
  cv.addEventListener("contextmenu", (e) => e.preventDefault());
  cv.addEventListener("dblclick", () => { resetView(); render(); });
  box.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    V.dist = Math.max(0.03, Math.min(4, V.dist * Math.exp(e.deltaY * 0.0012)));
    requestRender();
  }, { passive: false });
  window.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "4" && S.detail) setMode(MODE);
  });

  document.addEventListener("studio:mode", (e) => show(e.detail === MODE));
  document.addEventListener("studio:field", (e) => {
    if (box.hidden) return;
    if (e.detail.name !== V.name) load(e.detail.name);
    else recheck();
  });
  document.addEventListener("studio:jobs", (e) => {
    if (e.detail.some((j) => j.name === V.name && String(j.kind).startsWith("3d"))) recheck();
  });
  // follow the 2D view's framing (Fit, 1:1, Native) and the viewport's size
  let framed = "";
  setInterval(() => {
    if (box.hidden) return;
    const f = [S.zoom, S.panX, S.panY, box.clientWidth, box.clientHeight].join();
    if (f !== framed) { framed = f; render(); }
  }, 150);
  if (S.mode === MODE) show(true);
})();
