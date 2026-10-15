"""The types the rules give names (name_types.py), in analysis and in the upgrade that types the terms of older ones."""

from __future__ import annotations

import json

import pytest

from app.domain import activity, analyze, decide, entities, graph_history, ingest, migrations, name_types, store
from tests import fake_jev, fake_llm
from tests.api._assist import start_llm
from tests.helpers import quiet


def types(text, people=frozenset()):
    return dict(analyze.extract_entities(text, people=people))


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Yesterday I met Sarah Johnson in London to talk about the deal.", {"Sarah Johnson": "PERSON", "London": "PLACE"}),
        ("We flew from New York to Paris for the Web Summit.", {"New York": "PLACE", "Paris": "PLACE", "Web Summit": "EVENT"}),
        ("Barack Obama visited Kenya and Nigeria.", {"Barack Obama": "PERSON", "Kenya": "PLACE", "Nigeria": "PLACE"}),
        ("Then Dr. Lee from Stanford University joined Maria and Tom.", {"Dr. Lee": "PERSON", "Stanford University": "ORG"}),
        ("The Bank of England and King's College London replied.", {"Bank of England": "ORG", "King's College London": "ORG"}),
        ("We sold General Motors shares before the Clean Air Act.", {"General Motors": "ORG", "Clean Air Act": "WORK"}),
        ("We hiked Mount Fuji and the Hudson River after Christmas.", {"Mount Fuji": "PLACE", "Hudson River": "PLACE"}),
        ("Our CEO Maria Garcia met Tim Cook.", {"Maria Garcia": "PERSON", "Tim Cook": "PERSON"}),
        ("There's a startup called Lumen and an app called Notion.", {"Lumen": "ORG", "Notion": "PRODUCT"}),
        ("See you next Tuesday, maybe in March.", {"Tuesday": "DATE", "March": "DATE"}),
    ],
)
def test_names_get_the_type_their_words_give(text, want):
    got = types(text)
    assert {k: got.get(k) for k in want} == want


def test_and_no_longer_joins_two_names_into_one():
    assert set(types("Then Maria and Tom flew to Kenya and Nigeria.")) == {"Maria", "Tom", "Kenya", "Nigeria"}


def test_what_nothing_tells_is_a_term():
    got = types("We moved from Quillmark to Nomad, said our Acme folks.")
    assert got["Quillmark"] == "TERM" and got["Nomad"] == "TERM"
    assert types("Then I asked Will about it.")["Will"] == "TERM"  # a name, but mostly a word on its own
    assert types("Then I asked Will Smith about it.")["Will Smith"] == "PERSON"  # first of several words: a name


def test_well_known_organisations_and_products():
    got = types("We moved from Google Cloud to Kubernetes, and asked Claude, not Claude Monet.")
    assert (got["Kubernetes"], got["Claude"], got["Claude Monet"]) == ("PRODUCT", "PRODUCT", "PERSON")
    assert types("Then Google and Anthropic joined.") == {"Google": "ORG", "Anthropic": "ORG"}


def test_context_tells_a_person_from_a_place():
    assert types("He moved to Jordan last year.")["Jordan"] == "PLACE"
    assert types("Then Jordan said no.")["Jordan"] == "PERSON"
    assert types("We asked Nomad.", people=frozenset({"nomad"}))["Nomad"] == "PERSON"  # a named speaker


def test_guess_reads_the_lines_a_name_is_said_in():
    assert name_types.guess("Jordan", ["I flew to Jordan.", "Jordan is warm."]) == "PLACE"
    assert name_types.guess("Quillmark", ["We run Quillmark."]) is None
    assert name_types.guess("Lisbon") == "PLACE"  # no lines: the name alone


def test_one_type_per_name_in_a_recording():
    got = analyze.one_type([[("Lee", "TERM")], [("Lee", "PERSON")], [("Lee", "TERM")]])
    assert got == [[("Lee", "PERSON")], [("Lee", "PERSON")], [("Lee", "PERSON")]]


