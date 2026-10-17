"""The file tree over S3 (docs/file-tree.md#s3): a read-only S3 server any S3 client mounts or syncs (rclone,
Cyberduck, the AWS CLI, s3fs, boto3), each person's namespaces as buckets.

Off until an admin turns it on (`shares.enabled`); then the process that runs routines (`lens worker`) listens on
`shares.s3_port`, as the sensor hub does (shares.py). Every request is signed (AWS Signature Version 4, in the Authorization
header or a presigned link) with an access key someone made for themselves in the web app; the key acts as them and
sees what they see in the web app. Keys' secrets are sealed with the server's key (they must be readable: SigV4 signs
with the secret itself), shown once, and can be revoked. Writes answer AccessDenied.

Path-style addressing (http://host:port/<bucket>/<key>), any region name. Supported: ListBuckets, HeadBucket,
GetBucketLocation, ListObjects (v1 and v2, with a delimiter or recursive), HeadObject and GetObject (with Range).

With `shares.remote` on, the web app relays it at its own address too (https://<web app>/s3/<bucket>/<key>), path and
all: clients sign that path, and the bucket and key come after /s3 (shares.web_path()).
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import hmac
import http.server
import re
import secrets
import threading
import urllib.parse
from typing import Any
from xml.sax.saxutils import escape

from . import auth, filetree, keyring, settings, store

R = store.R
NS = "http://s3.amazonaws.com/doc/2006-03-01/"
KEY_RX = re.compile(r"^LK[A-Z0-9]{18}$")
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
SKEW = dt.timedelta(minutes=15)
MAX_KEYS = 1000
MAX_PRESIGN_SECONDS = 7 * 24 * 3600
NAME_MAX = 80


class S3Error(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---------- access keys ----------
def _context(kid):
    return f"file_key:{kid}:secret"


def create_key(db, cfg, account, name=None, days=None):
    """A new access key for an account: (key id, secret). The secret is shown this once. It lasts `days` (the API
    keys' limits apply: auth.token_days; None takes their default)."""
    days = auth.token_days(cfg, days)
    kid = "LK" + "".join(secrets.choice(ALPHABET) for _ in range(18))
    secret = secrets.token_urlsafe(30)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("file_key", kid),
        d={
            "account": int(account),
            "name": (str(name or "").strip() or "S3 key")[:NAME_MAX],
            "sealed": settings.seal(cfg, secret, _context(kid)),
            "created_at": store.now(),
            **({"expires_at": auth._later(24 * days)} if days else {}),
        },
    )
    return kid, secret


def keys_of(db, account=None):
    """Someone's keys (None: everyone's, with their owners' emails), newest first; never the secrets."""
    rows = db.rows(
        "SELECT record::id(id) AS id, account, name, created_at, expires_at, last_used_at FROM file_key"
        + (" WHERE account = $a" if account is not None else ""),
        a=account,
    )
    if account is None and rows:
        emails = {a["id"]: a["email"] for a in db.rows("SELECT record::id(id) AS id, email FROM account")}
        rows = [{**r, "email": emails.get(r["account"])} for r in rows]
    return sorted(rows, key=lambda r: (r.get("created_at") or "", r["id"]), reverse=True)


def revoke_key(db, kid, account=None):
    """Revoke a key (only the account's own, when given); whether there was one."""
    row = db.one("SELECT account FROM $r", r=R("file_key", str(kid))) if KEY_RX.match(str(kid)) else None
    if not row or (account is not None and row["account"] != account):
        return False
    db.q("DELETE $r", r=R("file_key", str(kid)))
    return True


def key_account(db, cfg, kid):
    """(secret, active account) for a key id (any case: SMB lowercases user names), or None."""
    kid = str(kid or "").upper()
    if not KEY_RX.match(kid):
        return None
    row = db.one("SELECT account, sealed, expires_at FROM $r", r=R("file_key", kid))
    if not row or (row.get("expires_at") and row["expires_at"] < store.now()):
        return None
    acct = auth.active_account(db, row["account"])
    if not acct:
        return None
    try:
        secret = settings.unseal(cfg, row["sealed"], _context(kid))
    except Exception:  # noqa: BLE001 - the server's key changed since: the key is dead
        return None
    return secret, acct


