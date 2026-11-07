"""apply_jobs.py [--write]: bring the Studio's ai/jobs.json up to date for F27, touching only
  - "animations" of the fields whose groups the patched scripts.analyze adds (new keys appended; existing ones left), and
  - "defaults" of the fields to re-render (DEFAULTS below: each field's states on a fresh entry, checked in game).
Every other field and key stays byte-for-byte as it was. Without --write: prints the changes (old -> new) only.
The file is written to a temp file and swapped in (the 3D builds read it at any time)."""
import importlib.util, json, os, sys, time
from pathlib import Path

STUDIO = Path(r"C:\Users\you\Desktop\ff7-field-bg-mod")
DEV = Path(r"C:\Users\you\Desktop\ff7-3d\dev\f27")
JOBS = STUDIO / "ai" / "jobs.json"
LGP = r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VII Steam Edition\ff7\workingdir\data\field\flevel.lgp"
sys.path.insert(0, str(STUDIO))
from ff7bg import scripts  # the patched one (applied to the Studio)
from ff7bg.lgp import LgpArchive
from ff7bg.lzs import decompress

# The re-render fields' new entry states (scratch/fresh.py: every init run with all game variables 0; checked against
# the game's original view on a fresh entry, scratch/vs/). Keys in the field's existing order, new ones after.
DEFAULTS = json.loads((DEV / "patches" / "defaults.json").read_text(encoding="utf-8"))


def main():
    write = "--write" in sys.argv
    text = JOBS.read_bytes().decode("utf-8")  # exact bytes: no newline translation
    nl = "\r\n" if "\r\n" in text else "\n"  # the file's line endings (CRLF), kept
    jobs = json.loads(text)
    assert json.dumps(jobs, indent=1).replace("\n", nl) == text, "jobs.json isn't in json.dumps(indent=1) form"
    changes = []
    with LgpArchive(LGP) as lgp:
        for name in sorted(jobs):
            job = jobs[name]
            new_anim = {str(p): a for p, a in scripts.analyze(decompress(lgp.read(name)))[1].items()}
            old_anim = job.get("animations", {})
            added = {p: a for p, a in new_anim.items() if p not in old_anim}
            if added:
                job["animations"] = {**old_anim, **added}
                changes.append(f"{name}: animations + {json.dumps(added)}")
    for name, new in DEFAULTS.items():
        old = jobs[name].get("defaults", {})
        merged = {p: new[p] for p in old if p in new}
        merged.update({p: m for p, m in new.items() if p not in merged})
        if merged != old:
            jobs[name]["defaults"] = merged
            changes.append(f"{name}: defaults {json.dumps(old)} -> {json.dumps(merged)}")
    print("\n".join(changes))
    print(len(changes), "changes")
    if not write:
        return
    out = json.dumps(jobs, indent=1).replace("\n", nl)
    tmp = JOBS.with_suffix(".f27tmp")
    tmp.write_bytes(out.encode("utf-8"))
    for i in range(50):
        try:
            os.replace(tmp, JOBS)
            break
        except PermissionError:  # a reader has it open this instant
            time.sleep(0.1)
    else:
        raise SystemExit("couldn't swap jobs.json in")
    print("written", JOBS)


if __name__ == "__main__":
    main()
