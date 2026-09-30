#!/data/data/com.termux/files/usr/bin/python
"""zet - ZET Strike V9 terminal: what runs, where to catch it, what the lines are, and the strike news."""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import news

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code):
    return code if COLOR else ""


B, T, H = _c("\033[38;5;214m"), _c("\033[38;5;250m"), _c("\033[38;5;220m")
OK, DIM, WARN, R = _c("\033[1;33m"), _c("\033[38;5;244m"), _c("\033[38;5;208m"), _c("\033[0m")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TAG = {"fleet": ("BUS/TRAM", H), "lines": ("LINES", OK), "news": ("NEWS", T), "ai": ("AI", OK),
       "system": ("SYSTEM", DIM), "key": ("KEY", DIM)}


class Frame:
    """Amber box that adapts to the terminal width (38 to 72 columns)."""
    def __init__(self):
        cols = shutil.get_terminal_size((72, 24)).columns
        self.w = max(38, min(cols, 72))
        self.inner = self.w - 4
        self.lines = []

    def top(self):
        self.lines.append(B + "┌" + "─" * (self.w - 2) + "┐" + R)

    def sep(self):
        self.lines.append(B + "├" + "─" * (self.w - 2) + "┤" + R)

    def bottom(self):
        self.lines.append(B + "└" + "─" * (self.w - 2) + "┘" + R)

    def row(self, *segs):
        left, out = self.inner, ""
        for text, color in segs:
            if left <= 0:
                break
            if len(text) > left:
                text = text[:max(left - 1, 0)] + "…"
            out += color + text
            left -= len(text)
        self.lines.append(B + "│" + R + " " + out + R + " " * (left + 1) + B + "│" + R)

    def para(self, text, color=T, indent=1):
        for ln in textwrap.wrap(text or "", width=self.inner - indent) or [""]:
            self.row((" " * indent + ln, color))

    def blank(self):
        self.row(("", T))


