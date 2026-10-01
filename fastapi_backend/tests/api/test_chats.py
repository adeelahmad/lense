"""Chat: answers cite only what the asker can read; the assistant's tools, approvals, and checking an answer."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, auth, chat, store
from app.domain import llm as llm_mod
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.helpers import drain, login, make_user, seed


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
def plain(cfg):
    """The plain search-and-answer path (no tools)."""
    cfg["ai"]["tools"] = False


def test_chat_cites_only_what_you_can_read(plain, client, new_client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    ca, cv = client, new_client()
    ha, hv = login(ca, "root@x.io", "root password 1"), login(cv, "vi@x.io", "viewer password 1")
    q = "When does the Dyno Therapeutics shipment leave?"
    cid = ca.post("/api/v1/chats", headers=ha, json={}).json()["id"]
    ev = sse(ca.post(f"/api/v1/chats/{cid}/messages", headers=ha, json={"content": q}).text)
    assert ev["passages"][0][0]["namespace"] == "calls"
    assert "".join(e["text"] for e in ev["token"]) == "The shipment leaves on Friday [1]."
    convo = ca.get(f"/api/v1/chats/{cid}", headers=ha).json()
    assert ([m["role"] for m in convo["messages"]], convo["title"]) == (["user", "assistant"], q)
    assert convo["messages"][1]["passages"][0]["used"]
    assert cv.get(f"/api/v1/chats/{cid}", headers=hv).status_code == 404  # someone else's conversation
    vid = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    ev = sse(cv.post(f"/api/v1/chats/{vid}/messages", headers=hv, json={"content": q}).text)
    assert ev["passages"][0] and {p["namespace"] for p in ev["passages"][0]} == {"pods"}
    assert cv.post("/api/v1/chats", headers=hv, json={"scope": {"namespaces": ["calls"]}}).status_code == 404
    assert ca.put("/api/v1/settings/llm", headers=ha, json={"base_url": None}).status_code == 200
    ev = sse(ca.post(f"/api/v1/chats/{cid}/messages", headers=ha, json={"content": "shipment Friday"}).text)
    assert "No language model is configured" in ev["token"][0]["text"]


def test_chat_crud_and_scope(plain, client, new_client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv = login(client, "vi@x.io", "viewer password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    assert client.post("/api/v1/chats", headers=hv).status_code == 200  # no body at all
    assert client.post("/api/v1/chats", headers=hv, json={"scope": {"recordings": [call]}}).status_code == 404
    assert client.post("/api/v1/chats", headers=hv, json={"scope": {"colour": "red"}}).status_code == 422
    dated = client.post("/api/v1/chats", headers=hv, json={"scope": {"from": "2000-01-01", "to": "2001-01-01", "speakers": []}}).json()[
        "id"
    ]
    assert client.get(f"/api/v1/chats/{dated}", headers=hv).json()["scope"] == {
        "from": "2000-01-01",
        "to": "2001-01-01",
    }  # empty keys dropped
    r = client.post("/api/v1/chats", headers=hv, json={"title": "Episode 2", "scope": {"recordings": [b]}})
    cid = r.json()["id"]
    c = client.get(f"/api/v1/chats/{cid}", headers=hv).json()
    assert (c["title"], c["scope"], c["messages"]) == ("Episode 2", {"recordings": [b]}, [])
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "Dyno Therapeutics capsid"}).text)
    assert {p["recording_id"] for p in ev["passages"][0]} == {b}  # the scope limits what it draws on
    assert client.get(f"/api/v1/chats/{cid}", headers=hv).json()["title"] == "Episode 2"  # a title you set is kept
    assert client.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "   "}).status_code == 400
    assert client.post(f"/api/v1/chats/{cid}/messages", headers=he, json={"content": "hi"}).status_code == 404

    assert client.patch(f"/api/v1/chats/{cid}", headers=hv, json={"title": "Renamed", "scope": {"namespaces": ["pods"]}}).status_code == 200
    c = client.get(f"/api/v1/chats/{cid}", headers=hv).json()
    assert (c["title"], c["scope"]) == ("Renamed", {"namespaces": ["pods"]})
    assert client.patch(f"/api/v1/chats/{cid}", headers=hv, json={"scope": {"namespaces": ["calls"]}}).status_code == 404
    assert client.patch(f"/api/v1/chats/{cid}", headers=he, json={"title": "x"}).status_code == 404
    assert cid in [x["id"] for x in client.get("/api/v1/chats", headers=hv).json()]
    assert client.get("/api/v1/chats", headers=he).json() == []

    # citations follow current access: lose the namespace, lose the citations
    auth.set_role(db, db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'vi@x.io'")[0], store.ns_id(db, "pods"), None)
    msgs = client.get(f"/api/v1/chats/{cid}", headers=hv).json()["messages"]
    assert msgs[1]["role"] == "assistant" and msgs[1]["passages"] == []

    assert client.delete(f"/api/v1/chats/{cid}", headers=he).status_code == 404
    assert client.delete(f"/api/v1/chats/{cid}", headers=hv).status_code == 200
    assert client.get(f"/api/v1/chats/{cid}", headers=hv).status_code == 404
    assert db.values("SELECT VALUE id FROM chat_message WHERE chat = $c", c=cid) == []


def test_tools_approvals_and_checking(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]

    def call(i, name, args):
        return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "Dyno Therapeutics"})]},
        {"content": "", "tool_calls": [call(2, "run_template", {"template_id": s.notes, "recording_ids": [s.a]})]},
        {
            "content": "Dyno Therapeutics designs capsids with machine learning [1]. It was founded in 1850 [2]. "
            "I asked to run meeting notes on that episode."
        },
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What does Dyno do? Summarise that episode too."}).text)
    assert [x["tool"] for x in ev["step"]] == ["search_transcripts", "run_template"]
    assert ev["step"][0]["summary"].startswith('Searched for "Dyno Therapeutics"')
    assert {p["namespace"] if "namespace" in p else "pods" for p in ev["passages"][0]} == {"pods"}
    assert [p["n"] for p in ev["passages"][0]] == [1, 2]
    approval = ev["approval"][0]
    assert approval["summary"] == "Run Meeting notes (prompt) on 1 recording(s)"
    pending = c.get("/api/v1/approvals", headers=h, params={"chat_id": cid}).json()
    assert [(x["id"], x["status"]) for x in pending] == [(approval["id"], "pending")]
    cv, hv = s.cl["viewer"]
    assert cv.post(f"/api/v1/approvals/{approval['id']}", headers=hv, json={"decision": "approve"}).status_code == 404  # not theirs
    out = c.post(f"/api/v1/approvals/{approval['id']}", headers=h, json={"decision": "approve"}).json()
    drain(db, cfg)
    assert c.get(f"/api/v1/batches/{out['batch']}", headers=h).json()["status"] == "finished"
    assert c.post(f"/api/v1/approvals/{approval['id']}", headers=h, json={"decision": "approve"}).status_code == 400  # only once
    assert c.get("/api/v1/approvals", headers=h).json()[0]["status"] == "done"
    mid = ev["done"][0]["message"]
    chk = c.post(f"/api/v1/chats/{cid}/messages/{mid}/check", headers=h).json()
    assert (chk["claims"], chk["supported"]) == (2, 1)  # the invented claim is flagged
    assert c.post(f"/api/v1/chats/{cid}/messages/{mid - 1}/check", headers=h).status_code == 404  # the question, not an answer
    # reopened, the answer still has the tools it used and its source check
    reopened = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert [x["tool"] for x in reopened[-1]["steps"]] == ["search_transcripts", "run_template"] and reopened[0]["steps"] == []
    assert reopened[-1]["steps"][0]["args"] == {"query": "Dyno Therapeutics"} and reopened[-1]["check"] == chk
    box = ai_tools.Toolbox(db, cfg, {"id": 99, "email": "v"}, {store.ns_id(db, "pods")}, set(), {"recordings": [s.a]}, None)
    assert "run_template" not in [t["function"]["name"] for t in box.specs()]  # viewers get read tools only
    assert "error" in box.call("read_transcript", {"recording_id": s.b})[0]  # outside the conversation's scope
    llm.reject_tools = True
    vid = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    ev = sse(cv.post(f"/api/v1/chats/{vid}/messages", headers=hv, json={"content": "capsid"}).text)
    assert "notice" in ev  # fell back to search-and-answer
    assert ev["passages"][0]
    assert cv.get(f"/api/v1/chats/{vid}", headers=hv).json()["messages"][-1]["notice"] == ev["notice"][0]["message"]  # kept too


def test_capabilities_for_everyone(client, new_client, db, cfg, folder, llm):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    caps = client.get("/api/v1/chats/capabilities", headers=hv).json()
    assert caps == {"configured": True, "model": "fake", "tools": True, "max_steps": 6, "check": True, "models": ["fake", "fake-large"]}
    assert "base_url" not in json.dumps(caps) and new_client().get("/api/v1/chats/capabilities").status_code == 401
    admin = new_client()
    assert (
        admin.put("/api/v1/settings/llm", headers=login(admin, "root@x.io", "root password 1"), json={"base_url": None}).status_code == 200
    )
    caps = client.get("/api/v1/chats/capabilities", headers=hv).json()
    assert (caps["configured"], caps["model"], caps["tools"], caps["check"]) == (False, None, False, False)


def test_stopping_an_answer(plain, client, new_client, db, cfg, folder, llm, monkeypatch):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "ed@x.io", "editor password 1")
    other = new_client()
    hv = login(other, "vi@x.io", "viewer password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert client.post(f"/api/v1/chats/{cid}/stop", headers=h).json() == {"ok": True, "stopping": False}  # nothing to stop
    assert other.post(f"/api/v1/chats/{cid}/stop", headers=hv).status_code == 404  # someone else's
    monkeypatch.setattr(chat.Answering, "EVERY", 0)

    def stream(*_a, **_k):  # the person presses Stop after the first piece
        yield "The shipment "
        assert chat.request_stop(db, cid)
        yield "leaves on Friday [1]."
        yield " And more."

    monkeypatch.setattr(llm_mod, "stream_chat", stream)
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does the shipment leave?"}).text)
    assert "".join(e["text"] for e in ev["token"]) == "The shipment leaves on Friday [1]." and "stopped" in ev
    saved = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert (saved["id"], saved["content"], saved["stopped"]) == (ev["done"][0]["message"], "The shipment leaves on Friday [1].", True)
    assert saved["passages"] and client.post(f"/api/v1/chats/{cid}/stop", headers=h).json()["stopping"] is False

    # a later question isn't stopped by the earlier request
    monkeypatch.setattr(llm_mod, "stream_chat", lambda *a, **k: iter(["Friday [1]."]))
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Again?"}).text)
    assert "stopped" not in ev and client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]["stopped"] is False

    # why there's no answer is kept with it
    def broken(*_a, **_k):
        raise llm_mod.LLMError("429 from the LLM server")
        yield  # a generator, like the real one

    monkeypatch.setattr(llm_mod, "stream_chat", broken)
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Once more?"}).text)
    last = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert (ev["error"][0]["message"], last["content"], last["error"]) == (
        "429 from the LLM server",
        "(no answer)",
        "429 from the LLM server",
    )


def test_stopping_between_tool_steps(client, db, cfg, folder, llm, monkeypatch):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]

    def tool_loop(*_a, **_k):  # Stop is pressed while the second tool runs: the model isn't asked again
        yield "step", {"tool": "search_transcripts", "args": {"query": "shipment"}, "summary": "Searched"}
        chat.request_stop(db, cid)
        yield "step", {"tool": "get_recording", "args": {"id": 1}, "summary": "Opened"}
        raise AssertionError("asked the model again after Stop")

    monkeypatch.setattr(chat, "tool_answer", tool_loop)
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "And the samples?"}).text)
    # the step that was running finishes and is shown; then it stops
    assert [s["tool"] for s in ev["step"]] == ["search_transcripts", "get_recording"] and "token" not in ev and "stopped" in ev
    saved = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert (saved["content"], saved["stopped"], ev["done"][0]["message"]) == ("(stopped)", True, saved["id"])


def test_choosing_the_model(plain, client, new_client, db, cfg, folder, llm):
    llm_mod._MODELS.clear()
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    admin = new_client()
    ha = login(admin, "root@x.io", "root password 1")
    assert client.post("/api/v1/chats", headers=h, json={"model": "nope"}).status_code == 400
    cid = client.post("/api/v1/chats", headers=h, json={"model": "fake-large"}).json()["id"]
    assert client.get("/api/v1/chats", headers=h).json()[0]["model"] == "fake-large"

    # the conversation's model answers in it; one question can ask another (Retry with another model)
    llm.seen.clear()
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does the shipment leave?"}).text)
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does the shipment leave?", "model": "fake"}).text)
    assert [b["model"] for b in llm.seen] == ["fake-large", "fake"]
    msgs = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert ([m["model"] for m in msgs["messages"] if m["role"] == "assistant"], msgs["model"]) == (["fake-large", "fake"], "fake-large")
    bad = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Again?", "model": "gpt-9"})
    assert bad.status_code == 400 and "isn't one of the models" in bad.json()["detail"]
    assert client.patch(f"/api/v1/chats/{cid}", headers=h, json={"model": None}).status_code == 200
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["model"] is None

    # admins can narrow (or widen) the choice; a conversation whose model was taken away falls back to the configured one
    assert admin.put("/api/v1/settings/llm", headers=ha, json={"chat_models": "fake-large"}).status_code == 400
    assert admin.put("/api/v1/settings/llm", headers=ha, json={"chat_models": [" "]}).status_code == 400
    assert admin.put("/api/v1/settings/llm", headers=ha, json={"chat_models": ["fake-mini", "fake-mini"]}).status_code == 200
    assert client.get("/api/v1/chats/capabilities", headers=h).json()["models"] == ["fake", "fake-mini"]
    assert client.patch(f"/api/v1/chats/{cid}", headers=h, json={"model": "fake-large"}).status_code == 400
    db.q("UPDATE $c SET model = 'fake-large'", c=store.R("chat", cid))
    llm.seen.clear()
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "And now?"}).text)
    assert [b["model"] for b in llm.seen] == ["fake"]
