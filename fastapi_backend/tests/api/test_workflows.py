"""Workflows: graphs checked and versioned, attached to pipelines (drawn as graphs too), chosen per content type, and run
on a recording: entity extraction by rules and by the model, conditions, outputs and custom fields."""

from __future__ import annotations

import pytest

from app.domain import analyze, ingest, jobs, pipelines, store, workflows
from app.domain import fields as fieldmod
from tests import fake_llm
from tests.helpers import drain, login, make_user


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b, branch=None):
    return {"source": a, "target": b, **({"branch": branch} if branch else {})}


def test_workflow_graphs_are_checked(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    cat = client.get("/api/v1/workflows", headers=h).json()
    types = {n["type"]: n for n in cat["node_types"]}
    assert types["condition"]["outputs"] == ["yes", "no"] and types["merge"]["inputs"] == -1 and types["output"]["outputs"] == []
    assert "PERSON" in cat["entity_types"] and cat["workflows"] == []

    def problem(nodes, edges):
        r = client.post("/api/v1/workflows", headers=h, json={"name": "x", "graph": {"nodes": nodes, "edges": edges}})
        assert r.status_code == 400, r.text
        return r.json()["detail"]

    out = node("o", "output", key="k")
    assert "exactly one input" in problem([out], [])
    assert "connect something into" in problem([node("i", "input"), out], [])
    assert "keeps nothing" in problem([node("i", "input"), node("p", "pick", path="a")], [edge("i", "p")])
    assert "one input" in problem([node("i", "input"), node("p", "pick", path="a"), out], [edge("i", "p"), edge("i", "o"), edge("p", "o")])
    assert "loop" in problem(
        [node("i", "input"), node("a", "pick", path="a"), node("b", "merge"), out],
        [edge("i", "b"), edge("b", "a"), edge("a", "b"), edge("a", "o")],
    )
    assert "yes or its no" in problem([node("i", "input"), node("c", "condition", op="exists"), out], [edge("i", "c"), edge("c", "o")])
    assert "prompt template" in problem(
        [node("i", "input"), node("l", "llm", template=tl["Markdown transcript"]), out], [edge("i", "l"), edge("l", "o")]
    )
    assert "doesn't work" in problem(
        [node("i", "input"), node("x", "extract_rules", patterns=[{"pattern": "(", "type": "T"}]), node("s", "save_entities")],
        [edge("i", "x"), edge("x", "s")],
    )
    assert "no setting colour" in problem([node("i", "input"), node("o", "output", key="k", colour=1)], [edge("i", "o")])


def test_workflows_run_in_pipelines_drawn_as_graphs(client, new_client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    rid = ingest.import_text(
        db, cfg, "pods", "Alice: The capsid samples ship Friday, see ticket-42.\nDave: Courier.\nAlice: Thanks.", title="Courier call"
    )
    analyze.analyze_recording(db, cfg, rid)
    sid = store.ns_id(db, "pods")
    tldr = fieldmod.create(db, sid, "TLDR", "text", "resource")
    tldr = tldr if isinstance(tldr, int) else tldr["id"]

    graph = {
        "nodes": [
            node("in", "input"),
            node("rules", "extract_rules", terms=["Courier|ORG"], patterns=[{"pattern": r"ticket-\d+", "type": "TICKET"}]),
            node("ai", "extract_llm", types=["PERSON", "PRODUCT"]),
            node("both", "merge"),
            node("keep", "save_entities"),
            node("notes", "llm", template=tl["Meeting notes"]),
            node("big", "condition", path="tldr", op="contains", value="capsid"),
            node("save", "output", key="notes"),
            node("other", "output", key="not_capsid"),
            node("tl", "pick", path="tldr"),
            node("f", "field", field=tldr),
        ],
        "edges": [
            edge("in", "rules"),
            edge("rules", "ai"),
            edge("rules", "both"),
            edge("ai", "both"),
            edge("both", "keep"),
            edge("in", "notes"),
            edge("notes", "big"),
            edge("big", "save", "yes"),
            edge("big", "other", "no"),
            edge("big", "tl", "yes"),
            edge("tl", "f"),
        ],
    }
    assert client.post("/api/v1/workflows", headers=he, json={"name": "Notes", "graph": graph}).status_code == 403  # admins only
    r = client.post("/api/v1/workflows", headers=h, json={"name": "Notes and entities", "graph": graph})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.post(f"/api/v1/workflows/{wid}/versions", headers=h, json={"graph": graph, "notes": "again"}).json()["version"] == 2
    w = client.get(f"/api/v1/workflows/{wid}", headers=he).json()
    assert (w["version"], [v["version"] for v in w["history"]], len(w["graph"]["nodes"])) == (2, [2, 1], 11)
    assert client.patch(f"/api/v1/workflows/{wid}", headers=h, json={"name": "Notes+"}).status_code == 200

    # a pipeline drawn on the canvas: nodes in any order, edges say what runs after what
    pg = {
        "nodes": [
            {"id": "wf", "step": {"type": "workflow", "workflow": wid}, "x": 500, "y": 0},
            {"id": "an", "step": {"type": "analyze"}, "x": 0, "y": 0},
            {"id": "sum", "step": "summarize", "x": 250, "y": 100},
        ],
        "edges": [{"source": "an", "target": "wf"}, {"source": "an", "target": "sum"}, {"source": "sum", "target": "wf"}],
    }
    bad = {**pg, "edges": pg["edges"] + [{"source": "wf", "target": "an"}]}
    assert "loop" in client.post("/api/v1/pipelines", headers=h, json={"name": "P", "graph": bad}).json()["detail"]
    bad = {"nodes": [{"id": "wf", "step": {"type": "workflow", "workflow": 999}}], "edges": []}
    assert client.post("/api/v1/pipelines", headers=h, json={"name": "P", "graph": bad}).status_code == 400
    r = client.post("/api/v1/pipelines", headers=h, json={"name": "Transcripts", "graph": pg})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    p = client.get(f"/api/v1/pipelines/{pid}", headers=he).json()
    assert [s["type"] for s in p["steps"]] == ["analyze", "summarize", "workflow"] and len(p["graph"]["edges"]) == 3
    assert [x["pipelines"] for x in client.get("/api/v1/workflows", headers=h).json()["workflows"]] == [["Transcripts"]]
    # a pipeline saved as a list is drawn as a chain
    legacy = pipelines.create(db, "Plain", ["analyze", "report"])
    g = client.get(f"/api/v1/pipelines/{legacy}", headers=h).json()["graph"]
    assert [n["step"]["type"] for n in g["nodes"]] == ["analyze", "report"] and g["edges"] == [{"source": "s1", "target": "s2"}]

    # text in pods gets it; other content types keep the default
    assert client.patch("/api/v1/namespaces/pods", headers=h, json={"pipelines": {"podcasts_and_more": pid}}).status_code == 400
    assert client.patch("/api/v1/namespaces/pods", headers=h, json={"pipelines": {"text": pid}}).status_code == 200
    listed = {x["id"]: x for x in client.get("/api/v1/pipelines", headers=h).json()["pipelines"]}
    assert listed[pid]["content_types"] == [{"namespace": "pods", "content_type": "text"}]
    assert pipelines.resolve(db, sid, content_type="audio")[1]["name"] == "Standard"
    jid = jobs.enqueue(db, rid, by="test")
    drain(db, cfg)
    run = client.get(f"/api/v1/jobs/{jid}", headers=he).json()
    assert run["status"] == "succeeded", run
    assert run["pipeline"]["name"] == "Transcripts"
    assert run["steps"][2] == {"type": "workflow", "workflow": wid, "version": 2}  # pinned when it was queued
    assert run["step_runs"][2]["outcome"] == "done" and run["step_runs"][2]["note"] == "workflow ran 9 of 10 nodes"

    out = {o["key"]: o for o in db.rows("SELECT key, value, origin FROM output WHERE recording = $r", r=rid)}
    assert out["notes"]["value"]["tldr"] == "Capsid samples ship Friday." and "not_capsid" not in out
    assert out["notes"]["origin"] == {
        "workflow": wid,
        "workflow_version": 2,
        "template": tl["Meeting notes"],
        "version": 1,
        "model": "fake",
    }
    assert db.one("SELECT fields FROM $r", r=store.R("recording", rid))["fields"] == {fieldmod.slot(tldr): "Capsid samples ship Friday."}
    names = {(e["name"], e["type"]) for e in db.rows("SELECT out.name AS name, out.type AS type FROM mentions WHERE recording = $r", r=rid)}
    assert {("Courier", "ORG"), ("ticket-42", "TICKET"), ("Dave", "PERSON"), ("capsid samples", "PRODUCT")} <= names

    # running one workflow by hand
    assert client.post(f"/api/v1/workflows/{wid}/run", headers=he, json={"recording": 999}).status_code == 404
    assert client.post("/api/v1/workflows/999/run", headers=he, json={"recording": rid}).status_code == 400
    r = client.post(f"/api/v1/workflows/{wid}/run", headers=he, json={"recording": rid, "version": 1})
    assert r.status_code == 200, r.text
    drain(db, cfg)
    assert db.one("SELECT status FROM $j", j=store.R("job", r.json()["job"]))["status"] == "succeeded"


def test_merge_and_conditions():
    ents = [{"name": "Acme", "type": "ORG", "seg": 1}, {"name": "acme", "type": "ORG", "seg": 1}, {"name": "Acme", "type": "ORG", "seg": 2}]
    assert len(workflows.merge([ents[:1], ents[1:]])) == 2
    assert workflows.merge([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}
    assert workflows.dig({"a": [{"b": 3}]}, "a.0.b") == 3 and workflows.dig({"a": []}, "a.0") is None
    assert workflows.test({"op": "gt", "path": "n", "value": 2}, {"n": 3})
    assert not workflows.test({"op": "gt", "path": "n", "value": 2}, {"n": "3"})
    assert workflows.test({"op": "empty", "path": "x"}, {}) and workflows.test({"op": "contains", "value": "b"}, ["a", "b"])
