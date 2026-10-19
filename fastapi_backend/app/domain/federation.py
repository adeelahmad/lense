"""Circles between Lens servers (docs/federation.md): people on another Lens, or any ActivityPub server, in your
circles, and what you share with those circles reaching them.

Off unless an admin turns it on (federation.enabled) and Lens has a public https:// address (federation.url, else the
HTTPS domain). Even then nobody is findable until they say so themselves: `findable` gives them a handle
(`sam@lens.example`), an RSA key pair (the private key sealed with the server's secret), a WebFinger entry and an
ActivityPub actor. Turning it off takes all of that away.

- A person adds someone elsewhere to a circle by their handle. Lens looks them up (WebFinger, then their actor, both
  over https, at public addresses only, through netguard) and keeps their inbox and public key.
- Sharing with a circle that has such people sends each a `Create` of a `Note` (the share's name, summary and note,
  never the item itself or a link into this Lens), signed with the sharer's key (HTTP Signatures, rsa-sha256), from a
  queue `deliver_due` works through with back-off.
- A signed `Create` of a `Note` sent to someone's inbox is taken only from a person they have in one of their own
  circles, checked against the key Lens kept for them (it never fetches a key an inbox request names), within five
  minutes of its Date, with its Digest matching. It shows in their timeline as `share.received`, its text marked as
  outside text. `Delete` and `Undo` of it take it back. Everything else is accepted and dropped.

Nothing is public: the outbox is always empty, actors say they aren't discoverable, and no model is called.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import auth, circles, events, netguard, store

R = store.R
AS = "https://www.w3.org/ns/activitystreams"
CONTEXT = [AS, "https://w3id.org/security/v1"]
AP = "application/activity+json"
ACCEPT = f'{AP}, application/ld+json; profile="{AS}"'
JRD = "application/jrd+json"
MAX_BODY = 256 * 1024  # an inbox request, or a document fetched
SKEW = 300  # seconds a signed request's Date may be off
HANDLE_RX = re.compile(r"^@?([a-z0-9_][a-z0-9_.-]{0,63})@([a-z0-9.-]+\.[a-z]{2,63}|localhost)(?::(\d{1,5}))?$", re.I)
SIGNED = ("(request-target)", "host", "date", "digest")
BACKOFF = (60, 300, 1800, 7200, 43200)  # seconds before each retry; then it's given up
TEXT_MAX = 4000
UNTRUSTED = ("actor.name", "object.name", "object.summary", "object.content")
AGENT = "Lens (+https://github.com/adeelahmad/lense)"


class Problem(circles.Problem):
    """Something a person can fix: the message says what."""


class Unsigned(PermissionError):
    """An inbox request that isn't signed, or whose signature doesn't check out (401, not 403)."""


# ---------- the server ----------
def conf(cfg):
    return cfg.get("federation") or {}


def base(cfg):
    """This Lens's public https:// address ("" when it has none)."""
    from . import https

    url = str(conf(cfg).get("url") or "").strip().rstrip("/") or https.public_url(cfg)
    return url if url.startswith("https://") else ""


def domain(cfg):
    """The host (with its port, when not 443) handles end in."""
    return urllib.parse.urlsplit(base(cfg)).netloc.lower()


def on(cfg):
    return bool(conf(cfg).get("enabled")) and bool(base(cfg))


def check(cfg):
    if not conf(cfg).get("enabled"):
        raise Problem("an admin hasn't turned on circles with other servers (Settings → Circles elsewhere)")
    if not base(cfg):
        raise Problem("Lens has no public https:// address: set one in Settings → Circles elsewhere, or turn on HTTPS")


# ---------- people here ----------
def _slug(acc):
    raw = (acc.get("name") or "").strip() or str(acc.get("email") or "").split("@")[0]
    s = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")[:30]
    return s or f"person{acc['id']}"


def _person(db, account=None, handle=None):
    if account is not None:
        return db.one("SELECT * FROM ap_person WHERE account = $a LIMIT 1", a=int(account))
    return db.one("SELECT * FROM ap_person WHERE handle = $h LIMIT 1", h=str(handle or "").lower())


def actor_url(cfg, handle):
    return f"{base(cfg)}/ap/users/{handle}"


def me(db, cfg, account):
    """{on, findable, handle, actor} for someone here: whether federation is on, and their handle if they're findable."""
    p = _person(db, account=account)
    return {
        "on": on(cfg),
        "findable": bool(p),
        "handle": f"{p['handle']}@{domain(cfg)}" if p and on(cfg) else None,
        "actor": actor_url(cfg, p["handle"]) if p and on(cfg) else None,
    }


