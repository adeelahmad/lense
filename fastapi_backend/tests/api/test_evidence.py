"""Evidence PDFs (evidence.py): a recording or a collection as one PDF made by anytopdf, each resource's own file (audio
and video with Lens's transcript) then a provenance page with every file's SHA-256, made here or on a conversion node."""

from __future__ import annotations

import hashlib
import io
import pathlib
import subprocess

import pytest

from app.domain import anytopdf, evidence, ingest, settings, store
from tests import fake_anytopdf
from tests.api.test_anytopdf import TOKEN
from tests.api.test_documents import _upload
from tests.helpers import drain, login, make_user, quiet, write_wav

R = store.R


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "ho": login(client, "out@x.io", "outsider password 1"),
    }


@pytest.fixture
def here(cfg, tmp_path):
    log = tmp_path / "anytopdf.log"
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "anytopdf", log)
    return log


def _text(data):
    import pypdf

    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def _call(db, cfg, folder):
    """An audio recording in pods with a transcript of two lines."""
    t = folder / "call.txt"
    t.write_text("[00:00:01] Alice: The invoice is paid.\n[00:00:04] Bob: Thanks, filed it.\n")
    rid = ingest.import_transcript(db, cfg, "pods", t, log=quiet)
    wav = folder / "call.wav"
    write_wav(wav)
    db.q("UPDATE $r SET source = 'audio', path = $p, title = 'Supplier call'", r=R("recording", rid), p=str(wav))
    return rid, wav


def test_a_document_and_a_call_as_evidence(here, client, env, db, cfg, folder):
    he = env["he"]
    raw = b"Invoice 42\n\nTotal: 100 EUR\n"
    doc = _upload(client, he, raw, "invoice.txt")
    drain(db, settings.effective(db, cfg))
    r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="invoice-evidence.pdf"'
    text = _text(r.content)
    assert "Total: 100 EUR" in text and "Provenance" in text and hashlib.sha256(raw).hexdigest() in text

    # run like every conversion here (offline, no plugins or config), but with the provenance page and no paths
    run = fake_anytopdf.runs(here)[-1]
    assert run["proxy"] == "http://127.0.0.1:9" and run["anytopdf_env"] == ["ANYTOPDF_DATA_DIR"]
    assert run["args"][:3] == ["--no-plugins", "--no-config", "convert"]
    assert "--no-provenance-page" not in run["args"] and run["args"][run["args"].index("--profile") + 1] == "share"
    assert run["args"][3].endswith("01 invoice.txt")  # its place and its title

    # audio goes in as itself, with Lens's transcript beside it
    rid, wav = _call(db, cfg, folder)
    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he)
    assert r.status_code == 200, r.text
    text = _text(r.content)
    assert "Audio source: 01 Supplier call.wav" in text and "Alice: The invoice is paid." in text
    assert hashlib.sha256(wav.read_bytes()).hexdigest() in text
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert args[args.index("--transcript") + 1].endswith("transcripts/01 Supplier call.srt")

    # nothing is left behind
    assert not list((pathlib.Path(cfg["data_dir"]) / "tmp").glob("evidence-*"))


def test_a_collection_as_one_evidence_pdf(here, client, env, db, cfg, folder):
    he, ho = env["he"], env["ho"]
    a = _upload(client, he, b"First memo\n", "memo-a.txt")
    b = _upload(client, he, b"Second memo\n", "memo-b.txt")
    drain(db, settings.effective(db, cfg))
    rid, _ = _call(db, cfg, folder)
    t = folder / "plain.txt"
    t.write_text("[00:00:01] Carol: Only words here.\n")
    words = ingest.import_transcript(db, cfg, "pods", t, log=quiet)  # no media: its text goes in
    order = [b["recording"], rid, words, a["recording"]]
    r = client.post("/api/v1/collections", headers=he, json={"name": "Supplier dispute", "recordings": order})
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]

    r = client.get(f"/api/v1/collections/{cid}/evidence.pdf", headers=he)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == 'attachment; filename="supplier-dispute-evidence.pdf"'
    text = _text(r.content)
    places = [text.index(x) for x in ("Second memo", "Alice: The invoice is paid.", "Carol: Only words here.", "First memo")]
    assert places == sorted(places)  # the list's order
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert [x.rsplit("/", 1)[-1] for x in args[3:7]] == ["01 memo-b.txt", "02 Supplier call.wav", "03 plain.txt", "04 memo-a.txt"]

    # someone who can't read pods can't have it
    assert client.get(f"/api/v1/collections/{cid}/evidence.pdf", headers=ho).status_code == 404
    assert client.get(f"/api/v1/recordings/{a['recording']}/evidence.pdf", headers=ho).status_code in (403, 404)


