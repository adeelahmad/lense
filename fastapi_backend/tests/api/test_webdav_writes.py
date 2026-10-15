"""Adding files over WebDAV (docs/file-tree.md#adding-files): what Finder, Windows Explorer and rclone send when
they copy files and folders onto the drive, and the files are imported where they were put."""

from __future__ import annotations

import base64
import http.client
import xml.etree.ElementTree as ET

import pytest

from app.domain import auth, hierarchy, share_import, shares, store, webdav
from tests.helpers import login, make_user, seed, text_pdf

D = "{DAV:}"
PDF = text_pdf([["Harbour minutes", "The lamp was lit at dusk."]])


@pytest.fixture
def env(client, db, cfg, folder):
    seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    talks = hierarchy.create(db, pods, "Talks")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    keys = {}
    for who, pw in (("ed", "editor password 1"), ("vi", "viewer password 1")):
        k = client.post("/api/v1/shares/s3/keys", headers=login(client, f"{who}@x.io", pw), json={}).json()
        keys[who] = "Basic " + base64.b64encode(f"{k['id']}:{k['secret']}".encode()).decode()
    conf = {"cfg": cfg}
    server = shares.Server("127.0.0.1", 0, db, lambda: conf["cfg"], handler=webdav.Handler).start()
    auth._FAILS.clear()
    share_import.REGISTRY.items.clear()
    yield {"port": server.port, "keys": keys, "talks": talks, "pods": pods, "conf": conf}
    server.stop()


