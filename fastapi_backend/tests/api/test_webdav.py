"""The asset tree over WebDAV (docs/file-tree.md#webdav): mounted with an access key (its id and secret as Basic
sign-in), listed with PROPFIND and read with GET, never written."""

from __future__ import annotations

import base64
import http.client
import xml.etree.ElementTree as ET

import pytest

from app.domain import auth, shares, webdav
from tests.helpers import login, make_user, seed, web_relay

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


def _call(env, method, path, headers=None, auth_header=True, body=None, port=None):
    c = http.client.HTTPConnection("127.0.0.1", port or env["port"], timeout=10)
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
    assert status == 200 and h["DAV"] == "1" and "PROPFIND" in h["Allow"]
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


def test_at_the_web_apps_address_once_an_admin_allows_it(env, cfg):
    """The web app relays WebDAV at /dav (nextjs-frontend/lens-relay.js), so remote access reaches it; listings link
    under /dav, and it's turned away, before any sign-in, until an admin turns on shares.remote."""
    with web_relay(env["port"], "/dav") as port:
        assert _call(env, "OPTIONS", "/dav/", auth_header=False, port=port)[0] == 403
        assert _call(env, "PROPFIND", "/dav/", {"Depth": "1"}, port=port)[0] == 403
        cfg["shares"] = {**(cfg.get("shares") or {}), "remote": True}
        status, h, _ = _call(env, "OPTIONS", "/dav/", auth_header=False, port=port)
        assert status == 200 and "PROPFIND" in h["Allow"]
        status, _, body = _call(env, "PROPFIND", "/dav/", {"Depth": "1"}, port=port)
        assert status == 207 and [(x[0], x[1]) for x in _listing(body)] == [("/dav/", True), ("/dav/pods/", True)]
        status, _, body = _call(env, "PROPFIND", "/dav/pods/Collections/General/", {"Depth": "1"}, port=port)
        assert "/dav/pods/Collections/General/ep1%20%5B1%5D.txt" in [x[0] for x in _listing(body)]
        status, _, body = _call(env, "GET", "/dav/pods/Collections/General/ep1%20%5B1%5D.txt", port=port)
        assert status == 200 and body.startswith(b"Alice: Welcome back.")
        assert _call(env, "PUT", "/dav/pods/Collections/General/x.txt", body=b"hi", port=port)[0] == 403
    # at its own port, links are as before
    status, _, body = _call(env, "PROPFIND", "/", {"Depth": "1"})
    assert [x[0] for x in _listing(body)] == ["/", "/pods/"]


def test_through_the_web_app_each_visitor_has_its_own_throttle(env, cfg):
    """Every request through the web app comes from its address; the visitor's, from X-Forwarded-For, is throttled."""
    cfg["shares"] = {**(cfg.get("shares") or {}), "remote": True}
    wrong = "Basic " + base64.b64encode(f"{env['k']['id']}:nope".encode()).decode()
    with web_relay(env["port"], "/dav", forwarded_for="198.51.100.1") as port:
        for _ in range(8):
            assert _call(env, "PROPFIND", "/dav/", {"Depth": "0", "Authorization": wrong}, port=port)[0] == 401
        assert _call(env, "PROPFIND", "/dav/", {"Depth": "0"}, port=port)[0] == 429
    with web_relay(env["port"], "/dav", forwarded_for="198.51.100.2") as port:
        assert _call(env, "PROPFIND", "/dav/", {"Depth": "0"}, port=port)[0] == 207  # someone else isn't locked out
    assert _call(env, "PROPFIND", "/", {"Depth": "0"})[0] == 207
