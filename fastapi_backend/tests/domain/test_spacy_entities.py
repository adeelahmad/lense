"""Entities found by spaCy (analysis.entities "spacy"): the defined entities first, then spaCy's names; the rules while
spaCy or its model isn't installed; and spaCy fetched only when it's picked."""

from __future__ import annotations

import re
import sys

import pytest

from app.domain import analyze, components, ingest, jobs, machine, store, workflows
from tests.helpers import drain

R = store.R
LINE = "Okay so Sarah Connor met the Dyno Therapeutics team in Boston next Tuesday."
LABELS = {"Okay": "ORG", "Sarah Connor": "PER", "Dyno Therapeutics team": "ORG", "Boston": "GPE", "next Tuesday": "DATE", "team": "MISC"}


class Ent:
    def __init__(self, m, label):
        self.text, self.label_, self.start_char, self.end_char = m.group(0), label, m.start(), m.end()

    def __len__(self):
        return len(self.text.split())


class FakeNlp:
    """spaCy's pipeline as its model answers: the phrases in LABELS, longest first, where they don't overlap."""

    def __call__(self, text):
        ents, taken = [], []
        for phrase in sorted(LABELS, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(phrase)}\b", text):
                if not any(m.start() < b and m.end() > a for a, b in taken):
                    taken.append((m.start(), m.end()))
                    ents.append(Ent(m, LABELS[phrase]))
        doc = type("Doc", (), {})()
        doc.ents = sorted(ents, key=lambda e: e.start_char)
        return doc


@pytest.fixture
def nlp(cfg, monkeypatch):
    cfg["analysis"].update(entities="spacy", gazetteer=["Dyno Therapeutics|PRODUCT"])
    monkeypatch.setattr(analyze, "_NLP", {"en_core_web_sm": FakeNlp()})


def test_defined_entities_come_first_then_spacy_s_names(cfg, nlp):
    find = analyze.entity_finder(cfg, analyze.parse_gazetteer(cfg["analysis"]["gazetteer"]))
    assert not find.fell_back
    # "Okay" alone isn't a name; the defined entity keeps its type over spaCy's longer guess; PER is a person
    assert find(LINE) == [("Sarah Connor", "PERSON"), ("Dyno Therapeutics", "PRODUCT"), ("Boston", "PLACE"), ("next Tuesday", "DATE")]


def test_the_analyze_step_and_workflows_use_it(db, cfg, nlp):
    rid = ingest.import_text(db, cfg, "pods", f"Ann|N|{LINE}", "Talk", "auto")
    jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    found = {e["name"]: e["type"] for e in db.rows("SELECT name, type FROM entity")}
    assert found["Sarah Connor"] == "PERSON" and found["Boston"] == "PLACE" and "Okay" not in found
    got = workflows.extract_rules(db, cfg, rid, {"terms": ["Tuesday|EVENT"]})
    assert {(e["name"], e["type"]) for e in got} >= {("Sarah Connor", "PERSON"), ("Tuesday", "EVENT")}


def test_without_spacy_the_rules_find_them(db, cfg, monkeypatch):
    cfg["analysis"]["entities"] = "spacy"
    monkeypatch.setattr(analyze, "_NLP", {})
    monkeypatch.setitem(sys.modules, "spacy", None)  # not installed here
    assert analyze.entity_finder(cfg).fell_back
    rid = ingest.import_text(db, cfg, "pods", f"Ann|N|{LINE}", "Talk", "auto")
    jid = jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    log = db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    assert any("isn't installed here (Settings → Components): the rules found the entities" in line for line in log)
    assert db.values("SELECT VALUE name FROM entity WHERE name = 'Dyno Therapeutics'")


def test_spacy_is_fetched_only_when_it_is_picked(cfg, monkeypatch):
    c, m = components.BY_ID["spacy"], machine.probe(cfg["data_dir"])
    assert not c.needed(cfg, m)
    cfg["analysis"]["entities"] = "spacy"
    assert c.needed(cfg, m) and c.serves(cfg, m) == {"analyze", "workflow"} and c.license == "MIT"
    # a model Lens doesn't fetch (its license isn't MIT, or it isn't one of spaCy's) is for people to install
    cfg["analysis"]["spacy_model"] = "fr_core_news_sm"
    monkeypatch.setattr(components, "importable", lambda *mods: mods == ("spacy",))
    with pytest.raises(components.Unavailable, match="isn't one Lens fetches"):
        c.fetch(cfg, m, None)


def test_spacy_s_english_model_finds_names():
    pytest.importorskip("en_core_web_sm")
    analyze._NLP.pop("en_core_web_sm", None)
    nlp = analyze.spacy_nlp("en_core_web_sm")
    assert "parser" not in nlp.pipe_names and "sentencizer" in nlp.pipe_names
    got = analyze.spacy_entities("Okay. We met Angela Merkel in Berlin on 3 May 2024. Then the BBC phoned.", nlp)
    assert ("Angela Merkel", "PERSON") in got and ("Berlin", "PLACE") in got and ("BBC", "ORG") in got
