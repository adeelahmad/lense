"""Evidence PDFs (evidence.py): a recording or a collection as one PDF made by anytopdf, each resource's own file (audio
and video with Lens's transcript) and what Lens found about it (evidence_report.py), then a provenance page with every
file's SHA-256, made here or on a conversion node."""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import subprocess

import pytest

from app.domain import anytopdf, comments, evidence, highlights, ingest, library, notes, pipelines, settings, store, topics
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
    assert [x.rsplit("/", 1)[-1] for x in args[3:11]] == [
        "01 memo-b.txt",
        "01 memo-b - what Lens found.txt",
        "02 Supplier call.wav",
        "02 Supplier call - what Lens found.txt",
        "03 plain.txt",
        "03 plain - what Lens found.txt",
        "04 memo-a.txt",
        "04 memo-a - what Lens found.txt",
    ]
    assert "What Lens found: Supplier call" in text

    # the parts picked go for every one of them
    r = client.get(f"/api/v1/collections/{cid}/evidence.pdf", headers=he, params={"include": ["file"]})
    assert r.status_code == 200, r.text
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert [x.rsplit("/", 1)[-1] for x in args[3:7]] == ["01 memo-b.txt", "02 Supplier call.wav", "03 plain.txt", "04 memo-a.txt"]

    # someone who can't read pods can't have it
    assert client.get(f"/api/v1/collections/{cid}/evidence.pdf", headers=ho).status_code == 404
    assert client.get(f"/api/v1/recordings/{a['recording']}/evidence.pdf", headers=ho).status_code in (403, 404)


def _enriched(db, cfg, folder, editor, outsider):
    """The call, analysed, with everything Lens can know about it: emotion and sounds on its lines, a summary, an
    analysis step's output, tags, a topic, places and dates, and people's notes, highlights and comments."""
    t = folder / "call.json"
    lines = [
        {"t0": 1000, "t1": 3500, "speaker": "Alice", "text": "The invoice is paid in Berlin.", "emotion": "Happy", "event": "Laughter"},
        {"t0": 4000, "t1": 6000, "speaker": "Bob", "text": "Thanks, filed it.", "emotion": "Neutral", "event": "Speech"},
    ]
    t.write_text(json.dumps({"lens": "lens/1", "doc": {"title": "Supplier call"}, "segments": lines}))
    rid = ingest.import_transcript(db, cfg, "pods", t, log=quiet)
    wav = folder / "call.wav"
    write_wav(wav)
    db.q("UPDATE $r SET source = 'audio', path = $p, title = 'Supplier call'", r=R("recording", rid), p=str(wav))
    from app.domain import analyze

    analyze.analyze_recording(db, cfg, rid)
    summary = {
        "summary": "Alice says the invoice is paid and Bob files it.",
        "key_points": [{"text": "The invoice is paid", "t0": 1000}],
        "action_items": [{"text": "File the invoice", "who": "Bob", "t0": 4000}],
        "topics": ["invoices"],
        "people": ["Alice", "Bob"],
        "sentiment": "Happy",
        "importance": 4,
    }
    db.q("UPDATE $r SET summary = $s", r=R("recording", rid), s=summary)
    pipelines.save_output(db, rid, "risk", {"level": "low", "reasons": ["paid on time"]}, {"model": "test-model"})
    library.set_tags(db, rid, ["supplier", "paid"])
    sid = store.ns_id(db, "pods")
    topics.tag(db, topics.create(db, sid, "Accounts payable"), [rid])
    enrichment = {
        "by": "anytopdf 0.4.0",
        "places": [{"name": "Berlin, Germany", "said": "Berlin", "lat": 52.524, "lon": 13.411, "from": "text"}],
        "dates": [{"text": "3 March 2026", "iso": "2026-03-03", "kind": "date"}],
    }
    db.q("UPDATE $r SET enrichment = $e", r=R("recording", rid), e=enrichment)
    notes.create(db, rid, sid, editor, "Shared with the team", t0=1000, shared=True)
    notes.create(db, rid, sid, editor, "Only mine to read", t0=1000)
    highlights.create(db, rid, sid, editor, 1000, 3000, quote="The invoice is paid", label="Payment")
    comments.create(db, rid, sid, editor, "Is this the March invoice?", t0=4000)
    return rid, wav


