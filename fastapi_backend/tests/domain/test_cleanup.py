"""The clean step: transcripts tidied by rules before analysis, each change in the edit history, people's corrections
left alone, and nothing done while it's off."""

from __future__ import annotations

import json
import re

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
    assert cleanup.plan(db, cfg, rid)["changes"] == []
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


# ---------- punctuation ----------
BARE = """Ann|N|yeah sorry about that okay so now it's recording so if I go back in network
Ben|N|is it like it should be in the chunks it's only every 30 seconds so it might take a while
Ann|N|I looked into different methods as well like trying to do that even ran local API"""
ENDS = {"recording": ".", "network": ".", "chunks": "?", "while": ".", "API": ".", "yeah": ","}


class FakePipe:
    """Transformers' token-classification pipeline as the punctuation model answers: a piece per word (two for long
    words, the mark on the last), the mark after the words in ENDS."""

    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        out, at = [], 0
        for w in text.split(" "):
            if len(w) > 6:
                out.append({"entity": "0", "score": 0.99, "start": at, "end": at + 3})
            out.append({"entity": ENDS.get(w, "0"), "score": 0.9, "start": at, "end": at + len(w)})
            at += len(w) + 1
        return out


@pytest.fixture
def model(monkeypatch):
    pipe = FakePipe()
    monkeypatch.setitem(cleanup._PIPE, "pipe", pipe)
    return pipe


def test_labels_read_long_text_in_overlapping_chunks(model):
    words = ("so we went to the market and then we bought apples while " * 40).split()
    labels = cleanup._labels(model, words)
    assert len(labels) == len(words) and model.calls == 4
    assert [w for w, m in zip(words, labels, strict=True) if m == "."] == ["while"] * 40


def test_punctuation_put_back_where_it_is_missing(db, cfg, model):
    cfg["analysis"].update(cleanup=True, cleanup_punctuation="missing")
    rid = ingest.import_text(db, cfg, "pods", BARE, "Bare", "auto")
    jid = jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert _lines(db, rid) == [
        "Yeah, sorry about that okay so now it's recording. So if I go back in network.",
        "Is it like it should be in the chunks? It's only every 30 seconds so it might take a while.",
        "I looked into different methods as well like trying to do that even ran local API.",
    ]
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("punctuation restored by kredor/punctuate-all" in line for line in log)
    assert set(db.values("SELECT VALUE punctuated FROM segment WHERE recording = $r", r=rid)) == {cleanup.PUNCTUATED}

    # a line is given marks once, and a transcript with them isn't read again
    calls = model.calls
    cfg["analysis"]["cleanup_punctuation"] = "always"
    jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert model.calls == calls
    assert len(db.rows("SELECT * FROM segment_edit WHERE recording = $r", r=rid)) == 3


def test_a_transcript_with_punctuation_keeps_it(db, cfg, model):
    cfg["analysis"].update(cleanup=True, cleanup_punctuation="missing")
    rid = _talk(db, cfg)
    jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert model.calls == 0  # it has its full stops: only the rules ran
    assert _lines(db, rid)[0] == "Yeah. Sorry, sorry about that. Okay, so now it's recording."
    # "always" reads it too, but keeps every mark it has
    assert cleanup.punctuate(["Sorry, I mean it. yeah"]) == ["Sorry, I mean it. yeah,"]


def test_without_the_model_the_rules_run_alone(db, cfg):
    cfg["analysis"].update(cleanup=True, cleanup_punctuation="always")
    rid = ingest.import_text(db, cfg, "pods", BARE, "Bare", "auto")
    jid = jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("punctuation not restored: the punctuation model isn't ready here" in line for line in log)
    assert _lines(db, rid)[0] == "Yeah sorry about that okay so now it's recording so if I go back in network"
    assert not db.values("SELECT VALUE punctuated FROM segment WHERE recording = $r AND punctuated != NONE", r=rid)


