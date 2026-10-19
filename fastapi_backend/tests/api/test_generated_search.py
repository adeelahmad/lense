"""What a pipeline's model step (or a workflow's model node) writes about an asset is found by search, as "generated"
with the step's name; running it again replaces those words, opting out or deleting the asset takes them away, and
outputs a model wrote before this are made searchable by an upgrade step."""

from __future__ import annotations

import pytest

from app.domain import llm as llmmod
from app.domain import migrations, pipelines, store, workflows
from tests import fake_llm
from tests.api.test_mcp import rpc
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import drain, login, make_user

R = store.R
CALL = "Alice: Samples are packed.\nDave: Courier at noon.\nAlice: Great."  # no "capsid": only the model's notes say it


@pytest.fixture
def llm(cfg):
    fake_llm.Handler.tool_script, fake_llm.Handler.reject_tools = [], False
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    cfg["ai"]["tools"] = False
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _found(client, h, q):
    hits = client.get("/api/v1/search", params={"q": q, "mode": "keyword"}, headers=h).json()["hits"]
    return [x for x in hits if x["source"] == "generated"]


def test_a_pipelines_model_step_is_found_by_search(client, db, cfg, h, monkeypatch):
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    step = {"type": "llm", "name": "Call notes", "template": tl["Meeting notes"], "key": "meeting_notes"}
    pid = client.post("/api/v1/pipelines", headers=h, json={"name": "Notes", "steps": ["analyze", step]}).json()["id"]
    assert client.patch("/api/v1/namespaces/calls", headers=h, json={"pipeline": pid}).status_code == 200
    r = client.post("/api/v1/import", headers=h, json={"namespace": "calls", "title": "Courier call", "text": CALL}).json()
    rid = r["id"]
    drain(db, cfg)
    assert client.get(f"/api/v1/jobs/{r['job']}", headers=h).json()["status"] == "succeeded"

    (hit,) = _found(client, h, "capsid")  # the fake model's notes: "Capsid samples ship Friday."
    assert (hit["recording_id"], hit["output_key"], hit["output_label"]) == (rid, "meeting_notes", "Call notes")
    assert "<mark>Capsid</mark>" in hit["snippet"] and hit["t0"] is None and hit["page"] is None
    assert hit["title"] == "Courier call" and hit["namespace"] == "calls"
    facets = client.get("/api/v1/search", params={"q": "capsid", "mode": "keyword", "facets": True}, headers=h).json()["facets"]
    assert facets["recordings"] == [{"id": rid, "title": "Courier call", "count": 1}]

    # agents: the MCP search tool lists it like the others, saying what wrote it
    _, tokens = _grant(client, h, scope="read")
    res = rpc(client, _bearer(tokens), "tools/call", {"name": "search", "arguments": {"query": "capsid"}})["result"]
    (m,) = [x for x in res["structuredContent"]["results"] if x["found"] == "generated"]
    assert (m["recording_id"], m["output"]) == (rid, "Call notes") and "Capsid" in m["text"]

    # running the step again replaces what it wrote
    monkeypatch.setattr(llmmod, "json_out", lambda *a, **k: {"tldr": "The vaccine trial moves to March.", "decisions": []})
    pipelines.run_llm(db, cfg, rid, step, print)
    assert _found(client, h, "capsid") == []
    assert [x["output_label"] for x in _found(client, h, "vaccine")] == ["Call notes"]

    # a step that says searchable: false keeps its output off search
    pipelines.run_llm(db, cfg, rid, {**step, "searchable": False}, print)
    assert _found(client, h, "vaccine") == []
    assert client.get(f"/api/v1/recordings/{rid}/outputs", headers=h).json()[0]["value"]["tldr"].startswith("The vaccine")
    pipelines.run_llm(db, cfg, rid, step, print)
    assert len(_found(client, h, "vaccine")) == 1

    # deleting the asset takes its words out of search
    assert client.delete(f"/api/v1/recordings/{rid}", headers=h).status_code == 200
    assert _found(client, h, "vaccine") == []
    assert db.rows("SELECT * FROM generated") == []


def test_searchable_is_an_llm_steps_setting(client, db, h):
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    ok = {"type": "llm", "template": tl["Meeting notes"], "key": "notes", "searchable": False}
    assert pipelines.validate_steps(db, [ok])[0]["searchable"] is False
    with pytest.raises(ValueError, match="no setting searchable"):
        pipelines.validate_steps(db, [{"type": "analyze", "searchable": True}])
    with pytest.raises(ValueError, match="true or false"):
        pipelines.validate_steps(db, [{**ok, "searchable": "yes"}])


def test_a_workflows_model_output_is_found_by_search(client, db, cfg, h):
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    graph = {
        "nodes": [
            {"id": "in", "type": "input", "config": {}},
            {"id": "ask", "type": "llm", "config": {"template": tl["Meeting notes"]}},
            {"id": "keep", "type": "output", "config": {"key": "notes"}, "label": "Notes node"},
            {"id": "quiet", "type": "output", "config": {"key": "quiet", "searchable": False}},
            {"id": "title", "type": "pick", "config": {"path": "tldr"}},
        ],
        "edges": [
            {"source": "in", "target": "ask"},
            {"source": "ask", "target": "keep"},
            {"source": "ask", "target": "quiet"},
            {"source": "in", "target": "title"},
        ],
    }
    graph["nodes"].append({"id": "plain", "type": "output", "config": {"key": "plain"}})
    graph["edges"].append({"source": "title", "target": "plain"})  # not from a model: not searchable
    wid = workflows.create(db, "Notes", graph)
    rid = client.post("/api/v1/import", headers=h, json={"namespace": "calls", "title": "Courier call", "text": CALL}).json()["id"]
    drain(db, cfg)
    workflows.run(db, cfg, rid, wid, say=print)
    assert {o["key"] for o in client.get(f"/api/v1/recordings/{rid}/outputs", headers=h).json()} >= {"notes", "quiet"}
    assert [(x["output_key"], x["output_label"]) for x in _found(client, h, "capsid")] == [("notes", "Notes node")]
    assert db.values("SELECT VALUE key FROM generated") == ["notes"]


def test_outputs_a_model_wrote_before_become_searchable(client, db, cfg, h):
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}
    rid = client.post("/api/v1/import", headers=h, json={"namespace": "calls", "title": "Old call", "text": CALL}).json()["id"]
    drain(db, cfg)
    old = {"template": tl["Meeting notes"], "version": 1, "model": "fake"}
    rows = [  # as an older Lens left them: outputs, nothing kept for search
        ("meeting_notes", {"tldr": "Capsid samples ship Friday."}, old),
        ("spec", {"note": 3, "title": "Capsid spec"}, old),  # a spec's note: not the model's words
        ("report_brief", {"url": "/reports/calls/capsid.html"}, {"template": tl["One-page brief"], "version": 1}),
    ]
    for key, value, origin in rows:
        db.q("UPSERT $o CONTENT $d", o=R("output", f"{rid}-{key}"), d={"recording": rid, "key": key, "value": value, "origin": origin})
    assert _found(client, h, "capsid") == []
    step = dict(migrations.steps())["generated-search"]
    step(db)
    once = db.rows("SELECT * FROM generated")
    step(db)  # safe to run twice
    assert db.rows("SELECT * FROM generated") == once
    assert [(x["output_key"], x["output_label"]) for x in _found(client, h, "capsid")] == [("meeting_notes", "Meeting notes")]
