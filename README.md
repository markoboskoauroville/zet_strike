# zet_strike

ZET Strike: a Termux app that follows Zagreb public transport during the
ZET and Zagrebački holding strike (from 28.09.2026). Mantra Productions.

## Install on the phone (Termux)

    curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/zet_strike/main/install.sh | bash

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
    zet map                the same as a map page in Chrome

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
