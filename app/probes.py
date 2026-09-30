"""
probes.py  --  is this key valid, and can it actually do work? (modules/key-testing.md)

Copied verbatim from KEYRING_TERMUX/probes.py (30.9.2026), only the User-Agent changed. ZET Strike
uses gemini_probe and google_probe. Fix it there and pull it here.

Ported from SAMPLE_PLAYER_MACOS/server.py (work_probe, WORK_PROBES, sounds_like_money, status_word,
is_dead_answer) and Key_Tester/Providers.kt (the validity probes, the Speechify/ElevenLabs fallback,
geminiWorkProbe, the Google probes), 13.9.2026, with their comments. Read those before changing this.

    TWO QUESTIONS, TWO PROBES.  A list call answers "is this a real key" and says yes just as
    cheerfully for an account that cannot generate a word. Only asking for WORK asks whether work
    is possible: one token, one word of speech, one second of audio. It costs a fraction of a cent
    when the account is alive and nothing when it is not.

    SIX STATES a person sees:
      works       2xx on a WORK call: valid AND has credit
      valid       the auth probe passed and the work call gave no real answer (a retired model, an
                  outage, a provider with no cheap work): NOT downgraded to unclear (key-testing §6)
      no credit   valid, the account is empty: top up, or delete deliberately. Never "rejected".
      rejected    401 / 402 / 403: wrong, revoked, or the wrong provider for the shape
      throttled   429 with a wait hint: valid, rate-limited this minute. Never delete.
      unclear     the answer says nothing about the key: no network, Cloudflare 1010, our request

    THE RETRY HINT IS CHECKED FIRST AND WINS. Gemini says RESOURCE_EXHAUSTED for a spent account
    and for two requests in one second; only retryDelay / Retry-After tells them apart.

    NEVER NAME A MODEL. Ask for the list, take the first that fits. The three dated names the
    first probes carried (a Llama, a Gemini, a Haiku) all answered 404 within months, and a 404 on
    a probe looks exactly like a broken key.

    GOOGLE ANSWERS 200 WHILE DENYING. A Maps / Geocoding call puts the verdict in the body's
    "status" (REQUEST_DENIED, OVER_QUERY_LIMIT), with HTTP 200. A tester that believes the status
    code calls a denied key working, and one that asks the wrong API calls a working key dead:
    Marko's Maps key is restricted to the Places API (New) and the Map Tiles API, so a Geocoding
    probe alone says REQUEST_DENIED for a key that works every day. The google probe asks each API
    the account might be allowed and reports per API; the key "works" when any of them does.

Every request sends a User-Agent: api.groq.com and api.hume.ai refuse a bare client with
403 error code 1010 on every key identically, which classified as dead buries a whole ring.
"""

import base64
import hashlib
import json
import math
import re
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

UA = "ZETStrike/7 (+https://github.com/markoboskoauroville/zet_strike)"
TIMEOUT = 25                      # every wait on the outside world has a deadline (delivery-gate G5)

STRONG_MONEY = ("credit", "e0300", "zero_credits", "balance", "depleted", "insufficient",
                "billing", "payment", "out of funds", "upgrade your plan", "prepayment")
WEAK_MONEY = ("quota", "exhausted", "resource_exhausted", "free tier", "plan limit")
WAIT_HINTS = ("retrydelay", "retry-after", "retryinfo", "quotafailure",
              "per minute", "per-minute", "rate limit", "rate_limit", "try again in")

WORK_PROVIDERS = ("anthropic", "openai", "openrouter", "groq", "gemini", "hume", "speechify", "assemblyai", "google")


