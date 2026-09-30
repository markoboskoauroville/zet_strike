#!/data/data/com.termux/files/usr/bin/python
"""app.py - ZET Strike V10 in Chrome (zet, the server): live map, near me, lines, news desk, event log, settings.

A monitor thread polls the ZET feed every 20 s, writes what changed into the event log, collects headlines
every few minutes and asks Gemini for a fresh summary when something new happened. Pages only read memory.

The server is the Termux app shape of MANTRA_MANIFEST (termux-app.md): waitress on 127.0.0.1 (it holds
keys), a port that never fails to open (portpick.py, 8100 then the next fifteen then any), the three
localguard checks on every /api/ call, the page opened in Chrome only once the port answers, and the
console of console.py (O A U R Q). The page is whole from the first frame even when no vehicle is in
the feed and even when the feed cannot be reached: the map, near me, lines, news and settings all work.
"""
import logging
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import secrets as pysecrets

import core
import localguard
import mapkey
import news
import portpick
import vendor
from flask import Flask, Response, jsonify, request, send_file

logging.getLogger("werkzeug").setLevel(logging.ERROR)
app = Flask(__name__)
app.logger.disabled = True
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024       # key files are notes, not archives

LIVE = {"ready": False, "error": "starting, loading the ZET timetable", "t": 0, "feed_ts": 0, "vehicles": []}
LOCK = threading.Lock()
STATE = {}
JOBS = {"news": False, "ai": False, "last_error": None}
STATIC = {"idx": None, "lines": None}
POLL = int(os.environ.get("ZET_POLL") or 0)     # tests only: a fixed interval overriding feed_interval
WATCH = {"t": 0.0}          # when a page last asked for live data: the server polls fast only while someone looks
WAKE = threading.Event()    # set when a page opens after a quiet spell, so it does not wait for the slow timer
WATCHING_S = int(os.environ.get("ZET_WATCHING_S") or 60)   # tests shorten it
LIVE_PORT = int(os.environ.get("ZET_PORT") or 0) or 8100    # the port actually bound; set in main before serving
TILE_TOKEN = pysecrets.token_urlsafe(12)   # in the Google tile address only the page can read (/api/settings)


# ---------------------------------------------------------------- background work
def run_news(ai, fresh=0):
    if JOBS["news"]:
        return
    JOBS["news"] = True
    JOBS["last_error"] = None
    try:
        news.fetch_headlines(fresh=fresh)
    except Exception as e:
        JOBS["last_error"] = "headlines: %s" % e
    finally:
        JOBS["news"] = False
    if ai:
        run_ai()


def run_ai():
    if JOBS["ai"]:
        return
    JOBS["ai"] = True
    try:
        with LOCK:
            vs = list(LIVE["vehicles"])
        idx, lines = STATIC["idx"], STATIC["lines"]
        facts = [core.describe_line(idx, lines, rid) for rid in sorted({v["route_id"] for v in vs})] if idx else []
        _s, meta = news.summarize(core.load_config(), vs, facts)
        JOBS["last_error"] = meta.get("error") if meta else None
    except Exception as e:
        JOBS["last_error"] = "AI: %s" % e
    finally:
        JOBS["ai"] = False


def something_new_since(t):
    return bool(core.read_events(1, since=t, kinds={"fleet", "lines", "news"}))


def watching():
    return time.time() - WATCH["t"] < WATCHING_S


def looked_at():
    """A page asked for live data. If the copy in memory is older than the fast interval, wake the loop."""
    quiet = not watching()
    WATCH["t"] = time.time()
    cfg = core.load_config()
    if quiet or time.time() - LIVE["t"] > core.feed_interval(cfg, True):
        WAKE.set()


