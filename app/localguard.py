"""
localguard.py  --  no passwords, and still not open to the internet.

Copied verbatim from KEYRING_TERMUX/localguard.py (30.9.2026), the header name and the open
endpoints changed. ZET Strike holds the Gemini keys and the Google Maps key, so all three checks
apply to every /api/ call, GET included: a page open in another tab must not be able to read a key
or spend one. The map tiles are not /api/ (an <img> cannot send a header), so they pass on the
Host and Origin/Referer checks alone, which is what stops a foreign page spending the Maps key.

Maha Transcribe now has one real POST endpoint (/api/optimize-audio, the
ffmpeg pipeline), so the guard grew back to the shape GDRIVE_DOWNLOADER_FLASK_MACOS
uses. A GET-only guard would miss the actual new risk:

    any site you visit can make your browser send requests to
    http://127.0.0.1:8420 in the background. A POST that runs ffmpeg on
    whatever bytes it is handed is real CPU and real disk, on YOUR machine,
    and the Host header alone does not stop it -- Host reflects where the
    browser is actually connecting, which is correctly 127.0.0.1 even for a
    cross-site fetch() from a page you merely have open in another tab.

Three checks, same as the reference, and the same reasons:

    1  HOST         must be a loopback name. Catches DNS REBINDING: a site
                    points a hostname at 127.0.0.1 after the fact, so the
                    connection really is local but the browser still sends
                    that site's own Host header
    2  ORIGIN       if the request carries an Origin or Referer, it must be
                    this app. A browser attaches Origin to every cross-site
                    POST, so a fetch from someone else's page is refused
                    on sight
    3  FETCH SITE   a custom header this page always sends and a cross-site
                    request cannot. A simple form POST cannot set custom
                    headers at all, and a fetch that tries triggers a CORS
                    preflight that never gets an allow -- only same-origin
                    JavaScript can set it

Read-only page loads (GET on /) pass with checks 1 and 2 only, since typing
the address in yourself sends no Origin and no custom header. Anything that
changes something or costs real work -- currently just the ffmpeg endpoint
-- needs all three.

Ported from markoboskoauroville/GDRIVE_DOWNLOADER_FLASK_MACOS/localguard.py.
"""

from flask import jsonify, request

# Loopback by every name it answers to.
# NOT 0.0.0.0. bandit flagged it in the original and it was right to: 0.0.0.0
# means "every interface", so accepting it as a loopback name would let a
# request that arrived from the network past the check.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

# The header the page sends on every API call. Its VALUE does not matter;
# its presence does, because a cross-site request cannot set it.
GUARD_HEADER = "X-ZET"

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Endpoints reachable before anything else, and harmless: the page itself
# and its static assets. Nothing here changes state or costs real work.
OPEN_ENDPOINTS = {"static", "favicon", "favicon_ico", "index", "health", "tile"}


def _host_ok(host_header):
    if not host_header:
        return False
    host = host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header
    if host.startswith("[") and "]" in host:
        host = host[:host.index("]") + 1]
    return host in LOCAL_HOSTS


def _origin_ok(origin, port):
    """An Origin that is present must be this app."""
    if not origin:
        return True                      # absent is fine: same-origin GET, curl
    allowed = set()
    for host in ("127.0.0.1", "localhost", "[::1]"):
        allowed.add(f"http://{host}:{port}")
    return origin.rstrip("/") in allowed


def check(port):
    """Returns None to allow, or a (body, status) to refuse.

    Called from a before_request hook. Deliberately small: a guard nobody
    can read is a guard nobody can check.
    """
    if not _host_ok(request.headers.get("Host", "")):
        return (jsonify({
            "error": "This app only answers to 127.0.0.1. The address used "
                     "to reach it was different, which is what a "
                     "DNS-rebinding attack looks like. Open "
                     "http://127.0.0.1:%d instead." % port
        }), 403)

    origin = request.headers.get("Origin") or ""
    if not origin:
        ref = request.headers.get("Referer") or ""
        if ref:
            parts = ref.split("/")
            origin = "/".join(parts[:3]) if len(parts) >= 3 else ""
    if not _origin_ok(origin, port):
        return (jsonify({
            "error": "That request came from another web page, so it was "
                     "refused. Nothing was changed."
        }), 403)

    # Anything that changes something or costs real work needs the header
    # only this page can send.
    changes = request.method not in SAFE_METHODS
    is_api = (request.path or "").startswith("/api/")
    if (changes or is_api) and request.endpoint not in OPEN_ENDPOINTS:
        if not request.headers.get(GUARD_HEADER):
            return (jsonify({
                "error": "That request did not come from this app's own "
                         "page. Nothing was changed."
            }), 403)
    return None
