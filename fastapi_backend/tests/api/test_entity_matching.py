"""Placing names by description with the model, and changing entities through the assistant."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, analyze, entities, entity_map, entity_setup, ingest, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.helpers import make_user, quiet

TEXT = "Alice|N|The Big Client called about Northwind Labs.\nBob|N|Globex wants a demo."


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    fake_llm.Handler.seen.clear()
    yield fake_llm.Handler
    srv.shutdown()


def _rec(db, cfg, folder):
    p = folder / "m.txt"
    p.write_text(TEXT)
    rid = ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_recording(db, cfg, rid)
    return rid


def _said(db, rid):
    names = {e["id"]: e["name"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity")}
    return {m["text"]: names[m["entity"]] for m in db.rows("SELECT text, entity FROM mentions WHERE recording = $r", r=rid)}


def test_self_organising_by_description(db, cfg, folder, llm):
    rid = _rec(db, cfg, folder)
    sid = store.ns_id(db, "pods")
    nw = [e["id"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity") if e["name"] == "Northwind Labs"][0]
    entities.describe(db, nw, "Our biggest customer, also called the big client")
    analyze.analyze_recording(db, cfg, rid)
    assert _said(db, rid)["Big Client"] == "Big Client"  # by name only: a new entity
    asked = len(llm.seen)

    entity_setup.save(db, sid, matching="model")
    db.q("DELETE entity WHERE name = 'Big Client'")
    analyze.analyze_recording(db, cfg, rid)
    said = _said(db, rid)
    assert said["Big Client"] == "Northwind Labs" and said["Globex"] == "Globex"  # placed by description; new
    prompt = llm.seen[-1]["messages"][-1]["content"]
    assert "Our biggest customer" in prompt and "Big Client" in prompt and len(llm.seen) == asked + 1
    assert "big client" in entities.aliases(db, [nw])[nw]  # learnt: the rules place it from now on
    analyze.analyze_recording(db, cfg, rid)
    assert len(llm.seen) == asked + 1  # every name is known now: the model isn't asked again
    assert _said(db, rid)["Big Client"] == "Northwind Labs"


def test_without_a_model_the_rules_decide(db, cfg, folder):
    cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"  # nothing listens
    rid = _rec(db, cfg, folder)
    entity_setup.save(db, store.ns_id(db, "pods"), matching="model")
    analyze.analyze_recording(db, cfg, rid)
    assert _said(db, rid)["Globex"] == "Globex"
    assert (
        entity_map.judge(db, {**cfg, "llm": {**cfg["llm"], "base_url": None}}, 1, entity_setup.DEFAULT, {"x": ("X", "TERM")}, [], ("new",))
        == {}
    )


def test_assistant_manages_entities(db, cfg, folder, llm):
    _rec(db, cfg, folder)
    sid = store.ns_id(db, "pods")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    me = {"id": 1, "email": "ed@x.io"}
    box = ai_tools.Toolbox(db, cfg, me, {sid}, {sid}, {}, None)
    setup = json.loads(box.call("entity_setup", {"namespace": "pods"})[0])
    assert setup["mode"] == "self-organising" and setup["types_kept"] == "all"
    assert box.call("entity_setup", {"namespace": "calls"})[1] == "entity_setup: no namespace called calls in scope"
    globex = [e["id"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity") if e["name"] == "Globex"][0]

    assert box.call("propose_entity_change", {"action": "describe", "entity_id": globex})[1].endswith("needs description or also_said_as")
    box.call(
        "propose_entity_change", {"action": "describe", "entity_id": globex, "description": "A prospect", "also_said_as": ["Globex Corp"]}
    )
    box.call("propose_entity_change", {"action": "define", "namespace": "pods", "new_name": "Initech", "new_type": "ORG"})
    box.call("propose_entity_change", {"action": "hide", "entity_id": globex})
    assert [a["summary"] for a in box.approvals] == [
        "Describe Globex as “A prospect” (also said as Globex Corp)",
        "Add Initech to the entities of pods",
        "Hide Globex",
    ]
    describe, define, hide = (a["id"] for a in box.approvals)
    assert db.one("SELECT description FROM $r", r=store.R("entity", globex)).get("description") is None  # not before approval
    ai_tools.approve(db, cfg, describe, me, {sid})
    d = entities.detail(db, globex, {sid})
    assert (d["description"], d["aliases"]) == ("A prospect", ["globex corp"])
    made = ai_tools.approve(db, cfg, define, me, {sid})
    assert entities.detail(db, made["entity"], {sid})["defined"] is True
    ai_tools.approve(db, cfg, hide, me, {sid})
    assert entities.detail(db, globex, {sid})["hidden"] is True
    found = json.loads(box.call("find_entities", {"query": "Initech"})[0])["entities"][0]
    assert found["defined"] is True
    box.call("propose_entity_change", {"action": "define", "namespace": "pods", "new_name": "Hooli"})
    with pytest.raises(PermissionError):  # approved by someone who can't change pods
        ai_tools.approve(db, cfg, box.approvals[-1]["id"], me, set())
