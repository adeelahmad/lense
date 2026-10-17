"""The asset tree over WebDAV (docs/file-tree.md#webdav): a read-only WebDAV server (RFC 4918, class 1) that Finder,
Windows Explorer, GNOME Files, Dolphin, rclone and davfs2 mount as a drive.

Each person signs in with one of their access keys (s3.py): the key id as the user name and its secret as the
password (HTTP Basic). They see the namespaces and collections the web app shows them. OPTIONS, PROPFIND (depth 0
or 1), GET and HEAD (with Range); anything that writes answers 403. Run by shares.py.

With `shares.remote` on, the web app relays it at its own address too (https://<web app>/dav/), and links in listings
start with /dav (shares.web_path()).
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import http.server
import re
import urllib.parse
from typing import Any
from xml.sax.saxutils import escape

from . import auth, filetree, keyring, s3

ALLOW = "OPTIONS, GET, HEAD, PROPFIND"
REALM = 'Basic realm="Lens", charset="UTF-8"'
MAX_BODY = 1 << 20


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


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Lens"
    protocol_version = "HTTP/1.1"
    server: Any  # shares.Server

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("DAV", "1")
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
            self._send(200, headers={"Allow": ALLOW, "MS-Author-Via": "DAV"})

    def _write(self):
        self._drain()
        if not self._closed():
            self._send(403, b"the file tree is read-only\n", headers={"Allow": ALLOW})

    do_PUT = do_POST = do_DELETE = do_MKCOL = do_MOVE = do_COPY = do_PROPPATCH = do_LOCK = do_UNLOCK = _write  # noqa: N815

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
