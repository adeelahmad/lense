"""The shapes automations are made of (docs/automations.md, Contract): triggers, joins, the If, policy and intents
checked with the first problem in words; and the action registry with risk from each action's effect."""

from __future__ import annotations

import logging

import pytest

from app.domain import actions, events
from app.domain import automation_spec as spec

SCHOOL = {"all": [{"path": "object.name", "op": "contains", "value": "trip"}]}


def test_durations():
    assert [spec.check_duration(d) for d in ("30s", "30m", "2h", "7d", "0s")] == [30, 1800, 7200, 604800, 0]
    assert spec.check_duration("-30m", back=True) == -1800
    for bad, said in [("30", "like 30m"), ("1.5h", "like 30m"), ("3w", "like 30m"), (30, "like 30m"), ("367d", "at most 366d")]:
        with pytest.raises(ValueError, match=said):
            spec.check_duration(bad)
    with pytest.raises(ValueError, match="the offset can't point back"):
        spec.check_duration("-5m", what="the offset")


def test_triggers():
    mail = spec.check_trigger({"as": "mail", "on": "event", "kinds": ["asset.added"], "where": SCHOOL})
    assert mail == {"as": "mail", "on": "event", "kinds": ["asset.added"], "where": SCHOOL, "from_automations": False}
    assert (
        spec.check_trigger({"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"})["cron"] == "0 7 * * 1-5"
    )
    assert spec.check_trigger({"as": "once", "on": "at", "at": "2026-10-16T07:30"}) == {
        "as": "once",
        "on": "at",
        "timezone": "UTC",
        "at": "2026-10-16T07:30:00",
    }
    soon = spec.check_trigger({"as": "soon", "on": "relative", "kinds": ["asset.added"], "field": "object.startTime", "offset": "-30m"})
    assert soon["offset"] == "-30m" and soon["field"] == "object.startTime"
    assert spec.check_trigger({"as": "hook", "on": "webhook"}) == {"as": "hook", "on": "webhook", "hook": None}
    assert spec.check_trigger({"as": "now", "on": "manual"}) == {"as": "now", "on": "manual"}
    bad = [
        ("mail", "trigger 1 is an object"),
        ({"on": "event"}, "trigger 1: give it a name"),
        ({"as": "Mail", "on": "event"}, "give it a name"),
        ({"as": "mail", "on": "email"}, "trigger mail: on is one of event, schedule"),
        ({"as": "mail", "on": "event", "kinds": ["asset.added"], "cron": "* * * * *"}, "a trigger on event has no cron"),
        ({"as": "mail", "on": "event"}, "kinds lists the kinds"),
        ({"as": "mail", "on": "event", "kinds": ["email.nope"]}, "trigger mail: no kind email.nope"),
        ({"as": "mail", "on": "event", "kinds": ["asset.added"], "where": {"any": []}}, "trigger mail's where: any needs"),
        ({"as": "mail", "on": "event", "kinds": ["asset.added"], "from_automations": "yes"}, "from_automations is true or false"),
        ({"as": "am", "on": "schedule"}, "give the schedule"),
        ({"as": "am", "on": "schedule", "cron": "61 * * * *"}, "trigger am:"),
        ({"as": "am", "on": "schedule", "cron": "0 7 * * *", "timezone": "Mars/Base"}, "unknown time zone"),
        ({"as": "once", "on": "at", "at": "tomorrow"}, "at is a time like"),
        ({"as": "soon", "on": "relative", "kinds": ["asset.added"]}, "field is the time in the activity"),
        (
            {"as": "soon", "on": "relative", "kinds": ["asset.added"], "field": "object.startTime", "offset": "soon"},
            "trigger soon's offset is",
        ),
        ({"as": "hook", "on": "webhook", "hook": "abc"}, "hook is given when the automation is saved"),
    ]
    for t, said in bad:
        with pytest.raises(ValueError, match=said):
            spec.check_trigger(t)
    both = spec.check_triggers([{"as": "a", "on": "manual"}, {"as": "b", "on": "manual"}])
    assert [t["as"] for t in both] == ["a", "b"]
    for ts, said in [
        ([], "at least one trigger"),
        ([{"as": "a", "on": "manual"}] * 2, "two triggers are named a"),
        ([{"as": f"t{n}", "on": "manual"} for n in range(11)], "at most 10 triggers"),
    ]:
        with pytest.raises(ValueError, match=said):
            spec.check_triggers(ts)


