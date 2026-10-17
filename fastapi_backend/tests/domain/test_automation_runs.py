"""Running automations (docs/automations.md, Runs): each new activity runs each automation it matches once, even
with two followers; the guards (switched on after, made by an automation, too deep, other namespaces, where); the If;
limits and budgets; intents done, waiting (handed to approvals) and scheduled (handed to timers), and settled later;
dry runs keep nothing; a person who lost access stops their automation; runs as automation.ran in the stream; tidy."""

from __future__ import annotations

import datetime as dt
import time

import pytest

from app.domain import actions, activity, auth, automation_hooks, automations, budgets, events, ingest, store, variables, workflows
from app.domain.actions import ActionSpec
from tests.helpers import make_user

SAMPLE = events.kind_info("asset.added")["sample"]


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b):
    return {"source": a, "target": b}


SAY = {
    "nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "New: {{ event.object.name }}"})],
    "edges": [edge("i", "a")],
}
NEW = [{"as": "new", "on": "event", "kinds": ["asset.added"]}]


@pytest.fixture
def env(db, cfg):
    events._seen.clear()
    events._claimed.clear()
    automations._swept["at"] = 0.0
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    root, ed = auth.find_account(db, "root@x.io"), auth.find_account(db, "ed@x.io")
    done = []

    def perform(db, cfg, intent, user, dry):
        done.append((intent["action"], intent["params"], actions.person(db, intent, user)["email"], dry))
        if intent["action"] == "shout":
            events.emit(
                db, "automation.limited", actor=dict(events.LENS), obj={"type": "Application", "name": "x"}, space=None, summary="shouted"
            )
        return {"ok": True}

    specs = [
        ActionSpec(
            name="tag",
            label="Tag an asset",
            group="Lens",
            params={"type": "object", "properties": {"object": {"type": "string"}}},
            required=("object",),
            effect="lens",
            perform=perform,
        ),
        ActionSpec(name="shout", label="Shout", group="Run", params={"type": "object", "properties": {}}, effect="read", perform=perform),
    ]
    for s in specs:
        actions.register(s)
    yield {
        "pods": pods,
        "calls": calls,
        "root": {**root, "admin": True},
        "ed": {**ed, "admin": False},
        "done": done,
    }
    for s in specs:
        actions._ACTIONS.pop(s.name, None)


def make(db, cfg, who, **over):
    return automations.create(db, cfg, {"name": "Say new assets", "triggers": NEW, "graph": SAY, **over}, who)["id"]


def tick():
    """Wait for the next second, as store.now() counts them."""
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)


def add(db, cfg, ns, title):
    return f"create-recording-{ingest.import_text(db, cfg, ns, f'Sam: {title}.', title=title)}"


def test_each_new_activity_runs_each_automation_it_matches_once(db, cfg, env):
    trip = make(
        db,
        cfg,
        env["root"],
        name="Trips",
        triggers=[{**NEW[0], "where": {"all": [{"path": "object.name", "op": "contains", "value": "trip"}]}}],
    )
    pods = make(db, cfg, env["ed"], name="Pods")
    assert automations.dispatch(db, cfg) == 0  # the first look marks where the follower starts
    tick()
    a1, a2, a3 = add(db, cfg, "pods", "School trip"), add(db, cfg, "pods", "Courier"), add(db, cfg, "calls", "Trip call")
    assert automations.dispatch(db, cfg) == 4
    events._seen.clear()  # the other process: nothing seen or claimed in its memory
    events._claimed.clear()
    assert automations.dispatch(db, cfg) == 0
    got = {(r["automation"], r["event"]) for r in db.rows("SELECT automation, event FROM automation_run")}
    assert got == {(trip, a1), (trip, a3), (pods, a1), (pods, a2)}
    [r] = automations.runs(db, pods, limit=1)
    run = automations.get_run(db, r["id"])
    assert run["status"] == "done" and run["counts"] == {"done": 1, "waiting": 0, "scheduled": 0, "failed": 0}
    assert run["trigger"] == "new" and run["cause_depth"] == 1 and run["space"] == env["pods"]
    assert run["intents"][0]["result"]["line"] == "New: Courier" and run["intents"][0]["status"] == "done"
    assert any("Write “New: Courier” to the run log" in line for line in run["log"]) and run["trace"]["a"]["status"] == "done"
    a = automations.get(db, pods)
    assert a["counters"]["runs"] == 2 and a["last_status"] == "done" and a["last_run"] == run["id"]
    assert [x["id"] for x in automations.caused(db, a1)] and {x["automation"] for x in automations.caused(db, a1)} == {trip, pods}
    # each run is in the stream, by its automation
    ran = events.get(db, cfg, f"ran-automation_run-{run['id']}", events.viewer(1, None, admin=True))
    assert ran["kind"] == "automation.ran" and ran["actor"]["name"] == "Pods" and ran["summary"] == "Pods ran: 1 done"
    assert ran["lens"]["cause"] == {"automation": pods, "run": run["id"], "depth": 1} and ran["space"] == env["pods"]


