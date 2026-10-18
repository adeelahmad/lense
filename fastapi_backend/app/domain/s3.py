"""The file tree over S3 (docs/file-tree.md#s3): an S3 server any S3 client mounts or syncs (rclone, Cyberduck, the
AWS CLI, s3fs, boto3), each person's namespaces as buckets.

Off until an admin turns it on (`shares.enabled`); then the process that runs routines (`lens worker`) listens on
`shares.s3_port`, as the sensor hub does (shares.py). Every request is signed (AWS Signature Version 4, in the Authorization
header or a presigned link) with an access key someone made for themselves in the web app; the key acts as them and
sees what they see in the web app. Keys' secrets are sealed with the server's key (they must be readable: SigV4 signs
with the secret itself), shown once, and can be revoked.

Path-style addressing (http://host:port/<bucket>/<key>), any region name. Supported: ListBuckets, HeadBucket,
GetBucketLocation, ListObjects (v1 and v2, with a delimiter or recursive), HeadObject and GetObject (with Range). While
`shares.add_files` is on, a file put into the tree is imported (share_import.py): PutObject (whole, chunked, or
aws-chunked with signed chunks), multipart uploads, a folder marker (a key ending in /) under Collections makes a
collection, and DeleteObject(s) removes only what share_import.py keeps aside. Anything else that writes answers
AccessDenied or NotImplemented.

With `shares.remote` on, the web app relays it at its own address too (https://<web app>/s3/<bucket>/<key>), path and
all: clients sign that path, and the bucket and key come after /s3 (shares.web_path()).
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

from . import auth, filetree, http_body, keyring, settings, share_import, store, uploads

R = store.R
NS = "http://s3.amazonaws.com/doc/2006-03-01/"
KEY_RX = re.compile(r"^LK[A-Z0-9]{18}$")
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
SKEW = dt.timedelta(minutes=15)
MAX_KEYS = 1000
MAX_PRESIGN_SECONDS = 7 * 24 * 3600
NAME_MAX = 80
UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{32}$")
MAX_PARTS = 10000
XML_MAX = 1 << 20  # a CompleteMultipartUpload or DeleteObjects body


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


def signing_key(secret, date, region, service):
    return _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), date), region), service), "aws4_request")


def signature(secret, date, region, service, to_sign):
    return hmac.new(signing_key(secret, date, region, service), to_sign.encode(), hashlib.sha256).hexdigest()


def _amz_time(s):
    try:
        return dt.datetime.strptime(s or "", "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise S3Error(403, "AccessDenied", "the request's date is missing or malformed") from None


def verify(db, cfg, method, raw_path, pairs, headers, now=None, chunks=None):
    """Check a request's signature; the account it acts as. `pairs`: the query string's (name, value) pairs, `headers`
    lowercased names to values. `chunks`, a dict, gets what a streaming body's chunks are signed with
    (http_body.Signed) under "signed", and the service the key was made for (NFS: "nfs"), if any, under "service"."""
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
    if chunks is not None:
        stamp = amz or when.strftime("%Y%m%dT%H%M%SZ")
        chunks["signed"] = http_body.Signed(signing_key(secret, date, region, service), stamp, "/".join(scope[1:]), sig)
        chunks["service"] = db.one("SELECT VALUE service FROM $r", r=R("file_key", kid.upper()))
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
class Handler(http_body.Lingering):
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

    def handle_expect_100(self):
        """Hold back 100 Continue until the request is known to be allowed (_go_on): a refused upload is never sent."""
        return True

    def _go_on(self):
        if self.headers.get("expect", "").lower() == "100-continue":
            self.send_response_only(100)
            self.end_headers()

    def _write(self):
        from . import shares

        raw, _, query = self.path.partition("?")
        headers = {k.lower(): v for k, v in self.headers.items()}
        self._read = False
        try:
            pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            db, cfg = self.server.db, self.server.cfg_fn()
            prefix = shares.web_path(headers, "s3")
            if prefix and not shares.remote(cfg):
                raise S3Error(
                    403, "AccessDenied", "S3 isn't open at the web app's address: an admin turns that on in Settings, File shares"
                )
            chunks: dict[str, Any] = {}
            acct = verify(db, cfg, self.command, raw, pairs, headers, chunks=chunks)
            raw = shares.share_path(raw, prefix)
            via = chunks.get("service") if chunks.get("service") in share_import.PROTOCOLS else "s3"  # the NFS share's key
            if not share_import.accepting(cfg, via):
                raise S3Error(403, "AccessDenied", "the file tree is read-only")
            adder = share_import.Adder(db, cfg, acct, via)
            bucket, _, key = urllib.parse.unquote(raw).lstrip("/").partition("/")
            q = dict(pairs)
            if not bucket or (not key and self.command != "POST"):
                raise S3Error(403, "AccessDenied", "namespaces are made in the web app" if bucket else "name a bucket")
            if bucket not in [e.name for e in adder.tree.list("")]:
                raise S3Error(404, "NoSuchBucket", "no such bucket, or not yours to read")
            Writes(self, db, cfg, acct, adder, bucket, key, q, headers, chunks.get("signed")).run()
        except S3Error as e:
            self._refuse(e, raw, headers)
        except http_body.BodyError as e:
            self._refuse(S3Error(e.status, e.code, e.message), raw, headers)
        except share_import.TooLarge as e:
            self._refuse(S3Error(400, "EntityTooLarge", str(e)), raw, headers)
        except share_import.Rejected as e:
            self._refuse(S3Error(400, "InvalidArgument", str(e)), raw, headers)
        except share_import.Refused as e:
            self._refuse(S3Error(403, "AccessDenied", str(e)), raw, headers)
        except filetree.NotFound:
            self._refuse(S3Error(403, "AccessDenied", "no such folder: files go in a namespace, or in Collections/…"), raw, headers)
        except keyring.Locked:
            self._refuse(S3Error(403, "AccessDenied", "this namespace is a locked vault: an owner unlocks it in the web app"), raw, headers)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OSError as e:
            if e.errno != errno.ENOSPC:
                self.server.log(f"s3: {type(e).__name__}: {e}")
            msg = "the server doesn't have room for this file" if e.errno == errno.ENOSPC else "something went wrong on the server"
            self._refuse(S3Error(507 if e.errno == errno.ENOSPC else 500, "InternalError", msg), raw, headers)
        except Exception as e:  # noqa: BLE001 - say so, and keep serving
            self.server.log(f"s3: {type(e).__name__}: {e}")
            self._refuse(S3Error(500, "InternalError", "something went wrong on the server"), raw, headers)

    def _refuse(self, e, raw, headers):
        try:
            if not self._read and not http_body.drain(self.rfile, headers):
                self.close_connection = True  # the body is still coming (or never will): don't read it
            self._fail(e, raw)
        except (BrokenPipeError, ConnectionResetError):  # the client went away mid-file
            self.close_connection = True

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
                if "uploads" in q:
                    return self._send(200, list_uploads(cfg, acct, bucket))
                if set(q) & {"acl", "policy", "versioning", "lifecycle", "cors", "tagging", "website", "object-lock"}:
                    raise S3Error(501, "NotImplemented", "the file tree only lists, reads and takes files")
                return self._send(200, list_objects(tree, bucket, q))
            if "uploadId" in q and not head:
                return self._send(200, list_parts(cfg, acct, bucket, key, q["uploadId"]))
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
        if e.mtime:
            self.send_header("x-amz-meta-mtime", e.mtime)  # as rclone sent it
        self.end_headers()
        if head or end < start:
            return
        for chunk in e.read(db, cfg, start, end):
            self.wfile.write(chunk)


# ---------- adding files ----------
def _mtime(headers):
    """The file's own time a client sent (rclone: x-amz-meta-mtime, seconds since 1970, as text), or None."""
    return headers.get("x-amz-meta-mtime") or None


