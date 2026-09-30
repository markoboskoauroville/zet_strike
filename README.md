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

    zet                  the server: map, near me, lines, news, log, settings in Chrome
    zet now              what runs now, with next stops, in the terminal
    zet near             where and when to catch the next tram or bus
    zet lines            lines in service: names, terminals
    zet line 228         one line with all its stops
    zet news             strike headlines, Gemini summary and timeline
    zet log              event log of everything the feed and news did
    zet keys             Gemini keys, used in order with fallback
    zet watch            live view
    zet data             what came over the network today
    zet update           newest version from this repo; asks whether the timetable changed
    zet update check     only look, change nothing
    zet keys google      the Google Maps key: paste, test, del
    zet map              the same as zet

## Mobile data

Every download goes through one copy on disk, shared by the server and every `zet` command. A copy
young enough is used without asking; older, ZET is asked "has it changed?" and an unchanged answer
costs no body. The server asks for the live feed every 20 s only while its page is on screen; with
no page open, every 5 minutes by day and 15 at night (Settings, Data). The 13 MB timetable is
checked once a day and downloaded only when ZET changes it. `zet data` shows today's use.

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

## The server (`zet`)

    ZET STRIKE  server
    ----------------------------------------
     on this phone  http://127.0.0.1:8100
     library        ~/.zet-strike
     version        V9
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

**From a file:** Settings, **Choose key files…** (the phone's own file dialog, several files at
once; a **File…** button also sits beside each paste box), or `zet keys import FILE...` in Termux.
Your key notes as they are: every Gemini key (`AQ.`) and Google Maps key (`AIza`) in them is found
by its shape, and the name written above it becomes its title. The Keyring app's export
(`# keyring v1`, `provider:` / `label:` / `key:`) is read too. Anything else in the note is left
alone; keys this app has no use for are counted and not kept.

    AV LIVE VMIX
    AQ.Ab8RN6...

    caffeteria
    AQ.Ab8RN6...

Every key has a title; **Rename** changes it. Paste boxes and `zet keys` still work.

- **Gemini** (for the news summary): several keys, used in order with fallback. Gemini keys begin `AQ.`.
- **Google Maps** (for the Google map): several keys may be kept, a radio chooses the one in use.
  They begin `AIza`. The map needs the **Map Tiles API** enabled for the key's project. The tiles
  come through this app, so the key never goes into the page.

**Test** asks each provider for real work, not only whether the key exists: one token from Gemini;
a map session, a place, a geocode from Google. The answer is one of: works, valid, no credit,
rejected, throttled, unclear; for Google, also per API (not enabled, restricted).
Keys stay on the phone in `~/.zet-strike/secrets/` (0600) and are shown only by fingerprint.
If the Keyring app is installed, `keyring get google` is asked when no key is saved here.

## Tests

    python3 tests/run_all.py       the four tests, before every push
    python3 tools/manifest.py --check

