# ZET Strike: handover

    version      V10 (30.9.2026)
    repository   markoboskoauroville/zet_strike (public)
    command      zet (the server; zet map is the same), zet now (the board in the terminal)
    port         8100, then the next fifteen, then any (MANTRA_MANIFEST ports.md)
    data         ~/.zet-strike   keys in ~/.zet-strike/secrets (0600)

## The request, word for word (30.9.2026)

> Zet strike We are working on this app. Please build the Flask server for it and see in manifest how
> to build Flask server, what are those, uh, what is our style of working and what kind of ask UART we
> are using. And see this screenshot for the example where we are now with this app and how we want it
> to look. Even if there are no vehicles running, I need to have my web interface with the maps and
> settings so I can enter the Gemini key and Google Map key, and we need to be able to test the keys.
> Everything is in the manifest. You need to read manifest to understand how to build my applications.

Two screenshots came with it: `zet` on day 3 with 0 in service, and MA READER's server console
(the name, a dashed rule, "on this phone" and "library", then [O] [A] [U] [Q]).

## V10 (30.9.2026): keys from files, with titles

Marko, 30.9.2026: *"Please upgrade that so it has a file picker for the keys and read how to parse
the files. It can have multiple keys and each key can have title. Where is it coming from? Read the
Mantra Manifest. Never in any future app you build API keys without file pickers. That's the
mandatory thing, always."*

- **Choose key files…** at the top of Settings, and **File…** beside both paste boxes: the phone's
  file dialog, several files at once, sent to `/api/keys/import` (2 MB a file; a picture is refused
  as "not a text file").
- **The parser is the Keyring's** (`app/keyparse.py`, KEYRING_TERMUX `ring.py`, keyring.md §4, §10d):
  the keyring v1 format first, then blocks split on blank lines, the key by shape and the title by
  elimination. `AQ.` goes to Gemini, `AIza` to Google Maps; other providers are counted, not kept.
- **Titles** (`app/labels.py`): kept by fingerprint in `secrets/key_labels.json`, 0600; Rename in
  the page. A title can never hold a key: the words beside a key on its own line are the title.
- **Google keys: several**, one in use by a radio (keyring.md §6), each with its own Test and
  Delete. The V7-V9 single key file is moved into the list, in use, with its last test.
- `zet keys import FILE...` does the same from Termux.

**Found by the tests, and it must go back to KEYRING_TERMUX:** its `_label_from` took a line that
holds the key (`google AIza...`) whole as the title, so the key was stored and shown as its own
name. Fixed here (`keyparse.py`); the source still has it, and this session cannot push there.

## V9 (30.9.2026): mobile data

Marko, 30.9.2026: *"this app unnecessarily downloads the stream from ZET every time it runs ... If
it's fresh enough ... you don't download it every second ... optimize my traffic. I'm working from
mobile phone internet."*

What V8 spent, and what V9 does instead (`app/net.py`, one door for every download):

| | V8 | V9 |
|---|---|---|
| live feed, server | every 20 s, day and night, page open or not | 20 s only while a page is ON SCREEN; none open: 5 min by day, 15 min 00:00 to 04:30 (Settings, Data) |
| live feed, `zet now` | 1 to 3 downloads every run | a copy under 20 s old (the server's or the last run's) is used; with 0 vehicles no movement sample |
| any feed request | the whole body, uncompressed | `If-Modified-Since`: unchanged is a 304 with no body; gzip |
| timetable, 13 MB | again whenever the copy was 6 h old; every `zet update`; every install | asked once a day with a HEAD (ETag, date, size); downloaded only when ZET changed it; `zet update timetable force` to insist |
| news, 8 RSS feeds | all of them every 10 min, always | `If-None-Match`/`If-Modified-Since`; hourly with no page open |
| a background Chrome tab | kept the server on the fast clock | asks only while the page is visible |

`zet data` and the Data card in Settings count what came over the network today, per source
(bodies only; headers, a few hundred bytes each, are not counted). A phone coming from V8 is not
charged the 13 MB again: its saved zip is matched to ZET's Content-Length by one HEAD.

**Not known yet:** whether zet.hr answers 304 and sends ETag / Last-Modified / gzip at all. The code
works either way (a copy is still used when young enough, and the HEAD falls back to the size), but
how much is saved per request is only measured against a stand-in, never against zet.hr, which the
cloud machine cannot reach. `zet data` on the phone will say.

## V8 (30.9.2026): `zet` alone is the server

