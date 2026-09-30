#!/data/data/com.termux/files/usr/bin/python
"""news.py - ZET Strike V10 news desk: Croatian headlines (RSS, free) plus a Gemini summary and timeline.

Gemini keys follow MANTRA_MANIFEST quota-and-fallback: four verdicts (ok, dead, cool, soft), one classifier
that reads status AND body AND headers, each key tried at most once per call, resume at the last good key,
lock around choosing and marking but never around the network call, fingerprints (never keys) in state and
logs. Second axis: when a model is missing or busy, the next model is tried with the same key.
"""
import concurrent.futures
import email.utils
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import core

SECRET_DIR = os.path.join(core.APP_DIR, "secrets")
KEYS_FILE = os.path.join(SECRET_DIR, "gemini_keys")
RING_FILE = os.path.join(core.APP_DIR, "keyring.json")
NEWS_FILE = os.path.join(core.APP_DIR, "news.json")
SUMMARY_FILE = os.path.join(core.APP_DIR, "summary.json")
GEMINI_BASE = os.environ.get("ZET_GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")
FEEDS = [
    ("Google News", "https://news.google.com/rss/search?q=ZET+%C5%A1trajk+when:3d&hl=hr&gl=HR&ceid=HR:hr"),
    ("Google News", "https://news.google.com/rss/search?q=%C5%A1trajk+Zagreb+Holding+when:3d&hl=hr&gl=HR&ceid=HR:hr"),
    ("Index.hr", "https://www.index.hr/rss/vijesti"),
    ("N1", "https://n1info.hr/feed/"),
    ("tportal", "https://www.tportal.hr/rss"),
    ("Dnevnik.hr", "https://dnevnik.hr/assets/feed/articles/"),
    ("Net.hr", "https://net.hr/feed"),
    ("Telegram", "https://www.telegram.hr/feed/"),
]
KEY_SHAPE = re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}")   # Gemini keys begin AQ. (keyring.md); AIza is a Google Cloud key
CREDIT_WORDS = ("zero_credits", "e0300", "credit balance", "insufficient", "quota exceeded", "out of credits",
                "payment required", "billing")
_ring_lock = threading.Lock()
_news_lock = threading.Lock()
_ai_lock = threading.Lock()


# ================================================================ headlines
def _text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return " ".join(s.split())


def parse_feed(data, source):
    """RSS 2.0 or Atom -> [{title, link, when, source, desc}]"""
    items = []
    root = ET.fromstring(data)
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        when = 0
        pd = _text(it, "pubDate") or _text(it, "{http://purl.org/dc/elements/1.1/}date")
        if pd:
            try:
                when = email.utils.parsedate_to_datetime(pd).timestamp()
            except Exception:
                when = 0
        src = _text(it, "source") or source
        title = _clean(_text(it, "title"))
        if src != source and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        items.append({"title": title, "link": _text(it, "link"), "when": when, "source": src,
                      "desc": _clean(_text(it, "description"))[:400]})
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        when = 0
        up = _text(it, atom + "updated") or _text(it, atom + "published")
        if up:
            try:
                from datetime import datetime
                when = datetime.fromisoformat(up.replace("Z", "+00:00")).timestamp()
            except Exception:
                when = 0
        items.append({"title": _clean(_text(it, atom + "title")), "link": link.get("href", "") if link is not None else "",
                      "when": when, "source": source, "desc": _clean(_text(it, atom + "summary"))[:400]})
    return [i for i in items if i["title"]]


def relevant(item):
    """ZET the company is written in capitals. Lower-case "zet" is Croatian for son-in-law and is not news here."""
    raw = item["title"] + " " + item.get("desc", "")
    if re.search(r"\bZET\b", raw) or re.search(r"\bzetovc|\bzetova", raw, re.I):
        return True
    t = core.norm(raw)
    strike = "strajk" in t
    local = any(w in t for w in ("zagreb", "holding", "tomasevic", "cistoc", "tramvaj", "sindikat"))
    return strike and local


