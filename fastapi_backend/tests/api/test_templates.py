"""Templates (with previews against a fake model) and pipelines built from them, run on import."""

from __future__ import annotations

import shutil

import pytest

from app.domain import analyze, ingest
from tests import fake_llm
from tests.helpers import drain, login, make_user


@pytest.fixture
def llm(cfg, folder):
    fake_llm.Handler.tool_script, fake_llm.Handler.reject_tools = [], False
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    cfg["ai"]["tools"] = False
    (folder / "inbox").mkdir()
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


@pytest.mark.skipif(not shutil.which("rclone"), reason="rclone is not installed")
def test_templates_pipelines_outputs(client, db, cfg, folder, h):
    c = client
    tl = {t["name"]: t["id"] for t in c.get("/api/v1/templates", headers=h).json()}
    assert set(tl) == {"Meeting notes", "Markdown transcript", "One-page brief"}
    assert c.post("/api/v1/templates", headers=h, json={"name": "Bad", "kind": "export", "body": "{% for x in %}"}).status_code == 400
    rid = ingest.import_text(
        db, cfg, "calls", "Alice: The capsid samples ship Friday.\nDave: I'll confirm the courier.\nAlice: Thanks.", title="Courier call"
    )
    analyze.analyze_recording(db, cfg, rid)
    pv = c.post(
        "/api/v1/templates/preview",
        headers=h,
        json={"recording": rid, "kind": "export", "body": "{{ recording.title }}: {{ segments | length }} lines"},
    ).json()
    assert pv["rendered"] == "Courier call: 3 lines"
    assert not c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "body": "{{ ''.__class__ }}"}).json()["ok"]
    ran = c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "template": tl["Meeting notes"], "run": True}).json()
    assert ran["result"]["action_items"][0]["owner"] == "Alice"
    dest = c.post("/api/v1/sources", headers=h, json={"name": "exports", "type": "local"}).json()["id"]
    steps = [
        "analyze",
        {"type": "llm", "template": tl["Meeting notes"], "key": "meeting_notes"},
        {"type": "report", "template": tl["One-page brief"]},
        {
            "type": "export",
            "template": tl["Markdown transcript"],
            "filename": "{{ recording.title }}.md",
            "destination": {"source": dest, "path": str(folder / "inbox")},
        },
        {"type": "summarize", "when": {"min_minutes": 60}},
    ]
    bad = c.post(
        "/api/v1/pipelines", headers=h, json={"name": "x", "steps": [{"type": "llm", "template": tl["Markdown transcript"], "key": "k"}]}
    )
    assert bad.status_code == 400
    pid = c.post("/api/v1/pipelines", headers=h, json={"name": "Notes", "steps": steps}).json()["id"]
    assert c.patch("/api/v1/namespaces/calls", headers=h, json={"pipeline": pid}).status_code == 200
    r = c.post(
        "/api/v1/import",
        headers=h,
        json={"namespace": "calls", "title": "Second call", "text": "Alice: Samples are packed.\nDave: Courier at noon.\nAlice: Great."},
    )
    jid, rid2 = r.json()["job"], r.json()["id"]
    drain(db, cfg)
    j = c.get(f"/api/v1/jobs/{jid}", headers=h).json()
    assert j["status"] == "succeeded", j.get("error")
    assert "summarize skipped" in " ".join(j["log"])  # condition: under an hour
    outs = {o["key"]: o["value"] for o in c.get(f"/api/v1/recordings/{rid2}/outputs", headers=h).json()}
    assert outs["meeting_notes"]["tldr"] == "Capsid samples ship Friday."
    page = c.get(outs["report_one_page_brief"]["url"], headers=h)
    assert page.status_code == 200
    assert "script-src 'none'" in page.headers["content-security-policy"]  # templates can't run scripts
    assert (folder / "inbox" / "Second call.md").exists()  # exported to the storage source
    tid = tl["Markdown transcript"]
    assert (
        c.post(f"/api/v1/templates/{tid}/versions", headers=h, json={"body": "# {{ recording.title }}\n", "notes": "short"}).json()[
            "version"
        ]
        == 2
    )
    assert "-{{ recording.recorded_at }}" in c.get(f"/api/v1/templates/{tid}/diff", params={"a": 1, "b": 2}, headers=h).text