# ---------------------------------------------------------------- the wire
def http(method, url, headers=None, body=None, timeout=TIMEOUT):
    """(status, body bytes, headers dict). -1 no network / refused, -2 timed out. Never raises."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310: every url here is an https literal of a provider
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:                                    # noqa: BLE001
            data = b""
        return e.code, data, dict(e.headers or {})
    except (socket.timeout, TimeoutError):
        return -2, b"timed out after %ds" % timeout, {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return -1, str(e).encode("utf-8", "replace")[:300], {}


def jbody(body):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return {}


def text(body):
    return (body or b"").decode("utf-8", "replace")


# ---------------------------------------------------------------- the money detector
def sounds_like_money(body):
    """True when the answer means PAY, false when it means WAIT or anything else."""
    b = text(body).lower()
    if any(h in b for h in WAIT_HINTS):
        return False                                         # it told us how long to wait: a throttle
    if any(w in b for w in STRONG_MONEY):
        return True
    return any(w in b for w in WEAK_MONEY)


def is_cloudflare(code, body):
    b = text(body).lower()
    return code == 403 and ("1010" in b or "cloudflare" in b)


def retry_after(headers, body):
    """Seconds to wait, from the headers or the body, floor 1, ceiling 3600; None when unsaid."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    v = h.get("retry-after")
    if v and v.strip().replace(".", "", 1).isdigit():
        return int(min(max(float(v), 1), 3600))
    for k, v in h.items():
        if k.startswith("x-ratelimit-reset") and v:
            m = re.match(r"(?:(\d+)m)?(?:([\d.]+)s)?(?:([\d.]+)ms)?$", v.strip())
            if m and any(m.groups()):
                secs = int(m.group(1) or 0) * 60 + float(m.group(2) or 0) + float(m.group(3) or 0) / 1000
                return int(min(max(secs, 1), 3600))
    m = re.search(r"try again in ([\d.]+)\s*(m|s)", text(body).lower())
    if m:
        return int(min(max(float(m.group(1)) * (60 if m.group(2) == "m" else 1), 1), 3600))
    m = re.search(r'"retrydelay"\s*:\s*"(\d+)s"', text(body).lower())
    if m:
        return int(min(max(int(m.group(1)), 1), 3600))
    return None


def classify(code, body, headers=None):
    """(state, why) for one answer of a WORK call. The list-call answer is classified by auth_state."""
    if code == -1:
        return "unclear", "no network, or the host refused the connection"
    if code == -2:
        return "unclear", text(body)
    if is_cloudflare(code, body):
        return "unclear", "Cloudflare refused the client (403 error 1010), not the key"
    if 200 <= code < 300:
        return "works", "working, and it has credit"
    if sounds_like_money(body):
        return "no credit", "account live, credit spent: it needs a top-up or a paid plan"
    if code == 429:
        wait = retry_after(headers, body)
        return "throttled", "alive, and throttled this minute" + (" (wait %ds)" % wait if wait else "")
    if code in (401, 402, 403):
        return "rejected", "refused (%d): wrong, revoked, or the wrong provider" % code
    if code == 404:
        return "unclear", "404: the endpoint or the model, not the key"
    if code == 400:
        return "unclear", "400: our request was refused, no key fixes that"
    if code >= 500:
        return "unclear", "%d: the provider's outage, not the key" % code
    return "unclear", "unexpected answer %d" % code


def auth_state(code, body):
    """The cheap question: is this a real key? valid / invalid / unknown."""
    if code == -1 or code == -2 or is_cloudflare(code, body):
        return "unknown"
    if 200 <= code < 300:
        return "valid"
    if code in (401, 403):
        return "invalid"
    if code == 402 or sounds_like_money(body):
        return "valid"                                       # an empty account is a real account
    if code == 429:
        return "valid"
    return "unknown"


def snippet(body, n=110):
    """A short, single-line piece of the provider's answer for the detail line."""
    j = jbody(body)
    msg = ""
    if isinstance(j, dict):
        e = j.get("error")
        if isinstance(e, dict):
            msg = e.get("message") or e.get("status") or ""
        elif isinstance(e, str):
            msg = e
        msg = msg or j.get("message") or j.get("error_message") or j.get("detail") or ""
    if not msg:
        msg = text(body)
    return re.sub(r"\s+", " ", str(msg)).strip()[:n]


# ---------------------------------------------------------------- the providers
def bearer(k):
    return {"Authorization": "Bearer " + k}


