"""Automations that fire when an entity is mentioned (docs/automations.md, Operators: mentions): a processed asset is
tagged with up to fifty of its entities, each with its type, and a trigger's where finds any entity of a type, a name
with * and ? wildcards, or an id; one that names neither doesn't fire, and trees saved before work as they did."""

from __future__ import annotations

import time

import pytest

from app.domain import automations, events, events_content, ingest, jobs, match, store
from tests.helpers import PODS1, drain, make_user

PODS = "\n".join(f"[00:{i:02d}] {line.split('|')[0]}: {line.split('|')[2]}" for i, line in enumerate(PODS1.splitlines()))
TAGS = [
    {"type": "Mention", "id": "lens:entity/9", "name": "Acme Robotics", "entityType": "ORG"},
    {"type": "Mention", "id": "lens:entity/12", "name": "Sam Lee", "entityType": "PERSON"},
]
SAY = {
    "nodes": [
        {"id": "i", "type": "input", "config": {}},
        {"id": "a", "type": "action", "config": {"action": "log_line", "params": {"text": "Mentioned in {{ event.object.name }}"}}},
    ],
    "edges": [{"source": "i", "target": "a"}],
}


def fires(value, activity=None):
    tree = match.check({"op": "mentions", "value": value})
    return match.test(tree, activity or {"tag": TAGS})


def test_a_wildcard_a_type_and_an_id_find_a_mention():
    assert fires({"name": "Acme*"}) and fires("acme*") and fires({"name": "*lee"}) and fires({"name": "Sam L?e"})
    assert fires({"type": "person"}) and fires({"type": "Organisation"}) and fires({"type": ["PLACE", "ORG"]})
    assert fires({"type": "ORG", "name": "Acme*"}) and fires({"id": "lens:entity/12"}) and fires({"id": 9})
    assert fires({"name": "acme robotics"})  # no wildcard: the whole name, in any case
    # what doesn't fit doesn't fire: another name, a part of a name without a star, a type on another entity
    assert not fires({"name": "Globex*"}) and not fires({"name": "Acme"}) and not fires({"type": "PLACE"})
    assert not fires({"type": "PERSON", "name": "Acme*"}) and not fires({"id": "lens:entity/4"})
    assert not fires({"type": "PERSON"}, {"tag": []}) and not fires({"name": "*"}, {"kind": "asset.processed"})
    # with a path it reads the tags there (the If's event), and under not it passes when none fits
    tree = match.check({"not": {"path": "event.tag", "op": "mentions", "value": {"name": "Globex*"}}})
    assert match.test(tree, {"event": {"tag": TAGS}})


def test_a_glob_is_linear_and_checks_refuse_what_it_cant_use():
    assert match.glob("*a*a*a*a*a*a*a*a*a*b", "a" * 5000) is False  # many stars, no blow-up
    assert match.glob("[x]*", "[x] y") and not match.glob("[x]*", "x y")  # only * and ? are wildcards
    for bad in ({}, {"name": ""}, {"type": "Planet"}, {"id": "entity 9"}, {"name": "x" * 201}, {"who": "Sam"}, 4):
        with pytest.raises(ValueError):
            match.check({"op": "mentions", "value": bad})
    assert match.check({"op": "mentions", "value": {"type": "organization"}})["value"] == {"type": ["ORG"]}
    assert match.describe(match.check({"op": "mentions", "value": {"type": "person", "name": "Sam*"}})) == (
        "it mentions a person named like “Sam*”"
    )


def test_trees_saved_before_still_pass_as_they_did():
    old = {
        "all": [
            {"path": "tag.0.name", "op": "equals", "value": "Acme Robotics"},
            {"path": "object.name", "op": "contains", "value": "call"},
        ]
    }
    tree = match.check(old)
    assert tree == old and match.test(tree, {"tag": TAGS, "object": {"name": "Sales call"}})
    assert not match.test(tree, {"tag": TAGS[1:], "object": {"name": "Sales call"}})


@pytest.fixture
def root(db):
    events._seen.clear()
    events._claimed.clear()
    events_content.forget_names()
    automations._swept["at"] = 0.0
    make_user(db, "root@x.io", "root password 1", admin=True)
    from app.domain import auth

    yield {**auth.find_account(db, "root@x.io"), "admin": True}
    events_content.forget_names()


def test_an_automation_fires_on_any_entity_of_a_type_or_a_name_pattern_and_not_on_others(db, cfg, root):
    def on(name, value):
        trigger = {"as": "said", "on": "event", "kinds": ["asset.processed"], "where": {"op": "mentions", "value": value}}
        return automations.create(db, cfg, {"name": name, "triggers": [trigger], "graph": SAY, "enabled": True}, root)["id"]

    by_name, by_type, other = on("Dyno", {"name": "dyno*"}), on("Any org", {"type": "ORG"}), on("Globex", {"name": "Globex*"})
    assert automations.dispatch(db, cfg) == 0  # the first look marks where the follower starts
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)
    rid = ingest.import_text(db, cfg, "pods", PODS, title="Capsid episode")
    jobs.enqueue(db, rid, ["analyze"], by="root@x.io")
    drain(db, cfg)
    [a] = [x for x in events.page(db, cfg, events.viewer(1, None, admin=True), flt={"kinds": ["asset.processed"]})["items"]]
    dyno = next(t for t in a["tag"] if t["name"] == "Dyno Therapeutics")
    assert dyno["entityType"] == "ORG" and dyno["id"].startswith("lens:entity/")
    automations.dispatch(db, cfg)
    ran = {r["automation"] for r in db.rows("SELECT automation, event FROM automation_run")}
    assert ran == {by_name, by_type} and other not in ran
    [r] = automations.runs(db, by_name, limit=1)
    assert automations.get_run(db, r["id"])["intents"][0]["result"]["line"] == "Mentioned in Capsid episode"


def test_a_processed_asset_is_tagged_with_up_to_fifty_entities(db, cfg, monkeypatch):
    rid = ingest.import_text(db, cfg, "pods", "Sam: hello.", title="Many names")
    counts = []
    for n in range(60):
        e = db.next_id("entity")
        db.q("CREATE $r CONTENT $d", r=store.R("entity", e), d={"name": f"Person {n}", "type": "PERSON", "key": f"person {n}"})
        counts.append({"recording": rid, "entity": e, "n": 60 - n})  # the first is the most mentioned
    rows = db.rows
    monkeypatch.setattr(db, "rows", lambda sql, **v: counts if "FROM mentions" in sql else rows(sql, **v))
    tags = events_content.entity_tags(db, [rid])[rid]
    assert len(tags) == events.MAX_TAGS == 50 and tags[0]["name"] == "Person 0" and tags[-1]["name"] == "Person 49"
    assert all(t["entityType"] == "PERSON" for t in tags)
