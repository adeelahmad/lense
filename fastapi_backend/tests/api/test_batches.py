"""Batch runs: estimate, confirmation or a sample first, continue, pause and resume, results, exports and a combined report."""

from __future__ import annotations

import pytest

from tests import fake_llm
from tests.api._assist import Assist, start_llm
from tests.helpers import drain


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def s(app, db, cfg, folder, new_client):
    return Assist(app, db, cfg, folder, new_client)


def test_collections_and_batches(s, db, cfg):
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/collections", headers=h, json={"name": "Capsid talk", "filter": {"namespaces": ["pods"], "q": "capsid"}}).json()[
        "id"
    ]
    assert [r["id"] for r in c.get(f"/api/v1/collections/{cid}", headers=h).json()["recordings"]] == [s.a]
    plan = {"selection": {"namespace": "pods"}, "run": {"template": s.notes}}
    est = c.post("/api/v1/batches/estimate", headers=h, json=plan).json()
    assert (est["recordings"], est["llm"]["calls"], est["needs_confirmation"]) == (2, 2, False)
    assert est["llm"]["input_tokens"] > 0
    ca, ha = s.cl["admin"]
    ca.put("/api/v1/settings/ai", headers=ha, json={"confirm_over_recordings": 1, "price_in": 1.0, "price_out": 2.0})
    est = c.post("/api/v1/batches/estimate", headers=h, json=plan).json()
    assert (est["needs_confirmation"], est["confirm_text"]) == (True, "RUN 2")
    assert est["llm"]["cost"] is not None
    r = c.post("/api/v1/batches", headers=h, json=plan)
    assert r.status_code == 409  # needs the typed confirmation
    assert r.json()["estimate"]["confirm_text"] == "RUN 2"
    bid = c.post("/api/v1/batches", headers=h, json={**plan, "sample": 1}).json()["id"]  # or a sample first
    drain(db, cfg)
    b = c.get(f"/api/v1/batches/{bid}", headers=h).json()
    assert (b["status"], b["progress"]["done"], b["progress"]["remaining"]) == ("sample done", 1, 1)
    c.post(f"/api/v1/batches/{bid}/continue", headers=h)
    drain(db, cfg)
    assert c.get(f"/api/v1/batches/{bid}", headers=h).json()["status"] == "finished"
    table = c.get(f"/api/v1/batches/{bid}/results", headers=h).json()
    assert (table["key"], len(table["rows"]), table["rows"][0]["owner"]) == ("meeting_notes", 2, "Alice")
    assert "task" in c.get(f"/api/v1/batches/{bid}/results.csv", headers=h).text.splitlines()[0]
    assert c.get(f"/api/v1/batches/{bid}/results.md", headers=h).text.startswith("| recording")
    rep = c.post(f"/api/v1/batches/{bid}/combine", headers=h, json={"instructions": "What were the commitments?"}).json()
    assert (rep["text"], rep["key"]) == ("OK", "meeting_notes")  # the fake model's reply, stored on the batch
    assert c.get(f"/api/v1/batches/{bid}", headers=h).json()["report"]["instructions"] == "What were the commitments?"
    b2 = c.post("/api/v1/batches", headers=h, json={"selection": {"collection": cid}, "run": {"steps": ["analyze"]}}).json()["id"]
    c.post(f"/api/v1/batches/{b2}/pause", headers=h)
    assert drain(db, cfg) == 0  # paused jobs aren't picked up
    c.post(f"/api/v1/batches/{b2}/resume", headers=h)
    assert drain(db, cfg) == 1
    cv, hv = s.cl["viewer"]
    assert cv.post("/api/v1/batches", headers=hv, json=plan).status_code == 400  # viewers have nothing they can change
    assert cv.get(f"/api/v1/batches/{bid}", headers=hv).status_code == 404


def test_batch_details(s, db, cfg):
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    cv, hv = s.cl["viewer"]
    plan = {"selection": {"recordings": [s.a, s.b, s.call]}, "run": {"template": s.notes}}
    est = c.post("/api/v1/batches/estimate", headers=h, json=plan).json()
    assert (est["recordings"], est["skipped"], est["label"]) == (2, 0, "Meeting notes (prompt)")  # the call is invisible to them
    assert c.post("/api/v1/batches/estimate", headers=h, json={"run": {"steps": ["juggle"]}}).status_code == 400
    assert c.post("/api/v1/batches/estimate", headers=h, json={"run": {"template": 999}}).status_code == 400
    assert c.post("/api/v1/batches/estimate", headers=h, json={"selection": {"colour": "red"}}).status_code == 422
    assert cv.post("/api/v1/batches/estimate", headers=hv, json=plan).json()["skipped"] == 2  # viewers can't change them
    # someone else's (unshared) collection is invisible
    cid = ca.post("/api/v1/collections", headers=ha, json={"name": "Mine", "recordings": [s.a]}).json()["id"]
    assert (
        c.post("/api/v1/batches/estimate", headers=h, json={"selection": {"collection": cid}, "run": {"steps": ["analyze"]}}).status_code
        == 404
    )

    bid = c.post("/api/v1/batches", headers=h, json=plan).json()["id"]
    assert [x["id"] for x in c.get("/api/v1/batches", headers=h).json()] == [bid]
    assert c.get("/api/v1/batches", headers=h).json()[0]["progress"]["total"] == 2
    assert cv.get("/api/v1/batches", headers=hv).json() == []
    assert [x["id"] for x in ca.get("/api/v1/batches", headers=ha).json()] == [bid]  # admins see every run
    assert ca.get(f"/api/v1/batches/{bid}", headers=ha).status_code == 200
    assert c.post(f"/api/v1/batches/{bid}/combine", headers=h).status_code == 400  # nothing to combine yet
    assert c.post(f"/api/v1/batches/{bid}/explode", headers=h).status_code == 422
    assert cv.post(f"/api/v1/batches/{bid}/cancel", headers=hv).status_code == 404
    assert c.get(f"/api/v1/batches/{bid}/results.xlsx", headers=h).status_code == 422
    r = c.post(f"/api/v1/batches/{bid}/cancel", headers=h)
    assert r.json() == {"ok": True, "result": None}
    assert c.get(f"/api/v1/batches/{bid}", headers=h).json()["status"] == "cancelled"
    assert drain(db, cfg) == 0
    assert c.post(f"/api/v1/batches/{bid}/retry", headers=h).json()["result"] == 0
    assert c.get(f"/api/v1/batches/{bid}/results", headers=h).json()["rows"] == []
    r = c.get(f"/api/v1/batches/{bid}/results.csv", headers=h)
    assert r.headers["content-disposition"] == f'attachment; filename="batch-{bid}.csv"' and r.headers["content-type"].startswith(
        "text/csv"
    )
    assert c.get("/api/v1/batches/999", headers=h).status_code == 404
    actions = {a["action"] for a in ca.get("/api/v1/audit", headers=ha).json()}
    assert {"batch.create", "batch.cancel", "batch.retry"} <= actions
