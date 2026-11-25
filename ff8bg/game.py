"""FF8 Remastered as a Studio game (the same interface as ff7bg.game.Ff7Game)."""
import json
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image

from ff7bg import ai

from . import field, pages, play, scripts, text
from .archive import DEFAULT_GAME, FieldArchive, Zzz


class Ff8Game:
    key = "ff8"
    title = "FF8 Field Studio"
    port = 8767
    # FF7-only parts of the Studio: the SYW Unified pack, FF7 palette effects (FFNx animated textures), plug-ins
    # (the 3D engine), 7th Heaven packaging
    features = {"syw": False, "palettes": False, "plugins": False, "play": True}

    def __init__(self, game_dir=DEFAULT_GAME):
        self.game_dir = Path(game_dir)
        self.archive = FieldArchive(self.game_dir)
        self.player = play.Player(self)  # "Play in game": a save standing in the field, loaded (ff8bg/play.py)
        self.mods_dir = pages.demaster_dir(self.game_dir)
        self._fields = OrderedDict()
        self._lock = threading.Lock()

    def names(self):
        return self.archive.names()

    def field(self, name):
        with self._lock:
            if name in self._fields:
                self._fields.move_to_end(name)
                return self._fields[name]
        f = field.Field(name, self.archive.field(name))
        with self._lock:
            self._fields[name] = f
            if len(self._fields) > 8:
                self._fields.popitem(last=False)
        return f

    def raw(self, name):
        """FF7's decompressed field file (palette effects read it); FF8 has none of those yet."""
        return None

    def background(self, name):
        return field.studio_background(self.field(name))

    def stamp(self):
        return self.archive.stamp()

    def prep(self, out_dir, status=lambda msg: None):
        """Every field's AI input (its entry view at 1x) and job entry, as ai.prep makes them for FF7. Fields that
        draw identically become copies of the first; fields in a layout not read yet are left out."""
        out_dir = Path(out_dir)
        (out_dir / "input").mkdir(parents=True, exist_ok=True)
        jobs, by_key, skipped = {}, {}, {}
        try:
            places = text.place_names(Zzz(self.archive.zzz_path))
        except (KeyError, IndexError, OSError):
            places = []
        names = self.names()
        for i, name in enumerate(names):
            if i % 50 == 0:
                status(f"preparing field images (first run): {i}/{len(names)}")
            try:
                f = self.field(name)
            except (field.Unsupported, KeyError) as e:
                skipped[name] = str(e)
                continue
            key = f.key()
            if key in by_key:
                jobs[by_key[key]]["copies"].append(name)
                continue
            bg = field.studio_background(f)
            defaults = field.studio_defaults(f)
            img, rect = ai.default_view(bg, defaults)
            Image.fromarray(img).save(out_dir / "input" / f"{name}.png")
            by_key[key] = name
            pid = scripts.place(f.jsm) if f.jsm is not None else None
            jobs[name] = {"canvas": list(rect), "pad": [0, 0, 0, 0], "input_size": [img.shape[1], img.shape[0]],
                          "copies": [], "defaults": defaults, "animations": field.studio_animations(f),
                          "place": places[pid] if pid is not None and 0 <= pid < len(places) else ""}
        (out_dir / "skipped_fields.json").write_text(json.dumps(skipped, indent=1))
        (out_dir / "jobs.json").write_text(json.dumps(jobs, indent=1))
        return jobs

    write_pages = staticmethod(pages.write_pages)
    load_pages = staticmethod(pages.load_pages)

    def install_target(self, rel):
        return pages.install_target(self.game_dir, rel)

    def window(self):
        """How tall the game draws its picture (the Studio's "game size" zoom); not read for FF8 yet."""
        return None
