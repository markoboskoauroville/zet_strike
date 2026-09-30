#!/data/data/com.termux/files/usr/bin/python
"""mapkey.py - ZET Strike V7: the Google Maps key, its test, and the Google map tiles.

ONE KEY PER JOB (keyring.md §2b): the map needs one Google key, so this holds one. It lives in
~/.zet-strike/secrets/google_key, 0600, the folder 0700, and is shown only by fingerprint.
Where a key is looked for, in order (keyring.md §11): ZET_GOOGLE_KEY in the environment, the 0600
file, then `keyring get google` when the Keyring app is on the phone.

THE TEST DOES WORK (keyring.md §2c, key-testing.md §6b). probes.google_probe asks Places (New),
Map Tiles, Geocoding and Gemini each for the smallest thing it sells and reads the BODY, because
Google answers 200 while denying. The map needs the Map Tiles API, so the verdict for this app is
the Tiles line: a key that works only for Places is a good key that cannot draw this map, and the
page says exactly that.

THE KEY NEVER REACHES THE PAGE. Tiles are fetched here and handed on at /tile/google/z/x/y: the
server makes a Map Tiles session (a token that lasts about two weeks) and asks for each tile with
it. A tile URL with the key in it would put the key in Chrome's history and every tile request.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

import core
import probes

SECRET_DIR = os.path.join(core.APP_DIR, "secrets")
KEY_FILE = os.path.join(SECRET_DIR, "google_key")
STATE_FILE = os.path.join(core.APP_DIR, "google_key.json")
TILE_BASE = os.environ.get("ZET_TILE_BASE", "https://tile.googleapis.com")
KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")   # a Google Cloud key (Maps, Places, Tiles); never a Gemini key
_lock = threading.Lock()
_session = {"token": None, "expiry": 0, "fp": None}


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- where the key is
def _from_file():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _from_keyring():
    """The Keyring app's one value on stdout (keyring.md §11). Bounded: a missing or slow keyring
    must never hold up the map."""
    if os.environ.get("ZET_NO_KEYRING") or not shutil.which("keyring"):
        return None
    try:
        r = subprocess.run(["keyring", "get", "google"], capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    k = (r.stdout or "").strip()
    return k if r.returncode == 0 and KEY_SHAPE.fullmatch(k) else None


def current():
    """(key, source) or (None, None)."""
    k = os.environ.get("ZET_GOOGLE_KEY", "").strip()
    if k:
        return k, "environment"
    k = _from_file()
    if k:
        return k, "this phone"
    k = _from_keyring()
    if k:
        return k, "Keyring app"
    return None, None


def extract(text):
    """By shape, then a lone long token: a pasted key may arrive with a label or spaces round it."""
    m = KEY_SHAPE.search(text or "")
    if m:
        return m.group(0)
    pieces = [p for p in re.split(r"[\s,;:=\"']+", text or "") if re.fullmatch(r"[A-Za-z0-9_\-]{30,100}", p)]
    return pieces[0] if len(pieces) == 1 else None


def save(text):
    key = extract(text)
    if not key:
        return {"error": "No Google key found in what was pasted. A Google Maps key begins AIza."}
    with _lock:
        os.makedirs(SECRET_DIR, exist_ok=True)
        os.chmod(SECRET_DIR, 0o700)
        tmp = KEY_FILE + ".part"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        os.replace(tmp, KEY_FILE)
        os.chmod(KEY_FILE, 0o600)
        core.save_json(STATE_FILE, {"fp": fingerprint(key), "state": "new", "t": time.time()}, mode=0o600)
        _session.update(token=None, expiry=0, fp=None)
    core.log_event("key", "Google Maps key %s saved" % fingerprint(key))
    return {"saved": True, "fp": fingerprint(key)}


def delete():
    with _lock:
        had = os.path.exists(KEY_FILE)
        for p in (KEY_FILE, STATE_FILE):
            try:
                os.remove(p)
            except OSError:
                pass
        _session.update(token=None, expiry=0, fp=None)
    if had:
        core.log_event("key", "Google Maps key deleted")
    return had


def status():
    """Fingerprint, where it came from, and the last test. Never the key."""
    key, source = current()
    st = core.load_json(STATE_FILE, {})
    st = st if isinstance(st, dict) else {}
    if not key:
        return {"has_key": False, "fp": None, "source": None, "state": None, "detail": None, "tiles": None, "t": None}
    fp = fingerprint(key)
    if st.get("fp") != fp:
        st = {}
    return {"has_key": True, "fp": fp, "source": source, "state": st.get("state", "new"),
            "detail": st.get("detail"), "tiles": st.get("tiles"), "apis": st.get("apis"), "t": st.get("t")}


# ---------------------------------------------------------------- the test
def test():
    """The work probe, every Google API asked; the map's own verdict is the Tiles line."""
    key, _source = current()
    if not key:
        return {"error": "No Google Maps key yet. Paste one in Settings, or run: zet keys google"}
    r = probes.google_probe(key)
    apis = [{"api": n, "state": s, "why": w, "status": c} for n, s, w, c in r.get("apis", [])]
    tiles = next((a for a in apis if a["api"] == "Tiles"), None)
    tiles_state = tiles["state"] if tiles else "unclear"
    if tiles_state == "works":
        map_says = "The Google map works with this key."
    elif tiles_state == "not enabled":
        map_says = "This key cannot draw the Google map: enable the Map Tiles API for its project in the Google Cloud console."
    elif tiles_state == "restricted":
        map_says = "This key is restricted to another IP or website, so the phone cannot use it for the map."
    elif tiles_state == "no credit":
        map_says = "The key's project needs billing turned on before Google sends map tiles."
    elif tiles_state == "throttled":
        map_says = "Google says wait a moment (throttled). The key is fine."
    elif tiles_state == "rejected":
        map_says = "Google refused this key. Check it was copied whole."
    else:
        map_says = "Google gave no clear answer (%s). Try again when the network is steady." % (tiles["why"] if tiles else "no answer")
    out = {"fp": fingerprint(key), "state": r["state"], "detail": r["detail"], "tiles": tiles_state,
           "map_says": map_says, "apis": apis, "t": time.time()}
    with _lock:
        core.save_json(STATE_FILE, {k: out[k] for k in ("fp", "state", "detail", "tiles", "apis", "t")}, mode=0o600)
        if tiles_state != "works":
            _session.update(token=None, expiry=0, fp=None)
    core.log_event("key", "Google Maps key %s tested: %s; map tiles %s" % (out["fp"], r["state"], tiles_state))
    return out


