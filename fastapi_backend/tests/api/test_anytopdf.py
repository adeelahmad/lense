"""anytopdf as a converter (anytopdf.py): run here or on a conversion node, it makes the PDF of what Lens can't
convert itself (documents.converter auto), or of every document and image (anytopdf), and Lens reads that PDF like any
other; with `lens` it's never used."""

from __future__ import annotations

import os
import pathlib
import shutil
import tarfile

import pytest

from app.domain import anytopdf, components, convert, settings, store
from tests import fake_anytopdf
from tests.api.test_conversion import _email, _text
from tests.api.test_documents import _png, _start, _upload
from tests.helpers import drain, login, make_user, scan, write_docx

R = store.R
REAL = os.environ.get("LENS_TEST_ANYTOPDF") or shutil.which("anytopdf")
TOKEN = "node token 0123456789"


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"ha": login(client, "root@x.io", "root password 1"), "he": login(client, "ed@x.io", "editor password 1")}


@pytest.fixture
def lean(monkeypatch):
    """A server with neither LibreOffice nor Chromium (the lean image)."""
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)


@pytest.fixture
def here(cfg, tmp_path):
    """The fake anytopdf, as the program to run here; its runs are logged."""
    log = tmp_path / "anytopdf.log"
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "anytopdf", log)
    return log


def _rec(db, rid):
    return db.one("SELECT source, status, rendition, engine, error, title FROM $r", r=R("recording", rid))


def test_where_lens_cannot_convert_anytopdf_does(lean, here, client, env, db, cfg):
    he = env["he"]
    assert client.get("/api/v1/uploads/limits", headers=he).json()["convert"] == {
        "office": False,  # here, anytopdf needs LibreOffice for those too
        "pages": True,
        "msg": False,
        "web": False,
    }
    txt = _upload(client, he, b"Tide notes\n\nHigh water at noon.\n", "notes.txt")
    mail = _upload(client, he, _email("Harbour report", 9), "mail.eml")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, txt["recording"])
    assert (rec["status"], rec["rendition"]) == ("analyzed", {"from": ".txt", "by": "anytopdf"}), rec.get("error")
    assert "High water at noon." in _text(db, txt["recording"])
    log = client.get(f"/api/v1/jobs/{txt['job']}/log", headers=he).json()["lines"]
    assert any("made into a PDF by anytopdf" in x for x in log)

    # an email: Lens still reads who sent it and keeps its attachments; anytopdf reads the page Lens made of it
    rec = _rec(db, mail["recording"])
    assert (rec["status"], rec["title"], rec["rendition"]["by"]) == ("analyzed", "Harbour report", "anytopdf"), rec.get("error")
    assert "The lighthouse keeper wrote twice." in _text(db, mail["recording"])
    files = client.get(f"/api/v1/resources/{mail['recording']}/files", headers=he).json()["files"]
    assert {f["name"] for f in files} == {"harbour.pdf", "photo.png", "data.xyz", "Re harbour.eml"}

    # run offline, without plugins, a config file or the server's ANYTOPDF_ variables, and with no provenance page
    runs = fake_anytopdf.runs(here)
    assert runs and all(r["proxy"] == "http://127.0.0.1:9" and r["anytopdf_env"] == ["ANYTOPDF_DATA_DIR"] for r in runs)
    assert all(r["args"][:3] == ["--no-plugins", "--no-config", "convert"] for r in runs)
    assert all({"--no-provenance-page", "--no-entities"} <= set(r["args"]) for r in runs)
    assert all(r["args"][3].endswith((".html", ".htm")) for r in runs)  # the page Lens made: sanitized, nothing to fetch

    # Office files still need LibreOffice here
    r = _start(client, he, b"x", "letter.docx")
    assert r.status_code == 400 and "or an anytopdf conversion node" in r.json()["detail"]


def test_with_lens_chosen_anytopdf_is_never_used(lean, here, client, env, db, cfg):
    he, ha = env["he"], env["ha"]
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "lens"}).status_code == 200
    assert client.get("/api/v1/uploads/limits", headers=he).json()["convert"]["pages"] is False
    assert _start(client, he, b"x", "notes.txt").status_code == 400
    assert fake_anytopdf.runs(here) == []


