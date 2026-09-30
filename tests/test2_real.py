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
from common import (APP, Console, FakeTiles, FakeZet, check, console_env, fake_key, fake_opener, finish, fresh_home,  # noqa: E402
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
        "ZET STRIKE  server", "on this phone  http://127.0.0.1:%d" % port, "library        ~/.zet-strike", "version        V10")), t[-600:])
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
    check("settings answer: the port bound, V10, the Google key by fingerprint only", st == 200 and s["port"] == port and s["version"] == 10
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
        check("the version sits at the foot of settings", out.get("ver") == "V10")
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


# ---------------------------------------------------------------- the file picker, in a real browser
# Marko, 30.9.2026: "a file picker for the keys ... multiple keys and each key can have title".
if node and os.path.isdir(pw):
    fresh_home()
    kdir = tempfile.mkdtemp(prefix="zet-keys-")
    ga, gb = "AQ." + fake_key("Ab", 50, seed=31), "AQ." + fake_key("Ab", 50, seed=32)
    m1, m2 = fake_key(seed=33), fake_key(seed=34)
    with open(os.path.join(kdir, "gemini.txt"), "w") as f:
        f.write("AV LIVE VMIX\n%s\n\ncaffeteria\n%s\n" % (ga, gb))
    with open(os.path.join(kdir, "maps.keys.txt"), "w") as f:
        f.write("# keyring v1\n\nprovider: google\nlabel: maps phone\nkey: %s\n\nprovider: google\nlabel: maps laptop\nkey: %s\n" % (m1, m2))
    port4 = free_port()
    envk = dict(os.environ, ZET_PORT=str(port4), ZET_NO_CONSOLE="1", ZET_NO_BROWSER="1", ZET_POLL="3600",
                ZET_FEED_URL="http://127.0.0.1:9/x", ZET_STATIC_URL="http://127.0.0.1:9/x")
    srv = subprocess.Popen([sys.executable, os.path.join(APP, "app.py")], env=envk, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time as _t
        for _ in range(60):
            if http("http://127.0.0.1:%d/health" % port4, 1)[0] == 200:
                break
            _t.sleep(0.2)
        js = tempfile.mktemp(suffix=".js")
        with open(js, "w") as f:
            f.write("""const {chromium} = require(%r);
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 390, height: 844}});
 const errs = []; p.on('pageerror', e => errs.push(e.message));
 await p.goto('http://127.0.0.1:%d/'); await p.waitForTimeout(800);
 await p.click('#tabs button[data-v="set"]'); await p.waitForTimeout(500);
 const pickerVisible = await p.isVisible('#b-kfile');
 const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#b-kfile')]);
 const multiple = chooser.isMultiple();
 await chooser.setFiles([%r, %r]);
 await p.waitForFunction(() => /added/.test(document.getElementById('kfmsg').textContent), null, {timeout: 20000});
 const msg = await p.textContent('#kfmsg');
 await p.waitForTimeout(800);
 const gem = await p.$$eval('#keys .ktitle', els => els.map(e => e.textContent));
 const goo = await p.$$eval('#gkeys .ktitle', els => els.map(e => e.textContent));
 const radios = await p.$$eval('#gkeys input[name=guse]', els => els.map(e => e.checked));
 await p.check('#gkeys input[name=guse] >> nth=1'); await p.waitForTimeout(800);
 const inUse = await p.textContent('#gfp');
 p.once('dialog', d => d.accept('main account'));
 await p.click('#keys button[data-act=ren] >> nth=0'); await p.waitForTimeout(800);
 const renamed = await p.$$eval('#keys .ktitle', els => els.map(e => e.textContent));
 const html = await p.content();
 const small = await p.$$eval('.kpick', els => els.length);
 console.log(JSON.stringify({errs, pickerVisible, multiple, msg, gem, goo, radios, inUse, renamed, small,
   leaked: [%r, %r, %r, %r].some(k => html.includes(k)), width: await p.evaluate(() => document.documentElement.scrollWidth)}));
 await b.close(); })();""" % (pw, port4, os.path.join(kdir, "gemini.txt"), os.path.join(kdir, "maps.keys.txt"), ga, gb, m1, m2))
        r = subprocess.run([node, js], capture_output=True, text=True, timeout=120)
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"errs": [r.stderr[-400:]]}
        check("Choose key files opens the phone's file dialog, and takes several files at once", out.get("pickerVisible") and out.get("multiple") is True, out)
        check("both files read: two Gemini keys, two Google keys, all with titles", "2 Gemini keys added, 2 with a title" in out.get("msg", "")
              and "2 Google Maps keys added, 2 with a title" in out.get("msg", ""), out.get("msg"))
        check("the Gemini keys carry their names from the note", out.get("gem") == ["AV LIVE VMIX", "caffeteria"], out.get("gem"))
        check("the Google keys carry theirs from the keyring file, the first in use", out.get("goo") == ["maps phone", "maps laptop"] and out.get("radios") == [True, False], out)
        check("the radio chooses the other Google key", (out.get("inUse") or "").startswith("maps laptop"), out.get("inUse"))
        check("Rename gives a key a new title", (out.get("renamed") or [""])[0] == "main account", out.get("renamed"))
        check("a File button beside each paste box too", out.get("small") == 2)
        check("no key ever reaches the page, no script error, nothing wider than the phone", out.get("leaked") is False and not out.get("errs") and out.get("width") == 390, out)
    finally:
        srv.terminate()
        srv.wait(5)
else:
    skipped("the file picker in Chromium", "no node or playwright here")

# ---------------------------------------------------------------- traffic: the real server against a stand-in ZET
# Every request ZET would see is written down by the stand-in, so what is measured is traffic, not intent.
import time  # noqa: E402

fz = FakeZet(n_vehicles=0)
home2 = fresh_home()
os.makedirs(os.environ["ZET_STRIKE_DIR"])
with open(os.path.join(os.environ["ZET_STRIKE_DIR"], "config.json"), "w") as f:
    json.dump({"poll_seconds": 10}, f)
port3 = free_port()
envz = dict(os.environ, ZET_PORT=str(port3), ZET_NO_CONSOLE="1", ZET_NO_BROWSER="1", ZET_WATCHING_S="6",
            ZET_FEED_URL=fz.feed_url, ZET_STATIC_URL=fz.static_url)
envz.pop("ZET_POLL", None)
srv = subprocess.Popen([sys.executable, os.path.join(APP, "app.py")], env=envz, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(80):
        if http("http://127.0.0.1:%d/health" % port3, 1)[0] == 200 and fz.count("GET", "/feed"):
            break
        time.sleep(0.2)
    time.sleep(1)
    check("started: the timetable once, the feed once", len(fz.count("GET", "/static", 200)) == 1 and len(fz.count("GET", "/feed")) == 1, fz.log)
    time.sleep(7)
    check("nobody looking: no second feed request in 7 s (the idle clock is 5 min)", len(fz.count("GET", "/feed")) == 1, fz.log)
    H2 = {"X-ZET": "1"}
    end = time.time() + 16
    while time.time() < end:
        http("http://127.0.0.1:%d/api/live" % port3, 2, headers=H2)
        time.sleep(2)
    looked = len(fz.count("GET", "/feed"))
    check("a page open: the feed is asked for on the 10 s clock", 2 <= looked <= 4, fz.log)
    check("and ZET answers 'not changed' with no body, so a quiet feed costs nothing", len(fz.count("GET", "/feed", 304)) >= 1, fz.log)
    time.sleep(6 + 2 * 10 + 1)             # the watching window, then at most the fast rounds already begun
    settled = len(fz.count("GET", "/feed"))
    time.sleep(14)
    check("the page closed: after its window the fast clock stops (nothing in 14 s)", settled <= looked + 2
          and len(fz.count("GET", "/feed")) == settled, (looked, settled, fz.log))
    st, body, _ = http("http://127.0.0.1:%d/api/traffic" % port3, headers=H2)
    tr = json.loads(body or b"{}")
    wire = fz.body_bytes("/feed") + fz.body_bytes("/static")
    check("the data card shows what really came over the network", st == 200 and tr["total"] == wire and not tr["watching"], (tr, wire))

    before = len(fz.log)
    zenv = dict(envz, NO_COLOR="1")
    r1 = subprocess.run([sys.executable, os.path.join(APP, "zet.py"), "now"], env=zenv, capture_output=True, text=True, timeout=60)
    mid = len(fz.log)
    r2 = subprocess.run([sys.executable, os.path.join(APP, "zet.py"), "now"], env=zenv, capture_output=True, text=True, timeout=60)
    check("`zet now` twice in a row: the second one downloads nothing", "running now" in r2.stdout and len(fz.log) == mid and mid - before <= 1,
          (fz.log[before:], r2.stdout[-200:], r2.stderr[-200:]))
    r = subprocess.run([sys.executable, os.path.join(APP, "zet.py"), "update", "timetable"], env=zenv, capture_output=True, text=True, timeout=60)
    check("`zet update timetable`: asks whether it changed, and the 13 MB do not come again", "timetable version 000396" in r.stdout
          and len(fz.count("GET", "/static", 200)) == 1 and fz.count("HEAD", "/static"), (r.stdout[-300:], fz.log[-4:]))
    r = subprocess.run([sys.executable, os.path.join(APP, "zet.py"), "data"], env=zenv, capture_output=True, text=True, timeout=30)
    check("`zet data` shows today's use per source", "live feed" in r.stdout and "timetable" in r.stdout and "together" in r.stdout, r.stdout[-400:])
finally:
    srv.terminate()
    srv.wait(5)
    fz.stop()

finish("test 2, the real thing")
