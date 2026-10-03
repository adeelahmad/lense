"""Extensions: tools, skills, hooks and plugins added to the assistant, written as code, shared, versioned, and used in
conversations (prompt and web tools, skills read when they apply, hooks that add context or block a tool)."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, extensions
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm

SKILL = """---
name: meeting_recap
kind: skill
description: Recaps a meeting
when: someone asks for a recap of a meeting
tools: [search_transcripts]
---

Find the meeting, read it, and answer with three bullets: decisions, owners, dates.
"""

TOOL = """---
name: translate
kind: tool
description: Translate text into another language.
effect: read
params:
  - {name: text, kind: text, required: true}
  - {name: language, kind: text, options: [French, German], default: French}
---

Translate into {{language}}: {{text}}
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


def test_written_as_code_shared_and_versioned(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    ca, ha = s.cl["admin"]
    # checked without saving: problems are named
    assert c.post("/api/v1/extensions/check", headers=h, json={"text": SKILL}).json()["manifest"]["spec"]["when"].startswith("someone")
    bad = c.post("/api/v1/extensions/check", headers=h, json={"text": TOOL.replace("{{text}}", "{{words}}")})
    assert bad.status_code == 400 and "{{words}}" in bad.json()["detail"]
    clash = c.post("/api/v1/extensions/check", headers=h, json={"text": SKILL})
    assert clash.status_code == 200
    builtin = c.post("/api/v1/extensions", headers=h, json={"text": SKILL.replace("meeting_recap", "search_transcripts")})
    assert builtin.status_code == 400 and "assistant's own tools" in builtin.json()["detail"]

    sid = c.post("/api/v1/extensions", headers=h, json={"text": SKILL}).json()["id"]
    assert c.post("/api/v1/extensions", headers=h, json={"text": SKILL}).status_code == 400  # the name is taken
    got = c.get(f"/api/v1/extensions/{sid}", headers=h).json()
    assert (got["kind"], got["enabled"], got["visibility"], got["version"], got["origin"]) == ("skill", True, "private", 1, "code")
    assert got["spec"]["instructions"].startswith("Find the meeting")
    # the manifest goes back out as code and comes back in the same
    again = extensions.check_manifest(extensions.parse_manifest(got["manifest"]), extensions.who(1, "e", True, {}))
    assert again["spec"] == got["spec"]

    # private: the viewer doesn't see it, the admin does; only its owner (or an admin) changes it
    assert [x["id"] for x in cv.get("/api/v1/extensions", headers=hv).json()] == []
    assert cv.get(f"/api/v1/extensions/{sid}", headers=hv).status_code == 404
    assert [x["id"] for x in ca.get("/api/v1/extensions", headers=ha).json()] == [sid]
    assert c.patch(f"/api/v1/extensions/{sid}", headers=h, json={"visibility": "namespace", "namespaces": ["pods"]}).status_code == 200
    assert [x["id"] for x in cv.get("/api/v1/extensions", headers=hv).json()] == [sid]
    assert cv.patch(f"/api/v1/extensions/{sid}", headers=hv, json={"enabled": False}).status_code in (403, 404)

    v2 = c.post(
        f"/api/v1/extensions/{sid}/versions", headers=h, json={"text": SKILL.replace("three bullets", "five bullets"), "notes": "longer"}
    )
    assert v2.json()["version"] == 2
    got = c.get(f"/api/v1/extensions/{sid}", headers=h).json()
    assert "five bullets" in got["spec"]["instructions"] and [x["version"] for x in got["history"]] == [2, 1]
    assert "three bullets" in c.get(f"/api/v1/extensions/{sid}", headers=h, params={"version": 1}).json()["spec"]["instructions"]
    assert c.delete(f"/api/v1/extensions/{sid}", headers=h).status_code == 200
    assert c.get("/api/v1/extensions", headers=h).json() == []
    # the name is free again
    assert c.post("/api/v1/extensions", headers=h, json={"text": SKILL}).status_code == 200


def test_tools_and_skills_in_a_conversation(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    c.post("/api/v1/extensions", headers=h, json={"text": SKILL})
    tid = c.post("/api/v1/extensions", headers=h, json={"text": TOOL}).json()["id"]
    # tried on its own, switched on or not
    assert c.post(f"/api/v1/extensions/{tid}/test", headers=h, json={"args": {"text": "hello"}}).json()["output"] == {"text": "OK"}
    assert c.post(f"/api/v1/extensions/{tid}/test", headers=h, json={"args": {"language": "Klingon", "text": "x"}}).status_code == 400

    llm.seen.clear()
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "use_skill", {"name": "meeting_recap"})]},
        {"content": "", "tool_calls": [call(2, "translate", {"text": "good morning", "language": "German"})]},
        {"content": "Guten Morgen."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Recap the meeting and say good morning in German"}).text)
    assert [x["tool"] for x in ev["step"]] == ["use_skill", "translate"]
    assert ev["step"][1]["summary"] == 'Ran translate(text="good morning", language="German")'
    first = [b for b in llm.seen if b.get("tools")][0]
    names = [t["function"]["name"] for t in first["tools"]]
    assert "translate" in names and "use_skill" in names
    assert "meeting_recap: someone asks for a recap" in first["messages"][0]["content"]
    # the skill's instructions reached the model when it asked for them, and the tool ran the prompt
    tool_msgs = [m for b in llm.seen if b.get("tools") for m in b["messages"] if m["role"] == "tool"]
    assert "three bullets" in tool_msgs[0]["content"]
    prompt = [b for b in llm.seen if not b.get("tools") and not b.get("stream")][-1]
    assert prompt["messages"][-1]["content"] == "Translate into German: good morning"

    # switched off, it's gone from the conversation
    c.patch(f"/api/v1/extensions/{tid}", headers=h, json={"enabled": False})
    llm.seen.clear()
    llm.tool_script = [{"content": "Hi."}]
    c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "hello"})
    assert "translate" not in [t["function"]["name"] for t in llm.seen[0]["tools"]]

    # the setting turns them all off
    me = {"id": db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"], "email": "ed@x.io"}
    names = lambda: [t["function"]["name"] for t in ai_tools.Toolbox(db, cfg, me, {s.pods}, {s.pods}, {}, cid).specs()]  # noqa: E731
    assert "use_skill" in names()
    cfg["ai"]["extensions"] = False
    assert "use_skill" not in names()


def test_change_tools_ask_first_and_hooks(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    sent = []
    monkeypatch.setattr(extensions, "http_call", lambda cfg, run, args: sent.append((run, args)) or {"status": 200})
    plugin = {
        "name": "tickets",
        "kind": "plugin",
        "description": "File tickets and keep the team posted",
        "items": [
            {
                "kind": "tool",
                "name": "file_ticket",
                "description": "File a ticket in the tracker.",
                "effect": "change",
                "params": [{"name": "title", "kind": "text", "required": True}],
                "run": {"type": "http", "method": "POST", "url": "https://tracker.example/api/tickets", "body": {"title": "{{title}}"}},
            },
            {
                "kind": "hook",
                "name": "no_entity_merges",
                "event": "before_tool",
                "match": {"tool": "propose_entity_change", "contains": "merge"},
                "action": {"type": "block", "reason": "merges are done by hand here"},
            },
            {
                "kind": "hook",
                "name": "tone",
                "event": "message",
                "action": {"type": "context", "text": "Answer like a pirate when asked about {{said}}."},
            },
        ],
    }
    r = c.post("/api/v1/extensions", headers=h, json={"manifest": plugin})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # trying a tool that changes something needs a yes
    assert c.post(f"/api/v1/extensions/{pid}/test", headers=h, json={"tool": "file_ticket", "args": {"title": "x"}}).status_code == 400

    llm.seen.clear()
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "file_ticket", {"title": "Capsid samples are late"})]},
        {"content": "", "tool_calls": [call(2, "propose_entity_change", {"action": "merge", "entity_id": 1, "merge_ids": [2]})]},
        {"content": "I asked to file it."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "file a ticket for the late samples"}).text)
    assert "Answer like a pirate when asked about file a ticket" in llm.seen[0]["messages"][0]["content"]
    assert ev["step"][1]["summary"] == "propose_entity_change: blocked (merges are done by hand here)"
    assert sent == []  # nothing sent yet: it waits for a yes
    appr = ev["approval"][0]
    assert appr["summary"] == 'Run file_ticket(title="Capsid samples are late")'
    out = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["output"] == {"status": 200}
    assert sent[0][1] == {"title": "Capsid samples are late"}


def test_web_tools_reach_public_addresses_only(cfg):
    run = {"type": "http", "method": "GET", "url": "http://127.0.0.1/{{q}}"}
    with pytest.raises(ValueError, match="public address"):
        extensions.http_call(cfg, run, {"q": "x"})
    assert extensions.fill(
        "https://x.io/s?q={{q}}", {"q": "a b/c"}, quote=lambda s: __import__("urllib.parse").parse.quote(s, safe="")
    ) == ("https://x.io/s?q=a%20b%2Fc")
    assert extensions.fill_json({"n": "{{n}}", "t": "n is {{n}}"}, {"n": 3}) == {"n": 3, "t": "n is 3"}
