#!/data/data/com.termux/files/usr/bin/python
"""tiles.py - ZET Strike V11: map tiles through this app, kept on the phone.

WHY. Marko's phone, 30.9.2026: every OpenStreetMap tile came back "403 Access blocked. App is not
following the tile usage policy of OpenStreetMap's volunteer-run servers". V7 had set
Referrer-Policy: same-origin, so Chrome sent tile.openstreetmap.org no Referer, and OSM refuses
browser tile requests that do not say where they come from. Their policy asks an app to identify
itself, and to cache.

WHAT. The page asks /tile/<source>/z/x/y; this server fetches it once with a User-Agent that names
the app and where to reach it, keeps it in ~/.zet-strike/tiles/, and serves the copy for 30 days.
A map looked at twice costs one download (the mobile-data rule of termux-app.md §13), and a street
seen once is still there with no signal. Google's tiles (roadmap and satellite) go through
mapkey.tile, which holds the key; they are cached here the same way.

    osm        OpenStreetMap, the map                       tile.openstreetmap.org
    esri       Esri World Imagery, satellite without a key  server.arcgisonline.com
    google     Google roadmap (a Google Maps key)
    googlesat  Google satellite (a Google Maps key)
"""
import os
import threading
import time
import urllib.error
import urllib.request

import core

DIR = os.path.join(core.APP_DIR, "tiles")
KEEP = 30 * 86400
UA = "ZETStrike/11 (personal Termux app; +https://github.com/markoboskoauroville/zet_strike)"
SOURCES = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}
URL_BASE = {k: os.environ.get("ZET_TILE_%s" % k.upper(), v) for k, v in SOURCES.items()}   # tests point these home
MAX_ZOOM = {"osm": 19, "esri": 19, "google": 22, "googlesat": 22, "googleterrain": 15}
_busy = threading.Semaphore(4)     # OSM's policy: no more than a couple of connections; the page asks many at once


def _path(src, z, x, y):
    return os.path.join(DIR, src, str(z), str(x), "%d.img" % y)


def get(src, z, x, y):
    """(status, bytes, content type, how): how is cache, net or stale (a copy kept when the network failed)."""
    if src not in MAX_ZOOM or not (0 <= z <= MAX_ZOOM[src] and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return 404, b"", "text/plain", "none"
    p = _path(src, z, x, y)
    ctype = "image/jpeg" if src in ("esri", "googlesat") else "image/png"
    try:
        age = time.time() - os.path.getmtime(p)
        if age < KEEP:
            with open(p, "rb") as f:
                return 200, f.read(), ctype, "cache"
    except OSError:
        age = None
    import net
    with _busy:
        if src.startswith("google"):
            import mapkey
            code, data, ctype2 = mapkey.tile(z, x, y, {"googlesat": "satellite", "googleterrain": "terrain"}.get(src, "roadmap"))
        else:
            url = URL_BASE[src].format(z=z, x=x, y=y)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/png,image/jpeg,image/*"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    code, data, ctype2 = 200, r.read(2 * 1024 * 1024), r.headers.get("Content-Type", ctype)
            except urllib.error.HTTPError as e:
                code, data, ctype2 = e.code, b"", "text/plain"
            except Exception:
                code, data, ctype2 = 504, b"", "text/plain"
    if code == 200 and data:
        net.count("tiles", len(data), "net")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
        return 200, data, ctype2 if ctype2.startswith("image/") else ctype, "net"
    net.count("tiles", 0, "fail")
    if age is not None:                      # older than 30 days, and the network said no: the old copy
        with open(p, "rb") as f:
            return 200, f.read(), ctype, "stale"
    return code, data, "text/plain", "none"
