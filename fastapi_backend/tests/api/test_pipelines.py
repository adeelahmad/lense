"""Pipelines: validation, versions, and running one on a recording."""

from __future__ import annotations

import pytest

from app.domain import analyze, ingest, store
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


def test_pipelines_versions_and_runs(client, new_client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "viewer"})
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    tl = {t["name"]: t["id"] for t in client.get("/api/v1/templates", headers=h).json()}

    cat = client.get("/api/v1/pipelines", headers=he).json()
    assert "analyze" in cat["standard"] and "llm" in cat["step_types"] and "min_minutes" in cat["conditions"] and cat["pipelines"] == []
    bad = client.post(
        "/api/v1/pipelines", headers=h, json={"name": "x", "steps": [{"type": "llm", "template": tl["Markdown transcript"], "key": "k"}]}
    )
    assert bad.status_code == 400
    assert client.post("/api/v1/pipelines", headers=h, json={"name": "x", "steps": []}).status_code == 400
    assert client.post("/api/v1/pipelines", headers=h, json={"name": "x", "steps": ["juggle"]}).status_code == 400
    assert client.post("/api/v1/pipelines", headers=h, json={"name": "x", "steps": [{"type": "analyze", "colour": 1}]}).status_code == 400
    steps = [
        "analyze",
        {"type": "llm", "template": tl["Meeting notes"], "key": "meeting_notes"},
        {"type": "summarize", "when": {"min_minutes": 60}},
    ]
    assert client.post("/api/v1/pipelines", headers=he, json={"name": "Notes", "steps": steps}).status_code == 403  # admins only
    r = client.post("/api/v1/pipelines", headers=h, json={"name": "Notes", "steps": steps, "description": "notes"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    p = client.get(f"/api/v1/pipelines/{pid}", headers=he).json()
    assert (p["name"], p["version"], [s["type"] for s in p["steps"]]) == ("Notes", 1, ["analyze", "llm", "summarize"])
    assert client.post(f"/api/v1/pipelines/{pid}/versions", headers=h, json={"steps": ["analyze"], "notes": "less"}).json()["version"] == 2
    assert client.post(f"/api/v1/pipelines/{pid}/versions", headers=h, json={"steps": ["nope"]}).status_code == 400
    assert client.post("/api/v1/pipelines/999/versions", headers=h, json={"steps": ["analyze"]}).status_code == 404
    p = client.get(f"/api/v1/pipelines/{pid}", headers=h).json()
    assert (p["version"], [v["version"] for v in p["history"]], p["history"][0]["notes"]) == (2, [2, 1], "less")
    assert client.get(f"/api/v1/pipelines/{pid}", params={"version": 1}, headers=h).json()["steps"][1]["key"] == "meeting_notes"
    assert client.get("/api/v1/pipelines/999", headers=h).status_code == 404
    assert client.post(f"/api/v1/pipelines/{pid}/versions", headers=h, json={"steps": steps}).json()["version"] == 3

    # running it on one recording: editors of its namespace only
    pods = ingest.import_text(
        db, cfg, "pods", "Alice: The capsid samples ship Friday.\nDave: Courier.\nAlice: Thanks.", title="Courier call"
    )
    call = ingest.import_text(db, cfg, "calls", "Alice: Hi.\nDave: Hello.\nAlice: Bye.", title="Hello")
    analyze.analyze_recording(db, cfg, pods)
    assert client.post(f"/api/v1/pipelines/{pid}/run", headers=he, json={"recording": call}).status_code == 403  # viewer there
    assert client.post(f"/api/v1/pipelines/{pid}/run", headers=he, json={"recording": 999}).status_code == 404
    assert client.post("/api/v1/pipelines/999/run", headers=he, json={"recording": pods}).status_code == 400
    r = client.post(f"/api/v1/pipelines/{pid}/run", headers=he, json={"recording": pods})
    assert r.status_code == 200, r.text
    drain(db, cfg)
    job = db.one("SELECT status, log FROM $j", j=store.R("job", r.json()["job"]))
    assert job["status"] == "succeeded" and "summarize skipped" in " ".join(job["log"])
    out = db.one("SELECT * FROM output WHERE recording = $r AND key = 'meeting_notes'", r=pods)
    assert out["value"]["tldr"] == "Capsid samples ship Friday."
    assert [x["name"] for x in client.get("/api/v1/pipelines", headers=h).json()["pipelines"]] == ["Notes"]