TALK = (
    "Alice|N|We met Sarah Johnson in Lisbon at the Web Summit.\n"
    "Bob|N|Then Quillmark came up, and Sarah Johnson said the Acme Corp deal is done.\n"
    "Alice|N|Thanks Bob, you said Lisbon again."
)


def _types(db, sid):
    return {r["name"]: r["type"] for r in db.rows("SELECT name, type FROM entity WHERE space = $s", s=sid)}


def test_analysis_types_people_places_and_events(db, cfg, folder):
    p = folder / "talk.txt"
    p.write_text(TALK)
    ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    got = _types(db, store.ns_id(db, "pods"))
    assert {k: got.get(k) for k in ("Sarah Johnson", "Lisbon", "Web Summit", "Acme Corp", "Quillmark", "Bob")} == {
        "Sarah Johnson": "PERSON",
        "Lisbon": "PLACE",
        "Web Summit": "EVENT",
        "Acme Corp": "ORG",
        "Quillmark": "TERM",
        "Bob": "PERSON",  # a named speaker
    }


def _old(db, cfg, folder, monkeypatch):
    """Entities as the old rules left them: every name they couldn't place a term, and one a person made a term."""
    with monkeypatch.context() as m:
        m.setattr(name_types, "kind", lambda *a, **k: None)
        p = folder / "talk.txt"
        p.write_text(TALK)
        ingest.import_transcript(db, cfg, "pods", p, log=quiet)
        analyze.analyze_pending(db, cfg, log=quiet)
    sid = store.ns_id(db, "pods")
    assert set(_types(db, sid).values()) == {"TERM"}
    web = db.values("SELECT VALUE record::id(id) FROM entity WHERE space = $s AND name = 'Web Summit'", s=sid)[0]
    entities.retype(db, [web], "PRODUCT")
    entities.retype(db, [web], "TERM")  # a person's pick: kept
    return sid


def test_the_upgrade_types_the_old_terms_and_keeps_what_people_chose(db, cfg, folder, monkeypatch):
    sid = _old(db, cfg, folder, monkeypatch)
    head = graph_history.head(db)
    entities.migrate_types(db)
    got = _types(db, sid)
    assert {k: got.get(k) for k in ("Sarah Johnson", "Lisbon", "Web Summit", "Quillmark", "Bob")} == {
        "Sarah Johnson": "PERSON",
        "Lisbon": "PLACE",
        "Web Summit": "TERM",  # a person set it
        "Quillmark": "TERM",  # nothing tells
        "Bob": "PERSON",  # a named speaker
    }
    ev = graph_history.versions(db, limit=1)[0]
    assert (ev["version"], ev["op"], ev["via"]) == (head + 1, "entity.typed", "system")  # in the history, to roll back
    once = _types(db, sid)
    entities.migrate_types(db)  # safe to run twice
    assert _types(db, sid) == once and graph_history.head(db) == head + 1
    assert graph_history.verify(db)["same"]


def test_the_upgrade_is_a_named_step(db):
    assert ("entity-types", entities.migrate_types) in migrations.steps()


# ---------- what the rules can't tell, a model may ----------
@pytest.fixture
def jev(cfg):
    srv = fake_jev.start(cfg)
    yield fake_jev.Handler
    srv.shutdown()


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


def _quillmark(question, *_):
    return "PRODUCT" if '"Quillmark"' in question else None


def _analysed(db, cfg, folder):
    p = folder / "talk.txt"
    p.write_text(TALK)
    ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    return _types(db, store.ns_id(db, "pods"))