def test_joins():
    assert spec.check_join(None) == {"mode": "any"} and spec.check_join({"mode": "any"}) == {"mode": "any"}
    names = ["mail", "reply"]
    assert spec.check_join({"mode": "all", "within": "2h", "key": "object.thread"}, names) == {
        "mode": "all",
        "within": "2h",
        "key": "object.thread",
    }
    assert spec.check_join({"mode": "count", "of": "mail", "at_least": 3, "within": "1h"}, names)["at_least"] == 3
    assert spec.check_join({"mode": "absent", "after": "mail", "missing": "reply", "within": "2d"}, names)["missing"] == "reply"
    assert spec.check_join({"mode": "batch", "within": "15m"}, names) == {"mode": "batch", "within": "15m"}
    bad = [
        ({"mode": "some"}, "the join's mode is one of any, all"),
        ({"mode": "any", "within": "1h"}, "a join of mode any has no within"),
        ({"mode": "all"}, "says how long it waits"),
        ({"mode": "all", "within": "0s"}, "within is more than 0"),
        ({"mode": "all", "within": "-1h"}, "can't point back"),
        ({"mode": "all", "within": "1h", "key": "a b"}, "the join's key is a path"),
        ({"mode": "count", "within": "1h", "at_least": 2}, "names a trigger in of"),
        ({"mode": "count", "of": "nope", "within": "1h", "at_least": 2}, "the join's of is the name of one of the triggers"),
        ({"mode": "count", "of": "mail", "within": "1h", "at_least": 1}, "how many \\(at_least\\), 2 to 1000"),
        ({"mode": "absent", "after": "mail", "missing": "mail", "within": "1h"}, "waits after one trigger for another"),
    ]
    for j, said in bad:
        with pytest.raises(ValueError, match=said):
            spec.check_join(j, names)
    with pytest.raises(ValueError, match="needs at least two triggers"):
        spec.check_join({"mode": "all", "within": "1h"}, ["mail"])


def test_if_policy_and_context():
    tree = {
        "all": [
            {"path": "events.mail.object.name", "op": "contains", "value": "trip"},
            {"path": "steps.n3.ok", "op": "equals", "value": True},
        ]
    }
    assert spec.check_if(tree) == tree and spec.check_if(None) is None
    with pytest.raises(ValueError, match="the If tests object.name; paths start with automation, event, events"):
        spec.check_if({"all": [{"path": "object.name", "op": "exists"}]})
    with pytest.raises(ValueError, match="the If, any.0: the test is one of"):
        spec.check_if({"any": [{"path": "event.kind", "op": "near"}]})
    assert spec.check_policy({"n7": "ask"}, ["n1", "n7"]) == {"n7": "ask"} and spec.check_policy(None) == {}
    assert spec.check_policy({"n7": "auto"}, allow_auto=True) == {"n7": "auto"}
    for p, said in [
        ({"n7": "auto"}, "only you can let node n7 run without asking"),
        ({"n9": "ask"}, "names node n9"),
        ({"n7": "always"}, "ask or auto"),
        (["n7"], "the policy is"),
    ]:
        with pytest.raises(ValueError, match=said):
            spec.check_policy(p, ["n7"])
    paths = spec.context_paths()
    assert [p["path"] for p in paths] == [
        "event",
        "events",
        "trigger",
        "automation",
        "run",
        "namespace",
        "user",
        "now",
        "timezone",
        "vars",
        "steps",
    ]
    assert all(p["title"] and p["description"] for p in paths)


