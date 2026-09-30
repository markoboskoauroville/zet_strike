#!/usr/bin/env python3
"""TEST 1 - each mechanism alone: the port picker, the guard's three checks, finding a key by its shape,
the key file's permissions and that no answer ever carries the key, Google's 200-while-denying
verdicts, and the map library's checksum."""
import os
import socket
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import APP, FakeZet, check, client, fake_key, finish, free_port, fresh_home  # noqa: E402

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
mode = stat.S_IMODE(os.stat(mapkey.KEYS_FILE).st_mode)
dmode = stat.S_IMODE(os.stat(mapkey.SECRET_DIR).st_mode)
check("the key file is 0600 and its folder 0700", mode == 0o600 and dmode == 0o700, (oct(mode), oct(dmode)))
st = mapkey.status()
check("status says where it came from and never carries the key", st["source"] == "this phone" and k not in str(st))
body = c.get("/api/settings", headers=H).get_data(as_text=True)
check("the settings answer never carries the key", k not in body and st["fp"] in body)
check("delete removes it", mapkey.delete() and mapkey.saved_keys() == [] and not mapkey.status()["has_key"])
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


# ---------------------------------------------------------------- key files: the Keyring parser, titles
import keyparse  # noqa: E402
import labels  # noqa: E402

ga, gb, mk = "AQ." + fake_key("Ab", 50, seed=1), "AQ." + fake_key("Ab", 50, seed=2), fake_key(seed=3)
note = """# keyring v1
provider: google
label: maps phone
key: %s

AV LIVE VMIX
%s

caffeteria
https://aistudio.google.com/app?srsltid=AfmBOoq123456789012345678901234
%s
cancelled 3.9.2026

claude
%s
""" % (mk, ga, gb, "sk-ant-api03-" + fake_key("", 90, seed=4))
got = [(e["provider"], e["label"]) for e in keyparse.parse(note, "keys.txt")]
check("the keyring v1 block, then each note block: provider by shape, title by elimination",
      got == [("google", "maps phone"), ("gemini", "AV LIVE VMIX"), ("gemini", "caffeteria"), ("anthropic", "claude")], got)
one = keyparse.parse("google maps: " + mk, "")
check("key and name on one line: the title is the words beside it, never the key", [e["label"] for e in one] == ["google maps"], [e["label"] for e in one])
check("a title given with a key in it is stored without the key", labels.set("fpx", "phone " + ga) == "phone")
check("a note with 'gemini' in it still files an AIza key as Google", keyparse.parse("gemini\n" + fake_key(seed=5), "")[0]["provider"] == "google")
r = labels.import_text(note, "keys.txt")
check("import: 2 Gemini and 1 Google key taken, the Anthropic one counted and left alone",
      r["gemini"] == 2 and r["google"] == 1 and r["other"] == {"anthropic": 1} and r["titled"] == 3, r)
check("the titles are stored beside the fingerprints", labels.get(news.fingerprint(ga)) == "AV LIVE VMIX" and labels.get(mapkey.fingerprint(mk)) == "maps phone")
lmode = stat.S_IMODE(os.stat(labels.LABELS_FILE).st_mode)
check("the titles file is 0600 (a title is often an account name)", lmode == 0o600, oct(lmode))
body = c.get("/api/settings", headers=H).get_data(as_text=True)
check("the settings answer carries the titles and never a key", "AV LIVE VMIX" in body and "maps phone" in body and not any(k in body for k in (ga, gb, mk)))
check("the summary is one sentence for a person", labels.summary(r) == "keys.txt: 2 Gemini keys, 1 Google Maps key added, 3 with a title; not used by this app: 1 anthropic.", labels.summary(r))
r2 = labels.import_text(note, "keys.txt")
check("the same file again: nothing new, three already here", r2["gemini"] == 0 and r2["google"] == 0 and r2["duplicates"] == 3, r2)
mapkey.add([fake_key(seed=6)])
st = mapkey.status()
check("two Google keys: the first stays in use until another is chosen", len(st["keys"]) == 2 and st["fp"] == mapkey.fingerprint(mk)
      and [k["active"] for k in st["keys"]] == [True, False])
check("choosing the other one", mapkey.select(st["keys"][1]["fp"]) and mapkey.status()["fp"] == st["keys"][1]["fp"])
check("choosing a key that is not here does nothing", not mapkey.select("nothere") and mapkey.status()["fp"] == st["keys"][1]["fp"])

# ---------------------------------------------------------------- traffic: nothing downloaded twice
import time  # noqa: E402
from datetime import datetime  # noqa: E402

import core  # noqa: E402
import net  # noqa: E402

fz = FakeZet(n_vehicles=2)
core.FEED_URL = fz.feed_url
f1 = core.fetch_feed(max_age=20)
check("the first feed comes over the network, gzip, and parses", f1["how"] == "net" and len(f1["vehicles"]) == 2
      and fz.count("GET", "/feed", 200) and f1["wire"] < len(fz.feed), (f1["how"], f1["wire"], len(fz.feed)))
f2 = core.fetch_feed(max_age=20)
check("asked again within 20 s: the copy on disk, no request at all", f2["how"] == "cache" and len(fz.count("GET", "/feed")) == 1
      and len(f2["vehicles"]) == 2)
f3 = core.fetch_feed(max_age=0)
check("older than wanted, unchanged at ZET: a 304, no body", f3["how"] == "same" and fz.count("GET", "/feed", 304) and len(f3["vehicles"]) == 2)
fz.new_feed(n_vehicles=3)
f4 = core.fetch_feed(max_age=0)
check("changed at ZET: the new feed comes", f4["how"] == "net" and len(f4["vehicles"]) == 3)
t = net.today()["feed"]
check("today's tally counts what came over the wire, not what was used", t["bytes"] == fz.body_bytes("/feed") and t["net"] == 2
      and t["same"] == 1 and t["cache"] == 1, (t, fz.body_bytes("/feed")))
fz.stop()

cfg = core.load_config()
z = lambda h, m=0: datetime(2026, 9, 30, h, m)
check("the server's clock: 20 s with a page open, day or night", core.feed_interval(cfg, True, z(14)) == 20 and core.feed_interval(cfg, True, z(2)) == 20)
check("no page open: 5 min by day", core.feed_interval(cfg, False, z(14)) == 300 and core.feed_interval(cfg, False, z(4, 30)) == 300)
check("no page open at night (00:00 to 04:30): 15 min", core.feed_interval(cfg, False, z(0, 5)) == 900 and core.feed_interval(cfg, False, z(4, 29)) == 900)

same = core._static_same
check("the timetable is unchanged when ETag, date and size agree", same({"etag": "a", "modified": "m", "length": "9"}, {"etag": "a", "modified": "m", "length": "9", "url": "u"}))
check("changed when the ETag differs", not same({"etag": "a", "length": "9"}, {"etag": "b", "length": "9"}))
check("changed when 'latest' points at another file", not same({"url": "x/396.zip", "length": "9"}, {"url": "x/397.zip", "length": "9"}))
check("unknown (never 'same') when the server says nothing", not same({"etag": "a"}, {}))

finish("test 1, the mechanisms")