def _get(url, fresh, timeout=15):
    """Through net.fetch: a copy younger than `fresh` seconds is used as it is; older, the site is asked
    whether it changed (an unchanged feed costs no body), gzip."""
    import net
    name = "news-" + hashlib.sha1(url.encode()).hexdigest()[:10]
    body, _info = net.fetch(name, url, fresh, timeout=timeout, limit=3_000_000,
                            accept="application/rss+xml, application/xml, text/xml, */*")
    return body


def fetch_headlines(max_age_days=4, fresh=0):
    """Fetch every feed in parallel, keep strike stories, log the new ones. Returns (items, report).
    fresh: seconds a saved copy of a feed is still good for (0: always ask, conditionally)."""
    report = {}

    def one(src_url):
        src, url = src_url
        try:
            got = [i for i in parse_feed(_get(url, fresh), src) if relevant(i)]
            return src, url, got, None
        except Exception as e:
            return src, url, [], str(e)[:120]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, FEEDS))
    fresh = []
    for src, url, got, err in results:
        report.setdefault(src, {"ok": 0, "items": 0, "errors": []})
        if err:
            report[src]["errors"].append(err)
        else:
            report[src]["ok"] += 1
            report[src]["items"] += len(got)
        fresh.extend(got)
    cutoff = time.time() - max_age_days * 86400
    new_items = []
    with _news_lock:
        store = core.load_json(NEWS_FILE, {})
        if not isinstance(store, dict) or "items" not in store:
            store = {"items": {}}
        items = store["items"]
        for i in fresh:
            if i["when"] and i["when"] < cutoff:
                continue
            nid = hashlib.sha1(core.norm(i["title"]).encode()).hexdigest()[:12]
            if nid in items:
                old = items[nid]
                if not old.get("link") and i["link"]:
                    old["link"] = i["link"]
                continue
            i["id"] = nid
            i["seen"] = time.time()
            items[nid] = i
            new_items.append(i)
        if len(items) > 400:
            for k in sorted(items, key=lambda k: items[k].get("when") or items[k]["seen"])[:len(items) - 300]:
                del items[k]
        store["fetched"] = time.time()
        store["report"] = report
        core.save_json(NEWS_FILE, store)
    for i in sorted(new_items, key=lambda i: i["when"] or i["seen"]):
        core.log_event("news", i["title"], source=i["source"], link=i["link"], when=i["when"])
    return headlines(), {"new": len(new_items), "sources": report}


def headlines(limit=60):
    store = core.load_json(NEWS_FILE, {})
    items = list((store.get("items") or {}).values()) if isinstance(store, dict) else []
    items.sort(key=lambda i: -(i.get("when") or i.get("seen") or 0))
    return items[:limit]


# ================================================================ key ring
def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def _read_keys():
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            return [k.strip() for k in f if k.strip()]
    except OSError:
        return []


def _write_keys(keys):
    os.makedirs(SECRET_DIR, exist_ok=True)
    os.chmod(SECRET_DIR, 0o700)
    tmp = KEYS_FILE + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(k + "\n" for k in keys))
    os.replace(tmp, KEYS_FILE)
    os.chmod(KEYS_FILE, 0o600)


def extract_keys(text):
    """By shape, never by eye. Gemini keys begin AQ. (keyring.md, MANTRA_MANIFEST); pasted keys may arrive
    glued together. A lone long token is taken too, so a key of another shape can still be pasted alone."""
    found = KEY_SHAPE.findall(text or "")
    if not found:
        for piece in re.split(r"[\s,;]+", text or ""):
            if re.fullmatch(r"[A-Za-z0-9_\-]{30,100}", piece):
                found.append(piece)
    out = []
    for k in found:
        if k not in out:
            out.append(k)
    return out


def add_values(values):
    """Add Gemini keys already found by the parser. Returns (added fingerprints, duplicates)."""
    with _ring_lock:
        keys = _read_keys()
        added = [k for k in dict.fromkeys(values) if k and k not in keys]
        if added:
            _write_keys(keys + added)
            ring = _load_ring()
            for k in added:
                ring["keys"][fingerprint(k)] = {"state": "new", "added": time.time()}
            _save_ring(ring)
    for k in added:
        core.log_event("key", "Gemini key %s added" % fingerprint(k))
    return [fingerprint(k) for k in added], len(values) - len(added)