def monitor():
    """The live feed on a clock that follows whether anybody is looking (core.feed_interval): 20 s with
    a page open, 5 min by day and 15 min at night without. Every fetch goes through net.fetch, so a copy
    another zet command fetched a moment ago is used as it is. The timetable is asked about once a day;
    the headlines every news_minutes while watched, hourly when not."""
    last_news = last_ai_try = 0.0
    feed_ok = True
    while True:
        cfg = core.load_config()
        looking = watching()
        interval = POLL or core.feed_interval(cfg, looking)
        try:
            idx = STATIC["idx"]
            if idx is None or time.time() - idx.get("checked", 0) > core.STATIC_CHECK:
                idx = core.ensure_static(progress=False)
                STATIC.update(idx=idx, lines=core.load_lines())
            feed = core.fetch_feed(max_age=max(5, min(interval, core.num(cfg, "poll_seconds", 10, 300)) - 2))
            vs = core.snapshot(idx, STATIC["lines"], feed, STATE, cfg)
            core.record_fleet(vs, feed["ts"])
            core.log_observed(vs)
            with LOCK:
                LIVE.update(ready=True, error=None, t=time.time(), feed_ts=feed["ts"], vehicles=vs)
            if not feed_ok:
                core.log_event("system", "ZET feed is reachable again")
                feed_ok = True
        except Exception as e:
            with LOCK:
                LIVE["error"] = "ZET feed problem: %s" % str(e)[:160]
            if feed_ok:
                core.log_event("system", "ZET feed problem: %s" % str(e)[:160])
                feed_ok = False
        try:
            news_min = core.num(cfg, "news_minutes", 2, 240) if looking else max(60, core.num(cfg, "news_minutes", 2, 240))
            if time.time() - last_news > news_min * 60:
                last_news = time.time()
                threading.Thread(target=run_news, args=(False, news_min * 60 - 30), daemon=True).start()
            ai_min = core.num(cfg, "ai_minutes", 0, 1440)
            s = news.last_summary()
            since = s["t"] if s else 0
            if (ai_min > 0 and time.time() - since > ai_min * 60 and time.time() - last_ai_try > ai_min * 60
                    and news.ring_status()["keys"] and something_new_since(since)):
                last_ai_try = time.time()
                threading.Thread(target=run_ai, daemon=True).start()
        except Exception:
            pass
        WAKE.wait(interval)
        WAKE.clear()


# ---------------------------------------------------------------- helpers
def guard():
    """Changes need a custom header, which a foreign web page cannot send to this server."""
    return request.headers.get("X-ZET") == "1"


def clock(secs):
    return core.clock(secs)


def slim(v):
    leg0 = [p for p in v["passes"] if p["leg"] == 0]
    leg1 = [p for p in v["passes"] if p["leg"] == 1]
    loc = v.get("loc")
    if loc and loc["at"] is not None and loc["at"] > 0:
        leg0 = [p for p in leg0 if p["i"] != loc["at"]]
    return {"key": v["key"], "line": v["line"], "tram": v["tram"], "head": v["head"], "lat": v["lat"], "lon": v["lon"],
            "status": v["status"], "kmh": round(v["kmh"]), "age": v["age"], "parked": v["parked"], "near": v.get("near"),
            "delay": round(v["delay"] / 60) if v.get("delay") else 0, "route_id": v["route_id"],
            "next": [{"name": p["name"], "eta": clock(p["eta"]), "end": p["end"]} for p in leg0[:5]],
            "to_end": clock(leg0[-1]["eta"]) if leg0 else None,
            "back": {"head": leg1[0]["head"], "eta": clock(leg1[0]["eta"])} if leg1 else None}


def snapshot_copy():
    with LOCK:
        return dict(LIVE), list(LIVE["vehicles"])


# ---------------------------------------------------------------- the guard
@app.before_request
def _guard():
    """Host must be loopback, a present Origin/Referer must be this page, and every /api/ call must
    carry the X-ZET header a foreign page cannot set (localguard.py, from KEYRING_TERMUX)."""
    return localguard.check(LIVE_PORT)


@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- pages and API
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           '<rect width="64" height="64" rx="14" fill="#0d0c0a"/>'
           '<rect x="14" y="12" width="36" height="34" rx="8" fill="none" stroke="#ffaf00" stroke-width="5"/>'
           '<path d="M14 30h36" stroke="#ffaf00" stroke-width="5"/>'
           '<circle cx="23" cy="38" r="3" fill="#ffaf00"/><circle cx="41" cy="38" r="3" fill="#ffaf00"/>'
           '<path d="M22 46l-6 8M42 46l6 8" stroke="#ffaf00" stroke-width="5" stroke-linecap="round"/></svg>')


