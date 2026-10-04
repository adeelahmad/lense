"""The entity graph's history: every change is an event with who, through what and why; the graph as of any version
matches what it was then; diffs, an entity's history and named versions."""

from __future__ import annotations

import pytest

from app.domain import analyze, entities, entity_map, ingest, organize, store
from app.domain import graph_history as gh
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


def now(db):
    return gh.state_at(db, gh.head(db))


def test_analysis_is_recorded_and_the_start_is_empty(db, spaces):
    found = gh.versions(db)
    assert found and {v["op"] for v in found} == {"analysis"}
    assert {v["via"] for v in found} == {"analysis"} and {v["actor"] for v in found} == {"analysis"}
    assert all(v["origin"]["recording"] for v in found)
    assert gh.state_at(db, 0)["entity"] == {}  # before the first analysis there was nothing
    assert now(db) == gh.live(db)


def test_analysing_again_changes_nothing(db, cfg, spaces):
    again = gh.head(db)
    analyze.analyze_pending(db, cfg, force=True, log=quiet)
    assert gh.head(db) == again  # nothing new was found, so nothing was recorded


def test_every_curation_is_an_event_and_every_version_replays(db, cfg, spaces):
    v0 = gh.head(db)
    dyno, calls_dyno = eid(db, spaces, "Dyno Therapeutics"), eid(db, spaces, "Dyno Therapeutics", "calls")
    keep, other = eid(db, spaces, "Northwind Labs"), eid(db, spaces, "North Wind Labs")
    aws, amazon = eid(db, spaces, "AWS"), eid(db, spaces, "Amazon Web Services")
    snaps = {v0: now(db)}
    with gh.acting(actor="ed@x.io", via="web", why="tidying up"):
        entities.rename(db, dyno, "Dyno Therapeutics Inc")
        snaps[gh.head(db)] = now(db)
        entities.describe(db, dyno, "A gene therapy company")
        entities.retype(db, [dyno], "PRODUCT")
        entities.hide(db, aws, True, "noise")
        snaps[gh.head(db)] = now(db)
        entities.link(db, dyno, calls_dyno)
        entities.not_same(db, aws, amazon)
        mid = entities.merge(db, keep, [other], "ed@x.io")
        snaps[gh.head(db)] = now(db)
        entities.undo_merge(db, mid)
        entities.unlink(db, dyno, calls_dyno)
        snaps[gh.head(db)] = now(db)
    assert [v["op"] for v in reversed(gh.versions(db, limit=9))] == [
        "entity.rename",
        "entity.describe",
        "entity.retype",
        "entity.hide",
        "link.add",
        "distinct.add",
        "entity.merge",
        "entity.unmerge",
        "link.remove",
    ]
    top = gh.versions(db, limit=1)[0]
    assert (top["actor"], top["via"], top["why"]) == ("ed@x.io", "web", "tidying up")
    for v, state in snaps.items():  # walking back from today gives exactly what the graph was then
        assert gh.state_at(db, v) == state, v
    merged = gh.event(db, [v for v in gh.versions(db) if v["op"] == "entity.merge"][0]["version"])
    assert merged["origin"]["merge"] == mid
    assert ("entity", other) in {(o["t"], o["k"]) for o in merged["ops"] if o.get("a") is None}  # the other one went
    assert "north wind labs" in {o["a"]["key"] for o in merged["ops"] if o["t"] == "entity_alias" and o.get("a")}


