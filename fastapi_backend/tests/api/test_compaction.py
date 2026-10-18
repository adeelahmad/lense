"""Long conversations: the latest messages go to the model word for word, older ones as a running summary."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from app.domain import chat
from app.domain import llm as llm_mod
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

    # on a version beside a question the summary covers, it isn't used; it's made again from that version's messages
    last = chat.head(db, cid)
    chat.add(db, cid, "user", "message 2, edited", parent=ids[1])
    assert chat.memory(db, cfg, cid) == {"text": "", "upto": 0}
    llm.seen.clear()
    talk(db, cid, chat.RECENT + chat.COMPACT_AFTER - 3)  # with the 3 messages of this version, enough to fold in
    again = chat.compact(db, cfg, cid)
    asked = summaries(llm.seen)[0]["messages"][1]["content"]
    assert "Summary so far:\n(none yet)" in asked and "Person: message 2, edited" in asked and "message 3\n" not in asked
    assert again["upto"] == chat.path(db, cid)[-chat.RECENT - 1]["id"]
    # switching back to the first version: the summary is now the other's, so its messages are read until it's made again
    assert chat.switch(db, cid, ids[2]) == last == chat.head(db, cid)
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


def long_talk(db, cid, n):
    for i in range(n):
        chat.add(db, cid, "user" if i % 2 == 0 else "assistant", f"long message {i} " + "word " * 100)


def forget_windows():
    llm_mod._MODELS.clear()
    llm_mod._WINDOWS.clear()


def test_the_context_window_is_the_model_servers(db, cfg, llm):
    """Without ai.context_tokens, a conversation is folded in early at the context the model really has: what the
    model server lists, llama.cpp's /props, or Lens's own local model's."""
    cid = chat.create(db, 1)
    long_talk(db, cid, chat.RECENT + 1)  # seven of these fill half of 1700 tokens, not half of 16000
    forget_windows()
    assert chat.window(cfg) == chat.CONTEXT_TOKENS and chat.compact(db, cfg, cid, full_only=True) is None

    llm.about = {"fake": {"max_model_len": 1700}}  # vLLM
    forget_windows()
    assert chat.window(cfg) == 1700 and chat.compact(db, cfg, cid, full_only=True)["folded"] == 1

    cid = chat.create(db, 1)
    long_talk(db, cid, chat.RECENT + 1)
    llm.about, llm.props = (
        {"fake": {"owned_by": "llamacpp", "meta": {"n_ctx_train": 131072}}},
        {"default_generation_settings": {"n_ctx": 1700}},
    )
    forget_windows()
    assert chat.window(cfg) == 1700  # what the server runs with, not what the model was trained for
    assert chat.compact(db, cfg, cid, full_only=True)["folded"] == 1

    cid = chat.create(db, 1)
    long_talk(db, cid, chat.RECENT + 1)
    llm.about, llm.props = {}, None
    port = urlsplit(cfg["llm"]["base_url"]).port
    cfg["local_llm"] = {**cfg.get("local_llm", {}), "enabled": True, "model": "fake", "port": port, "context": 1700}
    forget_windows()
    assert chat.window(cfg) == 1700 and chat.compact(db, cfg, cid, full_only=True)["folded"] == 1
    cfg["local_llm"]["enabled"] = False

    # ai.context_tokens, when set, is what counts; ai.compact_at is how much of it a conversation may fill
    llm.about = {"fake": {"context_length": 1700}, "fake-large": {"context_length": 1700}}
    forget_windows()
    cid = chat.create(db, 1)
    long_talk(db, cid, chat.RECENT + 1)
    cfg["ai"]["context_tokens"] = 100000
    assert chat.window(cfg) == 100000 and chat.compact(db, cfg, cid, full_only=True) is None
    cfg["ai"]["context_tokens"], cfg["ai"]["compact_at"] = None, 90
    assert chat.compact(db, cfg, cid, full_only=True) is None
    cfg["ai"]["compact_at"] = 50
    assert chat.compact(db, cfg, cid, full_only=True)["folded"] == 1

    # the conversation's own model's context counts, when it has one
    llm.about = {"fake-large": {"context_length": 1700}}
    forget_windows()
    cid = chat.create(db, 1, model="fake-large")
    long_talk(db, cid, chat.RECENT + 1)
    assert chat.window(cfg) == chat.CONTEXT_TOKENS and chat.compact(db, cfg, cid, full_only=True)["folded"] == 1


def test_a_long_conversation_is_folded_in_runs_that_fit_the_summary_model(db, cfg, llm):
    cfg["ai"]["compact_model"] = "fake-large"
    llm.about = {"fake-large": {"context_length": 4096}}  # about a dozen of these long messages at a time
    forget_windows()
    cid = chat.create(db, 1)
    long_talk(db, cid, 30)
    reply, mid = chat.run_command(db, cfg, cid, "compact")
    asked = summaries(llm.seen)
    assert reply.startswith("Compacted 30 messages") and len(asked) == 3
    assert all(b["model"] == "fake-large" and b["max_tokens"] == chat.SUMMARY_TOKENS for b in asked)
    assert "(none yet)" in asked[0]["messages"][1]["content"] and "Summary so far:\nOK" in asked[2]["messages"][1]["content"]
    assert "long message 29 " in asked[2]["messages"][1]["content"] and chat.memory(db, cfg, cid)["upto"] == mid
    # each call is in the activity ledger as the conversation's
    rows = db.rows("SELECT resources FROM activity WHERE action = 'model.chat' AND model = 'fake-large'")
    assert len(rows) == 3 and all(f"chat:{cid}" in r["resources"] for r in rows)


