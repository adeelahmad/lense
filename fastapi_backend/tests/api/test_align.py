"""An imported transcript lined up with its audio (align.py, docs/processing.md#aligning): only when someone asks, its
audio is transcribed, the imported words are matched to the words heard, and each line takes their times; the text and
speakers stay."""

from __future__ import annotations

import difflib
import json
import random

import pytest

from app.domain import align, ingest, jobs, quiet, store
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import drain, login, make_user, write_wav
from tests.helpers import quiet as hush

R = store.R
TEXT = "Welcome back to the show.\n\nUm.\n\nToday we talk about capsids.\n\nBye."


def _heard():
    """What a fake engine hears: one line with word times (Whisper), one without (SenseVoice), a word misheard."""
    return [
        {
            "t0": 1000,
            "t1": 2200,
            "text": "Welcome back to the show.",
            "words": json.dumps(
                [["Welcome", 1000, 1300], ["back", 1300, 1500], ["to", 1500, 1600], ["the", 1600, 1700], ["show.", 1700, 2200]]
            ),
        },
        {"t0": 5000, "t1": 8000, "text": "today we talk about capsid", "words": None},
    ]


class Engine:
    name = "whisper"

    def transcribe(self, audio, terms=()):
        return _heard()


def test_lines_take_the_times_of_their_words_and_the_rest_fill_in_between():
    lines = ["Welcome back to the show.", "Um.", "Today we talk about capsids.", "Bye."]
    heard = align.heard_words(_heard())
    assert heard[5:] == [("today", 5000, 5600), ("we", 5600, 6200), ("talk", 6200, 6800), ("about", 6800, 7400), ("capsid", 7400, 8000)]
    out, matched, total = align.line_up(lines, heard, duration_ms=10_000)
    assert (matched, total) == (9, 12)  # "Um", "capsids" and "Bye" weren't heard
    assert [(t0, t1) for t0, t1, _ in out] == [
        (1000, 2200),  # its first word's start to its last one's end
        (2200, 5000),  # nothing heard: between the lines either side
        (5000, 7400),  # up to "about": "capsids" wasn't heard
        (7400, 10_000),  # after the last: to the end of the audio
    ]
    assert out[0][2][-1] == ["show.", 1700, 2200] and out[2][2][0] == ["Today", 5000, 5600]  # its own spelling
    assert out[1][2] == [] and out[3][2] == []

    # a CJK line is matched character by character
    assert align.line_up(["你好"], [("你", 0, 100), ("好", 100, 200)])[1] == 2


def test_a_long_transcript_is_matched_as_well_as_word_by_word_and_quickly():
    """Rare words anchor first, then the gaps: as many matches as difflib finds word by word (which on 20,000 words
    took 7.5 s against 0.25 s), in order."""
    rng = random.Random(1)
    said = rng.choices([f"w{i}" for i in range(500)], [1 / (i + 1) for i in range(500)], k=3000)
    heard = [w for w in said if rng.random() > 0.08]  # some words not heard
    heard[::37] = ["zz"] * len(heard[::37])  # and some misheard
    got = align.matches(said, heard)
    plain = difflib.SequenceMatcher(None, said, heard, autojunk=False).get_matching_blocks()
    assert len(got) == sum(b.size for b in plain) > 2500
    assert all(said[i] == heard[j] for i, j in got) and got == sorted(got) and len({j for _, j in got}) == len(got)


def test_imports_say_their_times_are_estimated_and_older_ones_are_recognised(db, cfg):
    prose = ingest.import_text(db, cfg, "pods", TEXT, title="Prose")
    timed = ingest.import_text(db, cfg, "pods", "[00:00] Ann: Hello there.\n[00:02] Ben: Hi.\n[00:04] Ann: Bye.")
    times = lambda rid: db.one("SELECT times FROM $r", r=R("recording", rid)).get("times")  # noqa: E731
    assert (times(prose), times(timed)) == ("estimated", None)

    # an install from before: no `times`; the upgrade step finds the lines spaced as the estimate spaces them
    db.q("UPDATE recording SET times = NONE")
    lines = db.rows("SELECT idx, t0, t1 FROM segment WHERE recording = $r ORDER BY idx", r=prose)
    assert align.looks_estimated(lines) and lines[0]["t0"] == 0
    for _ in range(2):  # safe to run twice
        align.migrate_estimated(db)
        assert (times(prose), times(timed)) == ("estimated", None)


