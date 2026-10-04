"""Chat: answers cite only what the asker can read; the assistant's tools, approvals, and checking an answer."""

from __future__ import annotations

import io
import json

import pytest

from app.domain import ai_tools, auth, chat, store, topics
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
    cid = client.post("/api/v1/chats", headers=h, json={"model": "fake-large", "scope": {"namespaces": ["calls"]}}).json()["id"]
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


def test_scoped_by_a_collection(plain, client, new_client, db, cfg, folder):
    """A conversation can draw on saved collections: their recordings as they are when it answers."""
    from app.domain import recsets

    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv, he = login(client, "vi@x.io", "viewer password 1"), login(new_client(), "ed@x.io", "editor password 1")
    fixed = client.post("/api/v1/collections", headers=hv, json={"name": "Episode 2", "recordings": [b]}).json()["id"]
    mine_only = client.post("/api/v1/collections", headers=he, json={"name": "Private", "recordings": [a]}).json()["id"]
    shared = client.post("/api/v1/collections", headers=he, json={"name": "Capsid talk", "filter": {"q": "capsid"}, "shared": True}).json()[
        "id"
    ]

    # only collections you can see: yours, or shared
    assert client.post("/api/v1/chats", headers=hv, json={"scope": {"collections": [mine_only]}}).status_code == 404
    assert client.post("/api/v1/chats", headers=hv, json={"scope": {"collections": [999]}}).status_code == 404
    cid = client.post("/api/v1/chats", headers=hv, json={"scope": {"collections": [fixed]}}).json()["id"]
    assert client.get(f"/api/v1/chats/{cid}", headers=hv).json()["scope"] == {"collections": [fixed]}
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "Dyno Therapeutics capsid"}).text)
    assert {p["recording_id"] for p in ev["passages"][0]} == {b}

    # a collection is read when it answers: what's added later counts
    recsets.update(db, fixed, recordings=[a, b])
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "Dyno Therapeutics capsid"}).text)
    assert {p["recording_id"] for p in ev["passages"][0]} == {a, b}
    # several collections together, narrowed by recordings too; within what you can read
    both = {"collections": [fixed, shared], "recordings": [a]}
    assert client.patch(f"/api/v1/chats/{cid}", headers=hv, json={"scope": both}).status_code == 200
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "capsid"}).text)
    assert {p["recording_id"] for p in ev["passages"][0]} == {a}
    assert call not in recsets.within(db, {store.ns_id(db, "pods")}, collections=[shared])
    # a deleted collection adds nothing
    client.delete(f"/api/v1/collections/{fixed}", headers=hv)
    assert recsets.within(db, {store.ns_id(db, "pods")}, collections=[fixed]) == set()
    assert client.patch(f"/api/v1/chats/{cid}", headers=hv, json={"scope": {"collections": [fixed]}}).status_code == 404


def test_answers_survive_unusual_model_servers(app, db, cfg, folder, new_client, llm, monkeypatch):
    """Tool arguments sent as an object, thinking before the answer, a reply that isn't JSON, a stream cut off and a
    tool that breaks: each ends in an answer or an error that's saved, never a stream that just stops."""
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]

    def ask(q, cid=None):
        cid = cid or c.post("/api/v1/chats", headers=h, json={}).json()["id"]
        return cid, sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": q}).text)

    def last(cid):
        return c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]

    llm.tool_script = [
        {
            "content": None,
            "tool_calls": [{"id": "a", "type": "function", "function": {"name": "search_transcripts", "arguments": {"query": "capsid"}}}],
        },
        {"content": "<think>They want the capsid.</think>\n\nThe capsid model won [1]."},
    ]
    cid, ev = ask("capsid?")
    assert ev["step"][0]["args"] == {"query": "capsid"} and ev["token"][0]["text"] == "The capsid model won [1]."
    assert last(cid)["content"] == "The capsid model won [1]."

    # a tool that breaks tells the model, and the answer carries on
    monkeypatch.setattr(ai_tools.Toolbox, "t_find_entities", lambda self, **k: 1 / 0)
    llm.tool_script = [
        {"content": "", "tool_calls": [{"id": "b", "type": "function", "function": {"name": "find_entities", "arguments": "{}"}}]},
        {"content": "No entities to show."},
    ]
    cid, ev = ask("who?")
    assert ev["step"][0]["summary"] == "find_entities failed" and ev["token"][0]["text"] == "No entities to show."

    # a proxy's error page instead of the model's reply
    with monkeypatch.context() as m:
        m.setattr(llm_mod, "_post", lambda cfg, payload: io.BytesIO(b"<html>Bad gateway</html>"))
        cid, ev = ask("capsid?")
    assert ev["error"][0]["message"] == "the LLM server's reply wasn't JSON" and last(cid)["content"] == "(no answer)"

    # anything else that breaks the answer is an error, saved with the conversation
    def broken(*a, **k):
        raise RuntimeError("x")
        yield

    monkeypatch.setattr(chat, "tool_answer", broken)
    cid, ev = ask("again?")
    assert ev["error"] and ev["done"] and last(cid)["error"] == ev["error"][0]["message"]