def _limit(cfg):
    return int(cfg["uploads"]["max_mb"]) * uploads.MB


class Writes:
    """One request that writes (PUT, POST, DELETE), for an account allowed to add files."""

    def __init__(self, h, db, cfg, acct, adder, bucket, key, q, headers, signed):
        self.h, self.db, self.cfg, self.acct, self.adder = h, db, cfg, acct, adder
        self.bucket, self.key, self.q, self.headers, self.signed = bucket, key, q, headers, signed
        self.path = f"{bucket}/{key}"

    def run(self):
        m, q = self.h.command, self.q
        if not self.key:  # POST to the bucket
            if "delete" in q:
                return self.delete_many()
            raise S3Error(501, "NotImplemented", "the file tree only lists, reads and takes files")
        if "uploadId" in q:
            if m == "PUT" and "partNumber" in q:
                return self.part()
            if m == "POST":
                return self.complete()
            if m == "DELETE":
                return self.abort()
        if m == "POST" and "uploads" in q:
            return self.start()
        if m == "DELETE":
            return self.delete()
        if m == "PUT" and not set(q) - {"x-id"}:  # x-id: the AWS SDKs' operation name
            if self.headers.get("x-amz-copy-source"):
                return self.retime()
            return self.folder() if self.key.endswith("/") else self.put()
        raise S3Error(501, "NotImplemented", "copying, tags and the like aren't served: put a file, or upload it in parts")

    def _declared(self):
        n = self.headers.get("x-amz-decoded-content-length") or self.headers.get("content-length") or ""
        return int(n) if n.isdigit() else None

    def _receive(self, dest):
        """The body into `dest`: (size, md5 hex)."""
        self.h._go_on()
        self.h._read = True
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            self.h.close_connection = True  # a chunked body may have been read past its end
        return http_body.receive(self.h.rfile, self.headers, dest, _limit(self.cfg), self.signed)

    def put(self):
        self.adder.check(self.path, self._declared())
        dest = share_import.spool(self.cfg)
        try:
            _, md5 = self._receive(dest)
            self.adder.put(self.path, dest, _mtime(self.headers), md5)  # imported (moved away), or kept aside there
        except BaseException:
            share_import._remove(dest)
            raise
        self.h._send(200, headers={"ETag": f'"{md5}"'})

    def retime(self):
        """CopyObject of a file just added onto itself with new metadata: rclone setting its time. Nothing else."""
        source = urllib.parse.unquote(self.headers["x-amz-copy-source"].split("?")[0]).lstrip("/")
        replace = self.headers.get("x-amz-metadata-directive", "").upper() == "REPLACE"
        if source != self.path or not replace or not self.adder.touch(self.path, _mtime(self.headers)):
            raise S3Error(501, "NotImplemented", "copying isn't served: put the file instead")
        self.h._read = True
        e = self.adder.tree.stat(self.path)
        self.h._send(200, _xml(f'<CopyObjectResult xmlns="{NS}"><LastModified>{_iso(e.modified)}</LastModified>'
                               f"<ETag>&quot;{e.version}&quot;</ETag></CopyObjectResult>"))  # fmt: skip

    def folder(self):
        if self._declared():
            raise S3Error(400, "InvalidArgument", "a key ending in / is a folder: it holds no bytes")
        self.h._read = True
        self.adder.folder(self.path.rstrip("/"))
        self.h._send(200, headers={"ETag": f'"{hashlib.md5(b"", usedforsecurity=False).hexdigest()}"'})

    def delete(self):
        self.h._read = True
        try:
            self.adder.delete(self.path)
        except share_import.Refused:
            try:
                self.adder.tree.stat(self.path)
            except filetree.NotFound:
                return self.h._send(204)  # nothing there: S3 says done
            raise
        self.h._send(204)

    def delete_many(self):
        body = self._xml_body()
        quiet = (body.findtext("{*}Quiet") or body.findtext("Quiet") or "").lower() == "true"
        done, errs = [], []
        for o in body.iter():
            if o.tag.split("}")[-1] != "Object":
                continue
            k = next((c.text or "" for c in o if c.tag.split("}")[-1] == "Key"), "")
            try:
                self.adder.delete(f"{self.bucket}/{k}")
                done.append(k)
            except share_import.Refused as e:
                try:
                    self.adder.tree.stat(f"{self.bucket}/{k}")
                    errs.append((k, str(e)))
                except filetree.NotFound:
                    done.append(k)
        out = [] if quiet else [f"<Deleted><Key>{escape(k)}</Key></Deleted>" for k in done]
        out += [f"<Error><Key>{escape(k)}</Key><Code>AccessDenied</Code><Message>{escape(m)}</Message></Error>" for k, m in errs]
        self.h._send(200, _xml(f'<DeleteResult xmlns="{NS}">' + "".join(out) + "</DeleteResult>"))

    def _xml_body(self):
        n = self._declared() or 0
        if n > XML_MAX:
            raise S3Error(400, "MalformedXML", "the request is too large")
        dest = share_import.spool(self.cfg)
        try:
            self._receive(dest)
            raw = dest.read_bytes()
        finally:
            share_import._remove(dest)
        if len(raw) > XML_MAX:
            raise S3Error(400, "MalformedXML", "the request is too large")
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise S3Error(400, "MalformedXML", "the request isn't valid")
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            raise S3Error(400, "MalformedXML", "the request isn't valid XML") from None

    # ---------- in parts ----------
    def start(self):
        self.adder.check(self.path)
        self.h._read = True
        uid = start_upload(self.cfg, self.acct["id"], self.bucket, self.key, _mtime(self.headers))
        self.h._send(200, _xml(f'<InitiateMultipartUploadResult xmlns="{NS}"><Bucket>{escape(self.bucket)}</Bucket>'
                               f"<Key>{escape(self.key)}</Key><UploadId>{uid}</UploadId></InitiateMultipartUploadResult>"))  # fmt: skip

    def part(self):
        d, _ = upload_of(self.cfg, self.acct["id"], self.bucket, self.key, self.q["uploadId"])
        try:
            n = int(self.q["partNumber"])
        except ValueError:
            n = 0
        if not 1 <= n <= MAX_PARTS:
            raise S3Error(400, "InvalidArgument", f"part numbers go from 1 to {MAX_PARTS}")
        tmp = d / f"{n:05d}.part"
        try:
            size, md5 = self._receive(tmp)
            os.replace(tmp, d / f"{n:05d}")
            (d / f"{n:05d}.md5").write_text(md5)
        finally:
            share_import._remove(tmp)
        if sum(p["size"] for p in parts_of(d)) > _limit(self.cfg):
            raise S3Error(400, "EntityTooLarge", f"files up to {self.cfg['uploads']['max_mb']} MB")
        self.h._send(200, headers={"ETag": f'"{md5}"'})

    def complete(self):
        d, meta = upload_of(self.cfg, self.acct["id"], self.bucket, self.key, self.q["uploadId"])
        body = self._xml_body()
        listed = []
        for p in body.iter():
            if p.tag.split("}")[-1] != "Part":
                continue
            kids = {c.tag.split("}")[-1]: (c.text or "").strip() for c in p}
            try:
                listed.append((int(kids.get("PartNumber", "")), kids.get("ETag", "").strip('"')))
            except ValueError:
                raise S3Error(400, "MalformedXML", "a part's number isn't a number") from None
        if not listed:
            raise S3Error(400, "MalformedXML", "list the parts to put together")
        if [n for n, _ in listed] != sorted({n for n, _ in listed}):
            raise S3Error(400, "InvalidPartOrder", "list the parts in order, each once")
        have = {p["number"]: p for p in parts_of(d)}
        for n, etag in listed:
            if n not in have or (etag and etag != have[n]["etag"]):
                raise S3Error(400, "InvalidPart", f"part {n} wasn't uploaded, or its ETag differs")
        whole = share_import.spool(self.cfg)
        md5s = hashlib.md5(usedforsecurity=False)
        self.h._read = True
        try:
            with open(whole, "wb") as out:
                for n, _ in listed:
                    with open(d / f"{n:05d}", "rb") as f:
                        shutil.copyfileobj(f, out, 1 << 20)
                    md5s.update(bytes.fromhex(have[n]["etag"]))
            self.adder.put(self.path, whole, meta.get("mtime"))
        except BaseException:
            share_import._remove(whole)
            raise
        shutil.rmtree(d, ignore_errors=True)
        etag = f"{md5s.hexdigest()}-{len(listed)}"
        self.h._send(200, _xml(f'<CompleteMultipartUploadResult xmlns="{NS}"><Location>/{escape(self.path)}</Location>'
                               f"<Bucket>{escape(self.bucket)}</Bucket><Key>{escape(self.key)}</Key>"
                               f"<ETag>&quot;{etag}&quot;</ETag></CompleteMultipartUploadResult>"))  # fmt: skip

    def abort(self):
        d, _ = upload_of(self.cfg, self.acct["id"], self.bucket, self.key, self.q["uploadId"])
        self.h._read = True
        shutil.rmtree(d, ignore_errors=True)
        self.h._send(204)


