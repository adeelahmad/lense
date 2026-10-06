"""Documents that aren't PDFs (docs/api.md#documents-and-images): Word and other Office files, text, Markdown and
emails are made into PDFs and read like one; an email's attachments are kept as its files and, those Lens can read,
made resources of their own; and nothing a document refers to is fetched on the way."""

from __future__ import annotations

import http.server
import shutil
import threading
from email.message import EmailMessage

import pytest

from app.domain import convert, jobs, keyring, metadata, settings, sources, store
from app.domain import files as files_mod
from tests import fake_llm
from tests.api.test_documents import HARBOUR, _png, _start, _upload
from tests.helpers import chromium_binary, drain, login, make_user, scan, text_pdf, write_docx

R = store.R
CHROME = chromium_binary()
SOFFICE = pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")), reason="needs LibreOffice")
PAGES = pytest.mark.skipif(not (CHROME or shutil.which("soffice")), reason="needs Chromium or LibreOffice")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


@pytest.fixture(autouse=True)
def printer(cfg):
    cfg["documents"]["chromium"] = CHROME  # None: LibreOffice prints pages
    cfg["documents"]["convert_seconds"] = 120


def _text(db, rid):
    return " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid))


@SOFFICE
def test_office_and_text_files_become_documents(app, client, env, db, cfg, folder):
    from fastapi.testclient import TestClient

    he, hv = env["he"], env["hv"]
    write_docx(folder / "letter.docx", ["The harbour report", "Ships arrived at dawn and the cargo was counted."])
    doc = _upload(client, he, (folder / "letter.docx").read_bytes(), "letter.docx")
    md = _upload(client, he, b"# Lighthouse log\n\nThe keeper wrote **twice**.\n\n- oil\n- wicks\n", "log.md")
    txt = _upload(client, he, "Café notes\n\nTide at noon.\n".encode(), "notes.txt")
    drain(db, cfg)

    rec = db.one("SELECT source, rendition, media, status, engine, error FROM $r", r=R("recording", doc["recording"]))
    assert (rec["source"], rec["status"], rec["rendition"]) == ("document", "analyzed", {"from": ".docx", "by": "libreoffice"}), rec.get(
        "error"
    )
    assert rec["media"]["pages"] == 1 and rec["engine"] == "pdf"
    assert "Ships arrived at dawn and the cargo was counted." in _text(db, doc["recording"])
    log = client.get(f"/api/v1/jobs/{doc['job']}/log", headers=he).json()["lines"]
    assert any("made into a PDF by LibreOffice" in x for x in log)
    for up, words in ((md, ["Lighthouse log", "The keeper wrote twice.", "oil"]), (txt, ["Café notes", "Tide at noon."])):
        r = db.one("SELECT rendition, status, error FROM $r", r=R("recording", up["recording"]))
        assert r["status"] == "analyzed" and r["rendition"]["by"] == ("chromium" if CHROME else "libreoffice"), (CHROME, r.get("error"))
        assert all(w in _text(db, up["recording"]) for w in words), _text(db, up["recording"])

    # its own file, and the PDF made of it, to save
    primary = client.get(f"/api/v1/resources/{doc['recording']}/files", headers=hv).json()["primary"]
    assert (primary["kind"], primary["name"], primary["content_type"]) == ("document", "letter.docx", DOCX)
    got = client.get(primary["pdf"])
    assert (got.status_code, got.headers["content-type"]) == (200, "application/pdf") and got.content.startswith(b"%PDF")
    assert 'filename="letter.pdf"' in got.headers["content-disposition"] and "sandbox" in got.headers["content-security-policy"]
    assert client.get(primary["download"]).content == (folder / "letter.docx").read_bytes()
    assert client.get(f"/api/v1/resources/{doc['recording']}/pdf").status_code == 401  # not without a token or signature
    assert client.get(f"/api/v1/resources/{doc['recording']}/pdf", headers=hv).status_code == 200
    rec_view = client.get(f"/api/v1/resources/{doc['recording']}", headers=hv).json()
    assert rec_view["rendition"] == {"from": ".docx", "by": "libreoffice"} and rec_view["attached_to"] is None

    # IIIF offers both: the Word document and the PDF
    metadata.save(db, cfg, doc["recording"], {"access": "public"})
    anon = TestClient(app, base_url="https://127.0.0.1")
    own = anon.get(f"/iiif/{doc['recording']}/manifest").json()["rendering"][:2]
    assert [(r["label"]["en"][0], r["format"]) for r in own] == [("The Word document", DOCX), ("The PDF", "application/pdf")]
    assert anon.get(f"/iiif/{doc['recording']}/pdf").headers["content-type"] == "application/pdf"
    public = anon.get(f"/api/v1/public/recordings/{doc['recording']}").json()["media"]
    assert public["file"] == "Word document" and "sig=" in public["pdf"]

    # deleted: the PDF made of it goes too
    assert client.delete(f"/api/v1/resources/{doc['recording']}", headers=env["ha"]).status_code == 200
    assert not convert.rendition_path(cfg, doc["recording"]).exists()