def findable(db, cfg, acc, yes=True):
    """Let people on other servers find `acc` by a handle (a key pair and an actor), or stop (all of it removed)."""
    if not yes:
        db.q("DELETE ap_person WHERE account = $a", a=acc["id"])
        return me(db, cfg, acc["id"])
    check(cfg)
    if _person(db, account=acc["id"]):
        return me(db, cfg, acc["id"])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from . import settings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    stem, n = _slug(acc), 1
    handle = stem
    while _person(db, handle=handle):
        n += 1
        handle = f"{stem}{n}"
    db.q(
        "CREATE $r CONTENT $d",
        r=R("ap_person", db.next_id("ap_person")),
        d={
            "account": acc["id"],
            "handle": handle,
            "public": public.decode(),
            "private": settings.seal(cfg, private.decode(), f"ap_person:{acc['id']}"),
            "at": store.now(),
        },
    )
    return me(db, cfg, acc["id"])


def webfinger(db, cfg, resource):
    """The JRD for `acct:handle@domain` when that person is findable; KeyError otherwise."""
    m = re.match(r"^acct:([^@]+)@(.+)$", str(resource or "").strip(), re.I)
    if not on(cfg) or not m or m.group(2).lower() != domain(cfg):
        raise KeyError(resource)
    p = _person(db, handle=m.group(1))
    if not p:
        raise KeyError(resource)
    return {
        "subject": f"acct:{p['handle']}@{domain(cfg)}",
        "aliases": [actor_url(cfg, p["handle"])],
        "links": [{"rel": "self", "type": AP, "href": actor_url(cfg, p["handle"])}],
    }


def actor(db, cfg, handle):
    """A findable person's actor (their name, inbox and public key); KeyError otherwise."""
    p = _person(db, handle=handle) if on(cfg) else None
    acc = auth.get_account(db, p["account"]) if p else None
    if not acc or acc.get("disabled") or acc.get("removed_at"):
        raise KeyError(handle)
    me_ = actor_url(cfg, p["handle"])
    return {
        "@context": CONTEXT,
        "id": me_,
        "type": "Person",
        "preferredUsername": p["handle"],
        "name": acc.get("name") or p["handle"],
        "inbox": f"{me_}/inbox",
        "outbox": f"{me_}/outbox",
        "manuallyApprovesFollowers": True,
        "discoverable": False,
        "indexable": False,
        "publicKey": {"id": f"{me_}#main-key", "owner": me_, "publicKeyPem": p["public"]},
    }


def outbox(db, cfg, handle):
    """Always empty: what's shared goes to the people it's for, never to a public outbox."""
    me_ = actor(db, cfg, handle)["id"]
    return {"@context": AS, "id": f"{me_}/outbox", "type": "OrderedCollection", "totalItems": 0, "orderedItems": []}


# ---------- people elsewhere ----------
def _fetch(cfg, url, accept=ACCEPT, method="GET", body=None, headers=None):
    """A request to another server: https only, to public addresses (and the networks in documents.web_networks)
    only, through netguard, no redirects for anything but a GET. (status, bytes); RuntimeError saying what failed."""
    from . import webcapture  # it imports what imports this

    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise RuntimeError(f"{url} isn't an https:// address")
    try:
        webcapture.check_url(cfg, url)
    except ValueError as e:
        raise RuntimeError(f"{u.hostname} can't be reached: {e}") from None
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": AGENT, "Accept": accept, **(headers or {})})
    with netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=MAX_BODY * 2) as guard:
        handlers = [
            netguard.Through({"http": guard.url, "https": guard.url}),
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
        ]
        if method == "GET":
            handlers.append(urllib.request.HTTPRedirectHandler())
        o = urllib.request.OpenerDirector()
        for h in handlers:
            o.add_handler(h)
        try:
            with o.open(req, timeout=20) as r:
                return r.status, r.read(MAX_BODY + 1)[:MAX_BODY]
        except urllib.error.HTTPError as e:
            return e.code, b""
        except (urllib.error.URLError, OSError) as e:
            refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
            raise RuntimeError(f"{u.hostname} didn't answer: {getattr(e, 'reason', e)}{refused}") from None


def _json(cfg, url, accept=ACCEPT):
    status, raw = _fetch(cfg, url, accept)
    if status != 200:
        raise RuntimeError(f"{urllib.parse.urlsplit(url).hostname} answered {status}")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"{url} isn't JSON") from None
    if not isinstance(doc, dict):
        raise RuntimeError(f"{url} isn't a JSON object")
    return doc