def test_a_conversion_node_converts_office_files_too(lean, client, env, db, cfg, folder):
    he, ha = env["he"], env["ha"]
    srv, url = fake_anytopdf.node(TOKEN)
    try:
        r = client.put("/api/v1/settings/documents", headers=ha, json={"anytopdf_url": url + "/", "anytopdf_token": TOKEN})
        assert r.status_code == 200, r.text
        got = client.get("/api/v1/settings", headers=ha).json()["documents"]["values"]
        assert got["anytopdf_url"] == url and got["anytopdf_token"] == {"secret": True, "set": True}
        assert client.get("/api/v1/uploads/limits", headers=he).json()["convert"]["office"] is True

        write_docx(folder / "letter.docx", ["The harbour report", "Ships arrived at dawn."])
        doc = _upload(client, he, (folder / "letter.docx").read_bytes(), "letter.docx")
        bad = _upload(client, he, b"unreadable\n", "bad.txt")
        drain(db, settings.effective(db, cfg))
        rec = _rec(db, doc["recording"])
        assert (rec["status"], rec["rendition"]) == ("analyzed", {"from": ".docx", "by": "anytopdf"}), rec.get("error")
        sent = sorted(j["name"] for j in fake_anytopdf.Node.jobs.values())
        assert sent == ["document.docx", "document.html"]  # names aren't sent, only kinds; text goes as the page Lens made
        job = client.get(f"/api/v1/jobs/{bad['job']}", headers=he).json()
        assert job["status"] == "failed" and "the conversion node couldn't read it" in job["error"]

        # a wrong token: said so
        assert client.put("/api/v1/settings/documents", headers=ha, json={"anytopdf_token": "another token 0123456789"}).status_code == 200
        again = _upload(client, he, b"Notes\n", "again.txt")
        drain(db, settings.effective(db, cfg))
        job = client.get(f"/api/v1/jobs/{again['job']}", headers=he).json()
        assert job["status"] == "failed" and "refused Lens's token" in job["error"]
    finally:
        srv.shutdown()
        srv.server_close()
    # gone: said so too
    again = _upload(client, he, b"Other notes\n", "gone.txt")
    drain(db, settings.effective(db, cfg))
    job = client.get(f"/api/v1/jobs/{again['job']}", headers=he).json()
    assert job["status"] == "failed" and "can't be reached" in job["error"]


def test_check_it_says_whether_the_node_answers_and_takes_the_token(client, env):
    ha = env["ha"]
    check = lambda: client.post("/api/v1/settings/documents/node/test", headers=ha).json()  # noqa: E731
    assert not check()["ok"] and "set the conversion node's address" in check()["error"]
    srv, url = fake_anytopdf.node(TOKEN)
    try:
        assert client.put("/api/v1/settings/documents", headers=ha, json={"anytopdf_url": url, "anytopdf_token": TOKEN}).status_code == 200
        got = check()
        assert got["ok"] and got["error"] is None and got["ms"] >= 0, got
        assert client.put("/api/v1/settings/documents", headers=ha, json={"anytopdf_token": "another token 0123456789"}).status_code == 200
        got = check()
        assert not got["ok"] and "refused Lens's token" in got["error"]
    finally:
        srv.shutdown()
        srv.server_close()
    got = check()
    assert not got["ok"] and "can't be reached" in got["error"]


class _Hostile(fake_anytopdf.Node):
    """A conversion node that misbehaves the way `mode` says."""

    mode = ""

    def do_POST(self):  # noqa: N802
        if _Hostile.mode in ("redirect", "long", "odd id"):  # the upload read first, or Lens may see a reset instead
            self.rfile.read(int(self.headers["Content-Length"]))
        if _Hostile.mode == "redirect":
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{_Hostile.elsewhere}/v1/jobs")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if _Hostile.mode == "long":
            return self._send(202, b"{" + b" " * (anytopdf.MAX_JSON + 10) + b"}")
        if _Hostile.mode == "odd id":
            return self._send(202, {"job_id": "../../admin", "state": "queued"})
        super().do_POST()

    def do_GET(self):  # noqa: N802
        if _Hostile.mode == "not a pdf" and self.path.endswith("/output") and self._authorized():
            return self._send(200, b"<html>not a PDF</html>", "application/pdf")
        super().do_GET()