def test_templates_previews_and_versions(client, new_client, db, cfg, h):
    """The template parts of the flow above that need no rclone, plus who may do what."""
    c = client
    tl = {t["name"]: t["id"] for t in c.get("/api/v1/templates", headers=h).json()}
    assert set(tl) == {"Meeting notes", "Markdown transcript", "One-page brief"}
    assert c.post("/api/v1/templates", headers=h, json={"name": "Bad", "kind": "export", "body": "{% for x in %}"}).status_code == 400
    assert c.post("/api/v1/templates", headers=h, json={"name": " ", "kind": "export", "body": "x"}).status_code == 400
    assert c.post("/api/v1/templates", headers=h, json={"name": "x", "kind": "poem", "body": "x"}).status_code == 422
    r = c.post(
        "/api/v1/templates", headers=h, json={"name": "Titles", "kind": "export", "body": "{{ recording.title }}", "description": "t"}
    )
    assert r.status_code == 200, r.text
    new = r.json()["id"]

    rid = ingest.import_text(
        db, cfg, "calls", "Alice: The capsid samples ship Friday.\nDave: I'll confirm the courier.\nAlice: Thanks.", title="Courier call"
    )
    analyze.analyze_recording(db, cfg, rid)
    pv = c.post(
        "/api/v1/templates/preview",
        headers=h,
        json={"recording": rid, "kind": "export", "body": "{{ recording.title }}: {{ segments | length }} lines"},
    ).json()
    assert pv["rendered"] == "Courier call: 3 lines"
    bad = c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "body": "{{ ''.__class__ }}"}).json()
    assert not bad["ok"] and bad["error"]
    ran = c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "template": tl["Meeting notes"], "run": True}).json()
    assert ran["result"]["action_items"][0]["owner"] == "Alice" and ran["kind"] == "prompt"
    saved = c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "template": new}).json()
    assert (saved["ok"], saved["rendered"]) == (True, "Courier call")
    assert c.post("/api/v1/templates/preview", headers=h, json={"recording": rid, "template": 999}).status_code == 404
    assert c.post("/api/v1/templates/preview", headers=h, json={"recording": 999, "body": "x"}).status_code == 404

    # someone who can't read the recording's namespace can't preview against it
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(new_client(), "ed@x.io", "editor password 1")
    assert c.post("/api/v1/templates/preview", headers=he, json={"recording": rid, "body": "x"}).status_code == 404
    assert c.post("/api/v1/templates", headers=he, json={"name": "x", "kind": "export", "body": "x"}).status_code == 403  # admins only
    assert c.get("/api/v1/templates", headers=he).status_code == 200

    tid = tl["Markdown transcript"]
    assert (
        c.post(f"/api/v1/templates/{tid}/versions", headers=h, json={"body": "# {{ recording.title }}\n", "notes": "short"}).json()[
            "version"
        ]
        == 2
    )
    assert "-{{ recording.recorded_at }}" in c.get(f"/api/v1/templates/{tid}/diff", params={"a": 1, "b": 2}, headers=h).text
    assert c.post(f"/api/v1/templates/{tid}/versions", headers=h, json={"body": "{% if %}"}).status_code == 400
    assert c.post(f"/api/v1/templates/{tid}/versions", headers=h, json={"body": "draft", "publish": False}).json()["version"] == 3
    t = c.get(f"/api/v1/templates/{tid}", headers=h).json()
    assert (t["version"], t["current"], t["body"]) == (2, 2, "# {{ recording.title }}\n")
    assert [v["version"] for v in t["history"]] == [3, 2, 1] and t["history"][1]["notes"] == "short"
    assert c.get(f"/api/v1/templates/{tid}", params={"version": 3}, headers=h).json()["body"] == "draft"
    notes = c.get(f"/api/v1/templates/{tl['Meeting notes']}", headers=h).json()
    assert notes["kind"] == "prompt" and "action_items" in notes["schema"]["properties"]
    assert c.get("/api/v1/templates/999", headers=h).status_code == 404
    assert c.get(f"/api/v1/templates/{tid}", params={"version": 9}, headers=h).status_code == 404
    assert c.get(f"/api/v1/templates/{tid}/diff", params={"a": 1, "b": 9}, headers=h).status_code == 404
    assert c.post("/api/v1/templates/999/versions", headers=h, json={"body": "x"}).status_code == 404
    assert [v["versions"] for v in c.get("/api/v1/templates", headers=h).json() if v["id"] == tid] == [3]