def test_streamed_answers_without_thinking_or_cut_off(monkeypatch):
    def server(*lines):
        class R(io.BytesIO):
            headers = {"Content-Type": "text/event-stream"}

        body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n\n" for p in lines)
        return lambda cfg, payload: R((body + "data: [DONE]\n\n" if lines[-1] != "CUT" else body).encode())

    cfg = {"llm": {"base_url": "http://x", "model": "m"}}
    monkeypatch.setattr(llm_mod, "_post", server("<th", "ink>Let me ", "see.</th", "ink>\n", "Friday ", "[1]."))
    assert "".join(llm_mod.stream_chat(cfg, [])) == "Friday [1]."
    monkeypatch.setattr(llm_mod, "_post", server("<b>Friday</b>", " [1]."))
    assert "".join(llm_mod.stream_chat(cfg, [])) == "<b>Friday</b> [1]."
    monkeypatch.setattr(llm_mod, "_post", server("Fri", "CUT"))
    got = []
    with pytest.raises(llm_mod.LLMError, match="stopped mid-answer"):
        for piece in llm_mod.stream_chat(cfg, []):
            got.append(piece)
    assert got[0] == "Fri"
    assert llm_mod.unthink('<think>\nhmm {not json}\n</think>\n{"a": 1}') == '{"a": 1}'


def test_tools_stay_in_scope_and_say_what_is_missing(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    pods = {s.pods}
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, pods, pods, {"recordings": [s.b]}, None)
    out = json.loads(box.call("search_transcripts", {"query": "Dyno Therapeutics", "limit": 1})[0])
    assert [r["recording_id"] for r in out["results"]] == [s.b]  # not crowded out by matches elsewhere
    assert box.call("entity_mentions", {"entity_id": 999})[1] == "entity_mentions: not found"
    assert box.call("graph_neighbours", {})[1] == "graph_neighbours: give an entity_id or a speaker_id"
    assert box.call("propose_entity_change", {"action": "retype", "entity_id": 1})[1] == "propose_entity_change: retype needs new_type"
    assert box.approvals == []