def test_a_misbehaving_node_gets_nothing_more_and_gives_nothing_unchecked(cfg, tmp_path):
    import http.server
    import threading

    seen = []

    class Elsewhere(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.send_response(500)
            self.end_headers()

        def log_message(self, *a):
            pass

    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    srv, url = fake_anytopdf.node(TOKEN)
    srv.RequestHandlerClass = _Hostile
    _Hostile.elsewhere = other.server_address[1]
    cfg["documents"].update(anytopdf_url=url, anytopdf_token=TOKEN, convert_seconds=10)
    src = tmp_path / "notes.txt"
    src.write_text("Notes\n")
    out = tmp_path / "out.pdf"
    try:
        for mode, said in (
            ("redirect", "doesn't follow"),
            ("long", "answer was too long"),
            ("odd id", "didn't answer like anytopdf queue serve"),
            ("not a pdf", "isn't a PDF"),
        ):
            _Hostile.mode = mode
            with pytest.raises(ValueError, match=said):
                anytopdf.to_pdf(cfg, src, out)
            assert not out.exists() and not out.with_suffix(".part").exists()
        assert seen == []  # the token never went on to where the node pointed
        _Hostile.mode = ""
        anytopdf.to_pdf(cfg, src, out)
        assert out.read_bytes().startswith(b"%PDF-")
        cfg["documents"]["anytopdf_url"] = url.replace("http://", "http://user:pw@")
        with pytest.raises(ValueError, match="http\\(s\\) address"):
            anytopdf.to_pdf(cfg, src, out)
        cfg["documents"]["anytopdf_url"] = "file:///etc"
        assert "http(s) address" in anytopdf.check(cfg)
    finally:
        for s in (srv, other):
            s.shutdown()
            s.server_close()


def test_with_anytopdf_chosen_it_reads_images_too(here, client, env, db, cfg, folder):
    he, ha = env["he"], env["ha"]
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "anytopdf"}).status_code == 200
    up = _upload(client, he, _png(scan(["Galway harbour"])), "receipt.png")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, up["recording"])
    assert (rec["source"], rec["status"], rec["rendition"]) == ("image", "analyzed", {"from": ".png", "by": "anytopdf"}), rec.get("error")
    assert rec["engine"] == "image+ocr:anytopdf" and "Photographed page document" in _text(db, up["recording"])
    # its words, with where anytopdf's OCR found them, are its text: not read again
    segs = db.rows("SELECT text, page, box FROM segment WHERE recording = $r", r=up["recording"])
    assert segs == [{"text": "Photographed page document", "page": 0, "box": [0.1, 0.1, 0.55, 0.02]}]
    assert db.one("SELECT VALUE text FROM page WHERE recording = $r", r=up["recording"]) == "ocr"
    assert "--scan-mode" in fake_anytopdf.runs(here)[-1]["args"]
    assert convert.rendition_path(cfg, up["recording"]).is_file()

    # where anytopdf fails on one, Lens reads it itself, as before
    broken = folder / "broken-anytopdf"
    broken.write_text("#!/bin/sh\necho 'error: it broke' >&2\nexit 1\n")
    broken.chmod(0o755)
    cfg["documents"]["anytopdf"] = str(broken)
    other = _upload(client, he, _png(scan(["Second page"])), "other.png")
    drain(db, settings.effective(db, cfg))
    rec = _rec(db, other["recording"])
    assert (rec["status"], rec["rendition"]) == ("analyzed", None), rec.get("error")
    log = client.get(f"/api/v1/jobs/{other['job']}/log", headers=he).json()["lines"]
    assert any("read without anytopdf: anytopdf couldn't read it (error: it broke)" in x for x in log), log


