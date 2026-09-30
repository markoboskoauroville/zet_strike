#!/usr/bin/env python3
"""tools/manifest.py - writes app/MANIFEST.json from the files in app/: every file's SHA-256, the version
from core.py, the notes given. The updater (update.py) and the installer refuse any file whose checksum
differs, so a MANIFEST that is behind its files stops every update: run --check before each push.

    python3 tools/manifest.py "V7: what changed"     write it
    python3 tools/manifest.py --check                exit 1 when it is stale (termux-app.md §2)
"""
import hashlib
import json
import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
PATH = os.path.join(APP, "MANIFEST.json")


def build(notes):
    files = {}
    for name in sorted(os.listdir(APP)):
        if name.endswith((".py", ".html")) and not name.startswith("."):
            with open(os.path.join(APP, name), "rb") as f:
                files[name] = hashlib.sha256(f.read()).hexdigest()
    with open(os.path.join(APP, "core.py"), encoding="utf-8") as f:
        version = int(re.search(r"^VERSION = (\d+)", f.read(), re.M).group(1))
    return {"version": version, "notes": notes, "files": files}


def main():
    try:
        with open(PATH, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    if sys.argv[1:] == ["--check"]:
        want = build(old.get("notes", ""))
        if want["files"] != old.get("files") or want["version"] != old.get("version"):
            stale = sorted(set(want["files"].items()) ^ set((old.get("files") or {}).items()))
            print("MANIFEST.json is stale: %s" % ", ".join(sorted({n for n, _ in stale})) or "the version")
            return 1
        print("MANIFEST.json matches app/ (V%d, %d files)" % (want["version"], len(want["files"])))
        return 0
    notes = " ".join(sys.argv[1:]) or old.get("notes", "")
    m = build(notes)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    print("wrote MANIFEST.json, V%d, %d files" % (m["version"], len(m["files"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
