#!/data/data/com.termux/files/usr/bin/python
"""app.py - ZET Strike V6 in Chrome (zets): live map, near me, lines, news desk, event log, settings.

A monitor thread polls the ZET feed every 20 s, writes what changed into the event log, collects headlines
every few minutes and asks Gemini for a fresh summary when something new happened. Pages only read memory.
"""
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core
import news
from flask import Flask, jsonify, request, send_file

logging.getLogger("werkzeug").setLevel(logging.ERROR)
app = Flask(__name__)
app.logger.disabled = True

LIVE = {"ready": False, "error": "starting, loading the ZET timetable", "t": 0, "feed_ts": 0, "vehicles": []}
LOCK = threading.Lock()
STATE = {}
JOBS = {"news": False, "ai": False, "last_error": None}
STATIC = {"idx": None, "lines": None}
POLL = int(os.environ.get("ZET_POLL", "20"))


# ---------------------------------------------------------------- background work
def run_news(ai):
    if JOBS["news"]:
        return
    JOBS["news"] = True
    JOBS["last_error"] = None
    try:
        news.fetch_headlines()
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


def monitor():
    last_news = last_ai_try = 0.0
    feed_ok = True
    while True:
        try:
            cfg = core.load_config()
            idx = STATIC["idx"]
            if idx is None or time.time() - idx.get("checked", 0) > 6 * 3600:
                idx = core.ensure_static(progress=False)
                STATIC.update(idx=idx, lines=core.load_lines())
            feed = core.fetch_feed()
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
            cfg = core.load_config()
            if time.time() - last_news > core.num(cfg, "news_minutes", 2, 240) * 60:
                last_news = time.time()
                threading.Thread(target=run_news, args=(False,), daemon=True).start()
            ai_min = core.num(cfg, "ai_minutes", 0, 1440)
            s = news.last_summary()
            since = s["t"] if s else 0
            if (ai_min > 0 and time.time() - since > ai_min * 60 and time.time() - last_ai_try > ai_min * 60
                    and news.ring_status()["keys"] and something_new_since(since)):
                last_ai_try = time.time()
                threading.Thread(target=run_ai, daemon=True).start()
        except Exception:
            pass
        time.sleep(POLL)


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


# ---------------------------------------------------------------- pages and API
@app.route("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.route("/api/live")
def api_live():
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
    threading.Thread(target=run_news, args=(ai,), daemon=True).start()
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
            "news_minutes": (2, 240), "ai_minutes": (0, 1440)}


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
    out["ring"] = news.ring_status()
    return jsonify(out)


@app.route("/api/keys", methods=["POST"])
def api_keys_add():
    if not guard():
        return jsonify({"error": "forbidden"}), 403
    return jsonify(news.add_keys((request.get_json(silent=True) or {}).get("text", "")))


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


@app.route("/stream")
def stream():
    """V4/V5 compatible raw list."""
    _live, vs = snapshot_copy()
    return jsonify([slim(v) for v in vs])


# ---------------------------------------------------------------- start
def find_available_port(start_port=8080):
    port = start_port
    while port < start_port + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    raise RuntimeError("no free port between %d and %d" % (start_port, port))


def open_in_chrome(url):
    """Rule 45: verify Chrome exists, launch it, fall back to termux-open-url, else print the address."""
    if os.environ.get("ZET_NO_BROWSER"):
        return
    have = subprocess.run("pm list packages 2>/dev/null | grep -q com.android.chrome", shell=True).returncode == 0
    if have:
        print("\033[38;5;220mLaunching Google Chrome at %s\033[0m" % url)
        cmd = 'am start -n com.android.chrome/com.google.android.apps.chrome.Main -a android.intent.action.VIEW -d "%s"' % url
        if subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
    if subprocess.run("command -v termux-open-url >/dev/null 2>&1", shell=True).returncode == 0:
        subprocess.run(["termux-open-url", url])
        return
    print("\033[1;31m[!] Could not open Chrome automatically.\033[0m")
    print("Open this address by hand: %s\n" % url)


if __name__ == "__main__":
    cfg = core.load_config()
    port = find_available_port(int(os.environ.get("ZET_PORT") or cfg.get("port", 8080)))
    url = "http://127.0.0.1:%d" % port
    print("\033[38;5;220mZET Strike V6 on %s  (Ctrl-C stops it)\033[0m" % url)
    core.log_event("system", "zets started on port %d" % port)
    threading.Thread(target=monitor, daemon=True).start()
    open_in_chrome(url)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
