"""Match trees (docs/automations.md, Match trees): all, any and not over the workflow engine's tests, checked with
problems in words, tested against values, read back in words; and the condition, filter and switch nodes taking a
tree in `match` while old graphs run as they did."""

from __future__ import annotations

import pytest

from app.domain import flow, match, workflows

MAIL = {
    "kind": "email.received",
    "object": {"from": ["office@stmarys.org"], "subject": "School trip on Friday", "attachments": 2},
    "tag": [{"type": "Mention", "id": "lens:entity/44", "name": "St Mary's School"}],
}


def leaf(path, op, value=None):
    return {"path": path, "op": op, **({"value": value} if value is not None else {})}


def test_trees_test_values():
    school = leaf("object.from", "contains", "@stmarys.org")
    assert match.test(match.check(school), MAIL) is False  # contains on a list is an item that equals it, as flow.test
    assert match.test(match.check(leaf("object.from.0", "contains", "@STMARYS.org")), MAIL)  # text: any case
    assert match.test(match.check(leaf("object.from", "contains", "office@stmarys.org")), MAIL)
    tree = match.check(
        {
            "all": [
                leaf("object.from.0", "contains", "stmarys"),
                {"any": [leaf("object.subject", "contains", "trip"), leaf("object.subject", "contains", "outing")]},
                {"not": leaf("object.attachments", "gt", 5)},
                leaf("tag.0.id", "equals", "lens:entity/44"),
            ]
        }
    )
    assert match.test(tree, MAIL)
    assert not match.test(tree, {**MAIL, "object": {**MAIL["object"], "subject": "Parents' evening"}})
    assert match.test(match.check({"all": []}), MAIL)  # no conditions: always
    assert match.test(match.check(leaf("", "exists")), MAIL)  # no path: the value itself
    assert match.test(match.check(leaf("object.cc", "empty")), MAIL)
    assert match.test(match.check(leaf("missing.0.x", "not_equals", "a")), MAIL)
    assert match.test(match.check(leaf("object.attachments", "lt", 3)), MAIL, env={"now": "2026-10-15T16:00:00Z"})


def test_a_check_names_the_first_problem_and_where_it_is():
    bad = [
        ([], "a condition is"),
        ({"all": "x"}, "all is a list of conditions"),
        ({"any": []}, "any needs at least one condition"),
        ({"all": [], "any": [leaf("a", "exists")]}, "all stands alone"),
        ({"path": "a", "op": "near"}, "the test is one of exists, empty, equals"),
        ({"path": "a", "op": "gt", "value": "3"}, "compare with a number"),
        ({"path": "a b", "op": "exists"}, "give a path like"),
        ({"path": "a", "op": "exists", "when": 1}, "not when"),
        ({"all": [leaf("a", "exists"), {"not": {"op": "lt", "value": "x"}}]}, "the condition, all.1.not: compare with a number"),
    ]
    for tree, said in bad:
        with pytest.raises(ValueError, match=said):
            match.check(tree)
    with pytest.raises(ValueError, match="trigger mail's where, any.0"):
        match.check({"any": [{"op": "nope"}]}, "trigger mail's where")
    deep = leaf("a", "exists")
    for _ in range(match.MAX_DEPTH):
        deep = {"not": deep}
    with pytest.raises(ValueError, match="nest at most 6 deep"):
        match.check(deep)
    with pytest.raises(ValueError, match="at most 50 tests"):
        match.check({"any": [leaf("a", "exists")] * (match.MAX_LEAVES + 1)})
    # cleaned: an empty path left out, values kept as given
    assert match.check({"all": [{"path": "", "op": "equals", "value": [1, "a"]}]}) == {"all": [{"op": "equals", "value": [1, "a"]}]}


def test_trees_read_in_words():
    tree = {
        "all": [
            leaf("object.from", "contains", "@stmarys.org"),
            {"any": [leaf("object.subject", "contains", "trip"), leaf("object.subject", "contains", "outing")]},
            {"not": leaf("object.attachments", "gt", 5)},
            {"not": {"any": [leaf("object.cc", "exists"), leaf("object.list", "equals", True)]}},
        ]
    }
    assert match.describe(tree) == (
        "object.from contains “@stmarys.org” and (object.subject contains “trip” or object.subject contains “outing”) "
        "and object.attachments is not more than 5 and not (object.cc is set or object.list is true)"
    )
    assert match.describe({"all": []}) == "always"
    assert match.paths(tree) == ["object.from", "object.subject", "object.attachments", "object.cc", "object.list"]