INTENT = {
    "key": "51-n7",
    "action": "log_line",
    "params": {"text": "School trip on Friday"},
    "effect": "read",
    "risk": "routine",
    "untrusted": True,
    "spaces": [3],
    "run_as": 1,
    "node": "n7",
    "automation": 4,
    "run": 51,
    "when": None,
}


def test_intents():
    i = spec.check_intent(INTENT)
    assert i["status"] == "waiting" and i == {**INTENT, "status": "waiting"}
    assert spec.check_intent({**INTENT, "when": "2026-10-15T18:22:00Z", "status": "scheduled"})["when"] == "2026-10-15T18:22:00Z"
    bad = [
        ({"colour": 1}, "an intent has no colour"),
        ({"key": "51 n7"}, "an intent's key"),
        ({"action": "Notify"}, "names its action"),
        ({"params": []}, "params are an object"),
        ({"effect": "far"}, "effect is one of read, self, lens, out"),
        ({"risk": "maybe"}, "risk is routine or ask"),
        ({"untrusted": "no"}, "untrusted is true or false"),
        ({"spaces": ["family"]}, "spaces are namespace ids"),
        ({"run": "51"}, "run is an id"),
        ({"node": ""}, "names the node"),
        ({"when": "later"}, "when is an ISO 8601 time"),
        ({"status": "pending"}, "status is one of waiting"),
    ]
    for change, said in bad:
        with pytest.raises(ValueError, match=said):
            spec.check_intent({**INTENT, **change})


# ---------- actions ----------
@pytest.fixture
def registry():
    before = dict(actions._ACTIONS)
    yield
    actions._ACTIONS.clear()
    actions._ACTIONS.update(before)


def fake(name, effect, **kw):
    return actions.ActionSpec(
        name=name,
        label=name.title(),
        group="Lens",
        params={
            "type": "object",
            "properties": {"object": {"type": "string"}, "to": {"type": "string"}, "n": {"type": "integer", "default": 2}},
        },
        effect=effect,
        perform=lambda db, cfg, intent, user, dry: {"did": not dry, "params": intent["params"]},
        **kw,
    )


def test_the_catalog_and_params(registry):
    actions.register(fake("tag_it", "lens"))
    names = [a["name"] for a in actions.catalog()]
    assert "log_line" in names and "tag_it" in names
    info = next(a for a in actions.catalog() if a["name"] == "log_line")
    assert info["effect"] == "read" and info["required"] == ["text"] and info["group"] == "Run" and info["output"]
    assert actions.get("tag_it").label == "Tag_It"
    with pytest.raises(KeyError):
        actions.get("nope")
    assert actions.check_params(actions.get("tag_it"), {"object": "lens:recording/7"}) == {"object": "lens:recording/7", "n": 2}
    assert actions.check_params(actions.get("tag_it"), {"n": "{{ steps.n3.count }}"})["n"] == "{{ steps.n3.count }}"
    for params, said in [
        ({"x": 1}, "tag_it has no param x"),
        ({"n": "two"}, "n is an integer"),
        ({"n": True}, "n is an integer"),
        ([], "params are an object"),
    ]:
        with pytest.raises(ValueError, match=said):
            actions.check_params(actions.get("tag_it"), params)
    log = actions.get("log_line")
    with pytest.raises(ValueError, match="log_line needs Line \\(text\\)"):
        actions.check_params(log, {})
    with pytest.raises(ValueError, match="level is one of info, warning"):
        actions.check_params(log, {"text": "x", "level": "loud"})
    bad = [
        (dict(name="Tag It"), "an action is named"),
        (dict(group="Misc"), "its group is one of"),
        (dict(params={"type": "array"}), "params is a JSON Schema of type object"),
        (dict(required=("missing",)), "required param missing"),
        (dict(effect="far"), "its effect is one of"),
    ]
    for change, said in bad:
        base = {"name": "bad", "label": "Bad", "group": "Lens", "params": {"type": "object"}, "perform": lambda *a: {}, **change}
        with pytest.raises(ValueError, match=said):
            actions.register(actions.ActionSpec(**base))


