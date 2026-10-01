"""Documents and images as resources (docs/api.md, Documents and images): uploaded like audio and video, their pages
drawn and read by the transcribe step (a PDF's own text, OCR for scans and images), then shown, listed, searched and
summarised by page."""

from __future__ import annotations

import io
import pathlib
import shutil

import pytest

from app.domain import documents, jobs, settings, store, video
from tests import fake_llm
from tests.helpers import drain, login, make_user, scan, text_pdf

R = store.R
POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler-utils")
OCR = pytest.mark.skipif(not shutil.which("tesseract"), reason="needs tesseract")
HARBOUR = [
    ["The harbour report", "Ships arrived at dawn and the\ncargo was counted by the clerk."],
    ["Second page about the lighthouse keeper."],
]


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


def _start(client, h, data, name, ns="pods", **more):
    return client.post("/api/v1/uploads", headers=h, json={"namespace": ns, "filename": name, "size": len(data), **more})


def _upload(client, h, data, name, ns="pods", **more):
    r = _start(client, h, data, name, ns, **more)
    assert r.status_code == 201, r.text
    r = client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    assert r.status_code == 200, r.text
    return r.json()


def _png(img):
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _runs(client, h, job):
    """Each step's outcome and note: {type: (outcome, note)}."""
    j = client.get(f"/api/v1/jobs/{job}", headers=h).json()
    return {s["type"]: (r["outcome"], r.get("note")) for s, r in zip(j["steps"], j["step_runs"])}


@POPPLER
def test_a_pdf_becomes_a_document_with_its_pages_and_text(client, env, db, cfg):
    he, hv = env["he"], env["hv"]
    assert _start(client, hv, b"%PDF", "harbour.pdf").status_code == 403  # viewers don't upload
    up = _upload(client, he, text_pdf(HARBOUR), "harbour.pdf", title="Harbour report")
    rid = up["recording"]
    rec = db.one("SELECT source, media, status FROM $r", r=R("recording", rid))
    assert rec == {"source": "document", "media": {"kind": "document"}, "status": "new"}
    drain(db, cfg)

    rec = db.one("SELECT status, engine, media, duration_ms, summary FROM $r", r=R("recording", rid))
    assert (rec["status"], rec["engine"], rec.get("duration_ms")) == ("analyzed", "pdf", None)
    assert rec["media"]["kind"] == "document" and rec["media"]["pages"] == 2 and rec["media"]["height"] == 2000
    runs = _runs(client, he, up["job"])
    assert runs["transcribe"] == ("done", "2 page(s), 3 block(s) of text")
    assert runs["diarize"] == ("skipped", "a document has no voices")
    assert runs["shots"] == ("skipped", "it isn't a video")
    assert runs["ocr"] == ("skipped", "its pages were read when it was transcribed")
    # summaries cite pages: the first point is on page 1, the follow-up on page 2
    assert rec["summary"]["key_points"][0]["page"] == 0 and rec["summary"]["action_items"][0]["page"] == 1

    # the viewer's data: pages with signed links to their images, and blocks of text with their page and place
    p = client.get(f"/api/v1/resources/{rid}/player", headers=hv).json()
    assert p["media"] == {"kind": "document", "pages": 2, "width": rec["media"]["width"], "height": 2000}
    assert [(x["idx"], x["text"], x["height"]) for x in p["pages"]] == [(0, "pdf", 2000), (1, "pdf", 2000)]
    assert p["pages"][0]["chars"] > 40 and p["poster"] == p["pages"][0]["thumb"]
    assert [(s["text"], s["p"]) for s in p["segments"]] == [
        ("The harbour report", 0),
        ("Ships arrived at dawn and the cargo was counted by the clerk.", 0),
        ("Second page about the lighthouse keeper.", 1),
    ]
    x, y, w, h = p["segments"][1]["b"]
    assert 0.1 < x < 0.15 and 0 < y < 0.2 and 0.2 < w < 0.6 and 0 < h < 0.1
    img = client.get(p["pages"][1]["image"])  # a signed link: no token needed
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get(p["pages"][1]["thumb"]).status_code == 200
    assert client.get(f"/api/v1/resources/{rid}/frames/page-0002.jpg").status_code == 401  # not without one

    # its own file is a download that browsers don't run
    files = client.get(f"/api/v1/resources/{rid}/files", headers=hv).json()
    assert {k: files["primary"][k] for k in ("name", "kind", "content_type")} == {
        "name": "harbour.pdf",
        "kind": "document",
        "content_type": "application/pdf",
    }
    got = client.get(files["primary"]["download"])
    assert got.status_code == 200 and got.content.startswith(b"%PDF") and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("attachment") and "sandbox" in got.headers["content-security-policy"]

    # the Library lists it as a document with its pages; search finds its text on its page
    rows = client.get("/api/v1/resources?media=document", headers=hv).json()
    assert [(r["id"], r["media_kind"], r["pages"]) for r in rows] == [(rid, "document", 2)] and rows[0]["poster"]
    assert client.get("/api/v1/resources?media=transcript", headers=hv).json() == []
    assert client.get("/api/v1/resources?media=audio", headers=hv).json() == []
    hits = client.get("/api/v1/search?q=lighthouse", headers=hv).json()["hits"]
    assert [(h["recording_id"], h["source"], h["page"]) for h in hits] == [(rid, "page", 1)] and len(hits[0]["box"]) == 4

    # deleted (by an owner): its pages go with it
    assert client.delete(f"/api/v1/resources/{rid}", headers=env["ha"]).status_code == 200
    assert db.rows("SELECT * FROM page WHERE recording = $r", r=rid) == []
    assert not video.frames_dir(cfg, rid).exists()


