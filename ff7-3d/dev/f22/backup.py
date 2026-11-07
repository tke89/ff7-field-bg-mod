"""Back up a field's game files + work folder before I overwrite them: backup/<tag>/<field>/."""
import shutil, sys, time
from pathlib import Path
G = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
W = Path(r"C:\Users\you\Desktop\ff7-3d\work")
sys.path.insert(0, str(Path(__file__).parent))
from ff3d import common
tag = sys.argv[1]
root = Path(__file__).parent / "backup" / tag
root.mkdir(parents=True, exist_ok=True)
cfg = root / f"config_{time.strftime('%H%M%S')}.toml"
shutil.copy2(G / "lighting" / "config.toml", cfg)
for name in sys.argv[2:]:
    d = root / name
    if d.exists():
        print("exists", d); continue
    fid = common.maplist().index(name)
    shutil.copytree(W / name, d / "work", ignore=shutil.ignore_patterns("snapshot"))
    for src, dst in ((G / "mesh" / "field" / name, d / "mesh"), (G / "mods" / "3dtest" / "field" / name, d / "pages")):
        if src.exists():
            shutil.copytree(src, dst)
    (d / "ibl").mkdir()
    for p in (G / "lighting" / "ibl").glob(f"field_{fid}_*"):
        shutil.copy2(p, d / "ibl" / p.name)
    print("backed up", name, fid)
