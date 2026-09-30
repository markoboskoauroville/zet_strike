"""
keyparse.py  --  ZET Strike: reading key files. Copied from KEYRING_TERMUX/ring.py (30.9.2026), the
PARSER HALF only (the constants, new_entry, the parser, merge), byte for byte but for ONE FIX that
must go back to the source: _label_from took a line holding the key ("google AIza...") whole as the
title, key included. The vault half (load, save, the log) is not used here; ZET Strike keeps its
keys in ~/.zet-strike/secrets.

THE PARSER     keys live inside notes, not in key files (modules/keyring.md §4, §10d): blocks
               separated by blank lines, the key found by SHAPE inside a block and the name by
               elimination, never by line position. Shape only RANKS (key-testing.md §8): a long
               token nobody recognises is kept as "unknown" for the person to assign, never dropped.
               Gemini keys begin AQ. (keyring.md, "Gemini keys start with AQ."); an AIza key is a
               Google Cloud API key (Maps, Places, Tiles) and is labelled google, never gemini.

THE FORMAT     what the Keyring app's export writes and import reads first, "keyring v1": one block
               per entry, `field: value` lines, blank line between entries, # comments.

    # keyring v1
    provider: gemini
    label: kalabhumi
    key: AQ....
"""

import hashlib
import re
import secrets as _secrets
import time

STATES = ("untested", "works", "valid", "no credit", "rejected", "throttled", "unclear")

# provider -> (name shown, glyph letter for the row icon, the two-part flag)
PROVIDERS = {
    "anthropic":  {"name": "Anthropic",   "pair": False},
    "openai":     {"name": "OpenAI",      "pair": False},
    "openrouter": {"name": "OpenRouter",  "pair": False},
    "groq":       {"name": "Groq",        "pair": False},
    "gemini":     {"name": "Gemini",      "pair": False},
    "google":     {"name": "Google API key (Maps, Places, Tiles)", "pair": False},
    "speechify":  {"name": "Speechify",   "pair": False},
    "elevenlabs": {"name": "ElevenLabs",  "pair": False},
    "hume":       {"name": "Hume",        "pair": True},
    "assemblyai": {"name": "AssemblyAI",  "pair": False},
    "spotify":    {"name": "Spotify",     "pair": True},
    "github":     {"name": "GitHub",      "pair": False},
    "cloudflare": {"name": "Cloudflare",  "pair": False},
    "huggingface": {"name": "Hugging Face", "pair": False},
    "unknown":    {"name": "unknown",     "pair": False},
}

