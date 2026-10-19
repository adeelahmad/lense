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


YOUTUBE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.869 align:start position:0%
\x20
welcome<00:00:00.480><c> back</c><00:00:00.880><c> to</c><00:00:01.040><c> the</c><00:00:01.280><c> channel</c>

00:00:02.869 --> 00:00:05.000 align:start position:0%
today<00:00:03.200><c> we're</c><00:00:03.440><c> looking</c><00:00:03.760><c> at</c><00:00:04.000><c> tides</c>
"""

MEETING_VTT = """WEBVTT

1b2c4d5e-2a7f-4b1c-9d3e-0f6a8b7c5d4e/12-0
00:00:03.120 --> 00:00:06.480
<v Priya Natarajan>Can everyone hear me okay?</v>

1b2c4d5e-2a7f-4b1c-9d3e-0f6a8b7c5d4e/13-0
00:00:06.900 --> 00:00:09.300
<v Tom O'Brien>Yes, loud and clear.</v>

1b2c4d5e-2a7f-4b1c-9d3e-0f6a8b7c5d4e/14-0
00:00:09.300 --> 00:00:12.000
<v Priya Natarajan>Great, let's start with the
budget review.</v>
"""

CLASSED_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
<v.loud Esme>It's a blue apple tree!

00:00:02.000 --> 00:00:04.000
<v Mary>No way!
<v.first.loud Esme>Oh <i>yes</i>!

00:00:04.000 --> 00:00:05.000
Nobody named here.
"""


def test_webvtt_voice_spans_name_each_line(db, cfg):
    yt = ingest.read_text_transcript(YOUTUBE_VTT, "auto")["segments"]  # no voices: as before, the inline timings dropped
    assert [(s["speaker"], s["text"], s["t0"], s["t1"]) for s in yt] == [
        (None, "welcome back to the channel", 160, 2869),
        (None, "today we're looking at tides", 2869, 5000),
    ]
    meeting = ingest.read_text_transcript(MEETING_VTT, "vtt")["segments"]  # Teams and Zoom: <v Name>...</v>
    assert [(s["speaker"], s["text"]) for s in meeting] == [
        ("Priya Natarajan", "Can everyone hear me okay?"),
        ("Tom O'Brien", "Yes, loud and clear."),
        ("Priya Natarajan", "Great, let's start with the budget review."),
    ]
    classed = ingest.read_text_transcript(CLASSED_VTT, "vtt")["segments"]  # classes after the v; two voices in one cue
    assert [(s["speaker"], s["text"], s["t0"], s["t1"]) for s in classed] == [
        ("Esme", "It's a blue apple tree!", 0, 2000),
        ("Mary", "No way!", 2000, 3000),
        ("Esme", "Oh yes!", 3000, 4000),
        (None, "Nobody named here.", 4000, 5000),
    ]
    srt = "1\n00:00:01,000 --> 00:00:02,500\nAlice: <i>still</i> read from the line\n"  # a cue without a voice: unchanged
    assert [(s["speaker"], s["text"]) for s in ingest.read_text_transcript(srt, "srt")["segments"]] == [
        ("Alice", "still read from the line")
    ]
    ingest.import_text(db, cfg, "pods", MEETING_VTT, title="Standup")  # the voices become the namespace's people
    assert speaker_names(db, "pods") == {"Priya Natarajan", "Tom O'Brien"}


def test_an_engine_that_isnt_installed_falls_back_to_one_that_is(monkeypatch):
    """faster-whisper is there (EXTRAS=whisper), SenseVoice (the default engine) not yet: imports are transcribed anyway."""
    made = []

    def sensevoice(cfg):
        raise ingest.EngineMissing("SenseVoice needs FunASR")

    monkeypatch.setattr(ingest, "installed", lambda e: e == "whisper")
    monkeypatch.setattr(ingest, "SenseVoice", sensevoice)
    monkeypatch.setattr(ingest, "Whisper", lambda cfg, mlx=False: made.append(mlx) or "whisper engine")
    said = []
    cfg = {"transcribe": {"engine": "sensevoice", "sensevoice": {}}}
    assert ingest.get_engine(cfg, said.append) == "whisper engine" and made == [False]
    assert said == ["  sensevoice isn't installed on this worker (SenseVoice needs FunASR); transcribing with whisper"]


def test_an_engine_that_is_there_but_wont_import_falls_back_too(monkeypatch):
    """FunASR without PyTorch is found but doesn't import: the job goes on with whisper rather than failing."""
    monkeypatch.setattr(ingest, "installed", lambda e: True)

    def sensevoice(cfg):
        raise ingest.EngineMissing("SenseVoice needs FunASR")

    def whisper(cfg, mlx=False):
        if mlx:
            raise ingest.EngineMissing("mlx-whisper isn't installed")
        return "whisper engine"

    monkeypatch.setattr(ingest, "SenseVoice", sensevoice)
    monkeypatch.setattr(ingest, "Whisper", whisper)
    said = []
    assert ingest.get_engine({"transcribe": {"engine": "sensevoice"}}, said.append) == "whisper engine"
    assert said == ["  sensevoice isn't installed on this worker (SenseVoice needs FunASR); transcribing with whisper"]


def test_with_no_engine_at_all_the_error_says_how_to_add_one(monkeypatch):
    monkeypatch.setattr(ingest, "installed", lambda e: False)
    monkeypatch.setattr(ingest.importlib.util, "find_spec", lambda name: None)
    import builtins

    real = builtins.__import__

    def no_engines(name, *a, **k):
        if name.split(".")[0] in ("funasr", "faster_whisper", "mlx_whisper"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_engines)
    cfg = {"transcribe": {"engine": "sensevoice", "device": "cpu", "sensevoice": {}}}
    with pytest.raises(ingest.EngineMissing, match=r"no speech-to-text engine.*FunASR.*Settings → Components.*EXTRAS=whisper") as e:
        ingest.get_engine(cfg)
    assert not isinstance(e.value, SystemExit)  # a job records it as a plain error, not "SystemExit: ..."