@app.route("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.route("/favicon.svg")
def favicon():
    return Response(FAVICON, mimetype="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.route("/favicon.ico")
def favicon_ico():
    return favicon()


@app.route("/health")
def health():
    return jsonify({"ok": True, "app": "zet", "version": core.VERSION, "port": LIVE_PORT})


@app.route("/vendor/<name>")
def vendor_file(name):
    """Leaflet, fetched once, checked by SHA-256, served from the phone after that (vendor.py)."""
    data = vendor.get(name)
    if data is None:
        return Response(b"", status=404)
    return Response(data, mimetype=vendor.TYPES[name], headers={"Cache-Control": "max-age=604800"})


@app.route("/tile/google/<int:z>/<int:x>/<int:y>")
def tile(z, x, y):
    """Google map tiles through this server, so the key never reaches the page (mapkey.py)."""
    if request.args.get("t") != TILE_TOKEN or not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return Response(b"", status=404)
    code, data, ctype = mapkey.tile(z, x, y)
    if code != 200:
        return Response(data, status=code, mimetype="text/plain")
    return Response(data, mimetype=ctype, headers={"Cache-Control": "private, max-age=86400"})


@app.route("/api/live")
def api_live():
    looked_at()
    live, vs = snapshot_copy()
    cfg = core.load_config()
    now = core.now_zagreb()
    return jsonify({"ready": live["ready"], "error": live["error"], "feed_age": int(time.time() - live["feed_ts"]) if live["feed_ts"] else None,
                    "day": core.strike_day(cfg, now), "now": now.strftime("%H:%M:%S"), "vehicles": [slim(v) for v in vs]})


@app.route("/api/near")
def api_near():
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except Exception:
        return jsonify({"error": "lat and lon are needed"}), 400
    looked_at()
    live, vs = snapshot_copy()
    if not live["ready"]:
        return jsonify({"error": live["error"] or "not ready", "options": []})
    cfg = core.load_config()
    res = core.plan(vs, lat, lon, core.secs_of_day(core.now_zagreb()), cfg)
    for o in res["options"]:
        o["eta_s"], o["leave_s"] = clock(o["eta"]), clock(o["leave"])
        o["then_s"] = [clock(t) for t in o["then"]]
    if res["nearest"]:
        res["nearest"]["eta_s"] = clock(res["nearest"]["eta"])
    res["far_from_zagreb"] = core.hav(lat, lon, 45.813, 15.977) > 40000
    return jsonify(res)


@app.route("/api/place")
def api_place():
    idx = STATIC["idx"]
    q = request.args.get("q", "")
    hit = core.find_place(idx, q) if idx else None
    if not hit:
        return jsonify({"error": "No stop called '%s'" % q[:60]}), 404
    return jsonify({"name": hit[0], "lat": hit[1], "lon": hit[2]})


@app.route("/api/lines")
def api_lines():
    live, vs = snapshot_copy()
    idx, lines = STATIC["idx"], STATIC["lines"]
    if not idx:
        return jsonify({"lines": [], "error": live["error"]})
    by = {}
    for v in vs:
        by.setdefault(v["route_id"], []).append(v)
    out = []
    for rid, group in by.items():
        d = core.describe_line(idx, lines, rid)
        d["vehicles"] = [slim(v) for v in group]
        d["running"] = any(not v["parked"] for v in group)
        out.append(d)
    out.sort(key=lambda d: (not d["running"], not d["tram"], int(d["line"]) if str(d["line"]).isdigit() else 999))
    return jsonify({"lines": out})


@app.route("/api/line/<name>")
def api_line(name):
    idx, lines = STATIC["idx"], STATIC["lines"]
    rid = core.route_by_name(idx, name) if idx else None
    if not rid:
        return jsonify({"error": "No line %s in the ZET timetable" % name[:10]}), 404
    _live, vs = snapshot_copy()
    d = core.describe_line(idx, lines, rid)
    d["vehicles"] = [slim(v) for v in vs if v["route_id"] == rid]
    d["running"] = any(not v["parked"] for v in d["vehicles"])
    return jsonify(d)


@app.route("/api/news")
def api_news():
    store = core.load_json(news.NEWS_FILE, {})
    return jsonify({"summary": news.last_summary(), "headlines": news.headlines(60),
                    "has_keys": bool(news.ring_status()["keys"]), "busy": {"news": JOBS["news"], "ai": JOBS["ai"]},
                    "error": JOBS["last_error"], "fetched": store.get("fetched") if isinstance(store, dict) else None,
                    "sources": store.get("report") if isinstance(store, dict) else None})


@app.route("/api/news/refresh", methods=["POST"])
def api_news_refresh():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    ai = bool((request.get_json(silent=True) or {}).get("ai"))
    threading.Thread(target=run_news, args=(ai, 0), daemon=True).start()     # asked by hand: ask the sites now
    return jsonify({"started": True, "ai": ai})


@app.route("/api/log")
def api_log():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
        since = float(request.args.get("since", 0))
    except ValueError:
        limit, since = 200, 0.0
    kinds = set(request.args.get("kinds", "").split(",")) - {""} or None
    evs = core.read_events(limit, since=since, kinds=kinds)
    for e in evs:
        dt = core.from_epoch(e["t"])
        e["hm"], e["date"] = dt.strftime("%H:%M"), dt.strftime("%d.%m.%Y")
        if e.get("when"):
            e["pub"] = core.from_epoch(e["when"]).strftime("%d.%m %H:%M")
    return jsonify({"events": evs})


SETTABLE = {"walk_kmh": (2, 8), "max_walk_m": (200, 5000), "buffer_min": (0, 15), "layover_min": (0, 30),
            "news_minutes": (2, 240), "ai_minutes": (0, 1440),
            "poll_seconds": (10, 300), "idle_minutes": (1, 60), "night_minutes": (1, 120)}


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    cfg = core.load_config()
    if request.method == "POST":
        if not guard():
            return jsonify({"error": "forbidden"}), 403
        data = request.get_json(silent=True) or {}
        for k, (lo, hi) in SETTABLE.items():
            if k in data:
                try:
                    cfg[k] = max(lo, min(hi, float(data[k])))
                except (TypeError, ValueError):
                    pass
        if data.get("language") in ("en", "hr"):
            cfg["language"] = data["language"]
        if "models" in data:
            ms = data["models"] if isinstance(data["models"], list) else str(data["models"]).split(",")
            ms = [m.strip() for m in ms if m and m.strip() and len(m.strip()) < 60][:8]
            if ms:
                cfg["models"] = ms
        if data.get("map") in ("osm", "google", "own"):
            cfg["map"] = data["map"]
        if "tiles" in data:
            t = str(data["tiles"] or "").strip()
            if not t:
                cfg["tiles"] = ""
            elif re.match(r"^https?://\S+$", t) and all(p in t for p in ("{z}", "{x}", "{y}")) and len(t) < 300:
                cfg["tiles"] = t
            else:
                return jsonify({"error": "The tiles address must start with http and contain {z}, {x} and {y}"}), 400
        core.save_config(cfg)
    out = {k: cfg.get(k) for k in list(SETTABLE) + ["language", "models", "tiles"]}
    out["map"] = cfg.get("map") or ("own" if cfg.get("tiles") else "osm")
    out["ring"] = news.ring_status()
    out["google"] = mapkey.status()
    out["google_tiles"] = "/tile/google/{z}/{x}/{y}?t=" + TILE_TOKEN
    out["version"] = core.VERSION
    out["port"] = LIVE_PORT
    return jsonify(out)


@app.route("/api/keys", methods=["POST"])
def api_keys_add():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    return jsonify(news.add_keys((request.get_json(silent=True) or {}).get("text", "")))


@app.route("/api/keys/import", methods=["POST"])
def api_keys_import():
    """The file picker (multipart, any number of files) or text: every key found by the Keyring parser,
    with its title, sorted into Gemini and Google (labels.import_text)."""
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    import labels
    results = []
    if request.files:
        for f in request.files.getlist("file"):
            raw = f.read(app.config["MAX_CONTENT_LENGTH"])
            name = os.path.basename(f.filename or "picked file")[:80]
            if b"\x00" in raw[:4096]:
                results.append({"source": name, "found": 0, "gemini": 0, "google": 0, "duplicates": 0, "titled": 0,
                                "other": {}, "error": "not a text file", "say": name + ": not a text file, nothing read."})
                continue
            r = labels.import_text(raw.decode("utf-8", "replace"), name)
            r["say"] = labels.summary(r)
            results.append(r)
    else:
        text = str((request.get_json(silent=True) or {}).get("text") or "")
        if not text.strip():
            return jsonify({"error": "Nothing to import."}), 400
        r = labels.import_text(text, "pasted")
        r["say"] = labels.summary(r)
        results.append(r)
    for r in results:
        r.pop("gemini_fps", None)
        r.pop("google_fps", None)
    return jsonify({"results": results, "ring": news.ring_status(), "google": mapkey.status()})


@app.route("/api/keys/title", methods=["POST"])
def api_keys_title():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    import labels
    d = request.get_json(silent=True) or {}
    fp = str(d.get("fp") or "")
    known = {k["fp"] for k in news.ring_status()["keys"]} | {k["fp"] for k in mapkey.status()["keys"]}
    if fp not in known:
        return jsonify({"error": "No key with fingerprint %s." % fp[:12]}), 404
    return jsonify({"fp": fp, "title": labels.set(fp, d.get("title", ""))})


@app.route("/api/keys/test", methods=["POST"])
def api_keys_test():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"results": news.test_keys(), "ring": news.ring_status()})


