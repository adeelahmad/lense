"""Conditions in running automations (docs/automations.md, Running automations): an automation's If looks back over
the stream (`seen`) in its own namespaces, as its person may see; `changed` in a trigger's where compares each
activity with the last one; the If and a condition node testing the same thing agree within a run; a dry run keeps
nothing."""

from __future__ import annotations

import pytest

from app.domain import auth, automations, events, store
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
    events._seen.clear()
    events._claimed.clear()
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    yield {"pods": pods, "calls": calls, "ed": {**auth.find_account(db, "ed@x.io"), "admin": False}}
    events._KINDS.clear()
    events._KINDS.update(before)


def mail(db, cfg, sender, space, name="Trip"):
    aid = events.emit(
        db, "test_mail.came", actor=events.LENS, obj={"type": "Note", "name": name, "from": [sender]}, space=space, summary=name
    )
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
