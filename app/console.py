#!/data/data/com.termux/files/usr/bin/python
"""console.py - ZET Strike V8: the terminal side of `zet`, the server.

The look is MA READER's server console (Marko's screenshot, 30.9.2026): the name, a dashed rule,
where the page is and where the data lives, a dashed rule, one key per line, a dashed rule.
Plain lines, never a box (never-back-to-zero.md §4): a box drawn to a fixed inner width silently
loses its right-hand side on a narrow phone terminal, and the key that falls off is the one needed.

    [O]  open in Chrome                 whatever the phone's default browser is (termux-app.md §5)
    [A]  open in the default browser
    [U]  update the app                 check GitHub, show installed -> available, y to go
    [R]  restart
    [Q]  stop

The opener and the keys are KEYRING_TERMUX/console.py's (open_page, has_package, the confirm
latch), with ZET's own updater behind U: update.py, which checks every file's SHA-256 against
the repo's MANIFEST.json before anything is replaced.

DEGRADES HONESTLY. With no terminal there is no key to press: it prints the address and serves.
"""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

GOLD = "\033[38;5;221m"
AMBER = "\033[38;5;214m"
INK = "\033[38;5;255m"
GREY = "\033[38;5;245m"
RULE = "\033[38;5;240m"
RED = "\033[38;5;203m"
OFF = "\033[0m"

CHROME = ("com.android.chrome", "com.chrome.beta", "com.chrome.dev", "com.chrome.canary")
_HAS_PKG = {}


def say(line=""):
    print(line, flush=True)


def is_interactive():
    return sys.stdout.isatty() and sys.stdin.isatty() and not os.environ.get("ZET_NO_CONSOLE")


def c(text, colour, on):
    return colour + text + OFF if on else text


# ---------------------------------------------------------------- the page opener
def has_package(pkg):
    """Is an Android package installed? Cached: the answer does not change while the app runs."""
    if pkg not in _HAS_PKG:
        try:
            r = subprocess.run(["pm", "list", "packages", pkg], capture_output=True, text=True, timeout=10)
            _HAS_PKG[pkg] = ("package:" + pkg) in r.stdout.split()
        except Exception:
            _HAS_PKG[pkg] = False
    return _HAS_PKG[pkg]


def chrome():
    for pkg in CHROME:
        if has_package(pkg):
            return pkg
    return None


def open_page(url, want_chrome=True):
    """Chrome first when asked (Marko, 13.9.2026: every terminal app opens in Chrome), else the
    default browser; on a Mac `open`, on Linux `xdg-open`. Bounded by timeout(1): a Termux:API call
    that never returns is an orphan Android counts against the phantom-process limit.
    Returns what was used, or None when nothing could be started."""
    chain = []
    if shutil.which("termux-open-url"):
        pkg = chrome() if want_chrome else None
        if pkg:
            chain.append((["termux-open-url", url, pkg], "Chrome"))
        chain.append((["termux-open-url", url], "the default browser"))
    for other in ("open", "xdg-open"):
        if shutil.which(other):
            chain.append(([other, url], "the default browser"))
    for cmd, name in chain:
        if shutil.which("timeout"):
            cmd = ["timeout", "-k", "5", "30"] + cmd
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return name
        except Exception:
            continue
    return None


def wait_for_port(port, seconds=20):
    """Not a timer: connect for real, then open (termux-app.md §5). A page opened too early shows
    'connection refused', and a person who sees that closes the tab and does not try again."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------- the server
def quiet_flask():
    """Werkzeug shouts about development servers; on 127.0.0.1 its concern does not apply."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("waitress").setLevel(logging.ERROR)
    try:
        import flask.cli
        flask.cli.show_server_banner = lambda *a, **k: None
    except Exception:
        pass


def serve(app, host, port):
    """Waitress, thread-pooled and pure Python (termux-app.md §3); the dev server, said plainly,
    when waitress is not installed, so a broken install degrades rather than dies."""
    try:
        import waitress
        waitress.serve(app, host=host, port=port, threads=8, _quiet=True, ident="ZET Strike")
    except ImportError:
        say("  waitress is not installed (pip install waitress): the development server for now")
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


