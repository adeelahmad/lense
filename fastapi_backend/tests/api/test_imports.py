"""Imports: pasted text and uploaded files, validation, previews that save nothing."""

from __future__ import annotations

import base64

from app.domain import settings, store
from tests.helpers import login, make_user, seed, write_pdf


def test_import_preview_saves_nothing(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    before = len(db.values("SELECT VALUE id FROM recording"))
    pv = client.post(
        "/api/v1/import/preview", headers=h, json={"text": "[00:01] Ann: Hi there.\n[00:04] Ben: Hello.\n[00:07] Ann: Bye."}
    ).json()
    assert (pv["format"], pv["segments"], pv["speakers"]) == ("text", 3, ["Ann", "Ben"])
    assert len(db.values("SELECT VALUE id FROM recording")) == before  # preview saves nothing
    assert (pv["preview"][0]["speaker"], pv["preview"][0]["text"], pv["duration_ms"] > 0) == ("Ann", "Hi there.", True)
    pdf = folder / "minutes.pdf"
    write_pdf(
        pdf,
        [
            "Carol: The budget review starts now.",
            "Dave: I have the numbers ready.",
            "Carol: Please share them.",
            "Dave: Revenue rose 12% last quarter.",
        ],
    )
    pv = client.post(
        "/api/v1/import/preview", headers=h, json={"filename": "minutes.pdf", "data": base64.b64encode(pdf.read_bytes()).decode()}
    ).json()
    assert (pv["format"], pv["speakers"]) == ("pdf", ["Carol", "Dave"])
    assert len(db.values("SELECT VALUE id FROM recording")) == before


def test_import_validation(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    post = lambda body: client.post("/api/v1/import", headers=h, json=body)  # noqa: E731
    assert post({"namespace": "Bad Name", "text": "A: b"}).status_code == 400
    assert post({"namespace": "notes", "text": "A: b", "format": "odt"}).status_code == 422
    assert post({"namespace": "notes", "filename": "x.txt", "data": "not base64!"}).status_code == 400
    assert post({"namespace": "notes", "text": "A: b", "surprise": 1}).status_code == 422
    r = post({"namespace": "notes", "text": "S1: hello there.\nS2: hi.\nS1: bye.", "title": "Named", "speakers": "S1=Ann, S2=Ben"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    rec = db.one("SELECT title FROM $r", r=store.R("recording", rid))
    assert rec["title"] == "Named"
    names = {s["display"] for s in client.get("/api/v1/speakers", params={"ns": "notes"}, headers=h).json()["speakers"]}
    assert names == {"Ann", "Ben"}
    assert db.values("SELECT VALUE action FROM audit_log WHERE action = 'import'") == ["import"]


def test_upload_limit(client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    data = base64.b64encode(b"A: b\nC: d\nA: e").decode()
    settings.save(db, cfg, "server", {"max_upload_mb": 0})
    r = client.post("/api/v1/import", headers=h, json={"namespace": "notes", "filename": "x.txt", "data": data})
    assert (r.status_code, r.json()["detail"]) == (413, "files up to 0 MB")
    assert client.post("/api/v1/import/preview", headers=h, json={"filename": "x.txt", "data": data}).status_code == 413
