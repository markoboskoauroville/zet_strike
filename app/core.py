#!/data/data/com.termux/files/usr/bin/python
"""core.py - ZET Strike V6 engine: live feed, timetable, line catalog, trip planner, event log."""
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

try:
    import fcntl
except ImportError:  # not on Android, but keeps tests portable
    fcntl = None

APP_DIR = os.environ.get("ZET_STRIKE_DIR") or os.path.join(os.path.expanduser("~"), ".zet-strike")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
STATIC_ZIP = os.path.join(APP_DIR, "static.zip")
INDEX_FILE = os.path.join(APP_DIR, "index.json")
LINES_FILE = os.path.join(APP_DIR, "lines.json")
TRIPS_FILE = os.path.join(APP_DIR, "trips.json")
CHAIN_FILE = os.path.join(APP_DIR, "chain.txt")
EVENTS_FILE = os.path.join(APP_DIR, "events.jsonl")
FLEET_FILE = os.path.join(APP_DIR, "fleet.json")
FEED_URL = os.environ.get("ZET_FEED_URL", "https://www.zet.hr/gtfs-rt-protobuf")
STATIC_URL = os.environ.get("ZET_STATIC_URL", "https://www.zet.hr/gtfs-scheduled/latest")
UA = "Mozilla/5.0 (Linux; Android 14) zet-strike/6"
VERSION = 6
SCHEMA = 6          # bump when index.json / lines.json layout changes
ROUTE_TRAM = 0
PARK_M = 400        # further than this from its own route = parked, not in service
GONE_SECS = 300     # missing from the feed this long = left the feed
DEFAULT_CONFIG = {
    "port": 8080,
    "strike_start": "2026-09-28",
    "walk_kmh": 4.5,
    "max_walk_m": 1500,
    "buffer_min": 2,
    "layover_min": 4,
    "news_minutes": 10,
    "ai_minutes": 30,
    "language": "en",
    "tiles": "",
    "models": ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"],
}

_io_lock = threading.RLock()


# ---------------------------------------------------------------- files
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.part" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


class FileLock:
    """Cross-process lock (flock) plus an in-process lock."""
    def __init__(self, name):
        self.path = os.path.join(APP_DIR, name + ".lock")
        self.fh = None

    def __enter__(self):
        _io_lock.acquire()
        os.makedirs(APP_DIR, exist_ok=True)
        self.fh = open(self.path, "a")
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        try:
            if fcntl:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        finally:
            _io_lock.release()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    saved = load_json(CONFIG_FILE, {})
    if isinstance(saved, dict):
        cfg.update(saved)
    if not os.path.exists(CONFIG_FILE):
        save_config(cfg)
    return cfg


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def num(cfg, key, lo, hi):
    try:
        return max(lo, min(hi, float(cfg.get(key, DEFAULT_CONFIG[key]))))
    except Exception:
        return float(DEFAULT_CONFIG[key])


# ---------------------------------------------------------------- clock (Zagreb time)
def _last_sunday_utc(year, month):
    d = datetime(year, month, 31, 1, tzinfo=timezone.utc)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def to_zagreb(dt_utc):
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("Europe/Zagreb"))
    except Exception:
        y = dt_utc.year
        summer = _last_sunday_utc(y, 3) <= dt_utc < _last_sunday_utc(y, 10)
        return dt_utc.astimezone(timezone(timedelta(hours=2 if summer else 1)))


def now_zagreb():
    return to_zagreb(datetime.now(timezone.utc))


def from_epoch(ts):
    return to_zagreb(datetime.fromtimestamp(ts, timezone.utc))


def secs_of_day(dt):
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def hms_to_secs(text):
    try:
        h, m, s = text.strip().strip('"').split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