# ---------------------------------------------------------------- the banner
def banner(port, version, library, note=None, on=True):
    try:
        cols = shutil.get_terminal_size((44, 20)).columns
    except Exception:
        cols = 44
    rule = c("  " + "-" * max(20, min(cols - 4, 40)), RULE, on)
    url = "http://127.0.0.1:%d" % port
    if note:
        say("  " + c(note, GREY, on))
    say()
    say("  " + c("ZET STRIKE", GOLD, on) + "  " + c("server", GREY, on))
    say(rule)
    say("   " + c("on this phone  ", GREY, on) + c(url, INK, on))
    say("   " + c("library        ", GREY, on) + c(library, INK, on))
    say("   " + c("version        ", GREY, on) + c("V%s" % version, INK, on))
    say(rule)
    for k, what in (("O", "open in Chrome"), ("A", "open in the default browser"), ("U", "update the app"),
                    ("R", "restart"), ("Q", "stop")):
        say("   " + c("[%s]" % k, GOLD, on) + " " + c(what, GREY, on))
    say(rule)


# ---------------------------------------------------------------- the keys
def run(app, host, port, version, library, note=None, on_check=None, on_update=None):
    """Serve and answer keys. Returns "quit" or "restart"."""
    quiet_flask()
    on = is_interactive()
    url = "http://127.0.0.1:%d" % port
    banner(port, version, library, note, on)

    if not on:
        say("ZET Strike on %s" % url)
        serve(app, host, port)
        return "quit"

    threading.Thread(target=serve, args=(app, host, port), daemon=True).start()

    def first_open():
        if os.environ.get("ZET_NO_BROWSER"):
            return
        if not wait_for_port(port):
            say("  " + c("the server did not answer on %d, open %s by hand" % (port, url), RED, on))
            return
        used = open_page(url, want_chrome=True)
        say("  " + (c("opened in " + used, GREY, on) if used else c("no way to open a browser from here, open " + url, RED, on)))

    threading.Thread(target=first_open, daemon=True).start()

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    pending = [None]            # set while an update is offered; the next key answers it

    def check_update():
        say()
        say("  checking github for a newer version…")
        try:
            info = on_check()
        except Exception as e:                                   # noqa: BLE001
            say("  " + c("could not check, nothing was changed: %s" % e, RED, on))
            return
        if info["up_to_date"]:
            say("  " + c("V%s is already the newest version" % info["current"], GREY, on))
            return
        say("  " + c("V%s installed" % info["current"], INK, on) + "   ->   " + c("V%s available" % info["latest"], GOLD, on))
        if info.get("notes"):
            say("  " + c(str(info["notes"])[:200], GREY, on))
        say("  press " + c("y", GOLD, on) + " to update, any other key cancels")
        pending[0] = info

    def confirm_update(ch):
        info, pending[0] = pending[0], None
        if ch != "y":
            say("  update canceled")
            return None
        say("  downloading V%s, every file checked against its checksum…" % info["latest"])
        try:
            res = on_update()
        except Exception as e:                                   # noqa: BLE001
            say("  " + c("update failed, nothing was changed: %s" % e, RED, on))
            return None
        say("  " + c(res.get("message", "updated"), GREY, on))
        say("  " + c("restarting on the same port…", GOLD, on))
        time.sleep(0.8)
        return "restart"

    action = "quit"
    try:
        tty.setcbreak(fd)
        while True:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            ch = os.read(fd, 1).decode(errors="ignore").lower()
            if pending[0] is not None:
                if confirm_update(ch) == "restart":
                    action = "restart"
                    break
                continue
            if ch in ("q", "\x03", "\x04"):
                break
            if ch == "r":
                say("  restarting…")
                action = "restart"
                break
            if ch in ("o", "a"):
                used = open_page(url, want_chrome=(ch == "o"))
                say("  " + (c("opened in " + used, GREY, on) if used else c("no way to open a browser from here", RED, on)))
            elif ch == "u" and on_check:
                check_update()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if action == "quit":
        say(c("  stopped.", GREY, on))
    return action
