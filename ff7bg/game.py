"""FF7 (the 2026 Steam release, through FFNx) as a Studio game: where its fields come from, how its pages are named,
where they install. ff8bg.game.Ff8Game is the same interface for FF8 Remastered; the Studio picks one at start
(studio.py --game)."""
import threading
import tomllib
from pathlib import Path

import numpy as np
from PIL import Image

from . import ai, sheets
from .field import parse_background
from .lgp import LgpArchive
from .lzs import decompress

DEFAULT_WORKDIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")


class Ff7Game:
    key = "ff7"
    title = "FF7 Field Studio"
    port = 8765
    features = {"syw": True, "palettes": True, "plugins": True, "play": False}  # (FF7's go-to-field is the 3D plug-in's)

    def __init__(self, lgp_path=None, workdir=DEFAULT_WORKDIR):
        self.workdir = Path(workdir)
        self.lgp_path = Path(lgp_path or self.workdir / "data" / "field" / "flevel.lgp")
        self.lgp = LgpArchive(self.lgp_path)
        self.lock = threading.Lock()
        self.mods_dir = self.workdir / "mods" / "Textures"

    def names(self):
        return [n for n in self.lgp.names() if "." not in n and n != "maplist"]

    def raw(self, name):
        """The decompressed field file (palette effects and scripts read it)."""
        with self.lock:
            return decompress(self.lgp.read(name))

    def background(self, name):
        return parse_background(name, self.raw(name))

    def stamp(self):
        """Changes when flevel.lgp does."""
        st = self.lgp_path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"

    def prep(self, out_dir, status=lambda msg: None):
        """Every map's AI input and job entry (ai/jobs.json)."""
        return ai.prep(((n, self.raw(n)) for n in self.names()), out_dir)

    write_pages = staticmethod(sheets.write_pages)

    @staticmethod
    def load_pages(pages_dir, name, bg):
        """A version's pages back as {tex: RGBA}, for the viewer's in-game view."""
        pages = {}
        for f in Path(pages_dir).glob(f"{name}_*_00.png"):
            tex = int(f.stem[len(name) + 1 :].split("_")[0])
            pages[tex] = np.asarray(Image.open(f).convert("RGBA"))
        return pages

    def install_target(self, rel):
        """field/<map>/<file> (as in dist) -> the game's FFNx folder."""
        return self.mods_dir / rel

    def window(self):
        """How tall the game draws its picture, from FFNx.toml: the window's height, or the screen's when
        fullscreen (only the browser knows that one). None without FFNx."""
        try:
            with open(self.workdir / "FFNx.toml", "rb") as f:
                t = tomllib.load(f)
        except (OSError, ValueError):
            return None
        return {"fullscreen": bool(t.get("fullscreen")), "height": int(t.get("window_size_y") or 0)}
