"""An automation's workflow (scope event, docs/automations.md): its action and decide nodes are checked on every save,
with the leaf rule; action params render from the run context; routine intents are carried out and pass their output
on, intents that ask first or wait pass nothing on (and the nodes after them are skipped); a failing action is marked
failed and the rest of the graph goes on; and a try keeps nothing."""

from __future__ import annotations

import pytest

from app.domain import actions, automation_nodes, events, flow, match, workflows
from app.domain.actions import ActionSpec

SAMPLE = events.kind_info("asset.added")["sample"]
USER = {"id": 1, "email": "sam@x.io", "name": "Sam", "admin": True}


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b, port=None):
    return {"source": a, "target": b, **({"port": port} if port else {})}


def graph(*nodes, edges=()):
    return {"nodes": [node("in", "input"), *nodes], "edges": list(edges)}


@pytest.fixture
def made():
    """Two actions for the tests: `tell` (out to a room, self to "me") and `tag` (changes an asset in Lens), both
    remembering what they were asked to do; and one that always fails."""
    done = []

    def perform(db, cfg, intent, user, dry):
        done.append((intent["action"], intent["params"], user["email"], dry))
        return {"ok": True, "said": intent["params"].get("text") or intent["params"].get("object")}

    def broken(db, cfg, intent, user, dry):
        raise RuntimeError("the line is down")

    specs = [
        ActionSpec(
            name="tell",
            label="Tell someone",
            group="Messages",
            params={"type": "object", "properties": {"to": {"type": "string", "default": "me"}, "text": {"type": "string"}}},
            required=("text",),
            effect=lambda p: "self" if p.get("to") == "me" else "out",
            perform=perform,
            describe=lambda p: f"Tell {p.get('to')} “{p.get('text')}”",
        ),
        ActionSpec(
            name="tag",
            label="Tag an asset",
            group="Lens",
            params={"type": "object", "properties": {"object": {"type": "string"}, "tags": {"type": "array"}}},
            required=("object",),
            effect="lens",
            perform=perform,
        ),
        ActionSpec(
            name="broken",
            label="Always fails",
            group="Run",
            params={"type": "object", "properties": {}},
            effect="read",
            perform=broken,
        ),
    ]
    for s in specs:
        actions.register(s)
    yield done
    for s in specs:
        actions._ACTIONS.pop(s.name, None)


def _try(db, cfg, g, activity=SAMPLE, spaces=None):
    return workflows.try_graph(db, cfg, g, "event", spaces=spaces, user=USER, activity=activity)


def _problem(db, g):
    with pytest.raises(ValueError) as e:
        workflows.validate_graph(db, g, "event")
    return str(e.value)


def test_event_workflows_are_checked_on_save(db, made):
    assert "needs an action node" in _problem(db, graph(node("p", "pick", path="event"), edges=[edge("in", "p")]))
    assert "choose an action" in _problem(db, graph(node("a", "action", action="nope"), edges=[edge("in", "a")]))
    assert "needs Line (text)" in _problem(db, graph(node("a", "action", action="log_line"), edges=[edge("in", "a")]))
    assert "has no param colour" in _problem(
        db, graph(node("a", "action", action="log_line", params={"text": "x", "colour": "red"}), edges=[edge("in", "a")])
    )
    assert "line 1" in _problem(db, graph(node("a", "action", action="log_line", params={"text": "{{ x "}), edges=[edge("in", "a")]))
    assert "action node has no setting retry" in _problem(
        db, graph(node("a", "action", action="log_line", params={"text": "x"}, retry=3), edges=[edge("in", "a")])
    )
    # a templated param stands for any type until it runs
    ok = graph(
        node("a", "action", action="tag", params={"object": "{{ event.object.id }}", "tags": "{{ event.tag }}"}), edges=[edge("in", "a")]
    )
    assert workflows.validate_graph(db, ok, "event")["nodes"][1]["config"]["params"]["tags"] == "{{ event.tag }}"
    # waiting before an action comes with the time module; until then it is refused, as are both kinds of wait
    waits = graph(node("a", "action", action="log_line", params={"text": "x"}, delay="5m"), edges=[edge("in", "a")])
    assert "coming soon" in _problem(db, waits)
    assert "not both" in _problem(
        db,
        graph(
            node("a", "action", action="log_line", params={"text": "x"}, delay="5m", when="2026-10-16T07:00:00Z"), edges=[edge("in", "a")]
        ),
    )
    assert "can't point back" in _problem(
        db, graph(node("a", "action", action="log_line", params={"text": "x"}, delay="-5m"), edges=[edge("in", "a")])
    )
    # deciding with the model comes with the conditions module
    assert "coming soon" in _problem(
        db,
        graph(
            node("d", "decide", question="Urgent?", options={"yes": "urgent"}),
            node("a", "action", action="log_line", params={"text": "x"}),
            edges=[edge("in", "d"), edge("d", "a", "yes")],
        ),
    )
    # the event scope's nodes are its own: an action is refused in a recording workflow
    with pytest.raises(ValueError, match="node types are"):
        workflows.validate_graph(db, graph(node("a", "action", action="log_line", params={"text": "x"}), edges=[edge("in", "a")]))