@POPPLER
@OCR
def test_scans_and_images_are_read_by_ocr(client, env, db, cfg, folder):
    he = env["he"]
    page = scan(["Letter from the harbour master", "The lighthouse keeper wrote twice.", "Signed in Galway, 1942"])
    pdf = folder / "scan.pdf"
    page.save(pdf)
    scanned = _upload(client, he, pdf.read_bytes(), "scan.pdf")["recording"]
    photo = _upload(client, he, _png(scan(["Galway harbour", "Lighthouse keeper"], mode="RGBA")), "photo.png")["recording"]
    tiff = folder / "two.tif"
    scan(["First sheet"]).save(tiff, save_all=True, append_images=[scan(["Second sheet"])])
    sheets = _upload(client, he, tiff.read_bytes(), "two.tif")["recording"]
    drain(db, cfg)

    rec = db.one("SELECT source, engine, media FROM $r", r=R("recording", scanned))
    assert (rec["source"], rec["engine"], rec["media"]["pages"]) == ("document", "pdf+ocr:tesseract", 1)
    text = " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=scanned))
    assert "lighthouse keeper" in text and "Galway" in text
    assert db.values("SELECT VALUE text FROM page WHERE recording = $r", r=scanned) == ["ocr"]

    rec = db.one("SELECT source, engine, media, status FROM $r", r=R("recording", photo))
    assert (rec["source"], rec["engine"], rec["status"]) == ("image", "image+ocr:tesseract", "analyzed")
    assert rec["media"] == {"kind": "image", "pages": 1, "width": 1400, "height": 1000}
    assert "Galway harbour" in " ".join(db.values("SELECT VALUE text FROM segment WHERE recording = $r", r=photo))
    p = client.get(f"/api/v1/resources/{photo}/player", headers=env["hv"]).json()
    assert p["media"]["kind"] == "image" and all(len(s["b"]) == 4 for s in p["segments"])
    rows = client.get("/api/v1/resources?media=image", headers=he).json()
    assert sorted(r["id"] for r in rows) == sorted([photo, sheets])

    # a TIFF's frames are its pages
    assert [(x["idx"], x["image"]) for x in documents.pages(db, sheets)] == [(0, "page-0001.jpg"), (1, "page-0002.jpg")]
    by_page = db.rows("SELECT idx, page, text FROM segment WHERE recording = $r ORDER BY idx", r=sheets)
    assert [(s["page"], s["text"]) for s in by_page] == [(0, "First sheet"), (1, "Second sheet")]


