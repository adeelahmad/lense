"""The workflow engine: ports, switch, set and template, loops (for each, repeat), groups, custom nodes (made of
primitives, versioned, pinned, private or shared) and dry runs from the canvas."""

from __future__ import annotations

import pytest

from app.domain import analyze, custom_nodes, flow, ingest, store, workflows
from tests.helpers import login, make_user


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b, port=None, input=None):
    return {"source": a, "target": b, **({"port": port} if port else {}), **({"input": input} if input else {})}


def g(nodes, edges):
    return {"nodes": nodes, "edges": edges}


@pytest.fixture
def rec(db, cfg):
    rid = ingest.import_text(
        db, cfg, "pods", "Alice: Ship the capsid samples Friday.\nDave: I'll call the courier.\nAlice: Thanks Dave.", title="Courier call"
    )
    analyze.analyze_recording(db, cfg, rid)
    return rid


def outputs(db, rid):
    return {o["key"]: o["value"] for o in db.rows("SELECT key, value FROM output WHERE recording = $r", r=rid)}


def run(db, cfg, rid, graph):
    wid = workflows.create(db, "t", graph)
    return workflows.run(db, cfg, rid, wid, say=lambda m: None)


def test_switch_set_and_template(db, cfg, rec):
    graph = g(
        [
            node("in", "input"),
            node("n", "pick", path="recording.title"),
            node(
                "sw",
                "switch",
                cases=[{"port": "call", "op": "contains", "value": "call"}, {"port": "meet", "op": "contains", "value": "meeting"}],
            ),
            node(
                "call",
                "set",
                inputs=["title", "rec"],
                fields=[{"key": "kind", "value": "call"}, {"key": "who", "path": "rec.speakers"}, {"key": "t", "path": "title"}],
            ),
            node("meet", "output", key="meeting"),
            node("other", "output", key="other"),
            node("txt", "template", template="{{ input.t }} ({{ input.kind }}) in {{ recording.namespace }}"),
            node("o1", "output", key="kind"),
            node("o2", "output", key="line"),
        ],
        [
            edge("in", "n"),
            edge("n", "sw"),
            edge("sw", "call", "call", "title"),
            edge("in", "call", input="rec"),
            edge("sw", "meet", "meet"),
            edge("sw", "other", "default"),
            edge("call", "o1"),
            edge("call", "txt"),
            edge("txt", "o2"),
        ],
    )
    done = run(db, cfg, rec, graph)
    assert done["meet"] == done["other"] == "skipped" and done["o2"] == "done"
    out = outputs(db, rec)
    assert out["kind"]["kind"] == "call" and out["kind"]["t"] == "Courier call" and isinstance(out["kind"]["who"], list)
    assert out["line"] == "Courier call (call) in pods" and "meeting" not in out


def test_for_each_and_repeat(db, cfg, rec):
    # for each line: keep the ones Alice said, as text (an item the body returns nothing for is left out)
    body = g(
        [
            node("item", "arg", name="item", **{}),
            node("alice", "condition", path="speaker", op="equals", value="Alice"),
            node("text", "pick", path="text"),
            node("ret", "return", name="out"),
        ],
        [edge("item", "alice"), edge("alice", "text", "yes"), edge("text", "ret")],
    )
    count = g(
        [
            node("s", "arg", name="state"),
            node("next", "template", template='{"n": {{ input.n + 1 }}}', json=True),
            node("ret", "return", name="out"),
        ],
        [edge("s", "next"), edge("next", "ret")],
    )
    graph = g(
        [
            node("in", "input"),
            node("lines", "for_each", path="segments", body=body),
            node("said", "output", key="alice_said"),
            node("zero", "set", fields=[{"key": "n", "value": 0}]),
            node("count", "repeat", body=count, max_rounds=10, until={"path": "n", "op": "gt", "value": 3}),
            node("counted", "output", key="counted"),
        ],
        [edge("in", "lines"), edge("lines", "said"), edge("in", "zero"), edge("zero", "count"), edge("count", "counted")],
    )
    run(db, cfg, rec, graph)
    out = outputs(db, rec)
    assert out["alice_said"] == ["Ship the capsid samples Friday.", "Thanks Dave."]
    assert out["counted"] == {"n": 4}

    # the budget stops runaway loops
    old = flow.MAX_STEPS
    flow.MAX_STEPS = 5
    try:
        with pytest.raises(ValueError, match="was stopped"):
            run(db, cfg, rec, graph)
    finally:
        flow.MAX_STEPS = old