def _call(env, method, path, headers=None, body=None, who="ed", chunked=False):
    c = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=10)
    h = {"Authorization": env["keys"][who], **(headers or {})}
    if chunked:
        c.putrequest(method, path)
        for k, v in {**h, "Transfer-Encoding": "chunked"}.items():
            c.putheader(k, v)
        c.endheaders()
        for i in range(0, len(body), 100):
            piece = body[i : i + 100]
            c.send(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
        c.send(b"0\r\n\r\n")
    else:
        c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


def _names(env, path, who="ed"):
    status, _, body = _call(env, "PROPFIND", path, {"Depth": "1"}, who=who)
    assert status == 207, body
    return [r.find(f".//{D}displayname").text for r in ET.fromstring(body).findall(f"{D}response")][1:]


def _recording(db, title):
    return db.one("SELECT record::id(id) AS id, collection, size FROM recording WHERE title = $t", t=title)


def test_finder_copies_a_file(db, env):
    """Finder: an empty file, a lock, the bytes chunked, its ._ file, PROPPATCH, unlock."""
    path = "/pods/Collections/Talks/minutes.pdf"
    assert _call(env, "PUT", path, body=b"")[0] == 201
    status, h, body = _call(env, "LOCK", path, {"Timeout": "Second-600"}, body=b'<?xml version="1.0"?><D:lockinfo xmlns:D="DAV:"/>')
    assert status == 200 and h["Lock-Token"].startswith("<opaquelocktoken:") and b"activelock" in body
    status, h, _ = _call(env, "PUT", path, {"X-Expected-Entity-Length": str(len(PDF)), "If": f"({h['Lock-Token']})"}, PDF, chunked=True)
    assert status == 204 and h["ETag"].strip('"')
    assert _call(env, "PUT", "/pods/Collections/Talks/._minutes.pdf", body=b"\0\5\26\7AppleDouble")[0] == 201
    status, _, body = _call(
        env,
        "PROPPATCH",
        path,
        body=b'<?xml version="1.0"?><D:propertyupdate xmlns:D="DAV:" xmlns:Z="urn:schemas-microsoft-com:"><D:set><D:prop><Z:Win32LastModifiedTime>Thu, 15 Oct 2026 10:00:00 GMT</Z:Win32LastModifiedTime></D:prop></D:set></D:propertyupdate>',
    )
    assert status == 207 and b"Win32LastModifiedTime" in body and b"200 OK" in body
    assert _call(env, "UNLOCK", path, {"Lock-Token": "<opaquelocktoken:1>"})[0] == 204
    rec = _recording(db, "minutes")
    assert rec["collection"] == env["talks"] and rec["size"] == len(PDF)
    names = _names(env, "/pods/Collections/Talks/")
    assert {"minutes.pdf", "._minutes.pdf", f"minutes [{rec['id']}].pdf"} <= set(names)
    assert _call(env, "GET", path)[2] == PDF
    assert "minutes.pdf" not in _names(env, "/pods/Collections/Talks/", "vi")  # their own view only
    assert _call(env, "DELETE", "/pods/Collections/Talks/._minutes.pdf")[0] == 204  # Finder tidies up
    assert _call(env, "DELETE", path)[0] == 403  # an import isn't undone from here


def test_windows_copies_a_file(db, env):
    """Windows Explorer: a lock on a new name makes it (empty), then the bytes, its times, unlock."""
    path = "/pods/notes.pdf"
    status, h, _ = _call(env, "LOCK", path, {"Depth": "0"}, body=b'<?xml version="1.0"?><D:lockinfo xmlns:D="DAV:"/>')
    assert status == 201
    status, h2, _ = _call(env, "LOCK", path, {"If": f"({h['Lock-Token']})"})  # renewed
    assert status == 200 and h2["Lock-Token"] == h["Lock-Token"]
    assert _call(env, "PUT", path, body=PDF)[0] == 204
    assert _recording(db, "notes")["collection"] == store.default_collection(db, env["pods"])


def test_a_folder_dragged_in_becomes_collections(db, env):
    assert _call(env, "MKCOL", "/pods/Collections/Trips/")[0] == 201
    assert _call(env, "MKCOL", "/pods/Collections/Trips/Lisbon/")[0] == 201
    assert _call(env, "MKCOL", "/pods/Collections/Trips/")[0] == 405  # there already
    assert _call(env, "PUT", "/pods/Collections/Trips/Lisbon/day1.pdf", body=PDF)[0] == 201
    lisbon = next(c for c in hierarchy.of_space(db, env["pods"]) if c["name"] == "Lisbon")
    assert _recording(db, "day1")["collection"] == lisbon["id"]
    assert _call(env, "MKCOL", "/pods/Topics/New/")[0] == 403
    assert _call(env, "MKCOL", "/pods/Collections/Trips/x/", body=b"<x/>")[0] == 415


def test_an_upload_under_a_temporary_name_is_imported_when_renamed(db, env):
    assert _call(env, "PUT", "/pods/Collections/Talks/talk.pdf.filepart", body=PDF)[0] == 201
    assert not _recording(db, "talk")
    status, _, _ = _call(env, "MOVE", "/pods/Collections/Talks/talk.pdf.filepart",
                         {"Destination": f"http://127.0.0.1:{env['port']}/pods/Collections/Talks/talk.pdf"})  # fmt: skip
    assert status == 201 and _recording(db, "talk")["collection"] == env["talks"]
    assert "talk.pdf.filepart" not in _names(env, "/pods/Collections/Talks/")
    status, _, _ = _call(env, "MOVE", "/pods/Collections/General/ep1%20%5B1%5D.txt", {"Destination": "/pods/Collections/Talks/ep1.txt"})
    assert status == 403  # Lens's own files stay where they are


def test_what_may_not_be_added_is_refused(db, cfg, env):
    assert _call(env, "PUT", "/pods/Collections/Talks/x.pdf", body=PDF, who="vi")[0] == 403
    assert _call(env, "LOCK", "/pods/Collections/Talks/x.pdf", who="vi")[0] == 403
    assert _call(env, "PUT", "/pods/Collections/General/ep1%20%5B1%5D.json", body=b"{}")[0] == 403
    status, _, body = _call(env, "PUT", "/pods/tool.exe", body=b"MZ")
    assert status == 415 and b"can't be uploaded here" in body
    assert _call(env, "PUT", "/pods/Somewhere/x.pdf", body=PDF)[0] == 409
    assert _call(env, "PUT", "/calls/x.pdf", body=PDF)[0] == 409  # not theirs: no such folder, as for one that isn't there
    assert _call(env, "PUT", "/x.pdf", body=PDF)[0] == 403
    assert _call(env, "COPY", "/pods/Collections/General/ep1%20%5B1%5D.txt", {"Destination": "/pods/x.txt"})[0] == 403
    env["conf"]["cfg"] = {**cfg, "uploads": {**cfg["uploads"], "max_mb": 1}}
    # refused from what the client says it will send, before a byte of it is read
    assert _ask(env, "PUT /pods/big.pdf", {"Content-Length": str(2 << 20), "Expect": "100-continue"}).startswith(b"HTTP/1.1 413")
    assert _ask(env, "PUT /pods/big.pdf", {"X-Expected-Entity-Length": str(2 << 20), "Transfer-Encoding": "chunked"}).startswith(
        b"HTTP/1.1 413"
    )
    # and a chunked body that turns out larger than it said, once it has arrived; the client, still sending the rest,
    # reads the answer (the server drops what follows rather than reset the connection)
    big = PDF + b"\0" * (4 << 20)
    assert _call(env, "PUT", "/pods/big.pdf", {"X-Expected-Entity-Length": "10"}, big, chunked=True)[0] == 413
    assert _call(env, "PUT", "/pods/big.pdf", {"Content-Length": str(len(big))}, big)[0] == 413
    assert _ask(env, "PUT /pods/x.pdf", {"Content-Length": "5", "Expect": "100-continue"}).startswith(b"HTTP/1.1 100")
    assert not db.values("SELECT VALUE id FROM recording WHERE source = 'document'")


def _ask(env, line, headers):
    """Send a request's headers only, and the first answer's status line and headers."""
    import socket

    with socket.create_connection(("127.0.0.1", env["port"]), timeout=10) as s:
        h = {"Host": "x", "Authorization": env["keys"]["ed"], **headers}
        s.sendall((f"{line} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode())
        return s.recv(4096)
