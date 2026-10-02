"""Notifications (docs/notifications.md): what happens in a namespace, sent to webhooks and through Matterbridge.

A namespace's owners add targets, each with the events it wants:

- webhook: Lens's own JSON, signed the Standard Webhooks way (webhook-id, webhook-timestamp, webhook-signature:
  HMAC-SHA256 with the target's secret), for scripts, n8n, Home Assistant and the like;
- matterbridge: a message to a Matterbridge gateway's API (POST /api/message), which relays it to the chat rooms the
  gateway joins (Slack, Discord, Matrix, Telegram, IRC, Mattermost and more);
- slack and discord: a chat message to an incoming-webhook URL ({"text"} for Slack, Mattermost and Rocket.Chat;
  {"content"} for Discord).

Nothing in the rest of the app calls in here when something happens. A notifier thread in every process that runs
background work (the API's, `lens worker`) reads what changed since it last looked: runs that ended (job.finished_at)
and recordings added (recording.created_at). Each event is claimed once across every process (notify_event:<hash of
its key>, which only one CREATE wins), then queued per target (notify_delivery) and sent, with retries. It looks back
a minute each time, so a row written just as it looked isn't missed; the claim keeps that from sending twice.

Targets reach public addresses only, unless an admin allows private networks (notifications.networks): a Matterbridge
next to Lens on a LAN or a Docker network is one. Every address a target's host has is checked, and the connection goes
to the address that was checked, so a name can't be pointed elsewhere in between. Redirects aren't followed.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.parse

from . import netguard, settings, store

R = store.R
EVENTS = {
    "job.succeeded": "A run finished",
    "job.failed": "A run failed",
    "job.cancelled": "A run was cancelled",
    "batch.finished": "A batch run finished",
    "recording.added": "Something was added",
}
DEFAULT_EVENTS = ["job.failed", "batch.finished"]
KINDS = {
    "webhook": "Webhook (signed JSON)",
    "matterbridge": "Matterbridge",
    "slack": "Slack-style webhook",
    "discord": "Discord webhook",
}
NAME_MAX = 80
URL_MAX = 2000
FIELDS = (
    "record::id(id) AS id, space, name, kind, events, enabled, gateway, username, by, at, updated_by, updated_at, last, url_hint, sealed"
)
LOOKBACK = 60  # seconds each look goes back over, for rows written as the last one ran
MAX_EVENTS = 500  # rows read per look; the rest wait for the next
GROUP = 5  # more recordings than this added to a namespace in one look are one message
BACKOFF = 30  # seconds before the first retry; each later one waits four times longer, up to 6 hours
TIMEOUT = 10  # seconds a target has to answer
KEEP_DAYS = 30  # how long deliveries are kept, for the log
UA = "Lens-Notifications/1"


# ---------- targets ----------
def _sealed_ctx(tid, key):
    return f"notify:{tid}.{key}"


def _hint(url):
    """What a target's address shows once saved: scheme, host and port. The rest stays sealed, since Slack and
    Discord webhook URLs are credentials in themselves."""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}" + ("/…" if u.path.strip("/") or u.query else "")


def check_url(url, kind):
    u = (url or "").strip()
    if not u or len(u) > URL_MAX:
        raise ValueError("give the target's address (an http or https URL)")
    p = urllib.parse.urlsplit(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("the address is an http or https URL")
    if p.username or p.password:
        raise ValueError("leave the user name and password out of the address")
    if p.fragment:
        raise ValueError("the address has no #fragment")
    try:
        p.port  # noqa: B018 - raises on a bad port
    except ValueError:
        raise ValueError("the address's port isn't a number from 1 to 65535") from None
    if kind == "matterbridge" and not p.path.rstrip("/").endswith("/api/message"):
        u = u.rstrip("/") + "/api/message"  # the gateway's API address is enough
    return u


def _events(events):
    if not isinstance(events, list) or not events:
        raise ValueError("choose at least one event")
    bad = [e for e in events if e not in EVENTS]
    if bad:
        raise ValueError(f"events are {', '.join(EVENTS)}")
    return [e for e in EVENTS if e in events]


def _name(db, space, name, tid=None):
    n = " ".join((name or "").split())
    if not n:
        raise ValueError("give the target a name")
    if len(n) > NAME_MAX:
        raise ValueError(f"a name has at most {NAME_MAX} characters")
    for t in db.rows("SELECT record::id(id) AS id, name FROM notify_target WHERE space = $s", s=space):
        if t["id"] != tid and t["name"].casefold() == n.casefold():
            raise ValueError(f"there is already a target called {t['name']} here")
    return n


def _text(v, what, most=100):
    s = (v or "").strip()
    if len(s) > most:
        raise ValueError(f"{what} has at most {most} characters")
    return s


def new_secret():
    """A webhook secret the Standard Webhooks way: whsec_ and 24 random bytes in base64."""
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def view(t):
    """A target as owners see it: the address as a hint, whether a secret or token is set, never the values."""
    sealed = t.get("sealed") or {}
    out = {
        k: t.get(k)
        for k in ("id", "name", "kind", "events", "enabled", "gateway", "username", "by", "at", "updated_by", "updated_at", "last")
    }
    return {**out, "url": t.get("url_hint"), "secret_set": "secret" in sealed, "token_set": "token" in sealed}


def targets(db, space):
    return db.rows(f"SELECT {FIELDS} FROM notify_target WHERE space = $s ORDER BY name", s=space)


def get(db, tid, space=None):
    t = db.one(f"SELECT {FIELDS} FROM $t", t=R("notify_target", int(tid)))
    if not t or (space is not None and t["space"] != space):
        raise KeyError(tid)
    return t


def create(db, cfg, space, name, kind, url, events=None, gateway=None, username=None, token=None, enabled=True, by=None):
    """Add a target. A webhook gets a new secret, given back once (`secret`) to sign deliveries with."""
    if kind not in KINDS:
        raise ValueError(f"kind is {', '.join(KINDS)}")
    url = check_url(url, kind)
    d = {
        "space": space,
        "name": _name(db, space, name),
        "kind": kind,
        "events": _events(DEFAULT_EVENTS if events is None else events),
        "enabled": bool(enabled),
        "url_hint": _hint(url),
        "by": by,
        "at": store.now(),
    }
    if kind == "matterbridge":
        d["gateway"] = _text(gateway, "the gateway")
        if not d["gateway"]:
            raise ValueError("give the Matterbridge gateway to send to (its [[gateway]] name)")
        d["username"] = _text(username, "the user name", 60) or "Lens"
    tid = db.next_id("notify_target")
    sealed = {"url": settings.seal(cfg, url, _sealed_ctx(tid, "url"))}
    secret = None
    if kind == "webhook":
        secret = new_secret()
        sealed["secret"] = settings.seal(cfg, secret, _sealed_ctx(tid, "secret"))
    if kind == "matterbridge" and (token or "").strip():
        sealed["token"] = settings.seal(cfg, token.strip(), _sealed_ctx(tid, "token"))
    db.q("CREATE $r CONTENT $d", r=R("notify_target", tid), d=store.clean({**d, "sealed": sealed}))
    return get(db, tid), secret


def update(db, cfg, tid, space, changes, by=None):
    """Change a target: its name, events, whether it's on, its address (sent again in full), and for Matterbridge its
    gateway, user name and token ("" clears the token). Its kind stays."""
    t = get(db, tid, space)
    sets, sealed = {}, dict(t.get("sealed") or {})
    if "name" in changes:
        sets["name"] = _name(db, space, changes["name"], tid)
    if "events" in changes:
        sets["events"] = _events(changes["events"])
    if "enabled" in changes:
        sets["enabled"] = bool(changes["enabled"])
    if changes.get("url"):
        url = check_url(changes["url"], t["kind"])
        sealed["url"] = settings.seal(cfg, url, _sealed_ctx(tid, "url"))
        sets["url_hint"] = _hint(url)
    if t["kind"] == "matterbridge":
        if "gateway" in changes:
            sets["gateway"] = _text(changes["gateway"], "the gateway")
            if not sets["gateway"]:
                raise ValueError("give the Matterbridge gateway to send to (its [[gateway]] name)")
        if "username" in changes:
            sets["username"] = _text(changes["username"], "the user name", 60) or "Lens"
        if "token" in changes:
            if (changes["token"] or "").strip():
                sealed["token"] = settings.seal(cfg, changes["token"].strip(), _sealed_ctx(tid, "token"))
            else:
                sealed.pop("token", None)
    db.q("UPDATE $r MERGE $d", r=R("notify_target", int(tid)), d={**sets, "updated_by": by, "updated_at": store.now()})
    db.q("UPDATE $r SET sealed = $s", r=R("notify_target", int(tid)), s=sealed)  # whole, so a cleared token goes
    return get(db, tid)


def rotate_secret(db, cfg, tid, space, by=None):
    """A webhook's new secret, given back once; deliveries are signed with it from now on."""
    t = get(db, tid, space)
    if t["kind"] != "webhook":
        raise ValueError("only webhooks sign what they send")
    secret = new_secret()
    sealed = {**(t.get("sealed") or {}), "secret": settings.seal(cfg, secret, _sealed_ctx(tid, "secret"))}
    db.q("UPDATE $r SET sealed = $s, updated_by = $b, updated_at = $t", r=R("notify_target", tid), s=sealed, b=by, t=store.now())
    return secret


