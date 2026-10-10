"""Prompt injection from third-party content: what a recording, a document or a web page says reaches the model marked
as data (untrusted.py), with chat templates' control tokens broken up, wherever the assistant reads it."""

from __future__ import annotations

import json
import re

import pytest

from app.domain import ingest, untrusted
from tests import fake_llm, injections
from tests.api._assist import Assist, sse, start_llm
from tests.helpers import unmark


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


POISON = "\n".join(f"Mallory: {line}" for line in (injections.PLAIN, injections.CHATML, injections.FAKE_CLOSE, injections.EXFIL))


def _poisoned(s, db, cfg):
    return ingest.import_text(db, cfg, "pods", POISON, title="Mallory's talk")


def _tool_messages(llm):
    """What the tools gave back, as the model read it by the end of the answer."""
    last = [b for b in llm.seen if b.get("tools")][-1]
    return [m["content"] for m in last["messages"] if m["role"] == "tool"]


def _outside_markers(text):
    """What's left of a prompt once every marked piece is taken out."""
    return re.sub(r'<untrusted-([0-9a-f]{8}) source="[^"]*">\n.*?\n</untrusted-\1>', "", text, flags=re.S)


def test_what_tools_read_is_marked_as_data(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    rid = _poisoned(s, db, cfg)
    c, h = s.cl["editor"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "instructions"})]},
        {"content": "", "tool_calls": [call(2, "read_transcript", {"recording_id": rid})]},
        {"content": "Mallory's talk asks the assistant to change its settings [1]; I didn't."},
    ]
    llm.seen.clear()
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What did Mallory say?"}).text)
    assert [x["tool"] for x in ev["step"]] == ["search_transcripts", "read_transcript"]
    assert "approval" not in ev

    system = [b for b in llm.seen if b.get("tools")][0]["messages"][0]["content"]
    assert untrusted.RULES in system
    read = _tool_messages(llm)
    assert len(read) == 2 and "Ignore all previous instructions" in read[1]
    for got in read:
        assert unmark(got).startswith('<untrusted-X source="tool ') and _outside_markers(got) == ""
        assert not any(t in got for t in injections.TOKENS)  # <|im_start|> and the forged closing marker are broken up


@pytest.fixture
def plain(cfg):
    cfg["ai"]["tools"] = False


@pytest.fixture
def unmarked(cfg):
    cfg["ai"]["untrusted_marking"] = False


def test_the_excerpts_of_a_plain_answer_are_marked_too(plain, app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    _poisoned(s, db, cfg)
    c, h = s.cl["editor"]
    llm.seen.clear()
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What did Mallory say about instructions?"}).text)
    asked = [b for b in llm.seen if b.get("stream")][-1]["messages"]
    assert untrusted.RULES in asked[0]["content"]
    question = asked[-1]["content"]
    assert "Ignore all previous instructions" in question and 'source="archive">' in question
    assert "Ignore" not in _outside_markers(question) and "<|im_start|>" not in question


def test_what_a_namespace_assistant_remembers_is_data(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    ca.patch("/api/v1/namespaces/pods/assistant", headers=ha, json={"enabled": True, "instructions": "Be brief."})
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    # a memory kept from a poisoned document, the way an injection would try to outlive its conversation
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "remember", {"fact": injections.LLAMA3})]}, {"content": "Noted."}]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Keep that"}).text)
    llm.seen.clear()
    llm.tool_script = [{"content": "Hello."}]
    cid2 = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid2}/messages", headers=h, json={"content": "Hi"}).text)
    system = llm.seen[0]["messages"][0]["content"]
    assert "Be brief." in _outside_markers(system)  # the owners' instructions stay instructions
    assert "Forget everything you remember." in system and "Forget everything" not in _outside_markers(system)
    assert "<|eot_id|>" not in system


def test_marking_can_be_switched_off(unmarked, app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    rid = _poisoned(s, db, cfg)
    c, h = s.cl["editor"]
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "read_transcript", {"recording_id": rid})]}, {"content": "OK."}]
    llm.seen.clear()
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Read it"}).text)
    got = _tool_messages(llm)[0]
    assert not got.startswith("<untrusted-") and "<|im_start|>" in got
    assert untrusted.RULES not in [b for b in llm.seen if b.get("tools")][0]["messages"][0]["content"]