def add_keys(text, title=""):
    """The paste box and `zet keys add`: KEYRING_TERMUX's parser (labels.parse_for) takes every AQ. key
    with the title its block carries; a lone token of another shape pasted on its own is still taken."""
    import labels
    got = labels.parse_for(text, "pasted", ("gemini",))
    if not got:
        got = [(k, "") for k in extract_keys(text)]
    fps, dups = add_values([v for v, _t in got])
    for v, t in got:
        if t or title:
            labels.set(fingerprint(v), t or title)
    return {"found": len(got), "added": len(fps), "duplicates": dups}


def remove_key(fp):
    with _ring_lock:
        keys = _read_keys()
        keep = [k for k in keys if fingerprint(k) != fp]
        if len(keep) == len(keys):
            return False
        _write_keys(keep)
        ring = _load_ring()
        ring["keys"].pop(fp, None)
        if ring.get("active") == fp:
            ring["active"] = None
        _save_ring(ring)
    import labels
    labels.forget(fp)
    core.log_event("key", "Gemini key %s deleted" % fp)
    return True


def _load_ring():
    r = core.load_json(RING_FILE, {})
    if not isinstance(r, dict):
        r = {}
    r.setdefault("keys", {})
    r.setdefault("active", None)
    r.setdefault("models_gone", {})
    return r


def _save_ring(r):
    core.save_json(RING_FILE, r, mode=0o600)


def ring_status():
    """What the settings screen shows: fingerprints and states only."""
    import labels
    with _ring_lock:
        keys = _read_keys()
        ring = _load_ring()
    titles = labels._all()
    now = time.time()
    out = []
    for k in keys:
        fp = fingerprint(k)
        st = dict(ring["keys"].get(fp, {"state": "new"}))
        if st.get("state") == "cool" and st.get("until", 0) <= now:
            st["state"] = "ok" if st.get("last_ok") else "new"
        st["fp"] = fp
        st["label"] = titles.get(fp, "")
        st["active"] = ring.get("active") == fp
        st["rest"] = max(0, int(st.get("until", 0) - now)) if st.get("state") == "cool" else 0
        out.append(st)
    return {"keys": out, "models_gone": ring.get("models_gone", {})}


def retry_after(headers, body):
    """Ask, do not guess. Floor 1 s, ceiling 1 h."""
    wait = None
    h = {k.lower(): v for k, v in (headers or {}).items()}
    ra = h.get("retry-after")
    if ra:
        try:
            wait = float(ra)
        except ValueError:
            try:
                wait = email.utils.parsedate_to_datetime(ra).timestamp() - time.time()
            except Exception:
                wait = None
    if wait is None:
        for k, v in h.items():
            if k.startswith("x-ratelimit-reset"):
                m = re.findall(r"([\d.]+)(ms|h|m|s)?", v)
                total = 0.0
                for n, unit in m:
                    total += float(n) * {"ms": 0.001, "h": 3600, "m": 60, "s": 1, "": 1}[unit]
                wait = max(wait or 0, total)
    if wait is None:
        m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', body or "") or re.search(r"retry in ([\d.]+)\s*s", body or "", re.I)
        if m:
            wait = float(m.group(1))
    if wait is None:
        wait = 60
    return max(1.0, min(3600.0, wait))


