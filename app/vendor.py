#!/data/data/com.termux/files/usr/bin/python
"""vendor.py - ZET Strike V7: the map library, kept on the phone.

The page draws its map with Leaflet 1.9.4. V6 loaded it from unpkg.com on every visit, so with no
signal on the first open the whole page script stopped at `L is not defined`, and settings, news and
the log went with the map. Now the server fetches each file ONCE, checks its SHA-256 against the
numbers below (measured 30.9.2026 from the npm tarball, whose sha512 matched the registry's
`dist.integrity`), keeps it in ~/.zet-strike/vendor/, and serves it from there ever after.

Three sources, tried in order, because any one of them can be blocked on a given network:
unpkg, jsDelivr, and the npm registry's own tarball. Nothing that fails its checksum is kept.
"""
import hashlib
import io
import os
import tarfile
import threading
import urllib.request

import core

DIR = os.path.join(core.APP_DIR, "vendor")
VERSION = "1.9.4"
FILES = {
    "leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
    "leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
}
TYPES = {"leaflet.js": "application/javascript", "leaflet.css": "text/css"}
SOURCES = [
    "https://unpkg.com/leaflet@%s/dist/%%s" % VERSION,
    "https://cdn.jsdelivr.net/npm/leaflet@%s/dist/%%s" % VERSION,
]
TARBALL = "https://registry.npmjs.org/leaflet/-/leaflet-%s.tgz" % VERSION
_lock = threading.Lock()


def _get(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": core.UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit)


def _ok(name, data):
    return data is not None and hashlib.sha256(data).hexdigest() == FILES[name]


def _keep(name, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = os.path.join(DIR, "." + name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(DIR, name))


def _from_tarball():
    """Every file this app needs, out of the one npm tarball, checked."""
    got = {}
    raw = _get(TARBALL, 8 * 1024 * 1024)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
        for name in FILES:
            try:
                f = t.extractfile("package/dist/" + name)
            except KeyError:
                continue
            data = f.read() if f else None
            if _ok(name, data):
                got[name] = data
    return got


def get(name):
    """The file's bytes, or None when it is not here and no source could give a good copy."""
    if name not in FILES:
        return None
    path = os.path.join(DIR, name)
    try:
        with open(path, "rb") as f:
            data = f.read()
        if _ok(name, data):
            return data
    except OSError:
        pass
    with _lock:
        for src in SOURCES:
            try:
                data = _get(src % name, 2 * 1024 * 1024)
            except Exception:
                continue
            if _ok(name, data):
                _keep(name, data)
                return data
        try:
            got = _from_tarball()
        except Exception:
            got = {}
        for n, data in got.items():
            _keep(n, data)
        return got.get(name)
