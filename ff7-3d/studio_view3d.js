/* The preview's 3D view (FF7 Field Studio plug-in, loaded by studio_plugin.js): the field's 3D scene drawn with
   WebGL2 from the game's camera, framed like the 2D view so switching between New and 3D lines up. Drag to turn
   around the scene, right-drag (or shift-drag) to move, wheel to go closer, double-click for the game's camera.
   Server side: /api/3d/view/<field>, /api/3d/scene/<field>/<part> and /api/3d/life/<field> in studio_plugin.py.

   Life (on by default): the field's moving things as the game (FFNx-3d) shows them, from the same files: its
   particles (<field>_particles.toml: dust, embers, fireflies, snow, rain, blizzard; smoke, sparks, birds, motes, mist,
   bubbles), simulated by the engine's rules (src/ffnx3d_particles.cpp), and the scene's moving parts from its effects
   map and lighting settings (FFNx.lighting.frag): drifting sky, heat shimmer, waving flags, rippling water, grass in
   the wind. Not the game's light, haze or grade on them. */
(() => {
  const MODE = "3d";
  const LOOKS = { painted: "Painted", lit: "Painted, with light", clay: "Shape only" };
  const V = { name: null, info: null, gpu: null, token: 0, look: pref("view3d.look", "painted"),
              yaw: 0, pitch: 0, dist: 1, pan: [0, 0, 0],
              // life: on or off, what the server said (particles, fx settings, maps), the particle systems and mask,
              // the clock, the animation frame asked for
              life: pref("view3d.life", "1") === "1", L: null, systems: [], mask: null, t0: performance.now(),
              last: 0, anim: 0 };

  const css = document.createElement("style");
  css.textContent = `
    .v3d { position: absolute; inset: 0; z-index: 2; background: #0a0b0d; cursor: grab; }
    .v3d.dragging { cursor: grabbing; }
    .v3d canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
    .v3d-msg { position: absolute; inset: 0; display: none; flex-direction: column; gap: 10px; align-items: center;
               justify-content: center; color: var(--muted); text-align: center; padding: 30px; }
    .v3d-bar { position: absolute; left: 10px; bottom: 10px; display: flex; gap: 8px; align-items: center;
               padding: 5px 8px; border-radius: 6px; background: rgba(0,0,0,.6); cursor: default; }
    .v3d-bar label { display: flex; gap: 4px; align-items: center; font-size: 12px; color: var(--muted); }`;
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
      <label title="The field's moving things, as the game shows them: its particles (dust, smoke, sparks, birds,
snow and the like) and the scene's moving parts (drifting sky, heat shimmer, waving flags, rippling water). Off: the
scene stands still. They're drawn by the game's rules from the same files, but without the game's light and haze on
them."><input type="checkbox"> Life</label>
      <button class="small" title="Back to the game's camera (double-click)">Game camera</button>
      <span class="hint">drag: turn · right-drag: move · wheel: closer</span>
    </div>`;
  vp.appendChild(box);
  const cv = box.querySelector("canvas"), badge = box.querySelector(".badge"), msg = box.querySelector(".v3d-msg");
  const lookSel = box.querySelector("select"), lifeBox = box.querySelector(".v3d-bar input");
  lookSel.value = LOOKS[V.look] ? V.look : "painted";
  lookSel.onchange = () => { V.look = lookSel.value; setPref("view3d.look", V.look); render(); };
  lifeBox.checked = V.life;
  lifeBox.onchange = () => {
    V.life = lifeBox.checked;
    setPref("view3d.life", V.life ? "1" : "0");
    animate();
    render();
  };
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
  // the scene; with life on, its moving parts as FFNx.lighting.frag has them (the maps are BGRA as uploaded: .bgra
  // reads them as the engine does)
  const FS = `#version 300 es
    precision highp float;
    in vec3 vW, vN;
    in vec2 vUv;
    uniform sampler2D tex, fx, nml;
    uniform int look, life;
    uniform vec3 eye, light;
    uniform float time, water;
    uniform vec4 sceneFx;     // x sky drift, y heat shimmer, z flags; w the scene has an effects map
    uniform vec4 sceneFxDir;  // xy the sky's drift, zw along the flags (texture space)
    uniform vec4 wind;        // grass: x amount, y gust brightening, zw direction (texture space); its map: nml's alpha
    uniform int hasNml;
    out vec4 color;
    float cloudHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
    float cloudNoise(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      f = f * f * (3.0 - 2.0 * f);
      return mix(mix(cloudHash(i), cloudHash(i + vec2(1.0, 0.0)), f.x), mix(cloudHash(i + vec2(0.0, 1.0)), cloudHash(i + vec2(1.0, 1.0)), f.x), f.y);
    }
    vec3 at(vec2 uv) { return texture(tex, uv).bgr; }  // the scene's texture is BGRA, uploaded as it is
    void main() {
      vec2 uv = vUv;
      vec3 c = at(uv);
      if (life == 1 && look != 2) {
        float t = time;
        vec4 m = sceneFx.w > 0.0 ? texture(fx, uv).bgra : vec4(0.0, 0.0, 0.0, 1.0);  // R sky, G heat, B flags, A not water
        if (sceneFx.w > 0.0) {
          float drift = clamp((m.r - 0.04) / 0.96, 0.0, 1.0);
          vec2 off = vec2(0.0);
          if (m.g > 0.0 && sceneFx.y > 0.0)  // heat: a fine ripple running upward
            off += vec2(sin(uv.y * 1500.0 + t * 11.0) + 0.5 * sin(uv.x * 900.0 - t * 7.0),
                        0.6 * sin(uv.y * 1100.0 + uv.x * 400.0 + t * 9.0)) * 0.0004 * m.g * sceneFx.y;
          if (m.b > 0.0 && sceneFx.z > 0.0) {  // a flag: waves running out from its pole, across it
            float along = dot(uv, sceneFxDir.zw);
            float wave = sin(along * 1300.0 - t * 7.0) + 0.35 * sin(along * 2900.0 - t * 11.0 + uv.y * 300.0);
            off += vec2(-sceneFxDir.w, sceneFxDir.z) * wave * 0.0009 * m.b * sceneFx.z;
          }
          if (drift > 0.0 && sceneFx.x > 0.0) {  // the sky: two copies drifting, each faded out as it jumps back
            float phase = t / 9.0 + 0.6 * cloudNoise(uv * 3.0);
            float p0 = fract(phase), p1 = fract(phase + 0.5);
            vec2 d = sceneFxDir.xy * 0.006 * sceneFx.x * drift;
            c = mix(at(uv + off - d * p0), at(uv + off - d * p1), abs(1.0 - 2.0 * p0));
          } else if (m.g > 0.0 || m.b > 0.0) c = at(uv + off);
        }
        if (hasNml == 1 && wind.x > 0.0) {  // grass and leaves in the wind
          float a = texture(nml, uv).a;
          if (a < 0.99) {
            float grass = 1.0 - a;
            float along = dot(uv, wind.zw) * 9.0;
            float gust = (0.5 + 0.5 * sin(along * 6.2832 - t * 1.4)) * (0.65 + 0.35 * sin(along * 2.3 - t * 0.55 + uv.y * 4.0));
            float flutter = sin(uv.x * 1100.0 + t * 5.3) * sin(uv.y * 830.0 - t * 4.1);
            c = at(uv + wind.zw * (gust * 0.0011 + flutter * 0.00025) * grass * wind.x);
            c *= 1.0 + wind.y * grass * (gust - 0.35) * 0.1 * wind.x;
          }
        }
        float w = 1.0 - m.a;  // the water: its colour taken a little off where it is, moving
        if (water > 0.0 && sceneFx.w > 0.0 && w > 0.0) {
          vec2 wob = vec2(sin(uv.y * 180.0 + t * 1.7) + sin(uv.x * 97.0 - t * 1.1),
                          cos(uv.x * 160.0 - t * 1.3) + sin(uv.y * 71.0 + t * 0.9));
          c = at(uv + wob * water * 0.0008 * w);
        }
      }
      vec3 n = normalize(vN);
      if (dot(n, eye - vW) < 0.0) n = -n;  // both sides
      float l = max(dot(n, light), 0.0);
      if (look == 1) c *= 0.55 + 0.6 * l;
      else if (look == 2) c = vec3(0.78) * (0.22 + 0.78 * l);
      color = vec4(c, 1.0);
    }`;
  // particles: camera-facing quads already in view space, a sprite's alpha times their colour
  const PVS = `#version 300 es
    layout(location = 0) in vec3 cpos;
    layout(location = 1) in vec2 uv;
    layout(location = 2) in vec4 col;
    uniform mat4 proj;
    out vec2 vUv;
    out vec4 vCol;
    void main() { vUv = uv; vCol = col; gl_Position = proj * vec4(cpos, 1.0); }`;
  const PFS = `#version 300 es
    precision highp float;
    in vec2 vUv;
    in vec4 vCol;
    uniform sampler2D sprite;
    out vec4 color;
    void main() { color = vec4(vCol.rgb, vCol.a * texture(sprite, vUv).a); }`;
  let prog = null, pprog = null, pvao = null, pbuf = null;
  if (gl) {
    const shader = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const program = (vs, fs) => {
      const p = gl.createProgram();
      gl.attachShader(p, shader(gl.VERTEX_SHADER, vs));
      gl.attachShader(p, shader(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    };
    prog = program(VS, FS);
    pprog = program(PVS, PFS);
    pvao = gl.createVertexArray();
    gl.bindVertexArray(pvao);
    pbuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, pbuf);
    const stride = 9 * 4;  // view-space x y z, u v, r g b a
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 2, gl.FLOAT, false, stride, 12);
    gl.enableVertexAttribArray(2);
    gl.vertexAttribPointer(2, 4, gl.FLOAT, false, stride, 20);
    gl.bindVertexArray(null);
  }
  const uni = (n) => gl.getUniformLocation(prog, n);
  const puni = (n) => gl.getUniformLocation(pprog, n);

  function accessor(g, bin, i) {
    const a = g.accessors[i], bv = g.bufferViews[a.bufferView];
    const n = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
    const Arr = { 5126: Float32Array, 5125: Uint32Array, 5123: Uint16Array, 5121: Uint8Array }[a.componentType];
    return new Arr(bin, (bv.byteOffset || 0) + (a.byteOffset || 0), a.count * n);
  }

  // an uncompressed DDS (gltfout.write_dds: 128-byte header, or 148 with a DX10 one) as a texture, BGRA as it is
  function ddsTexture(dds, mips) {
    const head = new DataView(dds);
    const h = head.getUint32(12, true), w = head.getUint32(16, true);
    const off = head.getUint32(84, true) === 0x30315844 ? 148 : 128;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(dds, off, w * h * 4));
    if (mips) gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, mips ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  function release() {
    const g = V.gpu;
    if (!g) return;
    for (const b of g.buffers) gl.deleteBuffer(b);
    gl.deleteTexture(g.tex);
    if (g.fx) gl.deleteTexture(g.fx);
    if (g.nml) gl.deleteTexture(g.nml);
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

    const tex = ddsTexture(dds, true);
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
      vao, buffers, tex, fx: null, nml: null, count: idx.length,
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

  const lifeTime = () => (performance.now() - V.t0) / 1000;
  // the scene's moving parts this field has (the map and an amount for at least one of them)
  function fxActive() {
    const L = V.L, g = V.gpu;
    if (!L || !g) return false;
    const f = L.fx, sf = f.scene_fx || [0, 0, 0];
    return (!!g.fx && (sf.some((a) => a > 0) || (f.water_motion || 0) > 0)) || (!!g.nml && (f.grass_wind || 0) > 0);
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
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.disable(gl.CULL_FACE);
    gl.useProgram(prog);
    gl.uniformMatrix4fv(uni("view"), false, view);
    gl.uniformMatrix4fv(uni("proj"), false, proj);
    gl.uniform3fv(uni("eye"), pos);
    gl.uniform3fv(uni("light"), scale(light, 1 / Math.hypot(...light)));
    gl.uniform1i(uni("look"), Object.keys(LOOKS).indexOf(V.look));
    // the moving parts (FFNx.lighting.frag's uniforms, from the field's settings)
    const f = (V.L && V.L.fx) || {}, g = V.gpu;
    const sf = f.scene_fx || [0, 0, 0], sd = f.scene_fx_dir || [1, 0, 1, 0];
    const wd = f.grass_wind_dir || [1, 0], wl = Math.max(1e-4, Math.hypot(wd[0], wd[1]));
    gl.uniform1i(uni("life"), V.life && fxActive() ? 1 : 0);
    gl.uniform1f(uni("time"), lifeTime() % 3600);
    gl.uniform4f(uni("sceneFx"), sf[0], sf[1], sf[2], g.fx ? 1 : 0);
    gl.uniform4f(uni("sceneFxDir"), sd[0], sd[1], sd[2], sd[3]);
    gl.uniform1f(uni("water"), Math.min(2, Math.max(0, f.water_motion || 0)));
    gl.uniform4f(uni("wind"), Math.min(2, Math.max(0, f.grass_wind || 0)), Math.min(2, Math.max(0, f.grass_gust ?? 1)),
      wd[0] / wl, wd[1] / wl);
    gl.uniform1i(uni("hasNml"), g.nml ? 1 : 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, g.tex);
    gl.uniform1i(uni("tex"), 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, g.fx || g.tex);
    gl.uniform1i(uni("fx"), 1);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, g.nml || g.tex);
    gl.uniform1i(uni("nml"), 2);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindVertexArray(g.vao);
    gl.drawElements(gl.TRIANGLES, g.count, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
    if (V.life && V.systems.length && V.look !== "clay") drawParticles(rows, pos, proj, lifeTime());
  }

  let pending = false;
  function requestRender() {
    if (pending) return;
    pending = true;
    setTimeout(() => { pending = false; render(); }, 0);
  }

  // with life on and something moving: a frame each time the browser draws, the particles moved on first
  function tick(now) {
    V.anim = 0;
    if (box.hidden || !V.life || !V.gpu || !(V.systems.length || fxActive())) return;
    const dt = Math.min(0.1, Math.max(0, (now - V.last) / 1000));
    V.last = now;
    const t = lifeTime();
    for (const s of V.systems) simulate(s, dt, t);
    render();
    V.anim = requestAnimationFrame(tick);
  }
  function animate() {
    if (V.anim || box.hidden || !V.life || !V.gpu || !(V.systems.length || fxActive())) return;
    V.last = performance.now();
    V.anim = requestAnimationFrame(tick);
  }

  /* ---------------------------------------------------------- particles (src/ffnx3d_particles.cpp, in JS) */

  const SOURCES = new Set(["smoke", "sparks", "birds", "motes", "mist", "bubbles"]);
  const KINDS = new Set(["dust", "embers", "fireflies", "snow", "rain", "blizzard", ...SOURCES]);
  const GLOWS = new Set(["dust", "embers", "fireflies", "sparks", "motes", "bubbles"]);  // add light; the rest cover
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const rnd = (a, b) => a + (b - a) * Math.random();
  const rndInt = (n) => Math.min(n - 1, Math.floor(Math.random() * n));
  const num = (v, d) => (typeof v === "number" && isFinite(v) ? v : d);
  const pair = (v, d) => (Array.isArray(v) && v.length === 2 ? [num(v[0], d[0]), num(v[1], d[1])] : d.slice());
  const points = (v) => (Array.isArray(v) ? v.slice(0, v.length - (v.length % 3)).map((x) => num(x, 0)) : []);

  function readSpec(c) {
    const s = {
      kind: KINDS.has(c.kind) ? c.kind : "none", count: clamp(Math.trunc(num(c.count, 0)), 0, 4000),
      size: num(c.size, 5), alpha: num(c.alpha, 0.35), speed: clamp(num(c.speed, 1), 0.05, 20),
      color: Array.isArray(c.color) && c.color.length === 3 ? c.color.map((x) => num(x, 1)) : [1, 1, 1],
      wind: Array.isArray(c.wind) && c.wind.length === 3 ? c.wind.map((x) => num(x, 0)) : [0, 0, 0],
      streak: clamp(num(c.streak, 0.05), 0, 0.5), floor: points(c.floor), lift: pair(c.lift, [0, 100]),
      spread: num(c.spread, 50), box: Array.isArray(c.box) && c.box.length === 6 ? c.box.map((x) => num(x, 0)) : [0, 0, 0, 1, 1, 1],
      points: points(c.points), rise: num(c.rise, 40), grow: clamp(num(c.grow, 3), 0.1, 20),
      life: pair(c.life, [4, 8]), every: pair(c.every, [15, 40]),
    };
    s.life[0] = Math.max(0.1, s.life[0]); s.life[1] = Math.max(s.life[0], s.life[1]);
    s.every[0] = Math.max(1, s.every[0]); s.every[1] = Math.max(s.every[0], s.every[1]);
    return s;
  }

  // the screen mask (F23): world -> cell projection, cells 0..1 for every system and for the field-wide kind
  function readMask(c) {
    const p = c.mask_project, size = c.mask_size;
    if (!Array.isArray(p) || p.length !== 12 || !Array.isArray(size) || size.length !== 2) return null;
    const m = { m: p.map((x) => num(x, 0)), w: clamp(size[0] | 0, 0, 1024), h: clamp(size[1] | 0, 0, 1024),
                all: null, main: null, near: Math.max(0, num(c.near, 0)) };
    const cells = (s) => {
      if (typeof s !== "string" || s.length !== m.w * m.h) return null;
      const out = new Float32Array(s.length);
      for (let i = 0; i < s.length; i++) out[i] = parseInt(s[i], 16) / 15 || 0;
      return out;
    };
    m.all = cells(c.mask);
    m.main = cells(c.mask_main);
    if (m.main && m.all) for (let i = 0; i < m.main.length; i++) m.main[i] *= m.all[i];
    return m;
  }

  function maskAt(cells, x, y) {
    const M = V.mask, u = x - 0.5, v = y - 0.5;
    const x0 = Math.floor(u), y0 = Math.floor(v), fx = u - x0, fy = v - y0;
    let sum = 0;
    for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
      const cx = x0 + i, cy = y0 + j;
      const c = cx >= 0 && cy >= 0 && cx < M.w && cy < M.h ? cells[cy * M.w + cx] : 0;
      sum += c * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
    }
    return sum;
  }

  function buildSystems(file) {
    V.systems = [];
    V.mask = null;
    if (!file) return;
    V.mask = readMask(file);
    const main = readSpec(file);
    if (main.kind !== "none" && !SOURCES.has(main.kind)) V.systems.push({ spec: main, particles: [], wait: 0, fieldWide: true });
    for (const c of Array.isArray(file.source) ? file.source : []) {
      const spec = readSpec(c);
      if (SOURCES.has(spec.kind) && spec.points.length >= 3 && spec.count > 0) V.systems.push({ spec, particles: [], wait: 0, fieldWide: false });
    }
  }

  const pointOf = (spec, i) => { const k = (i % (spec.points.length / 3)) * 3; return [spec.points[k], spec.points[k + 1], spec.points[k + 2]]; };

  // a new particle somewhere in the box (falling kinds: at its top), with its kind's motion and lifetime
  function spawn(spec, q, anywhere) {
    const b = spec.box, k = spec.kind;
    q.p = [rnd(b[0], b[3]), rnd(b[1], b[4]), rnd(b[2], b[5])];
    const onFloor = spec.floor.length > 0 && (k === "dust" || k === "embers" || k === "fireflies");
    if (onFloor) {  // above a point of the walkable floor
      const i = rndInt(spec.floor.length / 3) * 3;
      const top = k === "embers" && !anywhere ? spec.lift[0] + (spec.lift[1] - spec.lift[0]) * 0.2 : spec.lift[1];
      q.p = [spec.floor[i] + rnd(-spec.spread, spec.spread), spec.floor[i + 1] + rnd(-spec.spread, spec.spread),
             spec.floor[i + 2] + rnd(spec.lift[0], top)];
    }
    if (k === "motes" || k === "mist" || k === "bubbles") {  // around a random point
      const c = pointOf(spec, rndInt(spec.points.length / 3));
      const sz = k === "bubbles" ? 0 : k === "mist" ? 0.3 : 1;
      q.p = [c[0] + rnd(-spec.spread, spec.spread), c[1] + rnd(-spec.spread, spec.spread), c[2] + rnd(-sz, sz) * spec.spread];
    }
    if (k === "smoke" || k === "sparks") {  // from its own point, a little around it
      const c = pointOf(spec, q.src);
      q.p = [c[0] + rnd(-spec.spread, spec.spread), c[1] + rnd(-spec.spread, spec.spread), c[2] + rnd(-spec.spread, spec.spread * 0.5)];
    }
    q.age = 0;
    q.phase = rnd(0, 6.2831853);
    const r = spec.rise, w = spec.wind;
    switch (k) {
      case "dust": q.v = [rnd(-12, 12), rnd(-12, 12), rnd(-4, 8)]; q.life = rnd(6, 14); break;
      case "embers":
        if (!onFloor) q.p[2] = anywhere ? q.p[2] : b[2];
        q.v = [rnd(-15, 15), rnd(-15, 15), rnd(50, 120)]; q.life = rnd(3, 6); break;
      case "fireflies": q.v = [rnd(-25, 25), rnd(-25, 25), rnd(-8, 8)]; q.life = rnd(5, 10); break;
      case "snow": q.p[2] = anywhere ? q.p[2] : b[5]; q.v = [rnd(-15, 15), rnd(-15, 15), -rnd(45, 85)]; q.life = 1e9; break;
      case "rain": q.p[2] = anywhere ? q.p[2] : b[5]; q.v = [120, 60, -rnd(1800, 2400)]; q.life = 1e9; break;
      case "blizzard": {
        const s = rnd(0.75, 1.25);
        q.v = [w[0] * s + rnd(-20, 20), w[1] * s + rnd(-20, 20), w[2] * s - rnd(60, 130)];
        q.life = rnd(1.5, 3.5); break;
      }
      case "smoke": q.v = [rnd(-0.15, 0.15) * r, rnd(-0.15, 0.15) * r, r * rnd(0.75, 1.25)]; q.life = rnd(spec.life[0], spec.life[1]); break;
      case "sparks": q.v = [rnd(-0.3, 0.3) * r, rnd(-0.3, 0.3) * r, r * rnd(0.6, 1.4)]; q.life = rnd(spec.life[0], spec.life[1]); break;
      case "motes": q.v = [rnd(-1, 1) * r, rnd(-1, 1) * r, rnd(-0.6, 0.6) * r]; q.life = rnd(spec.life[0], spec.life[1]); break;
      case "mist": q.v = [w[0] * rnd(0.7, 1.3), w[1] * rnd(0.7, 1.3), rnd(-0.1, 0.2) * r]; q.life = rnd(spec.life[0], spec.life[1]); break;
      case "bubbles": q.v = [0, 0, r * rnd(0.75, 1.25)]; q.life = rnd(spec.life[0], spec.life[1]); break;
      default: q.v = [0, 0, 0]; q.life = 1;
    }
    if (!SOURCES.has(k)) q.v = scale(q.v, spec.speed);
    if (anywhere) {
      q.age = rnd(0, Math.min(q.life, 10));
      if (SOURCES.has(k)) {  // already on its way
        q.p[0] += (q.v[0] + w[0] * 0.5) * q.age;
        q.p[1] += (q.v[1] + w[1] * 0.5) * q.age;
        q.p[2] += q.v[2] * q.age * (k === "smoke" ? 0.7 : 0.5);
      }
    }
  }

  // birds: a flock setting off from one point of the sky toward another far from it, in a loose V
  function launchFlock(s) {
    const spec = s.spec, n = spec.points.length / 3;
    if (n < 2) return;
    const a = pointOf(spec, rndInt(n));
    let b = a, best = -1;
    for (let k = 0; k < 6; k++) {
      const c = pointOf(spec, rndInt(n)), d = dot(sub(c, a), sub(c, a));
      if (d > best) { best = d; b = c; }
    }
    const dist = Math.sqrt(Math.max(best, 1e-6));
    const dir = scale(sub(b, a), 1 / dist);
    let side = [-dir[1], dir[0], 0];
    const sl = Math.max(1e-4, Math.hypot(side[0], side[1]));
    side = [side[0] / sl, side[1] / sl, 0];
    const gap = spec.size * 2.2, speed = spec.rise * rnd(0.85, 1.15);
    const birds = Math.min(s.particles.length, Math.trunc(rnd(3, Math.max(4, spec.count + 0.99))));
    s.particles.forEach((q, k) => {
      if (k >= birds) { q.age = q.life = 1; return; }
      const row = Math.trunc((k + 1) / 2), sign = k % 2 ? 1 : -1;
      const back = row * gap * rnd(0.8, 1.2), across = sign * row * gap * rnd(0.6, 0.9);
      q.p = [a[0] - dir[0] * back + side[0] * across, a[1] - dir[1] * back + side[1] * across, a[2] - dir[2] * back + rnd(-0.3, 0.3) * gap];
      q.v = scale(dir, speed);
      q.age = 0;
      q.life = (dist + back) / speed;
      q.phase = rnd(0, 6.2831853);
    });
    s.wait = rnd(spec.every[0], spec.every[1]) + dist / speed;
  }

  function simulate(s, dt, t) {
    const spec = s.spec, k = spec.kind;
    if (s.particles.length !== spec.count) {
      s.particles = Array.from({ length: spec.count }, (_, i) => ({ p: [0, 0, 0], v: [0, 0, 0], age: 1, life: 1, phase: 0, src: i }));
      if (k === "birds") s.wait = rnd(2, spec.every[0]);  // the first flock soon after arriving
      else for (const q of s.particles) spawn(spec, q, true);
    }
    if (k === "birds") {
      if ((s.wait -= dt) <= 0) launchFlock(s);
      for (const q of s.particles) {
        if (q.age >= q.life) continue;
        q.age += dt;
        for (let i = 0; i < 3; i++) q.p[i] += q.v[i] * dt;
      }
      return;
    }
    const b = spec.box;
    for (const q of s.particles) {
      q.age += dt;
      if (k === "fireflies" || k === "dust") {  // wander
        q.v[0] += rnd(-1, 1) * 20 * spec.speed * dt;
        q.v[1] += rnd(-1, 1) * 20 * spec.speed * dt;
        q.v[2] += rnd(-1, 1) * 6 * spec.speed * dt;
      }
      if (k === "smoke") {  // rising slower as it cools, carried more and more by the wind, churning
        const f = Math.min(1, 0.6 * dt), churn = spec.rise * 0.5 * dt;
        q.v[0] += (spec.wind[0] - q.v[0]) * f + rnd(-1, 1) * churn;
        q.v[1] += (spec.wind[1] - q.v[1]) * f + rnd(-1, 1) * churn;
        q.v[2] += (spec.rise * 0.35 + spec.wind[2] - q.v[2]) * f * 0.5;
      }
      if (k === "motes") {  // wandering
        q.v[0] += rnd(-1, 1) * spec.rise * 1.5 * dt;
        q.v[1] += rnd(-1, 1) * spec.rise * 1.5 * dt;
        q.v[2] += rnd(-1, 1) * spec.rise * 0.8 * dt;
        const f = Math.min(1, 0.5 * dt);
        q.v = q.v.map((x) => x - x * f);
      }
      if (k === "bubbles") {  // wobbling as they rise
        q.p[0] += Math.sin(t * 5 + q.phase) * spec.size * 0.6 * dt;
        q.p[1] += Math.cos(t * 4.3 + q.phase) * spec.size * 0.6 * dt;
      }
      if (k === "sparks") {  // slowing, fluttering
        q.v[0] += rnd(-1, 1) * spec.rise * 1.5 * dt;
        q.v[1] += rnd(-1, 1) * spec.rise * 1.5 * dt;
        q.v[2] -= spec.rise * 0.6 * dt;
      }
      const sway = k === "snow" || k === "embers" ? Math.sin(t * 1.3 + q.phase) * 20 * spec.speed : 0;
      const gust = k === "blizzard" ? 1 + 0.35 * Math.sin(t * 0.8) + 0.15 * Math.sin(t * 2.3 + q.phase) : 1;
      q.p[0] += (q.v[0] * gust + sway) * dt;
      q.p[1] += q.v[1] * gust * dt;
      q.p[2] += q.v[2] * gust * dt;
      let out = false;
      if (!SOURCES.has(k)) {
        out = q.p[2] < b[2] - 50 * spec.speed || q.p[2] > b[5] + 50 * spec.speed || q.p[0] < b[0] || q.p[0] > b[3] ||
          q.p[1] < b[1] || q.p[1] > b[4];
      }
      if (q.age > q.life || out) spawn(spec, q, false);
    }
  }

  // white sprites with each pixel's coverage in alpha: a soft dot, a lumpier puff (smoke, mist), two bird frames
  const SPRITES = {};
  function spriteTexture(name) {
    if (SPRITES[name]) return SPRITES[name];
    const hash2 = (x, y) => {
      let h = Math.imul(x, 374761393) + Math.imul(y, 668265263) >>> 0;
      h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
      return ((h ^ (h >>> 16)) & 0xffff) / 65535;
    };
    const noise = (x, y) => {
      const ix = Math.floor(x), iy = Math.floor(y);
      let fx = x - ix, fy = y - iy;
      fx = fx * fx * (3 - 2 * fx); fy = fy * fy * (3 - 2 * fy);
      const a = hash2(ix, iy), b = hash2(ix + 1, iy), c = hash2(ix, iy + 1), d = hash2(ix + 1, iy + 1);
      return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
    };
    const shapes = {
      dot: [32, 32, (x, y) => {
        const dx = (x + 0.5) / 32 * 2 - 1, dy = (y + 0.5) / 32 * 2 - 1;
        return Math.exp(-3.5 * (dx * dx + dy * dy)) - 0.03;
      }],
      puff: [64, 64, (x, y) => {
        const dx = (x + 0.5) / 64 * 2 - 1, dy = (y + 0.5) / 64 * 2 - 1;
        const n = 0.5 * noise(x / 9, y / 9) + 0.3 * noise(x / 4.5 + 7, y / 4.5 + 3) + 0.2 * noise(x / 2.2 + 1, y / 2.2 + 9);
        return Math.exp(-2.2 * (dx * dx + dy * dy)) * (0.7 + 0.7 * n) - 0.03;
      }],
      bird: [32, 16, (x, y) => {
        const frame = Math.trunc(x / 16), px = (x % 16) + 0.5, py = y + 0.5;
        const tipY = frame === 0 ? 3 : 13, elbowY = frame === 0 ? 6 : 10;
        const seg = (ax, ay, bx, by) => {
          const vx = bx - ax, vy = by - ay, wx = px - ax, wy = py - ay;
          const t = clamp((wx * vx + wy * vy) / (vx * vx + vy * vy), 0, 1);
          return Math.hypot(wx - vx * t, wy - vy * t);
        };
        const d = Math.min(seg(8, 8, 4.5, elbowY), seg(4.5, elbowY, 0.8, tipY), seg(8, 8, 11.5, elbowY), seg(11.5, elbowY, 15.2, tipY));
        const body = Math.sqrt((px - 8) * (px - 8) + (py - 8.5) * (py - 8.5) * 1.5);
        return Math.max(1.8 - d / 0.9, 2.2 - body / 1.1);
      }],
    };
    const [w, h, alpha] = shapes[name];
    const px = new Uint8Array(w * h * 4);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      px[i] = px[i + 1] = px[i + 2] = 255;
      px[i + 3] = Math.round(clamp(alpha(x, y), 0, 1) * 255);
    }
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, px);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    SPRITES[name] = tex;
    return tex;
  }

  // each system's particles as camera-facing quads in the preview camera's view space (rain, blizzard, sparks:
  // streaks along their motion), over the scene, depth tested and not written
  const CORNERS = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
  let pverts = new Float32Array(0);
  function drawParticles(rows, pos, proj, t) {
    const view = (p) => { const d = sub(p, pos); return [dot(rows[0], d), dot(rows[1], d), dot(rows[2], d)]; };
    const M = V.mask;
    gl.useProgram(pprog);
    gl.uniformMatrix4fv(puni("proj"), false, proj);
    gl.uniform1i(puni("sprite"), 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.enable(gl.BLEND);
    gl.depthMask(false);
    gl.bindVertexArray(pvao);
    gl.bindBuffer(gl.ARRAY_BUFFER, pbuf);
    for (const s of V.systems) {
      const spec = s.spec, k = spec.kind;
      if (pverts.length < s.particles.length * 54) pverts = new Float32Array(s.particles.length * 54);
      let n = 0;
      for (const q of s.particles) {
        let a = spec.alpha, size = spec.size;
        const u = clamp(q.age / q.life, 0, 1);
        if (k === "smoke") { a *= Math.min(1, u / 0.04) * (1 - u); size *= 0.9 + spec.grow * u; }
        else if (k === "sparks") a *= (1 - u) * (0.6 + 0.4 * Math.sin(t * 23 + q.phase * 5));
        else if (k === "motes") {
          const glint = 0.5 + 0.5 * Math.sin(t * 1.7 + q.phase * 3);
          a *= Math.sin(Math.PI * u) * (0.25 + 0.75 * glint * glint);
        } else if (k === "mist") { a *= Math.sin(Math.PI * u); size *= 1 + (spec.grow - 1) * u; }
        else if (k === "bubbles") a *= Math.min(1, u / 0.1) * Math.min(1, (1 - u) / 0.15);
        else if (k === "birds") {
          if (q.age >= q.life) continue;
          a *= Math.min(1, q.age / 1.5, (q.life - q.age) / 1.5);
        } else if (q.life < 1e8) a *= Math.sin(Math.PI * u);
        if (k === "fireflies") a *= Math.max(0, Math.sin(t * 2.2 + q.phase));
        if (M) {  // only where the scene is on the game's canvas, and none right in front of its camera
          const m = M.m, w = q.p;
          const hz = m[8] * w[0] + m[9] * w[1] + m[10] * w[2] + m[11];
          if (hz <= 1) continue;
          const cells = s.fieldWide && M.main ? M.main : M.all;
          const mx = (m[0] * w[0] + m[1] * w[1] + m[2] * w[2] + m[3]) / hz, my = (m[4] * w[0] + m[5] * w[1] + m[6] * w[2] + m[7]) / hz;
          if (cells) a *= maskAt(cells, mx, my);
          // the preview's own: none past the game's picture (the game's screen ends there; the preview's doesn't)
          else a *= clamp(Math.min(mx, my, M.w - mx, M.h - my) + 0.5, 0, 1);
          if (s.fieldWide && M.near > 0 && hz < M.near) {
            size *= hz / M.near;
            a *= clamp(2 * hz / M.near - 1, 0, 1);
          }
        }
        if (a <= 0.01) continue;
        const p = q.p.slice();
        if (k === "birds") p[2] += Math.sin(t * 1.3 + q.phase) * spec.size * 0.4;  // rising and dipping
        const c = view(p);
        if (c[2] <= 1) continue;  // behind the camera
        let ux = size, uy = 0, vx = 0, vy = size;
        if (k === "smoke" || k === "mist") {  // each puff turned its own way, slowly turning
          const r = q.phase + q.age * (q.src % 2 ? 0.25 : -0.25), cs = Math.cos(r), sn = Math.sin(r);
          ux = cs * size; uy = sn * size; vx = -sn * size; vy = cs * size;
        }
        if (k === "birds") vy = size * 0.5;  // the sprite is twice as wide as tall
        if (k === "rain" || k === "blizzard" || k === "sparks") {
          const back = k === "rain" ? 0.03 : k === "sparks" ? 0.04 : spec.streak;
          const ct = view(sub(p, scale(q.v, back)));
          const dx = c[0] - ct[0], dy = c[1] - ct[1], len = Math.max(Math.hypot(dx, dy), 1e-3);
          vx = dx / 2; vy = dy / 2;
          if (k === "sparks" && len < size) { vx *= size / len; vy *= size / len; }
          const across = k === "sparks" ? size : size * 0.5;
          ux = -dy / len * across; uy = dx / len * across;
        }
        const wingsDown = k === "birds" && Math.sin(t * 0.7 + q.phase) > -0.4 && Math.sin(t * 13 + q.phase * 3) < 0;
        // two triangles: corners 0 1 2, 0 2 3
        for (const ci of [0, 1, 2, 0, 2, 3]) {
          const [cx, cy] = CORNERS[ci], o = n * 9;
          if (o + 9 > pverts.length) {
            const grown = new Float32Array(pverts.length * 2 + 54);
            grown.set(pverts);
            pverts = grown;
          }
          pverts[o] = c[0] + cx * ux + cy * vx;
          pverts[o + 1] = c[1] + cx * uy + cy * vy;
          pverts[o + 2] = c[2];
          let tu = (cx + 1) / 2;
          if (k === "birds") tu = (tu + (wingsDown ? 1 : 0)) / 2;
          pverts[o + 3] = tu;
          pverts[o + 4] = (cy + 1) / 2;
          pverts[o + 5] = clamp(spec.color[0], 0, 1);
          pverts[o + 6] = clamp(spec.color[1], 0, 1);
          pverts[o + 7] = clamp(spec.color[2], 0, 1);
          pverts[o + 8] = clamp(a, 0, 1);
          n++;
        }
      }
      if (!n) continue;
      // glowing kinds add light; smoke, snow, rain and birds cover what's behind them (the canvas keeps its alpha)
      if (GLOWS.has(k)) gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE, gl.ZERO, gl.ONE);
      else gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE);
      gl.bindTexture(gl.TEXTURE_2D, spriteTexture(k === "smoke" || k === "mist" ? "puff" : k === "birds" ? "bird" : "dot"));
      gl.bufferData(gl.ARRAY_BUFFER, pverts.subarray(0, n * 9), gl.STREAM_DRAW);
      gl.drawArrays(gl.TRIANGLES, 0, n);
    }
    gl.bindVertexArray(null);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
  }

  /* ---------------------------------------------------------- the field's scene */

  function label() {
    const i = V.info;
    const v = S.detail && S.detail.versions.find((x) => x.id === i.version);
    badge.textContent = "3D scene" + (v ? ` · from ${verKind(v)} ${fmtWhen(v.created)}` : "") +
      (i.up_to_date ? "" : " · out of date: the field has another version in the game now");
    badge.hidden = false;
  }

  // the field's life: its particles and moving parts' settings, and the maps they need
  async function loadLife(name, token) {
    const L = await fetch(`/api/3d/life/${name}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (token !== V.token || !V.gpu) return;
    if (!L) { V.L = null; V.systems = []; return; }
    const g = V.gpu, at = `/api/3d/scene/${name}/`, q = `?t=${L.stamp}`;
    const need = (key) => L.maps && L.maps[key];
    const [fx, nml] = await Promise.all([
      need("fx") ? fetch(at + "fx.dds" + q).then((r) => (r.ok ? r.arrayBuffer() : null)).catch(() => null) : null,
      need("nml") && (L.fx.grass_wind || 0) > 0 ? fetch(at + "nml.dds" + q).then((r) => (r.ok ? r.arrayBuffer() : null)).catch(() => null) : null,
    ]);
    if (token !== V.token || V.gpu !== g) return;
    if (g.fx) gl.deleteTexture(g.fx);
    if (g.nml) gl.deleteTexture(g.nml);
    g.fx = fx ? ddsTexture(fx, false) : null;
    g.nml = nml ? ddsTexture(nml, false) : null;
    V.L = L;
    buildSystems(L.particles);
    V.t0 = performance.now();
    animate();
    render();
  }

  async function load(name) {
    const token = ++V.token;
    V.name = name;
    V.info = null;
    V.L = null;
    V.systems = [];
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
    loadLife(name, token);
  }

  // the scene may have been rebuilt (a 3D job, Automatic): reload it when its file changed; its life likewise
  async function recheck() {
    if (box.hidden || !V.name) return;
    const info = await fetch(`/api/3d/view/${V.name}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (!info || box.hidden || info.built !== !!(V.info && V.info.built) || (info.built && info.stamp !== V.info.stamp)) {
      if (info && !box.hidden) load(V.name);
      return;
    }
    if (info.built) { V.info = { ...V.info, ...info }; label(); }
    if (info.built && V.gpu) {
      const L = await fetch(`/api/3d/life/${V.name}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (L && (!V.L || L.stamp !== V.L.stamp)) loadLife(V.name, V.token);
    }
  }

  function show(on) {
    box.hidden = !on;
    if (!on) return;
    if (V.name !== S.current) load(S.current);
    else if (V.gpu) { label(); render(); animate(); recheck(); }
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
