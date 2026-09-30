"""Shared by the four tests (MANTRA_MANIFEST four-tests.md): a throwaway data folder, the Flask test
client with the guard satisfied the way the page satisfies it, the real server on a pty through its
real entry point, a stand-in termux-open-url that writes down what it was asked to open, and a fake
Google tile server for the cases the real one cannot be made to produce. Shape from
KEYRING_TERMUX/tests/common.py."""
import json
import os
import pty
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "app")
PY = sys.executable
fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", label, ("  " + str(detail)[:400]) if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(label)


def skipped(label, why):
    """A check that did not run never wears the clothes of one that passed (ports.md §4)."""
    print("  --    %s  (did not run: %s)" % (label, why), flush=True)


def finish(name):
    print()
    print("%s: %d checks, %d failed" % (name, count, len(fails)))
    for f in fails:
        print("  - " + f)
    sys.exit(1 if fails else 0)


def fresh_home():
    """A data folder and a HOME of its own, so the registry and the keys never touch the real ones."""
    home = tempfile.mkdtemp(prefix="zet-home-")
    os.environ["HOME"] = home
    os.environ["ZET_STRIKE_DIR"] = os.path.join(home, ".zet-strike")
    os.environ["ZET_NO_KEYRING"] = "1"
    os.environ.pop("ZET_GOOGLE_KEY", None)
    os.environ.pop("ZET_PORT", None)
    return home


def client():
    """The Flask test client, with the guard satisfied the way the page satisfies it."""
    sys.path.insert(0, APP)
    for m in ("app", "core", "news", "mapkey", "probes", "localguard", "portpick", "vendor", "update", "console"):
        sys.modules.pop(m, None)
    import app as appmod
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    H = {"Host": "127.0.0.1:%d" % appmod.LIVE_PORT, "X-ZET": "1", "Origin": "http://127.0.0.1:%d" % appmod.LIVE_PORT}
    return appmod, c, H


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(url, timeout=5, headers=None, data=None):
    try:
        req = urllib.request.Request(url, headers=headers or {}, data=data)
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310: the test's own 127.0.0.1 server
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:                                  # noqa: BLE001
        return None, repr(e).encode(), {}


