"""FF7 Field Studio plug-in for the 3D scenes.

Adds a 3D tab to each field: build the field's 3D scene from the version the game uses (a "3D scene" job in the
Studio's queue), its 3D options, and the game running FFNx-3d (start it, show the field, switch between the 3D scene,
the AI images in 2D and the original, screenshots). Scenes can follow a field's current version by themselves.

The Studio loads it through studio/plugins/ff7_3d.py, which stays out of the Studio's git (.git/info/exclude):
the 3D work is private."""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from flask import Blueprint, abort, jsonify, request, send_file
from PIL import Image

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "studio_settings.json"  # {"auto": "final" | "built" | "off" | "all"}
AUTO = ("final", "built", "off", "all")
LOOKS = ("painted", "relit")
# the look's light and colour settings a field can change, with their ranges (the 3D tab's sliders)
LIGHT = {"shine": (0.0, 1.0), "light_tint": (0.0, 1.0), "shading": (0.0, 1.0), "brightness": (0.3, 2.0),
         "saturation": (0.5, 1.6), "sun_turn": (-180.0, 180.0), "sun_height": (-60.0, 60.0)}
# around the characters (ff3d/atmos.py): their shadows, haze and the lights' glow
EFFECTS = {"char_shadow": (0.0, 1.0), "char_haze": (0.0, 1.0), "glow": (0.0, 1.0), "char_match": (0.0, 1.0),
           "char_light": (0.0, 1.0), "char_local": (0.0, 1.0), "char_outline": (0.0, 1.0), "char_soft": (0.0, 1.0),
           "contact_shadow": (0.0, 1.0), "char_finish": (0.0, 1.0), "char_fill": (0.0, 2.0), "char_sss": (0.0, 1.0),
           "char_highlight": (0.0, 1.0), "char_volume": (0.0, 1.0), "char_ao": (0.0, 1.0),
           "particle_amount": (0.0, 2.0), "water_motion": (0.0, 1.0),
           "clouds": (0.0, 1.0), "flicker": (0.0, 1.0), "grass": (0.0, 1.0), "occlusion": (0.0, 1.0), "gloss": (0.0, 1.0),
           "metal": (0.0, 1.0), "bump": (0.0, 1.0), "smoke": (0.0, 1.0), "fire": (0.0, 1.0), "windows": (0.0, 1.0),
           "sky": (0.0, 1.0), "flags": (0.0, 1.0), "birds": (0.0, 1.0), "blink": (0.0, 1.0), "motes": (0.0, 1.0),
           "mist": (0.0, 1.0), "bubbles": (0.0, 1.0), "char_lamps": (0.0, 1.0), "reflections": (0.0, 1.0)}
SHAPE = ("sway", "reveal")  # changing these rebuilds the scene
UI_KEYS = ("look", *SHAPE, "sun", "particles", *LIGHT, *EFFECTS)  # what the 3D tab shows
PARTICLES = ("auto", "none", "embers", "fireflies", "snow", "blizzard", "rain")  # no dust since 2026-11-09
SUNS = ("painting", "default")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: builds run without a console popping up


def settings():
    s = {"auto": "final"}
    if SETTINGS.exists():
        s.update(json.loads(SETTINGS.read_text()))
    return s