class _Tracker(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802 - the handler's name
        _Tracker.seen.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _email(subject, port, attachments=True):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = "Mara Keane <mara@example.org>"
    m["To"] = "tom@example.org"
    m["Date"] = "Tue, 29 Sep 2026 09:30:00 +0100"
    m.set_content("The ships arrived at dawn. The lighthouse keeper wrote twice.")
    m.add_alternative(
        f'<p>The ships arrived at dawn. The <b>lighthouse keeper</b> wrote twice.</p><img src="http://127.0.0.1:{port}/track.png">',
        subtype="html",
    )
    if attachments:
        m.add_attachment(text_pdf(HARBOUR), maintype="application", subtype="pdf", filename="harbour.pdf")
        m.add_attachment(_png(scan(["Galway harbour"])), maintype="image", subtype="png", filename="photo.png")
        m.add_attachment(b"just data", maintype="application", subtype="octet-stream", filename="data.xyz")
        reply = EmailMessage()
        reply["Subject"] = "Re harbour"
        reply.set_content("Received with thanks.")
        m.add_attachment(reply)
    return m.as_bytes()


@PAGES
def test_an_email_becomes_a_document_and_its_attachments_resources(client, env, db, cfg):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Tracker)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Tracker.seen = []
    try:
        he, ha = env["he"], env["ha"]
        up = _upload(client, he, _email("Harbour report", srv.server_address[1]), "mail.eml")
        drain(db, settings.effective(db, cfg))  # as the server's workers run: with the archive's settings (encrypting)
    finally:
        srv.shutdown()
    rid = up["recording"]
    assert _Tracker.seen == []  # the tracking image wasn't fetched
    rec = client.get(f"/api/v1/resources/{rid}", headers=he).json()
    why = (CHROME, (db.one("SELECT error FROM $r", r=R("recording", rid)) or {}).get("error"))
    assert (rec["title"], rec["recorded_at"], rec["status"]) == ("Harbour report", "2026-09-29T09:30:00+01:00", "analyzed"), why
    sent = {"subject": "Harbour report", "from": "Mara Keane <mara@example.org>", "to": "tom@example.org", "cc": None}
    assert rec["email"] == {**sent, "date": rec["recorded_at"]}
    assert "The lighthouse keeper wrote twice." in _text(db, rid)

    # its attachments are its files; those Lens reads are resources of their own, which say where they came from
    files = {f["name"]: f for f in client.get(f"/api/v1/resources/{rid}/files", headers=he).json()["files"]}
    assert set(files) == {"harbour.pdf", "photo.png", "data.xyz", "Re harbour.eml"} and {f["role"] for f in files.values()} == {
        "attachment"
    }
    assert files["data.xyz"]["resource"] is None
    made = {n: files[n]["resource"] for n in ("harbour.pdf", "photo.png", "Re harbour.eml")}
    assert all(made.values())
    rows = {n: db.one("SELECT source, attached_to, status, title FROM $r", r=R("recording", x)) for n, x in made.items()}
    assert {n: r["source"] for n, r in rows.items()} == {"harbour.pdf": "document", "photo.png": "image", "Re harbour.eml": "document"}
    assert rows["harbour.pdf"]["attached_to"] == {"resource": rid, "file": files["harbour.pdf"]["id"]}
    assert all(r["status"] == "analyzed" for r in rows.values()), rows
    assert "lighthouse keeper" in _text(db, made["harbour.pdf"]) and "Received with thanks." in _text(db, made["Re harbour.eml"])
    # a new archive keeps them encrypted, once each: they download as they were sent
    kept = db.one("SELECT path FROM $r", r=R("recording", made["harbour.pdf"]))["path"]
    assert keyring.is_encrypted(kept) and keyring.is_encrypted(files_mod.path_of(cfg, {"recording": rid, **files["harbour.pdf"]}))
    assert client.get(f"/api/v1/recordings/{made['harbour.pdf']}/media", headers=he).content == text_pdf(HARBOUR)
    att = client.get(f"/api/v1/resources/{made['harbour.pdf']}", headers=env["hv"]).json()
    assert att["attached_to"] == {"resource": rid, "file": files["harbour.pdf"]["id"], "title": "Harbour report"}
    log = client.get(f"/api/v1/jobs/{up['job']}/log", headers=he).json()["lines"]
    assert any("4 attachment(s): 4 kept now, 3 made resource(s) of their own" in x for x in log)

    # transcribed again: nothing is kept or made twice
    jobs.enqueue(db, rid, ["transcribe"], by="test")
    drain(db, cfg)
    assert len(client.get(f"/api/v1/resources/{rid}/files", headers=he).json()["files"]) == 4
    assert len(db.values("SELECT VALUE id FROM recording WHERE attached_to.resource = $r", r=rid)) == 3
    # an attachment's resource deleted: the email's file stays, no longer pointing at it
    assert client.delete(f"/api/v1/resources/{made['photo.png']}", headers=ha).status_code == 200
    files = {f["name"]: f for f in client.get(f"/api/v1/resources/{rid}/files", headers=he).json()["files"]}
    assert files["photo.png"]["resource"] is None

    # where attachments shouldn't become resources, they're only kept
    assert client.put("/api/v1/settings/documents", headers=ha, json={"attachment_resources": False}).status_code == 200
    second = _upload(client, he, _email("Second letter", 9), "second.eml")
    jobs.Worker(db, lambda: settings.effective(db, cfg)).drain()
    kept = client.get(f"/api/v1/resources/{second['recording']}/files", headers=he).json()["files"]
    assert len(kept) == 4 and all(f["resource"] is None for f in kept)