def test_the_decision_model_types_what_the_rules_cant(db, cfg, folder, jev):
    jev.pick, jev.answer = _quillmark, {"type": "choice", "choice": "TERM", "confidence": 1.0, "probabilities": {"TERM": 1.0}}
    got = _analysed(db, cfg, folder)
    assert got["Quillmark"] == "PRODUCT" and got["Sarah Johnson"] == "PERSON"  # the rules' types stay
    (_, body), *more = jev.seen
    assert not more  # the names of a recording in one call
    assert [q["instructions"] for q in body["questions"].values()] == ['What kind of thing is "Quillmark"?']
    assert "Then Quillmark came up" in body["state"] and set(next(iter(body["questions"].values()))["criteria"]) == set(name_types.ASKED)
    analyze.analyze_pending(db, cfg, force=True, log=quiet)
    assert len(jev.seen) == 1  # an entity already: not asked again


def test_a_type_the_model_isnt_sure_of_stays_a_term(db, cfg, folder, jev):
    jev.answer = {"type": "choice", "choice": "ORG", "confidence": 0.5, "probabilities": {"ORG": 0.5, "PRODUCT": 0.4}}
    assert _analysed(db, cfg, folder)["Quillmark"] == "TERM"


def test_by_default_the_language_model_isnt_asked(db, cfg, folder, llm):
    llm.pick = _quillmark
    assert _analysed(db, cfg, folder)["Quillmark"] == "TERM"
    assert not [b for b in llm.seen if "answers" in json.dumps(b.get("response_format") or {})]


def test_with_typing_model_the_language_model_answers(db, cfg, folder, llm):
    cfg["analysis"]["typing"] = "model"
    llm.pick = _quillmark
    assert _analysed(db, cfg, folder)["Quillmark"] == "PRODUCT"


def test_with_typing_rules_nothing_is_asked(db, cfg, folder, jev):
    cfg["analysis"]["typing"] = "rules"
    jev.pick = _quillmark
    assert _analysed(db, cfg, folder)["Quillmark"] == "TERM" and not jev.seen


def test_when_the_model_fails_the_names_stay_terms(db, cfg, folder, jev, monkeypatch):
    jev.status = 500
    assert _analysed(db, cfg, folder)["Quillmark"] == "TERM"  # and the analysis is done
    monkeypatch.setattr(decide, "choose_all", lambda *a, **k: (_ for _ in ()).throw(activity.OverBudget("the budget is spent")))
    assert name_types.ask(cfg, {"quillmark": ("Quillmark", ["We run Quillmark."])}) == {}


def test_laya_is_asked_one_name_at_a_time(cfg, monkeypatch):
    cfg["decisions"] = {**cfg.get("decisions", {}), "engine": "laya"}
    calls = []

    def laya(cfg, questions, state):
        calls.append(len(questions))
        return {q: {"choice": "ORG", "confidence": 0.9} for q in questions}

    monkeypatch.setitem(decide.ENGINES, "laya", laya)
    names = {k: (k.title(), []) for k in ("quillmark", "nomad", "zentry")}
    assert name_types.ask(cfg, names) == {"quillmark": "ORG", "nomad": "ORG", "zentry": "ORG"}
    assert calls == [1, 1, 1]


def test_several_choices_in_one_call(cfg, jev, llm):
    asks = {"a": ("Which?", {"x": "X.", "y": "Y."}), "b": ("And this?", {"y": "Y.", "z": "Z."})}
    jev.pick = lambda q, s: "y"
    got = decide.choose_all(cfg, asks, {"s": 1})
    assert {k: (d["choice"], d["by"]) for k, d in got.items()} == {"a": ("y", "jev"), "b": ("y", "jev")} and len(jev.seen) == 1
    jev.status = 500  # the decision model fails: the language model answers, in one call too
    llm.pick = lambda q: "z" if q == "And this?" else None
    got = decide.choose_all(cfg, asks, {"s": 1})
    assert {k: (d["choice"], d["by"]) for k, d in got.items()} == {"a": ("x", "llm"), "b": ("z", "llm")}
    with pytest.raises(decide.Undecided):
        decide.choose_all(cfg, asks, {"s": 1}, language_model=False)
