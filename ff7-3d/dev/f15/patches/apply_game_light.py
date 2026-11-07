"""F15 roll-out without 60 look re-applies: put atmos.game_light's key (game_light_saturation) into every built field's
[field_<name>] section in lighting/config.toml, the way build.write_look does once pipeline_F15.diff is merged (and
take it out of fields that don't need it). Run with the live pipeline after the merge:

  python apply_game_light.py [--dry] [--code <pipeline folder; default the live ff7-3d>]

Reads only the field files (flevel.lgp); never constructs common.Field. Holds ibl.config_lock() for the write."""
import re
import sys
import tomllib
from types import SimpleNamespace

CODE = sys.argv[sys.argv.index("--code") + 1] if "--code" in sys.argv else r"C:\Users\you\Desktop\ff7-3d"
sys.path.insert(0, CODE)
from ff3d import atmos, common, ibl  # noqa: E402
from ff7bg.lgp import LgpArchive  # noqa: E402
from ff7bg.lzs import decompress  # noqa: E402

KEY = "game_light_saturation"
dry = "--dry" in sys.argv
cfg = common.LIGHTING / "config.toml"
with ibl.config_lock():
    text = cfg.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    changed = []
    with LgpArchive(str(common.LGP)) as lgp:
        for name in common.maplist():
            pattern = re.compile(rf"^\[field_{re.escape(name)}\][^\r\n]*(?:\r?\n(?!\[)[^\r\n]*)*", re.M)
            m = pattern.search(text)
            if not m:
                continue  # not built: its build writes the key
            try:
                want = atmos.game_light(SimpleNamespace(raw=decompress(lgp.read(name)))).get(KEY)
            except Exception as e:  # noqa: BLE001
                print(f"{name}: skipped ({e})")
                continue
            block = m.group(0)
            lines = [l for l in block.split(nl) if not re.match(rf"{KEY}\s*=", l)]
            while lines and not lines[-1].strip():
                lines.pop()
            if want is not None:
                lines.append(f"{KEY} = {want}")
            new = nl.join(lines)
            if new.rstrip() != block.rstrip():
                changed.append((name, want))
                text = text[:m.start()] + new + text[m.start() + len(block.rstrip()):]
    print(len(changed), "sections changed:", changed)
    if changed and not dry:
        tomllib.loads(text)  # never write a file FFNx-3d can't parse
        cfg.write_bytes(text.encode("utf-8"))
        print("written", cfg)