def lookup_key(db, cfg, kid):
    """{account: id, secret} for a live key id (any case), or None: for servers that check the secret themselves
    (SMB's NTLM)."""
    found = key_account(db, cfg, kid)
    return {"account": found[1]["id"], "secret": found[0]} if found else None


def touch(db, kid):
    """Note that a key was just used."""
    kid = str(kid or "").upper()
    if KEY_RX.match(kid):
        db.q("UPDATE $r SET last_used_at = $t", r=R("file_key", kid), t=store.now())


def service_key(db, cfg, account, label):
    """(in-network S3 endpoint, key id, secret) for a service acting as an account (NFS: label "nfs"), made once per
    (account, label) and reused after; it doesn't expire. Revoke it with revoke_service_key."""
    from . import shares

    port = int(shares.settings_of(cfg)["s3_port"])
    endpoint = f"http://worker:{port}"
    for row in db.rows("SELECT record::id(id) AS id FROM file_key WHERE account = $a AND service = $l", a=int(account), l=str(label)):
        found = key_account(db, cfg, row["id"])
        if found:
            return endpoint, row["id"], found[0]
        db.q("DELETE $r", r=R("file_key", row["id"]))
    kid = "LK" + "".join(secrets.choice(ALPHABET) for _ in range(18))
    secret = secrets.token_urlsafe(30)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("file_key", kid),
        d={
            "account": int(account),
            "name": f"{label} (service)"[:NAME_MAX],
            "service": str(label),
            "sealed": settings.seal(cfg, secret, _context(kid)),
            "created_at": store.now(),
        },
    )
    return endpoint, kid, secret


_internal: dict[str, Any] = {}
_internal_lock = threading.Lock()


def internal_endpoint(db, cfg_fn):
    """The URL of an S3 server for this process alone (loopback, a free port), started on first use whatever the
    share settings say: for servers in the same process that read the tree through S3 (NFS). `cfg_fn` gives the
    current settings."""
    from . import shares

    with _internal_lock:
        if "server" not in _internal:
            _internal["server"] = shares.Server("127.0.0.1", 0, db, cfg_fn).start()
        return f"http://127.0.0.1:{_internal['server'].port}"


def revoke_service_key(db, account, label):
    """Revoke a service's key for an account; whether there was one."""
    rows = db.rows("SELECT record::id(id) AS id FROM file_key WHERE account = $a AND service = $l", a=int(account), l=str(label))
    for row in rows:
        db.q("DELETE $r", r=R("file_key", row["id"]))
    return bool(rows)


# ---------- Signature Version 4 ----------
def _enc(s, safe="-_.~"):
    return urllib.parse.quote(s, safe=safe)


def canonical_query(pairs):
    return "&".join(f"{_enc(k)}={_enc(v)}" for k, v in sorted((k, v) for k, v in pairs))


def canonical_path(raw):
    """The path as SigV4 signs it for S3: each segment encoded once, as the client sent it decoded."""
    return "/".join(_enc(urllib.parse.unquote(p)) for p in raw.split("/")) or "/"


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signature(secret, date, region, service, to_sign):
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), date), region), service), "aws4_request")
    return hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()