@app.route("/api/keys/delete", methods=["POST"])
def api_keys_delete():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"deleted": news.remove_key(str((request.get_json(silent=True) or {}).get("fp", "")))})


@app.route("/api/traffic")
def api_traffic():
    """What came over the network today, per source: bytes, downloads, 'not changed' answers, copies used."""
    import net
    t = net.today()
    total = sum(v.get("bytes", 0) for v in t.values())
    cfg = core.load_config()
    return jsonify({"today": t, "total": total, "total_s": net.human(total),
                    "sources": {k: dict(v, bytes_s=net.human(v.get("bytes", 0))) for k, v in t.items()},
                    "watching": watching(), "interval": core.feed_interval(cfg, watching())})


@app.route("/api/google")
def api_google():
    return jsonify(mapkey.status())


@app.route("/api/google/save", methods=["POST"])
def api_google_save():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    r = mapkey.save(str((request.get_json(silent=True) or {}).get("text", "")))
    return jsonify(r), (400 if r.get("error") else 200)


@app.route("/api/google/test", methods=["POST"])
def api_google_test():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    fp = (request.get_json(silent=True) or {}).get("fp")
    r = mapkey.test(str(fp) if fp else None)
    return jsonify(r), (400 if r.get("error") else 200)


@app.route("/api/google/select", methods=["POST"])
def api_google_select():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    ok = mapkey.select(str((request.get_json(silent=True) or {}).get("fp") or ""))
    return jsonify({"selected": ok, "google": mapkey.status()}), (200 if ok else 404)


