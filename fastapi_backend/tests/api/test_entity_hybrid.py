"""The hybrid mode (a few defined entities, then self-organising), and the model for fixed lists."""

from __future__ import annotations

import pytest

from app.domain import analyze, entity_map, entity_setup, ingest, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.helpers import quiet

TEXT = (
    "Alice|N|We met Northwind Labs in Paris on 3 March 2025.\n"
    "Bob|N|The acme rollout went well, Dyno Therapeutics paid.\n"
    "Alice|N|Acme Corp signed and Globex too. The Big Client called."
)


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    fake_llm.Handler.seen.clear()
    yield fake_llm.Handler
    srv.shutdown()


def _rec(db, cfg, folder):
    p = folder / "x.txt"
    p.write_text(TEXT)
    return ingest.import_transcript(db, cfg, "pods", p, log=quiet)


def _said(db, rid):
    names = {e["id"]: e["name"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity")}
    return {m["text"]: names[m["entity"]] for m in db.rows("SELECT text, entity FROM mentions WHERE recording = $r", r=rid)}


def _names(db):
    return {e["name"] for e in db.rows("SELECT name FROM entity")}


def test_hybrid(db, cfg, folder):
    rid = _rec(db, cfg, folder)
    sid = store.ns_id(db, "pods")
    analyze.analyze_recording(db, cfg, rid)  # self-organising first: Paris is an entity
    db.q("DELETE entity WHERE name IN ['Northwind Labs', 'Dyno Therapeutics']")
    entity_map.define(db, sid, "Acme Corp", "ORG", aliases=["Acme"])
    setup = entity_setup.save(db, sid, mode="hybrid", types=["ORG"])
    assert setup["mode"] == "hybrid"
    analyze.analyze_recording(db, cfg, rid)
    said = _said(db, rid)
    assert said["acme"] == "Acme Corp" and said["Acme Corp"] == "Acme Corp"  # the defined entity first
    assert said["Northwind Labs"] == "Northwind Labs" and said["Dyno Therapeutics"] == "Dyno Therapeutics"  # new: a type kept
    assert said["Globex"] == "Unknown" and said["Paris"] == "Unknown"  # not a type kept, though Paris is an entity
    assert {"Unknown", "Unlabeled", "Northwind Labs"} <= _names(db)
    ids = {e["name"]: e["id"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity")}
    analyze.analyze_recording(db, cfg, rid)  # again: the same entities, nothing made twice
    assert {e["name"]: e["id"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity")} == ids


def test_hybrid_by_description(db, cfg, folder, llm):
    rid = _rec(db, cfg, folder)
    sid = store.ns_id(db, "pods")
    nw = entity_map.define(db, sid, "Northwind Labs", "ORG", description="Our biggest customer, also called the big client")
    entity_setup.save(db, sid, mode="hybrid", types=["ORG"], matching="model")
    analyze.analyze_recording(db, cfg, rid)
    said = _said(db, rid)
    assert said["Big Client"] == "Northwind Labs"  # placed on the defined entity by its description
    assert said["Dyno Therapeutics"] == "Dyno Therapeutics"  # the model says new: an entity of its own
    assert said["Paris"] == "Unknown"
    assert "big client" in [a["key"] for a in db.rows("SELECT key FROM entity_alias WHERE entity = $e", e=nw)]
    prompt = llm.seen[-1]["messages"][-1]["content"]
    assert '"new"' in prompt and '"unknown"' in prompt and '"unlabeled"' not in prompt


def test_fixed_list_by_description(db, cfg, folder, llm):
    rid = _rec(db, cfg, folder)
    sid = store.ns_id(db, "pods")
    entity_map.define(db, sid, "Northwind Labs", "ORG", description="Our biggest customer, also called the big client")
    entity_setup.save(db, sid, mode="fixed", types=["ORG"], matching="model")
    analyze.analyze_recording(db, cfg, rid)
    said = _said(db, rid)
    assert said["Big Client"] == "Northwind Labs" and said["Northwind Labs"] == "Northwind Labs"
    assert said["Dyno Therapeutics"] == "Unlabeled" and said["Globex"] == "Unknown"  # the rules decide the rest
    assert "Dyno Therapeutics" not in _names(db)  # nothing is made in the fixed mode
    prompt = llm.seen[-1]["messages"][-1]["content"]
    assert '"unlabeled"' in prompt and '"new"' not in prompt