def _amz_time(s):
    try:
        return dt.datetime.strptime(s or "", "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise S3Error(403, "AccessDenied", "the request's date is missing or malformed") from None


def verify(db, cfg, method, raw_path, pairs, headers, now=None):
    """Check a request's signature; the account it acts as. `pairs`: the query string's (name, value) pairs, `headers`
    lowercased names to values."""
    now = now or dt.datetime.now(dt.timezone.utc)
    q = dict(pairs)
    presigned = "X-Amz-Signature" in q
    if presigned:
        if q.get("X-Amz-Algorithm") != "AWS4-HMAC-SHA256":
            raise S3Error(400, "AuthorizationQueryParametersError", "only AWS4-HMAC-SHA256 is supported")
        cred, signed, sig, amz = q.get("X-Amz-Credential", ""), q.get("X-Amz-SignedHeaders", ""), q["X-Amz-Signature"], q.get("X-Amz-Date")
        try:
            expires = int(q.get("X-Amz-Expires", ""))
        except ValueError:
            raise S3Error(400, "AuthorizationQueryParametersError", "X-Amz-Expires is a number of seconds") from None
        when = _amz_time(amz)
        if not 0 < expires <= MAX_PRESIGN_SECONDS or now > when + dt.timedelta(seconds=expires) or when - now > SKEW:
            raise S3Error(403, "AccessDenied", "this link has expired")
        payload = "UNSIGNED-PAYLOAD"
        pairs = [(k, v) for k, v in pairs if k != "X-Amz-Signature"]
    else:
        h = headers.get("authorization", "")
        if not h:
            raise S3Error(403, "AccessDenied", "sign requests with an access key made in Lens (Settings, File shares)")
        if not h.startswith("AWS4-HMAC-SHA256 "):
            raise S3Error(400, "InvalidRequest", "only AWS Signature Version 4 is supported")
        parts = dict(p.strip().split("=", 1) for p in h[len("AWS4-HMAC-SHA256 ") :].split(",") if "=" in p)
        cred, signed, sig = parts.get("Credential", ""), parts.get("SignedHeaders", ""), parts.get("Signature", "")
        amz = headers.get("x-amz-date") or ""
        when = _amz_time(amz) if amz else _http_time(headers.get("date"))
        if abs(now - when) > SKEW:
            raise S3Error(403, "RequestTimeTooSkewed", "the request's time is too far from the server's")
        payload = headers.get("x-amz-content-sha256") or "UNSIGNED-PAYLOAD"
    scope = cred.split("/")
    if len(scope) != 5 or scope[4] != "aws4_request":
        raise S3Error(403, "AccessDenied", "the credential is malformed")
    kid, date, region, service = scope[:4]
    found = key_account(db, cfg, kid)
    if not found:
        raise S3Error(403, "InvalidAccessKeyId", "no such access key, or it was revoked")
    secret, acct = found
    names = [n for n in signed.split(";") if n]
    if "host" not in names:
        raise S3Error(403, "AccessDenied", "the host header has to be signed")
    canon_headers = "".join(f"{n}:{' '.join(str(headers.get(n, '')).split())}\n" for n in names)
    canonical = "\n".join([method, canonical_path(raw_path), canonical_query(pairs), canon_headers, ";".join(names), payload])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz or when.strftime("%Y%m%dT%H%M%SZ"), "/".join(scope[1:4] + ["aws4_request"]),
                         hashlib.sha256(canonical.encode()).hexdigest()])  # fmt: skip
    if not hmac.compare_digest(signature(secret, date, region, service, to_sign), sig):
        raise S3Error(403, "SignatureDoesNotMatch", "the signature doesn't match: check the secret key")
    db.q("UPDATE $r SET last_used_at = $t", r=R("file_key", kid), t=store.now())
    return acct


