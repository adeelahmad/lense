"""The clean step: transcripts tidied by rules before analysis, each change in the edit history, people's corrections
left alone, and nothing done while it's off."""

from __future__ import annotations

import pytest

from app.domain import cleanup, ingest, jobs, store
from tests.helpers import drain, quiet

R = store.R
TALK = """Ann|N|Yeah. Sorry, sorry about that. Okay, um, so now it's recording.
Ben|N|Because I I I looked into, you know, segmentation , like, uh, the the different methods.
Ben|N|and i think we should talk to Dyno Therapeutics about it
Ann|N|what it is is very very good, and we had had enough of it."""


@pytest.mark.parametrize(
    ("line", "clean"),
    [
        ("Because I I I looked into it.", "Because I looked into it."),
        ("um, i think the the thing is, uh, we should go.", "I think the thing is we should go."),
        ("I was, uh, there.", "I was there."),
        ("I, I, I think so", "I think so"),
        ("So um... i'm not sure. Uh. what about it?", "So I'm not sure. What about it?"),
        ("as well , like , this", "As well, like, this"),
        ("what it is is very very good, we had had enough", "What it is is very very good, we had had enough"),
        ("ask Dr. smith, e.g. later", "Ask Dr. smith, e.g. later"),
        ("the ER was busy and UM won", "The ER was busy and UM won"),
        ("5 mm wide", "5 mm wide"),
        ("see www.example.com for more", "See www.example.com for more"),
        ("www.example.com has it", "www.example.com has it"),
    ],
)
def test_rules(line, clean):
    assert cleanup.clean_line(line) == clean
    assert cleanup.clean_line(clean) == clean  # running them again changes nothing


def test_other_languages_keep_their_words():
    # "um" is German for "at", and "er" for "he": only a word said three times in a row goes, and capitals
    assert cleanup.clean_line("ich ich ich komme um fünf. er kommt auch", english_rules=False) == "Ich komme um fünf. Er kommt auch"
    assert cleanup.english(None, ["ich weiss nicht, das ist gut und er kommt um fünf"]) is False
    assert cleanup.english(None, ["so I think we should go to the shop"]) is True
    assert cleanup.english("de", ["so I think we should go to the shop"]) is False


def test_a_line_that_carries_on_keeps_its_lower_case():
    assert cleanup.clean_line("and then we went", starts_sentence=False) == "and then we went"
    assert cleanup.clean_line("and then we went") == "And then we went"


def _talk(db, cfg):
    return ingest.import_text(db, cfg, "pods", TALK, "Talk", "auto")


def _lines(db, rid):
    return [s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)]


def test_off_by_default_changes_nothing(db, cfg):
    rid = _talk(db, cfg)
    before = _lines(db, rid)
    jobs.enqueue(db, rid, ["clean", "analyze"])
    drain(db, cfg)
    assert _lines(db, rid) == before
    assert not db.rows("SELECT * FROM segment_edit WHERE recording = $r", r=rid)


def test_clean_step_tidies_lines_and_keeps_them_in_the_history(db, cfg, folder):
    cfg["analysis"]["cleanup"] = True
    rid = _talk(db, cfg)
    before = _lines(db, rid)
    jid = jobs.enqueue(db, rid, ["clean", "analyze"])
    drain(db, cfg)
    assert _lines(db, rid) == [
        "Yeah. Sorry, sorry about that. Okay, so now it's recording.",
        "Because I looked into, you know, segmentation, like the different methods.",
        "And I think we should talk to Dyno Therapeutics about it",
        "What it is is very very good, and we had had enough of it.",
    ]
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("cleaned up 4 line(s)" in line for line in log)
    edits = db.rows("SELECT idx, before, after, by FROM segment_edit WHERE recording = $r ORDER BY idx", r=rid)
    assert [e["by"] for e in edits] == [cleanup.CLEANER] * 4
    assert [e["before"]["text"] for e in edits] == before
    # the extractor read the cleaned text
    assert db.values("SELECT VALUE name FROM entity WHERE name = 'Dyno Therapeutics'")

    # running it again changes nothing
    jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert len(db.rows("SELECT * FROM segment_edit WHERE recording = $r", r=rid)) == 4


def test_people_s_corrections_outrank_the_rules(db, cfg):
    cfg["analysis"]["cleanup"] = True
    rid = _talk(db, cfg)
    sid = rid * store.SEG + 1
    kept = "Because I I I looked into it, you know."
    db.q("UPDATE $s SET text = $t", s=R("segment", sid), t=kept)  # a person's correction (or Revert) of line 2
    db.q(
        "CREATE segment_edit CONTENT $d",
        d={"recording": rid, "idx": 1, "before": {"text": "x"}, "after": {"text": kept}, "by": "ed@x.io", "at": store.now(), "n": 1},
    )
    jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert _lines(db, rid)[1] == kept
    assert _lines(db, rid)[0] == "Yeah. Sorry, sorry about that. Okay, so now it's recording."


def test_documents_and_fillers(db, cfg, folder):
    cfg["analysis"]["cleanup"] = True
    cfg["analysis"]["cleanup_fillers"] = False
    rid = _talk(db, cfg)
    jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert _lines(db, rid)[0] == "Yeah. Sorry, sorry about that. Okay, um, so now it's recording."
    db.q("UPDATE $r SET source = 'document'", r=R("recording", rid))
    assert cleanup.plan(db, cfg, rid) == []
    assert cleanup.clean_pending(db, cfg, log=quiet) == 0  # the command line's clean: transcripts only


def test_standard_pipeline_cleans_before_analysis():
    from app.domain import pipelines

    for steps in (pipelines.STANDARD, jobs.PIPELINE, jobs.AFTER_IMPORT):
        assert steps.index("clean") < steps.index("analyze")


def test_workers_with_an_older_steps_list_run_it(db, cfg):
    w = jobs.Worker(db, lambda: cfg, name="old", steps=None)
    assert "clean" in w.can
    cfg["workers"]["steps"] = ["analyze", "report"]
    assert "clean" in jobs.Worker(db, lambda: cfg, name="older").can
