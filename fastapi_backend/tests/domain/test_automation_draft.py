"""Describing an automation in words (docs/automations.md, Describe it in words): one model call, with the kinds, the
operators and the actions as its vocabulary; what doesn't fit is dropped and said; nothing is saved; refused over
budget; and the call counts in the activity ledger."""

from __future__ import annotations

import pytest

from app.domain import activity, auth, automation_draft, automations, budgets, store
from tests.helpers import make_user, without


@pytest.fixture
def env(db, cfg, monkeypatch):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    cfg = {**cfg, "llm": {**cfg["llm"], "base_url": "http://model.invalid/v1", "model": "m"}}
    asked = []

    def reply(answer):
        def fake(cfg, system, user, schema, model=None):
            asked.append({"system": system, "user": user})
            activity.record("out", "model.chat", [], cfg, db, model="m", cost_usd=0.01)
            return answer

        monkeypatch.setattr(automation_draft.llm, "json_out", fake)

    return {
        "cfg": cfg,
        "ed": {"id": auth.find_account(db, "ed@x.io")["id"]},
        "vi": {"id": auth.find_account(db, "vi@x.io")["id"]},
        "pods": store.ns_id(db, "pods"),
        "asked": asked,
        "reply": reply,
    }


def test_a_sentence_becomes_a_draft_that_saves(db, env):
    cfg = env["cfg"]
    env["reply"](
        {
            "name": "Log new pods",
            "description": "Logs each new recording's title",
            "triggers": [
                {
                    "as": "new",
                    "on": "event",
                    "kinds": ["asset.added"],
                    "where": {"all": [{"path": "object.media", "op": "equals", "value": "audio"}]},
                }
            ],
            "if": {"all": [{"path": "event.object.name", "op": "exists"}]},
            "actions": [
                {"action": "notify", "params": {"to": "me", "text": "New: {{ event.object.name }}"}},
                {"action": "log_line", "params": {"text": "Logged {{ event.object.name }}"}},
            ],
            "unmapped": ["only on weekdays"],
        }
    )
    got = automation_draft.draft(db, cfg, "When an audio recording is added, tell me and log it, on weekdays", env["ed"])
    d = got["draft"]
    assert got["dropped"] == [] and got["unmapped"] == ["only on weekdays"]
    assert d["name"] == "Log new pods" and d["enabled"] is False and d["triggers"][0]["where"]["all"][0]["value"] == "audio"
    # an action that may reach outside Lens is a leaf off the one before it; the rest go on from there
    assert d["graph"]["edges"] == [{"source": "input", "target": "a1"}, {"source": "input", "target": "a2"}]
    # the model was given the vocabulary
    said = env["asked"][0]
    assert "asset.added" in said["user"] and "log_line" in said["user"] and "contains" in said["system"]
    assert "object.name (Title)" in said["user"]
    # nothing was saved, and the call counted for the person
    assert db.rows("SELECT id FROM automation") == []
    assert activity.costs(db, [f"account:{env['ed']['id']}"])[f"account:{env['ed']['id']}"]["calls"] == 1
    # it saves as it is
    aid = automations.create(db, cfg, d, {**env["ed"]})["id"]
    assert automations.get(db, aid)["enabled"] is False


def test_triggers_in_time_are_drafted_once_the_timers_are_there(db, env, monkeypatch):
    cfg = env["cfg"]
    morning = {"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"}
    env["reply"]({"name": "Mornings", "triggers": [morning], "actions": [{"action": "log_line", "params": {"text": "Morning"}}]})
    got = automation_draft.draft(db, cfg, "every weekday at 7 log morning", env["ed"])
    assert got["draft"]["triggers"] == [morning] and got["dropped"] == [] and got["unmapped"] == []
    assert '"on": "schedule"' in env["asked"][0]["system"] and '"on": "relative"' in env["asked"][0]["system"]
    # without them, the model is told to leave times out
    without(monkeypatch, "timers")
    automation_draft.draft(db, cfg, "every weekday at 7 log morning", env["ed"])
    assert '"on": "schedule"' not in env["asked"][1]["system"] and "aren't available yet" in env["asked"][1]["system"]


def test_what_doesnt_fit_is_dropped_and_said(db, env, monkeypatch):
    without(monkeypatch, "timers")  # schedules come with their own module
    cfg = env["cfg"]
    env["reply"](
        {
            "name": "Mixed",
            "triggers": [
                {"as": "morning", "on": "schedule", "cron": "0 7 * * *"},
                {"as": "door", "on": "webhook"},
                {"as": "new", "on": "event", "kinds": ["asset.added"], "where": {"all": [{"path": "x", "op": "matches"}]}},
                {"as": "odd", "on": "event", "kinds": ["no.such"]},
            ],
            "if": {"all": [{"path": "nowhere.at.all", "op": "exists"}]},
            "actions": [
                {"action": "launch_rockets"},
                {"action": "log_line", "params": {"text": 5}},
                {"action": "log_line", "params": {"text": "ok"}},
            ],
        }
    )
    got = automation_draft.draft(db, cfg, "every morning and when something comes in, launch rockets", env["ed"])
    d = got["draft"]
    assert got["unmapped"] == ["a trigger on schedule (coming soon)"]
    assert [t["as"] for t in d["triggers"]] == ["door", "new"] and "where" not in d["triggers"][1]
    assert any("its where was left out" in x for x in got["dropped"]) and any("no.such" in x for x in got["dropped"])
    assert any(x.startswith("the If:") for x in got["dropped"]) and "if" not in d
    assert any("no action launch_rockets" in x for x in got["dropped"]) and any(x.startswith("action 2: ") for x in got["dropped"])
    assert [n["config"]["action"] for n in d["graph"]["nodes"][1:]] == ["log_line"]
    # with nothing left: run by hand, and no Then
    env["reply"]({"name": "", "triggers": [], "actions": []})
    got = automation_draft.draft(db, cfg, "do something", env["ed"])
    assert got["draft"]["triggers"] == [{"as": "manual", "on": "manual"}] and "graph" not in got["draft"]
    assert got["draft"]["name"] == "New automation" and "no action fit; add one before saving" in got["dropped"]


def test_drafting_is_refused_when_it_cant_or_mustnt_ask(db, env, cfg):
    env["reply"]({"name": "x", "triggers": [], "actions": []})
    with pytest.raises(ValueError, match="say what"):
        automation_draft.draft(db, env["cfg"], "  ", env["ed"])
    with pytest.raises(ValueError, match="can't edit any namespace"):
        automation_draft.draft(db, env["cfg"], "log new things", env["vi"])
    with pytest.raises(ValueError, match="only in namespaces you can edit"):
        automation_draft.draft(db, env["cfg"], "log new things", env["ed"], [store.ns_id(db, "calls")])
    with pytest.raises(ValueError, match="no language model"):
        automation_draft.draft(db, {**cfg, "llm": {**cfg["llm"], "base_url": None}}, "log new things", env["ed"])
    activity.record("out", "model.chat", [f"space:{env['pods']}"], db=db, model="m", cost_usd=0.6)
    budgets.put(db, f"space:{env['pods']}", usd=0.5)
    with pytest.raises(ValueError, match="over budget"):
        automation_draft.draft(db, env["cfg"], "log new things", env["ed"], [env["pods"]])
    assert env["asked"] == []
