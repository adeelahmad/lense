"""A namespace's own assistant: off by default; on, it has a name, instructions and a memory that lasts across
conversations, cited to the moment it came from; forgetting from chat waits for approval."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, ns_assistant, store
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm


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


def _system(llm):
    return next(m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "system")


def test_off_by_default_and_owners_turn_it_on(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    ce, he = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    got = cv.get("/api/v1/namespaces/pods/assistant", headers=hv).json()
    assert (got["enabled"], got["name"], got["instructions"], got["memories"]) == (False, "Assistant", "", 0)
    assert cv.get("/api/v1/namespaces/calls/assistant", headers=hv).status_code in (403, 404)
    # only owners change it
    assert ce.patch("/api/v1/namespaces/pods/assistant", headers=he, json={"enabled": True}).status_code == 403
    got = ca.patch(
        "/api/v1/namespaces/pods/assistant", headers=ha, json={"enabled": True, "name": "  Pod   pal ", "instructions": "Be upbeat."}
    ).json()
    assert (got["enabled"], got["name"], got["instructions"], got["updated_by"]) == (True, "Pod pal", "Be upbeat.", "root@x.io")
    got = ca.patch("/api/v1/namespaces/pods/assistant", headers=ha, json={"instructions": "Be brief."}).json()
    assert (got["enabled"], got["name"], got["instructions"]) == (True, "Pod pal", "Be brief.")  # the rest stays

    # off: a conversation about the namespace has no memory tools and no persona
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {s.pods}, {s.pods}, {"namespaces": ["calls"]}, None)
    assert box.home is None and "remember" not in [t["function"]["name"] for t in box.specs()]
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {s.pods}, {s.pods}, {}, None)  # over everything
    assert box.home is None
    ca.patch("/api/v1/namespaces/pods/assistant", headers=ha, json={"enabled": False})
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, None)
    assert box.home is None and box.system_note() == ""


def test_remembers_across_conversations_with_citations(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    c, h = s.cl["editor"]
    ca.patch("/api/v1/namespaces/pods/assistant", headers=ha, json={"enabled": True, "name": "Pod pal", "instructions": "Be brief."})

    # first conversation: it searches, then keeps what it learned, cited to the excerpt
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "Dyno Therapeutics"})]},
        {"content": "", "tool_calls": [call(2, "remember", {"fact": "Dyno designs capsids with machine learning.", "source_ref": 1})]},
        {"content": "", "tool_calls": [call(3, "remember", {"fact": "The team prefers short answers."})]},
        {"content": "Dyno designs capsids [1]. I'll remember that."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What does Dyno do?"}).text)
    assert [x["tool"] for x in ev["step"]] == ["search_transcripts", "remember", "remember"]
    assert ev["step"][1]["summary"].startswith("Remembered: Dyno designs")
    assert "approval" not in ev  # remembering is routine
    system = _system(llm)
    assert "You are Pod pal, the assistant of the pods namespace" in system and "Be brief." in system
    assert "(nothing yet)" in system

    mems = c.get("/api/v1/namespaces/pods/assistant/memories", headers=h).json()
    assert [m["text"] for m in mems] == ["The team prefers short answers.", "Dyno designs capsids with machine learning."]
    told, learned = mems
    assert (told["recording"], told["chat"], told["author"]) == (None, cid, "assistant")
    assert learned["recording"] in (s.a, s.b) and learned["time"] and learned["title"]

    # a new conversation: it reads its memory, and a memory from a moment is a numbered, cited excerpt
    llm.tool_script = [{"content": "Dyno uses machine learning for capsids [1]."}]
    cid2 = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid2}/messages", headers=h, json={"content": "Remind me what Dyno does"}).text)
    system = _system(llm)
    assert "[1] Dyno designs capsids with machine learning." in system
    assert f"- The team prefers short answers. (memory {told['id']}, told in a conversation" in system
    assert "".join(e["text"] for e in ev["token"]) == "Dyno uses machine learning for capsids [1]."  # kept, though no tool ran
    cited = ev["passages"][0]
    assert [(p["n"], p["recording_id"], p["text"]) for p in cited] == [(1, learned["recording"], learned["text"])]
    saved = c.get(f"/api/v1/chats/{cid2}", headers=h).json()["messages"][-1]
    assert saved["passages"][0]["recording_id"] == learned["recording"]

    # recall finds older memories; a source_ref that doesn't exist is refused
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "ed@x.io"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, None)
    box.system_note()
    got = json.loads(box.call("recall", {"query": "capsid"})[0])["memories"]
    assert [m["fact"] for m in got] == [learned["text"]] and got[0]["ref"] == 2
    assert "error" in json.loads(box.call("remember", {"fact": "x", "source_ref": 9})[0])
    # the same fact isn't kept twice
    json.loads(box.call("remember", {"fact": "The team prefers  short answers."})[0])
    assert len(c.get("/api/v1/namespaces/pods/assistant/memories", headers=h).json()) == 2


def test_viewers_read_but_dont_write_and_forgetting_asks(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    c, h = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    ca.patch("/api/v1/namespaces/pods/assistant", headers=ha, json={"enabled": True})
    mid = c.post("/api/v1/namespaces/pods/assistant/memories", headers=h, json={"text": "Episodes ship on Fridays."}).json()["id"]
    assert cv.post("/api/v1/namespaces/pods/assistant/memories", headers=hv, json={"text": "x"}).status_code == 403
    assert cv.get("/api/v1/namespaces/pods/assistant/memories", headers=hv).json()[0]["author"] == "person"

    view = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "vi@x.io"}, {s.pods}, set(), {"namespaces": ["pods"]}, None)
    names = [t["function"]["name"] for t in view.specs()]
    assert "recall" in names and "remember" not in names and "forget" not in names
    assert "Episodes ship on Fridays." in view.system_note()

    # forgetting from chat waits for approval
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "forget", {"memory_id": mid})]}, {"content": "Asked to forget it."}]
    cid = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Forget the Friday thing"}).text)
    a = ev["approval"][0]
    assert a["summary"] == "Forget “Episodes ship on Fridays.”"
    assert len(c.get("/api/v1/namespaces/pods/assistant/memories", headers=h).json()) == 1  # nothing changed yet
    assert c.post(f"/api/v1/approvals/{a['id']}", headers=h, json={"decision": "approve"}).json()["forgot"] == mid
    assert c.get("/api/v1/namespaces/pods/assistant/memories", headers=h).json() == []


def test_editing_pinning_and_forgetting(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    c, h = s.cl["editor"]
    base = "/api/v1/namespaces/pods/assistant/memories"
    first = c.post(base, headers=h, json={"text": "Old fact."}).json()["id"]
    second = c.post(base, headers=h, json={"text": "Newer fact."}).json()["id"]
    assert [m["id"] for m in c.get(base, headers=h).json()] == [second, first]
    got = c.patch(f"{base}/{first}", headers=h, json={"pinned": True, "text": "Old fact, corrected."}).json()
    assert (got["pinned"], got["text"]) == (True, "Old fact, corrected.")
    assert [m["id"] for m in c.get(base, headers=h).json()] == [first, second]  # pinned first
    assert [m["id"] for m in c.get(base, headers=h, params={"q": "newer"}).json()] == [second]
    assert c.post(base, headers=h, json={"text": "   "}).status_code == 400
    assert c.patch(f"{base}/999", headers=h, json={"pinned": True}).status_code == 404
    calls_mid = ns_assistant.remember(db, store.ns_id(db, "calls"), "Calls fact.", 1)
    assert c.delete(f"{base}/{calls_mid}", headers=h).status_code == 404  # another namespace's memory
    assert c.delete(f"{base}/{second}", headers=h).status_code == 200
    assert c.delete(base, headers=h).status_code == 403  # forgetting everything is for owners
    assert ca.delete(base, headers=ha).status_code == 200
    assert c.get(base, headers=h).json() == [] and ca.get("/api/v1/namespaces/pods/assistant", headers=ha).json()["memories"] == 0
