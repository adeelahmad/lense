"""Long conversations: the latest messages go to the model word for word, older ones as a running summary."""

from __future__ import annotations

import pytest

from app.domain import chat
from tests import fake_llm
from tests.api._assist import sse, start_llm
from tests.helpers import login, make_user, seed


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    fake_llm.Handler.seen = []
    yield fake_llm.Handler
    srv.shutdown()


def talk(db, cid, n):
    """n more messages, numbered on from the ones before, taking turns."""
    start = len(chat.history(db, cid))
    for i in range(start, start + n):
        chat.add(db, cid, "user" if i % 2 == 0 else "assistant", f"message {i}")


def summaries(seen):
    return [b for b in seen if b["messages"][0]["content"] == chat.COMPACT_SYSTEM]


def test_older_messages_are_folded_into_a_summary(db, cfg, llm):
    cid = chat.create(db, 1)
    talk(db, cid, chat.RECENT + chat.COMPACT_AFTER - 1)
    assert chat.compact(db, cfg, cid) is None and summaries(llm.seen) == []  # not enough built up yet
    talk(db, cid, 1)
    out = chat.compact(db, cfg, cid)
    ids = [m["id"] for m in chat.history(db, cid)]
    assert out["text"] == "OK" and out["upto"] == ids[-chat.RECENT - 1]  # the fake model's summary, up to the recent ones
    asked = summaries(llm.seen)[0]["messages"][1]["content"]
    assert "Person: message 0" in asked and f"message {chat.COMPACT_AFTER - 1}" in asked and f"message {chat.COMPACT_AFTER}\n" not in asked
    assert chat.compact(db, cfg, cid) is None  # nothing new to fold in

    # the model gets the summary and only what it doesn't cover
    mem = chat.memory(db, cfg, cid)
    msgs = chat.messages_for("and then?", [], chat.history(db, cid), mem)
    assert "Earlier in this conversation" in msgs[0]["content"] and msgs[0]["content"].endswith("\nOK")
    assert [m["content"] for m in msgs[1:-1]] == [f"message {i}" for i in range(chat.COMPACT_AFTER, chat.COMPACT_AFTER + chat.RECENT)]

    # the next fold starts from the summary so far
    talk(db, cid, chat.COMPACT_AFTER)
    assert chat.compact(db, cfg, cid)["upto"] == chat.history(db, cid)[-chat.RECENT - 1]["id"]
    assert "Summary so far:\nOK" in summaries(llm.seen)[1]["messages"][1]["content"]

    # editing a question the summary covers drops the summary; it's made again from what's left
    chat.rewind(db, cid, ids[2])
    assert chat.memory(db, cfg, cid) == {"text": "", "upto": 0}


def test_compaction_can_be_turned_off(db, cfg, llm):
    cfg["ai"]["compact"] = False
    cid = chat.create(db, 1)
    talk(db, cid, 30)
    assert chat.compact(db, cfg, cid) is None and chat.memory(db, cfg, cid) is None
    assert len(chat.messages_for("q", [], chat.history(db, cid))) == 6 + 2  # the last six, as before


def test_a_long_chat_in_the_app_is_summarised(client, db, cfg, folder, llm):
    cfg["ai"]["tools"] = False
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    turns = (chat.RECENT + chat.COMPACT_AFTER) // 2
    for i in range(turns):
        sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": f"shipment question {i}"}).text)
    assert len(summaries(llm.seen)) == 1
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "shipment Friday?"}).text)
    answered = [b for b in llm.seen if b.get("stream")][-1]["messages"]
    assert "Earlier in this conversation" in answered[0]["content"]
    assert "shipment question 0" not in str(answered[1:])  # folded into the summary, not repeated


def test_a_conversation_that_fills_the_context_is_folded_in_early(db, cfg, llm):
    cfg["ai"]["context_tokens"] = 1700  # seven of these long messages fill half of it, six do not
    cfg["ai"]["compact_model"] = "small-model"
    cid = chat.create(db, 1)
    for i in range(chat.RECENT + 1):
        chat.add(db, cid, "user" if i % 2 == 0 else "assistant", f"long message {i} " + "word " * 100)
    assert chat.compact(db, cfg, cid, full_only=True)["upto"] == chat.history(db, cid)[0]["id"]  # well before COMPACT_AFTER
    assert summaries(llm.seen)[0]["model"] == "small-model"  # written by the cheaper model
    talk(db, cid, 2)
    assert chat.compact(db, cfg, cid, full_only=True) is None  # short ones don't fill it


def test_commands_are_read_from_the_message():
    assert chat.command("/clear") == ("clear", "")
    assert chat.command("  /Compact keep the dates\n") == ("compact", "keep the dates")
    assert chat.command("/clearly not") is None and chat.command("what does /clear do?") is None


def test_clear_starts_afresh_and_compact_folds_everything_now(db, cfg, llm):
    cid = chat.create(db, 1)
    talk(db, cid, 4)
    reply, mid = chat.run_command(db, cfg, cid, "compact", "keep the dates")
    assert reply.startswith("Compacted 4 messages") and reply.endswith("OK")
    assert "The person asked the summary to keep: keep the dates" in summaries(llm.seen)[0]["messages"][1]["content"]
    mem = chat.memory(db, cfg, cid)
    assert mem["upto"] == mid and mem["text"] == "OK"
    assert chat.past_turns(chat.history(db, cid), summary=mem) == []  # nor the command and its reply
    assert chat.run_command(db, cfg, cid, "compact")[0] == "Nothing new to compact yet."

    talk(db, cid, 2)
    reply, mid = chat.run_command(db, cfg, cid, "clear")
    assert reply.startswith("Cleared")
    assert chat.memory(db, cfg, cid) == {"text": "", "upto": mid, "at": chat.memory(db, cfg, cid)["at"]}
    talk(db, cid, 1)
    msgs = chat.messages_for("q", [], chat.history(db, cid), chat.memory(db, cfg, cid))
    assert "Earlier in this conversation" not in msgs[0]["content"] and len(msgs) == 3  # just what came after

    # with automatic compaction off, the commands still work, and the last six after a clear are read
    cfg["ai"]["compact"] = False
    talk(db, cid, 10)
    assert chat.compact(db, cfg, cid) is None
    assert len(chat.messages_for("q", [], chat.history(db, cid), chat.memory(db, cfg, cid))) == 6 + 2


def test_commands_in_the_app(client, db, cfg, folder, llm):
    cfg["ai"]["tools"] = False
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "shipment question"}).text)
    asked = len(llm.seen)
    out = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "/clear"}).text
    assert "event: cleared" in out and "Cleared." in out and len(llm.seen) == asked  # no model call
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "shipment Friday?"}).text)
    answered = [b for b in llm.seen if b.get("stream")][-1]["messages"]
    assert "shipment question" not in str(answered) and "/clear" not in str(answered)
    out = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "/compact"}).text
    assert "event: compacted" in out and "Compacted 2 messages" in out
    shown = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert [m["content"] for m in shown][:3] == ["shipment question", "The shipment leaves on Friday [1].", "/clear"]


def test_the_summary_settings_are_checked(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    ok = client.put("/api/v1/settings/ai", headers=h, json={"compact_model": " small ", "context_tokens": 32000})
    assert ok.status_code == 200, ok.text
    for bad in ({"context_tokens": 10}, {"context_tokens": "big"}, {"compact_model": 3}):
        assert client.put("/api/v1/settings/ai", headers=h, json=bad).status_code == 400, bad
