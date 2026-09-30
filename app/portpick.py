"""
portpick.py  --  the app never fails to start because a port is taken.

Copied verbatim from KEYRING_TERMUX/portpick.py (30.9.2026), itself a copy of
MAHA_TRANSCRIBE_TERMUX_TERMINAL/portpick.py; only the port, the marker and the app name changed.
Fix it there and pull it here.

A transcriber that refuses to open because some other program happens to be
on 8420 is a transcriber that is not there when it is wanted. Worse: the
thing on 8420 is very often THIS APP, still running from before -- so the
launcher would be blocking on its own success case.

    IT NEVER GIVES UP. Preferred port, then the next fifteen, then whatever
    the operating system hands out. There is no path through this module
    that ends in "could not start".

WHAT IT MUST NOT DO is pick a port and then have the rest of the app carry on
believing the old one. Two places depend on the real number:

    the browser         opened at the port actually bound, or it shows nothing
    localguard          checks the Host header against the port. Told the
                        wrong one, it would refuse every request from the
                        very page it just opened

So `pick()` returns the number and the caller must use THAT everywhere, never
the configured one again.

Ported near-verbatim from markoboskoauroville/GDRIVE_DOWNLOADER_FLASK_MACOS,
per modules/quota-and-fallback.md's own house rule: read the file that
already solves a problem before writing a new one.
"""

import atexit
import os
import socket

MAX_TRIES = 16          # 8100 through 8115, then the OS decides

# the string that identifies THIS app, used only to tell "another copy of
# maha transcribe" from "some other program" in the startup message
_SELF_MARKER = b"<title>ZET Strike</title>"


def is_free(host, port, timeout=0.4):
    """Can we actually bind it? Not "is something listening" -- BIND.

    Asking whether something is listening answers a different question: a
    socket held in TIME_WAIT, or bound to a different interface, or owned by
    another user, all answer "nothing is listening" and then refuse the
    bind. The only honest test is to try.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # NOT SO_REUSEADDR. With it, this test can succeed on a port another
        # process is already serving from, and then Flask fails behind us.
        s.settimeout(timeout)
        s.bind((host, port))
        return True
    except OSError:
        pass
    finally:
        s.close()
    # The bind failed. If nothing LISTENS there, the port is only in TIME_WAIT from the
    # connections the last copy of this app served (an HTTP server closes first, so its own
    # port wears TIME_WAIT for a minute), and the real server binds it with SO_REUSEADDR
    # without trouble. Found 13.9.2026: the u key's restart landed on the NEXT port every
    # time, and the page that was open pointed at a dead one. Only a port that answers a
    # connection is taken.
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        c.settimeout(timeout)
        c.connect((host, port))
        return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    finally:
        c.close()


def whats_there(port, timeout=1.0):
    """Best guess at what is holding the port, for the message only.

    Returns "self" if it looks like another copy of this app, "something" if
    the port answers at all, or None if it is simply unavailable. Never
    raises: a diagnosis is not worth failing a startup over.
    """
    try:
        port = int(port)
        if not (1 <= port <= 65535):
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                      b"Connection: close\r\n\r\n")
            body = b""
            while len(body) < 4000:
                chunk = s.recv(2048)
                if not chunk:
                    break
                body += chunk
        finally:
            s.close()
        if not body:
            return None
        return ("self" if _SELF_MARKER in body else "something")
    except Exception:                                            # noqa: BLE001
        return None


def pick(host, preferred, tries=MAX_TRIES):
    """Find a port. Always returns one.

    Returns (port, note). `note` is None when the preferred port was free,
    and otherwise a sentence saying what happened -- because a page that
    quietly opens somewhere other than where he expects is its own
    confusion.
    """
    preferred = int(preferred or 8100)

    if is_free(host, preferred):
        return preferred, None

    holder = whats_there(preferred)
    if holder == "self":
        why = (f"port {preferred} is already being used by another copy of "
               f"ZET Strike")
    elif holder == "something":
        why = f"port {preferred} is being used by another program"
    else:
        why = f"port {preferred} could not be opened"

    for offset in range(1, tries):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if is_free(host, candidate):
            return candidate, f"{why}, so this one is on {candidate} instead."

    # Every port in the range was taken. Ask the operating system for any
    # free one rather than give up: there is no situation where refusing to
    # start is the better outcome.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        chosen = s.getsockname()[1]
    except OSError:
        return preferred, (f"{why}, and no other port could be opened on "
                           f"{host}. Check that address.")
    finally:
        s.close()
    return chosen, (f"{why}, and {preferred}-{preferred + tries - 1} were "
                    f"all taken, so this one is on {chosen} instead.")


# ---------------------------------------------------------------------------
#  The live registry (modules/ports.md §3), written 13.9.2026.
#
#  A launcher (mamc) that wants to open an app's page cannot read the ports
#  table in the manifest; it needs the port the app bound TODAY. So every app
#  that picks a port writes one line, and removes it on the way out:
#
#      ~/.mantra/ports/<command>     the number, nothing else, 0600
#
#  Written right after pick(), removed at exit. Stale when the app was
#  killed: the launcher checks that the port answers before trusting the
#  file, so a stale line costs one refused connection and nothing else.
#  Nothing in here raises: a registry that cannot be written is not worth
#  failing a start over.
# ---------------------------------------------------------------------------

REGISTRY = os.path.join(os.path.expanduser("~"), ".mantra", "ports")


def _entry(command):
    command = str(command or "").strip()
    if not command or "/" in command or command in (".", ".."):
        return None
    return os.path.join(REGISTRY, command)


def announce(command, port):
    """Write ~/.mantra/ports/<command> = port, and remove it at exit.

    Returns the path written, or None when nothing was (no command, a port
    out of range, a registry that cannot be written). Written beside its
    name and renamed over it, so a reader never sees a half-written number.
    """
    path = _entry(command)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if path is None or not (1 <= port <= 65535):
        return None
    try:
        os.makedirs(REGISTRY, mode=0o700, exist_ok=True)
        tmp = path + ".new"
        with open(tmp, "w") as f:
            f.write("%d\n" % port)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        return None
    atexit.register(forget, command, port)
    return path


def forget(command, port=None):
    """Remove the line, but only if it still says OUR port: a second copy of
    the app started after us owns the file now, and its line must stay."""
    path = _entry(command)
    if path is None:
        return False
    try:
        if port is not None:
            with open(path) as f:
                if f.read().strip() != str(int(port)):
                    return False
        os.remove(path)
        return True
    except (OSError, ValueError):
        return False


def registered(command):
    """The launcher's side: the number in ~/.mantra/ports/<command>, or None.
    Whether that port ANSWERS is the caller's question, not this one's."""
    path = _entry(command)
    if path is None:
        return None
    try:
        with open(path) as f:
            port = int(f.read(16).strip())
    except (OSError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None