@app.route("/api/google/delete", methods=["POST"])
def api_google_delete():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    fp = (request.get_json(silent=True) or {}).get("fp")
    return jsonify({"deleted": mapkey.delete(str(fp) if fp else None)})


@app.route("/stream")
def stream():
    """V4/V5 compatible raw list."""
    _live, vs = snapshot_copy()
    return jsonify([slim(v) for v in vs])


# ---------------------------------------------------------------- start
def main():
    """Pick the port, announce it, serve through the console. U and R restart this same process on the
    same port (os.execv keeps the pid), so the page that is open keeps answering."""
    global LIVE_PORT
    import console
    import update
    cfg = core.load_config()
    wanted = int(os.environ.get("ZET_PORT") or cfg.get("port") or 8100)
    port, note = portpick.pick("127.0.0.1", wanted)
    LIVE_PORT = port
    portpick.announce("zet", port)
    core.log_event("system", "server started on port %d" % port)
    threading.Thread(target=monitor, daemon=True).start()
    library = core.APP_DIR.replace(os.path.expanduser("~"), "~", 1)
    try:
        action = console.run(app, "127.0.0.1", port, core.VERSION, library, note=note,
                             on_check=update.check, on_update=update.apply)
    except KeyboardInterrupt:
        action = "quit"
        print("\n  stopped.")
    if action == "restart":
        portpick.forget("zet", port)
        os.environ["ZET_PORT"] = str(port)
        os.environ["ZET_NO_BROWSER"] = "1"          # the page is already open at this port
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, os.path.join(HERE, "app.py")] + sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
