"""Choosing what an answer may use (docs/assistant.md, "Choosing tools, namespaces and a limit"): the tool groups and
tools a person has (GET /chats/tools), only the ones picked offered (an admin's switch still wins), at most so many tool
calls, a scope for one answer alone, and the choice kept with the conversation and for the next ones."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, browser, ops_tools
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm

TOOL = """---
name: translate
kind: tool
description: Translate text into another language.
effect: read
params:
  - {name: text, kind: text, required: true}
---

Translate into French: {{text}}
"""


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


def offered(llm):
    """The tools the model was offered, the last time it was offered any."""
    return {t["function"]["name"] for t in [b for b in llm.seen if b.get("tools")][-1]["tools"]}


def ask(c, h, cid, **body):
    return sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What did Dyno say?", **body}).text)


def test_every_built_in_tool_is_in_one_group():
    every = [t for g in ai_tools.GROUPS.values() for t in g["tools"]]
    assert sorted(every) == sorted(ai_tools.builtin_names()) and len(every) == len(set(every))
    assert not set(ai_tools.GROUPS) & ai_tools.builtin_names()  # a name is a group or a tool, never both
    assert ai_tools.BROWSER_OUT <= {t[0] for t in browser.TOOLS}


def test_the_catalog_says_what_each_person_has(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    cv, hv = s.cl["viewer"]
    got = cv.get("/api/v1/chats/tools", headers=hv).json()
    assert got["max_tool_calls"] == 50 and got["remembered"] == {"tools": None, "max_tool_calls": None}
    groups = {g["name"]: g for g in got["groups"]}
    assert list(groups)[: len(ai_tools.GROUPS)] == list(ai_tools.GROUPS)
    tools = {t["name"]: t for g in got["groups"] for t in g["tools"]}
    # a viewer reads the archive and the notes, but writing a note and proposing changes need an editor
    assert groups["archive"]["available"] and not groups["archive"]["asks"] and not groups["archive"]["out"]
    assert tools["find_notes"]["available"] and not tools["write_note"]["available"] and tools["write_note"]["effect"] == "asks"
    assert groups["changes"]["asks"] and tools["entity_setup"]["available"] and not tools["propose_entity_change"]["available"]
    assert (groups["server"]["why"], groups["server"]["setting"]) == ("for admins", None)
    # the browser's tools are there while the side panel is allowed; the ones that act on a site send data out
    assert groups["browser"]["available"] and groups["browser"]["out"] and tools["browser_type"]["effect"] == "out"
    assert tools["browser_read"]["effect"] == "read"
    # the web is there once an admin sets a search up; its tools send what they're given out
    assert (groups["web"]["available"], groups["web"]["why"], groups["web"]["setting"]) == (False, "no web search is set up", "web.search")
    assert tools["web_search"]["effect"] == "out" and groups["web"]["out"]
    # and the code sandbox once an admin turns it on; copying a file out of it waits for a yes
    assert (groups["code"]["available"], groups["code"]["setting"]) == (False, "sandbox.enabled")
    assert tools["import_sandbox_file"]["effect"] == "asks"

    ca, ha = s.cl["admin"]
    groups = {g["name"]: g for g in ca.get("/api/v1/chats/tools", headers=ha).json()["groups"]}
    assert groups["server"]["available"] and groups["changes"]["available"]
    # what an admin switched off isn't offered, and says where it's switched back on
    off = {"disabled_tools": [t[0] for t in ops_tools.ADMIN_TOOLS], "browser": False}
    assert ca.put("/api/v1/settings/ai", headers=ha, json=off).status_code == 200
    groups = {g["name"]: g for g in ca.get("/api/v1/chats/tools", headers=ha).json()["groups"]}
    assert (groups["server"]["available"], groups["server"]["why"], groups["server"]["setting"]) == (
        False,
        "switched off by an admin",
        "ai.disabled_tools",
    )
    assert (groups["browser"]["available"], groups["browser"]["setting"]) == (False, "ai.browser")
    assert cv.get("/api/v1/chats/tools").status_code == 401


def test_an_answer_is_offered_only_what_was_picked(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "find_notes", {"query": "Dyno"})]}, {"content": "Nothing noted."}]
    ev = ask(c, h, cid, tools=["notes", "find_entities"])
    assert offered(llm) == {"find_notes", "read_note", "write_note", "update_note", "write_spec", "find_entities"}
    meta = ev["meta"][0]
    assert meta["tools"]["picked"] == ["notes", "find_entities"] and set(meta["tools"]["offered"]) == offered(llm)
    question = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-2]
    assert question["meta"]["options"]["tools"] == ["notes", "find_entities"]
    # a tool it wasn't given isn't run, even when the model calls it
    llm.tool_script = [{"content": "", "tool_calls": [call(2, "search_transcripts", {"query": "Dyno"})]}, {"content": "Done."}]
    ev = ask(c, h, cid, tools=["notes"])
    assert ev["step"][0]["summary"] == "unknown tool search_transcripts" and ev["step"][0]["failed"] is True

    # an admin's switch still wins
    ca, ha = s.cl["admin"]
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"disabled_tools": ["write_note"]}).status_code == 200
    llm.tool_script = [{"content": "Nothing noted."}]
    ask(c, h, cid, tools=["notes"])
    assert offered(llm) == {"find_notes", "read_note", "update_note", "write_spec"}
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"disabled_tools": []}).status_code == 200

    # no tools: it answers from a search, and the model is offered none
    llm.seen.clear()
    ev = ask(c, h, cid, tools=[])
    assert not [b for b in llm.seen if b.get("tools")] and ev["meta"][0]["tools"] == {"picked": [], "offered": []}
    assert ev["passages"] and ev["meta"][0]["outcome"] == "answered"

    # what isn't a group or a tool is told, not ignored
    r = c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "tools": ["archive", "nope"]})
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_an_extensions_tools_are_a_group(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    assert c.post("/api/v1/extensions", headers=h, json={"text": TOOL}).status_code == 200
    groups = {g["name"]: g for g in c.get("/api/v1/chats/tools", headers=h).json()["groups"]}
    assert groups["ext:translate"]["tools"] == [{"name": "translate", "effect": "read", "out": False, "available": True}]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    llm.tool_script = [{"content": "Bonjour."}]
    ask(c, h, cid, tools=["ext:translate"])
    assert offered(llm) == {"translate"}
    # another person doesn't have it
    cv, hv = s.cl["viewer"]
    vid = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    assert cv.post(f"/api/v1/chats/{vid}/messages", headers=hv, json={"content": "x", "tools": ["ext:translate"]}).status_code == 400


def test_at_most_so_many_tool_calls(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    llm.seen.clear()
    # two calls, then two more of which one is made: three in all, and the answer is written from what they found
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "a"}), call(2, "find_entities", {"query": "b"})]},
        {"content": "", "tool_calls": [call(3, "find_topics", {"query": "c"}), call(4, "list_recordings", {})]},
        {"content": "", "tool_calls": [call(5, "find_notes", {"query": "d"})]},
        {"content": "Dyno designs capsids."},
    ]
    ev = ask(c, h, cid, max_tool_calls=3)
    assert [x["tool"] for x in ev["step"]] == ["search_transcripts", "find_entities", "find_topics"]
    meta = ev["meta"][0]
    assert (meta["outcome"], meta["limits"], meta["used"]) == ("limit", {"rounds": 3, "tool_calls": 3}, {"rounds": 2, "tool_calls": 3})
    asked = [b for b in llm.seen if b.get("tools")]
    assert "up to 3 tool calls for this question in all" in asked[0]["messages"][0]["content"]
    # the call past the limit is answered too, as the model's API needs, and not made
    replies = [m for m in asked[-1]["messages"] if m["role"] == "tool"]
    assert json.loads(replies[-1]["content"])["note"].startswith("This answer's tool calls are used up")
    assert asked[-1].get("tool_choice") == "none"  # then it answers, with the tools off

    # more rounds than ai.max_steps, when the limit allows: one call each
    llm.tool_script = [{"content": "", "tool_calls": [call(i, "find_topics", {"query": str(i)})]} for i in range(8)] + [
        {"content": "Found it."}
    ]
    meta = ask(c, h, cid, max_tool_calls=8)["meta"][0]
    assert (meta["outcome"], meta["used"]) == ("limit", {"rounds": 8, "tool_calls": 8})
    # an answer within its limit answered as usual
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "find_topics", {"query": "x"})]}, {"content": "Done."}]
    meta = ask(c, h, cid, max_tool_calls=8)["meta"][0]
    assert (meta["outcome"], meta["used"]["tool_calls"]) == ("answered", 1)

    # within 1 and ai.max_tool_calls
    for n, code in ((0, 422), (51, 400)):
        assert c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "max_tool_calls": n}).status_code == code
    ca, ha = s.cl["admin"]
    for bad in (0, 501, "5", True):
        assert ca.put("/api/v1/settings/ai", headers=ha, json={"max_tool_calls": bad}).status_code == 400, bad
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"max_tool_calls": 2}).status_code == 200
    assert c.get("/api/v1/chats/capabilities", headers=h).json()["max_tool_calls"] == 2
    assert c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "x", "max_tool_calls": 3}).status_code == 400


def test_a_conversation_keeps_its_choice_and_new_ones_start_with_it(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert c.get(f"/api/v1/chats/{cid}", headers=h).json()["tools"] is None  # Auto
    assert c.patch(f"/api/v1/chats/{cid}", headers=h, json={"tools": ["archive"], "max_tool_calls": 5}).status_code == 200
    assert c.patch(f"/api/v1/chats/{cid}", headers=h, json={"tools": ["nope"]}).status_code == 400
    got = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (got["tools"], got["max_tool_calls"]) == (["archive"], 5)
    assert [x for x in c.get("/api/v1/chats", headers=h).json() if x["id"] == cid][0]["tools"] == ["archive"]
    # its answers use it, unless a question says otherwise
    llm.tool_script = [{"content": "Done."}]
    meta = ask(c, h, cid)["meta"][0]
    assert (meta["tools"]["picked"], meta["limits"]["tool_calls"]) == (["archive"], 5)
    assert set(meta["tools"]["offered"]) == set(ai_tools.GROUPS["archive"]["tools"])
    llm.tool_script = [{"content": "Done."}]
    meta = ask(c, h, cid, tools=["graph"], max_tool_calls=2)["meta"][0]
    assert (meta["tools"]["picked"], meta["limits"]["tool_calls"]) == (["graph"], 2)
    # the latest choice is what new conversations start with, and what the catalog says
    assert c.get("/api/v1/chats/tools", headers=h).json()["remembered"] == {"tools": ["archive"], "max_tool_calls": 5}
    new = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    got = c.get(f"/api/v1/chats/{new}", headers=h).json()
    assert (got["tools"], got["max_tool_calls"]) == (["archive"], 5)
    # unless it says otherwise; back to Auto
    new = c.post("/api/v1/chats", headers=h, json={"tools": None, "max_tool_calls": None}).json()["id"]
    got = c.get(f"/api/v1/chats/{new}", headers=h).json()
    assert (got["tools"], got["max_tool_calls"]) == (None, None)
    # a choice made as one starts is the latest choice too
    c.post("/api/v1/chats", headers=h, json={"tools": ["notes"], "max_tool_calls": 4})
    assert c.get("/api/v1/chats/tools", headers=h).json()["remembered"] == {"tools": ["notes"], "max_tool_calls": 4}
    c.patch(f"/api/v1/chats/{cid}", headers=h, json={"tools": None, "max_tool_calls": None})
    assert c.get("/api/v1/chats/tools", headers=h).json()["remembered"] == {"tools": None, "max_tool_calls": None}
    # setting the server up keeps every tool
    ca, ha = s.cl["admin"]
    ca.patch(f"/api/v1/chats/{ca.post('/api/v1/chats', headers=ha, json={}).json()['id']}", headers=ha, json={"tools": ["archive"]})
    setup = ca.post("/api/v1/chats", headers=ha, json={"kind": "setup"}).json()["id"]
    assert ca.get(f"/api/v1/chats/{setup}", headers=ha).json()["tools"] is None
    # someone else's
    cv, hv = s.cl["viewer"]
    assert cv.get("/api/v1/chats/tools", headers=hv).json()["remembered"] == {"tools": None, "max_tool_calls": None}
    assert cv.get("/api/v1/chats/tools", headers=hv, params={"chat_id": cid}).status_code == 404


def test_a_scope_for_one_answer(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    cid = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "shipment"})]}, {"content": "Friday [1]."}]
    ev = ask(c, h, cid, scope={"namespaces": ["calls"]})
    assert ev["meta"][0]["scope"] == {"namespaces": ["calls"]}
    assert ev["passages"][0] and {p["recording_id"] for p in ev["passages"][0]} == {s.call}
    got = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert got["scope"] == {"namespaces": ["pods"]}  # the conversation keeps its own
    assert got["messages"][-2]["meta"]["options"]["scope"] == {"namespaces": ["calls"]}
    # everything one can read, for this answer
    llm.tool_script = [{"content": "Done."}]
    assert ask(c, h, cid, scope={})["meta"][0]["scope"] == {}
    # and only what one can read
    cv, hv = s.cl["viewer"]
    vid = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    r = cv.post(f"/api/v1/chats/{vid}/messages", headers=hv, json={"content": "x", "scope": {"namespaces": ["calls"]}})
    assert r.status_code == 404