def test_the_leaf_rule_holds_on_every_save_path(db, made):
    after = node("l", "action", action="log_line", params={"text": "after"})
    out = graph(node("a", "action", action="tell", params={"to": "family", "text": "hi"}), after, edges=[edge("in", "a"), edge("a", "l")])
    assert "reaches outside Lens or other people" in _problem(db, out)
    # a templated reach counts as out, as an effect that can't be worked out does
    tpl = graph(
        node("a", "action", action="tell", params={"to": "{{ vars.room }}", "text": "hi"}), after, edges=[edge("in", "a"), edge("a", "l")]
    )
    assert "reaches outside Lens" in _problem(db, tpl)
    # in a body too
    body = {"nodes": [node("x", "arg", name="item"), node("a", "action", action="tell", params={"to": "family", "text": "hi"}),
                      node("r", "return", name="out")], "edges": [edge("x", "a"), edge("a", "r")]}  # fmt: skip
    looped = graph(node("f", "for_each", path="event.tag", body=body), edges=[edge("in", "f")])
    assert "action node a reaches outside Lens" in _problem(db, looped)
    # a message to the person themselves, or a change in Lens, may have nodes after it
    mine = graph(node("a", "action", action="tell", params={"text": "hi"}), after, edges=[edge("in", "a"), edge("a", "l")])
    assert workflows.validate_graph(db, mine, "event")
    # an out action at the end is fine, and so is a body whose only node is an action (it does something)
    assert workflows.validate_graph(
        db, graph(node("a", "action", action="tell", params={"to": "family", "text": "hi"}), edges=[edge("in", "a")]), "event"
    )
    group = {
        "nodes": [node("x", "arg", name="in"), node("a", "action", action="tell", params={"to": "family", "text": "hi"})],
        "edges": [edge("x", "a")],
    }
    assert workflows.validate_graph(db, graph(node("g", "group", body=group), edges=[edge("in", "g")]), "event")
    # and the workflow API checks the same (its routes call validate_graph with the workflow's scope)
    wid = workflows.create(db, "School emails", mine, scope="event")
    with pytest.raises(ValueError, match="reaches outside Lens"):
        workflows.save_version(db, wid, out)


def test_actions_render_from_the_run_context_and_pass_their_output_on(db, cfg, made):
    g = graph(
        node("a", "action", action="tag", params={"object": "{{ event.object.id }}", "tags": "{{ [event.object.media, event.space] }}"}),
        node("b", "action", action="tell", params={"text": "Tagged {{ steps.a.said }} in {{ namespace.name }}"}),
        node("l", "action", action="log_line", params={"text": "{{ event.summary }}"}),
        edges=[edge("in", "a"), edge("a", "b"), edge("in", "l")],
    )
    out = _try(db, cfg, g)
    assert out["error"] is None and [t["status"] for t in out["trace"].values()] == ["done"] * 4
    by = {i["node"]: i for i in out["intents"]}
    # one expression keeps its type; others render as text
    assert by["a"]["params"] == {"object": "lens:recording/7", "tags": ["audio", 1]} and by["a"]["effect"] == "lens"
    # a change to the event's own object in the automation's namespaces is routine; so is telling the person
    assert by["a"]["risk"] == "routine" and by["b"]["risk"] == "routine" and by["b"]["effect"] == "self"
    assert by["b"]["params"]["text"] == "Tagged lens:recording/7 in pods" and by["b"]["result"]["ok"] is True
    # params made from the event's outside text are marked so; a fixed one isn't
    assert by["l"]["untrusted"] is True and by["l"]["params"]["text"] == SAMPLE["summary"]
    assert {i["key"] for i in out["intents"]} == {"try-a", "try-b", "try-l"} and all(i["status"] == "done" for i in out["intents"])
    # a try is dry: every action was asked what it would do, as the person
    assert {(a, dry, who) for a, _, who, dry in made} == {("tag", True, "sam@x.io"), ("tell", True, "sam@x.io")}
    assert any(line.startswith("action b: would tell me") for line in out["log"])