def delete(db, tid, space):
    t = get(db, tid, space)
    db.q("DELETE notify_delivery WHERE target = $t", t=int(tid))
    db.q("DELETE $r", r=R("notify_target", int(tid)))
    return t


def _unsealed(cfg, t, key):
    s = (t.get("sealed") or {}).get(key)
    if not s:
        return None
    try:
        return settings.unseal(cfg, s, _sealed_ctx(t["id"], key))
    except Exception:  # noqa: BLE001 - the server's key changed since it was saved
        raise ValueError(f"the target's {key} can't be read with this server's key; save it again") from None


# ---------- sending ----------
def networks(cfg):
    """The private networks an admin allowed targets in (notifications.networks), besides public addresses."""
    return [ipaddress.ip_network(str(n), strict=False) for n in (cfg.get("notifications") or {}).get("networks") or []]


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to an address already checked, with the certificate checked against the host name."""

    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def post(cfg, url, body, headers, timeout=TIMEOUT):
    """POST to a target: (status, the start of its answer). ValueError when the address isn't allowed; OSError and
    http.client errors when it can't be reached."""
    u = urllib.parse.urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    nets = networks(cfg)
    try:
        ip, _ = netguard.resolve(u.hostname, port, lambda a: netguard.public_ip(a, nets))
    except ValueError as e:
        if "public address" in str(e):
            raise ValueError(
                f"{u.hostname} isn't a public address; an admin can allow its network under Notifications in Settings"
            ) from None
        raise
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    if u.scheme == "https":
        conn = _Pinned(u.hostname, port, ip, timeout=timeout, context=ssl.create_default_context())
    else:
        conn = _PinnedHTTP(u.hostname, port, ip, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={"User-Agent": UA, "Content-Type": "application/json", **headers})
        r = conn.getresponse()
        return r.status, r.read(2000).decode("utf-8", "replace")
    finally:
        conn.close()