def test_risk_follows_the_effect(registry):
    event = {"object": {"id": "lens:recording/7"}, "space": 3, "lens": {}}
    ctx = {"event": event, "automation": {"id": 4, "spaces": [3]}, "node": "n7"}
    read, own = actions.register(fake("look", "read")), actions.register(fake("mine", "self"))
    lens, out = actions.register(fake("tag_it", "lens")), actions.register(fake("send", "out"))
    assert actions.resolve_risk(read, {}, ctx) == "routine" and actions.resolve_risk(own, {}, ctx) == "routine"
    assert actions.resolve_risk(lens, {"object": "lens:recording/7"}, ctx) == "routine"  # the event's own object
    assert actions.resolve_risk(lens, {"object": "lens:recording/8"}, ctx) == "ask"  # something else
    assert actions.resolve_risk(lens, {"object": "lens:recording/7"}, {**ctx, "automation": {"spaces": [5]}}) == "ask"  # not its namespaces
    assert actions.resolve_risk(lens, {}, ctx) == "ask"
    assert actions.resolve_risk(out, {}, ctx) == "ask" and actions.resolve_risk(out, {}, ctx, "auto") == "routine"
    assert actions.resolve_risk(out, {}, ctx, {"n7": "auto"}) == "routine" and actions.resolve_risk(out, {}, ctx, {"n8": "auto"}) == "ask"
    # an effect worked out from the params: to me is self, to anyone else out; a template that decides the reach is out
    tell = actions.register(fake("tell", lambda p: "self" if p.get("to") == "me" else "out"))
    assert actions.resolve_risk(tell, {"to": "me"}, ctx) == "routine" and actions.resolve_risk(tell, {"to": "gateway:family"}, ctx) == "ask"
    broken = actions.register(fake("broken", lambda p: 1 / 0))
    assert actions.effect_of(broken, {}) == "out" and actions.resolve_risk(broken, {}, ctx) == "ask"
    # an action's own risk may make it stricter, never looser
    careful = actions.register(fake("careful", "read", risk=lambda p, c: "ask" if p.get("n", 0) > 5 else "routine"))
    assert actions.resolve_risk(careful, {"n": 9}, ctx) == "ask" and actions.resolve_risk(careful, {"n": 1}, ctx) == "routine"
    loose = actions.register(fake("loose", "out", risk=lambda p, c: "routine"))
    assert actions.resolve_risk(loose, {}, ctx) == "ask"
    assert actions.describe("log_line", {"text": "hi"}) == "Write “hi” to the run log" and actions.describe("send", {}) == "Send"


def test_perform_carries_an_intent_out(registry, db, cfg, caplog):
    with caplog.at_level(logging.INFO, logger="lens.automations"):
        assert actions.perform(db, cfg, INTENT, {"id": 1}) == {"line": "School trip on Friday", "level": "info"}
        assert "automation 4 run 51: School trip on Friday" in caplog.text
        caplog.clear()
        assert actions.perform(db, cfg, INTENT, {"id": 1}, dry=True)["line"] == "School trip on Friday"
        assert caplog.text == ""  # a dry run changes nothing
    actions.register(fake("tag_it", "lens"))
    got = actions.perform(db, cfg, {**INTENT, "action": "tag_it", "params": {"object": "lens:recording/7"}}, {"id": 1}, dry=True)
    assert got == {"did": False, "params": {"object": "lens:recording/7", "n": 2}}
    with pytest.raises(ValueError, match="no action nope"):
        actions.perform(db, cfg, {**INTENT, "action": "nope"}, {"id": 1})
    with pytest.raises(ValueError, match="log_line needs"):
        actions.perform(db, cfg, {**INTENT, "params": {}}, {"id": 1})
    actions.register(actions.ActionSpec(name="odd", label="Odd", group="Lens", params={"type": "object"}, perform=lambda *a: "done"))
    with pytest.raises(ValueError, match="odd gave str, not an object"):
        actions.perform(db, cfg, {**INTENT, "action": "odd", "params": {}}, {"id": 1})
    assert events.kind_info("asset.added")  # the stream's catalog and the action catalog load together