AUTH = {   # provider -> (method, url, headers(key))   the cheap GET the key must be authorised for
    "anthropic":  ("GET", "https://api.anthropic.com/v1/models", lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"}),
    "openai":     ("GET", "https://api.openai.com/v1/models", bearer),
    "openrouter": ("GET", "https://openrouter.ai/api/v1/auth/key", bearer),
    "groq":       ("GET", "https://api.groq.com/openai/v1/models", bearer),
    "gemini":     ("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=50", lambda k: {"x-goog-api-key": k}),
    "assemblyai": ("GET", "https://api.assemblyai.com/v2/transcript?limit=1", lambda k: {"authorization": k}),   # raw key, NO Bearer
    "elevenlabs": ("GET", "https://api.elevenlabs.io/v1/user/subscription", lambda k: {"xi-api-key": k}),
    "speechify":  ("GET", "https://api.sws.speechify.com/v1/voices", bearer),
    "hume":       ("GET", "https://api.hume.ai/v0/tts/voices?page_size=1&provider=HUME_AI", lambda k: {"X-Hume-Api-Key": k}),
    "github":     ("GET", "https://api.github.com/user", lambda k: {"Authorization": "Bearer " + k, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),
    "cloudflare": ("GET", "https://api.cloudflare.com/client/v4/user/tokens/verify", bearer),
    "huggingface": ("GET", "https://huggingface.co/api/whoami-v2", bearer),
}

CHAT_SKIP = ("whisper", "tts", "guard", "embed", "moderation", "image", "vision-only", "realtime", "audio", "transcri", "dall-e", "playai")


def first_model(provider, key):
    """The first live text model this account lists, never a name from memory."""
    method, url, hdr = AUTH[provider]
    code, body, _ = http(method, url, hdr(key))
    if code != 200:
        return None, code, body
    j = jbody(body)
    items = j.get("data") if isinstance(j, dict) else None
    if provider == "gemini":
        items = j.get("models", []) if isinstance(j, dict) else []
        for m in items:
            name = m.get("name", "")
            if "generateContent" in (m.get("supportedGenerationMethods") or []) and not any(s in name for s in ("tts", "image", "embedding", "aqa", "veo", "imagen")):
                return name, code, body
        return None, code, body
    if not isinstance(items, list):
        return None, code, body
    if provider == "anthropic":
        ids = [m.get("id", "") for m in items]
        for fam in ("haiku", "sonnet", "opus"):              # cheapest first
            for i in ids:
                if fam in i:
                    return i, code, body
        return (ids[0] if ids else None), code, body
    ids = [m.get("id", "") for m in items if isinstance(m, dict)]
    for i in ids:
        low = i.lower()
        if not any(s in low for s in CHAT_SKIP):
            return i, code, body
    return (ids[0] if ids else None), code, body


def chat_probe(provider, key):
    """OpenAI-compatible and Anthropic: one token."""
    model, code, body = first_model(provider, key)
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + (": " + snippet(body) if snippet(body) else ""), "status": code, "auth": auth}
    if not model:
        return {"state": "valid", "detail": "valid; no text model listed to do work with", "status": code, "auth": auth}
    if provider == "anthropic":
        url, hdr = "https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    elif provider == "groq":
        url, hdr = "https://api.groq.com/openai/v1/chat/completions", {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    elif provider == "openrouter":
        url, hdr = "https://openrouter.ai/api/v1/chat/completions", {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    else:
        url, hdr = "https://api.openai.com/v1/chat/completions", {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    payload = json.dumps({"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
    if provider == "openai":
        # newer OpenAI models refuse max_tokens; max_completion_tokens is accepted by all of them
        payload = json.dumps({"model": model, "max_completion_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
    code, body, headers = http("POST", url, hdr, payload)
    st, why = classify(code, body, headers)
    if st == "unclear":
        st, why = "valid", "valid; the work call gave no real answer (" + why + ")"
    return {"state": st, "detail": why + " · model " + model, "status": code, "auth": auth}


def openrouter_probe(key):
    """OpenRouter's /auth/key says the limit and the usage: a real credit answer without a call."""
    code, body, _ = http("GET", "https://openrouter.ai/api/v1/auth/key", bearer(key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why, "status": code, "auth": auth}
    d = (jbody(body) or {}).get("data") or {}
    limit, usage = d.get("limit"), d.get("usage")
    if limit is not None and usage is not None and usage >= limit:
        return {"state": "no credit", "detail": "valid; the limit is used up (%.2f of %.2f)" % (usage, limit), "status": code, "auth": auth}
    r = chat_probe("openrouter", key)
    r["detail"] += " · usage %s of %s" % (usage, limit if limit is not None else "unlimited")
    return r


def gemini_probe(key):
    """The model is in the path, so the url cannot be written down until it is known; walk a chain and
    follow the 404's 'use models/X' hint; skip 5xx (Google's load, not the key)."""
    model, code, body = first_model("gemini", key)
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    queue = ["models/gemini-flash-latest"] + ([model] if model else [])     # the alias, the account's own list, the 404 hints
    tried, last = [], None
    while queue and len(tried) < 6:
        m = queue.pop(0)
        if m in tried:
            continue
        tried.append(m)
        url = "https://generativelanguage.googleapis.com/v1beta/%s:generateContent" % m
        payload = json.dumps({"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {"maxOutputTokens": 1}}).encode()
        code, body, headers = http("POST", url, {"x-goog-api-key": key, "Content-Type": "application/json"}, payload)
        last = (code, body, headers, m)
        if code == 404:
            hint = re.search(r"models/[A-Za-z0-9._-]+", text(body))
            if hint and hint.group(0) not in tried:
                queue.insert(0, hint.group(0))
            continue
        if code >= 500:
            continue
        st, why = classify(code, body, headers)
        return {"state": st, "detail": why + " · " + m, "status": code, "auth": auth}
    code, body, headers, m = last
    return {"state": "valid", "detail": "valid; no model answered the work call (last %d on %s)" % (code, m), "status": code, "auth": auth}


def hume_probe(key, secret=""):
    code, body, _ = http("GET", AUTH["hume"][1], AUTH["hume"][2](key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    payload = json.dumps({"utterances": [{"text": "Hi."}], "format": {"type": "wav"}, "num_generations": 1}).encode()
    code, body, headers = http("POST", "https://api.hume.ai/v0/tts", {"X-Hume-Api-Key": key, "Content-Type": "application/json"}, payload)
    st, why = classify(code, body, headers)
    if st == "unclear":
        st, why = "valid", "valid; the work call gave no real answer (" + why + ")"
    return {"state": st, "detail": why, "status": code, "auth": auth}


def speechify_probe(key):
    code, body, _ = http("GET", AUTH["speechify"][1], bearer(key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    voices = jbody(body)
    voice = "beatrice_32"
    if isinstance(voices, list) and voices:
        voice = voices[0].get("id") or voice
    payload = json.dumps({"input": "Hi.", "voice_id": voice, "audio_format": "wav", "model": "simba-english"}).encode()
    code, body, headers = http("POST", "https://api.sws.speechify.com/v1/audio/speech", {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, payload)
    st, why = classify(code, body, headers)
    if st == "unclear":
        st, why = "valid", "valid; the work call gave no real answer (" + why + ")"
    return {"state": st, "detail": why + " · voice " + voice, "status": code, "auth": auth}


def elevenlabs_probe(key):
    """No cheap generation probe: the subscription endpoint states the characters left, which is the
    credit question answered without spending."""
    code, body, _ = http("GET", AUTH["elevenlabs"][1], AUTH["elevenlabs"][2](key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    j = jbody(body) or {}
    used, limit = j.get("character_count"), j.get("character_limit")
    if isinstance(used, int) and isinstance(limit, int):
        if used >= limit:
            return {"state": "no credit", "detail": "valid; %d of %d characters used this cycle" % (used, limit), "status": code, "auth": auth}
        return {"state": "works", "detail": "valid; %d of %d characters left (%s)" % (limit - used, limit, j.get("tier", "")), "status": code, "auth": auth}
    return {"state": "valid", "detail": "valid; the subscription did not say how much is left", "status": code, "auth": auth}


def probe_clip():
    """One second of 440 Hz at a quarter of full scale, 16 kHz mono 16-bit: the exact bytes of
    MANTRA_MANIFEST/fixtures/probe-1s-440hz.wav (sha256 9e2c610d…), checked below."""
    n = 16000
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / n)) for i in range(n)]
    data = struct.pack("<%dh" % n, *samples)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, n, n * 2, 2, 16) + b"data" + struct.pack("<I", len(data))
    return header + data


PROBE_CLIP_SHA256 = "9e2c610d9b40fbfe83c6de65590d815834bfd2669e493c58ec526874222ec545"


def assemblyai_probe(key):
    """Upload, then submit; the money answer can arrive at either. Never poll to completion."""
    code, body, _ = http("GET", AUTH["assemblyai"][1], AUTH["assemblyai"][2](key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body)
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    clip = probe_clip()
    if hashlib.sha256(clip).hexdigest() != PROBE_CLIP_SHA256:
        return {"state": "valid", "detail": "valid; the probe clip does not match the manifest fixture, work not tried", "status": code, "auth": auth}
    code, body, headers = http("POST", "https://api.assemblyai.com/v2/upload", {"authorization": key, "Content-Type": "application/octet-stream"}, clip)
    st, why = classify(code, body, headers)
    if st != "works":
        if st == "unclear":
            st, why = "valid", "valid; the upload gave no real answer (" + why + ")"
        return {"state": st, "detail": why + " (at upload)", "status": code, "auth": auth}
    url = (jbody(body) or {}).get("upload_url")
    if not url:
        return {"state": "valid", "detail": "valid; the upload answered without a url", "status": code, "auth": auth}
    code, body, headers = http("POST", "https://api.assemblyai.com/v2/transcript", {"authorization": key, "Content-Type": "application/json"}, json.dumps({"audio_url": url}).encode())
    st, why = classify(code, body, headers)
    if st == "works":
        j = jbody(body) or {}
        if j.get("status") == "error":                        # a failure that arrives as 200
            st, why = "unclear", "submit answered 200 with an error: " + snippet(body)
        else:
            why = "working, and it has credit (submitted, not polled)"
    elif st == "unclear":
        st, why = "valid", "valid; the submit gave no real answer (" + why + ")"
    return {"state": st, "detail": why + " (at submit)", "status": code, "auth": auth}


def github_probe(key):
    code, body, headers = http("GET", AUTH["github"][1], AUTH["github"][2](key))
    auth = auth_state(code, body)
    if auth != "valid":
        st, why = classify(code, body, headers)
        if code == 403 and "rate limit" in text(body).lower():
            st, why = "throttled", "valid, rate-limited"
        return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}
    h = {k.lower(): v for k, v in headers.items()}
    j = jbody(body) or {}
    scopes = h.get("x-oauth-scopes", "") or "(fine-grained: no scope header)"
    exp = h.get("github-authentication-token-expiration", "") or "no expiry"
    return {"state": "works", "detail": "valid as %s · scopes %s · expires %s" % (j.get("login", "?"), scopes, exp), "status": code, "auth": auth, "owner": j.get("login")}


def cloudflare_probe(key):
    """A USER token verifies at /user/tokens/verify. An ACCOUNT-owned token (cfat_..., the kind the
    dashboard makes for one account) answers 401 "Invalid API Token" there while it works every day
    with wrangler: MEASURED 13.9.2026 on Marko's Pages token, the false negative he asked to have
    fixed. So: the user door first, then /accounts (which an account token can list), then that
    account's own /tokens/verify."""
    code, body, _ = http("GET", AUTH["cloudflare"][1], bearer(key))
    j = jbody(body) or {}
    if 200 <= code < 300 and j.get("success"):
        st = (j.get("result") or {}).get("status", "")
        return {"state": "works" if st == "active" else "rejected", "detail": "user token %s" % (st or "verified"), "status": code, "auth": "valid"}
    code2, body2, _ = http("GET", "https://api.cloudflare.com/client/v4/accounts?per_page=5", bearer(key))
    j2 = jbody(body2) or {}
    accounts = j2.get("result") if 200 <= code2 < 300 and j2.get("success") else None
    if accounts:
        acc = accounts[0]
        code3, body3, _ = http("GET", "https://api.cloudflare.com/client/v4/accounts/%s/tokens/verify" % acc.get("id", ""), bearer(key))
        j3 = jbody(body3) or {}
        if 200 <= code3 < 300 and j3.get("success"):
            st = (j3.get("result") or {}).get("status", "")
            return {"state": "works" if st == "active" else "rejected", "detail": "account token %s, account %s" % (st or "verified", (acc.get("name") or "")[:40]), "status": code3, "auth": "valid"}
        return {"state": "works", "detail": "lists the account %s (the token verify door said no, the account door yes)" % (acc.get("name") or "")[:40], "status": code2, "auth": "valid"}
    st, why = classify(code, body)
    auth = auth_state(code, body)
    return {"state": "rejected" if auth == "invalid" or code == 400 else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}


def huggingface_probe(key):
    code, body, _ = http("GET", AUTH["huggingface"][1], bearer(key))
    auth = auth_state(code, body)
    if auth == "valid":
        return {"state": "works", "detail": "valid as " + str((jbody(body) or {}).get("name", "?")), "status": code, "auth": auth}
    st, why = classify(code, body)
    return {"state": "rejected" if auth == "invalid" else st, "detail": why + ": " + snippet(body), "status": code, "auth": auth}


def spotify_probe(key, secret=""):
    """A client id + secret pair: the client-credentials token is the work. A lone token: /v1/me."""
    if secret:
        auth = base64.b64encode((key + ":" + secret).encode()).decode()
        code, body, headers = http("POST", "https://accounts.spotify.com/api/token", {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"}, b"grant_type=client_credentials")
        if 200 <= code < 300 and (jbody(body) or {}).get("access_token"):
            return {"state": "works", "detail": "the pair gets a token", "status": code, "auth": "valid"}
        st, why = classify(code, body, headers)
        if code == 400 and "invalid_client" in text(body).lower():
            st, why = "rejected", "invalid client id or secret"
        return {"state": st, "detail": why + ": " + snippet(body), "status": code, "auth": "invalid" if st == "rejected" else "unknown"}
    code, body, headers = http("GET", "https://api.spotify.com/v1/me", bearer(key))
    a = auth_state(code, body)
    if a == "valid":
        return {"state": "works", "detail": "valid as " + str((jbody(body) or {}).get("display_name", "?")) + " (an access token lasts an hour)", "status": code, "auth": a}
    st, why = classify(code, body, headers)
    return {"state": "rejected" if a == "invalid" else st, "detail": why + " (an access token expires after an hour)", "status": code, "auth": a}


# ---------------------------------------------------------------- Google: one shape, many services
def google_verdict(code, body):
    """A Google API answer: (state, why). Reads the body: HTTP 200 may carry REQUEST_DENIED."""
    j = jbody(body) or {}
    status = str(j.get("status") or "")
    err = j.get("error") if isinstance(j.get("error"), dict) else {}
    msg = (j.get("error_message") or err.get("message") or "").lower()
    estatus = str(err.get("status") or "")
    low = text(body).lower()
    if code == -1 or code == -2:
        return "unclear", text(body)
    if 200 <= code < 300 and status in ("", "OK", "ZERO_RESULTS"):
        return "works", "works"
    if "api key not valid" in msg or "api_key_invalid" in low or "invalid api key" in msg:
        return "rejected", "API key not valid"
    if "billing" in msg:
        return "no credit", "billing is not enabled on the key's project"
    if code == 429 or status in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT") or estatus == "RESOURCE_EXHAUSTED":
        return ("throttled" if not sounds_like_money(body) else "no credit"), "quota: " + (msg or status)
    if status == "REQUEST_DENIED" or code == 403 or estatus == "PERMISSION_DENIED":
        if "referer" in msg or "referrer" in msg or "ip address" in msg or "ip, site or mobile" in msg or "not authorized from" in msg:
            return "restricted", "the key is restricted to another IP or referrer"
        if ("not authorized to use this api" in msg or "not activated" in msg or "has not been used" in msg or "is disabled" in msg
                or "api restriction" in msg or "not enabled" in msg or ("blocked" in msg and "api" in msg)):
            return "not enabled", "this API is not enabled for the key"
        return "rejected", "denied: " + (msg or status or str(code))[:80]
    if code == 400:
        return "unclear", "our request: " + (msg or status or "400")[:80]
    if code >= 500:
        return "unclear", "%d: Google's outage" % code
    return "unclear", (msg or status or str(code))[:80]


def google_probe(key):
    """Every API the key might be allowed, each asked the smallest thing it sells; the key works when
    any of them does, and the detail says which. Places (New) is what SHOP_FINDER uses, so it is first."""
    calls = [
        ("Places", "POST", "https://places.googleapis.com/v1/places:searchText",
         {"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id", "Content-Type": "application/json"},
         json.dumps({"textQuery": "Spar Zagreb", "maxResultCount": 1}).encode()),
        ("Tiles", "POST", "https://tile.googleapis.com/v1/createSession?key=" + urllib.parse.quote(key),
         {"Content-Type": "application/json"}, json.dumps({"mapType": "roadmap", "language": "en-GB", "region": "HR"}).encode()),
        ("Geocoding", "GET", "https://maps.googleapis.com/maps/api/geocode/json?address=Zagreb&key=" + urllib.parse.quote(key), {}, None),
        ("Gemini", "GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", {"x-goog-api-key": key}, None),
    ]
    results, best = [], None
    rank = {"works": 0, "throttled": 1, "no credit": 2, "restricted": 3, "not enabled": 4, "unclear": 5, "rejected": 6}
    for name, method, url, hdr, payload in calls:
        code, body, _ = http(method, url, hdr, payload)
        st, why = google_verdict(code, body)
        results.append((name, st, why, code))
        if best is None or rank.get(st, 5) < rank.get(best[1], 5):
            best = (name, st, why, code)
    if all(r[1] == "rejected" for r in results):
        return {"state": "rejected", "detail": "API key not valid on any Google service", "status": results[0][3], "auth": "invalid", "apis": results}
    state = best[1] if best[1] in ("works", "throttled", "no credit") else ("valid" if best[1] in ("restricted", "not enabled") else "unclear")
    marks = " · ".join("%s %s" % (n, {"works": "✓", "throttled": "✓ throttled", "no credit": "no credit", "restricted": "restricted (valid, not from here)", "not enabled": "not enabled", "unclear": "?", "rejected": "✗"}[s]) for n, s, _, _ in results)
    if best[1] == "works":
        detail = "works for %s: %s" % (best[0], marks)
    elif best[1] == "restricted":
        detail = "valid, but restricted to another IP or referrer: " + marks
    elif best[1] == "not enabled":
        detail = "valid; none of these APIs is enabled for it: " + marks
    else:
        detail = best[2] + ": " + marks
    return {"state": state, "detail": detail, "status": best[3], "auth": "valid" if state != "unclear" else "unknown", "apis": results}


# ---------------------------------------------------------------- the front door
def test_key(provider, key, secret=""):
    """One provider, one answer: {state, detail, status, auth, provider?}."""
    if provider == "anthropic" or provider == "groq" or provider == "openai":
        return chat_probe(provider, key)
    if provider == "openrouter":
        return openrouter_probe(key)
    if provider == "gemini":
        return gemini_probe(key)
    if provider == "google":
        return google_probe(key)
    if provider == "hume":
        return hume_probe(key, secret)
    if provider == "speechify":
        return speechify_probe(key)
    if provider == "elevenlabs":
        return elevenlabs_probe(key)
    if provider == "assemblyai":
        return assemblyai_probe(key)
    if provider == "github":
        return github_probe(key)
    if provider == "cloudflare":
        return cloudflare_probe(key)
    if provider == "huggingface":
        return huggingface_probe(key)
    if provider == "spotify":
        return spotify_probe(key, secret)
    return {"state": "unclear", "detail": "no probe for '%s': assign a provider first" % provider, "status": None, "auth": "unknown"}


FALLBACK = {"speechify": "elevenlabs", "elevenlabs": "speechify", "assemblyai": "spotify", "gemini": "google", "google": "gemini"}


def test_entry(entry):
    """The entry's provider first; on a refusal, the provider that shares the shape (sk_ is
    Speechify AND ElevenLabs; a Google key is one shape for every service), adopting it when it
    authenticates. A soft answer never switches providers."""
    provider = entry.get("provider", "unknown")
    r = test_key(provider, entry["value"], entry.get("secret", ""))
    r["provider"] = provider
    other = FALLBACK.get(provider)
    if r["state"] == "rejected" and other and not (other == "spotify" and not entry.get("secret")):
        r2 = test_key(other, entry["value"], entry.get("secret", ""))
        if r2.get("auth") == "valid":
            r2["provider"] = other
            r2["detail"] = "as %s (not %s): " % (other, provider) + r2["detail"]
            return r2
    return r