# The shapes, most specific first. A shape labels a FIRST GUESS; the network decides (§8).
SHAPES = [
    ("anthropic",   re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openrouter",  re.compile(r"sk-or-(?:v1-)?[A-Za-z0-9_\-]{20,}")),
    ("openai",      re.compile(r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    ("groq",        re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("gemini",      re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}")),
    ("google",      re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("github",      re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}")),
    ("cloudflare",  re.compile(r"cfat_[A-Za-z0-9_\-]{30,}")),
    ("huggingface", re.compile(r"hf_[A-Za-z0-9]{30,}")),
    ("speechify",   re.compile(r"sk_[A-Za-z0-9]{40,60}")),      # sk_ is Speechify AND ElevenLabs: try one, adopt the other on refusal
    ("spotify",     re.compile(r"(?:BQ|AQ)[A-Za-z0-9_\-]{80,}")),  # a Spotify OAuth access token (an hour's life)
]
HEX32 = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])")          # AssemblyAI, or a Spotify id/secret
ALNUM48_64 = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{48}(?![A-Za-z0-9])|(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])")   # Hume api key (48) and secret (64)
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]{24,}(?![A-Za-z0-9_.\-])")
HEXONLY = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")              # a git commit, a sha256: never a key
URLISH = re.compile(r"https?://|www\.|/")
CONTEXT_WORDS = {
    "assemblyai": ("assembly",), "spotify": ("spotify",), "hume": ("hume",), "elevenlabs": ("eleven",),
    "cloudflare": ("cloudflare",), "google": ("maps", "places", "google cloud", "google_maps"),
    "gemini": ("gemini", "nano banana", "ai studio"),
}
FIELD = re.compile(r"^\s*(provider|label|key|secret|name|note)\s*[:=]\s*(.*?)\s*$", re.I)


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def mask(value):
    """First six and last four, never the middle (keyring.md §6). Short values show only their length."""
    if not value:
        return ""
    if len(value) <= 14:
        return "•" * len(value)
    return value[:6] + "…" + value[-4:]


def new_entry(provider, value, label="", secret="", source=""):
    return {
        "id": _secrets.token_hex(4),
        "provider": provider if provider in PROVIDERS else "unknown",
        "label": (label or "").strip()[:80],
        "value": value.strip(),
        "secret": (secret or "").strip(),
        "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": (source or "")[:120],
        "state": "untested",
        "state_at": "",
        "detail": "",
        "fp": fingerprint(value.strip()),
    }


# ---------------------------------------------------------------- the parser
def _guess_by_shape(token):
    for provider, rx in SHAPES:
        if rx.fullmatch(token):
            return provider
    return None


def _context(text):
    low = text.lower()
    return [p for p, words in CONTEXT_WORDS.items() if any(w in low for w in words)]


def _label_from(lines, taken):
    """The name by elimination: the first line of the block that is not a key, not a URL, not empty."""
    for l in lines:
        s = l.strip()
        for t in taken:                                    # ZET Strike, 30.9.2026: "google AIza..." on one line
            s = s.replace(t, " ")                          # made the whole line, key and all, the title.
        s = " ".join(s.split()).strip(" :=-\t").strip()   # Take the words beside the key, never the key.
        if not s or s in taken or URLISH.search(s) or len(s) > 60:
            continue
        m = FIELD.match(l)
        if m and m.group(1).lower() in ("key", "secret", "provider"):
            continue
        if s.lower() in ("api key", "apikey", "secret key", "secretkey", "secret", "api secret", "key", "token"):
            continue
        if m and m.group(1).lower() in ("label", "name", "note"):
            return m.group(2)[:80]
        if LONG_TOKEN.fullmatch(s):
            continue
        return s[:80]
    return ""


def parse_keyring_format(text):
    """The standard format, exact fields. Returns (entries, leftover_text): blocks that carry a
    provider:/key: pair are taken; everything else is handed to the shape parser."""
    found, leftover = [], []
    for block in re.split(r"\n\s*\n", text):
        fields = {}
        for l in block.splitlines():
            m = FIELD.match(l)
            if m:
                k = m.group(1).lower()
                if k == "name":
                    k = "label"
                fields.setdefault(k, m.group(2))
        if "key" in fields and ("provider" in fields):
            prov = fields["provider"].strip().lower()
            found.append(new_entry(prov if prov in PROVIDERS else "unknown", fields["key"], fields.get("label", ""), fields.get("secret", ""), "keyring file"))
        else:
            leftover.append(block)
    return found, "\n\n".join(leftover)


def parse_notes(text, source=""):
    """Keys by shape inside a handwritten note. Returns entries in the order found."""
    out = []
    ctx_file = _context(source)
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if not any(l.strip() for l in lines):
            continue
        ctx = _context(block) or ctx_file
        taken = set()
        tokens = []
        # explicit fields inside a plain note: key=..., secret=...
        explicit_secret = ""
        for l in lines:
            m = FIELD.match(l)
            if m and m.group(1).lower() == "secret":
                explicit_secret = m.group(2).strip()
        # the Hume export layout, which Key_Tester's parser reads by its labels:
        #     <account name> / API key / <key> / Secret key / <secret>
        labelled_key, labelled_secret = "", ""
        for i, l in enumerate(lines):
            head = l.strip().rstrip(":").lower()
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if head in ("api key", "apikey", "api-key") and LONG_TOKEN.fullmatch(nxt):
                labelled_key = nxt
            elif head in ("secret key", "secretkey", "secret", "api secret") and LONG_TOKEN.fullmatch(nxt):
                labelled_secret = nxt
        if labelled_secret and not explicit_secret:
            explicit_secret = labelled_secret
        for l in lines:
            if URLISH.search(l) and not any(rx.search(l) for _, rx in SHAPES):
                continue                                   # a URL with a tracking parameter is not a key
            for m in LONG_TOKEN.finditer(l):
                t = m.group(0).strip(".")
                if t in taken or t == explicit_secret:
                    continue
                prov = _guess_by_shape(t)
                if prov is None and HEX32.fullmatch(t):
                    prov = "assemblyai" if "assemblyai" in ctx else ("spotify" if "spotify" in ctx else "unknown")
                if prov is None and ALNUM48_64.fullmatch(t):
                    prov = "hume" if ("hume" in ctx or explicit_secret or t == labelled_key) else "unknown"
                if prov is None and HEXONLY.fullmatch(t):
                    continue                               # a commit hash, a digest, a colour: hex of 40 or 64 is not a key
                if prov is None:
                    if t.lower().startswith(("http", "www")) or t.count(".") > 2 or t.upper() == t and "_" in t and t.replace("_", "").isalpha():
                        continue                           # a URL, a domain, or a CONSTANT_NAME
                    prov = "unknown"
                if prov == "speechify" and "elevenlabs" in ctx:
                    prov = "elevenlabs"
                if prov == "google" and "gemini" in ctx and "google" not in ctx:
                    prov = "google"                        # an AIza key is never a Gemini key here, whatever the note says
                taken.add(t)
                tokens.append((prov, t))
        if not tokens:
            continue
        label = _label_from(lines, taken)
        # pairs: hume (48 + 64, or two 64s), spotify (two hex32) in one block -> one entry
        if len(tokens) == 2 and all(ALNUM48_64.fullmatch(t) for _, t in tokens) and tokens[0][0] in ("hume", "unknown") and tokens[1][0] in ("hume", "unknown"):
            tokens = [("hume", tokens[0][1]), ("hume", tokens[1][1])]
        if len(tokens) == 2 and tokens[0][0] == tokens[1][0] and PROVIDERS.get(tokens[0][0], {}).get("pair"):
            a, b = tokens[0][1], tokens[1][1]
            if labelled_key and b == labelled_key:
                a, b = b, a                                # the labelled API key is the key, whatever the order
            out.append(new_entry(tokens[0][0], a, label, b, source))
            continue
        if explicit_secret and len(tokens) == 1 and PROVIDERS.get(tokens[0][0], {}).get("pair", False) or (explicit_secret and len(tokens) == 1 and tokens[0][0] == "unknown"):
            out.append(new_entry("hume" if tokens[0][0] == "unknown" else tokens[0][0], tokens[0][1], label, explicit_secret, source))
            continue
        for prov, t in tokens:
            out.append(new_entry(prov, t, label, "", source))
    return out


def parse(text, source=""):
    """Everything: the standard format first, then shapes in what is left."""
    found, rest = parse_keyring_format(text)
    return found + parse_notes(rest, source)


def merge(existing, incoming):
    """De-duplicate on the value (keyring.md §4). Returns (entries, added, duplicates)."""
    have = {e["value"] for e in existing}
    added, dups = [], 0
    for e in incoming:
        if e["value"] in have:
            dups += 1
            continue
        have.add(e["value"])
        existing.append(e)
        added.append(e)
    return existing, added, dups