def test_editing_a_question(plain, client, new_client, db, cfg, folder, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "ed@x.io", "editor password 1")
    other = new_client()
    hv = login(other, "vi@x.io", "viewer password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    for q in ("When does the shipment leave?", "Who is shipping it?", "And where to?"):
        sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": q}).text)
    msgs = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert len(msgs) == 6
    second = msgs[2]["id"]

    # an answer, someone else's or another conversation's message isn't a question you can edit here
    assert client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "edit": msgs[1]["id"]}).status_code == 404
    assert client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "edit": 999999}).status_code == 404
    assert other.post(f"/api/v1/chats/{cid}/messages", headers=hv, json={"content": "x", "edit": second}).status_code == 404
    elsewhere = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert client.post(f"/api/v1/chats/{elsewhere}/messages", headers=h, json={"content": "x", "edit": second}).status_code == 404
    assert len(client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]) == 6  # nothing was removed

    # editing the second question replaces it and everything after it; the model sees only what came before
    llm.seen.clear()
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Who ships it, exactly?", "edit": second}).text)
    assert ev["done"]
    sent = [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "user"]
    assert "When does the shipment leave?" in sent[0] and not any("Who is shipping it?" in s or "And where to?" in s for s in sent)
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert [m["content"] for m in c["messages"] if m["role"] == "user"] == ["When does the shipment leave?", "Who ships it, exactly?"]
    assert [m["role"] for m in c["messages"]] == ["user", "assistant", "user", "assistant"]
    assert c["title"] == "When does the shipment leave?"

    # editing the first question retitles a conversation titled after it, but keeps a title you set
    first = c["messages"][0]["id"]
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When is the Friday shipment?", "edit": first}).text)
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert ([m["content"] for m in c["messages"] if m["role"] == "user"], c["title"]) == (
        ["When is the Friday shipment?"],
        "When is the Friday shipment?",
    )
    assert client.patch(f"/api/v1/chats/{cid}", headers=h, json={"title": "Shipping"}).status_code == 200
    first = c["messages"][0]["id"]
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does it ship?", "edit": first}).text)
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == "Shipping"

    # a refused edit (a model that isn't offered) removes nothing
    n = len(client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"])
    assert (
        client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "edit": first, "model": "gpt-9"}).status_code == 400
    )
    assert len(client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]) == n

    # a question asked from a page keeps the page and the highlighted text when edited
    page = {"url": "/library", "title": "Library", "text": "Shipment report", "selection": "Dyno shipment"}
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When?", "context": page}).text)
    asked = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-2]
    llm.seen.clear()
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When exactly?", "edit": asked["id"]}).text)
    assert '"""\nDyno shipment\n"""' in llm.seen[-1]["messages"][-1]["content"]
    edited = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-2]
    assert (edited["content"], edited["context"]) == ("When exactly?", asked["context"])


def test_asking_from_a_page(plain, client, db, cfg, folder, llm):
    """Chat on any page: the model reads the page and the highlighted part with the question; the question keeps where
    it was asked and what was highlighted, not the page's text."""
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    page = {"url": "/library?ns=calls", "title": "Library", "text": "Shipment report " + "x" * 20000, "selection": "  Dyno shipment  "}
    llm.seen.clear()
    ev = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does it leave?", "context": page}).text)
    assert "done" in ev
    asked = llm.seen[-1]["messages"][-1]["content"]
    assert "Lens page Library (/library?ns=calls)" in asked and '"""\nDyno shipment\n"""' in asked
    assert "Shipment report" in asked and "(cut short)" in asked and len(asked) < 14000 and asked.endswith("Question: When does it leave?")
    msgs = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert msgs[0]["content"] == "When does it leave?"
    assert msgs[0]["context"] == {"url": "/library?ns=calls", "title": "Library", "selection": "Dyno shipment", "page": True}
    assert client.get("/api/v1/chats", headers=h).json()[0]["title"] == "When does it leave?"

    # only the highlighted part, no page text; then a question with nothing shared reads as before
    sse(
        client.post(
            f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Who?", "context": {"url": "/x", "selection": "Alice"}}
        ).text
    )
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "And then?"}).text)
    assert "page's text" not in llm.seen[-2]["messages"][-1]["content"] and llm.seen[-1]["messages"][-1]["content"].endswith(
        "Question: And then?"
    )
    msgs = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert (msgs[2]["context"], msgs[4]["context"]) == ({"url": "/x", "title": None, "selection": "Alice", "page": False}, None)
    for bad in (
        {"title": "no url"},
        {"url": "https://elsewhere.example"},
        {"url": "//elsewhere.example/x"},
        {"url": "javascript:alert(1)"},
    ):
        assert client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "?", "context": bad}).status_code == 422

    # a follow-up still knows what the highlighted text was
    llm.seen.clear()
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Say more"}).text)
    turns = [m["content"] for m in llm.seen[-1]["messages"]]
    assert '(About the highlighted text: "Alice") Who?' in turns