def test_documents_the_server_cannot_convert(client, env, db, cfg, monkeypatch):
    he = env["he"]
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    assert client.get("/api/v1/uploads/limits", headers=he).json()["convert"] == {
        "office": False,
        "pages": False,
        "msg": False,
        "web": False,
    }
    r = _start(client, he, b"x", "letter.docx")
    assert (r.status_code, r.json()["detail"]) == (
        400,
        "this Word document can't be read here: converting Word documents needs LibreOffice on the server "
        "(the lens:full image) or an anytopdf conversion node; import it as a transcript instead",
    )
    r = _start(client, he, b"x", "deck.pptx")
    assert r.status_code == 400 and r.json()["detail"].endswith("(the lens:full image) or an anytopdf conversion node")
    assert _start(client, he, b"%PDF", "a.pdf").status_code == 201
    # a source's files: read as transcripts where they can be (an email's text, without its attachments), else left
    assert [sources.file_kind(cfg, n) for n in ("a.docx", "a.txt", "a.pptx", "a.eml", "a.pdf")] == [
        "transcript",
        "transcript",
        None,
        "transcript",
        "document",
    ]
    monkeypatch.setattr(convert, "soffice", lambda cfg: "/usr/bin/soffice")
    assert [sources.file_kind(cfg, n) for n in ("a.docx", "a.txt", "a.pptx", "a.srt")] == ["document", "document", "document", "transcript"]
    assert sources.file_kind(cfg, "a.txt", "transcript") == "transcript" and sources.file_kind(cfg, "a.pptx", "transcript") == "document"

    # a document whose converter has gone by the time it's read fails, saying why
    up = _upload(client, he, b"Notes\n", "later.txt")
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    drain(db, cfg)
    job = client.get(f"/api/v1/jobs/{up['job']}", headers=he).json()
    assert job["status"] == "failed" and "this text file can't be read here: converting text files needs Chromium" in job["error"]


def test_document_conversion_settings(client, env, db, cfg):
    ha = env["ha"]
    put = lambda changes: client.put("/api/v1/settings/documents", headers=ha, json=changes)  # noqa: E731
    assert put({"convert_seconds": 5}).status_code == 400
    assert put({"attachment_resources": "yes"}).status_code == 400
    r = put({"soffice": "/bin/sh"})
    assert r.status_code == 400 and "can't be changed in the app" in r.json()["detail"]
    assert put({"convert_seconds": 60, "attachment_resources": False}).status_code == 200
    got = client.get("/api/v1/settings", headers=ha).json()
    assert got["documents"]["values"]["convert_seconds"] == 60 and "soffice" not in got["documents"]["values"]
    assert set(got["bootstrap"]) >= {"soffice", "chromium"}
