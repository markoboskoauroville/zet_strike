#!/usr/bin/env python3
"""TEST 2 - the real thing, once: app.py through its own entry point on a real pty, with the ZET feed
unreachable (no vehicle, the case Marko named: the page must still be all there). The banner in the
MA READER shape, the page opened in Chrome only once the port answers and at the port bound, [A] for
the default browser, the page and its parts over real HTTP, the Google map tiles through the proxy
(a fake Google, the key checked), a real call to Google with a key it must refuse, the page in a
real Chromium at 390 px when one is here, and Q leaving nothing behind."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (APP, Console, FakeTiles, check, console_env, fake_key, fake_opener, finish, fresh_home,  # noqa: E402
                    free_port, http, read_lines, reachable, skipped)

home = fresh_home()
key = fake_key(seed=11)
tiles = FakeTiles(key)
bindir, opened = fake_opener()
port = free_port()
env = console_env({"PATH": bindir + os.pathsep + os.environ["PATH"], "ZET_PORT": str(port), "ZET_GOOGLE_KEY": key,
                   "ZET_TILE_BASE": tiles.base})
con = Console(env)
try:
    check("the banner comes up", con.wait_for(r"\[Q\] stop", 20), con.text()[-600:])
    t = con.text()
    check("MA READER's shape: name, where, library, version", all(s in t for s in (
        "ZET STRIKE  server", "on this phone  http://127.0.0.1:%d" % port, "library        ~/.zet-strike", "version        V8")), t[-600:])
    check("the five keys, one per line", all(s in t for s in (
        "[O] open in Chrome", "[A] open in the default browser", "[U] update the app", "[R] restart", "[Q] stop")))
    check("plain lines, never a box", not any(ch in t for ch in "┌┐└┘│"))
    check("the page is opened once the port answers", con.wait_for(r"opened in Chrome", 15), t[-300:])
    calls = read_lines(opened)
    check("in Chrome, at the port actually bound", calls and calls[0] == "http://127.0.0.1:%d com.android.chrome" % port, calls)

    base = "http://127.0.0.1:%d" % port
    st, body, hdr = http(base + "/")
    check("the page answers 200, the whole interface in the first frame", st == 200 and all(s in body for s in (
        b'id="map"', b'id="gkeyin"', b'id="b-gtest"', b'id="s-map"', b'id="keyin"', b'data-v="set"')))
    st, body, hdr = http(base + "/favicon.svg")
    check("a favicon, never the empty globe", st == 200 and hdr.get("Content-Type", "").startswith("image/svg+xml"))
    H = {"X-ZET": "1"}
    st, body, _ = http(base + "/api/live", headers=H)
    live = json.loads(body or b"{}")
    check("no vehicle and the feed unreachable: the API still answers, with the reason", st == 200 and live.get("vehicles") == [] and "feed" in (live.get("error") or ""), live)
    st, body, _ = http(base + "/api/settings", headers=H)
    s = json.loads(body or b"{}")
    check("settings answer: the port bound, V8, the Google key by fingerprint only", st == 200 and s["port"] == port and s["version"] == 8
          and s["google"]["has_key"] and s["google"]["source"] == "environment" and key not in body.decode())

    st, img, hdr = http(base + s["google_tiles"].replace("{z}", "3").replace("{x}", "4").replace("{y}", "2"))
    check("a Google tile comes through this server as an image", st == 200 and img.startswith(b"\x89PNG") and hdr.get("Content-Type") == "image/png", (st, img[:40]))
    check("one session made, reused for the next tile", tiles.sessions == 1 and http(base + s["google_tiles"].replace("{z}", "3").replace("{x}", "5").replace("{y}", "2"))[0] == 200 and tiles.sessions == 1)

    con.key("a")
    check("[A] opens the default browser, no package named", con.wait_for(r"opened in the default browser", 5) and read_lines(opened)[-1] == base)

    # a real call to Google, with a key it has never seen: rejected, never "works"
    if reachable("places.googleapis.com"):
        st, body, _ = http(base + "/api/google/test", headers=dict(H, **{"Content-Type": "application/json"}), data=b"{}")
        r = json.loads(body or b"{}")
        check("Google itself refuses a made-up key, and the page is told the map cannot use it",
              st == 200 and r.get("state") == "rejected" and r.get("tiles") == "rejected" and "refused" in r.get("map_says", ""), r)
    else:
        skipped("Google refuses a made-up key", "places.googleapis.com is not reachable from here")

    # the page in a real browser, 390 px, no script errors
    node = shutil.which("node")
    pw = "/opt/node22/lib/node_modules/playwright"
    if node and os.path.isdir(pw):
        js = tempfile.mktemp(suffix=".js")
        with open(js, "w") as f:
            f.write("""const {chromium} = require(%r);
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 390, height: 844}});
 const errs = []; p.on('pageerror', e => errs.push(e.message));
 await p.goto(%r); await p.waitForTimeout(1500);
 await p.click('#tabs button[data-v="set"]'); await p.waitForTimeout(800);
 const out = {errs, width: await p.evaluate(() => document.documentElement.scrollWidth),
   gfp: await p.textContent('#gfp'), gtest: await p.isDisabled('#b-gtest'), google: await p.isDisabled('#l-google input'),
   ver: await p.textContent('#ver'), status: await p.textContent('#status'), leaflet: await p.evaluate(() => typeof L.map)};
 console.log(JSON.stringify(out)); await b.close(); })();""" % (pw, base))
        r = subprocess.run([node, js], capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"errs": [r.stderr[-300:]]}
        check("in Chromium at 390 px: no script error, nothing wider than the phone", not out.get("errs") and out.get("width") == 390, out)
        check("the settings show the Google key by fingerprint, Test live, Google choosable", out.get("gfp") == s["google"]["fp"]
              and out.get("gtest") is False and out.get("google") is False, out)
        check("the version sits at the foot of settings", out.get("ver") == "V8")
    else:
        skipped("the page in Chromium", "no node or playwright here")

    con.key("q")
    code = con.done(8)
    check("Q stops it, cleanly", code == 0 and "stopped." in con.text(), (code, con.text()[-200:]))
    check("and its line in the port registry is gone", not os.path.exists(os.path.join(home, ".mantra", "ports", "zet")))
finally:
    con.kill()
    tiles.stop()

# the one word: `zet` alone is the server (Marko, 30.9.2026); `zet now` is the board
port2 = free_port()
con = Console(console_env({"PATH": bindir + os.pathsep + os.environ["PATH"], "ZET_PORT": str(port2), "ZET_NO_BROWSER": "1"}), script="zet.py")
try:
    check("`zet` with nothing after it starts the server, with its console", con.wait_for(r"\[Q\] stop", 20)
          and ("on this phone  http://127.0.0.1:%d" % port2) in con.text(), con.text()[-400:])
    check("and it serves the page", http("http://127.0.0.1:%d/health" % port2)[0] == 200)
    con.key("q")
    con.done(8)
finally:
    con.kill()
con = Console(console_env({"NO_COLOR": "1"}), script="zet.py", args=["now", "-q"])
try:
    code = con.done(40)
    t = con.text()
    # with the feed and timetable unreachable here the board ends early saying so (V6's behaviour); what
    # this checks is that `zet now` takes the board's road and ends, and never starts the server
    check("`zet now` is the board in the terminal, not the server", code is not None and "[Q] stop" not in t
          and "ZET STRIKE  server" not in t and ("running now" in t or "timetable" in t), (code, t[-400:]))
finally:
    con.kill()

finish("test 2, the real thing")