def test_the_server_saying_the_context_is_full_is_understood(cfg, llm):
    """A 400 that says the request is too long is llm.ContextFull, and the window it names is kept as the model's."""
    forget_windows()
    llm.context = 500
    with pytest.raises(llm_mod.ContextFull) as e:
        llm_mod.chat(cfg, [{"role": "user", "content": "word " * 1000}])
    assert e.value.window == 500 and llm_mod.context_window(cfg) == 500 and chat.window(cfg) == 500
    assert llm_mod.chat(cfg, [{"role": "user", "content": "hi"}]) == "OK"


class BigBox:
    """A toolbox with one tool, whose result is far too long for a small model."""

    flagged: list[str] = []
    approvals: list[dict] = []

    def specs(self):
        return [{"type": "function", "function": {"name": "read_big", "parameters": {"type": "object", "properties": {}}}}]

    def call(self, name, args, model=False):
        return json.dumps({"text": "line " * 6000}), "Read something big"

    def take_detail(self):
        return {}


def test_what_tools_returned_is_cut_to_fit_the_model(cfg, llm):
    forget_windows()
    llm.context = 2000
    llm.tool_script = [
        {"content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_big", "arguments": "{}"}}]},
        {"content": "Done [1]."},
    ]
    out = list(chat.tool_answer(cfg, BigBox(), "what's in it?", (), 6))
    assert out[-1] == ("answer", "Done [1].") and out[0][0] == "step"
    sent = [b for b in llm.seen if b.get("tools")]
    results = [m["content"] for b in sent for m in b["messages"] if m["role"] == "tool"]
    assert len(results[0]) > 20000 and "left out, to fit the model's context" not in results[0]  # as sent first: refused
    assert "left out, to fit the model's context" in results[-1] and len(results[-1]) < 2000 * 4  # cut, then answered
    assert results[-1].startswith('{"text": "line') and results[-1].endswith('"}')  # its ends kept, the middle gone
    assert llm_mod.context_window(cfg) == 2000  # what the server said, kept for the next answer


def test_a_conversation_that_doesnt_fit_is_folded_in_and_asked_again(client, db, cfg, folder, llm):
    """The model says the conversation is too long for it (nothing said it had a small context): all but the last two
    messages are folded into the summary now, and the question is asked again with that."""
    cfg["ai"]["tools"] = False
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    long_talk(db, cid, 10)
    forget_windows()
    llm.context = 900  # ten long messages don't fit; two and a summary do
    out = sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "shipment Friday?"}).text)
    assert "error" not in out and "".join(t["text"] for t in out["token"]) == "The shipment leaves on Friday [1]."
    folded = summaries(llm.seen)
    assert folded and "long message 7 " in folded[-1]["messages"][1]["content"] and "long message 8 " not in str(folded)
    answered = [b for b in llm.seen if b.get("stream")]
    assert len(answered) == 2 and "long message 0 " in str(answered[0]["messages"])  # refused, then asked again
    assert "Earlier in this conversation" in answered[1]["messages"][0]["content"]
    assert [m["content"][:14] for m in answered[1]["messages"][1:]] == ["long message 8", "long message 9", "Excerpts:\n\n<un"]
    assert chat.memory(db, cfg, cid)["upto"] == chat.history(db, cid)[-5]["id"]  # up to message 7; 8, 9, the question and answer follow
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]["content"] == "The shipment leaves on Friday [1]."


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
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["summary"] is None  # nothing summarised yet
    asked = len(llm.seen)
    out = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "/clear"}).text
    assert "event: cleared" in out and "Cleared." in out and len(llm.seen) == asked  # no model call
    shown = client.get(f"/api/v1/chats/{cid}", headers=h).json()  # the app marks where the model stops reading
    assert shown["summary"] == {"text": "", "upto": shown["messages"][-1]["id"], "at": shown["summary"]["at"], "covers": 4}
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "shipment Friday?"}).text)
    answered = [b for b in llm.seen if b.get("stream")][-1]["messages"]
    assert "shipment question" not in str(answered) and "/clear" not in str(answered)
    out = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "/compact"}).text
    assert "event: compacted" in out and "Compacted 2 messages" in out
    shown = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert [m["content"] for m in shown["messages"]][:3] == ["shipment question", "The shipment leaves on Friday [1].", "/clear"]
    assert shown["summary"]["text"] == "OK" and shown["summary"]["covers"] == 8 == len(shown["messages"])


def test_the_summary_settings_are_checked(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    ok = client.put("/api/v1/settings/ai", headers=h, json={"compact_model": " small ", "context_tokens": 32000})
    assert ok.status_code == 200, ok.text
    assert client.put("/api/v1/settings/ai", headers=h, json={"compact_at": 70}).status_code == 200
    for bad in (
        {"context_tokens": 10},
        {"context_tokens": "big"},
        {"compact_model": 3},
        {"compact_at": 5},
        {"compact_at": 95},
        {"compact_at": "half"},
    ):
        assert client.put("/api/v1/settings/ai", headers=h, json=bad).status_code == 400, bad