def _text(v, n=TEXT_MAX):
    """Plain text from what another server sent (HTML tags taken out), at most `n` characters."""
    if isinstance(v, dict):  # a language map
        v = next(iter(v.values()), "")
    s = re.sub(r"<br\s*/?>|</p>\s*<p[^>]*>", "\n", str(v or ""), flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = "\n".join(" ".join(line.split()) for line in s.splitlines()).strip()
    return s[:n]


def _remote_row(db, rid):
    return db.one("SELECT record::id(id) AS id, * FROM $r", r=R("ap_remote", int(rid)))


def remote_people(db, ids):
    """{ap_remote id: {id, handle, name}} for `ids`."""
    ids = sorted({int(i) for i in ids or []})
    if not ids:
        return {}
    rows = db.rows("SELECT record::id(id) AS id, handle, name FROM ap_remote WHERE id IN $r", r=[R("ap_remote", i) for i in ids])
    return {r["id"]: {"id": r["id"], "handle": r["handle"], "name": r.get("name")} for r in rows}


def resolve(db, cfg, handle):
    """Someone elsewhere, by handle (`sam@lens.example`): looked up (WebFinger, then their actor) and kept, with their
    inbox and public key. Their ap_remote id; Problem saying what's wrong."""
    check(cfg)
    m = HANDLE_RX.match(str(handle or "").strip())
    if not m:
        raise Problem(f"{handle} isn't a handle: write it like sam@lens.example")
    user, host = m.group(1).lower(), (m.group(2) + (f":{m.group(3)}" if m.group(3) else "")).lower()
    if host == domain(cfg):
        raise Problem(f"{user}@{host} is on this Lens: add them by their email instead")
    handle = f"{user}@{host}"
    try:
        jrd = _json(cfg, f"https://{host}/.well-known/webfinger?resource=acct:{urllib.parse.quote(handle)}", f"{JRD}, application/json")
        href = next(
            (
                link.get("href")
                for link in jrd.get("links") or []
                if isinstance(link, dict)
                and link.get("rel") == "self"
                and str(link.get("type") or "").split(";")[0] in (AP, "application/ld+json")
            ),
            None,
        )
        if not href:
            raise RuntimeError(f"{handle} has no ActivityPub actor")
        doc = _json(cfg, href)
    except RuntimeError as e:
        raise Problem(f"couldn't find {handle}: {e}") from None
    return _keep(db, doc, href, handle)


def _keep(db, doc, href, handle):
    """Check an actor document and keep it (one row per actor id): its id is where it was fetched from, its inbox is
    https, and its key is its own and an RSA public key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    aid, inbox, key = doc.get("id"), doc.get("inbox"), doc.get("publicKey") or {}
    if isinstance(key, list):
        key = next((k for k in key if isinstance(k, dict)), {})
    if aid != href or not str(inbox or "").startswith("https://") or not str(aid).startswith("https://"):
        raise Problem(f"{handle}'s actor doesn't check out: its id, or its inbox, isn't where it should be")
    if key.get("owner") != aid or not str(key.get("id") or "").startswith("https://"):
        raise Problem(f"{handle}'s key isn't their own")
    try:
        pub = serialization.load_pem_public_key(str(key.get("publicKeyPem") or "").encode())
    except ValueError:
        raise Problem(f"{handle}'s key can't be read") from None
    if not isinstance(pub, rsa.RSAPublicKey):
        raise Problem(f"{handle}'s key isn't an RSA key")
    row = {
        "actor": aid,
        "handle": handle,
        "name": _text(doc.get("name"), 120) or handle,
        "inbox": inbox,
        "key_id": key["id"],
        "public": key["publicKeyPem"],
        "fetched": store.now(),
    }
    had = db.one("SELECT record::id(id) AS id FROM ap_remote WHERE actor = $a LIMIT 1", a=aid)
    if had:
        db.q("UPDATE $r MERGE $d", r=R("ap_remote", had["id"]), d=row)
        return had["id"]
    rid = db.next_id("ap_remote")
    db.q("CREATE $r CONTENT $d", r=R("ap_remote", rid), d=row)
    return rid


# ---------- signatures ----------
def _digest(body):
    return "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()


def _signing_string(method, path, headers, names):
    lines = []
    for n in names:
        if n == "(request-target)":
            lines.append(f"(request-target): {method.lower()} {path}")
        else:
            lines.append(f"{n}: {headers.get(n, '')}")
    return "\n".join(lines).encode()


def sign(db, cfg, account, method, url, body):
    """The headers that sign a request as someone here (Host, Date, Digest, Signature)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    from . import settings

    p = _person(db, account=account)
    if not p:
        raise Problem("you aren't findable on other servers: turn that on first")
    u = urllib.parse.urlsplit(url)
    path = u.path + (f"?{u.query}" if u.query else "")
    h = {"host": u.netloc, "date": email.utils.formatdate(usegmt=True), "digest": _digest(body)}
    key = serialization.load_pem_private_key(settings.unseal(cfg, p["private"], f"ap_person:{account}").encode(), None)
    sig = key.sign(_signing_string(method, path, h, SIGNED), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
    params = {
        "keyId": f"{actor_url(cfg, p['handle'])}#main-key",
        "algorithm": "rsa-sha256",
        "headers": " ".join(SIGNED),
        "signature": base64.b64encode(sig).decode(),
    }
    return {
        "Host": h["host"],
        "Date": h["date"],
        "Digest": h["digest"],
        "Signature": ",".join(f'{k}="{v}"' for k, v in params.items()),
    }


def _params(sig):
    return {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', sig or "")}


def verify(db, cfg, method, path, headers, body, now=None):
    """The ap_remote row of who signed an inbox request: a key Lens already keeps for someone in a circle, over at
    least (request-target), host, date and digest, rsa-sha256, the Date within SKEW, the Digest the body's.
    Unsigned saying what's wrong."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    headers = {k.lower(): v for k, v in headers.items()}
    auth_ = str(headers.get("authorization") or "")
    p = _params(headers.get("signature") or (auth_[10:] if auth_.lower().startswith("signature ") else ""))
    names = (p.get("headers") or "date").lower().split()
    if not p.get("keyId") or not p.get("signature") or not set(SIGNED) <= set(names):
        raise Unsigned(f"sign the request over {' '.join(SIGNED)}")
    if p.get("algorithm", "rsa-sha256").lower() not in ("rsa-sha256", "hs2019"):
        raise Unsigned("sign with rsa-sha256")
    if headers.get("digest") != _digest(body):
        raise Unsigned("the Digest isn't the body's")
    try:
        when = email.utils.parsedate_to_datetime(headers.get("date") or "")
    except (TypeError, ValueError):
        raise Unsigned("the Date can't be read") from None
    now = now or dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None or abs((now - when).total_seconds()) > SKEW:
        raise Unsigned("the Date is more than five minutes off")
    who = db.one("SELECT record::id(id) AS id, * FROM ap_remote WHERE key_id = $k LIMIT 1", k=p["keyId"])
    if not who:
        raise Unsigned("nobody here has the sender in a circle")
    key = serialization.load_pem_public_key(who["public"].encode())
    headers = {**headers, "host": domain(cfg)}  # what it was sent to; behind the web app this server sees another Host
    try:
        key.verify(base64.b64decode(p["signature"]), _signing_string(method, path, headers, names), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
    except (InvalidSignature, ValueError):
        raise Unsigned("the signature doesn't check out") from None
    return who


# ---------- sending ----------
def _in_circles(db, owner, remote_id):
    return bool(db.values("SELECT VALUE id FROM circle WHERE owner = $o AND remote CONTAINS $r LIMIT 1", o=int(owner), r=int(remote_id)))


def queue_share(db, cfg, me_, sid, at, obj, note, remote_ids):
    """Queue a share for the people elsewhere in the circles it went to: a Create of a Note each, delivered by
    deliver_due. How many were queued (none while federation is off or `me_` isn't findable)."""
    p = _person(db, account=me_["id"])
    if not remote_ids or not on(cfg) or not p:
        return 0
    who = remote_people(db, remote_ids)
    rows = db.rows("SELECT record::id(id) AS id, actor, inbox FROM ap_remote WHERE id IN $r", r=[R("ap_remote", i) for i in who])
    me_url = actor_url(cfg, p["handle"])
    sid_url = f"{base(cfg)}/ap/shares/{sid}"
    text = "\n\n".join(x for x in (note, obj.get("summary")) if x)
    paras = "".join(f"<p>{html.escape(x)}</p>" for x in text.split("\n\n") if x.strip())
    stmts, params = [], {}
    for i, r in enumerate(rows):
        doc = {
            "@context": AS,
            "id": f"{sid_url}#to-{r['id']}",
            "type": "Create",
            "actor": me_url,
            "to": [r["actor"]],
            "published": at,
            "object": store.clean(
                {
                    "id": f"{sid_url}/note",
                    "type": "Note",
                    "attributedTo": me_url,
                    "to": [r["actor"]],
                    "name": obj.get("name"),
                    "summary": None,
                    "content": paras or html.escape(str(obj.get("name") or "")),
                    "published": at,
                    "tag": [{"type": "Hashtag", "name": t["name"]} for t in obj.get("tag") or [] if isinstance(t, dict) and t.get("name")]
                    or None,
                }
            ),
        }
        stmts.append(f"CREATE $q{i} CONTENT $d{i}")
        params[f"q{i}"] = R("ap_delivery", db.next_id("ap_delivery"))
        params[f"d{i}"] = {
            "share": sid,
            "by": me_["id"],
            "to": r["id"],
            "inbox": r["inbox"],
            "body": json.dumps(doc),
            "tries": 0,
            "next_at": at,
            "at": at,
        }
    if stmts:
        db.run(stmts, **params)
    return len(stmts)


def queue_undo(db, cfg, me_, sid):
    """Queue a Delete of a share's Note to everyone elsewhere it was delivered to (or still waits for)."""
    rows = db.rows("SELECT record::id(id) AS id, to, inbox, done_at FROM ap_delivery WHERE share = $s AND kind = NONE", s=int(sid))
    db.q("DELETE ap_delivery WHERE share = $s AND done_at = NONE", s=int(sid))
    p = _person(db, account=me_["id"])
    sent = [r for r in rows if r.get("done_at")]
    if not sent or not p or not on(cfg):
        return 0
    me_url = actor_url(cfg, p["handle"])
    at = store.now()
    note = f"{base(cfg)}/ap/shares/{sid}/note"
    for r in sent:
        doc = {"@context": AS, "id": f"{note}#delete-{r['to']}", "type": "Delete", "actor": me_url, "object": note, "to": []}
        db.q(
            "CREATE $q CONTENT $d",
            q=R("ap_delivery", db.next_id("ap_delivery")),
            d={
                "share": int(sid),
                "kind": "delete",
                "by": me_["id"],
                "to": r["to"],
                "inbox": r["inbox"],
                "body": json.dumps(doc),
                "tries": 0,
                "next_at": at,
                "at": at,
            },
        )
    return len(sent)


def deliver_due(db, cfg, now=None, limit=50):
    """Send what's queued and due, signed as the sharer: done on a 2xx, tried again later (BACKOFF) otherwise, then
    given up with the reason kept. How many were sent."""
    if not on(cfg):
        return 0
    stamp = now or store.now()
    rows = db.rows(
        f"SELECT record::id(id) AS id, * FROM ap_delivery WHERE done_at = NONE AND failed = NONE AND next_at <= $n ORDER BY next_at LIMIT {int(limit)}",
        n=stamp,
    )
    sent = 0
    for r in rows:
        body = r["body"].encode()
        try:
            status, _ = _fetch(
                cfg, r["inbox"], "*/*", "POST", body, {"Content-Type": AP, **sign(db, cfg, r["by"], "POST", r["inbox"], body)}
            )
            why = None if 200 <= status < 300 else f"answered {status}"
        except (RuntimeError, circles.Problem) as e:
            why = str(e)
        tries = int(r.get("tries") or 0) + 1
        if why is None:
            db.q("UPDATE $q SET done_at = $t, tries = $n, error = NONE", q=R("ap_delivery", r["id"]), t=store.now(), n=tries)
            sent += 1
        elif tries > len(BACKOFF):
            db.q("UPDATE $q SET failed = $t, tries = $n, error = $e", q=R("ap_delivery", r["id"]), t=store.now(), n=tries, e=why)
        else:
            nxt = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=BACKOFF[tries - 1])).isoformat()
            db.q("UPDATE $q SET next_at = $x, tries = $n, error = $e", q=R("ap_delivery", r["id"]), x=nxt, n=tries, e=why)
    return sent


def deliveries(db, sid):
    """How a share is getting on elsewhere: [{handle, state (waiting, sent, gave up), error}]."""
    rows = db.rows("SELECT to, done_at, failed, error FROM ap_delivery WHERE share = $s AND kind = NONE", s=int(sid))
    names = remote_people(db, [r["to"] for r in rows])
    return [
        store.clean(
            {
                "handle": (names.get(r["to"]) or {}).get("handle"),
                "state": "sent" if r.get("done_at") else "gave up" if r.get("failed") else "waiting",
                "error": r.get("error"),
            }
        )
        for r in rows
    ]


# ---------- receiving ----------
def receive(db, cfg, handle, method, path, headers, body):
    """An inbox request for someone here: checked (verify), from someone in one of their circles, then a Create of a
    Note kept (once) as a share from them, a Delete or Undo of it taken back. What happened, in a word."""
    p = _person(db, handle=handle) if on(cfg) else None
    if not p:
        raise KeyError(handle)
    if len(body) > MAX_BODY:
        raise ValueError("that's too large")
    who = verify(db, cfg, method, path, headers, body)
    try:
        doc = json.loads(body)
    except ValueError:
        raise ValueError("send JSON") from None
    if not isinstance(doc, dict) or doc.get("actor") != who["actor"]:
        raise PermissionError("the activity's actor isn't who signed it")
    if not _in_circles(db, p["account"], who["id"]):
        raise PermissionError("they don't have the sender in a circle")
    kind, obj = doc.get("type"), doc.get("object")
    if kind == "Create" and isinstance(obj, dict) and obj.get("type") == "Note":
        oid = str(obj.get("id") or doc.get("id") or "")
        if not oid or db.one("SELECT id FROM ap_inbox WHERE account = $a AND note = $n LIMIT 1", a=p["account"], n=oid):
            return "seen"
        url = obj.get("url") if isinstance(obj.get("url"), str) and str(obj.get("url")).startswith("https://") else None
        db.q(
            "CREATE $r CONTENT $d",
            r=R("ap_inbox", db.next_id("ap_inbox")),
            d=store.clean(
                {
                    "account": p["account"],
                    "from": who["id"],
                    "note": oid,
                    "name": _text(obj.get("name"), 200) or None,
                    "content": _text(obj.get("content")) or None,
                    "url": url,
                    "tag": [_text(t.get("name"), 80) for t in obj.get("tag") or [] if isinstance(t, dict) and t.get("name")][:20] or None,
                    "at": store.now(),
                }
            ),
        )
        events.wake()
        return "kept"
    if kind in ("Delete", "Undo"):
        target = obj.get("object") if kind == "Undo" and isinstance(obj, dict) else obj
        oid = target.get("id") if isinstance(target, dict) else target
        db.q("DELETE ap_inbox WHERE account = $a AND from = $f AND note = $n", a=p["account"], f=who["id"], n=str(oid or ""))
        return "taken back"
    return "ignored"


def forget(db, account):
    """What federation keeps for an account that's removed."""
    db.run(
        ["DELETE ap_person WHERE account = $a", "DELETE ap_inbox WHERE account = $a", "DELETE ap_delivery WHERE by = $a"],
        a=int(account),
    )


# ---------- as activities ----------
INBOX_FIELDS = "id AS rid, record::id(id) AS id, account, from, note, name, content, url, tag, at"


def _received(db, rows):
    rows = [r for r in rows if r and r.get("at")]
    if not rows:
        return []
    names = remote_people(db, [r["from"] for r in rows])
    out = []
    for r in rows:
        who = names.get(r["from"]) or {"handle": "someone elsewhere"}
        actor = {"type": "Person", "id": f"lens:remote/{r['from']}", "name": who.get("name") or who["handle"], "handle": who["handle"]}
        obj = store.clean(
            {
                "type": "Note",
                "id": f"lens:remote_note/{r['id']}",
                "name": r.get("name") or _text(r.get("content"), 80) or "a note",
                "content": r.get("content"),
                "url": r.get("url"),
                "remote": True,
            }
        )
        out.append(
            events.build(
                f"ap-share-{r['id']}",
                "share.received",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} shared “{obj['name']}” with you",
                space=None,
                source="shares_remote",
                pos=[r["at"], r["id"]],
                published=r["at"],
                tag=[{"type": "Hashtag", "name": t} for t in r.get("tag") or []] or None,
                untrusted=UNTRUSTED,
                audience="owner",
                owner=r["account"],
            )
        )
    return out


class RemoteSharesSource(circles._ShareSource):
    """ap_inbox rows (Notes from people elsewhere in someone's circles) as share.received: each person sees their own."""

    name = "shares_remote"
    kinds = ("share.received",)
    table = "ap_inbox"
    fields = INBOX_FIELDS
    rx = re.compile(r"^ap-share-(\d+)$")
    mine = "account"

    def _make(self, db, rows):
        return _received(db, rows)

    def _spaces(self, spaces, p):
        return "true" if spaces is None else "false"


events.register_source(RemoteSharesSource())
