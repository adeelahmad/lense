"""The assistant running the server: a setup conversation that makes the changes itself, the same tools asking for
approval elsewhere, and files dropped into a conversation that it imports."""

from __future__ import annotations

import json

import pytest

from app.domain import settings, setup, store
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.helpers import write_wav


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def held(c, h, data, name="talk.wav"):
    """A file uploaded to attach to a message: held, not in the archive."""
    r = c.post("/api/v1/uploads", headers=h, json={"filename": name, "size": len(data), "hold": True})
    assert r.status_code == 201, r.text
    up = r.json()
    r = c.put(f"/api/v1/uploads/{up['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_setup_conversation_sets_the_server_up(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    monkeypatch.setattr(
        setup, "detect_llm", lambda: [{"kind": "Ollama", "base_url": "http://x:11434/v1", "models": ["m"], "suggested": "m"}]
    )
    wav = folder / "memo.wav"
    write_wav(wav, seconds=1.5)
    up = held(c, h, wav.read_bytes(), "memo.wav")
    assert (up["state"], up["recording"], up["namespace"]) == ("held", None, "")
    recordings = len(db.values("SELECT VALUE id FROM recording"))

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "server_status", {}), call(2, "find_model_servers", {})]},
        {"content": "", "tool_calls": [call(3, "change_settings", {"section": "embeddings", "changes": {"model": "nomic-embed-text"}})]},
        {"content": "", "tool_calls": [call(4, "create_namespace", {"name": "family", "graph": "isolated"})]},
        {"content": "", "tool_calls": [call(5, "import_files", {"upload_ids": [up["id"]], "namespace": "family"})]},
        {"content": "", "tool_calls": [call(6, "change_settings", {"section": "telemetry", "changes": {"enabled": False}})]},
        {"content": "Set up search by meaning, made the family namespace and put your memo in it."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    convo = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (convo["kind"], convo["title"]) == ("setup", "Set up Lens")
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "", "attachments": [up["id"]]}).text)
    assert [x["tool"] for x in ev["step"]] == [
        "server_status",
        "find_model_servers",
        "change_settings",
        "create_namespace",
        "import_files",
        "change_settings",
    ]
    status = json.loads(llm.seen[-1]["messages"][3]["content"])  # what server_status told the model
    assert status["namespaces"] == ["calls", "pods"] and "missing" in status
    asked = llm.seen[-6]["messages"]
    assert "setting up this Lens server" in asked[0]["content"]
    assert f"attachment id {up['id']}" in asked[-1]["content"]  # the model hears about the file
    # made at once, without asking...
    assert settings.effective(db, cfg)["embeddings"]["model"] == "nomic-embed-text"
    assert "family" in store.space_names(db).values()
    assert len(db.values("SELECT VALUE id FROM recording")) == recordings + 1
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "done"
    # ...except telemetry, which sends data elsewhere
    assert [a["summary"] for a in ev["approval"]] == ["Set telemetry: enabled = False"]
    msgs = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert msgs[0]["attachments"][0]["filename"] == "memo.wav" and msgs[0]["content"] == "I attached this file."
    audit = db.values("SELECT VALUE action FROM audit_log WHERE detail CONTAINS 'assistant'")
    assert {"settings.save", "namespace.create"} <= set(audit)


def test_elsewhere_the_server_tools_ask_first(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "change_settings", {"section": "embeddings", "changes": {"model": "bge-m3"}})]},
        {"content": "I asked to switch the embedding model."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "use bge-m3 for search"}).text)
    appr = ev["approval"][0]
    assert settings.effective(db, cfg)["embeddings"]["model"] != "bge-m3"
    assert c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert settings.effective(db, cfg)["embeddings"]["model"] == "bge-m3"


def test_only_admins_get_the_server_tools(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ce, he = s.cl["editor"]
    assert ce.post("/api/v1/chats", headers=he, json={"kind": "setup"}).status_code == 403
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "change_settings", {"section": "llm", "changes": {"model": "x"}})]},
        {"content": "Done."},
    ]
    wav = folder / "e.wav"
    write_wav(wav, seconds=1)
    up = held(ce, he, wav.read_bytes(), "e.wav")
    cid = ce.post("/api/v1/chats", headers=he, json={}).json()["id"]
    ev = sse(ce.post(f"/api/v1/chats/{cid}/messages", headers=he, json={"content": "hi", "attachments": [up["id"]]}).text)
    offered = {t["function"]["name"] for t in llm.seen[-2]["tools"]}
    assert "import_files" in offered and not offered & {"change_settings", "server_status", "create_namespace"}
    assert ev["step"][0]["summary"] == "unknown tool change_settings"
    # someone else's file can't be attached
    ca, ha = s.cl["admin"]
    other = ca.post("/api/v1/chats", headers=ha, json={}).json()["id"]
    assert ca.post(f"/api/v1/chats/{other}/messages", headers=ha, json={"content": "x", "attachments": [up["id"]]}).status_code == 404


def test_a_held_upload_stays_out_until_placed(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    wav = folder / "h.wav"
    write_wav(wav, seconds=1)
    data = wav.read_bytes()
    assert (
        c.post("/api/v1/uploads", headers=h, json={"filename": "h.wav", "size": len(data), "hold": True, "pipeline": 1}).status_code == 400
    )
    up = held(c, h, data, "h.wav")
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "held"
    from app.domain import uploads

    with pytest.raises(KeyError):  # only its uploader places it
        uploads.place(db, cfg, up["id"], "pods", 999)
    with pytest.raises(KeyError):  # not one of the namespace's collections
        uploads.place(
            db, cfg, up["id"], "pods", db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'ed@x.io'")[0], collection=12345
        )