# ---------- workflow nodes ----------
def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b, port=None):
    return {"source": a, "target": b, **({"port": port} if port else {})}


@pytest.fixture
def kit():
    """A scope with one node that keeps what reaches it, to see what the primitives pass on."""
    seen = {}

    def run(r, f, n, v, vals):
        seen[n["config"]["key"]] = v
        return {}

    k = flow.Kit("t", ("sink",), {"sink": {"key"}}, lambda db, nid, t, cfg: None, run, {"sink"}, "a test graph needs a sink")
    k.seen = seen
    return k


def go(kit, graph, ctx):
    checked = flow.check_graph(None, graph, kit)
    kit.seen.clear()
    flow.run_graph(flow.Run(None, {}, kit, ctx, say=lambda m: None), checked)
    return checked, kit.seen


def test_condition_filter_and_switch_take_a_match_tree(kit):
    trip = {"any": [leaf("subject", "contains", "trip"), leaf("subject", "contains", "outing")]}
    graph = {
        "nodes": [
            node("in", "input"),
            node("c", "condition", path="object", match=trip),
            node("yes", "sink", key="yes"),
            node("no", "sink", key="no"),
            node("list", "pick", path="items"),
            node("f", "filter", match={"all": [leaf("n", "gt", 1), {"not": leaf("n", "gt", 3)}]}),
            node("kept", "sink", key="kept"),
            node("sw", "switch", path="object", cases=[{"port": "school", "match": {"all": [leaf("from.0", "contains", "stmarys")]}}]),
            node("school", "sink", key="school"),
            node("other", "sink", key="other"),
        ],
        "edges": [
            edge("in", "c"),
            edge("c", "yes", "yes"),
            edge("c", "no", "no"),
            edge("in", "list"),
            edge("list", "f"),
            edge("f", "kept"),
            edge("in", "sw"),
            edge("sw", "school", "school"),
            edge("sw", "other", "default"),
        ],
    }
    ctx = {"object": MAIL["object"], "items": [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]}
    checked, seen = go(kit, graph, ctx)
    assert checked["nodes"][1]["config"] == {"path": "object", "match": match.check(trip)}
    assert seen["yes"] == ctx and "no" not in seen  # the whole value goes on, as with one test
    assert seen["kept"] == [{"n": 2}, {"n": 3}]
    assert seen["school"] == ctx and "other" not in seen
    _, seen = go(kit, graph, {"object": {"subject": "Parents' evening", "from": ["a@b.c"]}, "items": []})
    assert "no" in seen and "other" in seen and seen["kept"] == []


def test_old_tests_work_as_before_and_one_node_takes_one_kind_of_test(kit):
    graph = {
        "nodes": [
            node("in", "input"),
            node("c", "condition", path="object.subject", op="contains", value="trip"),
            node("yes", "sink", key="yes"),
            node("sw", "switch", path="object.attachments", cases=[{"port": "many", "op": "gt", "value": 1}]),
            node("many", "sink", key="many"),
        ],
        "edges": [edge("in", "c"), edge("c", "yes", "yes"), edge("in", "sw"), edge("sw", "many", "many")],
    }
    checked, seen = go(kit, graph, {"object": MAIL["object"]})
    assert checked["nodes"][1]["config"] == {"path": "object.subject", "op": "contains", "value": "trip"}
    assert seen["yes"] == {"object": MAIL["object"]} and seen["many"] == {"object": MAIL["object"]}
    both = {**graph, "nodes": [*graph["nodes"][:1], node("c", "condition", op="exists", match={"all": []}), *graph["nodes"][2:]]}
    with pytest.raises(ValueError, match="condition c: give one test"):
        flow.check_graph(None, both, kit)
    bad = {**graph, "nodes": [*graph["nodes"][:1], node("c", "condition", match={"any": []}), *graph["nodes"][2:]]}
    with pytest.raises(ValueError, match="condition c: any needs at least one condition"):
        flow.check_graph(None, bad, kit)


def test_saved_workflows_keep_match_trees(db):
    graph = {
        "nodes": [
            node("in", "input"),
            node("c", "condition", path="recording", match={"all": [leaf("title", "contains", "call")]}),
            node("o", "output", key="calls"),
        ],
        "edges": [edge("in", "c"), edge("c", "o", "yes")],
    }
    wid = workflows.create(db, "calls", graph)
    assert workflows.get(db, wid)["graph"]["nodes"][1]["config"]["match"] == {"all": [leaf("title", "contains", "call")]}