def test_everything_lens_found_goes_in(here, client, db, cfg, folder):
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(client, "ed@x.io", "editor password 1")
    rid, wav = _enriched(db, cfg, folder, ed, None)

    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he)
    assert r.status_code == 200, r.text
    text = _text(r.content)
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert [x.rsplit("/", 1)[-1] for x in args[3:5]] == ["01 Supplier call.wav", "01 Supplier call - what Lens found.txt"]

    # each line of the transcript says how it was said
    assert "Alice: (Happy, Laughter) The invoice is paid in Berlin." in text and "Bob: Thanks, filed it." in text
    # and the pages after it hold everything else
    for said in (
        "What Lens found: Supplier call",
        "Type: audio",
        "Namespace: pods",
        "Summary (written by a model)",
        "Alice says the invoice is paid and Bob files it.",
        "The invoice is paid (at 0:01)",
        "Bob: File the invoice (at 0:04)",
        "Overall tone: Happy",
        "Importance: 4 of 5",
        "risk (by test-model)",
        "level: low",
        "- paid on time",
        "paid, supplier",
        "Accounts payable (accepted, added by a person)",
        "Speakers",
        "Alice: 0:02 (56% of the speech), 1 turn, 6 words",
        "named in the transcript",
        "Emotion and sounds",
        "Happy: 56%",
        "Alice: Happy 100%",
        "Laughter: 1 time",
        "Term: Berlin (1)",
        "Places it names:",
        "Berlin, Germany (52.52400, 13.41100)",
        "3 March 2026 (2026-03-03)",
        "Found by: anytopdf 0.4.0",
        "Metadata",
        "Title: Supplier call",
    ):
        assert said in text, said
    # people's notes stay out unless asked for
    assert "Shared with the team" not in text and "Payment" not in text and "March invoice" not in text
    assert hashlib.sha256(wav.read_bytes()).hexdigest() in text  # the provenance page, as before

    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he, params={"include": ["notes", "file", "summary"]})
    assert r.status_code == 200, r.text
    text = _text(r.content)
    assert "0:01: Shared with the team (" in text and '0:01 to 0:03 "The invoice is paid": Payment (' in text
    assert "Payment" in text and "Is this the March invoice?" in text
    assert "Only mine to read" not in text  # a private note is its writer's alone
    assert "Summary (written by a model)" in text and "Term: Berlin" not in text and "Berlin, Germany" not in text
    # without emotion, the transcript is as it was said
    assert "Alice: The invoice is paid in Berlin." in text and "(Happy" not in text


def test_picking_the_parts(here, client, db, cfg, folder):
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(client, "ed@x.io", "editor password 1")
    rid, wav = _enriched(db, cfg, folder, ed, None)

    # its file alone: the evidence PDF as it was before Lens added what it found
    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he, params={"include": "file"})
    assert r.status_code == 200, r.text
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert not any("what Lens found" in a for a in args) and "What Lens found" not in _text(r.content)
    assert "Alice: The invoice is paid in Berlin." in _text(r.content)

    # what Lens found alone, without the file
    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he, params={"include": ["summary", "tags"]})
    assert r.status_code == 200, r.text
    args = fake_anytopdf.runs(here)[-1]["args"]
    assert [a.rsplit("/", 1)[-1] for a in args[3:4]] == ["01 Supplier call - what Lens found.txt"]
    assert "--transcript" not in args
    text = _text(r.content)
    assert "Summary (written by a model)" in text and "Tags" in text and "Speakers" not in text

    # a part that isn't one
    r = client.get(f"/api/v1/recordings/{rid}/evidence.pdf", headers=he, params={"include": "everything"})
    assert r.status_code == 422