def sign(secret, msg_id, timestamp, body):
    """The Standard Webhooks signature of a delivery: v1,<base64 HMAC-SHA256 of "<id>.<timestamp>.<body>">, keyed with
    the secret's bytes (what follows whsec_, in base64)."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def _chat_text(event):
    return event["text"] + (f"\n{event['url']}" if event.get("url") else "")


def request_for(cfg, t, event, msg_id):
    """(url, body bytes, headers) of one event for one target."""
    url = _unsealed(cfg, t, "url")
    if not url:
        raise ValueError("the target has no address; save it again")
    kind, headers = t["kind"], {}
    if kind == "webhook":
        body = json.dumps({k: v for k, v in event.items() if k != "space"}, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        headers = {"webhook-id": msg_id, "webhook-timestamp": ts, "X-Lens-Event": event["type"]}
        secret = _unsealed(cfg, t, "secret")
        if secret:
            headers["webhook-signature"] = sign(secret, msg_id, ts, body)
    elif kind == "matterbridge":
        body = json.dumps({"text": _chat_text(event), "username": t.get("username") or "Lens", "gateway": t.get("gateway")}).encode()
        token = _unsealed(cfg, t, "token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif kind == "discord":
        body = json.dumps({"content": _chat_text(event)[:2000], "allowed_mentions": {"parse": []}}).encode()
    else:  # slack, Mattermost, Rocket.Chat
        body = json.dumps({"text": _chat_text(event)}).encode()
    return url, body, headers


def _fault(status, text):
    snippet = " ".join((text or "").split())[:200]
    return f"HTTP {status}" + (f": {snippet}" if snippet else "")


def send(db, cfg, t, event, msg_id):
    """Send one event to one target now: {ok, code, error}. Notes how it went on the target (`last`)."""
    code, error = None, None
    try:
        url, body, headers = request_for(cfg, t, event, msg_id)
        code, text = post(cfg, url, body, headers)
        if not 200 <= code < 300:
            error = _fault(code, text)
    except ValueError as e:
        error = str(e)
    except (OSError, http.client.HTTPException) as e:
        error = f"couldn't reach it: {type(e).__name__}: {e}"[:300]
    last = store.clean({"at": store.now(), "ok": error is None, "code": code, "error": error, "event": event["type"]})
    db.q("UPDATE $r SET last = $l", r=R("notify_target", int(t["id"])), l=last)
    return {"ok": error is None, "code": code, "error": error}


def _permanent(code):
    """A refusal that trying again won't change: a 4xx other than a timeout or too many requests."""
    return code is not None and 400 <= code < 500 and code not in (408, 425, 429)