def _uploads_dir(cfg, uid):
    if not UPLOAD_ID.match(uid or ""):
        raise S3Error(404, "NoSuchUpload", "no such upload")
    return uploads.incoming(cfg) / f"s3mp-{uid}"


def start_upload(cfg, account, bucket, key, mtime=None):
    """A new multipart upload of `key` by an account: its id. Uploads nobody finished within uploads.expire_hours go."""
    sweep_uploads(cfg)
    uid = secrets.token_urlsafe(24)
    d = _uploads_dir(cfg, uid)
    d.mkdir()
    meta = {"account": account, "bucket": bucket, "key": key, "created": store.now(), "mtime": mtime}
    (d / "meta.json").write_text(json.dumps(meta))
    return uid


def upload_of(cfg, account, bucket, key, uid):
    """(its folder, its meta) for one of the account's uploads of this key; NoSuchUpload otherwise."""
    d = _uploads_dir(cfg, uid)
    try:
        meta = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        raise S3Error(404, "NoSuchUpload", "no such upload: it was finished, cancelled or expired") from None
    if (meta.get("account"), meta.get("bucket"), meta.get("key")) != (account, bucket, key):
        raise S3Error(404, "NoSuchUpload", "no such upload")
    return d, meta


def parts_of(d):
    out = []
    for p in sorted(d.glob("[0-9][0-9][0-9][0-9][0-9]")):
        try:
            out.append({"number": int(p.name), "size": p.stat().st_size, "etag": (d / f"{p.name}.md5").read_text().strip(),
                        "modified": p.stat().st_mtime})  # fmt: skip
        except (OSError, ValueError):
            continue
    return out