def test_page_context_reaches_the_tools_too(app, db, cfg, folder, new_client, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    c = new_client()
    h = login(c, "ed@x.io", "editor password 1")
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    llm.tool_script[:] = [{"content": "It's the capsid one."}]
    llm.seen.clear()
    ev = sse(
        c.post(
            f"/api/v1/chats/{cid}/messages",
            headers=h,
            json={"content": "Which?", "context": {"url": "/resources/1", "title": "Ep 1", "text": "Capsid talk"}},
        ).text
    )
    assert ev["token"][0]["text"] == "It's the capsid one."
    asked = llm.seen[0]["messages"][-1]["content"]
    assert "Lens page Ep 1 (/resources/1)" in asked and "Capsid talk" in asked and asked.endswith("Question: Which?")


def test_a_model_that_skips_the_tools_still_answers_from_the_archive(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    llm.tool_script = [{"content": "The archive doesn't seem to cover it."}]  # answers straight away, without looking
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What does Dyno Therapeutics do?"}).text)
    assert ev["passages"][0]  # the excerpts found up front
    assert "".join(e["text"] for e in ev["token"]) == "The shipment leaves on Friday [1]."  # answered from them
    assert "notice" not in ev
    llm.tool_script = [{"content": "Hello!"}]  # nothing in the archive matches: the model's own answer stands
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "zzqx"}).text)
    assert "".join(e["text"] for e in ev["token"]) == "Hello!"


def test_the_assistant_queries_the_graph(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    pods = {s.pods}
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, pods, set(), {}, None)
    names = {sp["function"]["name"] for sp in box.specs()}
    assert {"graph_schema", "graph_query", "graph_related", "graph_paths"} <= names  # read tools, for viewers too
    schema = json.loads(box.call("graph_schema", {})[0])
    assert "[:SAID" in schema["graph"] and schema["namespaces"] == ["pods"]
    out = json.loads(box.call("graph_query", {"query": "MATCH (s:Speaker) RETURN s.name ORDER BY s.name"})[0])
    assert out["rows"] == [["Alice"], ["Bob"], ["Carol"]]
    assert "read-only" in box.call("graph_query", {"query": "MATCH (n) DELETE n"})[1]
    dyno = json.loads(box.call("graph_query", {"query": "MATCH (e:Entity {name: 'Dyno Therapeutics'}) RETURN id(e)"})[0])["rows"][0][0]
    up = json.loads(box.call("graph_related", {"node": dyno, "relation": "parents"})[0])
    assert {n["labels"][0] for n in up["nodes"]} >= {"Recording", "Speaker"}
    paths = json.loads(box.call("graph_paths", {"from_node": "n" + str(s.pods), "to_node": dyno})[0])
    assert paths["found"] and paths["paths"][0][0] == "pods"
    assert "no namespace called calls" in box.call("graph_schema", {"namespace": "calls"})[1]
    # a conversation about one recording sees only what was said in it
    one = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, pods, set(), {"recordings": [s.b]}, None)
    recs = json.loads(one.call("graph_query", {"query": "MATCH (r:Recording) RETURN id(r)"})[0])["rows"]
    assert recs == [[f"r{s.b}"]]


def test_the_assistant_finds_and_suggests_topics(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    pods = {s.pods}
    bio = topics.create(db, s.pods, "Biology")
    gene = topics.create(db, s.pods, "Gene therapy", alt=["GT"], broader=[bio])
    topics.tag(db, gene, [s.a])
    viewer = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, pods, set(), {}, None)
    names = {sp["function"]["name"] for sp in viewer.specs()}
    assert {"find_topics", "topic_recordings"} <= names and "suggest_topic" not in names  # suggesting needs an editor
    found = json.loads(viewer.call("find_topics", {"query": "gt"})[0])
    assert found["topics"] == [
        {"id": gene, "label": "Gene therapy", "namespace": "pods", "also": ["GT"], "broader": ["Biology"], "recordings": 1}
    ]
    t = json.loads(viewer.call("topic_recordings", {"topic_id": gene})[0])
    assert [r["recording_id"] for r in t["recordings"]] == [s.a] and t["broader"] == [{"id": bio, "label": "Biology"}]
    editor = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, pods, pods, {}, None)
    out = json.loads(editor.call("suggest_topic", {"topic_id": gene, "recording_ids": [s.a, s.b]})[0])
    assert out["suggested_for"] == [s.b]  # what holds stays; the rest waits for someone to accept
    assert [(x["label"], x["source"], x["status"]) for x in topics.of_recording(db, s.b)] == [("Gene therapy", "assistant", "suggested")]
