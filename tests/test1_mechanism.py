#!/usr/bin/env python3
"""TEST 1 - each mechanism alone: the port picker, the guard's three checks, finding a key by its shape,
the key file's permissions and that no answer ever carries the key, Google's 200-while-denying
verdicts, and the map library's checksum."""
import os
import socket
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import APP, check, client, fake_key, finish, free_port, fresh_home  # noqa: E402

fresh_home()
sys.path.insert(0, APP)

# ---------------------------------------------------------------- portpick
import portpick  # noqa: E402

p = free_port()
check("a port nothing listens on is free", portpick.is_free("127.0.0.1", p))
s = socket.socket()
s.bind(("127.0.0.1", p))
s.listen(1)
check("a listening port is taken", not portpick.is_free("127.0.0.1", p))
got, note = portpick.pick("127.0.0.1", p)
check("pick moves to the next port and says so", got != p and note and str(got) in note, (got, note))
s.close()

# ---------------------------------------------------------------- the guard
appmod, c, H = client()
check("the page answers", c.get("/", headers={"Host": H["Host"]}).status_code == 200)
r = c.get("/api/settings", headers={"Host": "evil.example:%d" % appmod.LIVE_PORT, "X-ZET": "1"})
check("a foreign Host is refused (DNS rebinding)", r.status_code == 403)
r = c.get("/api/settings", headers={"Host": H["Host"], "X-ZET": "1", "Origin": "https://evil.example"})
check("a foreign Origin is refused", r.status_code == 403)
r = c.get("/api/settings", headers={"Host": H["Host"]})
check("an /api/ call without the page's header is refused", r.status_code == 403)
check("with all three, it answers", c.get("/api/settings", headers=H).status_code == 200)
r = c.get("/tile/google/1/0/0?t=" + appmod.TILE_TOKEN, headers={"Host": H["Host"], "Referer": "https://evil.example/page"})
check("a tile asked for by another site is refused (the Maps key is not spent for it)", r.status_code == 403)

# ---------------------------------------------------------------- the Google key: shape, file, never shown
import mapkey  # noqa: E402

k = fake_key()
check("a key is found inside a labelled note", mapkey.extract("my maps key: %s (restricted)" % k) == k)
check("nothing key-shaped, nothing found", mapkey.extract("hello there") is None)
r = mapkey.save("google " + k)
check("save answers the fingerprint, not the key", r.get("saved") and r["fp"] == mapkey.fingerprint(k) and k not in str(r))
mode = stat.S_IMODE(os.stat(mapkey.KEY_FILE).st_mode)
dmode = stat.S_IMODE(os.stat(mapkey.SECRET_DIR).st_mode)
check("the key file is 0600 and its folder 0700", mode == 0o600 and dmode == 0o700, (oct(mode), oct(dmode)))
st = mapkey.status()
check("status says where it came from and never carries the key", st["source"] == "this phone" and k not in str(st))
body = c.get("/api/settings", headers=H).get_data(as_text=True)
check("the settings answer never carries the key", k not in body and st["fp"] in body)
check("delete removes it", mapkey.delete() and not os.path.exists(mapkey.KEY_FILE) and not mapkey.status()["has_key"])
os.environ["ZET_GOOGLE_KEY"] = k
check("the environment is asked first (keyring.md §11)", mapkey.current() == (k, "environment"))
del os.environ["ZET_GOOGLE_KEY"]

# ---------------------------------------------------------------- the Gemini key shape
import news  # noqa: E402

g = "AQ." + fake_key("Ab", 60)
check("a Gemini key is found by its AQ. shape inside a note", news.extract_keys("gemini: %s\n" % g) == [g])

# ---------------------------------------------------------------- Google's verdicts
import probes  # noqa: E402

denied = b'{"status":"REQUEST_DENIED","error_message":"This API project is not authorized to use this API."}'
check("200 with REQUEST_DENIED is 'not enabled', never 'works'", probes.google_verdict(200, denied)[0] == "not enabled")
check("200 OK is 'works'", probes.google_verdict(200, b'{"status":"OK"}')[0] == "works")
check("'API key not valid' is 'rejected'", probes.google_verdict(400, b'{"error":{"message":"API key not valid. Please pass a valid API key."}}')[0] == "rejected")
check("billing is 'no credit', not rejected", probes.google_verdict(403, b'{"error":{"message":"This API method requires billing to be enabled."}}')[0] == "no credit")

# ---------------------------------------------------------------- the map library's checksum
import vendor  # noqa: E402

check("a map library that fails its checksum is not accepted", not vendor._ok("leaflet.js", b"console.log('not leaflet')"))
check("only the two known files are served", vendor.get("../secrets/google_key") is None and vendor.get("other.js") is None)

finish("test 1, the mechanisms")