def sweep_uploads(cfg):
    cut = time.time() - int(cfg["uploads"]["expire_hours"]) * 3600
    for d in uploads.incoming(cfg).glob("s3mp-*"):
        try:
            if d.stat().st_mtime < cut and all(p.stat().st_mtime < cut for p in d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def list_parts(cfg, acct, bucket, key, uid):
    d, _ = upload_of(cfg, acct["id"], bucket, key, uid)
    parts = "".join(
        f"<Part><PartNumber>{p['number']}</PartNumber><LastModified>{_stamp(p['modified'])}</LastModified>"
        f"<ETag>&quot;{p['etag']}&quot;</ETag><Size>{p['size']}</Size></Part>"
        for p in parts_of(d)
    )
    return _xml(f'<ListPartsResult xmlns="{NS}"><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key><UploadId>{uid}</UploadId>'
                f"<IsTruncated>false</IsTruncated>{parts}</ListPartsResult>")  # fmt: skip


def list_uploads(cfg, acct, bucket):
    """The account's unfinished multipart uploads in a bucket."""
    out = []
    for d in sorted(uploads.incoming(cfg).glob("s3mp-*")):
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        if meta.get("account") == acct["id"] and meta.get("bucket") == bucket:
            out.append(f"<Upload><Key>{escape(meta['key'])}</Key><UploadId>{d.name[5:]}</UploadId>"
                       f"<Initiated>{_iso(meta.get('created'))}</Initiated></Upload>")  # fmt: skip
    return _xml(f'<ListMultipartUploadsResult xmlns="{NS}"><Bucket>{escape(bucket)}</Bucket><IsTruncated>false</IsTruncated>'
                + "".join(out) + "</ListMultipartUploadsResult>")  # fmt: skip


def password_account(db, cfg, kid, secret):
    """The account an access key and its secret sign in as (WebDAV's Basic sign-in), or None."""
    found = key_account(db, cfg, kid)
    if not found or not hmac.compare_digest(found[0].encode(), str(secret or "").encode()):
        return None
    db.q("UPDATE $r SET last_used_at = $t", r=R("file_key", kid), t=store.now())
    return found[1]
