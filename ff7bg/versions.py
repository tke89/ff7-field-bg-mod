"""Every generation of a field is kept as a version.

ai/versions/<map>/<id>/
    render.png        what the generator returned
    meta.json         workflow, look, mode (final / preview / imported), the settings used, results
    states/           restyled doors, lights and animation frames (states pass)
    pages/field/...   the FFNx texture pages built from it (for the map and maps sharing its background)
    review/<map>.jpg  the review sheet

One version per field is "current": its pages are what dist/mods/Textures holds and what the game
gets on Install.
"""
import json
import shutil
import time
from pathlib import Path

RENDER_EXTS = ("png", "webp", "jpg", "jpeg")


class VersionStore:
    def __init__(self, root, dist):
        self.root = Path(root)
        self.dist = Path(dist)

    def dir(self, name, vid):
        return self.root / name / vid

    def new_id(self, name, when=None):
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
        vid, i = base, 2
        while self.dir(name, vid).exists():
            vid, i = f"{base}-{i}", i + 1
        return vid

    def create(self, name, render_bytes, meta, suffix=".png"):
        vid = self.new_id(name)
        d = self.dir(name, vid)
        d.mkdir(parents=True)
        (d / f"render{suffix}").write_bytes(render_bytes)
        self.save_meta(name, vid, dict(meta, id=vid, created=meta.get("created", time.time())))
        return vid

    def ids(self, name):
        """Newest first."""
        d = self.root / name
        if not d.exists():
            return []

        def order(vid):  # 20261020-195003, then -2, -3 ... for the same second
            parts = vid.split("-")
            return parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 1

        return sorted((p.name for p in d.iterdir() if p.is_dir()), key=order, reverse=True)

    def meta(self, name, vid):
        p = self.dir(name, vid) / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_meta(self, name, vid, meta):
        (self.dir(name, vid) / "meta.json").write_text(json.dumps(meta, indent=1))

    def update_meta(self, name, vid, **fields):
        m = self.meta(name, vid)
        m.update(fields)
        self.save_meta(name, vid, m)
        return m

    def render_path(self, name, vid):
        for ext in RENDER_EXTS:
            p = self.dir(name, vid) / f"render.{ext}"
            if p.exists():
                return p
        return None

    def pages_root(self, name, vid):
        return self.dir(name, vid) / "pages"

    def pages_dir(self, name, vid):
        return self.pages_root(name, vid) / "field" / name

    def has_pages(self, name, vid):
        d = self.pages_dir(name, vid)
        return d.exists() and any(d.glob("*.png"))

    def states_dir(self, name, vid):
        return self.dir(name, vid) / "states"

    def review_dir(self, name, vid):
        return self.dir(name, vid) / "review"

    def listing(self, name):
        out = []
        for vid in self.ids(name):
            m = self.meta(name, vid)
            m["id"] = vid
            m["built"] = self.has_pages(name, vid)
            m["review"] = (self.review_dir(name, vid) / f"{name}.jpg").exists()
            out.append(m)
        return out

    def publish(self, name, vid, copies=()):
        """Make this version's pages the ones in dist (what Install copies into the game)."""
        for n in (name, *copies):
            src = self.pages_root(name, vid) / "field" / n
            dst = self.dist / "field" / n
            if dst.exists():
                shutil.rmtree(dst)
            if src.exists():
                shutil.copytree(src, dst)

    def delete(self, name, vid):
        shutil.rmtree(self.dir(name, vid), ignore_errors=True)

    # ---- one-time migration from the pre-versions layout

    def migrate(self, name, field_meta, ai_dir, copies, job_defaults, inputs_changed_at):
        """Turn ai/output, ai/history, ai/states and ai/review for one field into versions.
        Returns the id of the version that matches what's in dist (now current), or None."""
        ai_dir = Path(ai_dir)
        current = None
        for f in sorted((ai_dir / "history" / name).glob("*")) if (ai_dir / "history" / name).exists() else []:
            mtime = f.stat().st_mtime
            vid = self.new_id(name, mtime)
            d = self.dir(name, vid)
            d.mkdir(parents=True)
            shutil.move(str(f), d / f"render{f.suffix}")
            self.save_meta(name, vid, {
                "id": vid, "created": mtime, "mode": "imported",
                "note": "earlier render, from before versions were kept",
                # renders made before the inputs gained the game's default states didn't have them
                "rendered_defaults": job_defaults if mtime >= inputs_changed_at else {},
            })
        out = next((ai_dir / "output" / f"{name}.{e}" for e in RENDER_EXTS
                    if (ai_dir / "output" / f"{name}.{e}").exists()), None)
        if out:
            mtime = out.stat().st_mtime
            vid = self.new_id(name, mtime)
            d = self.dir(name, vid)
            d.mkdir(parents=True)
            shutil.move(str(out), d / f"render{out.suffix}")
            if (ai_dir / "states" / name).exists():
                shutil.move(str(ai_dir / "states" / name), d / "states")
            for n in (name, *copies):
                if (self.dist / "field" / n).exists():
                    shutil.copytree(self.dist / "field" / n, d / "pages" / "field" / n)
            review = ai_dir / "review" / f"{name}.jpg"
            if review.exists():
                (d / "review").mkdir()
                shutil.move(str(review), d / "review" / review.name)
            settings = field_meta.get("last_settings") or {}
            self.save_meta(name, vid, {
                "id": vid, "created": mtime,
                "mode": "preview" if field_meta.get("preview") else ("final" if field_meta.get("last") else "imported"),
                "family": settings.get("family", "flux1") if settings else None,
                "settings": settings,
                "rendered_defaults": field_meta.get("rendered_defaults", {}),
                "result": field_meta.get("last"),
            })
            current = vid
        return current
