"""Unpack the active 7th Heaven profile's mods into FFNx direct mode (workingdir/direct/<archive>/<file>), so the game
launched straight from ff7_en.exe looks as it does through 7th Heaven.

Follows each mod's mod.xml with the profile's option values: ModFolders whose ActiveWhen holds, and Conditional folders
too. Runtime conditions can't be followed outside 7th Heaven, so they're judged once for an ordinary moment: no special
field or story point, every character on their starting weapon. Lookup order as 7th Heaven: mods in
profile order, and within a mod its conditionals, then its folders in listed order, then the root; the first file
found wins.

  apply_profile.py            unpack (lists what it does; files we own in direct/ are never touched)
  apply_profile.py --undo     remove everything a previous run wrote
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import iro

WORKSHOP = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "7th Heaven" / "7thWorkshop"
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir")
DIRECT = GAME / "direct"
MANIFEST = DIRECT / "_7th_heaven_profile.json"
OURS = {"flevel/nmkin_4"}  # test files of ours (the quick-start jump): never overwritten
WEAPON_VARS = re.compile(r"weapon$", re.I)


def norm(s):
    return " ".join(s.lower().split())


def holds(expr, opts):
    """'opt = 1' (several joined by 'and'/'or' allowed)."""
    def one(e):
        k, v = (x.strip() for x in e.split("="))
        return str(opts.get(norm(k), "")) == v
    if re.search(r"\bor\b", expr, re.I):
        return any(holds(e, opts) for e in re.split(r"\bor\b", expr, flags=re.I))
    return all(one(e) for e in re.split(r"\band\b", expr, flags=re.I))


def node_holds(el, opts):
    """<ActiveWhen> children: <Option>, <Or>, <And> (default: all must hold)."""
    tag = el.tag.lower()
    kids = list(el)
    if tag == "option":
        return holds(el.text or "", opts)
    if tag == "or":
        return any(node_holds(k, opts) for k in kids)
    return all(node_holds(k, opts) for k in kids)


def active_when(el, opts):
    if el.get("ActiveWhen"):
        return holds(el.get("ActiveWhen"), opts)
    aw = el.find("ActiveWhen")
    return True if aw is None else node_holds(aw, opts)


def in_values(v, spec):
    for part in re.split(r"[ ,]+", spec.strip()):
        if ".." in part:
            a, b = part.split("..")
            if int(a) <= v <= int(b):
                return True
        elif part.lstrip("-").isdigit() and int(part) == v:
            return True
    return False


def rt_holds(el, state):
    """A Conditional's runtime test (RuntimeVar, Or, And, Not; several children: all must hold) against `state`."""
    tag = el.tag.lower()
    if tag == "runtimevar":
        v = state.get(el.get("Var", "").lower())
        return v is not None and in_values(v, el.get("Values", ""))
    kids = [k for k in el if k.tag.lower() != "activewhen"]
    if tag == "not":
        return not all(rt_holds(k, state) for k in kids)
    if tag == "or":
        return any(rt_holds(k, state) for k in kids)
    return all(rt_holds(k, state) for k in kids)


def mod_folders(xml_text, opts):
    """Folders to use, in lookup order. Runtime conditions are judged for an ordinary moment: not one of the special
    fields or story points the mod singles out (FieldID / PPV unknown), each character on their starting weapon
    (the lowest weapon value the mod lists)."""
    root = ET.fromstring(re.sub(r"^\s*=+.*$|^\s*//.*$", "", xml_text, flags=re.M))
    for co in root.iter("ConfigOption"):  # defaults for options the profile doesn't set
        k, d = co.findtext("ID"), co.findtext("Default")
        if k is not None and d is not None:
            opts.setdefault(norm(k), d.strip())
    state = {}
    for rv in root.iter("RuntimeVar"):
        var = rv.get("Var", "").lower()
        if WEAPON_VARS.search(var):
            low = min(int(x.split("..")[0]) for x in re.split(r"[ ,]+", rv.get("Values", "").strip()) if x)
            state[var] = min(state.get(var, low), low)
    conds = [c for c in root.iter("Conditional") if active_when(c, opts)]
    used = [c.get("Folder") for c in conds if rt_holds(c, state)]
    skipped = [c.get("Folder") for c in conds if not rt_holds(c, state)]
    folders = [f.get("Folder") for f in root.iter("ModFolder") if active_when(f, opts)]
    return used + folders, skipped


def main():
    if "--undo" in sys.argv:
        written = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
        for rel in written:
            (DIRECT / rel).unlink(missing_ok=True)
        MANIFEST.unlink(missing_ok=True)
        print(f"removed {len(written)} files")
        return

    settings = ET.parse(WORKSHOP / "settings.xml").getroot()
    library = Path(settings.findtext("LibraryLocation"))
    profile = ET.parse(WORKSHOP / "profiles" / f"{settings.findtext('CurrentProfile')}.xml").getroot()

    chosen = {}  # "archive/file" (lower case) -> (iro path, entry)
    for item in profile.iter("ProfileItem"):
        if item.findtext("IsModActive") != "true":
            continue
        mod_id, name = item.findtext("ModID"), item.findtext("Name")
        archives = list(library.glob(f"{mod_id}*.iro"))
        if not archives:
            print(f"{name}: no .iro in the library (folder mods aren't handled), skipped")
            continue
        path = str(archives[0])
        _, _, entries = iro.entries(path)
        by_name = {e[0].replace("/", "\\").lower(): e for e in entries}
        opts = {norm(s.findtext("ID")): s.findtext("Value") for s in item.iter("ProfileSetting")}
        folders, skipped = mod_folders(iro.read(path, by_name["mod.xml"]).decode("utf-8", "replace"), opts)
        print(f"{name}: {len(folders)} folders: {folders}")
        if skipped:
            print(f"   skipped {len(skipped)} runtime-only folders (field/plot fixes, other weapons)")
        n = 0
        for prefix in [f.replace("/", "\\").lower().rstrip("\\") + "\\" for f in folders] + [""]:
            for key, e in by_name.items():
                if not key.startswith(prefix):
                    continue
                rel = key[len(prefix):].split("\\")
                if len(rel) != 2:  # <archive>\<file> only
                    continue
                target = f"{rel[0]}/{rel[1]}"
                if target not in chosen:
                    chosen[target] = (path, e)
                    n += 1
        print(f"   {n} files")

    written = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    for rel in written:  # a previous run's files go first, so options switched off disappear
        (DIRECT / rel).unlink(missing_ok=True)
    written, kept = [], 0
    for target, (path, e) in sorted(chosen.items()):
        if target in OURS:
            kept += 1
            continue
        out = DIRECT / target
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(iro.read(path, e))
        written.append(target)
    MANIFEST.write_text(json.dumps(written))
    size = sum((DIRECT / t).stat().st_size for t in written) / 1e6
    print(f"wrote {len(written)} files ({size:.0f} MB) to {DIRECT}; left {kept} of our test files alone")


if __name__ == "__main__":
    main()
