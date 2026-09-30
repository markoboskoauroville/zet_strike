#!/data/data/com.termux/files/usr/bin/python
"""net.py - ZET Strike V9: every download goes through here, and nothing is downloaded twice.

Marko, 30.9.2026: "this app unnecessarily downloads the stream from ZET every time it runs ... If it's
fresh enough ... you don't download it every second ... optimize my traffic. I'm working from mobile
phone internet."

    A COPY ON DISK, SHARED       ~/.zet-strike/cache/<name>: the server and every `zet` command read the
                                 same copy, so `zet now` ten times in a minute is one download
    ASK ONLY WHEN IT IS OLD      each caller says how old is still fresh enough (max_age); younger than
                                 that, the copy is used and the network is not touched
    ASK "HAS IT CHANGED?"        If-None-Match / If-Modified-Since: an unchanged answer is a 304 with no
                                 body, and the copy is used again
    COMPRESSED                   Accept-Encoding: gzip, for the text and protobuf answers
    COUNTED                      every byte that came over the network, per source, per day, in
                                 traffic.json: what was SPENT, counted up (quota-and-fallback.md §9b)

Only the body is counted: HTTP headers add a few hundred bytes a request and are not measured.
"""
import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zlib

import core

CACHE = os.path.join(core.APP_DIR, "cache")
TRAFFIC_FILE = os.path.join(core.APP_DIR, "traffic.json")
KEEP_DAYS = 14


def _paths(name):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:80]
    return os.path.join(CACHE, safe + ".body"), os.path.join(CACHE, safe + ".json")


def _meta(path):
    m = core.load_json(path, {})
    return m if isinstance(m, dict) else {}


def count(source, wire_bytes, how):
    """Add one request to today's tally. how: net (a body came), same (304), cache (no request), fail."""
    day = core.now_zagreb().strftime("%Y-%m-%d")
    with core.FileLock("traffic"):
        t = core.load_json(TRAFFIC_FILE, {})
        t = t if isinstance(t, dict) else {}
        d = t.setdefault(day, {})
        s = d.setdefault(source, {"bytes": 0, "net": 0, "same": 0, "cache": 0, "fail": 0})
        s["bytes"] += int(wire_bytes)
        s[how] = s.get(how, 0) + 1
        for old in sorted(t)[:-KEEP_DAYS]:
            del t[old]
        core.save_json(TRAFFIC_FILE, t)


def today():
    t = core.load_json(TRAFFIC_FILE, {})
    t = t if isinstance(t, dict) else {}
    return t.get(core.now_zagreb().strftime("%Y-%m-%d"), {})


def source_of(name):
    return name.split("-", 1)[0]


def _decode(raw, encoding):
    enc = (encoding or "").lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def fetch(name, url, max_age, timeout=20, limit=8 * 1024 * 1024, accept=None):
    """(body bytes, info). info: {"how": cache|net|same, "age": seconds, "bytes": on the wire}.
    A copy younger than max_age is returned without asking. Raises when the network fails and there is
    no copy at all; with a copy, a failure returns the copy and says so (info["stale"])."""
    body_path, meta_path = _paths(name)
    meta = _meta(meta_path)
    have = os.path.exists(body_path)
    age = time.time() - meta.get("t", 0) if have else None
    if have and age is not None and age < max_age:
        count(source_of(name), 0, "cache")
        with open(body_path, "rb") as f:
            return f.read(), {"how": "cache", "age": age, "bytes": 0}
    h = {"User-Agent": core.UA, "Accept-Encoding": "gzip"}
    if accept:
        h["Accept"] = accept
    if have:
        if meta.get("etag"):
            h["If-None-Match"] = meta["etag"]
        if meta.get("modified"):
            h["If-Modified-Since"] = meta["modified"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            raw = r.read(limit)
            headers = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        if e.code == 304 and have:
            meta["t"] = time.time()
            core.save_json(meta_path, meta)
            count(source_of(name), 0, "same")
            with open(body_path, "rb") as f:
                return f.read(), {"how": "same", "age": 0, "bytes": 0}
        count(source_of(name), 0, "fail")
        if have:
            with open(body_path, "rb") as f:
                return f.read(), {"how": "cache", "age": age, "bytes": 0, "stale": str(e)}
        raise
    except Exception as e:
        count(source_of(name), 0, "fail")
        if have:
            with open(body_path, "rb") as f:
                return f.read(), {"how": "cache", "age": age, "bytes": 0, "stale": str(e)}
        raise
    body = _decode(raw, headers.get("content-encoding"))
    count(source_of(name), len(raw), "net")
    os.makedirs(CACHE, exist_ok=True)
    tmp = body_path + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, body_path)
    core.save_json(meta_path, {"t": time.time(), "url": url, "etag": headers.get("etag"),
                               "modified": headers.get("last-modified"), "size": len(body),
                               "wire": len(raw), "sha": hashlib.sha256(body).hexdigest()[:16]})
    return body, {"how": "net", "age": 0, "bytes": len(raw)}


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1000.0
