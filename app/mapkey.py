#!/data/data/com.termux/files/usr/bin/python
"""mapkey.py - ZET Strike: the Google Maps keys, their test, and the Google map tiles.

ONE KEY IN USE PER JOB (keyring.md §2b), several kept (V10): each with a title, one chosen by a radio.
They live in ~/.zet-strike/secrets/google_keys, 0600, the folder 0700, shown only by fingerprint and
title. Where the key in use is looked for (keyring.md §11): ZET_GOOGLE_KEY in the environment, the
keys on this phone, then `keyring get google` when the Keyring app is on the phone.

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
_lock = threading.RLock()   # saved_keys() may migrate a V7-V9 file while add() holds it
_session = {"token": None, "expiry": 0, "fp": None}


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- where the keys are
# V10: more than one Google key, each with a title (labels.py), one of them in use (a radio in the
# page, keyring.md §6). secrets/google_keys holds them one per line, 0600; google_key.json holds which
# one is in use and each one's last test, by fingerprint. V7-V9 kept one key in secrets/google_key:
# it is moved into the list the first time it is read.
KEYS_FILE = os.path.join(SECRET_DIR, "google_keys")


def _write_secret(path, text):
    os.makedirs(SECRET_DIR, exist_ok=True)
    os.chmod(SECRET_DIR, 0o700)
    tmp = path + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _state():
    st = core.load_json(STATE_FILE, {})
    st = st if isinstance(st, dict) else {}
    if "keys" not in st:                                  # the V7-V9 shape: one key's test at the top level
        old = {k: st[k] for k in ("state", "detail", "tiles", "apis", "t") if k in st}
        st = {"active": st.get("fp"), "keys": {st["fp"]: old} if st.get("fp") else {}}
    return st


def _save_state(st):
    core.save_json(STATE_FILE, st, mode=0o600)


def saved_keys():
    """Every Google key on this phone, in the order added. Moves a V7-V9 single key file in."""
    keys = []
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            keys = [k.strip() for k in f if k.strip()]
    except OSError:
        pass
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            legacy = f.read().strip()
    except OSError:
        legacy = ""
    if legacy:
        with _lock:
            if legacy not in keys:
                keys.insert(0, legacy)
            _write_secret(KEYS_FILE, "".join(k + "\n" for k in keys))
            os.remove(KEY_FILE)
    return keys


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
    """(key, source) or (None, None): the environment, the key in use on this phone, the Keyring app."""
    k = os.environ.get("ZET_GOOGLE_KEY", "").strip()
    if k:
        return k, "environment"
    keys = saved_keys()
    if keys:
        active = _state().get("active")
        for key in keys:
            if fingerprint(key) == active:
                return key, "this phone"
        return keys[0], "this phone"
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


def add(values):
    """Add keys (values, already found by the parser). Returns (added fingerprints, duplicates).
    The first key on a phone that had none is the one in use."""
    with _lock:
        keys = saved_keys()
        added = [v for v in dict.fromkeys(values) if v and v not in keys]
        if added:
            _write_secret(KEYS_FILE, "".join(k + "\n" for k in keys + added))
            st = _state()
            for v in added:
                st["keys"].setdefault(fingerprint(v), {"state": "new", "t": time.time()})
            if not st.get("active") or st["active"] not in {fingerprint(k) for k in keys + added}:
                st["active"] = fingerprint((keys + added)[0])
            _save_state(st)
            _session.update(token=None, expiry=0, fp=None)
    for v in added:
        core.log_event("key", "Google Maps key %s saved" % fingerprint(v))
    return [fingerprint(v) for v in added], len(values) - len(added)


def save(text, label=""):
    """The paste box: every Google key in the text by the Keyring parser (with its title), or one lone
    token when the text is only that."""
    import labels
    got = labels.parse_for(text, "pasted", ("google",))
    if not got:
        key = extract(text)
        got = [(key, label)] if key else []
    if not got:
        return {"error": "No Google key found in what was pasted. A Google Maps key begins AIza."}
    fps, dups = add([v for v, _l in got])
    for v, l in got:
        if l or label:
            labels.set(fingerprint(v), l or label)
    return {"saved": True, "fp": fingerprint(got[0][0]), "added": len(fps), "duplicates": dups}


def select(fp):
    with _lock:
        if fp not in {fingerprint(k) for k in saved_keys()}:
            return False
        st = _state()
        st["active"] = fp
        _save_state(st)
        _session.update(token=None, expiry=0, fp=None)
    core.log_event("key", "Google Maps key %s is now the one in use" % fp)
    return True


def delete(fp=None):
    """Delete one key (the one in use when no fingerprint is given)."""
    import labels
    with _lock:
        keys = saved_keys()
        if fp is None:
            st = _state()
            fp = st.get("active") or (fingerprint(keys[0]) if keys else None)
        keep = [k for k in keys if fingerprint(k) != fp]
        if len(keep) == len(keys):
            return False
        _write_secret(KEYS_FILE, "".join(k + "\n" for k in keep))
        st = _state()
        st["keys"].pop(fp, None)
        if st.get("active") == fp:
            st["active"] = fingerprint(keep[0]) if keep else None
        _save_state(st)
        _session.update(token=None, expiry=0, fp=None)
    labels.forget(fp)
    core.log_event("key", "Google Maps key %s deleted" % fp)
    return True


def status():
    """The key in use (fingerprint, where it came from, its last test) and every saved key. Never a key."""
    import labels
    key, source = current()
    st = _state()
    rows = []
    for k in saved_keys():
        fp = fingerprint(k)
        t = st["keys"].get(fp, {})
        rows.append({"fp": fp, "label": labels.get(fp), "state": t.get("state", "new"), "tiles": t.get("tiles"),
                     "detail": t.get("detail"), "apis": t.get("apis"), "t": t.get("t"),
                     "active": bool(key) and source == "this phone" and fp == fingerprint(key)})
    if not key:
        return {"has_key": False, "fp": None, "source": None, "state": None, "detail": None, "tiles": None, "t": None,
                "label": "", "keys": rows}
    fp = fingerprint(key)
    t = st["keys"].get(fp, {})
    return {"has_key": True, "fp": fp, "source": source, "label": labels.get(fp), "state": t.get("state", "new"),
            "detail": t.get("detail"), "tiles": t.get("tiles"), "apis": t.get("apis"), "t": t.get("t"), "keys": rows}


# ---------------------------------------------------------------- the test
def test(fp=None):
    """The work probe, every Google API asked; the map's own verdict is the Tiles line.
    fp: a saved key to test; none, the key in use."""
    key, _source = current()
    if fp:
        key = next((k for k in saved_keys() if fingerprint(k) == fp), None)
        if not key:
            return {"error": "No saved Google key with fingerprint %s." % fp}
    if not key:
        return {"error": "No Google Maps key yet. Choose a key file or paste one in Settings, or run: zet keys google"}
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
        st = _state()
        st["keys"][out["fp"]] = {k: out[k] for k in ("state", "detail", "tiles", "apis", "t")}
        _save_state(st)
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