def test_with_a_browser_and_poppler_here_it_draws_web_pages(here, client, env, db, cfg, monkeypatch):
    from app.domain import documents

    he, ha = env["he"], env["ha"]
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "anytopdf"}).status_code == 200
    monkeypatch.setattr(convert, "chromium", lambda cfg: "/opt/browser/chrome")
    monkeypatch.setattr(documents, "_poppler", lambda: ("/usr/bin/pdftoppm", "/usr/bin/pdftotext"))
    page = _upload(client, he, b"<html><title>Tides</title><body><h1>High water</h1></body></html>", "tides.html")
    mail = _upload(client, he, _email("Harbour report", 9), "mail.eml")
    drain(db, settings.effective(db, cfg))
    for up in (page, mail):
        assert _rec(db, up["recording"])["rendition"]["by"] == "anytopdf"
    runs = [r for r in fake_anytopdf.runs(here) if r["args"][3].endswith(".html")]  # pages, emails and an attached email
    assert len(runs) >= 2 and all("--html-render" in r["args"] for r in runs)
    assert all(r["anytopdf_env"] == ["ANYTOPDF_CHROME", "ANYTOPDF_DATA_DIR"] for r in runs)

    # without pdftoppm (a small server), it reads the page's text, as before
    monkeypatch.setattr(documents, "_poppler", lambda: (None, None))
    again = _upload(client, he, b"<html><body><p>Low water</p></body></html>", "low.html")
    drain(db, settings.effective(db, cfg))
    assert _rec(db, again["recording"])["rendition"]["by"] == "anytopdf"
    last = fake_anytopdf.runs(here)[-1]
    assert "--html-render" not in last["args"] and last["anytopdf_env"] == ["ANYTOPDF_DATA_DIR"]


def test_anytopdf_settings(client, env):
    ha = env["ha"]
    put = lambda changes: client.put("/api/v1/settings/documents", headers=ha, json=changes)  # noqa: E731
    assert put({"converter": "pandoc"}).status_code == 400
    assert put({"anytopdf_url": "ftp://x"}).status_code == 400
    assert put({"anytopdf_token": "short"}).status_code == 400
    r = put({"anytopdf": "/bin/sh"})
    assert r.status_code == 400 and "can't be changed in the app" in r.json()["detail"]
    assert put({"converter": "anytopdf", "anytopdf_url": "", "anytopdf_token": None}).status_code == 200
    got = client.get("/api/v1/settings", headers=ha).json()
    assert got["documents"]["values"]["converter"] == "anytopdf" and "anytopdf" in got["bootstrap"]


