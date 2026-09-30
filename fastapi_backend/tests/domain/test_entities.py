"""The entity index and curation at the domain level: suggestions, merges and their undo, links, moved mentions."""

from __future__ import annotations

import pytest

from app.domain import analyze, entities, ingest, render, store
from tests.helpers import quiet, seed

EXTRA = (
    "Alice|N|We met the Northwind Labs team and the North Wind Labs lawyers.\n"
    "Bob|N|Amazon Web Services hosts it; AWS bills monthly.\nAlice|N|Northwind Labs again, with Dyno Therapeutics."
)


@pytest.fixture
def spaces(db, cfg, folder):
    seed(db, cfg, folder)
    p = folder / "extra.txt"
    p.write_text(EXTRA)
    ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    return {store.ns_id(db, "pods"), store.ns_id(db, "calls")}


def eid(db, spaces, name, ns="pods"):
    return [x["id"] for x in entities.list_entities(db, spaces, q=name)["items"] if x["name"] == name and x["namespace"] == ns][0]


def test_suggestions_and_merge_undo(db, cfg, spaces):
    pairs = [(s["reason"], {s["a"]["name"], s["b"]["name"]}) for s in entities.suggestions(db, spaces)]
    assert ("same letters", {"Northwind Labs", "North Wind Labs"}) in pairs
    assert ("acronym", {"AWS", "Amazon Web Services"}) in pairs
    keep, other = eid(db, spaces, "Northwind Labs"), eid(db, spaces, "North Wind Labs")
    entities.not_same(db, eid(db, spaces, "AWS"), eid(db, spaces, "Amazon Web Services"))
    assert ("acronym", {"AWS", "Amazon Web Services"}) not in [
        (s["reason"], {s["a"]["name"], s["b"]["name"]}) for s in entities.suggestions(db, spaces)
    ]
    mid = entities.merge(db, keep, [other], "ed@x.io")
    assert entities.detail(db, keep, spaces)["aliases"] == ["north wind labs"]
    assert [m["id"] for m in entities.merges(db, spaces)] == [mid]
    entities.undo_merge(db, mid)
    assert {x["name"] for x in entities.list_entities(db, spaces, q="north")["items"]} == {"Northwind Labs", "North Wind Labs"}
    with pytest.raises(ValueError):
        entities.undo_merge(db, mid)  # once


def test_links_retype_and_moved_mentions(db, cfg, spaces):
    dyno, calls_dyno = eid(db, spaces, "Dyno Therapeutics"), eid(db, spaces, "Dyno Therapeutics", "calls")
    with pytest.raises(ValueError):
        entities.link(db, dyno, eid(db, spaces, "AWS"))  # same namespace: merge instead
    entities.link(db, dyno, calls_dyno)
    assert [x["namespace"] for x in entities.detail(db, dyno, spaces)["links"]] == ["calls"]
    assert entities.detail(db, dyno, {store.ns_id(db, "pods")})["links"] == []  # links only show what you can read
    entities.unlink(db, dyno, calls_dyno)
    with pytest.raises(ValueError):
        entities.retype(db, [dyno], "NOPE")
    entities.retype(db, [dyno], "PRODUCT")
    assert entities.detail(db, dyno, spaces)["type"] == "PRODUCT"

    aws = eid(db, spaces, "AWS")
    mention = entities.mentions(db, aws, spaces)["items"][0]["mention"]
    tid = entities.move_mention(db, mention, new_name="Amazon Web Services")
    assert tid == eid(db, spaces, "Amazon Web Services")
    analyze.analyze_pending(db, cfg, force=True, log=quiet)
    assert "AWS" not in [x["name"] for x in entities.list_entities(db, spaces)["items"]]  # the override survives re-analysis
    assert entities.detail(db, tid, spaces)["mentions"] == 2
    with pytest.raises(KeyError):
        entities.move_mention(db, "nope", remove=True)


def test_rename_preview_and_hide(db, cfg, spaces):
    dyno = eid(db, spaces, "Dyno Therapeutics")
    pv = entities.rename(db, dyno, "Dyno Therapeutics Inc", correct=True, dry_run=True)
    assert (pv["lines"], pv["recordings"]) == (5, 3)
    assert entities.detail(db, dyno, spaces)["name"] == "Dyno Therapeutics"  # a dry run changes nothing
    entities.hide(db, dyno, True, "noise")
    assert dyno not in [x["id"] for x in entities.list_entities(db, spaces)["items"]]
    assert dyno in [x["id"] for x in entities.list_entities(db, spaces, hidden=True)["items"]]
    assert "Dyno Therapeutics" not in [e["name"] for e in render.player_data(db, 1)["entities"]]  # hidden from players too