def test_without_poppler_or_an_ocr_engine(client, env, db, cfg, monkeypatch):
    """Without poppler a PDF is read by pypdf, with no pages to look at; without an OCR engine an image has no text."""
    monkeypatch.setattr(documents, "_poppler", lambda: (None, None))
    monkeypatch.setattr(video, "ocr_engine", lambda _cfg: None)
    he = env["he"]
    doc = _upload(client, he, text_pdf(HARBOUR), "harbour.pdf")
    img = _upload(client, he, _png(scan(["Unread words"])), "photo.png")
    drain(db, cfg)

    pages = documents.pages(db, doc["recording"])
    assert [(x["idx"], x.get("image"), x["text"]) for x in pages] == [(0, None, "pdf"), (1, None, "pdf")]
    segs = db.rows("SELECT idx, page, text, box FROM segment WHERE recording = $r ORDER BY idx", r=doc["recording"])
    assert [s["page"] for s in segs] == [0, 1] and "lighthouse keeper" in segs[1]["text"] and not segs[0].get("box")
    log = client.get(f"/api/v1/jobs/{doc['job']}/log", headers=he).json()["lines"]
    assert any("its pages couldn't be drawn: poppler-utils (pdftoppm) isn't installed" in x for x in log)

    assert [x["image"] for x in documents.pages(db, img["recording"])] == ["page-0001.jpg"]
    assert db.rows("SELECT * FROM segment WHERE recording = $r", r=img["recording"]) == []
    runs = _runs(client, he, img["job"])
    assert runs["transcribe"] == ("done", "1 page(s), 0 block(s) of text")
    assert runs["summarize"] == ("skipped", "there's no text to summarise")
    log = client.get(f"/api/v1/jobs/{img['job']}/log", headers=he).json()["lines"]
    assert any("its text wasn't read: no OCR engine is available" in x for x in log)


@POPPLER
def test_document_settings_and_what_can_be_uploaded(client, env, db, cfg):
    ha, he = env["ha"], env["he"]
    put = lambda section, changes: client.put(f"/api/v1/settings/{section}", headers=ha, json=changes)  # noqa: E731
    assert put("documents", {"max_pages": 0}).status_code == 400
    assert put("documents", {"page_pixels": 100}).status_code == 400
    assert put("documents", {"thumb_pixels": "big"}).status_code == 400
    assert put("documents", {"dpi": 300}).status_code == 400
    assert put("documents", {"max_pages": 1, "page_pixels": 1000}).status_code == 200
    up = _upload(client, he, text_pdf(HARBOUR), "harbour.pdf")
    jobs.Worker(db, lambda: settings.effective(db, cfg)).drain()  # a worker reads the settings saved in the app
    assert [(x["idx"], x["height"]) for x in documents.pages(db, up["recording"])] == [(0, 1000)]
    log = client.get(f"/api/v1/jobs/{up['job']}/log", headers=he).json()["lines"]
    assert any("only the first 1 of its 2 pages were read (documents.max_pages)" in x for x in log)

    # a broken PDF fails its job, saying why
    bad = _upload(client, he, b"%PDF-1.4 not really a pdf", "broken.pdf")
    jobs.Worker(db, lambda: settings.effective(db, cfg)).drain()
    job = client.get(f"/api/v1/jobs/{bad['job']}", headers=he).json()
    assert job["status"] == "failed" and "this PDF can't be read" in job["error"]

    # only audio and video are attached to a transcript
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"space": store.ns_id(db, "pods"), "source": "transcript", "title": "T"})
    r = _start(client, he, b"%PDF", "notes.pdf", recording=rid)
    assert (r.status_code, r.json()["detail"]) == (400, "only audio or video can be attached to a transcript")

    # documents and images are among the types uploads take, unless an admin leaves them out
    assert {".pdf", ".png", ".tif"} <= set(client.get("/api/v1/uploads/limits", headers=he).json()["extensions"])
    assert put("uploads", {"extensions": [".wav", ".svg"]}).status_code == 400
    assert put("uploads", {"extensions": [".wav"]}).status_code == 200
    r = _start(client, he, b"%PDF", "notes.pdf")
    assert r.status_code == 400 and r.json()["detail"].startswith("PDF files can't be uploaded here")
