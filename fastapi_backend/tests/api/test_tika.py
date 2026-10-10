"""Apache Tika as a document reader (tika.py): with a Tika server set, documents nothing here can make into a PDF are
read as text, and every document's own description is read as Dublin Core terms; without one, nothing changes."""

from __future__ import annotations

import os

import pytest

from app.domain import anytopdf, convert, settings, store, tika
from tests import fake_tika
from tests.api.test_conversion import _text
from tests.api.test_documents import _start, _upload
from tests.helpers import drain, login, make_user, text_pdf, write_docx

R = store.R
REAL = os.environ.get("LENS_TEST_TIKA")  # a real Tika server's address, such as http://127.0.0.1:9998


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"ha": login(client, "root@x.io", "root password 1"), "he": login(client, "ed@x.io", "editor password 1")}


@pytest.fixture
def lean(monkeypatch):
    """A server with neither LibreOffice, Chromium nor anytopdf (the lean image on a Raspberry Pi)."""
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    monkeypatch.setattr(anytopdf, "available", lambda cfg: None)


@pytest.fixture
def server():
    srv, url = fake_tika.server()
    yield url
    srv.shutdown()
    srv.server_close()


def _set(client, ha, **changes):
    r = client.put("/api/v1/settings/documents", headers=ha, json=changes)
    assert r.status_code == 200, r.text


def _rec(db, rid):
    return db.one("SELECT status, engine, rendition, described, title, recorded_at, media, error FROM $r", r=R("recording", rid))


def test_off_by_default_nothing_changes(lean, client, env):
    he = env["he"]
    assert client.get("/api/v1/uploads/limits", headers=env["he"]).json()["convert"] == {
        "office": False,
        "pages": False,
        "msg": False,
        "web": False,
        "tika": False,
    }
    r = _start(client, he, b"x", "letter.docx")
    assert r.status_code == 400 and "or a Tika server" in r.json()["detail"]
    got = client.get("/api/v1/settings", headers=env["ha"]).json()["documents"]["values"]
    assert (got["tika_url"], got["tika_text"], got["tika_metadata"]) == (None, True, True)


def test_what_nothing_here_can_convert_tika_reads_as_text(lean, server, client, env, db, cfg, folder):
    he, ha = env["he"], env["ha"]
    _set(client, ha, tika_url=server + "/")
    assert client.get("/api/v1/settings", headers=ha).json()["documents"]["values"]["tika_url"] == server
    assert client.get("/api/v1/uploads/limits", headers=he).json()["convert"] == {
        "office": True,
        "pages": True,
        "msg": True,
        "web": False,
        "tika": True,
    }
    write_docx(folder / "letter.docx", ["The harbour report", "Ships arrived at dawn."])
    doc = _upload(client, he, (folder / "letter.docx").read_bytes(), "letter.docx")
    drain(db, settings.effective(db, cfg))

    rec = _rec(db, doc["recording"])
    assert (rec["status"], rec["engine"], rec.get("rendition")) == ("analyzed", "text:tika", None), rec.get("error")
    assert rec["media"]["pages"] == 0
    text = _text(db, doc["recording"])
    assert "Ships arrived at dawn." in text and "A note tucked inside." in text  # what's in it too
    log = client.get(f"/api/v1/jobs/{doc['job']}/log", headers=he).json()["lines"]
    assert any("Tika reads it instead" in x for x in log) and any("read by Tika as text" in x for x in log)

    # what it says about itself: kept as Dublin Core terms, its title and date the resource's, its authors its metadata's
    assert rec["described"] == {
        "title": "Tide tables",
        "creators": ["Ada Harbour", "Ben Quay"],
        "language": "en",
        "created": "2024-03-01T12:00:00Z",
        "pages": 3,
        "format": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert rec["title"] == "Tide tables" and str(rec["recorded_at"]).startswith("2024-03-01")
    got = client.get(f"/api/v1/resources/{doc['recording']}", headers=he).json()
    assert got["described"]["creators"] == ["Ada Harbour", "Ben Quay"]
    meta = client.get(f"/api/v1/recordings/{doc['recording']}/metadata", headers=he).json()["meta"]
    assert [c["name"] for c in meta["creators"]] == ["Ada Harbour", "Ben Quay"] and meta["language"] == ["en"]

    # sent as its kind, never by its own name, with nothing but the document
    put = [s for s in fake_tika.Tika.seen if s["method"] == "PUT"]
    assert len(put) == 1 and put[0]["headers"]["Content-Disposition"] == "attachment; filename=document.docx"


def test_a_document_tika_cannot_read_fails_saying_so(lean, server, client, env, db, cfg):
    he, ha = env["he"], env["ha"]
    _set(client, ha, tika_url=server)
    bad = _upload(client, he, b"unreadable\n", "bad.txt")
    drain(db, settings.effective(db, cfg))
    job = client.get(f"/api/v1/jobs/{bad['job']}", headers=he).json()
    assert job["status"] == "failed" and "Tika couldn't read it (Unexpected RuntimeException)" in job["error"]


def test_tika_describes_what_lens_reads_itself(server, client, env, db, cfg):
    he, ha = env["he"], env["ha"]
    _set(client, ha, tika_url=server)
    up = _upload(client, he, text_pdf([["Harbour report"]]), "harbour.pdf", title="Harbour report")
    plain = _upload(client, he, text_pdf([["nothing about itself"]]), "plain.pdf")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, up["recording"])
    assert (rec["status"], rec["engine"]) == ("analyzed", "pdf"), rec.get("error")
    assert rec["described"]["title"] == "Tide tables" and rec["title"] == "Harbour report"  # named by someone: kept
    assert "Harbour report" in _text(db, up["recording"])  # its text is still Lens's own reading
    rec = _rec(db, plain["recording"])
    assert rec["described"] == {"format": "application/octet-stream"}
    assert rec["title"] == "plain"

    # off: nothing is sent, and nothing kept
    _set(client, ha, tika_metadata=False)
    sent = len(fake_tika.Tika.seen)
    again = _upload(client, he, text_pdf([["Tides"]]), "again.pdf")
    drain(db, settings.effective(db, cfg))
    assert _rec(db, again["recording"]).get("described") is None and len(fake_tika.Tika.seen) == sent


