"""Package: every field's in-game textures in one zip, to play them on another PC or a Steam Deck (2026-11-19, the
user: "how can i package up all my fields and play them on my Steam Deck").

The textures are the ones the Studio installs (dist/mods/Textures/field: each field's in-game version, its copies,
and their animation frames). The zip holds one folder that works two ways:
- plain FFNx: its "field" folder goes into ff7/workingdir/mods/Textures/ (FFNx.toml needs enable_animated_textures);
- 7th Heaven: the folder is imported as a mod, and its mod.xml turns animated textures on.

Textures bigger than max_scale are shrunk to it. A Steam Deck's 1280x800 screen shows a field at about 3.3x (800
of its 240 lines), so 4x is already more than it can show, and smaller pages load faster and use less memory.
"""
import concurrent.futures as cf
import datetime
import io
import os
import struct
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

PAGE = 256  # a field texture page's size at 1x
NAME = "HD Painterly Anime Fields"  # the mod's name (in 7th Heaven and its catalog; the user's choice, 2026-11-21)
FOLDER = NAME  # the one folder in the zip
MOD_ID = "d8ae1d67-2414-4124-9de4-fc1e8be82a86"  # the same every time, so 7th Heaven sees a new package as an update
DECK_SCALE = 4


def _size(path):
    """A PNG's width and height, from its header."""
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def shrink(path, w, h):
    """The PNG at path shrunk to w x h (area-averaged), as PNG bytes. Colour is averaged by alpha, so see-through
    pixels don't darken the edges of what's next to them; fully see-through pixels keep their own colour, which
    FFNx's smooth filtering still reads."""
    a = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    plain = cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA)
    weighted = cv2.resize(a[..., :3] * (a[..., 3:] / 255), (w, h), interpolation=cv2.INTER_AREA)
    alpha = plain[..., 3:] / 255
    rgb = np.where(alpha > 1e-4, weighted / np.maximum(alpha, 1e-4), plain[..., :3])
    out = np.clip(np.round(np.concatenate([rgb, plain[..., 3:]], -1)), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(out, "RGBA").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def _shrink_job(job):
    path, w, h = job
    return path, shrink(path, w, h)


def mod_xml(count, when):
    # Version YYYY.MMDDHH: 7th Heaven offers an update when the number is higher, and a second package on the same
    # day (2026-11-21's repack after the page fixes) has to be higher than the first (2026.1121)
    return f"""<?xml version="1.0"?>
<ModInfo>
  <ID>{MOD_ID}</ID>
  <Name>{NAME}</Name>
  <Author>FF7 Field Studio</Author>
  <Version>{when:%Y.%m%d%H}</Version>
  <ReleaseDate>{when:%Y-%m-%d}</ReleaseDate>
  <Category>Field Textures</Category>
  <Description>Remastered field backgrounds made with FF7 Field Studio: {count} fields, with their doors, lights and animations.</Description>
  <ReleaseNotes>Needs FFNx with animated textures on (this mod turns it on). Put it above other field texture mods, such as SYW Unified.</ReleaseNotes>
  <FFNxConfig>
    <enable_animated_textures>true</enable_animated_textures>
  </FFNxConfig>
</ModInfo>
"""


def readme(count, max_scale, when):
    size = f"Every field is at most {max_scale}x the original size." if max_scale else "Fields are at their full size."
    return f"""{NAME}
{"=" * len(NAME)}
{count} remastered fields, packaged {when:%Y-%m-%d %H:%M}. {size}
For the 2026 Steam release of FINAL FANTASY VII (Steam app 3837340). Works with plain FFNx or with 7th Heaven.

The "field" folder holds the textures: one folder per field, with its doors, lights and animation frames.


WITH PLAIN FFNx (Steam Deck or another PC)
------------------------------------------
On a Steam Deck, do this in Desktop Mode.

1. Install FINAL FANTASY VII from Steam, start it once, then quit.
2. Open the game's folder: in Steam, FINAL FANTASY VII > Manage > Browse local files. Then open ff7 > workingdir.
   Everything below happens in that workingdir folder.
3. Install FFNx (the same steps as on Windows):
   a. Download the latest FFNx-Steam zip from https://github.com/julianxhokaxhiu/FFNx/releases and extract it into
      workingdir, next to the "data" folder.
   b. Copy ff7/resources/ff7_1.02/ff7_en into workingdir and rename the copy to ff7_en.exe.
   c. Copy data/lang-ja/kernel/window.bin to data/kernel/windows.bin (make the kernel folder if it isn't there).
   d. Make a text file called steam_appid.txt containing just: 3837340
4. Copy this package's "field" folder into workingdir/mods/Textures/ (make the mods and Textures folders if needed).
   Updating later: delete the old field folder first, so fields you've since changed back don't linger.
5. Open FFNx.toml (in workingdir) in a text editor and set:
      game_language = "en"
      enable_animated_textures = true      <- doors, lights and animations need this
      fullscreen = true
6. Make Steam start ff7_en.exe instead of the game's own launcher. In Steam: FINAL FANTASY VII > Properties >
   Launch Options, paste:
      bash -c 'exec "${{@/FFVII_LAUNCHER.exe/ff7\\/workingdir\\/ff7_en.exe}}"' -- %command%
   If the game doesn't start that way, clear the launch options and instead add ff7_en.exe as a game of its own:
   Steam (Desktop Mode) > Games > Add a Non-Steam Game to My Library > Browse > workingdir/ff7_en.exe. Then in its
   Properties > Compatibility, tick "Force the use of a specific Steam Play compatibility tool" and pick Proton.
   Keep Steam running and signed in: the game checks it owns the game through steam_appid.txt.

If a field stutters on the Deck, set enable_antialiasing = 0 in FFNx.toml.


WITH 7th HEAVEN
---------------
1. 7th Heaven needs its Canary channel (and FFNx Canary) for the 2026 release.
2. Unzip this package, then in 7th Heaven use Import Mod > From folder, and pick the "{FOLDER}" folder.
3. Turn the mod on and move it above any other field texture mods (such as SYW Unified) so its fields win.
   It turns on FFNx's animated textures by itself.
"""


def build(src, out_zip, max_scale=DECK_SCALE, workers=None, progress=print):
    """Zip every field texture under src (dist/mods/Textures/field) into out_zip, shrinking any bigger than
    max_scale (0: keep all as they are). Returns a summary dict."""
    src, out_zip = Path(src), Path(out_zip)
    files = sorted(p for p in src.rglob("*.png"))
    if not files:
        raise ValueError(f"no field textures in {src}: build or install some fields first")
    keep, smaller = [], []
    for p in files:
        w, h = _size(p)
        scale = w / PAGE
        if max_scale and scale > max_scale:
            smaller.append((p, round(w * max_scale / scale), round(h * max_scale / scale)))
        else:
            keep.append(p)
    fields = sorted({p.parent.name for p in files})
    when = datetime.datetime.now()
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_name(out_zip.name + ".partial")
    total, done = len(files), 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr(f"{FOLDER}/mod.xml", mod_xml(len(fields), when))
        z.writestr(f"{FOLDER}/README.txt", readme(len(fields), max_scale, when))

        def arc(p):
            return f"{FOLDER}/field/{p.relative_to(src).as_posix()}"

        for p in keep:  # PNGs are already compressed: stored as they are
            z.write(p, arc(p))
            done += 1
            if done % 1000 == 0:
                progress(f"{done}/{total} textures")
        with cf.ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 2)) as pool:
            for p, data in pool.map(_shrink_job, smaller, chunksize=8):
                z.writestr(arc(p), data)
                done += 1
                if done % 250 == 0:
                    progress(f"{done}/{total} textures ({len(smaller)} being shrunk)")
    os.replace(tmp, out_zip)
    return {"zip": str(out_zip), "fields": len(fields), "textures": total, "shrunk": len(smaller),
            "bytes": out_zip.stat().st_size}
