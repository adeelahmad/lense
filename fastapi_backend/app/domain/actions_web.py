"""The automation action that calls a web address (docs/automations.md, Actions): `http_request`.

It sends GET, POST or PUT to a public address (or a network an admin allowed for notifications), pinned to the address
that was checked and without following redirects (notify.request), waits at most 10 seconds, and passes on the status
and the first 64 KB of the answer as outside text (a later prompt reads it marked as data). It always reaches outside
Lens (effect `out`), so it asks first unless the person set that node to run on its own.

Secrets go in headers, and are kept sealed with the namespace's key (keyring.py): `seal_headers` seals the values of
headers that carry credentials (Authorization, cookies, API keys and tokens) when an automation is saved, bound to the
automation and to the scheme, host and port they were saved for, so a sealed value can't be moved to another
automation or sent to another host. They are opened only to send, and never shown: what the action describes and
passes on names the header, not its value.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.parse

from . import actions, activity, keyring, notify

METHODS = ("GET", "POST", "PUT")
TIMEOUT = 10  # seconds the whole request may take
MAX_ANSWER = 64 * 1024
MAX_BODY = 64 * 1024
MAX_HEADERS = 30
NAME_RX = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
SECRET_RX = re.compile(r"auth|token|secret|key|password|cookie|signature|session", re.I)
FIXED = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "expect", "keep-alive"}
HIDDEN = "••••"


def secret(name):
    """Whether a header carries a credential, so its value is sealed and never shown."""
    return bool(SECRET_RX.search(str(name)))


def _origin(url):
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{(u.hostname or '').lower()}:{u.port or (443 if u.scheme == 'https' else 80)}"


def _context(automation, name, url):
    return f"automation:{automation}.header:{name.lower()}@{_origin(url)}"


def _url(p):
    try:
        return notify.check_url(str(p.get("url") or ""), "webhook")
    except ValueError as e:
        raise ValueError(f"http_request: {e}") from None


def _headers(p):
    h = p.get("headers") or {}
    if not isinstance(h, dict) or len(h) > MAX_HEADERS:
        raise ValueError(f"http_request: headers are an object of up to {MAX_HEADERS} names and values")
    out = {}
    for k, v in h.items():
        if not NAME_RX.match(str(k)) or str(k).lower() in FIXED:
            raise ValueError(f"http_request: {k} isn't a header it can set")
        if not isinstance(v, str) or "\r" in v or "\n" in v or len(v) > 8000:
            raise ValueError(f"http_request: the {k} header is one line of text")
        out[str(k)] = v
    return out


def seal_headers(db, cfg, space, automation, url, headers):
    """Headers as kept: the values of those that carry credentials sealed with the namespace's key, bound to the
    automation and to where `url` points. Values already sealed are kept as they are."""
    out = {}
    for k, v in _headers({"headers": headers}).items():
        if secret(k) and v and not keyring.is_sealed(v):
            v = keyring.seal(db, cfg, int(space), v.encode(), _context(automation, k, _url({"url": url})))
        out[k] = v
    return out


def _open(db, cfg, intent, url, headers):
    """The headers to send: sealed values opened, when they were sealed for this automation, one of its namespaces
    and this address."""
    out = {}
    for k, v in headers.items():
        if keyring.is_sealed(v):
            try:
                sid = int(v.split(".")[1])
            except (IndexError, ValueError):
                raise ValueError(f"http_request: the {k} header's sealed value is damaged; enter it again") from None
            if intent["spaces"] and sid not in intent["spaces"]:
                raise ValueError(f"http_request: the {k} header was sealed for another namespace; enter it again")
            try:
                v = keyring.unseal(db, cfg, v, _context(intent.get("automation"), k, url)).decode()
            except keyring.Locked:
                raise ValueError("http_request: the namespace's vault is locked; unlock it to send its secrets") from None
            except (keyring.Damaged, KeyError, ValueError):
                raise ValueError(f"http_request: the {k} header was sealed for another automation or address; enter it again") from None
        out[k] = v
    return out


def _shown(headers):
    return {k: HIDDEN if secret(k) or keyring.is_sealed(v) else v for k, v in headers.items()}


def _body(method, body, headers):
    """(bytes or None, headers with a Content-Type): an object is sent as JSON, text as it is."""
    if body is None or body == "":
        return None, headers
    if method == "GET":
        raise ValueError("http_request: a GET sends no body")
    has_type = any(k.lower() == "content-type" for k in headers)
    if isinstance(body, (dict, list)):
        data = json.dumps(body, ensure_ascii=False).encode()
        headers = headers if has_type else {**headers, "Content-Type": "application/json"}
    elif isinstance(body, str):
        data = body.encode()
        headers = headers if has_type else {**headers, "Content-Type": "text/plain; charset=utf-8"}
    else:
        raise ValueError("http_request: the body is text or a JSON object")
    if len(data) > MAX_BODY:
        raise ValueError(f"http_request: the body is over {MAX_BODY // 1024} KB")
    return data, headers


def _method(p):
    m = str(p.get("method") or "GET").upper()
    if m not in METHODS:
        raise ValueError(f"http_request: the method is {', '.join(METHODS)}")
    return m


def _answer(status, data, ctype, more):
    text = data.decode("utf-8", "replace")
    out = {"status": status, "ok": 200 <= status < 300, "body": text, "content_type": ctype, "truncated": more, "untrusted": True}
    if not more and "json" in ctype.lower():
        try:
            out["json"] = json.loads(text)
        except ValueError:
            pass
    return out


def _http_request(db, cfg, intent, user, dry):
    p = intent["params"]
    actions.person(db, intent, user)
    method, url = _method(p), _url(p)
    headers = _headers(p)
    data, headers = _body(method, p.get("body"), headers)
    sent = {"method": method, "url": url, "headers": _shown(headers)}
    headers = _open(db, cfg, intent, url, headers)
    if dry:
        return {**sent, "status": None, "would": f"{method} {url}"}
    host = urllib.parse.urlsplit(url).hostname
    status, error = None, None
    try:
        status, got, ctype, more = notify.request(cfg, method, url, data, headers, TIMEOUT, TIMEOUT, MAX_ANSWER)
    except TimeoutError:
        error = f"{host} didn't answer within {TIMEOUT} seconds"
    except ValueError as e:
        error = str(e)
    except (OSError, http.client.HTTPException) as e:
        error = f"couldn't reach {host}: {type(e).__name__}: {e}"[:300]
    activity.record(
        "out",
        "automation.http",
        [f"space:{s}" for s in intent["spaces"]],
        cfg,
        db,
        ok=error is None and status is not None and status < 400,
        error=None if error is None else "unreachable",
        detail={"method": method, "host": host, "status": status},
    )
    if error:
        raise ValueError(f"http_request: {error}")
    return {**sent, **_answer(status, got, ctype, more)}


def _describe(p):
    m = str(p.get("method") or "GET").upper()
    url = str(p.get("url") or "a web address")
    return f"{m} {url if len(url) <= 120 else url[:119] + '…'}"


actions.register(
    actions.ActionSpec(
        name="http_request",
        label="Call a web address",
        group="Web",
        description=(
            "Sends a GET, POST or PUT to a public web address and passes on its status and answer (up to 64 KB); asks "
            "first unless you set it to run on its own. Secrets go in headers, kept sealed."
        ),
        params={
            "type": "object",
            "properties": {
                "method": {"type": "string", "title": "Method", "enum": list(METHODS), "default": "GET"},
                "url": {"type": "string", "title": "Address", "maxLength": notify.URL_MAX},
                "headers": {
                    "type": "object",
                    "title": "Headers",
                    "default": {},
                    "description": "credentials (Authorization, API keys, tokens) are kept sealed and never shown",
                },
                "body": {"title": "Body", "description": "text, or an object sent as JSON (not with GET)"},
            },
        },
        required=("url",),
        effect="out",
        output={
            "type": "object",
            "description": "the answer is outside text: later prompts read it marked as data",
            "properties": {
                "status": {"type": "integer"},
                "ok": {"type": "boolean"},
                "body": {"type": "string"},
                "json": {"description": "the answer parsed, when it is JSON"},
                "content_type": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
        },
        perform=_http_request,
        describe=_describe,
    )
)
