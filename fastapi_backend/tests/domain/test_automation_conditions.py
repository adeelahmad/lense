"""Conditions and joins in running automations (docs/automations.md, Running automations): an automation's If looks
back over the stream (`seen`) in its own namespaces, as its person may see; `changed` in a trigger's where compares
each activity with the last one; the If and a condition node testing the same thing agree within a run; a dry run
keeps nothing; an absent join waits on a real timer, and switching the automation off drops what it waited on."""

from __future__ import annotations

import datetime as dt
import time

import pytest

from app.domain import auth, automations, events, store, timers
from tests.helpers import make_user


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


SAY = {
    "nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "Mail from {{ event.object.from.0 }}"})],
    "edges": [{"source": "i", "target": "a"}],
}
MAIL = [{"as": "mail", "on": "event", "kinds": ["test_mail.came"]}]


@pytest.fixture
def env(db, cfg):
    before = dict(events._KINDS)
    events.kind("test_mail.came", label="Mail came", source="log", as_type="Create", object_type="Note")
    events.kind("test_mail.sent", label="Mail sent", source="log", as_type="Create", object_type="Note")
    events._seen.clear()
    events._claimed.clear()
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    yield {"pods": pods, "calls": calls, "ed": {**auth.find_account(db, "ed@x.io"), "admin": False}}
    events._KINDS.clear()
    events._KINDS.update(before)


def tick():
    """Wait for the next second, as store.now() counts them (the follower starts after its first look's second)."""
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)


def mail(db, cfg, sender, space, name="Trip", kind="test_mail.came"):
    aid = events.emit(db, kind, actor=events.LENS, obj={"type": "Note", "name": name, "from": [sender]}, space=space, summary=name)
    return events.get(db, cfg, aid, events.viewer(1, None, admin=True))


def make(db, cfg, who, **over):
    return automations.create(db, cfg, {"name": "Mail", "triggers": MAIL, "graph": SAY, "spaces": [over.pop("space")], **over}, who)["id"]


def test_the_if_looks_back_in_the_automations_namespaces(db, cfg, env):
    again = {"seen": {"kinds": ["test_mail.came"], "within": "1d", "same": "object.from"}}
    aid = make(db, cfg, env["ed"], space=env["pods"], **{"if": again})
    assert automations.fire(db, cfg, aid, "mail", mail(db, cfg, "office@stmarys.org", env["pods"])) is None  # the first one
    mail(db, cfg, "club@example.com", env["calls"])  # a namespace the automation isn't in
    assert automations.fire(db, cfg, aid, "mail", mail(db, cfg, "club@example.com", env["pods"])) is None
    run = automations.fire(db, cfg, aid, "mail", mail(db, cfg, "office@stmarys.org", env["pods"]))
    assert run and automations.get_run(db, run)["intents"][0]["result"]["line"] == "Mail from office@stmarys.org"
    assert automations.get(db, aid)["counters"]["skipped"] == 2


def test_changed_in_a_where_compares_with_the_last_activity(db, cfg, env):
    where = {"path": "object.name", "op": "changed"}
    aid = make(db, cfg, env["ed"], space=env["pods"], triggers=[{**MAIL[0], "where": where}])
    a = automations.get(db, aid)
    who = automations._viewer(db, auth.get_account(db, env["ed"]["id"]))
    sec = automations.section(cfg)
    names = ["Open", "Open", "Closed", "Closed", "Open"]
    got = [automations._route(db, cfg, a, a["triggers"][0], mail(db, cfg, "door", env["pods"], n), who, sec) for n in names]
    assert got == [0, 0, 1, 0, 1]


def test_the_if_and_a_condition_node_agree_and_dry_runs_keep_nothing(db, cfg, env):
    changed = {"path": "event.object.name", "op": "changed"}
    graph = {
        "nodes": [*SAY["nodes"][:1], node("c", "condition", match=changed), SAY["nodes"][1]],
        "edges": [{"source": "i", "target": "c"}, {"source": "c", "target": "a", "port": "yes"}],
    }
    aid = make(db, cfg, env["ed"], space=env["pods"], graph=graph, **{"if": changed})
    assert automations.fire(db, cfg, aid, "mail", mail(db, cfg, "door", env["pods"], "Open")) is None  # nothing to compare with
    # a dry run of a new name passes, but keeps nothing: the next real run still compares with Open
    assert automations.test(db, cfg, aid, activity=mail(db, cfg, "door", env["pods"], "Closed"))["status"] == "done"
    run = automations.fire(db, cfg, aid, "mail", mail(db, cfg, "door", env["pods"], "Closed"))
    assert run and automations.get_run(db, run)["trace"]["a"]["status"] == "done"  # the node saw the If's change
    assert automations.fire(db, cfg, aid, "mail", mail(db, cfg, "door", env["pods"], "Closed")) is None


def test_an_absent_join_waits_on_a_timer_and_switching_off_drops_it(db, cfg, env):
    triggers = [*MAIL, {"as": "reply", "on": "event", "kinds": ["test_mail.sent"]}]
    join = {"mode": "absent", "after": "mail", "missing": "reply", "within": "1h", "key": "object.name"}
    aid = make(db, cfg, env["ed"], space=env["pods"], triggers=triggers, join=join)
    assert automations.dispatch(db, cfg) == 0  # the first look marks where the follower starts
    tick()
    mail(db, cfg, "office@stmarys.org", env["pods"], "Trip")
    mail(db, cfg, "club@example.com", env["pods"], "Club")
    assert automations.dispatch(db, cfg) == 0
    mail(db, cfg, "me@home", env["pods"], "Club", kind="test_mail.sent")  # Club was answered in time
    assert automations.dispatch(db, cfg) == 0
    waiting = db.rows("SELECT key, status FROM timer WHERE automation = $a ORDER BY status", a=aid)
    assert [r["status"] for r in waiting] == ["cancelled", "waiting"]
    soon = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=61)
    assert timers.due(db, cfg, now=soon) == 1
    [r] = automations.runs(db, aid, limit=5)
    run = automations.get_run(db, r["id"])
    assert run["trigger"] == "mail" and run["intents"][0]["result"]["line"] == "Mail from office@stmarys.org"
    assert not db.rows("SELECT id FROM automation_state WHERE kind = 'join'")
    # switched off while one waits: its timer is called off and what it waited on goes with it
    mail(db, cfg, "office@stmarys.org", env["pods"], "Outing")
    assert automations.dispatch(db, cfg) == 0 and db.rows("SELECT id FROM automation_state WHERE kind = 'join'")
    automations.enable(db, cfg, aid, on=False)
    assert not db.rows("SELECT id FROM automation_state WHERE kind = 'join'")