def ago(secs):
    secs = max(int(secs), 0)
    if secs < 90:
        return "%ds" % secs
    if secs < 5400:
        return "%d min" % (secs // 60)
    return "%d h" % (secs // 3600)


def kind(v):
    return "TRAM" if v.get("tram") else "BUS"


def header(f, cfg, title):
    now = core.now_zagreb()
    f.top()
    f.row((" ZET STRIKE", H), ("  day %d" % core.strike_day(cfg, now), OK), ("  " + title, T))
    f.row((" %s" % now.strftime("%a %d.%m.%Y  %H:%M:%S"), T))


def emit(lines):
    text = "\n".join(lines)
    print(text)
    try:
        with open(core.CHAIN_FILE, "w", encoding="utf-8") as fh:
            fh.write(ANSI.sub("", text) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- data
def load_static():
    try:
        return core.ensure_static(), core.load_lines()
    except Exception as e:
        sys.stderr.write("  no timetable available (%s)\n" % e)
        return None, None


def live(idx, lines, cfg, quick, state=None):
    """Feed with movement measured: a second sample 12 s later tells MOVING from STANDING."""
    state = {} if state is None else state
    feed = core.fetch_feed()
    vs = core.snapshot(idx, lines, feed, state, cfg)
    if not quick and feed["vehicles"]:
        started = time.time()
        wait = 12
        while True:
            for left in range(wait, 0, -1):
                sys.stderr.write("\r  measuring movement %2ds " % left)
                sys.stderr.flush()
                time.sleep(1)
            feed = core.fetch_feed(max_age=0)      # the second sample must be new, or nothing moved
            vs = core.snapshot(idx, lines, feed, state, cfg)
            # a vehicle that sends no new position within ~20 s is reported as it is, not waited for
            if not [v for v in vs if v["status"] == "NEW" and not v["parked"]] or time.time() - started > 17:
                break
            wait = 6
        sys.stderr.write("\r" + " " * 32 + "\r")
    if idx:
        core.record_fleet(vs, feed["ts"])
        core.log_observed(vs)
    return feed, vs


# ---------------------------------------------------------------- zet (snapshot) and zet watch
def render_vehicle(f, v, nmax, full, now_secs):
    f.row((" %s %s" % (kind(v), v["line"]), H), ("  > " + v["head"], H))
    if v["parked"]:
        f.row((" veh %s  " % v["key"], T), ("PARKED", DIM), ("  near %s" % v["near"], T))
        f.row(("  far from its route, not carrying passengers", DIM))
        return
    stat = {"MOVING": ("MOVING %d km/h" % round(v["kmh"]), OK), "STANDING": ("STANDING", DIM)}.get(
        v["status"], ("position only", WARN))
    f.row((" veh %s  " % v["key"], T), stat, ("  signal %s old" % ago(v["age"]) if v["age"] > 60 else "", WARN))
    loc = v["loc"]
    if not loc:
        f.row((" no timetable for this trip", DIM))
        return
    if loc["at"] is not None:
        f.row((" at %s" % v["stops"][loc["at"]][0], T))
    else:
        f.row((" near %s (%d m)" % (v["stops"][loc["near"]][0], loc["near_d"]), T))
    if v["delay"]:
        f.row((" feed says %+d min against timetable" % round(v["delay"] / 60), DIM))
    leg0 = [p for p in v["passes"] if p["leg"] == 0 and not (loc["at"] is not None and p["i"] == loc["at"] and p["i"] > 0)]
    leg1 = [p for p in v["passes"] if p["leg"] == 1]
    if not leg0:
        f.row((" at the end of its trip", DIM))
    else:
        f.row((" next stops", DIM), ("  standing now, add its wait" if v["status"] == "STANDING" else "", WARN))
        shown = leg0 if full else leg0[:nmax]
        if not full and leg0[-1] not in shown:
            shown = shown + [leg0[-1]]
        prev = None
        for p in shown:
            if prev is not None and p["i"] - prev > 1:
                f.row(("       ...", DIM))
            f.row(("  %s  " % core.clock(p["eta"]), H), (p["name"] + ("  (end)" if p["end"] else ""), T))
            prev = p["i"]
    if leg1:
        f.row((" then back > %s from about %s" % (leg1[0]["head"], core.clock(leg1[0]["eta"])), DIM))


def render_board(cfg, feed, vs, args, footer_events=0):
    f = Frame()
    header(f, cfg, "running now")
    f.sep()
    flt = set(args.lines)
    shown = [v for v in vs if v["line"] in flt] if flt else [v for v in vs if args.all or not v["parked"]]
    hidden_parked = len([v for v in vs if v["parked"] and v not in shown])
    running = [v for v in shown if not v["parked"]]
    f.row((" %d in service" % len(running), OK), ("  %d moving" % sum(1 for v in running if v["status"] == "MOVING"), T))
    if not shown:
        f.sep()
        f.row((" No ZET vehicle is reporting from the field now.", T))
    for v in shown:
        f.sep()
        render_vehicle(f, v, args.next, args.full, core.secs_of_day(core.now_zagreb()))
    f.sep()
    if hidden_parked:
        f.row((" +%d parked vehicles hidden, show: zet now -a" % hidden_parked, DIM))
    if footer_events:
        evs = core.read_events(footer_events, kinds={"fleet", "lines", "news", "ai"})
        if evs:
            f.row((" latest events", DIM))
            for e in reversed(evs):
                f.row((" %s " % core.clock(core.secs_of_day(core.from_epoch(e["t"]))), H), (e["text"], T))
            f.sep()
    f.row((" feed %s old" % ago(time.time() - feed["ts"]), DIM), ("   zet.hr GTFS-RT", DIM))
    f.bottom()
    return f.lines


def cmd_board(idx, lines, cfg, args):
    feed, vs = live(idx, lines, cfg, args.quick)
    emit(render_board(cfg, feed, vs, args, footer_events=3))


def cmd_watch(idx, lines, cfg, args):
    state = {}
    last_news = 0
    interval = max(5, args.interval)
    try:
        while True:
            try:
                feed, vs = live(idx, lines, cfg, True, state)
                if time.time() - last_news > core.num(cfg, "news_minutes", 2, 240) * 60:
                    last_news = time.time()
                    try:
                        news.fetch_headlines(fresh=core.num(cfg, "news_minutes", 2, 240) * 60 - 30)
                    except Exception:
                        pass
                out = render_board(cfg, feed, vs, args, footer_events=5)
                sys.stdout.write("\033[2J\033[H" if COLOR else "\n")
                emit(out)
                print("%s  every %ds, Ctrl-C to stop%s" % (DIM, interval, R))
            except Exception as e:
                print(WARN + "feed problem: %s, retrying" % e + R)
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


# ---------------------------------------------------------------- zet near
def gps():
    """Phone position through Termux:API. Returns (lat, lon, label) or raises with a plain message."""
    if not shutil.which("termux-location"):
        raise RuntimeError("termux-location not found. Install the Termux:API app (F-Droid), then: pkg install termux-api")
    for provider, timeout in (("gps", 35), ("network", 20)):
        sys.stderr.write("  getting your position (%s)...\r" % provider)
        sys.stderr.flush()
        try:
            out = subprocess.run(["termux-location", "-p", provider, "-r", "once"], capture_output=True,
                                 text=True, timeout=timeout).stdout
            d = json.loads(out or "{}")
            if "latitude" in d:
                sys.stderr.write(" " * 44 + "\r")
                return d["latitude"], d["longitude"], "%s, within %d m" % (provider.upper(), d.get("accuracy") or 0)
        except Exception:
            continue
    sys.stderr.write(" " * 44 + "\r")
    raise RuntimeError("No position from the phone. Turn on Location and allow it for Termux:API, or type a place: zet near Vodnikova")


def where(idx, words):
    if len(words) == 2:
        try:
            lat, lon = float(words[0]), float(words[1])
            return lat, lon, "%.5f, %.5f" % (lat, lon)
        except ValueError:
            pass
    if words:
        hit = core.find_place(idx, " ".join(words))
        if not hit:
            raise RuntimeError("No stop called '%s'. Try part of the name, e.g. zet near jelacic" % " ".join(words))
        return hit[1], hit[2], "stop " + hit[0]
    return gps()


def cmd_near(idx, lines, cfg, args):
    if not idx:
        print("No timetable yet, run: zet update")
        return 1
    try:
        lat, lon, label = where(idx, args.rest)
    except RuntimeError as e:
        print(WARN + str(e) + R)
        return 1
    feed, vs = live(idx, lines, cfg, args.quick)
    now = core.secs_of_day(core.now_zagreb())
    res = core.plan(vs, lat, lon, now, cfg)
    f = Frame()
    header(f, cfg, "near you")
    f.row((" from ", DIM), (label, T))
    f.row((" walking %.1f km/h, up to %d m" % (core.num(cfg, "walk_kmh", 2, 8), core.num(cfg, "max_walk_m", 200, 5000)), DIM))
    if not res["options"]:
        f.sep()
        f.row((" Nothing running passes within walking distance.", T))
        n = res["nearest"]
        if n:
            f.para("Closest running line: %s %s at %s, %.1f km away." % (
                "TRAM" if n["tram"] else "BUS", n["line"], n["stop"], n["dist"] / 1000.0), T)
    for i, o in enumerate(res["options"][:6], 1):
        f.sep()
        f.row((" %d  %s %s" % (i, "TRAM" if o["tram"] else "BUS", o["line"]), H), ("  > " + o["head"], H))
        f.row(("    walk %d min (%d m) to " % (max(1, round(o["walk"])), o["dist"]), T), (o["stop"], OK))
        f.row(("    arrives %s" % core.clock(o["eta"]), H), ("   leave by %s" % core.clock(o["leave"]), OK if o["hurry"] else T))
        note = {"live": "from its live position", "standing": "standing now, times assume it leaves now",
                "return": "after it turns at the terminal", "position": "from its last position"}[o["basis"]]
        f.row(("    " + note, DIM))
        if o["then"]:
            f.row(("    also at " + ", ".join(core.clock(t) for t in o["then"]), DIM))
        if o["hurry"]:
            f.row(("    go now", WARN))
    f.sep()
    f.row((" times are estimates from the live feed", DIM))
    f.bottom()
    emit(f.lines)
    return 0


# ---------------------------------------------------------------- zet lines, zet line N
def render_line(f, d, vehicles, full):
    f.row((" %s %s" % ("TRAM" if d["tram"] else "BUS", d["line"]), H), ("  " + d["long"], T))
    for x in d["dirs"]:
        if x["circular"]:
            f.row(("  loop from %s" % x["from"], OK))
            f.para("via " + ", ".join(dict.fromkeys(x["names"][1:-1])), DIM, indent=4)
        else:
            f.row(("  %s > %s" % (x["from"], x["to"]), OK))
        f.row(("    %d stops, %d min" % (x["stops"], x["mins"]), T))
        f.row(("    normally %s to %s" % (x["first"], x["last"]) if x["first"] else "    no trips today", DIM))
        if full:
            for k, name in enumerate(x["names"], 1):
                f.row(("     %2d %s" % (k, name), T))
    for v in vehicles:
        if v["parked"]:
            f.row(("  veh %s parked near %s" % (v["key"], v["near"]), DIM))
        else:
            f.row(("  veh %s near %s > %s" % (v["key"], v["near"], v["head"]), H))


def cmd_lines(idx, lines, cfg, args):
    if not idx:
        print("No timetable yet, run: zet update")
        return 1
    if args.rest:
        rid = core.route_by_name(idx, args.rest[0])
        if not rid:
            print("No line %s in the ZET timetable" % args.rest[0])
            return 1
        feed, vs = live(idx, lines, cfg, True)
        f = Frame()
        header(f, cfg, "line %s" % args.rest[0])
        f.sep()
        render_line(f, core.describe_line(idx, lines, rid), [v for v in vs if v["route_id"] == rid], args.full)
        if not [v for v in vs if v["route_id"] == rid]:
            f.row(("  not in the live feed right now", WARN))
        f.bottom()
        emit(f.lines)
        return 0
    feed, vs = live(idx, lines, cfg, True)
    f = Frame()
    header(f, cfg, "lines in the feed")
    by_route = {}
    for v in vs:
        by_route.setdefault(v["route_id"], []).append(v)
    order = sorted(by_route, key=lambda r: (all(v["parked"] for v in by_route[r]), not core.route_info(idx, r)[2],
                                           int(core.route_info(idx, r)[0]) if core.route_info(idx, r)[0].isdigit() else 999))
    if not order:
        f.sep()
        f.row((" No line has a vehicle in the feed now.", T))
    for rid in order:
        f.sep()
        render_line(f, core.describe_line(idx, lines, rid), by_route[rid], args.full)
    f.sep()
    f.row((" all stops of a line: zet line 228 -f", DIM))
    f.bottom()
    emit(f.lines)
    return 0


# ---------------------------------------------------------------- zet news, zet log
def render_summary(f, s):
    d = s.get("data") or {}
    f.row((" AI summary ", OK), ("%s ago, %s, key %s" % (ago(time.time() - s["t"]), s.get("model"), s.get("key")), DIM))
    if not d:
        f.para(s.get("raw") or "(empty)", T)
        return
    f.para(d.get("headline", ""), H)
    if d.get("status"):
        f.row((" status: ", DIM), (d["status"], OK))
    f.blank()
    f.para(d.get("summary", ""), T)
    if d.get("service_now"):
        f.blank()
        f.row((" running today", DIM))
        for x in d["service_now"][:8]:
            f.para("%s: %s" % (x.get("line", "?"), x.get("what", "")), T, indent=2)
    if d.get("timeline"):
        f.blank()
        f.row((" timeline", DIM))
        for x in d["timeline"][-14:]:
            f.row(("  %s" % str(x.get("when", ""))[-11:], H))
            f.para("%s (%s)" % (x.get("what", ""), x.get("source", "")), T, indent=4)
    if d.get("next"):
        f.blank()
        f.row((" coming up", DIM))
        for x in d["next"][:5]:
            f.para("%s  %s" % (x.get("when", ""), x.get("what", "")), T, indent=2)
    if d.get("advice"):
        f.blank()
        f.para(d["advice"], OK)
    if s.get("sources"):
        f.row((" %d web sources checked" % len(s["sources"]), DIM))


def cmd_news(idx, lines, cfg, args):
    sys.stderr.write("  collecting headlines...\r")
    items, rep = news.fetch_headlines(fresh=0 if args.refresh else 300)      # a copy under 5 min old is enough
    sys.stderr.write(" " * 32 + "\r")
    s = news.last_summary()
    meta = None
    has_keys = bool(news.ring_status()["keys"])
    if has_keys and (args.refresh or not s or time.time() - s["t"] > 15 * 60):
        sys.stderr.write("  asking Gemini (with Google Search)...\r")
        live_vs = []
        if idx:
            try:
                _feed, live_vs = live(idx, lines, cfg, True)
            except Exception:
                live_vs = []
        facts = [core.describe_line(idx, lines, rid) for rid in sorted({v["route_id"] for v in live_vs})] if idx else []
        new_s, meta = news.summarize(cfg, live_vs, facts)
        sys.stderr.write(" " * 44 + "\r")
        s = new_s or s
    f = Frame()
    header(f, cfg, "news")
    f.sep()
    if meta and meta.get("error"):
        f.para("Gemini: " + meta["error"], WARN)
        f.sep()
    if s:
        render_summary(f, s)
    elif not has_keys:
        f.para("Add a Gemini key for the summary and timeline: zet keys add", DIM)
    f.sep()
    f.row((" headlines ", DIM), ("%d new" % rep["new"], OK))
    for i in items[:args.next + 4]:
        when = core.from_epoch(i["when"]).strftime("%d.%m %H:%M") if i.get("when") else "--"
        f.row(("  %s " % when, H), (i["source"], DIM))
        f.para(i["title"], T, indent=4)
    bad = [k for k, r in rep["sources"].items() if r["errors"] and not r["ok"]]
    if bad:
        f.row((" unreachable: " + ", ".join(bad), WARN))
    f.bottom()
    emit(f.lines)
    return 0


def cmd_log(idx, lines, cfg, args):
    n = int(args.rest[0]) if args.rest and args.rest[0].isdigit() else 40
    evs = list(reversed(core.read_events(n)))
    f = Frame()
    header(f, cfg, "event log")
    day = None
    if not evs:
        f.sep()
        f.row((" Nothing logged yet. Run zet now, zet watch or zet.", T))
    for e in evs:
        dt = core.from_epoch(e["t"])
        if dt.strftime("%d.%m") != day:
            day = dt.strftime("%d.%m")
            f.sep()
            f.row((" " + dt.strftime("%A %d.%m.%Y"), OK))
        tag, col = TAG.get(e.get("type"), (e.get("type", "?").upper(), T))
        pub = ""
        if e.get("when"):
            pd = core.from_epoch(e["when"])
            pub = "  published %s" % (pd.strftime("%H:%M") if pd.date() == dt.date() else pd.strftime("%d.%m %H:%M"))
        f.row(("  %s " % dt.strftime("%H:%M"), H), (tag, col), (pub, DIM))
        f.para(e["text"] + ("  (%s)" % e["source"] if e.get("source") else ""), T, indent=4)
    f.bottom()
    emit(f.lines)
    return 0


# ---------------------------------------------------------------- zet keys
def cmd_google(rest):
    """zet keys google: paste the Google Maps key (hidden). zet keys google test | del."""
    import mapkey
    sub = rest[0] if rest else "add"
    if sub in ("del", "delete", "rm"):
        print("  deleted" if mapkey.delete() else "  no Google key saved on this phone")
        return 0
    if sub == "add":
        text = getpass.getpass("  Paste the Google Maps key, the text stays hidden: ") if sys.stdin.isatty() else sys.stdin.read()
        r = mapkey.save(text)
        if r.get("error"):
            print(WARN + "  " + r["error"] + R)
            return 1
        print("  saved %s, testing it (one map session, one place)..." % r["fp"])
    r = mapkey.test()
    if r.get("error"):
        print(WARN + "  " + r["error"] + R)
        return 1
    print("  %s  %s" % (r["fp"], r["detail"]))
    print((OK if r["tiles"] == "works" else WARN) + "  " + r["map_says"] + R)
    return 0 if r["tiles"] == "works" else 1


def cmd_keys(idx, lines, cfg, args):
    sub = args.rest[0] if args.rest else "list"
    if sub == "google":
        return cmd_google(args.rest[1:])
    if sub == "add":
        text = getpass.getpass("  Paste Gemini key(s), the text stays hidden: ") if sys.stdin.isatty() else sys.stdin.read()
        r = news.add_keys(text)
        print("  found %d, added %d, already had %d" % (r["found"], r["added"], r["duplicates"]))
        if r["added"]:
            print("  checking them, one real token each...")
            sub = "test"
        else:
            return 0 if r["found"] else 1
    if sub == "test":
        for t in news.test_keys():
            extra = (", models: " + ", ".join(t.get("flash", [])[:4])) if t.get("flash") else ""
            print("  %s  %-9s %s%s" % (t["fp"], t["verdict"], t["reason"], extra))
        return 0
    if sub in ("del", "delete", "rm") and len(args.rest) > 1:
        ok = news.remove_key(args.rest[1])
        print("  deleted" if ok else "  no key with fingerprint %s" % args.rest[1])
        return 0 if ok else 1
    st = news.ring_status()
    f = Frame()
    header(f, cfg, "Gemini keys")
    f.sep()
    if not st["keys"]:
        f.row((" No keys yet. Add: zet keys add", T))
    for k in st["keys"]:
        col = {"ok": OK, "new": T, "cool": WARN, "dead": WARN}.get(k["state"], T)
        f.row((" %s%s " % ("*" if k["active"] else " ", k["fp"]), H), (k["state"], col),
              ("  rest %ds" % k["rest"] if k["rest"] else "", WARN), ("  " + (k.get("last") or ""), DIM))
    f.sep()
    f.row((" * = in use. Keys are shown by fingerprint only.", DIM))
    f.row((" zet keys add | test | del FINGERPRINT", DIM))
    try:
        import mapkey
        g = mapkey.status()
        f.row((" Google Maps key: ", T), ((g["fp"] + " from " + g["source"] + ", map tiles " + (g.get("tiles") or "not tested")) if g["has_key"] else "none", DIM))
    except Exception:
        pass
    f.row((" zet keys google | google test | google del", DIM))
    f.bottom()
    emit(f.lines)
    return 0


# ---------------------------------------------------------------- zet day
def cmd_day(idx, lines, cfg, args):
    path = os.path.join(core.APP_DIR, "observed-%s.jsonl" % core.now_zagreb().strftime("%Y%m%d"))
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    except Exception:
        pass
    f = Frame()
    header(f, cfg, "seen today")
    f.sep()
    groups = {}
    for r in rows:
        groups.setdefault((r["k"], r["trip"]), []).append(r)
    items = sorted(((rs[0]["t"], k, rs) for k, rs in groups.items() if args.all or any(x["s"] != "PARKED" for x in rs)),
                   key=lambda x: x[0])
    if not items:
        f.row((" Nothing logged yet. Run zet now or zet watch first.", T))
    for first, (key, trip), rs in items:
        moved = any(x["s"] == "MOVING" for x in rs)
        a = core.nearest_stop(idx, rs[0]["lat"], rs[0]["lon"]) if idx else "?"
        b = core.nearest_stop(idx, rs[-1]["lat"], rs[-1]["lon"]) if idx else "?"
        f.row((" %s veh %s" % (rs[0]["r"], key), H),
              ("  %s-%s" % (core.clock(core.secs_of_day(core.from_epoch(first))),
                            core.clock(core.secs_of_day(core.from_epoch(rs[-1]["t"])))), T),
              ("  moved" if moved else "  stood", OK if moved else DIM))
        f.row(("   %s > %s" % (a, b), DIM))
    f.bottom()
    emit(f.lines)
    return 0


HELP = """zet, ZET Strike V9

  zet                 the server: map, near me, lines, news, log and settings in Chrome (O A U R Q)
  zet now             what runs now, with next stops, here in the terminal
  zet now 17 228      only these lines
  zet near            where and when to catch something (phone GPS)
  zet near Vodnikova  same, from a stop you name
  zet lines           the lines in the feed: from, to, stops, hours
  zet line 228 -f     one line with all its stops
  zet news            strike headlines + AI summary and timeline
  zet news -r         ask Gemini again now
  zet log             event log (zet log 100)
  zet watch           live board, refreshes every 20 s
  zet keys            Gemini keys (add, test, del)
  zet keys google     the Google Maps key (paste, test, del)
  zet day             every vehicle seen today
  zet data            what came over the network today (feed, timetable, news)
  zet update          newest version from GitHub; asks whether the timetable changed
  zet update timetable force   the timetable again even if unchanged (13 MB)
  zet update check    only look, change nothing
  options: -q skip movement check, -a show parked, -n 10 more stops
  zet map is the same as zet (the server)
Output of the last run: ~/.zet-strike/chain.txt"""


SERVER_WORDS = ("map", "server", "serve", "s")
BOARD_WORDS = ("now", "board", "b")


def main():
    # One word runs the app (MANTRA_MANIFEST termux-app.md §4): `zet` alone is the server, as
    # `zet map` was in V7. The terminal board is `zet now`; flags alone (zet -a) still mean the board.
    if len(sys.argv) == 1 or sys.argv[1] in SERVER_WORDS:
        here = os.path.dirname(os.path.abspath(__file__))
        os.execv(sys.executable, [sys.executable, os.path.join(here, "app.py")] + sys.argv[2:])
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # zet | head must end quietly (chained use)
    except Exception:
        pass
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("words", nargs="*")
    p.add_argument("-a", "--all", action="store_true")
    p.add_argument("-q", "--quick", action="store_true")
    p.add_argument("-f", "--full", action="store_true")
    p.add_argument("-r", "--refresh", action="store_true")
    p.add_argument("-n", "--next", type=int, default=6)
    p.add_argument("-i", "--interval", type=int, default=20)
    p.add_argument("-h", "--help", action="store_true")
    args = p.parse_args()
    if args.help or (args.words and args.words[0] in ("help", "h")):
        print(HELP)
        return 0
    cmds = {"watch": cmd_watch, "w": cmd_watch, "near": cmd_near, "n": cmd_near, "lines": cmd_lines,
            "line": cmd_lines, "l": cmd_lines, "news": cmd_news, "log": cmd_log, "keys": cmd_keys,
            "key": cmd_keys, "day": cmd_day, "d": cmd_day, "update": None, "u": None, "data": cmd_data}
    mode, args.lines, args.rest = "board", [], []
    if args.words and args.words[0] in BOARD_WORDS:
        args.lines = args.words[1:]
    elif args.words and args.words[0] in cmds:
        mode, args.rest = args.words[0], args.words[1:]
    else:
        args.lines = args.words
    cfg = core.load_config()
    if mode in ("keys", "key"):
        return cmd_keys(None, None, cfg, args)
    if mode in ("update", "u"):
        return cmd_update(args.rest)
    if mode == "data":
        return cmd_data(None, None, cfg, args)
    idx, lines = load_static()
    try:
        if mode == "board":
            return cmd_board(idx, lines, cfg, args) or 0
        return cmds[mode](idx, lines, cfg, args) or 0
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as e:
        print(WARN + "problem: %s" % e + R)
        return 1


def cmd_data(idx, lines, cfg, args):
    """zet data: what came over the network today, per source."""
    import net
    t = net.today()
    f = Frame()
    header(f, cfg, "data used today")
    f.sep()
    names = {"feed": "live feed", "timetable": "timetable", "news": "news feeds"}
    total = 0
    for src in ("feed", "timetable", "news"):
        v = t.get(src, {})
        total += v.get("bytes", 0)
        f.row((" %-11s" % names[src], H), ("%9s" % net.human(v.get("bytes", 0)), OK),
              ("  %d down, %d not changed, %d copies used" % (v.get("net", 0), v.get("same", 0), v.get("cache", 0)), DIM))
    f.sep()
    f.row((" together    ", H), ("%9s" % net.human(total), OK), ("  bodies only, headers not counted", DIM))
    f.bottom()
    emit(f.lines)
    return 0


def cmd_update(rest):
    """zet update: newest version from GitHub (every file checksum verified), then the timetable.
    zet update check: only look.  zet update timetable: only the timetable."""
    import update
    code = 0
    if "timetable" not in rest:
        print(H + "ZET Strike update from github.com/%s" % update.REPO + R)
        try:
            if "check" in rest:
                info = update.check()
                res = dict(info, message="already the newest version" if info["up_to_date"] else "a newer version is waiting, run: zet update")
            else:
                res = update.apply(force="force" in rest)
            print("  installed: V%s %s" % (res["current"], res["current_commit"]))
            print("  on GitHub: V%s %s" % (res["latest"], res["latest_commit"]))
            print(OK + "  " + res["message"] + R)
            if res.get("changed"):
                print("  previous version kept in %s" % res["backup"].replace(os.path.expanduser("~"), "~"))
                print("  if the server (zet) is running, press R in it to restart")
        except Exception as e:
            print(WARN + "  update failed, nothing was changed: %s" % e + R)
            code = 1
    if "check" not in rest:
        try:
            # asked whether it changed (a HEAD, a conditional download): the 13 MB come only when it did,
            # or with `zet update timetable force`
            idx = core.ensure_static(force="force" in rest, max_age=0)
            print("  timetable version %s, %d lines, %d stops" % (idx["version"], len(idx["routes"]), len(idx["stops"])))
        except Exception as e:
            print(WARN + "  timetable refresh failed: %s" % e + R)
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