def test_prompts_mark_outside_text_and_template_defaults_are_filled_in(db, cfg, made):
    def ask(db, cfg, intent, user, dry):
        return {"reply": "a web page said so", "untrusted": True}

    spec = ActionSpec(
        name="ask",
        label="Ask the model",
        group="AI",
        params={
            "type": "object",
            "properties": {"prompt": {"type": "string", "format": "prompt"}, "about": {"type": "object", "default": "{{ event }}"}},
        },
        required=("prompt",),
        effect="read",
        perform=ask,
    )
    actions.register(spec)
    try:
        g = graph(
            node("a", "action", action="ask", params={"prompt": "Sum up {{ event.object.name }} for {{ user.name }}"}),
            node("s", "action", action="log_line", params={"text": "{{ steps.a.reply }}"}),
            node("f", "action", action="log_line", params={"text": "fixed"}),
            edges=[edge("in", "a"), edge("a", "s"), edge("a", "f")],
        )
        out = _try(db, cfg, g)
        by = {i["node"]: i for i in out["intents"]}
        prompt = by["a"]["params"]["prompt"]
        # in a prompt, what the run read from outside Lens is marked as data; the person's own words stay as written
        assert prompt.startswith("Sum up <untrusted-") and SAMPLE["object"]["name"] in prompt and prompt.endswith(" for Sam")
        # a default that is a template is filled in like the params the person gave
        assert by["a"]["params"]["about"]["id"] == SAMPLE["id"]
        # a node that passed on outside text counts as outside text for the params that read it, like an activity's
        quiet = {**SAMPLE, "lens": {**SAMPLE["lens"], "untrusted": []}}
        by = {i["node"]: i for i in _try(db, cfg, g, activity=quiet)["intents"]}
        assert by["a"]["untrusted"] is False and by["s"]["untrusted"] is True and by["f"]["untrusted"] is False
        assert "<untrusted-" not in by["a"]["params"]["prompt"]
    finally:
        actions._ACTIONS.pop("ask", None)


def test_an_intent_that_asks_first_or_fails_passes_nothing_on(db, cfg, made):
    after = node("l", "action", action="log_line", params={"text": "after"})
    # a change to another asset asks first: the node passes nothing on and what comes after it is skipped
    g = graph(node("a", "action", action="tag", params={"object": "lens:recording/99"}), after, edges=[edge("in", "a"), edge("a", "l")])
    out = _try(db, cfg, g)
    assert out["trace"]["a"]["status"] == "done" and out["trace"]["a"]["ports"] == [] and out["trace"]["l"]["status"] == "skipped"
    assert [(i["node"], i["status"], i["risk"]) for i in out["intents"]] == [("a", "waiting", "ask")]
    assert "stopped at a: waiting for your pick" in out["log"] and made == []
    # an out action asks first too (nothing can come after it)
    out = _try(db, cfg, graph(node("t", "action", action="tell", params={"to": "family", "text": "hi"}), edges=[edge("in", "t")]))
    assert [(i["status"], i["effect"]) for i in out["intents"]] == [("waiting", "out")]
    # a failing action is marked failed with why; the other branch still runs
    g = graph(node("x", "action", action="broken"), node("y", "action", action="log_line", params={"text": "{{ nope.nothing }}"}),
              node("z", "action", action="log_line", params={"text": "fine"}), edges=[edge("in", "x"), edge("in", "y"), edge("in", "z")])  # fmt: skip
    out = _try(db, cfg, g)
    assert out["error"] is None
    assert out["trace"]["x"]["status"] == "failed" and "the line is down" in out["trace"]["x"]["error"]
    assert out["trace"]["y"]["status"] == "failed" and "nope" in out["trace"]["y"]["error"]
    assert [(i["node"], i["status"]) for i in out["intents"]] == [("x", "failed"), ("y", "failed"), ("z", "done")]