def register(studio):
    os.environ["FF7_STUDIO"] = str(studio.HERE)  # the pipeline reads this Studio's versions (and so do its builds)
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE / "tools"))
    import live
    from ff3d import atmos, build, common

    project, worker = studio.project, studio.worker
    starting = {"since": 0.0}
    build.adopt_shared()  # once: one set for all fields, from the per-field copies of before

    # ------------------------------------------------------------ state

    def source_of(name):
        """(version id, render mtime) the field's scene was built from, or (None, None)."""
        r = common.WORK / name / "report.json"
        if r.exists():
            src = json.loads(r.read_text()).get("source")
            if src:
                return src["version"], src["mtime"]
            stamp = common.WORK / name / "aligned_from.txt"  # scenes built before reports named their version
            if stamp.exists():
                path, mtime = stamp.read_text().rsplit(" ", 1)
                return Path(path).parent.name, float(mtime)
        return None, None

    def up_to_date(name):
        vid, mtime = source_of(name)
        cur = project.current(name)
        if not vid or not cur or vid != cur:
            return False
        render = project.versions.render_path(name, cur)
        return render is not None and abs(render.stat().st_mtime - mtime) < 1

    def built(name):
        return (common.MESH / name / "scene.gltf").exists() and (common.WORK / name / "report.json").exists()

    def building(name):
        with worker.lock:
            cur = worker.current
            return bool(cur) and cur["job"]["name"] == name and cur["job"]["kind"] == "3d"

    def game():
        st = live.status()
        p = live.ROOT / "status.txt"
        running = p.exists() and time.time() - p.stat().st_mtime < 3  # FFNx-3d rewrites it twice a second
        if running:
            starting["since"] = 0.0
        return {"running": running, "starting": time.time() - starting["since"] < 60,
                "field": st.get("field") if running else None, "view": st.get("view") if running else None}

    def field_state(name):
        work = common.WORK / name
        rep = json.loads((work / "report.json").read_text()) if (work / "report.json").exists() else None
        err = work / "error.txt"
        failed = err.exists() and (not rep or err.stat().st_mtime > (work / "report.json").stat().st_mtime)
        vid, _ = source_of(name)
        o, all_o = _options(name), build.shared_options()
        shot = work / "ingame.png"
        return {
            "name": name, "built": built(name), "up_to_date": built(name) and up_to_date(name),
            "version": vid, "current": project.current(name),
            "error": err.read_text().strip().splitlines()[-1] if failed else "",
            "report": {k: rep.get(k) for k in ("triangles", "full_triangles", "vertices", "bytes", "cracks", "built",
                                               "seconds")} | {"fit": (rep.get("fit") or {}).get("median_err")}
            if rep else None,
            "options": shown(o),            # this field's (all fields' and its own changes)
            "shared": shown(all_o),         # all fields'
            "own_keys": [k for k in build.own(name) if k in UI_KEYS],
            "shared_keys": [k for k in build.shared() if k in UI_KEYS],  # all fields' changes from the built-in defaults
            "shared_light_defaults": build.look_light(build.DEFAULTS, all_o["look"]),
            "particles_auto": atmos.particle_kind(name),
            # what life.py found in the painting (chimneys, fires, windows, flags, sky), as of the last look
            "life": json.loads((common.WORK / name / "life.json").read_text())
            if (common.WORK / name / "life.json").exists() else None,
            "effect_defaults": {k: build.DEFAULTS[k] for k in EFFECTS},
            "light_defaults": build.look_light(build.DEFAULTS, o["look"]),
            "shot": shot.stat().st_mtime if shot.exists() else None,
            "auto": settings()["auto"], "game": game(),
        }

    def _options(name):
        return build.field_options(name)

    def shown(o):
        """The settings as the 3D tab shows them."""
        return {"look": o["look"], "sway": o["sway"], "reveal": o["reveal"], **build.look_light(o, o["look"]),
                "sun": o["sun"], "particles": o["particles"], **{k: float(o[k]) for k in EFFECTS}}

    # ------------------------------------------------------------ the game

    def refresh_in_game(name):
        """Re-enter the field if the game is in it, where the player stands: its pages and glow reload too."""
        g = game()
        if g["running"] and g["field"] == name:
            tri = live.status().get("triangle", "0")
            live.send(f"jump {common.spot(name)[0]} {tri}")

    def show(name):
        fid, tri = common.spot(name)
        live.send(f"jump {fid} {tri}")

    def start_game(name):
        starting["since"] = time.time()
        subprocess.Popen([sys.executable, str(HERE / "tools" / "boot.py"), name], cwd=str(HERE),
                         creationflags=NO_WINDOW)

    # ------------------------------------------------------------ jobs

    def run_pipeline(w, args, job):
        """run.py <args> in its own process, its '@ share step' lines as the job's progress."""
        proc = subprocess.Popen([sys.executable, "-u", str(HERE / "run.py"), *args], cwd=str(HERE),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env={**os.environ, "FF7_STUDIO": str(studio.HERE)},
                                creationflags=NO_WINDOW)
        stop = threading.Event()

        def watch_cancel():  # a cancel stops the build at once, not at its next step
            while not stop.wait(0.3):
                if w.cancel_flag:
                    proc.kill()
                    return
        threading.Thread(target=watch_cancel, daemon=True).start()
        tail = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith("@ "):
                    share, what = line[2:].split(" ", 1)
                    w._stage(what, float(share))
                elif line:
                    tail = (tail + [line])[-20:]
            rc = proc.wait()
        finally:
            stop.set()
        if w.cancel_flag:
            raise InterruptedError("cancelled")
        if rc:
            failed = [t for t in tail if "FAILED" in t]
            raise RuntimeError((failed or tail or ["the 3D build stopped"])[-1].split("FAILED", 1)[-1].strip())

    def run_build(w, job):
        name = job["name"]
        if not project.current(name) and not project.versions.ids(name):
            raise RuntimeError("no render yet: generate one first")
        run_pipeline(w, ["build", "--progress", name], job)
        w._stage("showing it in the game", 0.98)
        try:
            refresh_in_game(name)
        except Exception:
            pass  # the game is optional

    # Look steps run side by side (user, 2026-11-09: a change for all fields took hours, one field after another): a
    # look step takes the look steps queued right behind it along, up to LOOK_PARALLEL in all, each in its own process
    # with LOOK_THREADS maths threads (6 x 4 on this PC's 32 cores, not 6 x 32 fighting over them)
    LOOK_PARALLEL = 6
    LOOK_THREADS = "4"

    def look_seconds(name):
        """How long this field's last look step took (run.py writes it), else about the usual."""
        try:
            return float(json.loads((common.WORK / name / "look.json").read_text())["seconds"])
        except (OSError, ValueError, KeyError, TypeError):
            return 20.0  # about what one takes run side by side

    def run_look(w, job):
        """This look step and the ones queued right behind it (a build or a render in between keeps its place, and a
        look step after a build waits for it), each in its own process, side by side. Each one's result goes in the
        queue's history as its own; this job's own failure is raised as usual."""
        group = [job]
        with w.lock:
            while w.queue and len(group) < LOOK_PARALLEL and w.queue[0]["kind"] == "3dlook":
                group.append(w.queue.popleft())
        env = {**os.environ, "FF7_STUDIO": str(studio.HERE), "OMP_NUM_THREADS": LOOK_THREADS,
               "OPENBLAS_NUM_THREADS": LOOK_THREADS, "MKL_NUM_THREADS": LOOK_THREADS}
        results, procs, tails = {}, {}, {}
        started = time.time()
        for j in group:
            name = j["name"]
            if not up_to_date(name):  # it would re-align to the new version and drop the build's caches
                results[j["id"]] = (False, "the 3D scene is out of date: rebuild it instead", 0.0)
                continue
            look = _options(name)["look"]
            procs[j["id"]] = subprocess.Popen(
                [sys.executable, "-u", str(HERE / "run.py"), "look", look, name], cwd=str(HERE), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)

        def drain(jid, proc):  # each one's output, read as it comes (a full pipe would stop it)
            tail = []
            for line in proc.stdout:
                if line.strip():
                    tail = (tail + [line.rstrip()])[-20:]
            tails[jid] = tail
        readers = [threading.Thread(target=drain, args=(jid, p), daemon=True) for jid, p in procs.items()]
        for r in readers:
            r.start()
        names = ", ".join(j["name"] for j in group)
        while any(p.poll() is None for p in procs.values()):
            if w.cancel_flag:  # a cancel stops them all at once
                for p in procs.values():
                    p.kill()
                break
            done = sum(p.poll() is not None for p in procs.values()) + len(group) - len(procs)
            w._stage(f"switching {len(group)} fields to their look at once ({names}): {done} done", done / len(group))
            time.sleep(0.3)
        for r in readers:
            r.join(timeout=5)
        for j in group:
            p = procs.get(j["id"])
            if p is None:
                continue
            if w.cancel_flag:
                results[j["id"]] = (False, "cancelled", time.time() - started)
            elif p.returncode:
                tail = tails.get(j["id"], [])
                failed = [t for t in tail if "FAILED" in t]
                msg = (failed or tail or ["the 3D look step stopped"])[-1].split("FAILED", 1)[-1].strip()
                results[j["id"]] = (False, msg, time.time() - started)
            else:
                results[j["id"]] = (True, "", look_seconds(j["name"]))
        # the others' results, as the worker records its own job's
        for j in group[1:]:
            ok, msg, secs = results[j["id"]]
            if ok:
                project.update_meta(j["name"], error=None)
            elif msg != "cancelled":
                project.update_meta(j["name"], error=msg)
            with w.lock:
                w.recent.append({**w._public(j), "version": j.get("version"), "ok": ok, "message": msg,
                                 "seconds": round(secs, 1), "finished": time.time()})
        ok, msg, _ = results[job["id"]]
        if not ok:
            raise InterruptedError("cancelled") if msg == "cancelled" else RuntimeError(msg)

    def estimate(name):
        r = common.WORK / name / "report.json"
        return json.loads(r.read_text()).get("seconds", 30) if r.exists() else 30

    studio.JOB_KINDS["3d"] = {"run": run_build, "est": estimate}
    studio.JOB_KINDS["3dlook"] = {"run": run_look, "est": look_seconds}

    def queue(names, kind="3d", front=False):
        jobs = [studio.make_job(n, kind) for n in names]
        if kind == "3dlook" and len(jobs) > 1:  # they run LOOK_PARALLEL at a time (run_look): each one's share
            for j in jobs:
                j["est"] = round(j["est"] / min(LOOK_PARALLEL, len(jobs)), 1)
        return worker.enqueue(jobs, front=front)

    def on_current(name, vid, options=None):
        """A field's version in the game changed (a render finished, Use this version): its 3D scene next, as the
        Automatic setting says. final: every final render gets one, and scenes there are kept in step; built: only
        scenes there are kept in step; all: any version. A batch sent with Skip the 3D scenes leaves them alone."""
        auto = settings()["auto"]
        if auto == "off" or (options or {}).get("skip_3d") or (built(name) and up_to_date(name)):
            return
        final = project.versions.meta(name, vid).get("mode") == "final"
        if built(name) or auto == "all" or (auto == "final" and final):
            queue([name], front=True)  # right after the render, so a batch finishes field by field

    studio.ON_CURRENT.append(on_current)

    # ------------------------------------------------------------ routes

    bp = Blueprint("ff7_3d", __name__, url_prefix="/api/3d")

    def known(name):
        if not project.ready or name not in project.jobs:
            abort(404)

    @bp.get("/ui.js")
    def ui():
        return send_file(HERE / "studio_plugin.js", mimetype="text/javascript", max_age=0)

    @bp.get("/field/<name>")
    def api_field(name):
        known(name)
        return jsonify(field_state(name))

    @bp.get("/built")
    def api_built():
        """Fields with a 3D scene, and whether each is up to date (the field list marks them)."""
        if not project.ready:
            return jsonify({})
        return jsonify({n: up_to_date(n) for n in project.jobs if built(n)})

    @bp.post("/build")
    def api_build():
        names = [n for n in request.get_json(force=True).get("names", []) if n in project.jobs]
        return jsonify(added=queue(names), names=len(names))

    @bp.post("/look")
    def api_look():
        """Apply each field's look, light and effects again (after the look step itself changed), without rebuilding.
        Only up-to-date scenes: the look step loads the field's current version, and on a scene built from another
        one it would re-align and drop the build's caches. Those get it when they're rebuilt."""
        names = [n for n in request.get_json(force=True).get("names", []) if n in project.jobs and built(n)]
        fresh = [n for n in names if up_to_date(n)]
        return jsonify(added=queue(fresh, "3dlook"), skipped=sorted(set(names) - set(fresh)))

    def parse(body):
        """The setting changes a request asks for, checked."""
        changes = {}
        if body.get("look") in LOOKS:
            changes["look"] = body["look"]
        if "sway" in body:
            changes["sway"] = bool(body["sway"])
        if "reveal" in body:
            changes["reveal"] = bool(body["reveal"])
        for k, (lo, hi) in LIGHT.items():
            if isinstance(body.get(k), (int, float)):
                changes[k] = round(min(max(float(body[k]), lo), hi), 3)
        if body.get("sun") in SUNS:
            changes["sun"] = body["sun"]
        if body.get("particles") in PARTICLES:
            changes["particles"] = body["particles"]
        for k, (lo, hi) in EFFECTS.items():
            if isinstance(body.get(k), (int, float)):
                changes[k] = round(min(max(float(body[k]), lo), hi), 3)
        if body.get("reset_light"):
            changes.update({k: build.DEFAULTS[k] for k in LIGHT})
        if body.get("reset_effects"):
            changes.update({k: build.DEFAULTS[k] for k in (*EFFECTS, "sun", "particles")})
        return changes

    def look_changed(before, after):
        """A change a look step would show (the game-wide ones show by themselves: build.write_fields_section)."""
        return any(after[k] != before[k] for k in ("look", *LIGHT, *EFFECTS, "sun", "particles") if k not in build.GAMEWIDE)

    @bp.post("/options/<name>")
    def api_options(name):
        """Change settings for all fields (scope "all") or for this field only. All fields: the up-to-date scenes
        that follow a changed setting take it in seconds (a look step), the others when they're rebuilt; nothing is
        rebuilt from here (a change of shape would mean every scene). This field: as before, a look step, or a build
        when its shape changed or it's out of date anyway. follow: setting names this field stops changing for itself."""
        known(name)
        body = request.get_json(force=True)
        changes = parse(body)
        if body.get("scope") == "all":
            names = [n for n in project.jobs if built(n)]
            before = {n: _options(n) for n in names}
            build.set_shared(**changes)
            fresh = [n for n in names if look_changed(before[n], _options(n)) and up_to_date(n)]
            return jsonify(queued=queue(fresh, "3dlook"), field=field_state(name))
        before = _options(name)
        keys = [k for k in body.get("follow", []) if k in UI_KEYS]
        if keys:
            build.follow(name, keys)
        after = build.set_options(name, **changes)
        queued = 0
        if built(name):
            if after["sway"] != before["sway"] or after["reveal"] != before["reveal"] or not up_to_date(name):
                queued = queue([name])  # the mesh changes (or is stale anyway): build again
                if not queued and look_changed(before, after) and building(name):
                    # its build is running: it takes the look as it is when it gets there, but it may be past it
                    queued = queue([name], "3dlook")
            elif look_changed(before, after):
                queued = queue([name], "3dlook")  # a look is textures and light: seconds
        return jsonify(queued=queued, field=field_state(name))

    @bp.get("/all")
    def api_all():
        """Every 3D scene, for rebuilding them all: how many, how many out of date, the fields with their own changes
        and about how long it takes (their last builds)."""
        names = [n for n in project.jobs if built(n)]
        own = {n: [k for k in build.own(n) if k in UI_KEYS] for n in names}
        return jsonify(scenes=len(names), stale=sum(1 for n in names if not up_to_date(n)),
                       own={n: k for n, k in own.items() if k}, seconds=round(sum(estimate(n) for n in names)))

    @bp.post("/rebuild_all")
    def api_rebuild_all():
        """Rebuild every 3D scene from the version each field has in the game (its render isn't made again).
        drop_own: each field drops its own changes and follows all fields; keep_shape: except flower sway and
        character hiding (they suit what's in a field: flower beds)."""
        body = request.get_json(force=True)
        names = [n for n in project.jobs if built(n)]
        if body.get("drop_own"):
            for n in names:
                kept = set(build.own(n)) & set(SHAPE) if body.get("keep_shape") else set()
                build.follow(n, [k for k in build.own(n) if k not in kept])
        return jsonify(added=queue(names), scenes=len(names))

    @bp.get("/settings")
    def api_settings_get():
        return jsonify(settings())

    @bp.post("/settings")
    def api_settings():
        auto = request.get_json(force=True).get("auto")
        if auto in AUTO:
            SETTINGS.write_text(json.dumps({**settings(), "auto": auto}))
        return jsonify(settings())

    @bp.get("/game")
    def api_game():
        return jsonify(game())

    @bp.post("/game")
    def api_game_action():
        body = request.get_json(force=True)
        action, name = body.get("action"), body.get("name")
        try:
            if action == "start":
                if not game()["running"]:
                    start_game(name or "eals_1")
            elif action == "show":
                known(name)
                show(name)
            elif action == "view" and body.get("view") in ("3d", "ai2d", "original"):
                live.send(f"view {body['view']}")
            elif action == "shot":
                known(name)
                live.shot(common.WORK / name / "ingame.png")
            else:
                abort(400)
        except TimeoutError:
            return jsonify(ok=False, message="The game didn't answer. Is it running with FFNx-3d?"), 409
        return jsonify(ok=True, game=game())

    @bp.get("/shot/<name>.jpg")
    def api_shot(name):
        known(name)
        p = common.WORK / name / "ingame.png"
        if not p.exists():
            abort(404)
        im = Image.open(p).convert("RGB")
        if im.size == (1280, 720):
            im = im.crop((160, 0, 1120, 720))  # the game's 4:3 picture
        im.thumbnail((640, 480))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg", max_age=0)

    # the preview's 3D view (studio_view3d.js)

    @bp.get("/view3d.js")
    def view3d():
        return send_file(HERE / "studio_view3d.js", mimetype="text/javascript", max_age=0)

    @bp.get("/view/<name>")
    def api_view(name):
        """The field's scene for the preview: its game camera, when it was built, and from which version."""
        known(name)
        if not built(name):
            return jsonify(built=False, current=project.current(name))
        R, T, zoom, rect = common.camera(name)
        C = -np.linalg.solve(R, T)  # where the camera is (R isn't quite a rotation: common.Field.Rinv)
        return jsonify(built=True, R=R.tolist(), T=T.tolist(), C=C.tolist(), zoom=zoom, rect=list(rect),
                       stamp=(common.MESH / name / "scene.gltf").stat().st_mtime, version=source_of(name)[0],
                       up_to_date=up_to_date(name), current=project.current(name))

    # the field's life for the preview (studio_view3d.js): its particles file and the settings of its moving parts,
    # looked up as the engine does ([field_<name>], then [fields], then the file's top level)
    LIFE_KEYS = ("scene_fx", "scene_fx_dir", "water_motion", "grass_wind", "grass_gust", "grass_wind_dir")

    @bp.get("/life/<name>")
    def api_life(name):
        """{particles: the field's <name>_particles.toml as it is (null: none), fx: the moving parts' settings, maps:
        which of the effects map and the normal map (grass) the scene has, stamp: when any of them last changed}."""
        import tomllib
        known(name)
        d = common.MESH / name
        out = {"particles": None, "fx": {}, "maps": {}, "stamp": 0.0}
        p = d / f"{name}_particles.toml"
        if p.exists():
            try:
                out["particles"] = tomllib.loads(p.read_text("utf-8"))
            except (OSError, ValueError) as ex:
                out["error"] = f"{p.name}: {ex}"
            out["stamp"] = p.stat().st_mtime
        cfg_path = common.LIGHTING / "config.toml"
        try:
            cfg = tomllib.loads(cfg_path.read_bytes().decode("utf-8"))
            out["stamp"] = max(out["stamp"], cfg_path.stat().st_mtime)
        except (OSError, ValueError):
            cfg = {}
        for k in LIFE_KEYS:
            for group in (cfg.get(f"field_{name}") or {}, cfg.get("fields") or {}, cfg):
                if k in group:
                    out["fx"][k] = group[k]
                    break
        if (d / "scene.gltf").exists():
            base = json.loads((d / "scene.gltf").read_text())["images"][0]["name"]
            for key in ("fx", "nml"):
                f = d / f"{base}_{key}.dds"
                out["maps"][key] = f.exists()
                if f.exists():
                    out["stamp"] = max(out["stamp"], f.stat().st_mtime)
        return jsonify(out)

    @bp.get("/scene/<name>/<part>")
    def api_scene(name, part):
        """scene.gltf, scene.bin, colour.dds (the colour texture, uncompressed BGRA), fx.dds (its effects map) or
        nml.dds (its normal map: grass in its alpha), the same."""
        known(name)
        d = common.MESH / name
        if part in ("colour.dds", "fx.dds", "nml.dds"):
            gltf = json.loads((d / "scene.gltf").read_text())
            base = gltf["images"][0]["name"]
            path = d / (f"{base}.dds" if part == "colour.dds" else f"{base}_{part}")
        elif part in ("scene.gltf", "scene.bin"):
            path = d / part
        else:
            abort(404)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype="application/octet-stream", max_age=0)

    studio.app.register_blueprint(bp)
    return "/api/3d/ui.js"
