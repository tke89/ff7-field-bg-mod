"""restore.py <tag> <field...>: put a field's game files, work folder and lighting config section back from
backup/<tag>/<field>/ (and backup/<tag>/config_*.toml), as backup.py saved them."""
import re, shutil, sys
from pathlib import Path
G = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
W = Path(r"C:\Users\you\Desktop\ff7-3d\work")
sys.path.insert(0, str(Path(__file__).parent))
from ff3d import common, ibl
tag = sys.argv[1]
root = Path(__file__).parent / "backup" / tag
cfg_old = sorted(root.glob("config_*.toml"))[0].read_text(encoding="utf-8")


def section(text, name):
    m = re.search(rf"^\[field_{re.escape(name)}\]\n(?:(?!^\[).*\n?)*", text, re.M)
    return m


def mirror(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


for name in sys.argv[2:]:
    d = root / name
    fid = common.maplist().index(name)
    for sub, dst in (("mesh", G / "mesh" / "field" / name), ("pages", G / "mods" / "3dtest" / "field" / name)):
        if (d / sub).exists():
            mirror(d / sub, dst)
    for p in (G / "lighting" / "ibl").glob(f"field_{fid}_*"):
        if not (d / "ibl" / p.name).exists():
            p.unlink()
    for p in (d / "ibl").iterdir():
        shutil.copy2(p, G / "lighting" / "ibl" / p.name)
    # work folder: the backup's files back, files the build added since removed (the snapshot folder stays)
    wb, wl = d / "work", W / name
    for p in sorted(wl.rglob("*"), reverse=True):
        rel = p.relative_to(wl)
        if rel.parts[0] == "snapshot" or (wb / rel).exists():
            continue
        p.unlink() if p.is_file() else p.rmdir()
    for p in wb.rglob("*"):
        q = wl / p.relative_to(wb)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(p, q)
    old = section(cfg_old, name)
    with ibl.config_lock():
        path = G / "lighting" / "config.toml"
        cur = path.read_text(encoding="utf-8")
        now = section(cur, name)
        if old and now:
            new = cur[:now.start()] + old.group(0) + cur[now.end():]
            import tomllib
            tomllib.loads(new)
            tmp = path.with_suffix(".toml.f22tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(path)
            print(name, "config section restored", "(same)" if old.group(0) == now.group(0) else "")
        else:
            print(name, "config section NOT restored", bool(old), bool(now))
    print("restored", name, fid)
