"""Each answer records what produced it, and each question how it was sent (docs/assistant.md, "What each answer
records"): the model and its host, the tools offered and used, the scope, limits and use, tokens and cost from the
ledger rows that name the answer, the time, how it ended and which tool results read like instructions to an AI."""

from __future__ import annotations

import json

import pytest

from app.domain import __version__, chat
from app.domain import llm as llm_mod
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm

USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    cfg["telemetry"]["prices"] = {"fake": {"input": 1.0, "output": 2.0}}  # USD per million tokens
    fake_llm.Handler.usage = USAGE
    yield fake_llm.Handler
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def plain(cfg):
    """The plain search-and-answer path (no tools)."""
    cfg["ai"]["tools"] = False


def tool(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def search(i):
    return {"content": "", "tool_calls": [tool(i, "search_transcripts", {"query": f"capsid {i}"})]}


def kept(d):
    """A record without the fields it doesn't have (the API lists them as null)."""
    return {k: kept(v) if isinstance(v, dict) else v for k, v in d.items() if v is not None} if isinstance(d, dict) else d


def ask(c, h, q, **body):
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    raw = c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": q, **body}).text
    return cid, sse(raw), raw


def test_an_answer_records_what_produced_it(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    # one round of two calls, the second outside what the editor can read, then the answer
    llm.tool_script = [
        {
            "content": "",
            "tool_calls": [
                tool(1, "search_transcripts", {"query": "Dyno Therapeutics"}),
                tool(2, "read_transcript", {"recording_id": s.call}),
            ],
        },
        {"content": "Dyno Therapeutics designs capsids with machine learning [1]."},
    ]
    cid, ev, raw = ask(c, h, "What does Dyno do?")
    meta = ev["meta"][0]
    assert raw.index("event: meta") < raw.index("event: done")  # the record comes just before the end
    assert (meta["model"], meta["provider"], meta["outcome"], meta["version"]) == ("fake", "127.0.0.1", "answered", __version__)
    assert meta["tools"]["picked"] is None and {"search_transcripts", "read_transcript"} <= set(meta["tools"]["offered"])
    assert meta["limits"] == {"rounds": 6, "tool_calls": None} and meta["used"] == {"rounds": 1, "tool_calls": 2}
    assert meta["flagged"] == [] and meta["research"] is None and isinstance(meta["ms"], int) and meta["ms"] >= 0
    assert "base_url" not in json.dumps(meta) and ":" not in meta["provider"]  # the host name only, never the address
    # each step says how long it took, and whether it failed
    assert [isinstance(x["ms"], int) for x in ev["step"]] == [True, True]
    assert "failed" not in ev["step"][0] and ev["step"][1]["failed"] is True

    msgs = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    mid = ev["done"][0]["message"]
    answer, question = msgs["messages"][-1], msgs["messages"][-2]
    assert answer["id"] == mid and kept(answer["meta"]) == kept(meta) and answer["model"] == "fake"  # kept, with `model` as before
    assert meta["scope"] == msgs["scope"]  # the scope it answered in, after the conversation was narrowed
    assert kept(question["meta"]) == {"via": "web", "options": {}} and question["id"] == mid - 1

    # every model call made for it names it in the ledger, and its tokens and cost are theirs
    one = c.get(f"/api/v1/chats/{cid}/messages/{mid}", headers=h).json()
    assert kept(one["meta"]) == kept(meta) and one["content"] == answer["content"]
    calls = [r for r in one["ledger"] if r["kind"] == "out"]
    assert sum(r["action"] == "model.chat" for r in calls) >= 2
    assert all({f"chat_message:{mid}", f"chat:{cid}"} <= set(r["resources"]) for r in calls)
    assert meta["tokens"] == {"in": sum(r.get("tokens_in") or 0 for r in calls), "out": sum(r.get("tokens_out") or 0 for r in calls)}
    assert meta["tokens"]["in"] >= 200 and meta["cost_usd"] == pytest.approx(sum(r.get("cost_usd") or 0 for r in calls))
    assert meta["cost_usd"] > 0 and meta["cost_estimated"] is False
    # yours alone: the message's activity too
    assert c.get("/api/v1/activity", headers=h, params={"resource": f"chat_message:{mid}"}).status_code == 200
    cv, hv = s.cl["viewer"]
    assert cv.get(f"/api/v1/chats/{cid}/messages/{mid}", headers=hv).status_code == 404
    assert cv.get("/api/v1/activity", headers=hv, params={"resource": f"chat_message:{mid}"}).status_code == 403
    assert c.get(f"/api/v1/chats/{cid}/messages/999999", headers=h).status_code == 404

    # a question said in a voice conversation, and one asked with another model, say so
    llm.tool_script = [{"content": "Hello!"}]
    cid, ev, _ = ask(c, h, "zzqx", via="voice", model="fake-large")
    question = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-2]
    assert kept(question["meta"]) == {"via": "voice", "options": {"model": "fake-large"}} and ev["meta"][0]["model"] == "fake-large"
    # answered without looking anything up
    assert (ev["meta"][0]["outcome"], ev["meta"][0]["used"]) == ("direct", {"rounds": 0, "tool_calls": 0})


def test_how_an_answer_ended(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    # its rounds ran out, so it answered from what it found
    llm.tool_script = [search(i) for i in range(6)] + [{"content": "The capsid model won [1]."}]
    _, ev, _ = ask(c, h, "Would you hire them?")
    assert (ev["meta"][0]["outcome"], ev["meta"][0]["used"]) == ("limit", {"rounds": 6, "tool_calls": 6})
    # the model can't use tools: from a search instead
    llm.reject_tools = True
    _, ev, _ = ask(c, h, "capsid")
    meta = ev["meta"][0]
    assert "notice" in ev and meta["outcome"] == "fallback" and meta["tokens"]["out"] >= USAGE["completion_tokens"]
    llm.reject_tools = False


def test_stopped_and_failed_answers_are_recorded(plain, app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    monkeypatch.setattr(chat.Answering, "EVERY", 0)
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    stream = llm_mod.stream_chat

    def stopped(*a, **k):  # the person presses Stop after the first piece
        pieces = stream(*a, **k)
        yield next(pieces)
        chat.request_stop(db, cid)
        yield from pieces

    monkeypatch.setattr(llm_mod, "stream_chat", stopped)
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does the shipment leave?"}).text)
    meta = ev["meta"][0]
    assert "stopped" in ev and meta["outcome"] == "stopped" and meta["tools"]["offered"] == [] and meta["limits"]["rounds"] is None
    # the call that was cut off still counts for it (its row is written when the stream is closed)
    one = c.get(f"/api/v1/chats/{cid}/messages/{ev['done'][0]['message']}", headers=h).json()
    assert kept(one["meta"]) == kept(meta) and "model.chat" in [r["action"] for r in one["ledger"]]

    def broken(*_a, **_k):
        raise llm_mod.LLMError("429 from the LLM server")
        yield  # a generator, like the real one

    monkeypatch.setattr(llm_mod, "stream_chat", broken)
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Once more?"}).text)
    assert ev["meta"][0]["outcome"] == "error" and ev["error"][0]["message"] == "429 from the LLM server"
    # with no model set up, the best excerpts come back on their own
    assert c.put("/api/v1/settings/llm", headers=s.cl["admin"][1], json={"base_url": None}).status_code == 200
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "capsid"}).text)
    meta = ev["meta"][0]
    assert (meta["outcome"], meta["model"], meta["provider"], meta["tokens"]) == ("fallback", None, None, {"in": 0, "out": 0})


def test_old_messages_have_no_record(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    chat.add(db, cid, "user", "From before")
    mid = chat.add(db, cid, "assistant", "An answer from before", [], steps=[{"tool": "search_transcripts", "args": {}, "summary": "x"}])
    got = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert [m["meta"] for m in got] == [None, None] and got[-1]["steps"][0].get("ms") is None
    one = c.get(f"/api/v1/chats/{cid}/messages/{mid}", headers=h).json()
    assert one["meta"] is None and one["ledger"] == []