def _later(attempts):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=min(6 * 3600, BACKOFF * 4 ** (attempts - 1)))).isoformat(
        timespec="seconds"
    )


def _queue(db, t, event):
    db.q(
        "CREATE notify_delivery CONTENT $d",
        d={
            "target": int(t["id"]),
            "space": t["space"],
            "event": event["type"],
            "key": event["id"],
            "payload": json.dumps(event),
            "status": "pending",
            "attempts": 0,
            "next_at": store.now(),
            "created_at": store.now(),
        },
    )


DELIVERY_FIELDS = "record::id(id) AS id, target, event, key, status, attempts, code, error, created_at, sent_at, next_at"


def deliveries(db, tid, limit=50):
    """A target's latest deliveries, newest first."""
    rows = db.rows(
        f"SELECT {DELIVERY_FIELDS}, payload FROM notify_delivery WHERE target = $t ORDER BY created_at DESC LIMIT {int(limit)}", t=int(tid)
    )
    for r in rows:
        r["id"] = str(r["id"])
        r["text"] = (json.loads(r.pop("payload") or "{}") or {}).get("text")
    return rows


def deliver_due(db, cfg, limit=20, worker=None):
    """Send the deliveries that are due, each claimed first so that only one process sends it. How many were tried."""
    t, n = store.now(), 0
    most = int((cfg.get("notifications") or {}).get("max_attempts") or 6)
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    db.q("UPDATE notify_delivery SET status = 'pending' WHERE status = 'sending' AND claimed_at < $s", s=stale)  # its sender died
    rows = db.rows(
        "SELECT record::id(id) AS id, next_at FROM notify_delivery WHERE status = 'pending' AND next_at <= $t "
        f"ORDER BY next_at LIMIT {int(limit)}",
        t=t,
    )
    for row in rows:
        dr = R("notify_delivery", row["id"])
        got = db.rows(
            "UPDATE $d SET status = 'sending', claimed_at = $t, claimed_by = $w WHERE status = 'pending' RETURN AFTER",
            d=dr,
            t=store.now(),
            w=worker,
        )
        if not got:
            continue
        d = got[0]
        n += 1
        try:
            target = get(db, d["target"])
        except KeyError:
            target = None
        if not target or not target.get("enabled"):
            db.q("UPDATE $d SET status = 'dropped', error = $e", d=dr, e="the target was turned off or deleted")
            continue
        attempts = int(d.get("attempts") or 0) + 1
        r = send(db, cfg, target, json.loads(d["payload"]), f"msg_{row['id']}")
        if r["ok"]:
            db.q(
                "UPDATE $d SET status = 'sent', attempts = $a, code = $c, error = NONE, sent_at = $t",
                d=dr,
                a=attempts,
                c=r["code"],
                t=store.now(),
            )
        else:
            done = attempts >= most or _permanent(r["code"])
            db.q(
                "UPDATE $d SET status = $s, attempts = $a, code = $c, error = $e, next_at = $n",
                d=dr,
                s="failed" if done else "pending",
                a=attempts,
                c=r["code"],
                e=r["error"],
                n=store.now() if done else _later(attempts),
            )
    return n