def test_a_saved_variable_counts_across_runs(db, cfg, env):
    count = node("count", "action", action="set_variable", params={"name": "seen", "op": "increment"})
    say = node("say", "action", action="log_line", params={"text": "{{ event.object.name }} is number {{ steps.count.value }}"})
    aid = make(
        db,
        cfg,
        env["ed"],
        name="Count",
        graph={"nodes": [node("i", "input"), count, say], "edges": [edge("i", "count"), edge("count", "say")]},
    )
    assert automations.dispatch(db, cfg) == 0
    tick()
    add(db, cfg, "pods", "One")
    add(db, cfg, "pods", "Two")
    assert automations.dispatch(db, cfg) == 2
    [seen] = variables.list_vars(db, automation=aid)
    assert seen["name"] == "seen" and seen["value"] == 2 and seen["updated_by"] == f"automation:{aid}"
    lines = {
        i["result"]["line"]
        for r in automations.runs(db, aid)
        for i in automations.get_run(db, r["id"])["intents"]
        if i["action"] == "log_line"
    }
    assert lines == {"One is number 1", "Two is number 2"}


def test_a_call_to_a_webhook_runs_its_automation(db, cfg, env):
    say = {"nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "Door: {{ event.object.payload.door }}"})]}
    say["edges"] = [edge("i", "a")]
    triggers = [{"as": "ping", "on": "webhook"}]
    made = automations.create(db, cfg, {"name": "Door", "triggers": triggers, "graph": say, "spaces": [env["pods"]]}, env["ed"])
    other = make(db, cfg, env["ed"], name="Other door", triggers=triggers)
    aid, (hook,) = made["id"], made["hooks"]
    assert automations.dispatch(db, cfg) == 0
    tick()
    token = hook["url"].rsplit("/", 1)[1]
    automation_hooks.receive(db, cfg, token, b'{"door": "open"}', "application/json")
    # only the automation whose hook was called runs, with what was sent marked as outside text
    assert automations.dispatch(db, cfg) == 1
    assert [r["automation"] for r in db.rows("SELECT automation FROM automation_run")] == [aid] and other != aid
    [r] = automations.runs(db, aid, limit=1)
    run = automations.get_run(db, r["id"])
    assert run["trigger"] == "ping" and run["intents"][0]["result"]["line"] == "Door: open" and run["intents"][0]["untrusted"]


def test_a_schedule_runs_its_automation_when_its_timer_fires(db, cfg, env):
    from app.domain import timers

    say = {"nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "It is {{ event.object.local }}"})]}
    say["edges"] = [edge("i", "a")]
    morning = [{"as": "m", "on": "schedule", "cron": "0 7 * * *"}]
    aid = automations.create(db, cfg, {"name": "Mornings", "triggers": morning, "graph": say, "spaces": [env["pods"]]}, env["ed"])["id"]
    at = dt.datetime.fromisoformat(timers.get(db, f"sched-{aid}-m")["at"])
    timers._idle["until"] = 0.0
    assert timers.due(db, cfg, now=at + dt.timedelta(seconds=3)) == 1
    [r] = automations.runs(db, aid, limit=1)
    run = automations.get_run(db, r["id"])
    assert run["trigger"] == "m" and run["status"] == "done" and run["intents"][0]["result"]["line"].startswith("It is ")
    assert run["space"] == env["pods"]
    # it waits for the next morning, and switching the automation off calls that off
    assert dt.datetime.fromisoformat(timers.get(db, f"sched-{aid}-m")["at"]) == at + dt.timedelta(days=1)
    automations.enable(db, cfg, aid, False, env["ed"])
    assert timers.get(db, f"sched-{aid}-m")["status"] == "cancelled"


def test_the_guards_leave_activities_out(db, cfg, env):
    aid = make(db, cfg, env["ed"], triggers=[{**NEW[0], "where": {"all": [{"path": "object.media", "op": "equals", "value": "audio"}]}}])
    a = automations.get(db, aid)
    who = automations._viewer(db, auth.get_account(db, env["ed"]["id"]))
    sec = automations.section(cfg)
    later = events.stamp(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5))

    def act(n, **over):
        return {**SAMPLE, "id": f"create-recording-{900 + n}", "published": later, "space": env["pods"], **over}

    assert automations._route(db, cfg, a, a["triggers"][0], act(1), who, sec) == 1
    assert automations._route(db, cfg, a, a["triggers"][0], act(1), who, sec) == 0  # claimed: once
    assert automations._route(db, cfg, a, a["triggers"][0], act(2, published="2020-01-01T00:00:00.000Z"), who, sec) == 0
    bot = {"type": "Application", "id": "lens:automation/9", "name": "Another"}
    assert automations._route(db, cfg, a, a["triggers"][0], act(3, actor=bot), who, sec) == 0
    deep = {**SAMPLE["lens"], "cause": {"automation": 9, "run": 1, "depth": 3}}
    assert automations._route(db, cfg, a, a["triggers"][0], act(4, lens=deep), who, sec) == 0
    assert automations._route(db, cfg, a, a["triggers"][0], act(5, space=env["calls"]), who, sec) == 0
    assert automations._route(db, cfg, a, a["triggers"][0], act(6, object={**SAMPLE["object"], "media": "video"}), who, sec) == 0
    mine = {**SAMPLE["lens"], "audience": "owner", "owner": env["root"]["id"]}
    assert automations._route(db, cfg, a, a["triggers"][0], act(7, lens=mine), who, sec) == 0
    let = {**a["triggers"][0], "from_automations": True}
    assert automations._route(db, cfg, a, let, act(8, actor=bot), who, sec) == 1


def test_the_if_limits_and_dry_runs(db, cfg, env):
    aid = make(db, cfg, env["root"], **{"if": {"all": [{"path": "event.object.media", "op": "equals", "value": "audio"}]}})
    assert automations.fire(db, cfg, aid, "new", {**SAMPLE, "object": {**SAMPLE["object"], "media": "video"}}) is None
    assert automations.get(db, aid)["counters"]["skipped"] == 1 and not db.rows("SELECT id FROM automation_run")
    # a dry run (a test) keeps, sends and counts nothing, and says what it would do
    got = automations.test(db, cfg, aid)
    assert got["status"] == "done" and got["run"] is None and got["intents"][0]["result"]["line"] == "New: Call with the school"
    assert not db.rows("SELECT id FROM automation_run") and not db.rows("SELECT id FROM automation_intent")
    assert (
        automations.test(db, cfg, aid, activity={**SAMPLE, "object": {**SAMPLE["object"], "media": "image"}})["why"] == "its If was false"
    )
    draft = automations.test(db, cfg, draft={"name": "x", "triggers": NEW, "graph": SAY}, user=env["ed"])
    assert draft["status"] == "done" and draft["intents"][0]["params"]["text"] == "New: Call with the school"
    # limits: per hour, cooldown, and every automation's per minute
    automations.update(db, cfg, aid, {"if": None, "limits": {"max_per_hour": 2}})
    runs = [automations.fire(db, cfg, aid, "new", SAMPLE) for _ in range(4)]
    assert runs[0] and runs[1] and runs[2:] == [None, None]
    a = automations.get(db, aid)
    assert a["counters"]["limited"] == 2 and a["counters"]["runs"] == 2
    said = db.rows("SELECT summary FROM event_log WHERE kind = 'automation.limited'")
    assert len(said) == 1 and "the most it may" in said[0]["summary"]  # once an hour
    cool = make(db, cfg, env["root"], name="Cool", limits={"cooldown": "1h"})
    assert automations.fire(db, cfg, cool, "new", SAMPLE) and automations.fire(db, cfg, cool, "new", SAMPLE) is None
    cfg["automations"] = {**automations.DEFAULTS, "max_runs_per_minute": 1}
    other = make(db, cfg, env["root"], name="Other")
    assert automations.fire(db, cfg, other, "new", SAMPLE) is None  # this minute's runs are spent
    cfg["automations"] = {**automations.DEFAULTS, "enabled": False}
    assert automations.run(db, cfg, other, "new", SAMPLE)["status"] == "off"


def test_a_run_over_budget_is_held_for_the_persons_pick(db, cfg, env):
    aid = make(db, cfg, env["root"])
    res = f"automation:{aid}"
    activity.record("out", "model.chat", [res], db=db, model="m", cost_usd=0.6)
    budgets.put(db, res, usd=0.5)
    held = automations.run(db, cfg, aid, "new", SAMPLE)
    assert held["status"] == "held" and "$0.6 of $0.5 this month" in held["why"]
    assert automations.run(db, cfg, aid, "new", SAMPLE)["run"] == held["run"]  # one held run waits; later ones add to it
    row = automations.get_run(db, held["run"])
    assert row["hold"]["missed"] == 1 and automations.get(db, aid)["last_status"] == "held"
    ran = automations.decide_held(db, cfg, held["run"], True, "root@x.io")
    assert automations.get_run(db, ran)["status"] == "done" and automations.get_run(db, held["run"])["status"] == "released"
    with pytest.raises(ValueError, match="isn't waiting on a budget"):
        automations.decide_held(db, cfg, held["run"], False)


def test_intents_wait_for_a_pick_or_a_time_and_settle_later(db, cfg, env, monkeypatch):
    asked, later = [], []
    fake = {
        ("approvals", "ask"): lambda db, cfg, intent, event=None: asked.append((intent, event)) or 77,
        ("timers", "later"): lambda db, intent, depth=1: later.append((intent, depth)) or {"timer": "t1", "at": intent["when"]},
    }
    monkeypatch.setattr(automations, "provided", lambda m, fn: fake.get((m, fn)))
    monkeypatch.setattr("app.domain.automation_nodes._timers", lambda: fake[("timers", "later")])
    g = {
        "nodes": [
            node("i", "input"),
            node("t", "action", action="tag", params={"object": "lens:recording/99"}),
            node("s", "action", action="log_line", params={"text": "after the tag"}),
            node("w", "action", action="log_line", params={"text": "in an hour"}, delay="1h"),
        ],
        "edges": [edge("i", "t"), edge("t", "s"), edge("i", "w")],
    }
    aid = make(db, cfg, env["ed"], graph=g)
    run_id = automations.fire(db, cfg, aid, "new", {**SAMPLE, "space": env["pods"]})
    run = automations.get_run(db, run_id)
    assert run["status"] == "waiting" and run["counts"] == {"done": 0, "waiting": 1, "scheduled": 1, "failed": 0}
    assert run["trace"]["s"]["status"] == "skipped" and any("waiting for your pick" in line for line in run["log"])
    by = {i["node"]: i for i in run["intents"]}
    assert by["t"]["status"] == "waiting" and by["t"]["result"] == {"approval": 77} and asked[0][0]["key"] == by["t"]["key"]
    assert asked[0][1]["id"] == SAMPLE["id"]  # the approval says which activity it's about
    assert by["w"]["status"] == "scheduled" and later[0][0]["key"] == by["w"]["key"] and later[0][1] == 1
    assert by["w"]["result"] == {"timer": "t1", "at": by["w"]["when"]}
    assert env["done"] == []  # nothing was done that should wait
    # approvals and timers report back
    automations.update_intent(db, run_id, by["t"]["key"], "done", {"ok": True})
    assert automations.get_run(db, run_id)["status"] == "waiting"
    automations.update_intent(db, run_id, by["w"]["key"], "failed", {"error": "gone"})
    assert automations.get_run(db, run_id)["status"] == "partly" and automations.get(db, aid)["last_status"] == "partly"
    with pytest.raises(KeyError):
        automations.update_intent(db, run_id + 1, by["t"]["key"], "done")
    with pytest.raises(ValueError):
        automations.update_intent(db, run_id, by["t"]["key"], "maybe")


def test_an_intent_is_approved_in_the_inbox_or_carried_out_at_its_time(db, cfg, env):
    from app.domain import approvals, timers

    g = {
        "nodes": [
            node("i", "input"),
            node("t", "action", action="tag", params={"object": "lens:recording/99"}),
            node("w", "action", action="shout", params={}, delay="1h"),
        ],
        "edges": [edge("i", "t"), edge("i", "w")],
    }
    aid = make(db, cfg, env["ed"], graph=g)
    act = events.get(db, cfg, add(db, cfg, "pods", "Field trip"), events.viewer(admin=True))
    run_id = automations.fire(db, cfg, aid, "new", act)
    by = {i["node"]: i for i in automations.get_run(db, run_id)["intents"]}
    # the tag on another object asks: an approval, for the person, about the activity
    approval = approvals.get(db, by["t"]["result"]["approval"])
    assert approval["status"] == "pending" and approval["origin"] == {"automation": aid, "run": run_id, "node": "t"}
    assert approval["why"]["activity"] == act["id"] and approvals._event_of(db, cfg, by["t"])["id"] == act["id"]
    got = approvals.decide(db, cfg, approval, {**env["ed"], "admin": False}, "approve")
    assert got["status"] == "done" and env["done"][0][0] == "tag" and env["done"][0][2] == "ed@x.io"
    # the shout waits an hour as a timer, then runs on its own (a read), and the run is done
    timer = timers.get(db, by["w"]["result"]["timer"])
    assert timer["status"] == "waiting" and by["w"]["result"]["at"] == timer["at"]
    assert automations.get_run(db, run_id)["status"] == "waiting"
    timers._idle["until"] = 0.0
    assert timers.due(db, cfg, now=dt.datetime.fromisoformat(timer["at"]) + dt.timedelta(seconds=3)) == 1
    run = automations.get_run(db, run_id)
    assert [x[0] for x in env["done"]] == ["tag", "shout"] and run["status"] == "done"
    assert {i["node"]: i["status"] for i in run["intents"]} == {"t": "done", "w": "done"}


def test_what_a_run_does_is_caused_by_it_and_acts_for_its_person(db, cfg, env):
    g = {"nodes": [node("i", "input"), node("s", "action", action="shout", params={})], "edges": [edge("i", "s")]}
    aid = make(db, cfg, env["ed"], graph=g)
    run_id = automations.fire(db, cfg, aid, "new", {**SAMPLE, "space": env["pods"]})
    assert env["done"] == [("shout", {}, "ed@x.io", False)]
    [row] = db.rows("SELECT cause FROM event_log WHERE summary = 'shouted'")
    assert row["cause"] == {"automation": aid, "run": run_id, "depth": 1}
    assert activity.costs(db, [f"automation_run:{run_id}"])  # its calls count for the run


def test_a_person_who_lost_access_stops_their_automation(db, cfg, env):
    aid = make(db, cfg, env["ed"])
    auth.set_role(db, env["ed"]["id"], env["pods"], "viewer")
    got = automations.run(db, cfg, aid, "new", {**SAMPLE, "space": env["pods"]})
    assert got["status"] == "failed" and "can no longer edit namespace pods" in got["why"]
    a = automations.get(db, aid)
    assert not a["enabled"] and a["last_status"] == "failed"
    assert "switched off" in automations.get_run(db, got["run"])["error"]


def test_tidy_keeps_recent_runs_and_removes_deleted_automations(db, cfg, env):
    aid = make(db, cfg, env["root"], limits={"max_per_hour": 100})
    ids = [automations.fire(db, cfg, aid, "new", SAMPLE) for _ in range(23)]
    old = "2020-01-01T00:00:00+00:00"
    db.q("UPDATE automation_run SET started_at = $o", o=old)
    keep = sorted(ids)[-automations.KEEP_LAST :]
    automations.tidy(db, cfg)
    assert sorted(r["id"] for r in automations.runs(db, aid, limit=100)) == keep
    assert {i["run"] for i in db.rows("SELECT run FROM automation_intent")} == set(keep)
    assert automations.tidy(db, cfg) is None  # once an hour
    gone = make(db, cfg, env["root"], name="Gone")
    wid = automations.get(db, gone)["workflow"]
    automations.remove(db, cfg, gone)
    db.q("UPDATE $r SET removed_at = $o", r=store.R("automation", gone), o=old)
    automations.tidy(db, cfg, now=dt.datetime.now(dt.UTC))
    assert not db.rows("SELECT id FROM $r", r=store.R("automation", gone))
    with pytest.raises(KeyError):
        workflows.get(db, wid)
    assert automations.get(db, aid)


def test_a_switched_off_follower_replays_nothing(db, cfg, env):
    assert automations.dispatch(db, cfg) == 0  # nothing switched on: the follower stays at now
    tick()
    add(db, cfg, "pods", "Before")
    tick()
    aid = make(db, cfg, env["root"])
    assert automations.dispatch(db, cfg) == 0  # what happened before it was switched on isn't replayed
    tick()
    add(db, cfg, "pods", "After")
    assert automations.dispatch(db, cfg) == 1 and automations.get(db, aid)["counters"]["runs"] == 1