def classify(status, body, headers=None):
    """(verdict, wait, reason). verdict: ok | dead | cool | soft | model | busy"""
    b = (body or "").lower()
    if status == 200:
        return "ok", 0, "ok"
    if status == 403 and "1010" in b:
        return "soft", 0, "blocked by Cloudflare (1010), not the key"
    if status in (401, 402):
        return "dead", 0, "rejected (%d)" % status
    if status == 403:
        return "dead", 0, "key reported as leaked" if "leak" in b else "permission denied (403)"
    if status == 429:
        daily = re.search(r"per\s*day|perday|requests_per_day|daily", b) is not None
        wait = 3600.0 if daily else retry_after(headers, body)
        return "cool", wait, "daily quota used up" if daily else "rate limited, resting %ds" % wait
    if status == 400 and ("api_key_invalid" in b or "api key not valid" in b or "api key expired" in b):
        return "dead", 0, "invalid or expired key"   # MEASURED 29.9.2026: Gemini says 400, not 401
    if status == 400 and any(w in b for w in CREDIT_WORDS):
        return "dead", 0, "no credit"
    if status == 400 and "location is not supported" in b:
        return "soft", 0, "Gemini is not available from this network location"
    if status == 400:
        return "soft", 0, "request rejected (400): " + _msg(body)
    if status == 404:
        return "model", 0, "model not found"
    if status >= 500:
        return "busy", 0, "Gemini busy (%d)" % status
    return "soft", 0, "unexpected answer %d" % status


def _msg(body):
    try:
        return json.loads(body)["error"]["message"][:160]
    except Exception:
        return (body or "")[:160]


