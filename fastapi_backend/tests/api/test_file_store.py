"""Where Lens keeps its own files (blobs.py): this machine, or a storage connection through rclone, optionally crypt."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time

import pytest

from app.domain import ai_tools, blobs, keyring, store
from tests.helpers import login, make_user

needs_rclone = pytest.mark.skipif(not shutil.which("rclone"), reason="rclone is not installed")


def _roundtrip(db, cfg, sid, where, folder, name="note.txt", data=b"the quarterly numbers\n" * 50):
    src = folder / name
    src.write_bytes(data)
    key = f"notes/{sid}/1/{name}"
    got = blobs.put(db, cfg, sid, key, src, where)
    assert src.read_bytes() == data  # the original is left as it was
    with blobs.open_file(db, cfg, got, key) as f:
        assert f.read() == data
    return key, got


def test_on_this_machine(client, db, cfg, folder):
    pods = store.ns_id(db, "pods")
    key, where = _roundtrip(db, cfg, pods, None, folder)
    assert where == {"store": "local"}
    kept = folder / "data" / "objects" / key
    assert kept.read_bytes().startswith(b"the quarterly")  # plain, like Lens's other files while encryption.files is off
    blobs.delete(db, cfg, where, key)
    assert not kept.exists()
    blobs.delete(db, cfg, where, key)  # already gone: fine
    with pytest.raises(KeyError), blobs.open_file(db, cfg, where, key):
        pass
    with pytest.raises(ValueError):
        blobs.put(db, cfg, pods, "../escape", folder / "note.txt")
    assert blobs.test(db, cfg)["ok"]


def test_settings(client, db, cfg, folder):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    put = lambda body: client.put("/api/v1/settings/files", headers=h, json=body)  # noqa: E731
    assert put({"store": "connection"}).status_code == 400  # which connection?
    assert put({"store": "cloud"}).status_code == 400
    assert put({"folder": "a/../b"}).status_code == 400
    assert put({"store": "connection", "connection": 999}).status_code == 400
    cal = client.post("/api/v1/sources", headers=h, json={"name": "cal", "type": "ical", "params": {"url": "https://example.org/a.ics"}})
    if cal.status_code == 200:  # a calendar isn't somewhere files can be kept
        assert put({"store": "connection", "connection": cal.json()["id"]}).status_code == 400
    r = client.post("/api/v1/settings/files/test", headers=h)
    assert r.json()["ok"] and r.json()["store"] == "local", r.text
    r = client.post("/api/v1/settings/files/test", headers=h, json={"store": "connection"})
    assert (r.json()["ok"], r.json()["error"]) == (False, "choose a storage connection")
    files = client.get("/api/v1/settings", headers=h).json()["files"]["values"]
    assert files == {"store": "local", "connection": None, "folder": "lens", "crypt": False}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@needs_rclone
def test_on_a_connection(client, db, cfg, folder):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    pods = store.ns_id(db, "pods")
    (folder / "inbox" / "kept").mkdir(parents=True)
    disk = client.post("/api/v1/sources", headers=h, json={"name": "disk", "type": "local"}).json()["id"]

    # a folder on this machine, through rclone: it only ever holds ciphertext
    where = {"store": "connection", "connection": disk, "folder": str(folder / "inbox" / "kept"), "crypt": False}
    key, got = _roundtrip(db, cfg, pods, where, folder)
    assert got == where
    raw = (folder / "inbox" / "kept" / key).read_bytes()
    assert raw.startswith(keyring.MAGIC) and b"quarterly" not in raw
    # with crypt, not even the names show
    crypt = {**where, "crypt": True}
    ckey, _ = _roundtrip(db, cfg, pods, crypt, folder, name="secret-plan.txt")
    names = [p.name for p in (folder / "inbox" / "kept").rglob("*")]
    assert "secret-plan.txt" not in names and len(names) > 3
    # the crypt password is made once, kept sealed, and the same the next time
    assert blobs._crypt_secrets(db, cfg) == blobs._crypt_secrets(db, cfg)
    blobs.delete(db, cfg, crypt, ckey)
    with pytest.raises(KeyError), blobs.open_file(db, cfg, crypt, ckey):
        pass
    # outside sources.local_roots: refused
    with pytest.raises(ValueError):
        blobs.put(db, cfg, pods, "x/y", folder / "note.txt", {**where, "folder": "/etc"})

    # saved, then checked from Settings → Storage and by the assistant
    r = client.put(
        "/api/v1/settings/files",
        headers=h,
        json={"store": "connection", "connection": disk, "folder": str(folder / "inbox" / "kept"), "crypt": True},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/settings/files/test", headers=h)
    assert r.json()["ok"] and r.json()["store"] == "connection", r.text
    r = client.post("/api/v1/settings/files/test", headers=h, json={"store": "connection", "connection": disk, "folder": "/etc"})
    assert not r.json()["ok"] and "local_roots" in r.json()["error"]
    current = client.app.state.archive.current()
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'root@x.io'")["id"]
    box = ai_tools.Toolbox(db, current, {"id": uid, "email": "root@x.io", "admin": True}, {pods}, {pods}, {}, None, admin=True)
    out = json.loads(box.call("file_storage", {"check": True})[0])
    assert out["store"] == "connection" and out["crypt"] and out["check"]["ok"], out
    assert [c["name"] for c in out["connections"]] == ["disk"]
    # what was kept before the setting changed is still read from where it went
    with blobs.open_file(db, cfg, got, key) as f:
        assert f.read().startswith(b"the quarterly")

    # S3, through rclone's own S3 server
    root = folder / "s3"
    (root / "bucket").mkdir(parents=True)
    port = _free_port()
    srv = subprocess.Popen(
        ["rclone", "serve", "s3", str(root), "--addr", f"127.0.0.1:{port}", "--auth-key", "AKTEST,SK-plain-123"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        s3 = client.post(
            "/api/v1/sources",
            headers=h,
            json={
                "name": "bucket",
                "type": "s3",
                "secrets": {"secret_access_key": "SK-plain-123"},
                "params": {"provider": "Other", "endpoint": f"http://127.0.0.1:{port}", "access_key_id": "AKTEST"},
            },
        ).json()["id"]
        s3where = {"store": "connection", "connection": s3, "folder": "bucket/lens", "crypt": False}
        key, _ = _roundtrip(db, cfg, pods, s3where, folder, name="s3.txt")
        assert (root / "bucket" / "lens" / key).read_bytes().startswith(keyring.MAGIC)
        assert blobs.test(db, cfg, {**s3where, "crypt": True})["ok"]
    finally:
        srv.terminate()
        srv.wait()