def test_it_is_fetched_when_asked_for(cfg, monkeypatch, tmp_path):
    comp = components.BY_ID["anytopdf"]
    assert not comp.needed(cfg, {})  # auto: only used when it's here
    cfg["documents"]["converter"] = "anytopdf"
    assert comp.needed(cfg, {}) == bool(anytopdf.archive())
    cfg["documents"]["anytopdf_url"] = "http://node:8640"
    assert not comp.needed(cfg, {})  # a node does the work

    # the release archive for this machine, checked against its checksum; only the program is kept
    assert anytopdf.archive("Linux", "aarch64")[0] == "anytopdf-0.4.0-aarch64-unknown-linux-musl.tar.gz"
    assert anytopdf.archive("Darwin", "arm64")[0] == "anytopdf-0.4.0-aarch64-apple-darwin.tar.gz"
    assert anytopdf.archive("Windows", "AMD64") is None
    name = "anytopdf-0.4.0-x86_64-unknown-linux-musl"
    (tmp_path / name / "plugins").mkdir(parents=True)
    (tmp_path / name / "anytopdf").write_bytes(b"#!/bin/sh\necho anytopdf 0.4.0\n")
    (tmp_path / name / "plugins" / "anytopdf-plugin-faces").write_bytes(b"x")
    (tmp_path / name / "plugins" / "anytopdf-plugin-sentiment").write_bytes(b"x")  # not one Lens keeps
    with tarfile.open(tmp_path / f"{name}.tar.gz", "w:gz") as t:
        t.add(tmp_path / name, arcname=name)
    raw = (tmp_path / f"{name}.tar.gz").read_bytes()
    import hashlib
    import io

    monkeypatch.setattr(anytopdf, "archive", lambda: (f"{name}.tar.gz", hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(anytopdf.urllib.request, "urlopen", lambda url, timeout=None: io.BytesIO(raw))
    got = anytopdf.fetch(cfg)
    assert got == str(anytopdf.fetched_path(cfg)) and os.access(got, os.X_OK)
    kept = sorted(str(p.relative_to(anytopdf.fetched_path(cfg).parent)) for p in anytopdf.fetched_path(cfg).parent.rglob("*"))
    assert kept == ["anytopdf", "plugins", "plugins/anytopdf-plugin-faces"]  # the program and its face and object plugins
    assert comp.present({**cfg, "documents": {**cfg["documents"], "anytopdf": None}})

    monkeypatch.setattr(anytopdf, "archive", lambda: (f"{name}.tar.gz", "0" * 64))
    assert anytopdf.fetch(cfg) == got  # already here
    shutil.rmtree(anytopdf.fetched_path(cfg).parent / "plugins")
    with pytest.raises(RuntimeError, match="checksum"):  # fetched before plugins were kept: fetched again
        anytopdf.fetch(cfg)
    anytopdf.fetched_path(cfg).unlink()
    with pytest.raises(RuntimeError, match="checksum"):
        anytopdf.fetch(cfg)
    assert not anytopdf.fetched_path(cfg).exists()


def test_its_plugins_look_at_a_picture(cfg, tmp_path):
    log = tmp_path / "runs.jsonl"
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "anytopdf", log)
    pic = tmp_path / "bus.jpg"
    pic.write_bytes(b"a picture")
    model = tmp_path / "yolox_s.onnx"
    model.write_bytes(b"a model")
    assert anytopdf.analyze(cfg, pic) == [
        {"kind": "face", "label": "face", "box": [0.1, 0.2, 0.05, 0.06], "score": 0.9, "attributes": {"face_index": "0"}}
    ]  # faces are found whatever is given; their summary is left out
    found = anytopdf.analyze(cfg, pic, {"ANYTOPDF_OBJECTS_MODEL": model, "HOME": "/elsewhere"})
    assert [(f["kind"], f["label"], f["box"]) for f in found] == [
        ("face", "face", [0.1, 0.2, 0.05, 0.06]),
        ("object", "bus", [0.02, 0.2, 0.9, 0.5]),
    ]
    run = fake_anytopdf.runs(log)[-1]
    args = run["args"]
    assert args[args.index("--plugin-sandbox") + 1] == "strict" and args[args.index("--plugin-sandbox-allow-read") + 1] == str(model)
    assert "--no-plugins" not in args and "--no-config" in args and args[args.index("--ocr") + 1] == "off"
    assert run["anytopdf_env"] == ["ANYTOPDF_DATA_DIR", "ANYTOPDF_OBJECTS_MODEL"] and run["proxy"]  # only what it's given, no network

    pic.write_bytes(b"unreadable")
    with pytest.raises(ValueError, match="anytopdf couldn't look at it"):
        anytopdf.analyze(cfg, pic)
    cfg["documents"]["anytopdf"] = str(tmp_path / "gone")
    with pytest.raises(anytopdf.Unavailable):
        anytopdf.analyze(cfg, pic)


def _with_face_plugins(cfg, tmp_path):
    """The fake program with the face plugins beside it, as a release archive has them, and an SFace file."""
    log = tmp_path / "runs.jsonl"
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "anytopdf", log)
    assert not anytopdf.face_plugins(cfg)
    (tmp_path / "plugins").mkdir()
    for name in anytopdf.FACE_PLUGINS:
        (tmp_path / "plugins" / name).write_text("#!/bin/sh\n")
        (tmp_path / "plugins" / name).chmod(0o755)
    assert anytopdf.face_plugins(cfg)
    model = tmp_path / "face_recognition_sface_2021dec.onnx"
    model.write_bytes(b"a model")
    return log, model


