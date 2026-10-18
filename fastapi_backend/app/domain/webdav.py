"""The asset tree over WebDAV (docs/file-tree.md#webdav): a WebDAV server (RFC 4918) that Finder, Windows Explorer,
GNOME Files, Dolphin, rclone and davfs2 mount as a drive.

Each person signs in with one of their access keys (s3.py): the key id as the user name and its secret as the
password (HTTP Basic). They see the namespaces and collections the web app shows them. OPTIONS, PROPFIND (depth 0
or 1), GET and HEAD (with Range). While `shares.add_files` is on, a file put into the tree is imported
(share_import.py): PUT (whole or chunked), MKCOL under Collections makes a collection, DELETE and MOVE act on what
share_import.py keeps aside (an app's own files, or a file uploaded under a temporary name and then renamed), and
LOCK, UNLOCK and PROPPATCH answer as clients expect (class 2: Finder and Windows mount a class 1 server read-only)
without storing anything. Anything else that writes answers 403. Run by shares.py.

With `shares.remote` on, the web app relays it at its own address too (https://<web app>/dav/), and links in listings
start with /dav (shares.web_path()).
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import errno
import re
import secrets
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

from . import auth, filetree, http_body, keyring, s3, share_import, uploads

ALLOW = "OPTIONS, GET, HEAD, PROPFIND"
ALLOW_ADD = ALLOW + ", PUT, MKCOL, DELETE, MOVE, LOCK, UNLOCK, PROPPATCH"
REALM = 'Basic realm="Lens", charset="UTF-8"'
MAX_BODY = 1 << 20
LOCK_SECONDS = 3600


def _attr(s):
    """Text for a double-quoted XML attribute."""
    return escape(s, {'"': "&quot;"})


def _http_date(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        t = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return email.utils.format_datetime((t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc), usegmt=True)


def href(parts, folder, prefix=""):
    return prefix + "/" + "/".join(urllib.parse.quote(p, safe="") for p in parts) + ("/" if folder and parts else "")


def _response(parts, e, prefix=""):
    props = [f"<D:displayname>{escape(e.name)}</D:displayname>", f"<D:getlastmodified>{_http_date(e.modified)}</D:getlastmodified>"]
    if e.folder:
        props.append("<D:resourcetype><D:collection/></D:resourcetype>")
    else:
        props += [
            "<D:resourcetype/>",
            f"<D:getcontentlength>{e.size}</D:getcontentlength>",
            f"<D:getcontenttype>{escape(e.content_type or 'application/octet-stream')}</D:getcontenttype>",
            f'<D:getetag>"{e.version}"</D:getetag>',
        ]
    return (
        f"<D:response><D:href>{escape(href(parts, e.folder, prefix))}</D:href><D:propstat><D:prop>{''.join(props)}</D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
    )


def multistatus(tree, parts, depth, prefix=""):
    """The PROPFIND answer for a path: itself, and with depth 1 what's in it. `prefix`: the path the share is at."""
    e = tree.stat("/".join(parts))
    out = [_response(parts, e, prefix)]
    if depth != "0" and e.folder:
        out += [_response([*parts, k.name], k, prefix) for k in tree.list("/".join(parts))]
    return ('<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">' + "".join(out) + "</D:multistatus>").encode()


