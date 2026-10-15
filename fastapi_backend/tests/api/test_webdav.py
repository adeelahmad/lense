"""The asset tree over WebDAV (docs/file-tree.md#webdav): mounted with an access key (its id and secret as Basic
sign-in), listed with PROPFIND and read with GET, never written."""

from __future__ import annotations

import base64
import http.client
import xml.etree.ElementTree as ET

import pytest

from app.domain import auth, shares, webdav
from tests.helpers import login, make_user, seed

D = "{DAV:}"


@pytest.fixture
def env(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    k = client.post("/api/v1/shares/s3/keys", headers=hv, json={}).json()
    server = shares.Server("127.0.0.1", 0, db, lambda: cfg, handler=webdav.Handler).start()
    auth._FAILS.clear()
    yield {"port": server.port, "auth": "Basic " + base64.b64encode(f"{k['id']}:{k['secret']}".encode()).decode(), "k": k, "folder": folder}
    server.stop()


def _call(env, method, path, headers=None, auth_header=True, body=None):
    c = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=10)
    h = {**({"Authorization": env["auth"]} if auth_header else {}), **(headers or {})}
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


def _listing(body):
    out = []
    for r in ET.fromstring(body).findall(f"{D}response"):
        size = r.find(f".//{D}getcontentlength")
        out.append((r.find(f"{D}href").text, r.find(f".//{D}collection") is not None, None if size is None else size.text))
    return out


def test_mount_and_read(env):
    status, h, _ = _call(env, "OPTIONS", "/", auth_header=False)
    assert status == 200 and h["DAV"] == "1, 2" and "PROPFIND" in h["Allow"] and "PUT" in h["Allow"]
    status, _, body = _call(env, "PROPFIND", "/", {"Depth": "1"})
    assert status == 207 and [(x[0], x[1]) for x in _listing(body)] == [("/", True), ("/pods/", True)]
    status, _, body = _call(env, "PROPFIND", "/pods/Collections/General/", {"Depth": "1"})
    hrefs = [x[0] for x in _listing(body)]
    assert hrefs[0] == "/pods/Collections/General/" and "/pods/Collections/General/ep1%20%5B1%5D.txt" in hrefs
    size = next(int(x[2]) for x in _listing(body) if x[0].endswith("ep1%20%5B1%5D.txt"))
    status, _, body = _call(env, "PROPFIND", "/pods/Collections/General/ep1%20%5B1%5D.txt", {"Depth": "0"})
    assert status == 207 and len(_listing(body)) == 1
    status, h, body = _call(env, "GET", "/pods/Collections/General/ep1%20%5B1%5D.txt")
    assert status == 200 and len(body) == size and body.startswith(b"Alice: Welcome back.")
    own = (env["folder"] / "ep1.txt").read_bytes()
    status, h, body = _call(env, "GET", "/pods/Collections/General/ep1%20%5B1%5D.original.txt", {"Range": "bytes=2-5"})
    assert status == 206 and body == own[2:6] and h["Content-Range"] == f"bytes 2-5/{len(own)}"
    status, h, body = _call(env, "HEAD", "/pods/Collections/General/ep1%20%5B1%5D.txt")
    assert status == 200 and h["Content-Length"] == str(size) and body == b""


def test_read_only_when_files_are_not_taken(db, cfg, folder):
    seed(db, cfg, folder)
    uid = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    from app.domain import s3

    kid, secret = s3.create_key(db, cfg, uid)
    off = {**cfg, "shares": {**cfg["shares"], "add_files": False}}
    server = shares.Server("127.0.0.1", 0, db, lambda: off, handler=webdav.Handler).start()
    env = {"port": server.port, "auth": "Basic " + base64.b64encode(f"{kid}:{secret}".encode()).decode()}
    try:
        status, h, _ = _call(env, "OPTIONS", "/")
        assert h["DAV"] == "1" and "PUT" not in h["Allow"]
        assert _call(env, "PUT", "/pods/Collections/General/x.pdf", body=b"%PDF-1.4")[0] == 403
        assert _call(env, "LOCK", "/pods/Collections/General/x.pdf")[0] == 403
    finally:
        server.stop()


def test_what_webdav_refuses(env):
    assert _call(env, "PROPFIND", "/", {"Depth": "1"}, auth_header=False)[0] == 401
    assert _call(env, "PROPFIND", "/calls/", {"Depth": "1"})[0] == 404  # not theirs
    assert _call(env, "PROPFIND", "/", {"Depth": "infinity"})[0] == 403
    assert _call(env, "GET", "/pods/Collections/General/nope.txt")[0] == 404
    assert _call(env, "PUT", "/pods/Collections/General/x.txt", body=b"hi")[0] == 403
    assert _call(env, "DELETE", "/pods/Collections/General/ep1%20%5B1%5D.txt")[0] == 403
    assert _call(env, "MKCOL", "/pods/New/")[0] == 403
    wrong = "Basic " + base64.b64encode(f"{env['k']['id']}:nope".encode()).decode()
    for _ in range(8):
        assert _call(env, "PROPFIND", "/", {"Depth": "0", "Authorization": wrong})[0] == 401
    assert _call(env, "PROPFIND", "/", {"Depth": "0"})[0] == 429  # too many failed sign-ins from this address