def test_diff_history_and_scopes(db, cfg, spaces):
    pods = store.ns_id(db, "pods")
    v0 = gh.head(db)
    dyno, calls_dyno = eid(db, spaces, "Dyno Therapeutics"), eid(db, spaces, "Dyno Therapeutics", "calls")
    entities.rename(db, dyno, "Dyno")
    entities.link(db, dyno, calls_dyno)
    entities.hide(db, eid(db, spaces, "AWS"), True)
    d = gh.diff(db, v0, gh.head(db))
    changed = {c["id"]: c["fields"] for c in d["entities"]["changed"]}
    assert changed[dyno]["name"] == ["Dyno Therapeutics", "Dyno"]
    assert d["links"]["added"] == [{"a": min(dyno, calls_dyno), "b": max(dyno, calls_dyno), "names": d["links"]["added"][0]["names"]}]
    assert d["events"] == 3
    only_pods = gh.diff(db, v0, gh.head(db), {pods})
    assert only_pods["links"]["added"] == []  # one end isn't readable
    assert dyno in {c["id"] for c in only_pods["entities"]["changed"]}
    # the link touched both namespaces: only someone who reads both sees that event
    assert "link.add" in [v["op"] for v in gh.versions(db, spaces)]
    assert "link.add" not in [v["op"] for v in gh.versions(db, {pods})]
    with pytest.raises(KeyError):
        gh.event(db, [v for v in gh.versions(db) if v["op"] == "link.add"][0]["version"], {pods})
    mine = gh.versions(db, entity=dyno)
    assert [v["op"] for v in mine][:2] == ["link.add", "entity.rename"]
    assert mine[0]["names"][str(dyno)] == "Dyno"
    assert gh.as_of(db, v0, {pods})["entities"][0]["space"] == pods
    assert "Dyno Therapeutics" in {e["name"] for e in gh.as_of(db, v0, {pods})["entities"]}
    with pytest.raises(ValueError):
        gh.state_at(db, gh.head(db) + 1)


def test_moved_mentions_defined_entities_and_graph_changes(db, cfg, spaces):
    aws = eid(db, spaces, "AWS")
    mention = entities.mentions(db, aws, spaces)["items"][0]["mention"]
    tid = entities.move_mention(db, mention, new_name="Amazon Web Services")
    ev = gh.versions(db, limit=1)[0]
    assert ev["op"] == "mention.move" and set(ev["entities"]) >= {aws, tid}
    assert ev["origin"]["mention"]["from"] == aws
    pods = store.ns_id(db, "pods")
    new = entity_map.define(db, pods, "Lens", "PRODUCT", "Our archive", ["the lens app"])
    row = [v for v in gh.versions(db, limit=3) if v["op"] == "entity.define"][0]
    full = gh.event(db, row["version"])
    assert {(o["t"], o["k"]) for o in full["ops"]} >= {("entity", new), ("entity_alias", f"{pods}:the lens app")}
    a, b = eid(db, spaces, "Northwind Labs"), eid(db, spaces, "North Wind Labs")
    cid, status = organize.propose(db, "merge", a, b, "same company", apply=True, user="ed@x.io")
    applied = gh.versions(db, limit=1)[0]
    assert (applied["op"], applied["origin"]["graph_change"], applied["why"]) == ("merge.apply", cid, "same company")
    organize.undo(db, cid, "ed@x.io")
    undone = gh.versions(db, limit=1)[0]
    assert undone["op"] == "merge.undo" and undone["origin"]["graph_change"] == cid


def test_named_versions(db, cfg, spaces):
    v = gh.head(db)
    assert gh.tag(db, "before the cleanup", by="ed@x.io") == {"name": "before the cleanup", "version": v}
    entities.rename(db, eid(db, spaces, "Dyno Therapeutics"), "Dyno")
    assert gh.resolve(db, "Before the  cleanup") == v
    assert gh.resolve(db, "head") == gh.head(db) == v + 1
    assert gh.resolve(db, "v3") == 3
    assert gh.versions(db, limit=2)[1]["tags"] == ["before the cleanup"]
    with pytest.raises(ValueError):
        gh.tag(db, "x" * 81)
    with pytest.raises(ValueError):
        gh.tag(db, "later", gh.head(db) + 5)
    gh.untag(db, "before the cleanup")
    with pytest.raises(KeyError):
        gh.resolve(db, "before the cleanup")