def clock(secs):
    secs = int(round(secs)) % 86400
    return "%02d:%02d" % (secs // 3600, (secs % 3600) // 60)


def strike_day(cfg, dt):
    try:
        y, m, d = [int(x) for x in str(cfg.get("strike_start", "2026-09-28")).split("-")]
        return (dt.date() - datetime(y, m, d).date()).days + 1
    except Exception:
        return 0


# ---------------------------------------------------------------- geometry
def hav(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _seg(ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(ax, ay), 0.0
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy), t


def locate(stops, lat, lon):
    """stops: [(name, lat, lon, secs, sid)] in trip order. Where on its trip is this position?"""
    if not stops:
        return None
    kx, ky = 111320.0 * math.cos(math.radians(lat)), 110540.0
    pts = [((s[2] - lon) * kx, (s[1] - lat) * ky) for s in stops]
    near = min(range(len(pts)), key=lambda i: math.hypot(*pts[i]))
    near_d = math.hypot(*pts[near])
    best = (near_d, max(0, min(near, len(pts) - 2)), 0.0)
    for k in range(len(pts) - 1):
        d, t = _seg(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1])
        if d < best[0] - 1e-6:
            best = (d, k, t)
    off, k, t = best
    at = near if near_d <= 60 else None
    if at is not None:
        sched = stops[at][3]
    elif len(stops) > 1:
        sched = stops[k][3] + t * (stops[k + 1][3] - stops[k][3])
    else:
        sched = stops[0][3]
    return {"at": at, "seg": k, "t": t, "off": off, "near": near, "near_d": near_d, "sched": sched}


def norm(text):
    text = (text or "").lower().replace("đ", "d").replace("ð", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


# ---------------------------------------------------------------- live feed
def fetch_feed(timeout=15):
    from google.transit import gtfs_realtime_pb2
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    vehicles, updates, seen = [], {}, set()
    for e in feed.entity:
        if e.HasField("trip_update"):
            tu = e.trip_update
            for s in tu.stop_time_update:
                d = s.arrival.delay or s.departure.delay
                if s.stop_sequence > 1 and d:
                    updates[tu.trip.trip_id] = {"seq": s.stop_sequence, "delay": d}
                    break
        if e.HasField("vehicle"):
            v = e.vehicle
            if not v.HasField("position"):
                continue
            lat, lon = v.position.latitude, v.position.longitude
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (abs(lat) < 1 and abs(lon) < 1):
                continue
            key = v.vehicle.id or e.id
            if key in seen:
                continue
            seen.add(key)
            vehicles.append({
                "key": key,
                "trip_id": v.trip.trip_id,
                "route_id": v.trip.route_id,
                "lat": lat,
                "lon": lon,
                "ts": v.timestamp or feed.header.timestamp,
            })
    ts = feed.header.timestamp or int(time.time())
    return {"ts": ts, "vehicles": vehicles, "updates": updates, "bytes": len(data)}


# ---------------------------------------------------------------- static timetable
_cache = {}


def _cached(path, default):
    """JSON file cached in memory until it changes on disk."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return default
    hit = _cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    data = load_json(path, default)
    _cache[path] = (sig, data)
    return data


def _download(dest, progress=True):
    tmp = dest + ".part"
    req = urllib.request.Request(STATIC_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = shown = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress and got - shown >= 1 << 20:
                shown = got
                sys.stderr.write("\r  timetable download %5.1f / %5.1f MB" % (got / 1e6, total / 1e6))
                sys.stderr.flush()
    if progress:
        sys.stderr.write("\n")
    with zipfile.ZipFile(tmp) as z:
        if "stop_times.txt" not in z.namelist():
            raise ValueError("timetable zip has no stop_times.txt")
    os.replace(tmp, dest)


def _text(z, name):
    return io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline="")


def _build_index(zpath):
    with zipfile.ZipFile(zpath) as z:
        routes = {}
        for r in csv.DictReader(_text(z, "routes.txt")):
            try:
                rtype = int(r.get("route_type") or 3)
            except ValueError:
                rtype = 3
            routes[r["route_id"]] = [r.get("route_short_name") or r["route_id"], r.get("route_long_name") or "", rtype]
        stops = {}
        for r in csv.DictReader(_text(z, "stops.txt")):
            try:
                lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
            except Exception:
                continue
            # ZET's own file has a few stops with coordinates in Russia and the Arctic: drop anything outside Zagreb county
            if r.get("location_type") in ("1", "2") or not (45.3 < lat < 46.3 and 15.3 < lon < 16.8):
                continue
            stops[r["stop_id"]] = [r["stop_name"], lat, lon]
        version = "?"
        try:
            for r in csv.DictReader(_text(z, "feed_info.txt")):
                version = r.get("feed_version", "?")
        except Exception:
            version = hashlib.sha1(open(zpath, "rb").read(1 << 20)).hexdigest()[:8]
    return {"version": version, "routes": routes, "stops": stops, "checked": time.time(), "schema": SCHEMA}


def _build_lines(zpath, version, progress=True):
    """Line catalog: for every route its stop patterns (from, to, stops, duration) and daily span per service."""
    t0 = time.time()
    with zipfile.ZipFile(zpath) as z:
        trips, pcount, prep = {}, {}, {}
        for r in csv.DictReader(_text(z, "trips.txt")):
            pk = (r["route_id"], r.get("shape_id") or "", r.get("trip_headsign") or "")
            trips[r["trip_id"]] = (r["route_id"], r["service_id"], r.get("trip_headsign") or "", pk)
            pcount[pk] = pcount.get(pk, 0) + 1
            prep.setdefault(pk, r["trip_id"])
        want = {v.encode() for v in prep.values()}
        span, rows = {}, {}
        with z.open("stop_times.txt") as f:
            f.readline()
            n = 0
            for line in f:
                n += 1
                if progress and n % 200000 == 0:
                    sys.stderr.write("\r  building line catalog %4.1f M rows" % (n / 1e6))
                    sys.stderr.flush()
                p = line.split(b",", 5)
                if len(p) < 5:
                    continue
                tid = p[0].strip(b'"')
                try:
                    seq = int(p[4])
                except ValueError:
                    continue
                if tid in want:
                    rows.setdefault(tid.decode(), []).append((seq, p[3].strip(b'"').decode(), hms_to_secs((p[2] or p[1]).decode())))
                if seq <= 1:
                    secs = hms_to_secs((p[2] or p[1]).decode())
                    if secs is not None:
                        span[tid.decode()] = secs
        cal = [dict(r) for r in csv.DictReader(_text(z, "calendar.txt"))] if "calendar.txt" in z.namelist() else []
        caldates = [dict(r) for r in csv.DictReader(_text(z, "calendar_dates.txt"))] if "calendar_dates.txt" in z.namelist() else []
    if progress:
        sys.stderr.write("\r" + " " * 44 + "\r")
    routes = {}
    for pk, tid in prep.items():
        rid, shape, head = pk
        st = sorted(x for x in rows.get(tid, []) if x[2] is not None)
        if len(st) < 2:
            continue
        base = st[0][2]
        routes.setdefault(rid, {"patterns": [], "span": {}})["patterns"].append({
            "id": shape, "head": head, "n": pcount[pk],
            "stops": [[sid, secs - base] for _seq, sid, secs in st]})
    for tid, (rid, svc, head, pk) in trips.items():
        secs = span.get(tid)
        if secs is None or rid not in routes:
            continue
        s = routes[rid]["span"].setdefault(svc, {}).setdefault(head, [secs, secs, 0])
        s[0] = min(s[0], secs)
        s[1] = max(s[1], secs)
        s[2] += 1
    for rid, r in routes.items():
        r["patterns"].sort(key=lambda p: -p["n"])
        keep = [p for p in r["patterns"] if p["n"] >= 3] or r["patterns"][:1]
        r["patterns"] = keep[:8]
    return {"version": version, "schema": SCHEMA, "routes": routes, "calendar": cal, "calendar_dates": caldates,
            "built": time.time(), "secs": round(time.time() - t0, 1)}


def load_index():
    idx = _cached(INDEX_FILE, None)
    return idx if isinstance(idx, dict) and "routes" in idx and "stops" in idx else None


def load_lines():
    ln = _cached(LINES_FILE, None)
    return ln if isinstance(ln, dict) and "routes" in ln else None


def ensure_static(force=False, max_age=6 * 3600, progress=True):
    """Timetable index + line catalog. Refresh from ZET when stale; keep the saved copy when offline."""
    with FileLock("static"):
        idx = load_index()
        lines = load_lines()
        have_zip = os.path.exists(STATIC_ZIP)
        if idx and have_zip and idx.get("schema") != SCHEMA:
            checked = idx.get("checked", 0)
            idx = _build_index(STATIC_ZIP)
            idx["checked"] = checked
            save_json(LINES_FILE, _build_lines(STATIC_ZIP, idx["version"], progress))
            save_json(INDEX_FILE, idx)
            if os.path.exists(TRIPS_FILE):
                os.remove(TRIPS_FILE)
            lines = load_lines()
        lines_ok = bool(idx and lines and lines.get("version") == idx.get("version") and lines.get("schema") == SCHEMA)
        fresh = bool(idx and time.time() - idx.get("checked", 0) < max_age)
        if idx and have_zip and fresh and not force:
            if not lines_ok:
                save_json(LINES_FILE, _build_lines(STATIC_ZIP, idx["version"], progress))
            return idx
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            _download(STATIC_ZIP, progress)
            new = _build_index(STATIC_ZIP)
            if idx and idx.get("version") == new["version"] and lines_ok:
                idx["checked"] = time.time()
                save_json(INDEX_FILE, idx)
                return idx
            save_json(LINES_FILE, _build_lines(STATIC_ZIP, new["version"], progress))
            save_json(INDEX_FILE, new)
            if os.path.exists(TRIPS_FILE):
                os.remove(TRIPS_FILE)
            log_event("system", "Timetable version %s loaded (%d lines, %d stops)" % (
                new["version"], len(new["routes"]), len(new["stops"])))
            return new
        except Exception as e:
            if idx and have_zip:
                if progress:
                    sys.stderr.write("  timetable refresh failed (%s), using the saved copy\n" % e)
                if not lines_ok:
                    save_json(LINES_FILE, _build_lines(STATIC_ZIP, idx["version"], progress))
                return idx
            raise


def today_services(lines, day=None):
    day = day or now_zagreb().date()
    ymd = day.strftime("%Y%m%d")
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][day.weekday()]
    out = set()
    for c in (lines or {}).get("calendar", []):
        if c.get(wd) == "1" and c.get("start_date", "0") <= ymd <= c.get("end_date", "99999999"):
            out.add(c["service_id"])
    for c in (lines or {}).get("calendar_dates", []):
        if c.get("date") == ymd:
            if c.get("exception_type") == "1":
                out.add(c["service_id"])
            elif c.get("exception_type") == "2":
                out.discard(c["service_id"])
    return out


_trip_lock = threading.Lock()


def resolve_trips(idx, trip_ids):
    """Timetable of the given trip ids, cached on disk. {trip_id: {route, head, st: [[stop_id, seq, secs]]}}"""
    with _trip_lock:
        cache = load_json(TRIPS_FILE, {})
        if not isinstance(cache, dict) or cache.get("version") != idx.get("version"):
            cache = {"version": idx.get("version"), "trips": {}}
        known = cache["trips"]
        missing = sorted({t for t in trip_ids if t and t not in known})
        if missing and os.path.exists(STATIC_ZIP):
            want = {t.encode() for t in missing}
            found = {}
            with zipfile.ZipFile(STATIC_ZIP) as z:
                for r in csv.DictReader(_text(z, "trips.txt")):
                    if r["trip_id"] in want or r["trip_id"].encode() in want:
                        found[r["trip_id"]] = {"route": r["route_id"], "head": r.get("trip_headsign") or "",
                                               "shape": r.get("shape_id") or "", "st": []}
                with z.open("stop_times.txt") as f:
                    f.readline()
                    for line in f:
                        p = line.split(b",", 5)
                        if p[0].strip(b'"') in want and len(p) >= 5:
                            tid = p[0].strip(b'"').decode()
                            secs = hms_to_secs((p[2] or p[1]).decode())
                            if tid in found and secs is not None:
                                found[tid]["st"].append([p[3].strip(b'"').decode(), int(p[4]), secs])
            for t in missing:
                entry = found.get(t, {"route": "", "head": "", "shape": "", "st": []})
                entry["st"].sort(key=lambda x: x[1])
                known[t] = entry
            if len(known) > 3000:
                for k in list(known)[:len(known) - 2000]:
                    del known[k]
            save_json(TRIPS_FILE, cache)
        return {t: known.get(t) for t in trip_ids}


def trip_stops(idx, trip):
    out = []
    for sid, _seq, secs in (trip or {}).get("st", []):
        s = idx["stops"].get(sid)
        if s:
            out.append((s[0], s[1], s[2], secs, sid))
    return out


def pattern_stops(idx, pattern):
    out = []
    for sid, rel in pattern.get("stops", []):
        s = idx["stops"].get(sid)
        if s:
            out.append((s[0], s[1], s[2], rel, sid))
    return out


def route_info(idx, route_id):
    """(short_name, long_name, is_tram)"""
    r = (idx or {}).get("routes", {}).get(str(route_id))
    if not r:
        return (str(route_id) or "?", "", False)
    return (r[0], r[1], r[2] == ROUTE_TRAM)


def route_by_name(idx, name):
    name = str(name).strip()
    for rid, r in (idx or {}).get("routes", {}).items():
        if r[0] == name or rid == name:
            return rid
    return None


def nearest_stop(idx, lat, lon):
    best, name = 1e18, "?"
    for s in idx["stops"].values():
        d = (s[1] - lat) ** 2 + ((s[2] - lon) * 0.7) ** 2
        if d < best:
            best, name = d, s[0]
    return name


def find_place(idx, text):
    """Stop name search, accent-insensitive. Returns (name, lat, lon) of the best match, or None."""
    q = norm(text)
    if not q:
        return None
    groups = {}
    for s in idx["stops"].values():
        groups.setdefault(s[0], []).append(s)
    ranked = []
    for name, ss in groups.items():
        n = norm(name)
        if n == q:
            score = 0
        elif n.startswith(q):
            score = 1
        elif q in n:
            score = 2
        elif all(w in n for w in q.split()):
            score = 3
        else:
            continue
        ranked.append((score, len(n), name, ss))
    if not ranked:
        return None
    ranked.sort()
    _s, _l, name, ss = ranked[0]
    lats = sorted(s[1] for s in ss)
    lons = sorted(s[2] for s in ss)
    return (name, lats[len(lats) // 2], lons[len(lons) // 2])


def describe_line(idx, lines, rid, services=None):
    """Plain facts about one line from the ZET timetable: name, each direction's from/to, stops, duration, today's span."""
    short, longname, is_tram = route_info(idx, rid)
    r = (lines or {}).get("routes", {}).get(rid, {})
    services = services if services is not None else today_services(lines)
    out = {"line": short, "route_id": rid, "tram": is_tram, "long": longname, "dirs": []}
    per_head = {}
    for p in r.get("patterns", []):
        if p["head"] not in per_head or p["n"] > per_head[p["head"]]["n"]:
            per_head[p["head"]] = p
    top = max([p["n"] for p in per_head.values()] or [0])
    for p in sorted(per_head.values(), key=lambda p: -p["n"]):
        st = pattern_stops(idx, p)
        if len(st) < 2 or p["n"] < 0.15 * top:
            continue
        first = last = None
        n = 0
        for svc, heads in r.get("span", {}).items():
            if svc in services and p["head"] in heads:
                a, b, c = heads[p["head"]]
                first = a if first is None else min(first, a)
                last = b if last is None else max(last, b)
                n += c
        out["dirs"].append({"head": p["head"], "from": st[0][0], "to": st[-1][0], "stops": len(st),
                            "mins": round(st[-1][3] / 60), "first": clock(first) if first is not None else None,
                            "last": clock(last) if last is not None else None, "today": n, "share": p["n"],
                            "circular": hav(st[0][1], st[0][2], st[-1][1], st[-1][2]) < 300,
                            "names": [s[0] for s in st], "id": p["id"]})
    out["dirs"] = out["dirs"][:3]
    return out


# ---------------------------------------------------------------- motion + projection
def update_motion(state, vehicles):
    """MOVING when it covered 20 m+ between two reports; STANDING when not; NEW until a second report."""
    for v in vehicles:
        st = state.get(v["key"])
        if st is None:
            state[v["key"]] = {"lat": v["lat"], "lon": v["lon"], "ts": v["ts"], "status": "NEW", "kmh": 0.0}
        elif v["ts"] > st["ts"]:
            dist = hav(st["lat"], st["lon"], v["lat"], v["lon"])
            dt = v["ts"] - st["ts"]
            kmh = dist / dt * 3.6
            st.update({"lat": v["lat"], "lon": v["lon"], "ts": v["ts"], "kmh": kmh,
                       "status": "MOVING" if (dist >= 20 and kmh >= 3) else "STANDING"})
        v["status"] = state[v["key"]]["status"]
        v["kmh"] = state[v["key"]]["kmh"]
    return vehicles


def return_pattern(idx, lines, rid, end_lat, end_lon):
    best = None
    for p in (lines or {}).get("routes", {}).get(rid, {}).get("patterns", []):
        if not p["stops"]:
            continue
        s = idx["stops"].get(p["stops"][0][0])
        if s and hav(s[1], s[2], end_lat, end_lon) <= 450:
            if best is None or p["n"] > best["n"]:
                best = p
    return best


def project(idx, lines, v, stops, loc, now_secs, cfg, horizon=5400):
    """Where this vehicle will be: rest of its trip, then one return leg from the same terminal."""
    basis = {"MOVING": "live", "STANDING": "standing"}.get(v.get("status"), "position")
    here = loc["sched"]
    first = loc["at"] if loc["at"] is not None else loc["seg"] + 1
    passes = []
    for i in range(first, len(stops)):
        eta = now_secs + max(0.0, stops[i][3] - here)
        passes.append({"name": stops[i][0], "lat": stops[i][1], "lon": stops[i][2], "eta": eta,
                       "head": v["head"], "leg": 0, "i": i, "basis": basis, "end": i == len(stops) - 1})
    end_eta = passes[-1]["eta"] if passes else now_secs
    pat = return_pattern(idx, lines, v["route_id"], stops[-1][1], stops[-1][2])
    if pat:
        pst = pattern_stops(idx, pat)
        start = end_eta + num(cfg, "layover_min", 0, 30) * 60
        for j, s in enumerate(pst):
            eta = start + s[3]
            if eta - now_secs > horizon:
                break
            passes.append({"name": s[0], "lat": s[1], "lon": s[2], "eta": eta, "head": pat["head"],
                           "leg": 1, "i": j, "basis": "return", "end": j == len(pst) - 1})
    return passes


def snapshot(idx, lines, feed, state, cfg, now=None):
    """Enrich every vehicle in the feed: line, direction, where it is, parked or not, and its projected passes."""
    now = now or now_zagreb()
    now_secs = secs_of_day(now)
    vehicles = update_motion(state, feed["vehicles"])
    trips = resolve_trips(idx, [v["trip_id"] for v in vehicles]) if idx else {}
    out = []
    for v in vehicles:
        short, longname, is_tram = route_info(idx, v["route_id"]) if idx else (v["route_id"], "", False)
        trip = trips.get(v["trip_id"]) or {}
        v.update({"line": short, "long": longname, "tram": is_tram, "head": trip.get("head") or "?",
                  "age": max(int(feed["ts"] - v["ts"]), 0), "parked": False, "passes": [], "loc": None})
        stops = trip_stops(idx, trip) if idx else []
        v["stops"] = stops
        if stops:
            loc = locate(stops, v["lat"], v["lon"])
            v["loc"] = loc
            if loc["off"] > PARK_M:
                v["parked"] = True
            else:
                v["passes"] = project(idx, lines, v, stops, loc, now_secs, cfg)
        if idx:
            if v["loc"] and not v["parked"]:
                v["near"] = stops[v["loc"]["near"]][0]
            else:
                v["near"] = nearest_stop(idx, v["lat"], v["lon"])
        else:
            v["near"] = "?"
        d = feed["updates"].get(v["trip_id"])
        v["delay"] = d["delay"] if d and 0 < abs(d["delay"]) <= 1200 else 0
        out.append(v)
    order = {"MOVING": 0, "STANDING": 1, "NEW": 2}
    out.sort(key=lambda v: (v["parked"], not v["tram"], order.get(v["status"], 3),
                            int(v["line"]) if str(v["line"]).isdigit() else 999))
    return out


# ---------------------------------------------------------------- trip planner
def plan(vehicles, lat, lon, now_secs, cfg):
    """Which running vehicle can you catch, at which stop, when, and when to leave."""
    speed = num(cfg, "walk_kmh", 2, 8) * 1000 / 60.0
    max_walk = num(cfg, "max_walk_m", 200, 5000)
    buffer = num(cfg, "buffer_min", 0, 15) * 60
    options, nearest = [], None
    for v in vehicles:
        if v.get("parked") or not v.get("passes"):
            continue
        legs = {}
        for p in v["passes"]:
            d = hav(lat, lon, p["lat"], p["lon"])
            if nearest is None or d < nearest["dist"]:
                nearest = {"line": v["line"], "tram": v["tram"], "stop": p["name"], "dist": round(d),
                           "eta": p["eta"], "head": p["head"]}
            if d > max_walk or p["end"]:
                continue
            walk = d * 1.25 / speed * 60
            leave = p["eta"] - walk - buffer
            if leave < now_secs - 30:
                continue
            cand = {"line": v["line"], "tram": v["tram"], "key": v["key"], "head": p["head"], "stop": p["name"],
                    "lat": p["lat"], "lon": p["lon"], "dist": round(d), "walk": round(walk / 60, 1),
                    "eta": p["eta"], "leave": leave, "basis": p["basis"], "leg": p["leg"],
                    "hurry": leave - now_secs < 120}
            best = legs.get(p["leg"])
            if best is None or (cand["dist"], cand["eta"]) < (best["dist"], best["eta"]):
                legs[p["leg"]] = cand
        options.extend(legs.values())
    # same line, same direction, same stop: one row with the later passes listed after it
    groups = {}
    for o in sorted(options, key=lambda o: o["eta"]):
        g = groups.get((o["line"], o["head"], o["stop"]))
        if g is None:
            o["then"] = []
            groups[(o["line"], o["head"], o["stop"])] = o
        elif len(g["then"]) < 3 and all(abs(o["eta"] - t) >= 90 for t in [g["eta"]] + g["then"]):
            g["then"].append(o["eta"])
    merged = sorted(groups.values(), key=lambda o: o["eta"])
    # a farther stop of the same line and direction only earns a row when it is 10+ min sooner
    out = []
    for o in merged:
        if any(p["line"] == o["line"] and p["head"] == o["head"] and p["dist"] <= o["dist"] and p["eta"] - o["eta"] < 600
               for p in merged if p is not o):
            continue
        out.append(o)
    return {"options": out, "nearest": nearest, "now": now_secs}


# ---------------------------------------------------------------- event log
def log_event(kind, text, **extra):
    ev = {"t": round(time.time(), 1), "type": kind, "text": text}
    ev.update(extra)
    try:
        with FileLock("events"):
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            if os.path.getsize(EVENTS_FILE) > 2_000_000:
                with open(EVENTS_FILE, encoding="utf-8") as f:
                    keep = f.readlines()[-4000:]
                with open(EVENTS_FILE + ".part", "w", encoding="utf-8") as f:
                    f.writelines(keep)
                os.replace(EVENTS_FILE + ".part", EVENTS_FILE)
    except Exception:
        pass
    return ev


def read_events(limit=200, since=0.0, kinds=None):
    try:
        with open(EVENTS_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 600_000))
            raw = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(raw):
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("t", 0) <= since:
            break
        if kinds and ev.get("type") not in kinds:
            continue
        out.append(ev)
        if len(out) >= limit:
            break
    return out


def _label(v):
    return "%s %s" % ("TRAM" if v["tram"] else "BUS", v["line"])


def record_fleet(vehicles, feed_ts):
    """Compare this feed with the last one and write what changed into the event log."""
    events = []
    with FileLock("fleet"):
        st = load_json(FLEET_FILE, None)
        first_run = not isinstance(st, dict) or "vehicles" not in st
        if first_run:
            st = {"vehicles": {}, "lines": []}
        known = st["vehicles"]
        for v in vehicles:
            k = v["key"]
            info = {"line": v["line"], "tram": v["tram"], "head": v["head"], "trip": v["trip_id"],
                    "near": v.get("near", "?"), "parked": v["parked"], "last": v["ts"]}
            old = known.get(k)
            if old is None:
                if v["parked"]:
                    events.append(("fleet", "%s (%s) is in the feed but parked near %s" % (_label(v), k, info["near"])))
                else:
                    events.append(("fleet", "%s (%s) appeared near %s, heading %s" % (_label(v), k, info["near"], v["head"])))
            else:
                if old.get("parked") and not v["parked"]:
                    events.append(("fleet", "%s (%s) is in service now, near %s, heading %s" % (_label(v), k, info["near"], v["head"])))
                elif not old.get("parked") and v["parked"]:
                    events.append(("fleet", "%s (%s) parked near %s" % (_label(v), k, info["near"])))
                elif old.get("trip") != v["trip_id"] and not v["parked"]:
                    events.append(("fleet", "%s (%s) started a trip to %s, now near %s" % (_label(v), k, v["head"], info["near"])))
            known[k] = info
        for k, old in list(known.items()):
            if feed_ts - old.get("last", 0) > GONE_SECS:
                if not old.get("parked"):
                    events.append(("fleet", "%s %s (%s) left the feed, last seen near %s at %s" % (
                        "TRAM" if old.get("tram") else "BUS", old.get("line"), k, old.get("near"),
                        clock(secs_of_day(from_epoch(old.get("last", feed_ts)))))))
                del known[k]
        running = sorted({i["line"] for i in known.values() if not i.get("parked")},
                         key=lambda x: int(x) if str(x).isdigit() else 999)
        if running != st.get("lines"):
            added = [x for x in running if x not in st.get("lines", [])]
            gone = [x for x in st.get("lines", []) if x not in running]
            text = "Lines running now: %s" % (", ".join(running) or "none")
            bits = []
            if added and not first_run:
                bits.append("new " + ", ".join(added))
            if gone:
                bits.append("stopped " + ", ".join(gone))
            if bits:
                text += " (%s)" % "; ".join(bits)
            events.append(("lines", text))
            st["lines"] = running
        st["t"] = feed_ts
        save_json(FLEET_FILE, st)
    return [log_event(kind, text) for kind, text in events]


def log_observed(vehicles):
    """Raw position samples for the day log (zet day)."""
    path = os.path.join(APP_DIR, "observed-%s.jsonl" % now_zagreb().strftime("%Y%m%d"))
    try:
        with FileLock("observed"):
            with open(path, "a", encoding="utf-8") as fh:
                for v in vehicles:
                    fh.write(json.dumps({"t": v["ts"], "k": v["key"], "r": v["line"], "trip": v["trip_id"],
                                         "lat": round(v["lat"], 6), "lon": round(v["lon"], 6),
                                         "s": "PARKED" if v["parked"] else v["status"]}, ensure_ascii=False) + "\n")
    except Exception:
        pass
