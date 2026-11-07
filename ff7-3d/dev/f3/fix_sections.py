"""Put the F10 (game-wide character keys) state back in my 5 fields' config sections: the section as it was before my
look re-apply, with only the keys my changes move taken from now (point_lights; reflection gone in blin60_2)."""
import re, sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d")   # the live ibl.config_lock (same lock file either way)
from ff3d import ibl
CFG = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir\lighting\config.toml")
before = Path(r"C:\Users\you\Desktop\ff7-3d-dev-f3\backup\config.toml.before").read_bytes().decode("utf-8")
TAKE = ("point_lights",)

def section(text, name):
    m = re.search(rf"^\[field_{re.escape(name)}\][^\r\n]*(?:\r?\n(?!\[)[^\r\n]*)*\r?\n?", text, re.M)
    return m

with ibl.config_lock():
    text = CFG.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    for name in ["blin66_2", "blin66_6", "church", "blin63_1", "blin60_2"]:
        old, cur = section(before, name), section(text, name)
        now = {l.split(" = ", 1)[0]: l for l in cur.group(0).splitlines() if " = " in l}
        lines = []
        for l in old.group(0).splitlines():
            k = l.split(" = ", 1)[0] if " = " in l else None
            if k == "reflection" and "reflection" not in now:
                continue
            lines.append(now[k] if k in TAKE and k in now else l)
        for k in TAKE:  # a key my change added
            if k in now and not any(l.startswith(k + " = ") for l in lines):
                lines.append(now[k])
        body = nl.join(l for l in lines if l.strip()) + nl + nl
        text = text[:cur.start()] + body + text[cur.end():]
        print(name, "restored,", len(lines), "lines")
    CFG.write_bytes(text.encode("utf-8"))