def test_a_tika_server_that_is_gone_does_not_stop_a_document(server, client, env, db, cfg):
    he, ha = env["he"], env["ha"]
    _set(client, ha, tika_url="http://127.0.0.1:9")
    up = _upload(client, he, text_pdf([["Harbour report"]]), "harbour.pdf")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, up["recording"])
    assert rec["status"] == "analyzed" and rec.get("described") is None, rec.get("error")
    log = client.get(f"/api/v1/jobs/{up['job']}/log", headers=he).json()["lines"]
    assert any("Tika couldn't say what it is: the Tika server can't be reached" in x for x in log)


def test_tika_settings_and_check(server, client, env, cfg):
    ha = env["ha"]
    put = lambda changes: client.put("/api/v1/settings/documents", headers=ha, json=changes)  # noqa: E731
    assert put({"tika_url": "ftp://tika"}).status_code == 400
    assert put({"tika_url": "http://user:secret@tika:9998"}).status_code == 400
    assert put({"tika_text": "yes"}).status_code == 400
    test = lambda: client.post("/api/v1/settings/documents/tika/test", headers=ha).json()  # noqa: E731
    assert test() == {"ok": False, "error": "set the Tika server's address first (documents.tika_url)", "version": None, "ms": None}
    assert put({"tika_url": server}).status_code == 200
    got = test()
    assert got["ok"] and got["version"] == "Apache Tika 4.0.0"
    assert client.post("/api/v1/settings/documents/tika/test", headers=env["he"]).status_code == 403
    assert put({"tika_url": "http://127.0.0.1:9"}).status_code == 200
    got = test()
    assert not got["ok"] and "it can't be reached" in got["error"]
    assert put({"tika_url": ""}).status_code == 200


def test_a_redirect_is_never_followed(server, cfg, tmp_path):
    cfg["documents"]["tika_url"] = server
    fake_tika.Tika.redirect = True
    f = tmp_path / "a.txt"
    f.write_text("Tides")
    with pytest.raises(ValueError, match="the Tika server said 302"):
        tika.read(cfg, f)
    with pytest.raises(ValueError, match="it said 302"):
        tika.version(cfg)
    assert all("elsewhere" not in s["path"] for s in fake_tika.Tika.seen)


def test_what_tika_says_is_bounded(cfg, tmp_path, monkeypatch):
    part = {
        "dc:title": "x" * 900,
        "dc:creator": "Ada Harbour; Ben Quay;  ",
        "dcterms:created": "not a date",
        "dc:language": "English, mostly",
        "meta:page-count": "-2",
        "tk:content": "a" * 50,
    }
    d = tika.described(part)
    assert d == {"title": "x" * 500, "creators": ["Ada Harbour", "Ben Quay"]}
    monkeypatch.setattr(tika, "MAX_TEXT", 70)
    assert len(tika.text_of([part, {"X-TIKA:content": "b" * 50}])) == 70 + 2  # Tika 2.x and 3.x keys read too


@pytest.mark.skipif(not REAL, reason="LENS_TEST_TIKA names a real Tika server")
def test_a_real_tika_server(lean, client, env, db, cfg, folder):
    he, ha = env["he"], env["ha"]
    _set(client, ha, tika_url=REAL)
    assert client.post("/api/v1/settings/documents/tika/test", headers=ha).json()["ok"]
    write_docx(folder / "letter.docx", ["The harbour report", "Ships arrived at dawn."])
    doc = _upload(client, he, (folder / "letter.docx").read_bytes(), "letter.docx")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, doc["recording"])
    assert (rec["status"], rec["engine"]) == ("analyzed", "text:tika"), rec.get("error")
    assert "Ships arrived at dawn." in _text(db, doc["recording"])
