"""Storage sources and watched folders. Listing and reading files goes through rclone; those tests need it installed."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time

import pytest

from app.domain import sources
from tests.helpers import drain, login, make_user

needs_rclone = pytest.mark.skipif(not shutil.which("rclone"), reason="rclone is not installed")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@needs_rclone
def test_sources_and_watched_folders(client, new_client, db, cfg, folder):
    inbox = folder / "inbox"
    (inbox / "calls").mkdir(parents=True)
    (inbox / "calls" / "call1.txt").write_text("Ann: from a watched folder.\nBen: great.\nAnn: bye.")
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    ce = new_client()
    he = login(ce, "ed@x.io", "editor password 1")
    assert ce.get("/api/v1/sources", headers=he).status_code == 403
    r = client.post("/api/v1/sources", json={"name": "inbox", "type": "local"}, headers=h)
    assert r.json()["health"]["ok"], r.text
    sid = r.json()["id"]
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "/etc"}, headers=h).status_code == 400  # outside local_roots
    assert [e["name"] for e in client.get(f"/api/v1/sources/{sid}/browse", params={"path": str(inbox)}, headers=h).json()] == ["calls"]
    r = client.post(
        "/api/v1/watches", json={"source": sid, "path": str(inbox), "namespace": "pods", "stable_seconds": 0, "backfill": True}, headers=h
    )
    assert r.status_code == 200, r.text
    assert sources.poll_due(db, cfg) == 1
    drain(db, cfg)
    assert db.one("SELECT status FROM recording WHERE title = 'call1'")["status"] == "analyzed"
    # S3, through rclone's own S3 server
    root = folder / "s3"
    (root / "media" / "eps").mkdir(parents=True)
    (root / "media" / "eps" / "ep1.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nHello from S3.\n\n2\n00:00:03,500 --> 00:00:05,000\nSecond cue.\n"
    )
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
        r = client.post(
            "/api/v1/sources",
            headers=h,
            json={
                "name": "bucket",
                "type": "s3",
                "secrets": {"secret_access_key": "SK-plain-123"},
                "params": {"provider": "Other", "endpoint": f"http://127.0.0.1:{port}", "access_key_id": "AKTEST"},
            },
        )
        assert r.json()["health"]["ok"], r.text
        listed = client.get("/api/v1/sources", headers=h)
        assert "SK-plain-123" not in listed.text
        assert [s for s in listed.json() if s["type"] == "s3"][0]["secrets"]["secret_access_key"]["set"]
        client.post(
            "/api/v1/watches",
            headers=h,
            json={"source": r.json()["id"], "path": "media/eps", "namespace": "calls", "stable_seconds": 0, "backfill": True},
        )
        assert sources.poll_due(db, cfg) == 1
        drain(db, cfg)
        assert db.one("SELECT status, path FROM recording WHERE title = 'ep1'") == {
            "status": "analyzed",
            "path": "bucket:media/eps/ep1.srt",
        }
    finally:
        srv.terminate()
        srv.wait()
    assert client.post("/api/v1/sources", json={"name": "x", "type": "s3", "params": {"bogus": 1}}, headers=h).status_code == 400


@needs_rclone
def test_watch_preview_counts_files(client, db, folder):
    inbox = folder / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("Ann: hi.\nBen: hello.")
    (inbox / "b.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi.\n")
    (inbox / "notes.xyz").write_text("ignored")
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    sid = client.post("/api/v1/sources", json={"name": "inbox", "type": "local"}, headers=h).json()["id"]
    r = client.post("/api/v1/watches/preview", json={"source": sid, "path": str(inbox)}, headers=h)
    assert r.json() == {"files": 2, "audio": 0, "transcripts": 2}
    r = client.post("/api/v1/watches/preview", json={"source": sid, "path": str(inbox), "exclude": ["*.srt"]}, headers=h)
    assert r.json()["transcripts"] == 1


def test_sources_and_watches_without_rclone(client, new_client, db, cfg, folder):
    """What doesn't need rclone: managing sources and watches, secrets, validation and who may do what."""
    inbox = folder / "inbox"
    (inbox / "calls").mkdir(parents=True)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"calls": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    he, ho = login(new_client(), "ed@x.io", "editor password 1"), login(new_client(), "own@x.io", "owner password 1")

    backends = client.get("/api/v1/sources/backends", headers=he).json()
    assert backends["s3"]["secrets"] == ["secret_access_key"] and backends["local"]["fields"] == {}
    assert client.get("/api/v1/sources", headers=he).status_code == 403
    assert client.post("/api/v1/sources", json={"name": "x", "type": "local"}, headers=he).status_code == 403

    r = client.post("/api/v1/sources", json={"name": "inbox", "type": "local"}, headers=h)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert set(r.json()["health"]) >= {"ok", "checked_at"}
    assert client.post("/api/v1/sources", json={"name": "x", "type": "s3", "params": {"bogus": 1}}, headers=h).status_code == 400
    assert client.post("/api/v1/sources", json={"name": "x", "type": "ftp"}, headers=h).status_code == 422
    r = client.post(
        "/api/v1/sources",
        headers=h,
        json={"name": "bucket", "type": "s3", "secrets": {"secret_access_key": "SK-plain-123"}, "params": {"access_key_id": "AK"}},
    )
    s3 = r.json()["id"]
    listed = client.get("/api/v1/sources", headers=h)
    assert "SK-plain-123" not in listed.text and "SK-plain-123" not in str(db.rows("SELECT * FROM storage_source"))
    bucket = next(s for s in listed.json() if s["id"] == s3)
    assert bucket["secrets"]["secret_access_key"] == {"secret": True, "set": True} and bucket["params"]["access_key_id"] == "AK"
    assert (
        client.patch(f"/api/v1/sources/{s3}", json={"name": "Bucket", "secrets": {"secret_access_key": None}}, headers=h).status_code == 200
    )
    bucket = next(s for s in client.get("/api/v1/sources", headers=h).json() if s["id"] == s3)
    assert bucket["name"] == "Bucket" and not bucket["secrets"]["secret_access_key"]["set"]
    assert client.patch(f"/api/v1/sources/{s3}", json={"params": {"bogus": 1}}, headers=h).status_code == 400
    assert client.patch("/api/v1/sources/999", json={"name": "x"}, headers=h).status_code == 404
    assert client.post("/api/v1/sources/999/test", headers=h).status_code == 404

    # browsing a local source: no path lists the allowed roots; anything outside them is refused
    roots = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()
    assert [e["path"] for e in roots] == [str(inbox)] and roots[0]["dir"]
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "/etc"}, headers=h).status_code == 400
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "/etc"}, headers=he).status_code == 403
    assert client.get("/api/v1/sources/999/browse", headers=h).status_code == 404

    # watched folders
    body = {"source": sid, "path": str(inbox / "calls"), "namespace": "calls", "stable_seconds": 0, "backfill": True}
    assert client.post("/api/v1/watches", json=body, headers=he).status_code == 403
    r = client.post("/api/v1/watches", json=body, headers=h)
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.post("/api/v1/watches", json={**body, "path": "/etc"}, headers=h).status_code == 400
    assert client.post("/api/v1/watches", json={**body, "source": 999}, headers=h).status_code == 400
    assert client.post("/api/v1/watches", json={**body, "namespace": "Not A Name"}, headers=h).status_code == 400
    assert client.post("/api/v1/watches", json={**body, "kinds": "video"}, headers=h).status_code == 422
    assert client.post("/api/v1/watches", json={**body, "steps": ["juggle"]}, headers=h).status_code == 400
    w = client.get("/api/v1/watches", headers=h).json()
    assert [(x["id"], x["namespace"], x["source_name"], x["stable_seconds"], x["kinds"]) for x in w] == [(wid, "calls", "inbox", 0, "both")]
    assert [x["id"] for x in client.get("/api/v1/watches", headers=ho).json()] == [wid]  # owners of the namespace see it
    assert client.get("/api/v1/watches", headers=he).json() == []  # others don't
    assert client.patch(f"/api/v1/watches/{wid}", json={"poll_minutes": 30, "enabled": False}, headers=h).status_code == 200
    assert [(x["poll_minutes"], x["enabled"]) for x in client.get("/api/v1/watches", headers=h).json()] == [(30, False)]
    assert client.patch(f"/api/v1/watches/{wid}", json={"poll_minutes": 0}, headers=h).status_code == 422
    assert client.patch(f"/api/v1/watches/{wid}", json={"poll_minutes": 30}, headers=ho).status_code == 403
    assert client.patch("/api/v1/watches/999", json={"poll_minutes": 30}, headers=h).status_code == 404
    assert client.post("/api/v1/watches/999/scan", headers=h).status_code == 404
    r = client.post(f"/api/v1/watches/{wid}/scan", headers=h)
    assert (r.status_code, r.json()) == (202, {"ok": True, "status": "scanning"})

    # deleting a source takes its watches with it
    assert client.delete(f"/api/v1/sources/{sid}", headers=h).status_code == 200
    assert client.get("/api/v1/watches", headers=h).json() == []
    actions = [a["action"] for a in client.get("/api/v1/audit", headers=h).json()]
    assert {"source.create", "source.update", "source.delete", "watch.create"} <= set(actions)