def test_the_model_is_fetched_only_when_it_is_asked_for(cfg):
    from app.domain import components, machine

    c, m = components.BY_ID["punctuation"], machine.probe(cfg["data_dir"])
    assert not c.needed(cfg, m)
    cfg["analysis"]["cleanup_punctuation"] = "missing"
    assert not c.needed(cfg, m)  # cleanup itself is off
    cfg["analysis"]["cleanup"] = True
    assert c.needed(cfg, m) and c.serves(cfg, m) == {"clean"}


# ---------- grammar ----------
# what LanguageTool 6.8 answers for these: the rule, its category, and the words it matches
FIXES = [
    (r"(?<=he )go\b", "goes", "HE_VERB_AGR", "GRAMMAR"),
    (r"\ba(?= apple)", "an", "EN_A_VS_AN", "MISC"),
    (r"\brealy\b", "really", "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    (r"very very", "very", "ENGLISH_WORD_REPEAT_RULE", "MISC"),
    (r"Dyno", "dyno", "MADE_UP", "GRAMMAR"),
]
WORDY = """Ann|N|so he go to the shop 🙂 he go home
Ben|N|I ate a apple and it was realy very very good
Ann|N|we met Dyno Therapeutics"""


@pytest.fixture
def languagetool():
    """A LanguageTool server that knows FIXES, counting offsets in UTF-16 units as the real one (in Java) does."""
    import http.server
    import threading
    import urllib.parse

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            text = form["text"][0]
            seen.append((form["language"][0], text))
            u16 = lambda s: len(s.encode("utf-16-le")) // 2  # noqa: E731
            matches = [
                {
                    "offset": u16(text[: m.start()]),
                    "length": u16(m.group()),
                    "replacements": [{"value": right}],
                    "rule": {"id": rule, "category": {"id": cat}},
                }
                for wrong, right, rule, cat in FIXES
                for m in re.finditer(wrong, text)
            ]
            body = json.dumps({"matches": matches}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def test_grammar_fixes_from_languagetool_leave_names_and_spelling(db, cfg, languagetool):
    url, seen = languagetool
    cfg["analysis"].update(cleanup=True, cleanup_grammar_url=url)
    rid = ingest.import_text(db, cfg, "pods", WORDY, "Wordy", "auto")
    jid = jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert _lines(db, rid) == [
        "So he goes to the shop 🙂 he goes home",  # after the emoji too: offsets are counted as LanguageTool counts them
        "I ate an apple and it was realy very very good",  # nor spelling, nor how people talk
        "We met Dyno Therapeutics",  # a word with a capital isn't changed
    ]
    assert len(seen) == 1 and seen[0][0] == "auto"  # the lines go at once, a blank line between them
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("grammar checked by LanguageTool" in line for line in log)
    edits = db.rows("SELECT by FROM segment_edit WHERE recording = $r", r=rid)
    assert [e["by"] for e in edits] == [cleanup.CLEANER] * 3


def test_without_its_server_the_rules_run_alone(db, cfg):
    cfg["analysis"].update(cleanup=True, cleanup_grammar_url="http://127.0.0.1:9")
    rid = ingest.import_text(db, cfg, "pods", WORDY, "Wordy", "auto")
    jid = jobs.enqueue(db, rid, ["clean"])
    drain(db, cfg)
    assert _lines(db, rid)[0] == "So he go to the shop 🙂 he go home"
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("grammar not checked: the LanguageTool server at 127.0.0.1 didn't answer" in line for line in log)


def test_the_grammar_server_is_an_address_without_a_password():
    from app.domain import settings

    def check(v):
        return settings._check("analysis", "cleanup_grammar_url", v, None)

    assert check(" http://languagetool:8010/ ") == "http://languagetool:8010" and check("") is None
    for bad in ("ftp://lt", "http://user:pw@lt:8010", "languagetool"):
        with pytest.raises(ValueError, match="LanguageTool server"):
            check(bad)