def test(db, cfg, tid, space, by=None):
    """Send a test message to a target now, and keep it in its deliveries: {ok, code, error}."""
    t = get(db, tid, space)
    ns = (db.one("SELECT name FROM $s", s=R("space", space)) or {}).get("name")
    event = _event(
        "test",
        f"test-{tid}-{secrets.token_hex(4)}",
        space,
        ns,
        f"Test from Lens: {t['name']} in {ns} gets notifications here.",
        None,
        {"by": by},
    )
    r = send(db, cfg, t, event, f"msg_test_{secrets.token_hex(8)}")
    db.q(
        "CREATE notify_delivery CONTENT $d",
        d=store.clean(
            {
                "target": int(tid),
                "space": space,
                "event": "test",
                "key": event["id"],
                "payload": json.dumps(event),
                "status": "sent" if r["ok"] else "failed",
                "attempts": 1,
                "code": r["code"],
                "error": r["error"],
                "created_at": store.now(),
                "sent_at": store.now() if r["ok"] else None,
            }
        ),
    )
    return r


# ---------- what happened ----------
def _iso_ago(ts, seconds):
    return (dt.datetime.fromisoformat(ts) - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def app_url(cfg):
    """Where the web app is, for links in messages: notifications.app_url, else FRONTEND_URL."""
    return ((cfg.get("notifications") or {}).get("app_url") or os.environ.get("FRONTEND_URL") or "").rstrip("/") or None


def _link(cfg, path):
    base = app_url(cfg)
    return f"{base}{path}" if base else None


def _event(kind, key, space, ns, text, url, data):
    return {
        "id": key,
        "type": kind,
        "timestamp": store.now(),
        "namespace": ns,
        "space": space,
        "text": text,
        "url": url,
        "data": store.clean(data),
    }


def _q(title):
    return f"“{title}”" if title else "A recording"


def _job_events(db, cfg, since, floor, spaces, names):
    rows = db.rows(
        "SELECT record::id(id) AS id, recording, space, batch, steps, step_index, status, error, finished_at FROM job "
        f"WHERE finished_at >= $c AND finished_at > $f AND status IN ['succeeded', 'failed', 'cancelled'] AND space IN $sp ORDER BY finished_at LIMIT {MAX_EVENTS}",
        c=since,
        f=floor,
        sp=spaces,
    )
    titles = (
        {
            r["id"]: r.get("title")
            for r in db.rows(
                "SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in {j["recording"] for j in rows}],
            )
        }
        if rows
        else {}
    )
    out, batches = [], set()
    for j in rows:
        if j.get("batch") is not None:
            batches.add(j["batch"])  # a batch run's jobs are told as one, when it finishes
            continue
        ns, title, st = names.get(j["space"]), titles.get(j["recording"]), j["status"]
        steps = [s if isinstance(s, str) else s.get("type") for s in j.get("steps") or []]
        if st == "succeeded":
            text = f"{_q(title)} finished processing in {ns} ({', '.join(steps)})."
        elif st == "failed":
            i = j.get("step_index") or 0
            step = steps[i] if i < len(steps) else None
            text = f"{_q(title)} failed{f' at {step}' if step else ''} in {ns}: {j.get('error') or 'no reason given'}"
        else:
            text = f"Processing {_q(title)} was cancelled in {ns}."
        out.append(
            _event(
                f"job.{st}",
                f"job-{j['id']}-{st}-{j['finished_at']}",
                j["space"],
                ns,
                text,
                _link(cfg, f"/activity/{j['id']}"),
                {
                    "job": j["id"],
                    "recording": j["recording"],
                    "title": title,
                    "status": st,
                    "steps": steps,
                    "error": j.get("error"),
                    "finished_at": j["finished_at"],
                    "recording_url": _link(cfg, f"/resources/{j['recording']}"),
                },
            )
        )
    return out, rows, batches


def _batch_events(db, cfg, bids, spaces, names):
    from . import batches as bmod

    out = []
    for bid in sorted(bids):
        try:
            b = bmod.get(db, bid)
        except KeyError:
            continue
        if b["status"] not in ("finished", "sample done"):
            continue
        counts = b["progress"]["counts"]
        ok, bad, gone = counts.get("succeeded", 0), counts.get("failed", 0), counts.get("cancelled", 0)
        where = sorted(set(db.values("SELECT VALUE space FROM job WHERE batch = $b", b=bid)) & set(spaces))
        what = "The sample of batch run" if b["status"] == "sample done" else "Batch run"
        parts = [f"{ok} done"] + ([f"{bad} failed"] if bad else []) + ([f"{gone} cancelled"] if gone else [])
        for sp in where:
            out.append(
                _event(
                    "batch.finished",
                    f"batch-{bid}-{sp}-{b['status']}-{ok}-{bad}-{gone}",
                    sp,
                    names.get(sp),
                    f"{what} “{b.get('label') or bid}” finished: {', '.join(parts)}.",
                    _link(cfg, f"/batches/{bid}"),
                    {
                        "batch": bid,
                        "label": b.get("label"),
                        "status": b["status"],
                        "counts": counts,
                        "sample": b["status"] == "sample done",
                    },
                )
            )
    return out


SOURCES = {"audio": "recording", "document": "document", "image": "image", "web": "web page"}


def _added_events(db, cfg, since, floor, spaces, names):
    rows = db.rows(
        "SELECT record::id(id) AS id, space, title, source, created_at FROM recording "
        f"WHERE created_at >= $c AND created_at > $f AND space IN $sp ORDER BY created_at LIMIT {MAX_EVENTS}",
        c=since,
        f=floor,
        sp=spaces,
    )
    return [
        _event(
            "recording.added",
            f"recording-{r['id']}-added",
            r["space"],
            names.get(r["space"]),
            f"{_q(r.get('title'))} was added to {names.get(r['space'])}.",
            _link(cfg, f"/resources/{r['id']}"),
            {"recording": r["id"], "title": r.get("title"), "source": r.get("source"), "created_at": r["created_at"]},
        )
        for r in rows
    ], rows


_CLAIMED: dict[str, None] = {}  # keys this process has claimed or seen claimed, so it doesn't ask again
_CL = threading.Lock()


def _claim(db, key):
    """Whether this process is the one to send the event: the first CREATE of notify_event:<hash> across all of them."""
    with _CL:
        if key in _CLAIMED:
            return False
        _CLAIMED[key] = None
        while len(_CLAIMED) > 20000:
            _CLAIMED.pop(next(iter(_CLAIMED)))
    try:
        db.q("CREATE $r CONTENT $d", r=R("notify_event", hashlib.sha1(key.encode()).hexdigest()), d={"key": key, "at": store.now()})
        return True
    except Exception:  # noqa: BLE001 - another process claimed it
        return False


def _grouped(events, cfg):
    """recording.added events, with a namespace's that came in one look past GROUP made into one."""
    by_space: dict = {}
    for e in events:
        by_space.setdefault(e["space"], []).append(e)
    out = []
    for sp, evs in by_space.items():
        evs.sort(key=lambda e: e["data"]["recording"])
        if len(evs) <= GROUP:
            out += evs
            continue
        ns = evs[0]["namespace"]
        titles = [e["data"].get("title") for e in evs]
        named = ", ".join(f"“{t}”" for t in titles[:3] if t)
        out.append(
            _event(
                "recording.added",
                f"recordings-{evs[0]['data']['recording']}-{len(evs)}",
                sp,
                ns,
                f"{len(evs)} items were added to {ns}" + (f", among them {named}." if named else "."),
                _link(cfg, "/library"),
                {"recordings": [e["data"]["recording"] for e in evs], "count": len(evs)},
            )
        )
    return out


def _mark(db):
    """Start from now: what happened before isn't sent, not even what the lookback would see."""
    t = store.now()
    db.q("UPSERT $s SET since = $t, floor = $t", s=R("notify_state", "scan"), t=t)


def scan(db, cfg):
    """Look for what happened since the last look and queue it for the targets that want it. How many deliveries
    were queued. The first look only marks where to start: nothing that happened before notifications were set up is
    sent."""
    t_now = store.now()
    st = db.one("SELECT since, floor FROM $s", s=R("notify_state", "scan"))
    live = db.rows(f"SELECT {FIELDS} FROM notify_target WHERE enabled = true") if st else []
    if not live:
        _mark(db)
        return 0
    spaces = sorted({t["space"] for t in live})
    names = {
        r["id"]: r["name"]
        for r in db.rows("SELECT record::id(id) AS id, name FROM space WHERE id IN $ids", ids=[R("space", s) for s in spaces])
    }
    # the floor is where notifications started: what happened in that second may have come before, so it's left out
    since, floor = _iso_ago(st["since"], LOOKBACK), st.get("floor") or ""
    wanted = {e for t in live for e in t.get("events") or []}
    events, seen = [], [t_now]
    if wanted & {"job.succeeded", "job.failed", "job.cancelled", "batch.finished"}:
        evs, rows, bids = _job_events(db, cfg, since, floor, spaces, names)
        events += evs
        if "batch.finished" in wanted:
            events += _batch_events(db, cfg, bids, spaces, names)
        if len(rows) >= MAX_EVENTS:
            seen.append(rows[-1]["finished_at"])
    if "recording.added" in wanted:
        evs, rows = _added_events(db, cfg, since, floor, spaces, names)
        events += _grouped([e for e in evs if _claim(db, e["id"])], cfg)
        if len(rows) >= MAX_EVENTS:
            seen.append(rows[-1]["created_at"])
    n = 0
    for e in events:
        if e["type"] != "recording.added" and not _claim(db, e["id"]):
            continue
        for t in live:
            if t["space"] == e["space"] and e["type"] in (t.get("events") or []):
                _queue(db, t, e)
                n += 1
    # a full page means more is waiting: start the next look where this one stopped (its own lookback covers the edge)
    db.q("UPSERT $s SET since = $t", s=R("notify_state", "scan"), t=min(seen))
    return n


def prune(db):
    """Forget deliveries older than KEEP_DAYS and the claims that only matter while the lookback can see them."""
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    db.q("DELETE notify_delivery WHERE created_at < $o AND status IN ['sent', 'failed', 'dropped']", o=old)
    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(timespec="seconds")
    db.q("DELETE notify_event WHERE at < $d", d=day)


def tick(db, cfg, worker=None):
    """One round: look for what happened, then send what's due."""
    if not (cfg.get("notifications") or {}).get("enabled", True):
        _mark(db)  # turned on again, it starts from then
        return 0
    scan(db, cfg)
    return deliver_due(db, cfg, worker=worker)


def start(db, cfg_fn, stop, name=None, log=None):
    """The notifier thread: a round every notifications.poll_seconds until `stop` is set."""

    def loop():
        pruned = 0.0
        while not stop.is_set():
            cfg = cfg_fn()
            try:
                tick(db, cfg, name)
                if time.time() - pruned > 3600:
                    prune(db)
                    pruned = time.time()
            except Exception as e:  # noqa: BLE001 - keep notifying
                if log:
                    log(f"notifications: {type(e).__name__}: {e}")
            stop.wait(max(1, int((cfg.get("notifications") or {}).get("poll_seconds") or 5)))

    th = threading.Thread(target=loop, daemon=True, name="notifier")
    th.start()
    return th