def test_graphs_are_checked(db):
    def problem(nodes, edges):
        with pytest.raises(ValueError) as e:
            workflows.validate_graph(db, g(nodes, edges))
        return str(e.value)

    out = node("o", "output", key="k")
    assert "for each or repeat" in problem(
        [node("i", "input"), node("a", "pick", path="a"), node("b", "merge"), out],
        [edge("i", "b"), edge("b", "a"), edge("a", "b"), edge("a", "o")],
    )
    assert "has no output maybe" in problem([node("i", "input"), out], [edge("i", "o", "maybe")])
    assert "has no input x" in problem([node("i", "input"), out], [edge("i", "o", input="x")])
    assert "passes nothing on" in problem([node("i", "input"), out, node("o2", "output", key="j")], [edge("i", "o"), edge("o", "o2")])
    sw = node("s", "switch", cases=[{"port": "default", "op": "exists"}])
    assert "not default" in problem([node("i", "input"), sw, out], [edge("i", "s"), edge("s", "o", "default")])
    assert "belong in the body" in problem([node("i", "input"), node("a", "arg", name="x"), out], [edge("i", "o")])
    no_out = g([node("a", "arg", name="item"), node("r", "return", name="other")], [edge("a", "r")])
    assert "returns out" in problem([node("i", "input"), node("f", "for_each", body=no_out), out], [edge("i", "f"), edge("f", "o")])
    bad_arg = g([node("a", "arg", name="thing"), node("r", "return", name="out")], [edge("a", "r")])
    assert "has item, index, input" in problem(
        [node("i", "input"), node("f", "for_each", body=bad_arg), out], [edge("i", "f"), edge("f", "o")]
    )
    two = node("st", "set", inputs=["a", "b"], fields=[{"key": "x", "path": "a.x"}])
    assert "connect input b" in problem([node("i", "input"), two, out], [edge("i", "st", input="a"), edge("st", "o")])
    assert "start the path with an input" in problem(
        [node("i", "input"), node("st", "set", inputs=["a", "b"], fields=[{"key": "x", "path": "c"}]), out], [edge("i", "st", input="a")]
    )
    # a group's ports are its body's args and returns; a body that saves keeps something
    body = g([node("a", "arg", name="text"), node("o", "output", key="k")], [edge("a", "o")])
    clean = workflows.validate_graph(db, g([node("i", "input"), node("gr", "group", body=body)], [edge("i", "gr", input="text")]))
    assert clean["edges"] == [{"source": "i", "target": "gr", "input": "text"}]
    # a condition's old branch edges are its ports
    clean = workflows.validate_graph(
        db,
        g(
            [node("i", "input"), node("c", "condition", op="exists"), out],
            [{"source": "i", "target": "c"}, {"source": "c", "target": "o", "branch": "no"}],
        ),
    )
    assert clean["edges"][1] == {"source": "c", "target": "o", "port": "no"}