def test_its_plugins_describe_faces(cfg, tmp_path):
    import numpy as np

    log, model = _with_face_plugins(cfg, tmp_path)
    pics = [tmp_path / n for n in ("a.jpg", "b.jpg", "empty.jpg")]
    for pic, raw in zip(pics, (b"a picture", b"the same person", b"nobody here"), strict=True):
        pic.write_bytes(raw)
    found = anytopdf.faces(cfg, [str(p) for p in pics], model)
    assert list(found) == [str(p) for p in pics] and found[str(pics[2])] == []  # by picture, in one run
    a, b = found[str(pics[0])][0], found[str(pics[1])][0]
    assert (a["box"], a["score"]) == ([0.1, 0.2, 0.05, 0.06], 0.9)
    assert abs(np.linalg.norm(a["embedding"]) - 1) < 1e-6 and a["embedding"] @ b["embedding"] > 0.99
    (run,) = fake_anytopdf.runs(log)
    args = run["args"]
    assert args[args.index("--plugin-sandbox") + 1] == "strict" and args[args.index("--plugin-sandbox-allow-read") + 1] == str(model)
    assert "--recognize-faces" in args and run["anytopdf_env"] == ["ANYTOPDF_DATA_DIR", "ANYTOPDF_FACE_EMBED_MODEL"]
    assert not any(pathlib.Path(x).name in ("a.jpg", "b.jpg") for x in args)  # copies: no names of Lens's files

    # a face in the graph that the index has no description of isn't one Lens can keep
    graph = {
        "sources": [{"id": "s", "path": "/p.jpg"}],
        "units": [{"source_id": "s", "annotations": [{"kind": "face", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}]}],
    }
    assert anytopdf.faces_found(graph, tmp_path / "none.sqlite") == {}


def test_anytopdf_as_the_face_engine(cfg, tmp_path):
    from app.domain import video

    cfg["video"]["face_engine"] = "anytopdf"
    cfg["documents"]["anytopdf"] = str(tmp_path / "gone")
    assert video.face_engine(cfg) is None  # not here: the faces step says there's no face engine
    log, model = _with_face_plugins(cfg, tmp_path)
    cfg["video"]["sface_model"] = str(model)
    engine = video.face_engine(cfg)
    assert engine.name == "anytopdf"
    pic = tmp_path / "frame.jpg"
    pic.write_bytes(b"a frame")
    assert [f["box"] for f in engine.faces(pic)] == [[0.1, 0.2, 0.05, 0.06]]

    # fetched for it: anytopdf, and the SFace file only (it finds faces itself, so no YuNet or OpenCV)
    del cfg["video"]["sface_model"]
    assert components.BY_ID["anytopdf"].needed(cfg, {}) == bool(anytopdf.archive())
    faces = components.BY_ID["faces"]
    assert faces.needed(cfg, {}) and faces._files(cfg) == components.FACE_FILES[1:]


@pytest.fixture
def real(cfg):
    cfg["documents"]["anytopdf"] = REAL


@pytest.mark.skipif(not REAL, reason="needs the anytopdf program (LENS_TEST_ANYTOPDF or on PATH)")
def test_the_real_anytopdf(real, lean, client, env, db, cfg):
    he, ha = env["he"], env["ha"]
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "anytopdf"}).status_code == 200
    mail = _upload(client, he, _email("Harbour report", 9, attachments=False), "mail.eml")
    page = _upload(client, he, _png(scan(["Galway harbour", "Ships at dawn"])), "page.png")
    drain(db, settings.effective(db, cfg))
    for up, words in ((mail, "The lighthouse keeper wrote twice."), (page, "Galway harbour")):
        rec = _rec(db, up["recording"])
        assert (rec["status"], rec["rendition"]["by"]) == ("analyzed", "anytopdf"), rec.get("error")
        assert words in _text(db, up["recording"]), _text(db, up["recording"])
    # the photographed page: its text from the words anytopdf's OCR found, each block with where it is
    assert _rec(db, page["recording"])["engine"] == "image+ocr:anytopdf"
    assert all(s["box"] for s in db.rows("SELECT box FROM segment WHERE recording = $r", r=page["recording"]))
    pic = pathlib.Path(cfg["data_dir"]) / "page.png"
    pic.write_bytes(_png(scan(["Galway harbour"])))
    assert anytopdf.analyze(cfg, pic) == []  # its plugins ran, sandboxed, and found no face on a page of text