Marko, 30.9.2026, typing `zet` and getting the board: *"Where is my Flask server and everything?
That should be the command which runs server."* The one word runs the app (`termux-app.md` §4), so
`zet` starts the server, `zet map` stays as a second name for it, and the terminal board is `zet now`.
Flags alone (`zet -a`) and line numbers alone (`zet 228`) still mean the board.

## What V7 is

The Termux app shape of the manifest (`termux-app.md`), on top of V6's engine, which is unchanged:

| part | from | what |
|---|---|---|
| `console.py` | MA READER's look, KEYRING_TERMUX's opener and keys | the banner and O A U R Q; plain lines, never a box |
| `portpick.py` | KEYRING_TERMUX, verbatim | 8100 then the next fifteen then any; the live registry `~/.mantra/ports/zet` |
| `localguard.py` | KEYRING_TERMUX, verbatim | Host, Origin/Referer, and the `X-ZET` header on every `/api/` call |
| `probes.py` | KEYRING_TERMUX, verbatim | the work probes: `gemini_probe`, `google_probe` |
| `mapkey.py` | new | the Google Maps key (env, 0600 file, `keyring get google`), its test, the tile proxy |
| `vendor.py` | new | Leaflet 1.9.4 fetched once, SHA-256 checked, served from the phone |
| `tools/manifest.py` | new | writes `app/MANIFEST.json`; `--check` fails when it is stale |
| `tests/` | new | the four tests, 78 checks |

## Decided, and why

- **Port 8100.** V6 used 8080, which the manifest reserves for the Shop Finder. A V6 config that
  says 8080 is moved to 8100 once, on first load.
- **127.0.0.1, not 0.0.0.0.** It holds keys (`termux-app.md` §7). A laptop on the same wifi cannot
  reach it; that is the point.
- **Waitress**, with the Flask dev server as a spoken fallback.
- **The Google key never reaches the page.** Tiles come through `/tile/google/z/x/y`, made with a Map
  Tiles session; the tile address carries a per-run token only the page can read, and the guard
  refuses a tile asked for by another site. A foreign page cannot spend the key.
- **For the map, the verdict is the Tiles line.** A key that works only for Places is a good key
  that cannot draw this map, and the page says exactly that ("enable the Map Tiles API").
- **The Gemini test does work**, one token, not a list call (`keyring.md` §2c). V6 listed models
  and called a spent account "ok". The test now costs a fraction of a cent per key.
- **Gemini keys are found by `AQ.`** (`keyring.md`); V6 looked for `AIza`, which is the Google Cloud
  (Maps) shape. A lone pasted token of any shape is still taken.
- **The page no longer dies without unpkg.** V6 loaded Leaflet from unpkg on every visit; with no
  signal the whole script stopped, settings included. Now the server keeps its own checked copy,
  and if even that is missing, every map call is a no-op and the rest of the page works.
- **The U key uses V6's updater** (MANIFEST.json checksums), not git: the phone install is not a
  checkout. After `y` the server restarts as the same process on the same port.
- **Branch installs.** `ZET_BRANCH` at install time; `VERSION.json` remembers it, so `zet update`
  and U follow that branch until reinstalled from `main`.
- **The page: added to, not changed** (`design-language.md`, "you add to it"). New: the favicon,
  the Google Maps key card, the map choice (OpenStreetMap, Google, my own server), "no vehicle
  reporting" in the status line, V7 at the foot of settings linking to this repository.

## NOT TESTED

Everything below is unproven until it runs on the phone:

- `pkg install`, `pip install waitress` in Termux, and waitress importing on the phone's Python
- `termux-open-url` with `com.android.chrome` opening real Chrome (a stand-in was on the PATH)
- `pm list packages` on the phone
- the page on the phone itself: at 390 px in headless Chromium it has no script error and nothing
  wider than the screen, but not at 250% text size and not with a thumb
- a real Google Maps key: only a made-up key against real Google (it answered "rejected", as it
  must). A key with the Map Tiles API enabled, and the Google map drawing, are not seen
- a real Gemini key through the new work probe
- the ZET feed and timetable: zet.hr is blocked from the cloud machine, so every test ran with no
  feed (which is also the case asked for: no vehicles, the page still whole)
- OpenStreetMap tiles in the browser (blocked here too)
- `install.sh` end to end; its Python part and `bash -n` only

## What is left

- Merge the branch to `main` once it has run on the phone; `main` is what `zet update` serves.
- The Gemini summary still names its models in config (`gemini-3.7-flash` …). The manifest says ask
  for the list (`model-self-repair.md`); the probe already does, the summary does not.
- Nine gates (`delivery-gate.md`) have not been run: this app has no `gates/` yet.
