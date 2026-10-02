"""Transcript formats, speakers by name, documents, re-transcribing, pasting and chunk stitching."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.domain import analyze, ingest, store
from tests.helpers import seed, speaker_names, write_docx, write_pdf


def test_formats_and_speakers_by_name(db, cfg, folder):
    a, b, c = seed(db, cfg, folder)
    assert speaker_names(db, "pods") == {"Alice", "Bob", "Carol"}  # Alice reused across recordings in a namespace
    assert speaker_names(db, "calls") == {"Alice", "Dave"}  # a different Alice: ids never cross namespaces
    segs = db.rows("SELECT idx, t0, emotion FROM segment WHERE recording = $r ORDER BY idx", r=c)
    assert [s["t0"] for s in segs] == [1000, 9000, 15000]
    emo = db.rows("SELECT idx, emotion FROM segment WHERE recording = $r ORDER BY idx", r=a)
    assert [e["emotion"] for e in emo[:2]] == ["Neutral", "Surprise"]


def test_documents_markdown_mdx_docx_pdf(folder):
    md = folder / "notes.md"
    md.write_text(
        "---\ntitle: Standup\n---\n# Standup\n\n**Alice:** We shipped the [release](http://x) today.\n\n**Bob:** Great, *finally*.\n\n"
        "**Alice:** Next is the `v2` migration.\n"
    )
    t = ingest.read_transcript(md)
    assert t["title"] == "Standup"
    segs = t["segments"]
    assert [(s["speaker"], s["text"]) for s in segs][:2] == [("Alice", "We shipped the release today."), ("Bob", "Great, finally.")]
    mdx = folder / "talk.mdx"
    mdx.write_text(
        "import Player from './Player'\nexport const meta = {}\n\n<Player src=\"a.mp3\" />\n\n"
        "Alice: Hello from MDX.\n\n{/* note */}\nBob: <Highlight>Bold</Highlight> claim here.\n\nAlice: Done.\n"
    )
    segs = ingest.read_transcript(mdx)["segments"]
    assert [s["text"] for s in segs] == ["Hello from MDX.", "Bold claim here.", "Done."]
    dx = folder / "call.docx"
    write_docx(
        dx,
        [
            "Alice Smith  0:03",
            "Thanks for joining the call.",
            "Bob Jones  0:11",
            "Happy to be here.",
            "It has been busy.",
            "Alice Smith  0:20",
            "Let us begin.",
        ],
    )
    segs = ingest.read_transcript(dx)["segments"]
    assert [(s["speaker"], s["t0"]) for s in segs] == [("Alice Smith", 3000), ("Bob Jones", 11000), ("Alice Smith", 20000)]
    assert segs[1]["text"] == "Happy to be here. It has been busy."
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
    segs = ingest.read_transcript(pdf)["segments"]
    assert [s["speaker"] for s in segs] == ["Carol", "Dave", "Carol", "Dave"]
    prose = folder / "essay.txt"
    prose.write_text("A first paragraph that is\nhard wrapped across lines.\n\nA second paragraph.")
    assert [s["text"] for s in ingest.read_transcript(prose)["segments"]] == [
        "A first paragraph that is hard wrapped across lines.",
        "A second paragraph.",
    ]


def test_retranscribing_replaces_segments(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", "Ann: one.\nBen: two.\nAnn: three.\nBen: four.", title="Redo")
    analyze.analyze_recording(db, cfg, rid)
    ingest.write_transcript(
        db,
        rid,
        store.ns_id(db, "pods"),
        [{"t0": 0, "t1": 500, "text": "only this"}, {"t0": 500, "t1": 900, "text": "and this"}],
        {"status": "transcribed"},
    )
    texts = [x["text"] for x in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)]
    assert texts == ["only this", "and this"]  # guards against SurrealDB 3.2 dropping re-created ids
    analyze.analyze_recording(db, cfg, rid)  # and analysing again works
    assert db.one("SELECT status FROM $r", r=store.R("recording", rid))["status"] == "analyzed"


def test_paste_and_chunk_stitching(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", "Alice: pasted hello there.\nBob: pasted reply.\nAlice: and more.", title="Pasted")
    assert db.one("SELECT title, source FROM $r", r=store.R("recording", rid)) == {"title": "Pasted", "source": "transcript"}
    rows = [
        {
            "index": 0,
            "start_ms": 0,
            "end_ms": 20000,
            "raw_text": "<|en|><|NEUTRAL|><|Speech|><|withitn|>x",
            "text": "one two three four five six seven eight nine ten",
        },
        {
            "index": 1,
            "start_ms": 15000,
            "end_ms": 35000,
            "raw_text": "<|en|><|HAPPY|><|Laughter|><|withitn|>x",
            "text": "eight nine ten eleven twelve thirteen",
        },
    ]
    segs = ingest.read_text_transcript("\n".join(json.dumps(r) for r in rows), "auto")["segments"]
    assert " ".join(s["text"] for s in segs) == "one two three four five six seven eight nine ten eleven twelve thirteen"
    assert (segs[-1]["emotion"], segs[-1]["event"]) == ("Happy", "Laughter")
    assert [ingest.sniff(x) for x in ("[00:01] Alice: hi", '{"segments": []}', "[1, 2]", "WEBVTT\n", "# Notes\nA: b")] == [
        "text",
        "json",
        "json",
        "vtt",
        "markdown",
    ]
    up = sorted(pathlib.Path("/mnt/user-data/uploads").glob("*_chunks.jsonl"))
    if up:  # a real chunked transcript, when one is around
        rows = [json.loads(line) for line in up[-1].read_text().splitlines() if line.strip()]
        naive = sum(len((r.get("text") or "").split()) for r in rows)
        stitched = sum(len(s["text"].split()) for s in ingest.stitch_chunks(rows))
        assert naive * 0.6 < stitched < naive * 0.9


def test_an_engine_that_isnt_installed_falls_back_to_one_that_is(monkeypatch):
    """The Docker images carry faster-whisper, not SenseVoice (the default engine): imports are transcribed anyway."""
    made = []
    monkeypatch.setattr(ingest, "installed", lambda e: e == "whisper")
    monkeypatch.setattr(ingest, "Whisper", lambda cfg, mlx=False: made.append(mlx) or "whisper engine")
    said = []
    cfg = {"transcribe": {"engine": "sensevoice", "sensevoice": {}}}
    assert ingest.get_engine(cfg, said.append) == "whisper engine" and made == [False]
    assert said == ["  sensevoice isn't installed on this worker; transcribing with whisper"]
    monkeypatch.setattr(ingest, "installed", lambda e: False)  # nothing installed: the configured engine says what it needs
    with pytest.raises(SystemExit, match="FunASR"):
        ingest.get_engine(cfg)
