#!/data/data/com.termux/files/usr/bin/python
"""update.py - ZET Strike self update from GitHub (markoboskoauroville/zet_strike).
Asks GitHub for the newest commit, downloads each file raw at that commit, checks every SHA-256 against
the repo's app/MANIFEST.json, backs up the running version, then swaps the files in."""
import hashlib
import json
import os
import shutil
import time
import urllib.request

import core

REPO = os.environ.get("ZET_REPO", "markoboskoauroville/zet_strike")
BRANCH = "main"
API = os.environ.get("ZET_GITHUB_API", "https://api.github.com")
RAW = os.environ.get("ZET_GITHUB_RAW", "https://raw.githubusercontent.com")
LOCAL = os.path.join(core.APP_DIR, "VERSION.json")
BACKUPS = os.path.join(core.APP_DIR, "backup")


def _get(url, timeout=30, accept=None):
    h = {"User-Agent": core.UA}
    if accept:
        h["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read(5 * 1024 * 1024)


def installed():
    d = core.load_json(LOCAL, {})
    return d if isinstance(d, dict) else {}


def latest_commit():
    """Newest commit on main. Falls back to the branch name when the API is out of reach or rate limited."""
    try:
        sha = _get("%s/repos/%s/commits/%s" % (API, REPO, BRANCH), 20, "application/vnd.github.sha").decode().strip()
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            return sha
    except Exception:
        pass
    return BRANCH


def remote_manifest(ref):
    m = json.loads(_get("%s/%s/%s/app/MANIFEST.json" % (RAW, REPO, ref)).decode("utf-8"))
    if not isinstance(m, dict) or not isinstance(m.get("files"), dict) or not m["files"]:
        raise ValueError("MANIFEST.json in the repo is not valid")
    for name in m["files"]:
        if "/" in name or "\\" in name or name.startswith(".") or not name.endswith((".py", ".html")):
            raise ValueError("MANIFEST.json lists an unsafe file name: %r" % name)
    return m


def check():
    ref = latest_commit()
    m = remote_manifest(ref)
    have = installed()
    same = have.get("files") == m["files"]
    return {"current": have.get("version", "?"), "current_commit": (have.get("commit") or "")[:7],
            "latest": m.get("version", "?"), "latest_commit": ref[:7] if ref != BRANCH else "main",
            "up_to_date": same, "ref": ref, "manifest": m, "notes": m.get("notes", "")}


def apply(force=False):
    """Download, verify, back up, install. Nothing is changed unless every file verifies."""
    info = check()
    if info["up_to_date"] and not force:
        return dict(info, changed=False, message="already the newest version")
    ref, m = info["ref"], info["manifest"]
    fresh = {}
    for name, want in m["files"].items():
        data = _get("%s/%s/%s/app/%s" % (RAW, REPO, ref, name))
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise ValueError("%s failed its checksum, nothing was changed" % name)
        if name.endswith(".py"):
            compile(data, name, "exec")
        fresh[name] = data
    have = installed()
    stamp = "v%s-%s-%s" % (have.get("version", "old"), (have.get("commit") or "local")[:7], time.strftime("%Y%m%d-%H%M%S"))
    dest = os.path.join(BACKUPS, stamp)
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(core.APP_DIR):
        if name.endswith((".py", ".html")) or name == "VERSION.json":
            shutil.copy2(os.path.join(core.APP_DIR, name), os.path.join(dest, name))
    for name, data in fresh.items():
        tmp = os.path.join(core.APP_DIR, ".%s.new" % name)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(core.APP_DIR, name))
    core.save_json(LOCAL, {"version": m.get("version"), "commit": ref, "files": m["files"], "installed": time.time()})
    migrate()
    olds = sorted(os.listdir(BACKUPS))
    for old in olds[:-5]:
        shutil.rmtree(os.path.join(BACKUPS, old), ignore_errors=True)
    return dict(info, changed=True, backup=dest, files=len(fresh),
                message="updated to V%s (commit %s)" % (m.get("version"), info["latest_commit"]))


def migrate():
    """The command is called zet. Remove the old zs and zets commands and the old zs.py."""
    bindir = os.path.join(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"), "bin")
    zet = os.path.join(core.APP_DIR, "zet.py")
    if not os.path.exists(zet):
        return False
    try:
        os.makedirs(bindir, exist_ok=True)
        path = os.path.join(bindir, "zet")
        with open(path, "w") as f:
            f.write('#!/data/data/com.termux/files/usr/bin/sh\nexec python "%s" "$@"\n' % zet)
        os.chmod(path, 0o755)
        for old in ("zs", "zets"):
            p = os.path.join(bindir, old)
            if os.path.exists(p):
                os.remove(p)
        old_py = os.path.join(core.APP_DIR, "zs.py")
        if os.path.exists(old_py):
            os.remove(old_py)
        return True
    except OSError:
        return False
