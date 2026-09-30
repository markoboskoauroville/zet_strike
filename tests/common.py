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
