"""Webhook triggers (docs/automations.md, Webhook trigger): a secret address per automation trigger, for anything that
can send HTTP (Home Assistant, a phone shortcut, another service) to set an automation off.

An automation's trigger `{"on": "webhook"}` gets a hook when the automation is saved (`token_for`): a row in
`automation_hook` with the automation, the trigger's alias, the hash of a token (`lah_...`, in the address and shown
once, as an import hook's is) and a signing secret sealed with the server's key. `revoke` deletes it, and the address
stops working at once.

A call (`receive`, from `POST /api/v1/automations/hooks/{token}`) needs only the token. It takes JSON, a form or text
up to MAX_BODY, at most PER_MINUTE calls a minute for each hook (in this process), and is written to the event log as
`webhook.received` with what was sent as outside text. When the call is signed the Standard Webhooks way (the
`webhook-id`, `webhook-timestamp` and `webhook-signature` headers; notify.py signs its own deliveries so), the
signature must match the hook's secret and the time be within TOLERANCE; a hook made `signed` refuses calls that
aren't. A call naming its message (`webhook-id`, or `Idempotency-Key`) is logged once however often it is retried.

The route answers at once: the automation runs where the stream is followed (the worker, in Docker), on its next look.
Nothing here runs an automation, and no hook exists until an automation with a webhook trigger is saved.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.parse

from . import events, notify, settings, store

R = store.R
TOKEN_PREFIX = "lah_"  # recognisable in logs and secret scanners
TOKEN_RX = re.compile(r"^lah_[A-Za-z0-9_-]{20,64}$")
ALIAS_RX = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PATH = "/api/v1/automations/hooks/"
MAX_BODY = 64 * 1024
PER_MINUTE = 60
TOLERANCE = 300  # seconds a signed call's time may be off from this server's
MAX_KEY = 200
MAX_FIELDS = 200
FIELDS = "record::id(id) AS id, automation, alias, signed, created_at, last_called_at, calls"

_calls: dict[int, collections.deque] = {}
_lock = threading.Lock()


class Refused(ValueError):
    """A call this hook won't take: `status` is the HTTP status to answer with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _secret_context(hid):
    return f"automation_hook:{hid}:secret"


def _base(cfg, db):
    from app.email import app_url

    return app_url(cfg, db)


def token_for(db, cfg, automation_id, alias, signed=False):
    """A webhook for trigger `alias` of automation `automation_id`: {"hook": its id, "url": its address, "path": the
    address without the server, "secret": the signing secret}. The address and secret are shown this once; only the
    token's hash is kept. Calling it again for the same trigger replaces its hook, and the old address stops working.
    `signed` makes the hook refuse calls that aren't signed with the secret."""
    if not isinstance(automation_id, int) or isinstance(automation_id, bool) or automation_id < 1:
        raise ValueError("an automation's id is a whole number")
    if not isinstance(alias, str) or not ALIAS_RX.match(alias):
        raise ValueError("a trigger's alias is lowercase letters, digits and _ (its `as`)")
    hid, token, secret = db.next_id("automation_hook"), TOKEN_PREFIX + secrets.token_urlsafe(32), notify.new_secret()
    db.run(
        ["DELETE automation_hook WHERE automation = $a AND alias = $al", "CREATE $r CONTENT $d"],
        a=automation_id,
        al=alias,
        r=R("automation_hook", hid),
        d={
            "automation": automation_id,
            "alias": alias,
            "token_hash": _hash(token),
            "secret": settings.seal(cfg, secret, _secret_context(hid)),
            "signed": bool(signed),
            "created_at": store.now(),
            "calls": 0,
        },
    )
    return {"hook": hid, "url": _base(cfg, db) + PATH + token, "path": PATH + token, "secret": secret}


def revoke(db, hook):
    """Delete a hook: its address stops working at once. True when there was one."""
    got = db.rows("DELETE $r RETURN BEFORE", r=R("automation_hook", int(hook)))
    with _lock:
        _calls.pop(int(hook), None)
    return bool(got)


def get(db, hook):
    """A hook (without its token or secret), or None."""
    return db.one(f"SELECT {FIELDS} FROM $r", r=R("automation_hook", int(hook)))


def for_automation(db, automation_id):
    """An automation's hooks, oldest first."""
    return db.rows(f"SELECT {FIELDS} FROM automation_hook WHERE automation = $a ORDER BY id", a=int(automation_id))


def by_token(db, token):
    """The hook whose token this is, with its sealed secret; KeyError for any other."""
    if not isinstance(token, str) or not TOKEN_RX.match(token):
        raise KeyError("token")
    row = db.one(f"SELECT {FIELDS}, secret FROM automation_hook WHERE token_hash = $h LIMIT 1", h=_hash(token))
    if not row:
        raise KeyError("token")
    return row


