#!/data/data/com.termux/files/usr/bin/python
"""labels.py - ZET Strike V10: key titles, and bringing keys in from files.

Marko, 30.9.2026: "a file picker for the keys ... It can have multiple keys and each key can have
title ... Never in any future app you build API keys without file pickers."

THE TITLE      the name beside the key (keyring.md §10d): "kalabhumi is unpaid" is something a person
               can act on, "key 7 of 21" is not. Kept by fingerprint in secrets/key_labels.json, 0600,
               because a title is often an account name. Taken from the file when the file has one
               (the block's name line, or `label:` in the keyring v1 format); renamed in the page.

IMPORT         one door for the file picker, the paste boxes and `zet keys import FILE`: keyparse.py
               (KEYRING_TERMUX's parser, verbatim) finds every key by shape and its title by
               elimination; AQ. keys go to the Gemini ring, AIza keys to the Google keys, and what
               this app has no use for (an Anthropic key, a Groq key) is counted and left alone.
"""
import os
import threading

import core
import keyparse

SECRET_DIR = os.path.join(core.APP_DIR, "secrets")
LABELS_FILE = os.path.join(SECRET_DIR, "key_labels.json")
MAX_TITLE = 60
_lock = threading.Lock()


def _all():
    d = core.load_json(LABELS_FILE, {})
    return d if isinstance(d, dict) else {}


def get(fp):
    return _all().get(fp, "")


def set(fp, title):
    title = str(title or "")
    for _prov, rx in keyparse.SHAPES:                      # a title never holds a key, whatever it was given
        title = rx.sub(" ", title)
    title = " ".join(title.split())[:MAX_TITLE]
    with _lock:
        os.makedirs(SECRET_DIR, exist_ok=True)
        os.chmod(SECRET_DIR, 0o700)
        d = _all()
        if title:
            d[fp] = title
        else:
            d.pop(fp, None)
        core.save_json(LABELS_FILE, d, mode=0o600)
    return title


def forget(fp):
    return set(fp, "")


def parse_for(text, source, providers):
    """[(value, title)] for the providers asked for, in the order the file has them."""
    return [(e["value"], e["label"]) for e in keyparse.parse(text or "", source or "") if e["provider"] in providers]


def import_text(text, source="picked file"):
    """Every key in the text into the app. Returns what happened, per provider, never a key."""
    import mapkey
    import news
    entries = keyparse.parse(text or "", source or "")
    gem = [(e["value"], e["label"]) for e in entries if e["provider"] == "gemini"]
    goo = [(e["value"], e["label"]) for e in entries if e["provider"] == "google"]
    other = {}
    for e in entries:
        if e["provider"] not in ("gemini", "google"):
            other[e["provider"]] = other.get(e["provider"], 0) + 1
    g_fps, g_dups = news.add_values([v for v, _t in gem])
    m_fps, m_dups = mapkey.add([v for v, _t in goo])
    titled = 0
    for v, t in gem:
        if t:
            set(news.fingerprint(v), t)
            titled += 1
    for v, t in goo:
        if t:
            set(mapkey.fingerprint(v), t)
            titled += 1
    return {"source": source, "found": len(entries), "gemini": len(g_fps), "google": len(m_fps),
            "duplicates": g_dups + m_dups, "titled": titled, "other": other,
            "gemini_fps": g_fps, "google_fps": m_fps}


def summary(r):
    """One sentence for a person, from import_text's answer."""
    parts = []
    if r["gemini"]:
        parts.append("%d Gemini key%s" % (r["gemini"], "" if r["gemini"] == 1 else "s"))
    if r["google"]:
        parts.append("%d Google Maps key%s" % (r["google"], "" if r["google"] == 1 else "s"))
    s = "%s: " % r["source"] + (", ".join(parts) + " added" if parts else "nothing new")
    if r["duplicates"]:
        s += ", %d already here" % r["duplicates"]
    if r["titled"]:
        s += ", %d with a title" % r["titled"]
    if r["other"]:
        s += "; not used by this app: " + ", ".join("%d %s" % (n, p) for p, n in sorted(r["other"].items()))
    if not r["found"]:
        s = "%s: no key found in it" % r["source"]
    return s + "."