# ---------------------------------------------------------------- the tiles
def _new_session(key):
    body = json.dumps({"mapType": "roadmap", "language": "hr-HR", "region": "HR"}).encode()
    code, data, _h = probes.http("POST", "%s/v1/createSession?key=%s" % (TILE_BASE, urllib.parse.quote(key)),
                                 {"Content-Type": "application/json"}, body)
    j = probes.jbody(data) or {}
    if code != 200 or not j.get("session"):
        st, why = probes.google_verdict(code, data)
        raise RuntimeError("Google map session refused: %s" % why)
    try:
        expiry = float(j.get("expiry") or 0)
    except (TypeError, ValueError):
        expiry = 0
    return j["session"], expiry or time.time() + 86400


def tile(z, x, y):
    """(status, bytes, content type). One retry with a fresh session when Google says the old one ended."""
    key, _source = current()
    if not key:
        return 404, b"", "text/plain"
    fp = fingerprint(key)
    for attempt in (0, 1):
        with _lock:
            fresh = _session["token"] and _session["fp"] == fp and _session["expiry"] - time.time() > 600
            token = _session["token"] if fresh else None
        if not token:
            try:
                token, expiry = _new_session(key)
            except RuntimeError as e:
                return 502, str(e).encode(), "text/plain"
            with _lock:
                _session.update(token=token, expiry=expiry, fp=fp)
        url = "%s/v1/2dtiles/%d/%d/%d?session=%s&key=%s" % (TILE_BASE, z, x, y, urllib.parse.quote(token), urllib.parse.quote(key))
        code, data, headers = probes.http("GET", url, {"Accept": "image/*"}, timeout=15)
        if code == 200:
            ctype = {k.lower(): v for k, v in (headers or {}).items()}.get("content-type", "image/png")
            return 200, data, ctype
        if code in (400, 401, 403) and attempt == 0:
            with _lock:
                _session.update(token=None, expiry=0, fp=None)
            continue
        return (code if code > 0 else 504), b"", "text/plain"
    return 502, b"", "text/plain"
