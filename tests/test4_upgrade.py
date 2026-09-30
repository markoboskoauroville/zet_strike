#!/usr/bin/env python3
"""TEST 4 - the upgrade: a phone that has V6 (its files, its config with port 8080, its Gemini ring)
takes V8 through the real updater, from a stand-in GitHub serving this checkout at a commit, the
branch remembered. Then V8 starts from that folder: the port moved off the finder's 8080, the old
ring still read, the MANIFEST the updater trusts matching every file."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import APP, REPO, Console, check, console_env, fake_opener, finish, fresh_home, free_port, http  # noqa: E402

home = fresh_home()
data = os.environ["ZET_STRIKE_DIR"]
os.makedirs(data)

# ---------------------------------------------------------------- the MANIFEST the updater trusts
r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "manifest.py"), "--check"], capture_output=True, text=True)
check("MANIFEST.json matches every file in app/", r.returncode == 0, r.stdout + r.stderr)
with open(os.path.join(APP, "MANIFEST.json")) as f:
    manifest = json.load(f)
check("MANIFEST says V8", manifest["version"] == 8)

# ---------------------------------------------------------------- a phone on V6
v6 = {}
for name in ("app.py", "core.py", "index.html", "news.py", "update.py", "zet.py", "zs.py"):
    r = subprocess.run(["git", "-C", REPO, "show", "81e54ce:app/" + name], capture_output=True)
    if r.returncode == 0:
        v6[name] = r.stdout
check("the V6 files are at hand (commit 81e54ce)", len(v6) == 7, sorted(v6))
for name, b in v6.items():
    with open(os.path.join(data, name), "wb") as f:
        f.write(b)
with open(os.path.join(data, "VERSION.json"), "w") as f:
    json.dump({"version": 6, "commit": "81e54ce" + "0" * 33, "files": {n: hashlib.sha256(b).hexdigest() for n, b in v6.items()}}, f)
with open(os.path.join(data, "config.json"), "w") as f:
    json.dump({"port": 8080, "strike_start": "2026-09-28", "walk_kmh": 5.0, "tiles": ""}, f)
gkey = "AQ.Ab" + "k" * 50
gfp = hashlib.sha256(gkey.encode()).hexdigest()[:10]
os.makedirs(os.path.join(data, "secrets"), mode=0o700)
with open(os.path.join(data, "secrets", "gemini_keys"), "w") as f:
    f.write(gkey + "\n")
with open(os.path.join(data, "keyring.json"), "w") as f:
    json.dump({"keys": {gfp: {"state": "ok", "last": "ok, gemini"}}, "active": gfp, "models_gone": {}}, f)

# ---------------------------------------------------------------- a stand-in GitHub serving this checkout
SHA = "7" * 40
BRANCH = "claude/test-branch"
served = []


class GH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        served.append(self.path)
        if self.path == "/repos/markoboskoauroville/zet_strike/commits/" + BRANCH:
            body = SHA.encode()
        elif self.path.startswith("/markoboskoauroville/zet_strike/%s/app/" % SHA):
            try:
                with open(os.path.join(APP, self.path.rsplit("/", 1)[1]), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


gh = ThreadingHTTPServer(("127.0.0.1", 0), GH)
threading.Thread(target=gh.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % gh.server_address[1]
env = dict(os.environ, ZET_GITHUB_API=base, ZET_GITHUB_RAW=base, ZET_BRANCH=BRANCH, PREFIX=os.path.join(home, "usr"))

# the real updater, as the U key and `zet update` run it, from the new code (the V6 updater has no branch)
code = "import sys; sys.path.insert(0, %r); import update, json; print(json.dumps({k: v for k, v in update.apply().items() if k != 'manifest'}))" % APP
r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    res = {}
check("the updater reports V6 -> V8, every file verified", res.get("changed") and res.get("current") == 6 and res.get("latest") == 8, (res, r.stderr[-400:]))
same = all(open(os.path.join(data, n), "rb").read() == open(os.path.join(APP, n), "rb").read() for n in manifest["files"] if n != "zs.py")
check("every V8 file is in place, byte for byte", same)
check("zs.py, the bridge for installs from before the rename, is removed as migrate() intends", not os.path.exists(os.path.join(data, "zs.py")))
with open(os.path.join(data, "VERSION.json")) as f:
    ver = json.load(f)
check("VERSION.json remembers V8, the commit and the branch it came from", ver["version"] == 8 and ver["commit"] == SHA and ver["branch"] == BRANCH, ver)
backups = os.listdir(os.path.join(data, "backup"))
check("the V6 files are kept in backup", len(backups) == 1 and open(os.path.join(data, "backup", backups[0], "app.py"), "rb").read() == v6["app.py"])
check("the zet command is written", os.path.exists(os.path.join(home, "usr", "bin", "zet")))

# ---------------------------------------------------------------- V8 starts from the upgraded folder
sys.path.insert(0, data)
import core  # noqa: E402
cfg = core.load_config()
with open(os.path.join(data, "config.json")) as f:
    saved = json.load(f)
check("the port moves off the finder's 8080 to 8100, once, and the rest of the config stays",
      cfg["port"] == 8100 and saved["port"] == 8100 and saved["walk_kmh"] == 5.0, saved)
import news  # noqa: E402
st = news.ring_status()
check("the V6 Gemini key and its ring state are still read", [(k["fp"], k["state"], k["active"]) for k in st["keys"]] == [(gfp, "ok", True)], st)
port = free_port()
srv = subprocess.Popen([sys.executable, os.path.join(data, "app.py")],
                       env=dict(os.environ, ZET_PORT=str(port), ZET_NO_CONSOLE="1", ZET_NO_BROWSER="1", ZET_POLL="3600",
                                ZET_FEED_URL="http://127.0.0.1:9/x", ZET_STATIC_URL="http://127.0.0.1:9/x"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
try:
    got = None
    for _ in range(60):
        st_, body, _ = http("http://127.0.0.1:%d/health" % port, 1)
        if st_ == 200:
            got = json.loads(body)
            break
        time.sleep(0.2)
    check("the upgraded folder serves V8", got and got["version"] == 8 and got["port"] == port, got)
    st_, body, _ = http("http://127.0.0.1:%d/api/settings" % port, headers={"X-ZET": "1"})
    s = json.loads(body or b"{}")
    check("and its settings carry the Google key section and the map choice", st_ == 200 and "google" in s and s.get("map") == "osm", s)
finally:
    srv.terminate()
    srv.wait(5)

# the same updater, run again: nothing to do, nothing touched
r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    res = {"stderr": r.stderr[-300:]}
check("run again: already the newest, nothing changed", res.get("changed") is False, res)

# ---------------------------------------------------------------- the U key: V8 -> V9 while it runs
# GitHub now has a V9 whose page differs by one line. U, then y: the files change, the server comes
# back as the same process on the SAME port, and the open page keeps answering (ports.md §4, test 4).
V9_MARK = b"<!-- V9 -->"
v9_html = open(os.path.join(APP, "index.html"), "rb").read() + V9_MARK
v9_files = dict(manifest["files"], **{"index.html": hashlib.sha256(v9_html).hexdigest()})
SHA9 = "9" * 40
_orig = GH.do_GET


def do_get_v9(self):
    if self.path == "/repos/markoboskoauroville/zet_strike/commits/" + BRANCH:
        body = SHA9.encode()
    elif self.path == "/markoboskoauroville/zet_strike/%s/app/MANIFEST.json" % SHA9:
        body = json.dumps({"version": 9, "notes": "V9: one line", "files": v9_files}).encode()
    elif self.path == "/markoboskoauroville/zet_strike/%s/app/index.html" % SHA9:
        body = v9_html
    elif self.path.startswith("/markoboskoauroville/zet_strike/%s/app/" % SHA9):
        self.path = self.path.replace(SHA9, SHA)
        return _orig(self)
    else:
        return _orig(self)
    self.send_response(200)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


GH.do_GET = do_get_v9
bindir, opened = fake_opener()
port = free_port()
con = Console(console_env(dict(env, PATH=bindir + os.pathsep + os.environ["PATH"], ZET_PORT=str(port))), folder=data)
try:
    check("V8 runs from the upgraded folder, with its console", con.wait_for(r"\[Q\] stop", 20), con.text()[-300:])
    con.wait_for(r"opened in", 10)
    con.key("u")
    check("U: installed and available, and a question", con.wait_for(r"press y to update", 20) and "V8 installed   ->   V9 available" in con.text(), con.text()[-400:])
    con.buf = b""
    con.key("y")
    check("y: every file checked, then a restart", con.wait_for(r"restarting on the same port", 30), con.text()[-400:])
    check("the server is back on the SAME port", con.wait_for(r"\[Q\] stop", 20) and ("http://127.0.0.1:%d" % port) in con.text(), con.text()[-400:])
    st_, body, _ = http("http://127.0.0.1:%d/" % port, 3)
    check("and the page that was open answers with the V9 page", st_ == 200 and body.endswith(V9_MARK))
    check("the page was not opened a second time", len([ln for ln in open(opened).read().splitlines() if ln.strip()]) == 1)
    con.key("q")
    check("Q still stops it", con.done(8) == 0)
finally:
    con.kill()
gh.shutdown()

finish("test 4, the upgrade")