def test_custom_nodes_are_shared_versioned_and_pinned(client, new_client, db, cfg, rec):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1", roles={"other": "editor"})
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    hv = login(new_client(), "vi@x.io", "viewer password 1")
    ho = login(new_client(), "out@x.io", "outsider password 1")

    # a primitive extended: a condition on the title with the word to look for as a parameter
    body = g(
        [
            node("a", "arg", name="in"),
            node("t", "pick", path="recording.title"),
            node("c", "condition", op="contains", value={"$param": "word"}),
            node("yes", "return", name="match"),
            node("no", "return", name="other"),
        ],
        [edge("a", "t"), edge("t", "c"), edge("c", "yes", "yes"), edge("c", "no", "no")],
    )
    params = [{"name": "word", "label": "Word", "kind": "text", "default": "call"}]
    spec = {"name": "Title has", "graph": body, "params": params, "icon": "git-branch", "color": "purple"}
    r = client.post("/api/v1/custom-nodes", headers=he, json=spec)
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    got = client.get(f"/api/v1/custom-nodes/{cid}", headers=he).json()
    assert (got["inputs"], got["outputs"], got["scopes"], got["keeps"]) == (["in"], ["match", "other"], ["recording", "graph"], False)
    assert got["graph"]["nodes"][2]["config"]["value"] == {"$param": "word"} and got["editable"]

    # private: the owner and admins see it, nobody else
    assert client.get(f"/api/v1/custom-nodes/{cid}", headers=hv).status_code == 404
    assert [n["id"] for n in client.get("/api/v1/custom-nodes", headers=h).json()] == [cid]
    # shared with pods: its viewer sees it but can't change it; someone outside pods doesn't see it
    assert (
        client.patch(f"/api/v1/custom-nodes/{cid}", headers=he, json={"visibility": "namespace", "namespaces": ["other"]}).status_code
        == 400
    )
    assert (
        client.patch(f"/api/v1/custom-nodes/{cid}", headers=he, json={"visibility": "namespace", "namespaces": ["pods"]}).status_code == 200
    )
    seen = client.get("/api/v1/custom-nodes", headers=hv).json()
    assert [n["namespaces"] for n in seen] == [["pods"]] and not seen[0]["editable"]
    assert client.post(f"/api/v1/custom-nodes/{cid}/versions", headers=hv, json={"graph": body, "params": params}).status_code == 403
    assert client.get("/api/v1/custom-nodes", headers=ho).json() == []
    assert client.patch(f"/api/v1/custom-nodes/{cid}", headers=he, json={"visibility": "everyone"}).status_code == 200
    assert len(client.get("/api/v1/workflows", headers=ho).json()["custom_nodes"]) == 1

    # used in a workflow (pinned to version 1), with its parameter set
    graph = g(
        [
            node("in", "input"),
            node("is", "custom", node=cid, params={"word": "courier"}),
            node("y", "output", key="yes"),
            node("n", "output", key="no"),
        ],
        [edge("in", "is"), edge("is", "y", "match"), edge("is", "n", "other")],
    )
    assert (
        "parameter colour"
        in client.post(
            "/api/v1/workflows",
            headers=h,
            json={
                "name": "x",
                "graph": {
                    **graph,
                    "nodes": [*graph["nodes"][:1], node("is", "custom", node=cid, params={"colour": 1}), *graph["nodes"][2:]],
                },
            },
        ).json()["detail"]
    )
    r = client.post("/api/v1/workflows", headers=h, json={"name": "Courier?", "graph": graph})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.get(f"/api/v1/workflows/{wid}", headers=h).json()["graph"]["nodes"][1]["config"] == {
        "node": cid,
        "version": 1,
        "params": {"word": "courier"},
    }
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    assert set(outputs(db, rec)) == {"yes"}

    # version 2 swaps the outputs; the workflow keeps running version 1 until it is saved again
    flipped = {**body, "edges": [edge("a", "t"), edge("t", "c"), edge("c", "yes", "no"), edge("c", "no", "yes")]}
    assert client.post(f"/api/v1/custom-nodes/{cid}/versions", headers=he, json={"graph": flipped, "params": params}).json()["version"] == 2
    db.q("DELETE output")
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    assert set(outputs(db, rec)) == {"yes"}
    unpinned = {**graph, "nodes": [graph["nodes"][0], node("is", "custom", node=cid, params={"word": "courier"}), *graph["nodes"][2:]]}
    workflows.save_version(db, wid, unpinned)
    db.q("DELETE output")
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    assert set(outputs(db, rec)) == {"no"}

    # removed: off the palette, but pinned workflows keep running it
    assert client.delete(f"/api/v1/custom-nodes/{cid}", headers=hv).status_code == 403
    assert client.delete(f"/api/v1/custom-nodes/{cid}", headers=he).status_code == 200
    assert client.get("/api/v1/custom-nodes", headers=he).json() == []
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    with pytest.raises(ValueError, match="was removed"):
        workflows.save_version(db, wid, unpinned)

    # a custom node can't use itself, nor mix recording and graph nodes
    me = custom_nodes.who(1, "root@x.io", True, {})
    keep = g([node("a", "arg", name="in"), node("o", "output", key="k")], [edge("a", "o")])
    k = custom_nodes.create(db, me, "Keep", keep)
    assert custom_nodes.get(db, k)["keeps"] and custom_nodes.get(db, k)["scopes"] == ["recording"]
    with pytest.raises(ValueError, match="use itself"):
        custom_nodes.save_version(db, me, k, g([node("a", "arg", name="in"), node("s", "custom", node=k)], [edge("a", "s")]))
    mixed = g([node("a", "arg", name="in"), node("o", "output", key="k"), node("ap", "apply_changes")], [edge("a", "o"), edge("a", "ap")])
    with pytest.raises(ValueError, match="not both"):
        custom_nodes.create(db, me, "Mixed", mixed)
    # a workflow whose only saving node is inside a custom node keeps something
    workflows.validate_graph(db, g([node("in", "input"), node("k", "custom", node=k)], [edge("in", "k")]))