def test_what_is_seen_on_pages_and_in_a_video(here, client, env, db, cfg, folder):
    from app.domain import evidence_report

    he = env["he"]
    doc = _upload(client, he, b"Invoice 42\n\nTotal: 100 EUR\n", "invoice.txt")
    drain(db, settings.effective(db, cfg))
    rid = doc["recording"]
    db.q(
        "CREATE description CONTENT $d",
        d={"recording": rid, "idx": 0, "t0": 0, "t1": 1, "paged": True, "text": "A printed invoice.", "model": "vision-test"},
    )
    obj = {"recording": rid, "label": "table", "spans": [[0, 1]], "screen_ms": 1, "first_ms": 0, "count": 1, "score": 0.8, "paged": True}
    db.q("CREATE object_track CONTENT $d", d=obj)
    text = evidence_report.write(db, cfg, rid, list(evidence_report.PARTS))
    assert "Type: document" in text and "Pages: 1" in text
    assert "What each page shows (written by vision-test):" in text and "- p. 1: A printed invoice." in text
    assert "- table: found 1 time, on 1 page, first at p. 1, 0.80 sure" in text
    assert "Notes, highlights and comments" in text and "None shared." in text

    rid, _ = _call(db, cfg, folder)
    db.q("UPDATE $r SET media = {kind: 'video', width: 640, height: 360, fps: 25}", r=R("recording", rid))
    db.q("CREATE shot CONTENT {recording: $r, idx: 0, t0: 0, t1: 4000}", r=rid)
    db.q("CREATE ocr_span CONTENT {recording: $r, t0: 1000, t1: 2000, text: 'INVOICE 42'}", r=rid)
    text = evidence_report.write(db, cfg, rid, ["details", "visual"])
    assert "Type: video" in text and "Size: 640 × 360 pixels, 25 frames a second" in text
    assert "Shots: 1" in text and "Text on screen:" in text and "- 0:01: INVOICE 42" in text
    assert "Summary" not in text and "NSFW" not in text  # nothing to say where classifying is off

    db.q("UPDATE $r SET nsfw = {score: 0.93, flagged: true, checked: 12, of: 40}", r=R("recording", rid))
    assert "NSFW" not in evidence_report.write(db, cfg, rid, ["details"])
    db.q("UPDATE $s SET nsfw = {enabled: true}", s=R("space", store.ns_id(db, "pods")))
    text = evidence_report.write(db, cfg, rid, ["details"])
    assert "NSFW: flagged (its most likely picture scored 0.93, flagged from 0.80; 12 of 40 pictures checked)" in text


def test_places_only_for_members(db, cfg, folder):
    from app.domain import evidence_report

    rid, _ = _call(db, cfg, folder)
    db.q("UPDATE $r SET enrichment = $e", r=R("recording", rid), e={"by": "anytopdf 0.4.0", "places": [{"name": "Berlin, Germany"}]})
    assert "Berlin, Germany" in evidence_report.write(db, cfg, rid, ["places"])
    assert "Berlin" not in evidence_report.write(db, cfg, rid, ["places"], places=False)
    assert evidence_report.parts_of(None) == [p for p in evidence_report.PARTS if p != "notes"]
    with pytest.raises(ValueError):
        evidence_report.parts_of(["everything"])


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
        r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he, params={"include": "file"})
        assert r.status_code == 200, r.text
        assert [j["name"] for j in fake_anytopdf.Node.jobs.values()] == ["01 invoice.txt"]  # one document: as it is

        # with what Lens found: a zip of both
        r = client.get(f"/api/v1/recordings/{doc['recording']}/evidence.pdf", headers=he)
        assert r.status_code == 200, r.text
        assert list(fake_anytopdf.Node.jobs.values())[-1]["name"] == "evidence.zip"
        assert "Invoice 7" in _text(r.content) and "What Lens found: invoice" in _text(r.content)

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