@pytest.fixture
def env(client, db, cfg, folder, monkeypatch):
    wav, tr = folder / "show.wav", folder / "show.txt"
    write_wav(wav, seconds=10)
    tr.write_text(TEXT)
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=hush)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    monkeypatch.setattr(ingest, "get_engine", lambda cfg, log=None: Engine())
    return {
        "rid": rid,
        "wav": wav,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def test_lining_up_a_transcript_with_its_audio(client, env, db, cfg, folder):
    rid, he, hv = env["rid"], env["he"], env["hv"]
    rec = client.get(f"/api/v1/recordings/{rid}", headers=he).json()
    assert (rec["times"], rec["aligned"]) == ("estimated", None)
    before = db.rows("SELECT idx, text, speaker, t0 FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [s["t0"] for s in before] == [0, 2175, 2810, 4985]  # the speaking-rate estimate

    # editors only; nothing without audio, nor when the lines came with times
    assert client.post(f"/api/v1/recordings/{rid}/align", headers=hv).status_code == 403
    text_only = ingest.import_text(db, cfg, "pods", TEXT, title="No audio")
    r = client.post(f"/api/v1/recordings/{text_only}/align", headers=he)
    assert (r.status_code, r.json()["detail"]) == (400, "Attach its audio first: there's nothing to line the transcript up with.")
    (folder / "timed.txt").write_text("[00:00] Ann: Hello there.\n[00:02] Ben: Hi.\n[00:04] Ann: Bye.")
    write_wav(folder / "timed.wav", seconds=3)
    timed = ingest.import_transcript(db, cfg, "pods", folder / "timed.txt", audio=folder / "timed.wav", log=hush)
    r = client.post(f"/api/v1/recordings/{timed}/align", headers=he)
    assert (r.status_code, r.json()["detail"]) == (
        400,
        "Its lines already have times: they came with the transcript or from transcription.",
    )

    # a heavy step in the background, then what reads the times; asking again gives the same job
    assert align.STEP in quiet.HEAVY and align.STEP in jobs.STEPS
    r = client.post(f"/api/v1/recordings/{rid}/align", headers=he)
    assert r.status_code == 202
    jid = r.json()["job"]
    assert client.post(f"/api/v1/recordings/{rid}/align", headers=he).json()["job"] == jid
    assert [s["type"] for s in db.one("SELECT steps FROM $j", j=R("job", jid))["steps"]] == ["align", "analyze", "embed", "report"]
    drain(db, cfg)
    job = db.one("SELECT status, log FROM $j", j=R("job", jid))
    assert job["status"] == "succeeded", job["log"]
    assert any("9 of 12 word(s) heard (75%)" in line for line in job["log"])

    # only the times changed
    after = db.rows("SELECT idx, text, speaker, t0, t1, dur, words FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [(s["idx"], s["text"], s.get("speaker")) for s in after] == [(s["idx"], s["text"], s.get("speaker")) for s in before]
    assert [(s["t0"], s["t1"]) for s in after] == [(1000, 2200), (2200, 5000), (5000, 7400), (7400, 10_000)]
    assert after[1]["dur"] == 2800 and json.loads(after[0]["words"])[0] == ["Welcome", 1000, 1300] and after[1].get("words") is None
    rec = client.get(f"/api/v1/recordings/{rid}", headers=he).json()
    assert rec["times"] == "aligned" and (rec["aligned"]["matched"], rec["aligned"]["engine"]) == (0.75, "whisper")
    player = client.get(f"/api/v1/recordings/{rid}/player", headers=he).json()
    assert player["segments"][0]["t0"] == 1000

    # transcribing it again makes the lines the engine's own: their times are no longer the import's
    jobs.enqueue(db, rid, [{"type": "transcribe", "force": True}])
    drain(db, cfg)
    row = db.one("SELECT times, aligned FROM $r", r=R("recording", rid))
    assert (row.get("times"), row.get("aligned")) == (None, None)

    # nothing heard of it: the step fails, saying so, and the times stay
    again = ingest.import_text(db, cfg, "pods", "Quite different words entirely.", title="Other")
    db.q("UPDATE $r SET source = 'audio', path = $p", r=R("recording", again), p=str(env["wav"]))
    jid = client.post(f"/api/v1/recordings/{again}/align", headers=he).json()["job"]
    drain(db, cfg)
    job = db.one("SELECT status, error FROM $j", j=R("job", jid))
    assert job["status"] == "failed" and "none of the transcript's words were heard" in job["error"]
    assert db.one("SELECT times FROM $r", r=R("recording", again))["times"] == "estimated"

    audited = db.values("SELECT VALUE action FROM audit_log WHERE action = 'recording.align'")
    assert len(audited) == 3


def test_agents_line_up_a_transcript_through_mcp(client, env, db, cfg):
    rid, he = env["rid"], env["he"]
    assert tool(client, he, "get_recording", recording_id=rid)["times"] == "estimated"
    _, tokens = _grant(client, he, scope="read")
    assert "read-only" in tool_error(client, _bearer(tokens), "align_transcript", recording_id=rid)
    out = tool(client, he, "align_transcript", recording_id=rid)
    assert out["status"] == "aligning" and out["recording_url"].endswith(str(rid))
    drain(db, cfg)
    assert db.one("SELECT status FROM $j", j=R("job", out["job"]))["status"] == "succeeded"
    assert tool(client, he, "get_recording", recording_id=rid)["times"] == "aligned"
    assert tool_error(client, he, "align_transcript", recording_id=999_999) == "not found, or not something you can read"