def test_try_on_the_canvas_keeps_nothing(client, new_client, db, cfg, rec):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    body = g(
        [node("a", "arg", name="item"), node("p", "pick", path="speaker"), node("r", "return", name="out")],
        [edge("a", "p"), edge("p", "r")],
    )
    graph = g(
        [
            node("in", "input"),
            node("each", "for_each", path="segments", body=body),
            node("big", "condition", op="contains", value="Dave"),
            node("keep", "output", key="speakers"),
            node("nope", "output", key="none"),
        ],
        [edge("in", "each"), edge("each", "big"), edge("big", "keep", "yes"), edge("big", "nope", "no")],
    )
    req = {"graph": graph, "recording": rec}
    assert client.post("/api/v1/workflows/test", headers=he, json=req).status_code == 403  # admins only, as editing workflows
    r = client.post("/api/v1/workflows/test", headers=h, json=req)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["error"] is None and t["trace"]["each"]["value"] == '["Alice", "Dave", "Alice"]'
    assert t["trace"]["each/p"]["value"] == '"Alice"' and t["trace"]["big"]["ports"] == ["yes"]
    assert t["trace"]["nope"]["status"] == "skipped" and "would save output speakers" in t["log"]
    assert outputs(db, rec) == {}
    # a failure is shown on the node it happened in
    bad = {**graph, "nodes": [*graph["nodes"][:1], node("each", "for_each", path="recording", body=body), *graph["nodes"][2:]]}
    t = client.post("/api/v1/workflows/test", headers=h, json={"graph": bad, "recording": rec}).json()
    assert "needs a list" in t["error"] and t["trace"]["each"]["status"] == "failed"
    assert client.post("/api/v1/workflows/test", headers=h, json={"graph": graph}).status_code == 400
    # graph workflows try over namespaces, proposing nothing
    gg = g(
        [node("in", "input"), node("pairs", "candidates", kind="merge"), node("ap", "apply_changes", apply_above=0.5)],
        [edge("in", "pairs"), edge("pairs", "ap")],
    )
    t = client.post("/api/v1/workflows/test", headers=h, json={"graph": gg, "scope": "graph", "namespaces": ["pods"]}).json()
    assert t["error"] is None and t["trace"]["ap"]["status"] == "done"
    assert db.rows("SELECT * FROM graph_change") == []
    assert store.space_names(db)


def test_catalog_lists_ports_and_primitives(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    types = {n["type"]: n for n in client.get("/api/v1/workflows", headers=h).json()["node_types"]}
    assert types["switch"]["dynamic"] == "outputs" and types["switch"]["primitive"]
    assert types["for_each"]["scopes"] == ["recording", "graph"] and types["output"]["keeps"]
    assert types["arg"]["inputs"] == 0 and types["return"]["outputs"] == [] and types["merge"]["inputs"] == -1
