#!/usr/bin/env python3
"""TEST 3 - the ugly cases: the preferred port already taken (by another copy of this very app), a
Google session that ends mid-map, a tile address without the page's token, a key pasted with
nothing key-shaped in it, a test with no key at all, a map library nobody can fetch, and a page
whose map library never arrived still giving settings."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (APP, Console, FakeTiles, FakeZet, gtfs_zip, check, client, console_env, fake_key, fake_opener, finish,  # noqa: E402
                    fresh_home, free_port, http, read_lines, skipped)

home = fresh_home()
key = fake_key(seed=23)
tiles = FakeTiles(key)
os.environ["ZET_TILE_BASE"] = tiles.base
os.environ["ZET_GOOGLE_KEY"] = key

# ---------------------------------------------------------------- the port is taken
bindir, opened = fake_opener()
busy = free_port()
blocker = socket.socket()
blocker.bind(("127.0.0.1", busy))
blocker.listen(1)
con = Console(console_env({"PATH": bindir + os.pathsep + os.environ["PATH"], "ZET_PORT": str(busy)}))
try:
    ok = con.wait_for(r"\[Q\] stop", 20)
    t = con.text()
    check("a taken port: it starts anyway and says which port instead", ok and ("port %d " % busy) in t
          and ("so this one is on %d instead" % (busy + 1)) in t and ("on this phone  http://127.0.0.1:%d" % (busy + 1)) in t, t[-500:])
    con.wait_for(r"opened in", 10)
    calls = read_lines(opened)
    check("and the page opens at the port it really took", calls and calls[0].startswith("http://127.0.0.1:%d " % (busy + 1)), calls)
    with open(os.path.join(home, ".mantra", "ports", "zet")) as f:
        check("the registry holds the real port, not the wanted one", f.read().strip() == str(busy + 1))
    con.key("q")
    con.done(8)
finally:
    con.kill()
    blocker.close()

# ---------------------------------------------------------------- the rest, through the test client
appmod, c, H = client()
J = dict(H, **{"Content-Type": "application/json"})
url = "/tile/google/2/1/1?t=" + appmod.TILE_TOKEN
check("a tile without the page's token is not served", c.get("/tile/google/2/1/1", headers={"Host": H["Host"]}).status_code == 404)
check("a tile outside the world is not asked of Google", c.get("/tile/google/2/9/1?t=" + appmod.TILE_TOKEN, headers={"Host": H["Host"]}).status_code == 404)
r = c.get(url, headers={"Host": H["Host"]})
check("a tile is served", r.status_code == 200 and r.data.startswith(b"\x89PNG"))
tiles.expire_next = True
before = tiles.sessions
r = c.get(url, headers={"Host": H["Host"]})
check("a Google session that ended mid-map: a new one is made and the tile still comes", r.status_code == 200 and tiles.sessions == before + 1, (r.status_code, tiles.sessions))

os.environ["ZET_GOOGLE_KEY"] = "AIza" + "x" * 10          # the wrong key: Google (the fake) refuses the session
import mapkey  # noqa: E402
mapkey._session.update(token=None, expiry=0, fp=None)
r = c.get(url, headers={"Host": H["Host"]})
check("a key Google refuses: the tile answers 502 with why, never a broken image", r.status_code == 502 and b"refused" in r.data, (r.status_code, r.data[:80]))
del os.environ["ZET_GOOGLE_KEY"]

r = c.post("/api/google/save", json={"text": "hello, this is not a key"}, headers=H)
check("nothing key-shaped pasted: a sentence, nothing saved", r.status_code == 400 and "AIza" in r.get_json()["error"] and mapkey.saved_keys() == [])
r = c.post("/api/google/test", json={}, headers=H)
check("test with no key: a sentence saying where to put one", r.status_code == 400 and "Settings" in r.get_json()["error"])
r = c.post("/api/google/delete", json={}, headers=H)
check("delete with no key: says nothing was there", r.get_json() == {"deleted": False})
r = c.post("/api/google/save", json={"text": key}, headers={"Host": H["Host"], "Content-Type": "application/json"})
check("a save without the page's header is refused", r.status_code == 403 and mapkey.saved_keys() == [])

# ---------------------------------------------------------------- the map library cannot be fetched
import vendor  # noqa: E402
vendor.SOURCES = ["http://127.0.0.1:9/%s"]
vendor.TARBALL = "http://127.0.0.1:9/leaflet.tgz"
shutil.rmtree(vendor.DIR, ignore_errors=True)
r = c.get("/vendor/leaflet.js", headers={"Host": H["Host"]})
check("no source reachable: 404, and nothing half-kept", r.status_code == 404 and not os.path.exists(os.path.join(vendor.DIR, "leaflet.js")))

node, pw = shutil.which("node"), "/opt/node22/lib/node_modules/playwright"
if node and os.path.isdir(pw):
    # the real page with its map library missing: settings must still work
    port = free_port()
    env = dict(os.environ, ZET_PORT=str(port), ZET_NO_CONSOLE="1", ZET_NO_BROWSER="1", ZET_POLL="3600",
               ZET_FEED_URL="http://127.0.0.1:9/x", ZET_STATIC_URL="http://127.0.0.1:9/x")
    srv = subprocess.Popen([sys.executable, os.path.join(APP, "app.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time
        for _ in range(50):
            if http("http://127.0.0.1:%d/health" % port, 1)[0] == 200:
                break
            time.sleep(0.2)
        js = tempfile.mktemp(suffix=".js")
        with open(js, "w") as f:
            f.write("""const {chromium} = require(%r);
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 390, height: 844}});
 const errs = []; p.on('pageerror', e => errs.push(e.message));
 await p.route('**/vendor/**', r => r.abort());
 await p.goto('http://127.0.0.1:%d/'); await p.waitForTimeout(1500);
 await p.click('#tabs button[data-v="set"]'); await p.waitForTimeout(800);
 console.log(JSON.stringify({errs, status: await p.textContent('#status'), lang: await p.inputValue('#s-walk')})); await b.close(); })();""" % (pw, port))
        r = subprocess.run([node, js], capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"errs": [r.stderr[-300:]]}
        check("no map library at all: no script error, settings still filled, and the page says why",
              not out.get("errs") and out.get("lang") == "4.5" and "map library" in out.get("status", ""), out)
    finally:
        srv.terminate()
        srv.wait(5)
else:
    skipped("the page without its map library", "no node or playwright here")

tiles.stop()

# ---------------------------------------------------------------- key files that are not what they should be
import io  # noqa: E402
import news  # noqa: E402

def pick(files):
    data = {"file": [(io.BytesIO(b), n) for n, b in files]}
    return c.post("/api/keys/import", data=data, headers=H, content_type="multipart/form-data")

r = pick([("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00binary" + b"\x00" * 100)])
check("a picture picked by mistake: 'not a text file', nothing read", r.status_code == 200 and "not a text file" in r.get_json()["results"][0]["say"])
r = pick([("shopping.txt", b"milk\nbread\nhttps://shop.example/?srsltid=AfmBOoq1234567890123456789012345\n")])
j = r.get_json()["results"][0]
check("a note with no key in it: says so, and a tracking token is not a key", j["found"] == 0 and "no key found" in j["say"], j)
r = pick([("huge.txt", b"a" * (3 * 1024 * 1024))])
check("a file over 2 MB is refused before it is read", r.status_code == 413)
gk = "AQ." + fake_key("Ab", 50, seed=91)
r = pick([("one.txt", ("kalabhumi\n%s\n" % gk).encode())])
r = pick([("again.txt", ("kalabhumi\n%s\n" % gk).encode())])
j = r.get_json()["results"][0]
check("the same key picked twice: 'already here', one copy kept", j["gemini"] == 0 and j["duplicates"] == 1 and len(news._read_keys()) == 1, j)
fp = news.fingerprint(gk)
r = c.post("/api/keys/title", json={"fp": "nothere", "title": "x"}, headers=H)
check("renaming a key that is not here: 404", r.status_code == 404)
r = c.post("/api/keys/title", json={"fp": fp, "title": "  a   very " + "long " * 40}, headers=H)
check("a long title is kept to 60 characters, spaces tidied", len(r.get_json()["title"]) == 60 and r.get_json()["title"].startswith("a very long"), r.get_json())
import labels  # noqa: E402
c.post("/api/keys/delete", json={"fp": fp}, headers=H)
check("deleting a key deletes its title", labels.get(fp) == "" and news._read_keys() == [])

# ---------------------------------------------------------------- traffic, when the world misbehaves
import core  # noqa: E402
import net  # noqa: E402

fz = FakeZet(n_vehicles=1)
core.FEED_URL, core.STATIC_URL = fz.feed_url, fz.static_url
core.fetch_feed(max_age=0)
fz.stop()
f = core.fetch_feed(max_age=0)
check("ZET unreachable with a copy on disk: the copy is used, and says it is stale", f["how"] == "cache" and f.get("stale") and len(f["vehicles"]) == 1, f.get("stale"))

fz = FakeZet()
core.FEED_URL, core.STATIC_URL = fz.feed_url, fz.static_url
idx = core.ensure_static(progress=False)
check("no timetable yet: it is downloaded, once", idx["version"] == "000396" and len(fz.count("GET", "/static", 200)) == 1)
idx = core.ensure_static(progress=False)
check("asked again the same day: nothing is asked of ZET at all", len(fz.count(path="/static")) == 1)
idx = core.ensure_static(progress=False, max_age=0)
check("asked again when due: one HEAD, no download", len(fz.count("HEAD", "/static")) == 1 and len(fz.count("GET", "/static")) == 1, fz.log)
fz.set_static(gtfs_zip("000397", pad=5000))
idx = core.ensure_static(progress=False, max_age=0)
check("ZET publishes a new timetable: it comes, and the new version is used", idx["version"] == "000397"
      and len(fz.count("GET", "/static", 200)) == 2, (idx.get("version"), fz.log))

fz.honour_conditionals = False
before = len(fz.count("GET", "/static", 200))
idx = core.ensure_static(progress=False, max_age=0)
check("a server that ignores 'has it changed?': the HEAD's ETag and size still spare the download",
      len(fz.count("GET", "/static", 200)) == before, fz.log[-3:])
fz.honour_conditionals = True

import news  # noqa: E402
news.FEEDS = [("Test", "http://127.0.0.1:%d/rss" % fz.port)]
news.fetch_headlines(fresh=0)
news.fetch_headlines(fresh=0)
check("a news feed asked twice: the second answer is 'not changed', no body", len(fz.count("GET", "/rss", 200)) == 1 and len(fz.count("GET", "/rss", 304)) == 1, fz.log[-3:])
news.fetch_headlines(fresh=600)
check("and within its fresh time it is not asked at all", len(fz.count("GET", "/rss")) == 2)
fz.stop()

finish("test 3, the ugly cases")