def allow(hook, now=None):
    """Whether one more call to `hook` fits in PER_MINUTE (counted in this process); counts it when it does."""
    now = time.monotonic() if now is None else now
    with _lock:
        q = _calls.setdefault(int(hook), collections.deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= PER_MINUTE:
            return False
        q.append(now)
        if len(_calls) > 10000:  # hooks gone quiet
            for k in [k for k, v in _calls.items() if not v or now - v[-1] >= 60][:5000]:
                _calls.pop(k, None)
        return True


def _header(headers, name):
    v = headers.get(name)
    return v.strip() if isinstance(v, str) else None


def check_signature(cfg, row, headers, body, now=None):
    """The Standard Webhooks message id of a signed call, None for a call that isn't signed; Refused (401) when the
    signature doesn't match the hook's secret, the time is off by more than TOLERANCE, or the hook takes only signed
    calls."""
    sig = _header(headers, "webhook-signature")
    if not sig:
        if row.get("signed"):
            raise Refused(401, "this webhook takes only signed calls (webhook-id, webhook-timestamp and webhook-signature)")
        return None
    msg_id, ts = _header(headers, "webhook-id"), _header(headers, "webhook-timestamp")
    if not msg_id or not ts or not ts.isdigit():
        raise Refused(401, "a signed call has webhook-id and webhook-timestamp (seconds) too")
    if abs((time.time() if now is None else now) - int(ts)) > TOLERANCE:
        raise Refused(401, "the call's webhook-timestamp is too far from this server's time")
    secret = settings.unseal(cfg, row["secret"], _secret_context(row["id"]))
    want = notify.sign(secret, msg_id, ts, body)
    given = [s for s in sig.split() if s.startswith("v1,")]
    if not any(hmac.compare_digest(want.encode(), s.encode()) for s in given):
        raise Refused(401, "the signature doesn't match this webhook's secret")
    return msg_id


def _form(body):
    pairs = urllib.parse.parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=MAX_FIELDS)
    out: dict = {}
    for k, v in pairs:
        out.setdefault(k, []).append(v)
    return {k: v[0] if len(v) == 1 else v for k, v in out.items()}


def payload(body, content_type, form=None):
    """What a call sent, as its content type says: JSON parsed, a form as {field: value, or a list of values}, text as
    it is, nothing as None. `form` is a multipart form the route has already read. Refused (400, 415) otherwise."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if form is not None:
        return form
    if not body.strip():
        return None
    if ctype == "application/json" or ctype.endswith("+json") or (not ctype and body.lstrip()[:1] in (b"{", b"[")):
        try:
            return json.loads(body)
        except ValueError:
            raise Refused(400, "the body isn't JSON") from None
    if ctype == "application/x-www-form-urlencoded":
        try:
            return _form(body)
        except ValueError:
            raise Refused(400, f"a form has at most {MAX_FIELDS} fields") from None
    if not ctype or ctype.startswith("text/"):
        return body.decode("utf-8", "replace")
    raise Refused(415, "send JSON, a form or text")


def _query(query):
    out: dict = {}
    for k, v in list(query or [])[:MAX_FIELDS]:
        out.setdefault(str(k)[:200], []).append(str(v)[:2000])
    return {k: v[0] if len(v) == 1 else v for k, v in out.items()}


def _automation(db, automation_id):
    """The automation's name, its namespaces and whether it's switched off, as the automations table keeps them (an
    automation not stored there yet is named by its id)."""
    try:
        row = db.one("SELECT name, spaces, enabled FROM $r", r=R("automation", int(automation_id))) or {}
    except Exception:  # noqa: BLE001 - no automations table yet
        row = {}
    spaces = [s for s in row.get("spaces") or [] if isinstance(s, int) and not isinstance(s, bool)]
    name = " ".join(str(row.get("name") or "").split())[:120] or f"Automation {automation_id}"
    return name, spaces, row.get("enabled") is False


def receive(db, cfg, token, body, content_type="", query=(), headers=None, form=None):
    """Take a call to a hook: the id of its `webhook.received` activity. Refused with the status to answer otherwise:
    401 for a token that isn't a hook's or a bad signature, 403 while its automation is switched off, 413 over
    MAX_BODY, 429 over PER_MINUTE, 400 or 415 for a body it can't read."""
    headers = headers or {}
    if len(body) > MAX_BODY:
        raise Refused(413, f"send at most {MAX_BODY // 1024} KB")
    try:
        row = by_token(db, token)
    except KeyError:
        raise Refused(401, "that token isn't an automation webhook's") from None
    name, spaces, off = _automation(db, row["automation"])
    if off:
        raise Refused(403, "this webhook's automation is switched off")
    if not allow(row["id"]):
        raise Refused(429, f"at most {PER_MINUTE} calls a minute to one webhook")
    msg_id = check_signature(cfg, row, headers, body) or _header(headers, "idempotency-key")
    sent = payload(body, content_type, form)
    who = f"Webhook for {name}"
    aid = events.emit(
        db,
        "webhook.received",
        actor={"type": "Service", "id": f"lens:automation_hook/{row['id']}", "name": who},
        obj=store.clean(
            {
                "type": "Note",
                "hook": row["id"],
                "payload": sent,
                "content_type": (content_type or "").split(";")[0].strip().lower()[:100] or None,
                "query": _query(query),
            }
        ),
        space=spaces[0] if spaces else None,
        spaces=spaces if len(spaces) > 1 else None,
        audience=None if spaces else "admins",
        summary=f"{who} was called",
        key=f"automation_hook:{row['id']}:{msg_id[:MAX_KEY]}" if msg_id else None,
    )
    db.q("UPDATE $r SET last_called_at = $t, calls += 1", r=R("automation_hook", row["id"]), t=store.now())
    return aid