class Handler(http_body.Lingering):
    server_version = "Lens"
    protocol_version = "HTTP/1.1"
    server: Any  # shares.Server

    def log_message(self, fmt, *args):
        pass

    def _adds(self):
        return share_import.accepting(self.server.cfg_fn(), "webdav")

    def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("DAV", "1, 2" if self._adds() else "1")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _drain(self):
        n = int(self.headers.get("content-length") or 0)
        if n > MAX_BODY:
            self.close_connection = True
            return
        self.rfile.read(n)

    def _prefix(self):
        from . import shares

        return shares.web_path(self.headers, "webdav")

    def _closed(self):
        """True (and turned away) when the request came through the web app while that's not allowed."""
        from . import shares

        if self._prefix() and not shares.remote(self.server.cfg_fn()):
            self._send(403, b"WebDAV isn't open at the web app's address: an admin turns that on in Settings, File shares\n")
            return True
        return False

    def _account(self):
        """The account the request signs in as, or None (an answer was sent)."""
        from . import shares

        if self._closed():
            return None
        ip = shares.visitor(self, self.server.cfg_fn())
        if auth.throttled(f"webdav:{ip}"):
            self._send(429, b"too many failed sign-ins: try again later\n")
            return None
        h = self.headers.get("authorization", "")
        acct = None
        if h[:6].lower() == "basic ":
            try:
                kid, _, secret = base64.b64decode(h[6:].strip()).decode().partition(":")
            except ValueError:
                kid, secret = "", ""
            acct = s3.password_account(self.server.db, self.server.cfg_fn(), kid, secret)
            if acct is None:
                auth.hit(f"webdav:{ip}")
        if acct is None:
            self._send(401, b"sign in with an access key from Lens (your API tokens page)\n", headers={"WWW-Authenticate": REALM})
        return acct

    def _parts(self):
        from . import shares

        raw = shares.share_path(self.path.partition("?")[0], self._prefix())
        return [urllib.parse.unquote(p) for p in raw.split("/") if p]

    def do_OPTIONS(self):  # noqa: N802 - http.server's names
        self._drain()
        if not self._closed():
            self._send(200, headers={"Allow": ALLOW_ADD if self._adds() else ALLOW, "MS-Author-Via": "DAV"})

    def _write(self):
        self._drain()
        if not self._closed():
            self._send(403, b"the file tree is read-only\n", headers={"Allow": ALLOW})

    do_POST = do_COPY = _write  # noqa: N815

    def handle_expect_100(self):
        """Hold back 100 Continue until the upload is known to be allowed (_go_on)."""
        return True

    def _go_on(self):
        if self.headers.get("expect", "").lower() == "100-continue":
            self.send_response_only(100)
            self.end_headers()

    def _adding(self):
        """(the account, its Adder) for a request that writes, or None (an answer was sent)."""
        if not self._adds():
            self._write()
            return None
        acct = self._account()
        if acct is None:
            self._skip()
            return None
        db = self.server.db
        return acct, share_import.Adder(db, self.server.cfg_fn(), acct, "webdav", self._tree(acct))

    def _skip(self):
        """Don't read a body that's still to come: drop a small one, else close the connection after the answer."""
        if not http_body.drain(self.rfile, {k.lower(): v for k, v in self.headers.items()}):
            self.close_connection = True

    def _refused(self, e):
        """The answer for something share_import.py or the body reader turned down."""
        if isinstance(e, share_import.Refused):  # a PermissionError (as keyring.Locked is), so before OSError
            return self._send(403, f"{e}\n".encode())
        if isinstance(e, http_body.BodyError):
            return self._send(413 if e.code == "EntityTooLarge" else e.status, f"{e.message}\n".encode())
        if isinstance(e, share_import.TooLarge):
            return self._send(413, f"{e}\n".encode())
        if isinstance(e, share_import.Rejected):
            return self._send(415, f"{e}\n".encode())
        if isinstance(e, filetree.NotFound):
            return self._send(409, b"no such folder: files go in a namespace, or in Collections/...\n")
        if isinstance(e, keyring.Locked):
            return self._send(403, b"this namespace is a locked vault: an owner unlocks it in the web app\n")
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return self._send(507, b"the server doesn't have room for this file\n")
        if isinstance(e, OSError):
            self.server.log(f"webdav: {type(e).__name__}: {e}")
            return self._send(500, b"something went wrong on the server\n")
        return self._send(403, f"{e}\n".encode())

    def do_PUT(self):  # noqa: N802
        got = self._adding()
        if got is None:
            return
        _, adder = got
        headers = {k.lower(): v for k, v in self.headers.items()}
        path = "/".join(self._parts())
        dest = share_import.spool(adder.cfg)
        declared = headers.get("content-length") or headers.get("x-expected-entity-length") or ""
        try:
            try:
                before = adder.tree.stat(path)
            except filetree.NotFound:
                before = None
            adder.check(path, int(declared) if declared.isdigit() else None)
        except (share_import.Refused, share_import.Rejected, filetree.NotFound) as e:
            self._skip()
            return self._refused(e)
        try:
            self._go_on()
            if "chunked" in headers.get("transfer-encoding", "").lower():
                self.close_connection = True  # a chunked body may have been read past its end
            limit = int(adder.cfg["uploads"]["max_mb"]) * uploads.MB
            _, md5 = http_body.receive(self.rfile, headers, dest, limit)
            adder.put(path, dest, headers.get("x-oc-mtime"), md5)  # imported (moved away), or kept aside there
        except (share_import.Refused, share_import.Rejected, filetree.NotFound, http_body.BodyError, OSError) as e:
            share_import._remove(dest)
            if isinstance(e, http_body.BodyError):
                self.close_connection = True
            try:
                return self._refused(e)
            except (BrokenPipeError, ConnectionResetError):  # the client went away mid-file
                return None
        self._send(204 if before is not None else 201, headers={"ETag": f'"{md5}"'})

    def do_MKCOL(self):  # noqa: N802
        got = self._adding()
        if got is None:
            return
        _, adder = got
        if int(self.headers.get("content-length") or 0) or self.headers.get("transfer-encoding"):
            self._skip()
            return self._send(415, b"MKCOL takes no body\n")
        try:
            made = adder.folder("/".join(self._parts()))
        except (share_import.Refused, share_import.Rejected, filetree.NotFound) as e:
            return self._refused(e)
        self._send(201 if made else 405, b"" if made else b"there's a folder there already\n")

    def do_DELETE(self):  # noqa: N802
        got = self._adding()
        if got is None:
            return
        self._drain()
        _, adder = got
        path = "/".join(self._parts())
        try:
            adder.delete(path)
        except share_import.Refused as e:
            try:
                adder.tree.stat(path)
            except filetree.NotFound:
                return self._send(404, b"not found\n")
            return self._refused(e)
        self._send(204)

    def do_MOVE(self):  # noqa: N802
        from . import shares

        got = self._adding()
        if got is None:
            return
        self._drain()
        _, adder = got
        dest = shares.share_path(urllib.parse.urlsplit(self.headers.get("destination", "")).path, self._prefix())
        to = "/".join(urllib.parse.unquote(p) for p in dest.split("/") if p)
        if not to:
            return self._send(400, b"say where to move it (Destination)\n")
        try:
            existed = adder.tree.stat(to) is not None
        except filetree.NotFound:
            existed = False
        if existed and self.headers.get("overwrite", "T").upper() == "F":
            return self._send(412, b"there's a file there already\n")
        try:
            adder.move("/".join(self._parts()), to)
        except (share_import.Refused, share_import.Rejected, filetree.NotFound, OSError) as e:
            return self._refused(e)
        self._send(204 if existed else 201)

    def do_LOCK(self):  # noqa: N802
        """A lock that holds nothing back (one person's own files, nobody else writes them): what Finder and Windows
        ask for before they write. On a name that isn't there yet it makes an empty file, as RFC 4918 says."""
        got = self._adding()
        if got is None:
            return
        self._drain()
        _, adder = got
        path = "/".join(self._parts())
        status = 200
        try:
            adder.tree.stat(path)
        except filetree.NotFound:
            empty = share_import.spool(adder.cfg)
            try:
                empty.touch()
                adder.put(path, empty)
            except (share_import.Refused, share_import.Rejected, filetree.NotFound, OSError) as e:
                share_import._remove(empty)
                return self._refused(e)
            status = 201
        token = self.headers.get("if", "").strip("()<> ")  # a refresh names the lock it renews
        token = token if re.fullmatch(r"opaquelocktoken:[0-9a-f-]{1,64}", token) else f"opaquelocktoken:{secrets.token_hex(16)}"
        depth = "0" if self.headers.get("depth", "infinity") == "0" else "infinity"
        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
            "<D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope>"
            f"<D:depth>{depth}</D:depth><D:owner>{escape(str(got[0].get('email') or ''))}</D:owner>"
            f"<D:timeout>Second-{LOCK_SECONDS}</D:timeout><D:locktoken><D:href>{token}</D:href></D:locktoken>"
            f"<D:lockroot><D:href>{escape(href(self._parts(), False, self._prefix()))}</D:href></D:lockroot>"
            "</D:activelock></D:lockdiscovery></D:prop>"
        ).encode()
        self._send(status, body, "application/xml; charset=utf-8", headers={"Lock-Token": f"<{token}>"})

    def do_UNLOCK(self):  # noqa: N802
        if self._adding() is None:
            return
        self._drain()
        self._send(204)

    def do_PROPPATCH(self):  # noqa: N802
        """Properties aren't kept: say each one was set, so clients that set times after a PUT (Windows) carry on."""
        got = self._adding()
        if got is None:
            return
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if 0 < n <= MAX_BODY else (self._drain() or b"")
        try:
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                raise ET.ParseError("no DTDs")
            names = [p.tag for x in ET.fromstring(raw).iter() if x.tag == "{DAV:}prop" for p in x] if raw else []
        except ET.ParseError:
            return self._send(400, b"PROPPATCH takes an XML body\n")
        try:
            got[1].tree.stat("/".join(self._parts()))
        except filetree.NotFound:
            return self._send(404, b"not found\n")
        props = "".join(f'<x:{t.split("}")[-1]} xmlns:x="{_attr(t[1:].split("}")[0] if "}" in t else "")}"/>' for t in names)
        body = (
            f'<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:"><D:response>'
            f"<D:href>{escape(href(self._parts(), False, self._prefix()))}</D:href><D:propstat><D:prop>{props}</D:prop>"
            "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"
        ).encode()
        self._send(207, body, "application/xml; charset=utf-8")

    def _tree(self, acct):
        db = self.server.db
        return filetree.Tree(db, self.server.cfg_fn(), filetree.Viewer.of(db, acct))

    def do_PROPFIND(self):  # noqa: N802
        self._drain()
        acct = self._account()
        if acct is None:
            return
        depth = self.headers.get("depth", "1").strip().lower()
        if depth not in ("0", "1"):
            return self._send(403, b"PROPFIND with Depth: infinity isn't supported; ask for depth 1\n")
        try:
            body = multistatus(self._tree(acct), self._parts(), depth, self._prefix())
        except filetree.NotFound:
            return self._send(404, b"not found\n")
        self._send(207, body, "application/xml; charset=utf-8")

    def do_GET(self):  # noqa: N802
        self._get()

    def do_HEAD(self):  # noqa: N802
        self._get()

    def _get(self):
        acct = self._account()
        if acct is None:
            return
        db, cfg = self.server.db, self.server.cfg_fn()
        try:
            e = self._tree(acct).stat("/".join(self._parts()))
        except filetree.NotFound:
            return self._send(404, b"not found\n")
        if e.folder:
            return self._send(200, b"a folder: open it with a WebDAV client\n")
        try:
            if e.encrypted:
                keyring.plain_size(db, cfg, e.path)
        except keyring.Locked:
            return self._send(403, b"this namespace is a locked vault: an owner unlocks it in the web app\n")
        start, end, status = 0, e.size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (self.headers.get("range") or "").strip())
        if m and m.groups() != ("", "") and e.size:
            a, b = m.groups()
            start, end = (max(0, e.size - int(b)), e.size - 1) if a == "" else (int(a), min(e.size - 1, int(b)) if b else e.size - 1)
            if start > end or start >= e.size:
                return self._send(416, headers={"Content-Range": f"bytes */{e.size}"})
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", e.content_type or "application/octet-stream")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("ETag", f'"{e.version}"')
        self.send_header("Last-Modified", _http_date(e.modified))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{e.size}")
        self.end_headers()
        if self.command == "HEAD" or end < start:
            return
        try:
            for chunk in e.read(db, cfg, start, end):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