def _http_time(s):
    try:
        t = email.utils.parsedate_to_datetime(s or "")
    except (TypeError, ValueError):
        t = None
    if t is None:
        raise S3Error(403, "AccessDenied", "the request's date is missing or malformed")
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ---------- answers ----------
def _iso(s):
    """An S3 time (2026-10-10T20:05:45.000Z) for a stored ISO time."""
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        t = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _xml(body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode()


def error_xml(e: S3Error, resource=""):
    return _xml(
        f"<Error><Code>{e.code}</Code><Message>{escape(e.message)}</Message><Resource>{escape(resource)}</Resource>"
        f"<RequestId>{secrets.token_hex(8)}</RequestId></Error>"
    )


def list_buckets(tree, account):
    buckets = "".join(f"<Bucket><Name>{escape(e.name)}</Name><CreationDate>{_iso(None)}</CreationDate></Bucket>" for e in tree.list(""))
    who = escape(str(account.get("email") or account["id"]))
    return _xml(f'<ListAllMyBucketsResult xmlns="{NS}"><Owner><ID>{who}</ID><DisplayName>{who}</DisplayName></Owner>'
                f"<Buckets>{buckets}</Buckets></ListAllMyBucketsResult>")  # fmt: skip


def _items(tree, bucket, prefix, delimiter):
    """(key, entry or None for a common prefix), sorted as S3 sorts them (by their UTF-8 bytes)."""
    folder, _, _ = prefix.rpartition("/")
    base = f"{bucket}/{folder}" if folder else bucket
    pre = f"{folder}/" if folder else ""
    try:
        entries = tree.list(base)
    except (filetree.NotFound, filetree.NotAFolder):
        return []
    out = []
    if delimiter == "/":
        for e in entries:
            key = pre + e.name + ("/" if e.folder else "")
            if key.startswith(prefix):
                out.append((key, None if e.folder else e))
    else:
        for e in entries:
            key = pre + e.name
            if e.folder:
                if (key + "/").startswith(prefix):  # only the folders the prefix reaches into
                    out += [(p[len(bucket) + 1 :], f) for p, f in tree.walk(f"{base}/{e.name}")]
            else:
                out.append((key, e))
        out = [(k, e) for k, e in out if k.startswith(prefix)]
        if delimiter:  # another delimiter: group by it after the prefix
            grouped, seen = [], set()
            for k, e in out:
                cut = k.find(delimiter, len(prefix))
                if cut < 0:
                    grouped.append((k, e))
                elif k[: cut + len(delimiter)] not in seen:
                    seen.add(k[: cut + len(delimiter)])
                    grouped.append((k[: cut + len(delimiter)], None))
            out = grouped
    return sorted(out, key=lambda x: x[0].encode())


def list_objects(tree, bucket, q):
    """ListObjects, v1 or v2 (list-type=2)."""
    v2 = q.get("list-type") == "2"
    prefix, delimiter = q.get("prefix", ""), q.get("delimiter", "")
    try:
        most = max(0, min(MAX_KEYS, int(q.get("max-keys", MAX_KEYS))))
    except ValueError:
        raise S3Error(400, "InvalidArgument", "max-keys is a number") from None
    if v2:
        token = q.get("continuation-token")
        try:
            after = base64.urlsafe_b64decode(token.encode()).decode() if token else q.get("start-after", "")
        except ValueError:
            raise S3Error(400, "InvalidArgument", "the continuation token is not valid") from None
    else:
        after = q.get("marker", "")
    items = [x for x in _items(tree, bucket, prefix, delimiter) if x[0].encode() > after.encode()]
    page, more = items[:most], len(items) > most
    url = q.get("encoding-type") == "url"

    def k(s):
        return escape(urllib.parse.quote(s, safe="/") if url else s)

    body = [f"<Name>{escape(bucket)}</Name><Prefix>{k(prefix)}</Prefix><MaxKeys>{most}</MaxKeys>"]
    if delimiter:
        body.append(f"<Delimiter>{k(delimiter)}</Delimiter>")
    if url:
        body.append("<EncodingType>url</EncodingType>")
    body.append(f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>")
    if v2:
        body.append(f"<KeyCount>{len(page)}</KeyCount>")
        if q.get("continuation-token"):
            body.append(f"<ContinuationToken>{escape(q['continuation-token'])}</ContinuationToken>")
        if q.get("start-after"):
            body.append(f"<StartAfter>{k(q['start-after'])}</StartAfter>")
        if more:
            body.append(f"<NextContinuationToken>{base64.urlsafe_b64encode(page[-1][0].encode()).decode()}</NextContinuationToken>")
    else:
        body.append(f"<Marker>{k(after)}</Marker>")
        if more and delimiter:
            body.append(f"<NextMarker>{k(page[-1][0])}</NextMarker>")
    for key, e in page:
        if e is None:
            body.append(f"<CommonPrefixes><Prefix>{k(key)}</Prefix></CommonPrefixes>")
        else:
            body.append(
                f"<Contents><Key>{k(key)}</Key><LastModified>{_iso(e.modified)}</LastModified><ETag>&quot;{e.version}&quot;</ETag>"
                f"<Size>{e.size}</Size><StorageClass>STANDARD</StorageClass></Contents>"
            )
    root = "ListBucketResult"
    return _xml(f'<{root} xmlns="{NS}">' + "".join(body) + f"</{root}>")


# ---------- the server ----------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Lens"
    protocol_version = "HTTP/1.1"
    server: Any  # shares.Server

    def log_message(self, fmt, *args):  # quiet: the requests aren't worth a line each
        pass

    def _send(self, status, body=b"", ctype="application/xml", headers=None, head=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-amz-request-id", secrets.token_hex(8))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _fail(self, e: S3Error, resource=""):
        self._send(e.status, b"" if self.command == "HEAD" else error_xml(e, resource), head=self.command == "HEAD")

    def do_GET(self):
        self._handle(head=False)

    def do_HEAD(self):
        self._handle(head=True)

    def _write(self):
        self.rfile.read(min(int(self.headers.get("content-length") or 0), 1 << 20))  # what was sent, to keep the connection
        self._fail(S3Error(403, "AccessDenied", "the file tree is read-only"))

    do_PUT = do_POST = do_DELETE = _write  # noqa: N815 - http.server's names

    def _handle(self, head):
        from . import shares

        raw, _, query = self.path.partition("?")
        try:
            pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            headers = {k.lower(): v for k, v in self.headers.items()}
            db, cfg = self.server.db, self.server.cfg_fn()
            prefix = shares.web_path(headers, "s3")
            if prefix and not shares.remote(cfg):
                raise S3Error(
                    403, "AccessDenied", "S3 isn't open at the web app's address: an admin turns that on in Settings, File shares"
                )
            acct = verify(db, cfg, self.command, raw, pairs, headers)
            raw = shares.share_path(raw, prefix)
            tree = filetree.Tree(db, cfg, filetree.Viewer.of(db, acct))
            path = urllib.parse.unquote(raw)
            bucket, _, key = path.lstrip("/").partition("/")
            q = dict(pairs)
            if not bucket:
                return self._send(200, list_buckets(tree, acct), head=head)
            if bucket not in [e.name for e in tree.list("")]:
                raise S3Error(404, "NoSuchBucket", "no such bucket, or not yours to read")
            if not key:
                if "location" in q:
                    return self._send(200, _xml(f'<LocationConstraint xmlns="{NS}"></LocationConstraint>'), head=head)
                if head:
                    return self._send(200, head=True)
                if set(q) & {"acl", "policy", "versioning", "lifecycle", "cors", "tagging", "uploads", "website", "object-lock"}:
                    raise S3Error(501, "NotImplemented", "the file tree only lists and reads")
                return self._send(200, list_objects(tree, bucket, q))
            return self._object(tree, db, cfg, bucket, key, head)
        except S3Error as e:
            self._fail(e, raw)
        except keyring.Locked:
            self._fail(S3Error(403, "AccessDenied", "this namespace is a locked vault: an owner unlocks it in the web app"), raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 - say so, and keep serving
            self.server.log(f"s3: {type(e).__name__}: {e}")
            self._fail(S3Error(500, "InternalError", "something went wrong on the server"), raw)

    def _object(self, tree, db, cfg, bucket, key, head):
        try:
            e = tree.stat(f"{bucket}/{key}")
        except filetree.NotFound:
            e = None
        if e is None or e.folder:
            raise S3Error(404, "NoSuchKey", "no such file")
        if e.encrypted:
            keyring.plain_size(db, cfg, e.path)  # a locked vault says so before anything is sent
        start, end, status = 0, e.size - 1, 200
        rng = self.headers.get("range")
        if rng and e.size:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m or m.groups() == ("", ""):
                raise S3Error(416, "InvalidRange", "the range is not valid")
            a, b = m.groups()
            start, end = (max(0, e.size - int(b)), e.size - 1) if a == "" else (int(a), min(e.size - 1, int(b)) if b else e.size - 1)
            if start > end or start >= e.size:
                raise S3Error(416, "InvalidRange", "the range is not satisfiable")
            status = 206
        when = dt.datetime.fromisoformat(_iso(e.modified).replace(".000Z", "+00:00"))
        self.send_response(status)
        self.send_header("Content-Type", e.content_type or "application/octet-stream")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("ETag", f'"{e.version}"')
        self.send_header("Last-Modified", email.utils.format_datetime(when, usegmt=True))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{e.size}")
        self.end_headers()
        if head or end < start:
            return
        for chunk in e.read(db, cfg, start, end):
            self.wfile.write(chunk)


def password_account(db, cfg, kid, secret):
    """The account an access key and its secret sign in as (WebDAV's Basic sign-in), or None."""
    found = key_account(db, cfg, kid)
    if not found or not hmac.compare_digest(found[0].encode(), str(secret or "").encode()):
        return None
    db.q("UPDATE $r SET last_used_at = $t", r=R("file_key", kid), t=store.now())
    return found[1]
