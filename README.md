# zet_strike

ZET Strike: a Termux app that follows Zagreb public transport during the
ZET and Zagrebački holding strike (from 28.09.2026). Mantra Productions.

## How to install

On the phone, in Termux:

    curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/zet_strike/main/install.sh | bash

To try a branch before it reaches `main` (`zet update` then follows that branch):

    curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/zet_strike/BRANCH/install.sh | ZET_BRANCH=BRANCH bash

For GPS in `zet near`, also install the Termux:API app from F-Droid.

## Commands

    zet                  what runs now, with next stops
    zet near             where and when to catch the next tram or bus
    zet lines            lines in service: names, terminals
    zet line 228         one line with all its stops
    zet news             strike headlines, Gemini summary and timeline
    zet log              event log of everything the feed and news did
    zet keys             Gemini keys, used in order with fallback
    zet watch            live view
    zet update           newest version from this repo, then the timetable
    zet update check     only look, change nothing
    zet keys google      the Google Maps key: paste, test, del
    zet map              the server: the page in Chrome, O A U R Q

## How updating works

`zet update` asks GitHub for the newest commit on `main`, downloads each file
in `app/` from raw.githubusercontent.com at that commit, and checks every
file against the SHA-256 in `app/MANIFEST.json`. Nothing changes unless every
file verifies. The running version is kept in `~/.zet-strike/backup/`
(last five).

To release: change files in `app/`, regenerate `app/MANIFEST.json`, bump
`version` when it is a new whole version, push to `main`.

## Data

Live positions: ZET GTFS-RT feed. Stops, lines and timetables: ZET GTFS
timetable. News: public RSS feeds of Croatian outlets. Gemini keys stay on
the phone in `~/.zet-strike/secrets/` with closed permissions and are never
printed.

## The server (`zet map`)

    ZET STRIKE  server
    ----------------------------------------
     on this phone  http://127.0.0.1:8100
     library        ~/.zet-strike
     version        V7
    ----------------------------------------
     [O] open in Chrome
     [A] open in the default browser
     [U] update the app
     [R] restart
     [Q] stop
    ----------------------------------------

Port 8100 is ZET Strike's own (MANTRA_MANIFEST `ports.md`); when it is taken the server takes the
next free one and says which. It listens on 127.0.0.1 only, because it holds keys, and every `/api/`
call needs the page's own header. The page is whole even when no vehicle is in the feed or the feed
cannot be reached: map, near me, lines, news, log and settings.

## Keys

Settings in the page, or `zet keys` in Termux.

- **Gemini** (for the news summary): several keys, used in order with fallback. Gemini keys begin `AQ.`.
- **Google Maps** (for the Google map): one key, beginning `AIza`. The map needs the **Map Tiles API**
  enabled for the key's project. The tiles come through this app, so the key never goes into the page.

**Test** asks each provider for real work, not only whether the key exists: one token from Gemini;
a map session, a place, a geocode from Google. The answer is one of: works, valid, no credit,
rejected, throttled, unclear; for Google, also per API (not enabled, restricted).
Keys stay on the phone in `~/.zet-strike/secrets/` (0600) and are shown only by fingerprint.
If the Keyring app is installed, `keyring get google` is asked when no key is saved here.

## Tests

    python3 tests/run_all.py       the four tests, before every push
    python3 tools/manifest.py --check

