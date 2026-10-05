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
