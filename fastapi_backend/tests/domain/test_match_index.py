"""Which automations an activity could start (docs/automations.md, Running automations): compile_triggers indexes the
stream triggers of the automations switched on by kind, with the kinds under them, and gives each source the actors
its triggers name, which events.follow hands that source alone; the dispatcher follows the stream with it."""

from __future__ import annotations

import pytest

from app.domain import auth, automations, events, match
from tests.helpers import make_user


class Readings(events.Source):
    """A source that records the hint each read was given."""

    name = "test_readings"

    def __init__(self):
        self.hints = []

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        self.hints.append(hint)
        return []

    def page(self, db, cfg, before, viewer, flt, limit):
        return []


@pytest.fixture(autouse=True)
def kinds():
    before, srcs = dict(events._KINDS), dict(events._SOURCES)
    events.kind("test_reading.taken", label="Reading", source="test_readings", as_type="Update", object_type="Note", noisy=True)
    events.kind(
        "test_reading.alarmed", label="Alarm", source="test_readings", as_type="Announce", object_type="Note", parent="test_reading.taken"
    )
    events.kind("test_mail.came", label="Mail came", source="log", as_type="Create", object_type="Note")
    src = events.register_source(Readings())
    yield src
    events._KINDS.clear()
    events._KINDS.update(before)
    events._SOURCES.clear()
    events._SOURCES.update(srcs)


def sensor(*ids):
    if len(ids) == 1:
        return {"all": [{"path": "actor.id", "op": "equals", "value": ids[0]}, {"path": "object.value", "op": "gt", "value": 3}]}
    return {"path": "actor.id", "op": "in", "value": list(ids)}


def test_the_index_lists_triggers_by_kind_and_hints_by_source():
    autos = [
        {"id": 1, "triggers": [{"as": "temp", "on": "event", "kinds": ["test_reading.taken"], "where": sensor("lens:sensor/5")}]},
        {
            "id": 2,
            "triggers": [
                {"as": "alarm", "on": "event", "kinds": ["test_reading.alarmed"], "where": sensor("lens:sensor/6", "lens:sensor/7")}
            ],
        },
        {
            "id": 3,
            "triggers": [
                {"as": "mail", "on": "event", "kinds": ["test_mail.came"]},
                {"as": "morning", "on": "schedule", "cron": "0 7 * * *"},
            ],
        },
        {"id": 4, "enabled": False, "triggers": [{"as": "any", "on": "event", "kinds": ["test_reading.taken"]}]},
        {"id": 5, "triggers": [{"as": "gone", "on": "event", "kinds": ["test_gone.away"]}]},
    ]
    index = match.compile_triggers(autos)
    assert index["kinds"] == ["test_mail.came", "test_reading.alarmed", "test_reading.taken"]
    assert [(c["automation"], c["as"]) for c in index["by_kind"]["test_reading.alarmed"]] == [(1, "temp"), (2, "alarm")]
    assert [(c["automation"], c["as"]) for c in match.candidates(index, {"kind": "test_reading.taken"})] == [(1, "temp")]
    assert match.candidates(index, {"kind": "test_mail.came"})[0]["trigger"]["as"] == "mail"
    assert match.candidates(index, {"kind": "asset.added"}) == []
    assert index["hint"] == {"test_readings": {"actors": ["lens:sensor/5", "lens:sensor/6", "lens:sensor/7"]}}
    # one trigger on a kind of that source that takes any sensor: no narrowing for it
    autos[3]["enabled"] = True
    assert match.compile_triggers(autos)["hint"] == {}
    assert match.compile_triggers([])["kinds"] == []


def test_actor_narrowing_reads_the_where_tree():
    def actors(where):
        return match._actors(match.check(where))

    assert actors(sensor("a")) == {"a"}
    assert actors({"any": [sensor("a"), sensor("b", "c")]}) == {"a", "b", "c"}
    assert actors({"any": [sensor("a"), {"path": "object.value", "op": "gt", "value": 1}]}) is None
    assert actors({"all": [sensor("a", "b"), sensor("b", "c")]}) == {"b"}
    assert actors({"not": sensor("a")}) is None
    assert actors({"path": "actor.id", "op": "contains", "value": "sensor"}) is None


def test_follow_gives_each_source_its_own_hint(db, cfg, kinds):
    events.follow(db, cfg, "test_index")  # the first look marks where it starts
    hint = match.compile_triggers(
        [{"id": 1, "triggers": [{"as": "t", "on": "event", "kinds": ["test_reading.taken"], "where": sensor("lens:sensor/5")}]}]
    )["hint"]
    events.follow(db, cfg, "test_index", kinds=["test_reading.taken", "test_mail.came"], hint=hint)
    assert kinds.hints and all(h == {"actors": ["lens:sensor/5"]} for h in kinds.hints)
    kinds.hints.clear()
    events.follow(db, cfg, "test_index", kinds=["test_reading.taken"])
    assert kinds.hints and all(h is None for h in kinds.hints)


def test_the_dispatcher_follows_with_the_index(db, cfg, kinds):
    make_user(db, "root@x.io", "root password 1", admin=True)
    root = {**auth.find_account(db, "root@x.io"), "admin": True}
    say = {
        "nodes": [
            {"id": "i", "type": "input", "config": {}},
            {"id": "a", "type": "action", "config": {"action": "log_line", "params": {"text": "hot"}}},
        ],
        "edges": [{"source": "i", "target": "a"}],
    }
    trigger = {"as": "t", "on": "event", "kinds": ["test_reading.taken"], "where": sensor("lens:sensor/5")}
    automations.create(db, cfg, {"name": "Hot", "triggers": [trigger], "graph": say}, root)
    automations.dispatch(db, cfg)  # the first look marks where the follower starts
    kinds.hints.clear()
    automations.dispatch(db, cfg)
    assert kinds.hints and all(h == {"actors": ["lens:sensor/5"]} for h in kinds.hints)
