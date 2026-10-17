"""Actions that wait: later() keeps an intent as a timer; when its time comes its risk is worked out again from the
automation as it is then, so a routine one runs (as the automation, inside its run's cause and ledger scope) and one
that should ask becomes an approval; switched off, cancelled or missed, it expires."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import actions, activity, events, store, timers
from app.domain.actions import ActionSpec

UTC = dt.UTC
WHEN = "2026-10-16T18:22:00Z"
DUE = dt.datetime(2026, 10, 16, 18, 22, 3, tzinfo=UTC)


@pytest.fixture
def studio(monkeypatch):
    """What M6 and M8 provide (update_intent, fire, approvals.ask), faked, and two actions: test_send (out) and
    test_tag (lens), which record what they saw."""
    timers._idle["until"] = 0.0
    log = {"status": [], "asked": [], "done": []}
    have = {
        ("automations", "update_intent"): lambda db, run, key, status, result=None: log["status"].append((run, key, status, result)),
        ("automations", "fire"): lambda *a, **k: None,
        ("approvals", "ask"): lambda db, cfg, intent: log["asked"].append(intent) or 77,
    }
    monkeypatch.setattr(timers, "_provided", lambda module, fn: have.get((module, fn)))

    def did(db, cfg, intent, user, dry):
        log["done"].append({"params": intent["params"], "cause": events.cause(), "refs": activity.current(), "user": user})
        if intent["params"].get("fail"):
            raise ValueError("the far end said no")
        return {"sent": True}

    params = {"type": "object", "properties": {"to": {"type": "string"}, "fail": {"type": "boolean"}}}
    monkeypatch.setattr(actions, "_ACTIONS", dict(actions._ACTIONS))
    actions.register(ActionSpec(name="test_send", label="Send", group="Messages", params=params, perform=did, effect="out"))
    actions.register(
        ActionSpec(
            name="test_tag",
            label="Tag",
            group="Lens",
            params=params,
            perform=did,
            effect="lens",
            risk=lambda p, ctx: "ask" if p.get("to") == "everyone" else "routine",
        )
    )
    return have, log


def intent(action="test_send", node="n7", effect="out", risk="ask", **params):
    return {
        "key": f"51-{node}",
        "action": action,
        "params": params,
        "effect": effect,
        "risk": risk,
        "untrusted": False,
        "spaces": [3],
        "run_as": 1,
        "node": node,
        "automation": 4,
        "run": 51,
        "when": WHEN,
        "status": "scheduled",
    }


def automation(db, enabled=True, policy=None, spaces=(3,)):
    db.q("UPSERT $r CONTENT $d", r=store.R("automation", 4), d={"enabled": enabled, "policy": policy or {}, "spaces": list(spaces)})


def test_later_keeps_an_intent_until_its_time(db, cfg, studio):
    _, log = studio
    automation(db)
    got = timers.later(db, intent("log_line", effect="read", risk="routine", text="hi"), depth=2)
    assert got == {"timer": "intent-51-n7", "at": "2026-10-16T18:22:00+00:00"}
    [u] = timers.upcoming(db, 4, now=DUE - dt.timedelta(hours=1))
    assert u["description"] == "Write “hi” to the run log: on Fri 16 Oct at 18:22 (UTC)" and u["node"] == "n7"
    assert timers.due(db, cfg, now=DUE - dt.timedelta(minutes=1)) == 0 and log["status"] == []
    assert timers.due(db, cfg, now=DUE) == 1
    assert log["status"] == [(51, "51-n7", "done", {"line": "hi", "level": "info"})]
    with pytest.raises(ValueError, match="doesn't say when"):
        timers.later(db, {**intent(), "when": None})
    with pytest.raises(ValueError, match="key"):
        timers.later(db, {**intent(), "key": "no spaces allowed"})


def test_a_routine_intent_runs_as_the_automation_in_its_run(db, cfg, studio):
    _, log = studio
    automation(db, policy={"n7": "auto"})
    timers.later(db, intent(to="gateway:family"), depth=2)
    assert timers.due(db, cfg, now=DUE) == 1
    [done] = log["done"]
    assert done["params"] == {"to": "gateway:family"} and done["user"] is None  # actions.person: its run_as
    assert done["cause"] == {"automation": 4, "run": 51, "depth": 2}  # what it causes carries the run
    assert done["refs"][:2] == ["automation:4", "automation_run:51"]  # model calls land in the ledger for it
    assert log["status"] == [(51, "51-n7", "done", {"sent": True})] and log["asked"] == []


def test_one_that_should_ask_becomes_an_approval(db, cfg, studio):
    have, log = studio
    automation(db, policy={"n7": "ask"})  # auto when the run made it; the person has since set it back to ask
    timers.later(db, intent(risk="routine", to="gateway:family"))
    assert timers.due(db, cfg, now=DUE) == 1
    assert log["done"] == [] and log["asked"][0]["risk"] == "ask" and log["asked"][0]["status"] == "waiting"
    assert log["status"] == [(51, "51-n7", "waiting", {"approval": 77, "why": "waiting for your pick"})]
    # before approvals are built it stays waiting, saying so
    del have[("approvals", "ask")]
    timers.later(db, intent(node="n8", to="gateway:family"))
    assert timers.due(db, cfg, now=DUE) == 1
    assert log["status"][-1] == (51, "51-n8", "waiting", {"why": "waiting for your pick"})


def test_switched_off_cancelled_or_missed_it_expires(db, cfg, studio):
    _, log = studio
    automation(db, enabled=False, policy={"n7": "auto"})
    timers.later(db, intent())
    assert timers.due(db, cfg, now=DUE) == 1 and log["done"] == []
    assert log["status"][-1][2:] == ("expired", {"why": "the automation was switched off or removed before its time"})
    timers.later(db, intent(node="n8"))
    assert timers.cancel_for(db, 4) == 1
    assert log["status"][-1] == (51, "51-n8", "expired", {"why": "the automation was switched off"})
    timers.later(db, intent(node="n9"))
    assert timers.due(db, cfg, now=DUE + dt.timedelta(hours=3)) == 0  # Lens was off for three hours
    assert log["status"][-1] == (51, "51-n9", "expired", {"why": "Lens was off when it was due"})


def test_a_failure_is_said_on_the_run_and_the_timer(db, cfg, studio):
    _, log = studio
    automation(db, policy={"n7": "auto"})
    timers.later(db, intent(fail=True))
    assert timers.due(db, cfg, now=DUE) == 1
    assert log["status"][-1] == (51, "51-n7", "failed", {"error": "ValueError: the far end said no"})
    assert timers.get(db, "intent-51-n7")["status"] == "failed"


def test_risk_is_worked_out_again_from_the_automation_now(db, studio):
    out = intent()
    assert timers.risk_now(db, out, None) == "ask"  # no automation to read: an out action asks
    assert timers.risk_now(db, out, {"policy": {"n7": "auto"}, "spaces": [3]}) == "routine"
    assert timers.risk_now(db, {**out, "effect": "read"}, None) == "ask"  # its params still reach out
    tag = intent("test_tag", effect="lens", risk="routine", to="me")
    assert timers.risk_now(db, tag, {"spaces": [3, 5]}) == "routine"
    assert timers.risk_now(db, tag, {"spaces": [5]}) == "ask"  # its namespace isn't the automation's any more
    assert timers.risk_now(db, {**tag, "risk": "ask"}, {"spaces": [3]}) == "ask"
    assert timers.risk_now(db, {**tag, "params": {"to": "everyone"}}, None) == "ask"  # the action's own check
    assert timers.risk_now(db, intent("log_line", effect="read", risk="routine", text="x"), None) == "routine"