def reachable(host, port=443, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        st, body, _ = http("https://%s/" % host, timeout=timeout)
        return st is not None
    except OSError:
        return False


def fake_opener():
    """A termux-open-url on the PATH that writes each call's arguments to a file (termux-app.md §12)."""
    d = tempfile.mkdtemp(prefix="zet-bin-")
    log = os.path.join(d, "opened.txt")
    path = os.path.join(d, "termux-open-url")
    with open(path, "w") as f:
        f.write('#!/bin/sh\necho "$@" >> "%s"\n' % log)
    os.chmod(path, 0o755)
    pm = os.path.join(d, "pm")                    # Chrome is installed, as on Marko's phone
    with open(pm, "w") as f:
        f.write('#!/bin/sh\necho "package:com.android.chrome"\n')
    os.chmod(pm, 0o755)
    return d, log


def read_lines(path):
    try:
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return []


class Console:
    """app.py on a real pty, through its real entry point, so the console sees a terminal."""

    def __init__(self, env, folder=APP, script="app.py", args=()):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(folder)
            os.execvpe(PY, [PY, os.path.join(folder, script)] + list(args), env)
        self.buf = b""

    def read(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.buf += chunk
        return self.text()

    def wait_for(self, pattern, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            if re.search(pattern, self.text()):
                return True
            self.read(0.3)
        return False

    def text(self):
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", self.buf.decode("utf-8", "replace"))

    def key(self, ch):
        os.write(self.fd, ch.encode())

    def done(self, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            self.read(0.2)
        return None

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


def console_env(extra=None):
    env = dict(os.environ)
    env.update({"TERM": "xterm-256color", "COLUMNS": "44", "LINES": "30", "ZET_POLL": "3600",
                "ZET_FEED_URL": "http://127.0.0.1:9/none", "ZET_STATIC_URL": "http://127.0.0.1:9/none"})
    env.update(extra or {})
    return env


# ---------------------------------------------------------------- a fake Google tile server
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeTiles:
    """createSession and 2dtiles, with the key and session checked, so the proxy is exercised for real.
    `expire_next` makes the next tile answer 403 as an ended session does."""

    def __init__(self, key):
        outer = self
        self.key, self.sessions, self.calls, self.expire_next = key, 0, [], False

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                outer.calls.append(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if not self.path.startswith("/v1/createSession") or ("key=" + outer.key) not in self.path:
                    return self._send(400, b'{"error":{"message":"API key not valid"}}')
                outer.sessions += 1
                body = json.dumps({"session": "S%d" % outer.sessions, "expiry": str(int(time.time()) + 86400)}).encode()
                self._send(200, body)

            def do_GET(self):
                outer.calls.append(self.path)
                if not self.path.startswith("/v1/2dtiles/"):
                    return self._send(404, b"{}")
                if outer.expire_next:
                    outer.expire_next = False
                    return self._send(403, b'{"error":{"message":"session expired"}}')
                if ("session=S%d" % outer.sessions) not in self.path or ("key=" + outer.key) not in self.path:
                    return self._send(403, b'{"error":{"message":"bad session"}}')
                self._send(200, PNG, "image/png")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.srv.shutdown()


def fake_key(prefix="AIza", n=35, seed=7):
    """A key of the right shape that no provider will accept. Never a real key in a test (secrets.md)."""
    import random
    r = random.Random(seed)
    return prefix + "".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") for _ in range(n))


def run_quiet(cmd, env=None, timeout=60):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------- a fake ZET: the live feed and the timetable
def gtfs_zip(version="000396", pad=0):
    """A small timetable in ZET's shape: one tram line, three stops, a feed_info version. `pad` adds
    bytes (a stored file) so a changed timetable can also differ in size."""
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("feed_info.txt", "feed_publisher_name,feed_version\nZET,%s\n" % version)
        z.writestr("routes.txt", "route_id,route_short_name,route_long_name,route_type\n6,6,Crnomerec - Sopot,0\n")
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n1,Crnomerec,45.8150,15.9350\n2,Trg bana Jelacica,45.8130,15.9770\n3,Sopot,45.7800,15.9900\n")
        trips = "route_id,service_id,trip_id,trip_headsign,shape_id\n" + "".join("6,W,T%d,Sopot,S1\n" % i for i in range(4))
        z.writestr("trips.txt", trips)
        st = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        for i in range(4):
            h = 6 + i
            st += "T%d,%02d:00:00,%02d:00:00,1,1\nT%d,%02d:10:00,%02d:10:00,2,2\nT%d,%02d:20:00,%02d:20:00,3,3\n" % (i, h, h, i, h, h, i, h, h)
        z.writestr("stop_times.txt", st)
        z.writestr("calendar.txt", "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nW,1,1,1,1,1,1,1,20260101,20271231\n")
        if pad:
            z.writestr(zipfile.ZipInfo("pad.bin"), os.urandom(pad))
    return b.getvalue()


def rt_feed(n_vehicles=0, ts=None):
    from google.transit import gtfs_realtime_pb2
    m = gtfs_realtime_pb2.FeedMessage()
    m.header.gtfs_realtime_version = "2.0"
    m.header.timestamp = int(ts or time.time())
    for i in range(n_vehicles):
        e = m.entity.add()
        e.id = "v%d" % i
        e.vehicle.vehicle.id = "V%d" % i
        e.vehicle.trip.trip_id = "T%d" % i
        e.vehicle.trip.route_id = "6"
        e.vehicle.position.latitude = 45.813
        e.vehicle.position.longitude = 15.977 + i * 0.001
        e.vehicle.timestamp = m.header.timestamp
    # ZET's real feed carries many trip updates; pad with them so gzip has something to save
    for i in range(200):
        e = m.entity.add()
        e.id = "tu%d" % i
        e.trip_update.trip.trip_id = "X%d" % i
        s = e.trip_update.stop_time_update.add()
        s.stop_sequence = 1
        s.arrival.delay = 0
    return m.SerializeToString()


class FakeZet:
    """/feed (GTFS-RT, gzip when asked, 304 on an unchanged Last-Modified) and /static (the zip: HEAD with
    ETag, Last-Modified and Content-Length, GET with 304 on If-None-Match). Every request is written down
    with the bytes of body it sent, so a test measures traffic rather than trusting the code."""

    def __init__(self, n_vehicles=0):
        import email.utils
        import gzip as _gz
        outer = self
        self.log = []                       # (method, path, status, body bytes)
        self.feed_changed = time.time()
        self.feed = rt_feed(n_vehicles)
        self.set_static(gtfs_zip())
        self.honour_conditionals = True

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None, head=False):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)) if not head or code != 200 else (headers or {}).get("X-Len", str(len(body))))
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                outer.log.append((self.command, self.path.split("?")[0], code, 0 if head else len(body)))

            def do_HEAD(self):
                if self.path.startswith("/static"):
                    self.send_response(200)
                    self.send_header("ETag", outer.static_etag)
                    self.send_header("Last-Modified", outer.static_mod)
                    self.send_header("Content-Length", str(len(outer.static)))
                    self.end_headers()
                    outer.log.append(("HEAD", "/static", 200, 0))
                else:
                    self._send(404, b"", head=True)

            def do_GET(self):
                if self.path.startswith("/feed"):
                    mod = email.utils.formatdate(int(outer.feed_changed), usegmt=True)
                    if outer.honour_conditionals and self.headers.get("If-Modified-Since") == mod:
                        return self._send(304)
                    body, h = outer.feed, {"Last-Modified": mod, "Content-Type": "application/octet-stream"}
                    if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                        body, h["Content-Encoding"] = _gz.compress(body), "gzip"
                    return self._send(200, body, h)
                if self.path.startswith("/static"):
                    if outer.honour_conditionals and self.headers.get("If-None-Match") == outer.static_etag:
                        return self._send(304)
                    return self._send(200, outer.static, {"ETag": outer.static_etag, "Last-Modified": outer.static_mod,
                                                          "Content-Type": "application/zip"})
                if self.path.startswith("/rss"):
                    rss = b'<?xml version="1.0"?><rss><channel><item><title>ZET strajk traje</title><link>http://x/1</link></item></channel></rss>'
                    if self.headers.get("If-None-Match") == '"rss1"':
                        return self._send(304)
                    return self._send(200, rss, {"ETag": '"rss1"', "Content-Type": "application/rss+xml"})
                self._send(404, b"")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def set_static(self, data):
        import email.utils
        self.static = data
        self.static_etag = '"%s"' % __import__("hashlib").sha1(data).hexdigest()[:12]
        self.static_mod = email.utils.formatdate(time.time(), usegmt=True)

    def new_feed(self, n_vehicles=0):
        self.feed = rt_feed(n_vehicles)
        self.feed_changed = time.time() + 1

    @property
    def feed_url(self):
        return "http://127.0.0.1:%d/feed" % self.port

    @property
    def static_url(self):
        return "http://127.0.0.1:%d/static" % self.port

    def count(self, method=None, path=None, status=None):
        return [x for x in self.log if (method is None or x[0] == method) and (path is None or x[1] == path)
                and (status is None or x[2] == status)]

    def body_bytes(self, path):
        return sum(x[3] for x in self.log if x[1] == path)

    def stop(self):
        self.srv.shutdown()