def _http(method, url, key, payload=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": core.UA, "Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, dict(e.headers or {}), None
    except Exception as e:
        return 0, "", {}, "%s" % type(e).__name__ + (": " + str(e)[:100] if str(e) else "")


def _mark(fp, verdict, reason, wait=0):
    with _ring_lock:
        ring = _load_ring()
        st = ring["keys"].setdefault(fp, {"state": "new"})
        st["last"] = reason
        st["t"] = time.time()
        if verdict == "ok":
            st["state"] = "ok"
            st["last_ok"] = time.time()
            st["uses"] = st.get("uses", 0) + 1
            st.pop("until", None)
            ring["active"] = fp
        elif verdict == "dead":
            st["state"] = "dead"
        elif verdict == "cool":
            st["state"] = "cool"
            st["until"] = time.time() + wait
        _save_ring(ring)
    if verdict in ("dead", "cool"):
        core.log_event("key", "Gemini key %s: %s" % (fp, reason))


def _model_gone(model, gone=True):
    with _ring_lock:
        ring = _load_ring()
        if gone:
            ring["models_gone"][model] = time.time()
        else:
            ring["models_gone"].pop(model, None)
        _save_ring(ring)


def generate(payload, models, timeout=90, counter=None):
    """Run one request through the ring. Returns (response_json, meta). meta has error, key, model, tried."""
    with _ring_lock:
        keys = _read_keys()
        ring = _load_ring()
        now = time.time()
        gone = {m for m, t in ring.get("models_gone", {}).items() if now - t < 86400}
        fps = [fingerprint(k) for k in keys]
        start = fps.index(ring["active"]) if ring.get("active") in fps else 0
        order = [(keys[i], fps[i], dict(ring["keys"].get(fps[i], {}))) for i in
                 [(start + j) % len(keys) for j in range(len(keys))]] if keys else []
    meta = {"tried": [], "error": None, "key": None, "model": None}
    if not keys:
        meta["error"] = "No Gemini key saved. Add one in Settings (or: zet keys add)."
        return None, meta
    models = [m for m in models if m and m not in gone] or list(models)
    resting = []
    for key, fp, st in order:                     # each key at most once per call
        if st.get("state") == "dead":
            continue
        if st.get("state") == "cool" and st.get("until", 0) > time.time():
            resting.append(st["until"] - time.time())
            continue
        busy = False
        next_key = False
        for model in models:
            if counter is not None:
                counter.append((fp, model))
            status, body, headers, neterr = _http("POST", "%s/models/%s:generateContent" % (GEMINI_BASE, model),
                                                  key, payload, timeout)
            meta["tried"].append({"key": fp, "model": model, "status": status})
            if neterr:
                meta["error"] = "Network problem reaching Gemini (%s). Nothing else tried." % neterr
                return None, meta                 # every other key would only cost another timeout
            verdict, wait, reason = classify(status, body, headers)
            if verdict == "ok":
                try:
                    data = json.loads(body)
                except Exception:
                    meta["error"] = "Gemini answered 200 with something that is not JSON"
                    return None, meta
                _mark(fp, "ok", "ok, " + model)
                if model in gone:
                    _model_gone(model, False)
                meta.update({"key": fp, "model": model})
                return data, meta
            if verdict == "model":
                _model_gone(model)
                core.log_event("key", "Gemini model %s not found, trying the next model" % model)
                continue
            if verdict == "busy":
                busy = True
                continue
            if verdict in ("dead", "cool"):
                _mark(fp, verdict, reason, wait)
                meta["error"] = reason
                next_key = True
                break
            meta["error"] = reason                # soft: our fault or theirs, no key fixes it
            return None, meta
        if not next_key:
            meta["error"] = "Gemini is busy on every model right now, try again in a minute" if busy \
                else "None of the configured Gemini models exist (%s). Check Settings." % ", ".join(models)
            return None, meta
    if meta["tried"]:
        meta["error"] = "All %d Gemini keys unavailable. Last: %s%s" % (
            len(keys), meta["error"], " (%d resting, next free in %d s)" % (len(resting), min(resting)) if resting else "")
    elif resting:
        meta["error"] = "Every usable key is resting, next one free in %d s" % min(resting)
    else:
        meta["error"] = "All %d Gemini keys are marked dead. Test or replace them in Settings." % len(keys)
    return None, meta


def test_keys():
    """The WORK probe for every key (keyring.md §2c): one token from a model the account itself lists,
    never a list call alone, because a spent account lists its models with a cheerful 200.
    probes.gemini_probe is KEYRING_TERMUX's, copied verbatim. Six states come back (works, valid,
    no credit, rejected, throttled, unclear) and each moves the ring the way it should:
    works revives, throttled rests, no credit and rejected are dead for now, the rest leave it alone."""
    import probes
    results = []
    for key in _read_keys():
        fp = fingerprint(key)
        r = probes.gemini_probe(key)
        state, detail = r["state"], r["detail"]
        if state == "works":
            with _ring_lock:
                ring = _load_ring()
                st = ring["keys"].setdefault(fp, {})
                st.update({"state": "ok", "last": "test: works, " + detail.split(" · ")[-1], "t": time.time(), "last_ok": time.time()})
                st.pop("until", None)
                _save_ring(ring)
        elif state == "throttled":
            m = re.search(r"wait (\d+)s", detail)
            _mark(fp, "cool", "test: " + detail, float(m.group(1)) if m else 60)
        elif state == "no credit":
            _mark(fp, "dead", "test: no credit, it needs a top-up or a paid plan")
        elif state == "rejected":
            _mark(fp, "dead", "test: " + detail)
        else:
            with _ring_lock:
                ring = _load_ring()
                st = ring["keys"].setdefault(fp, {"state": "new"})
                st.update({"last": "test: " + detail, "t": time.time()})
                _save_ring(ring)
        results.append({"fp": fp, "state": state, "verdict": state, "reason": detail, "status": r.get("status")})
    return results


# ================================================================ summary
def _live_lines(live):
    out = []
    for v in (live or [])[:20]:
        kind = "TRAM" if v.get("tram") else "BUS"
        if v.get("parked"):
            out.append("- %s %s vehicle %s parked near %s (not in service)" % (kind, v["line"], v["key"], v.get("near")))
        else:
            out.append("- %s %s vehicle %s heading %s, near %s, %s" % (kind, v["line"], v["key"], v.get("head"),
                                                                       v.get("near"), (v.get("status") or "").lower()))
    return "\n".join(out) or "- no vehicles in the feed"


def build_prompt(cfg, live, line_facts, items, events, previous):
    now = core.now_zagreb()
    lang = "Croatian" if str(cfg.get("language", "en")).lower().startswith("hr") else "English"
    heads = "\n".join("- [%s %s] %s  %s" % (core.from_epoch(i["when"]).strftime("%d.%m %H:%M") if i.get("when") else "?",
                                           i["source"], i["title"], i.get("link", "")) for i in items[:40]) or "- none collected"
    evs = "\n".join("- %s %s" % (core.from_epoch(e["t"]).strftime("%d.%m %H:%M"), e["text"])
                    for e in reversed(events[:40])) or "- none"
    lf = "\n".join("- %s %s: %s" % ("TRAM" if d["tram"] else "BUS", d["line"], "; ".join(
        "%s to %s" % (x["from"], x["to"]) for x in d["dirs"])) for d in line_facts) or "- none"
    prev = json.dumps(previous.get("data"), ensure_ascii=False)[:1500] if previous and previous.get("data") else "none"
    return f"""Now: {now.strftime('%A %d.%m.%Y %H:%M')} Zagreb time. Strike day {core.strike_day(cfg, now)}.
You are the news desk of a personal app that follows the strike of ZET (Zagreb public transport) and Zagrebacki holding workers, which began on 28.09.2026 at 03:30.
Use Google Search to check the very latest developments (court rulings on legality, negotiations, minimum services, which lines run, replacement or private buses, official ZET and City notices), then write in {lang}.

LIVE ZET FEED (official GTFS-realtime, just now):
{_live_lines(live)}

LINES IN THE FEED (from the ZET timetable):
{lf}

APP EVENT LOG (what this app saw, oldest first):
{evs}

HEADLINES COLLECTED FROM CROATIAN NEWS SITES:
{heads}

PREVIOUS SUMMARY (for continuity, may be outdated): {prev}

Return ONLY one JSON object, no markdown, no text before or after it:
{{"headline": "one line, the most important thing right now",
 "status": "strike ongoing | strike suspended | strike ended | unclear",
 "summary": "4 to 6 short plain sentences",
 "service_now": [{{"line": "17", "what": "what this line does today, from where to where"}}],
 "timeline": [{{"when": "YYYY-MM-DD HH:MM", "what": "one sentence", "source": "site name"}}],
 "next": [{{"when": "time or date", "what": "what is expected"}}],
 "advice": "one or two practical sentences for someone who must travel in Zagreb right now"}}
Rules: timeline oldest first, every significant event since the strike began, Zagreb times. Only facts supported by the sources or the live feed; say plainly when something is not confirmed. Short sentences, no jargon."""


def _parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    chunk = t[a:b + 1]
    for attempt in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
        try:
            d = json.loads(attempt)
            return d if isinstance(d, dict) else None
        except Exception:
            continue
    return None


def last_summary():
    s = core.load_json(SUMMARY_FILE, None)
    return s if isinstance(s, dict) else None


def summarize(cfg, live=None, line_facts=None, counter=None):
    """One Gemini call with Google Search grounding. Saves summary.json and logs the headline."""
    if not _ai_lock.acquire(blocking=False):
        return None, {"error": "A summary is already being written, one moment"}
    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": build_prompt(
                cfg, live, line_facts or [], headlines(), core.read_events(60, kinds={"fleet", "lines", "news"}),
                last_summary())}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
        }
        models = cfg.get("models") or core.DEFAULT_CONFIG["models"]
        if isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        resp, meta = generate(payload, models, counter=counter)
        if resp is None:
            core.log_event("system", "AI summary failed: %s" % meta["error"])
            return None, meta
        cand = (resp.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        if not text.strip():
            why = cand.get("finishReason") or (resp.get("promptFeedback") or {}).get("blockReason") or "no text"
            meta["error"] = "Gemini answered but wrote nothing (%s)" % why   # a failure that arrives as 200
            core.log_event("system", "AI summary empty: %s" % why)
            return None, meta
        sources = []
        for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []:
            w = ch.get("web") or {}
            if w.get("uri") and len(sources) < 12:
                sources.append({"title": w.get("title") or w["uri"], "uri": w["uri"]})
        data = _parse_json(text)
        out = {"t": time.time(), "model": meta["model"], "key": meta["key"], "data": data,
               "raw": None if data else text[:6000], "sources": sources,
               "finish": cand.get("finishReason"), "queries": (cand.get("groundingMetadata") or {}).get("webSearchQueries", [])}
        core.save_json(SUMMARY_FILE, out)
        head = (data or {}).get("headline") or text.strip().split("\n")[0][:200]
        core.log_event("ai", head, model=meta["model"], key=meta["key"])
        return out, meta
    finally:
        _ai_lock.release()