def test_too_many_or_none(here, client, env, db, cfg, monkeypatch):
    he = env["he"]
    r = client.post("/api/v1/collections", headers=he, json={"name": "Empty", "recordings": []})
    cid = r.json()["id"]
    r = client.get(f"/api/v1/collections/{cid}/evidence.pdf", headers=he)
    assert r.status_code == 400 and "nothing" in r.json()["detail"]
    monkeypatch.setattr(evidence, "MAX", 1)
    a = _upload(client, he, b"One\n", "one.txt")
    b = _upload(client, he, b"Two\n", "two.txt")
    r = client.post("/api/v1/collections", headers=he, json={"name": "Two", "recordings": [a["recording"], b["recording"]]})
    r = client.get(f"/api/v1/collections/{r.json()['id']}/evidence.pdf", headers=he)
    assert r.status_code == 400 and "1 recordings at most" in r.json()["detail"]


def test_where_anytopdf_may_not_run(client, env, db, cfg, monkeypatch):
    he, ha = env["he"], env["ha"]
    doc = _upload(client, he, b"Memo\n", "memo.txt")
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "lens"}).status_code == 200
    monkeypatch.setattr(anytopdf, "binary", lambda cfg: None)
    fetched = []
    monkeypatch.setattr(anytopdf, "fetch", lambda cfg, say=None: fetched.append(1))
    r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he)
    assert r.status_code == 503 and "never to use it" in r.json()["detail"] and not fetched

    # otherwise it's fetched on first use
    assert client.put("/api/v1/settings/documents", headers=ha, json={"converter": "auto"}).status_code == 200
    monkeypatch.setattr(anytopdf, "archive", lambda: ("x.tar.gz", "0" * 64))

    def fetch(cfg, say=None):
        fetched.append(1)
        raise OSError("offline")

    monkeypatch.setattr(anytopdf, "fetch", fetch)
    r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he)
    assert r.status_code == 503 and "couldn't be fetched" in r.json()["detail"] and fetched


def test_on_a_conversion_node(client, env, db, cfg, folder):
    he, ha = env["he"], env["ha"]
    srv, url = fake_anytopdf.node(TOKEN)
    try:
        assert client.put("/api/v1/settings/documents", headers=ha, json={"anytopdf_url": url, "anytopdf_token": TOKEN}).status_code == 200
        raw = b"Invoice 7\n"
        doc = _upload(client, he, raw, "invoice.txt")
        drain(db, settings.effective(db, cfg))
        fake_anytopdf.Node.jobs.clear()
        r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he)
        assert r.status_code == 200, r.text
        assert [j["name"] for j in fake_anytopdf.Node.jobs.values()] == ["01 invoice.txt"]  # one document: as it is

        # several: one zip, audio as its transcript only
        rid, _ = _call(db, cfg, folder)
        r = client.post("/api/v1/collections", headers=he, json={"name": "Case", "recordings": [doc["recording"], rid]})
        r = client.get(f"/api/v1/collections/{r.json()['id']}/evidence.pdf", headers=he)
        assert r.status_code == 200, r.text
        assert list(fake_anytopdf.Node.jobs.values())[-1]["name"] == "evidence.zip"
        text = _text(r.content)
        assert "Invoice 7" in text and "Alice: The invoice is paid." in text and hashlib.sha256(raw).hexdigest() in text
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not __import__("tests.api.test_anytopdf", fromlist=["REAL"]).REAL, reason="anytopdf isn't installed")
def test_with_the_real_anytopdf(client, env, db, cfg, folder):
    from tests.api.test_anytopdf import REAL

    cfg["documents"]["anytopdf"] = REAL
    he = env["he"]
    raw = b"Invoice 42\n\nTotal: 100 EUR\n"
    doc = _upload(client, he, raw, "invoice.txt")
    drain(db, settings.effective(db, cfg))
    rid, wav = _call(db, cfg, folder)
    r = client.post("/api/v1/collections", headers=he, json={"name": "Real", "recordings": [doc["recording"], rid]})
    r = client.get(f"/api/v1/collections/{r.json()['id']}/evidence.pdf", headers=he)
    assert r.status_code == 200, r.text
    out = folder / "real.pdf"
    out.write_bytes(r.content)
    text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True).stdout
    assert "Total: 100 EUR" in text and "Alice: The invoice is paid." in text
    assert hashlib.sha256(raw).hexdigest() in text and hashlib.sha256(wav.read_bytes()).hexdigest() in text
