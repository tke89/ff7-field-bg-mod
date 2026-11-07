"""Put the battle-look test data in the game, or take it out again (../TESTPLAN.md). Run with the Studio's venv from
anywhere:

  python install_testdata.py strong    files + [battle_STAGE10] from config_battle_STAGE10.toml (obvious test values)
  python install_testdata.py subtle    files + [battle_STAGE10] from config_battle_STAGE10_subtle.toml
  python install_testdata.py bat310    also [bat_310] from config_bat_310.toml (a formation's group over its scene's)
  python install_testdata.py nobat310  [bat_310] out again
  python install_testdata.py off       every section and file this put in, out

lighting/config.toml is changed the way the pipeline changes it (ff3d.ibl: under config.toml.lock, CRLF kept, never
written unless it parses), so FFNx-3d hot-reloads it. Files go to mods/3dtest/battle/ and mesh/battle/ (only the ones
in this folder; nothing else there is touched).
"""
import re
import shutil
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # ff7-3d
from ff3d import common, ibl  # noqa: E402

GAME = common.GAME
FILES = [p.relative_to(HERE) for p in sorted(HERE.rglob("*")) if p.is_file() and p.parts[len(HERE.parts)] in ("mods", "mesh")]


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v))
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(v)


def put_sections(source):
    for table, values in tomllib.loads((HERE / source).read_text("utf-8")).items():
        ibl.write_section(table, {k: toml_value(v) for k, v in values.items()})
        print("section", table)


def drop_section(table):
    cfg = common.LIGHTING / "config.toml"
    with ibl.config_lock():
        text = cfg.read_bytes().decode("utf-8")
        pattern = re.compile(rf"^\[{re.escape(table)}\][^\r\n]*(?:\r?\n(?!\[)[^\r\n]*)*\r?\n?", re.M)
        new = pattern.sub("", text)
        if new != text:
            tomllib.loads(new)
            cfg.write_bytes(new.encode("utf-8"))
            print("removed section", table)


def put_files():
    for rel in FILES:
        dst = GAME / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / rel, dst)
        print("file", dst)


def drop_files():
    for rel in FILES:
        dst = GAME / rel
        if dst.exists():
            dst.unlink()
            print("removed", dst)


def main(what):
    if what in ("strong", "subtle"):
        put_files()
        put_sections("config_battle_STAGE10.toml" if what == "strong" else "config_battle_STAGE10_subtle.toml")
    elif what == "bat310":
        put_sections("config_bat_310.toml")
    elif what == "nobat310":
        drop_section("bat_310")
    elif what == "off":
        drop_section("bat_310")
        drop_section("battle_STAGE10")
        drop_files()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