def test_waiting_intents_are_handed_on_and_kept(db, cfg, made, monkeypatch):
    """With the time module there, a delayed action becomes a scheduled intent handed on to it; a real run (not dry)
    keeps every intent and hands on the ones that wait."""
    monkeypatch.setattr(automation_nodes, "_timers", lambda: lambda db, intent: None)
    g = graph(
        node("w", "action", action="log_line", params={"text": "later"}, delay="2h"),
        node("t", "action", action="tell", params={"to": "family", "text": "{{ event.object.name }}"}),
        node("n", "action", action="log_line", params={"text": "now"}),
        edges=[edge("in", "w"), edge("in", "t"), edge("in", "n")],
    )
    g = workflows.validate_graph(db, g, "event")
    kept, handed = [], []
    box = automation_nodes.Intents(
        db, cfg, user=USER, policy={"t": "ask"}, automation=4, run=51, spaces=[1],
        keep=lambda i, extra: kept.append((i["key"], i["status"], extra["path"])), hand_off=lambda i: handed.append(i["key"]) or {"approval": 9},
    )  # fmt: skip
    ctx = automation_nodes.context(db, cfg, {"id": 4, "name": "School", "version": 1, "spaces": [1]}, {"id": 51, "dry": False},
                                   {"as": "mail", "on": "event"}, SAMPLE, {"mail": SAMPLE}, USER, now=events._parse("2026-10-15T16:00:00Z"))  # fmt: skip
    r = flow.Run(db, cfg, workflows.KITS["event"], ctx, lambda m: None, user=USER["email"], intents=box)
    flow.run_graph(r, g)
    assert sorted(kept) == [("51-n", "done", "n"), ("51-t", "waiting", "t"), ("51-w", "scheduled", "w")]
    assert sorted(handed) == ["51-t", "51-w"]
    by = {i["node"]: i for i in box.items}
    assert by["w"]["when"] == "2026-10-15T18:00:00Z" and by["t"]["untrusted"] is True and by["t"]["result"] == {"approval": 9}
    assert by["t"]["run_as"] == 1 and by["t"]["spaces"] == [1] and by["t"]["automation"] == 4
    assert box.counts() == {"done": 1, "waiting": 1, "scheduled": 1}
    # the person's policy lets an out node run on its own
    box2 = automation_nodes.Intents(db, cfg, user=USER, policy={"t": "auto"}, automation=4, run=52, spaces=[1])
    r = flow.Run(db, cfg, workflows.KITS["event"], {**ctx, "steps": {}}, lambda m: None, intents=box2)
    flow.run_graph(r, g)
    assert {i["node"]: i["status"] for i in box2.items}["t"] == "done"


def test_decide_nodes_get_a_port_per_option_once_the_conditions_module_is_there(db, cfg, made, monkeypatch):
    def check_decide(nid, c):
        if not isinstance(c.get("options"), dict) or not c["options"]:
            raise ValueError(f"decide node {nid}: give its options")

    monkeypatch.setattr(match, "check_decide", check_decide, raising=False)
    monkeypatch.setattr(match, "run_decide", lambda r, f, node, value: {"later": value}, raising=False)
    g = graph(
        node("d", "decide", question="Urgent?", options={"now": "urgent", "later": "can wait"}),
        node("a", "action", action="log_line", params={"text": "now"}),
        node("b", "action", action="log_line", params={"text": "{{ steps.d.choice }}"}),
        edges=[edge("in", "d"), edge("d", "a", "now"), edge("d", "b", "later")],
    )
    assert flow.ports(g["nodes"][1], workflows.KITS["event"]) == (["in"], ["now", "later", "unsure"], set())
    bad = graph(node("d", "decide", question="?", options={"now": "x"}), node("a", "action", action="log_line", params={"text": "x"}),
                edges=[edge("in", "d"), edge("d", "a", "maybe")])  # fmt: skip
    assert "has no output maybe (it has now, unsure)" in _problem(db, bad)
    out = _try(db, cfg, g)
    assert out["trace"]["a"]["status"] == "skipped" and out["trace"]["b"]["status"] == "done"
    assert [i["params"]["text"] for i in out["intents"]] == ["later"]


def test_event_workflows_are_made_and_run_by_their_automation_only(db, made):
    mine = graph(node("a", "action", action="log_line", params={"text": "x"}), edges=[edge("in", "a")])
    wid = workflows.create(db, "School emails", mine, scope="event")
    assert workflows.list_workflows(db) == [] and [w["id"] for w in workflows.list_workflows(db, "event")] == [wid]
    with pytest.raises(ValueError, match="its automation runs it"):
        workflows.run(db, {}, 1, wid)
