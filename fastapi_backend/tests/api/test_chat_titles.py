"""Conversation titles (docs/assistant.md#titles): after a first answer the model titles a conversation nobody named,
with the cheaper model for summaries, counted for the conversation in the activity ledger; a name someone gives it is
kept, and agents rename or retitle it over the API and MCP."""

from __future__ import annotations

import threading
import time

import pytest

from app.domain import chat
from tests import fake_llm
from tests.api._assist import sse, start_llm
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

Q = "When does the Dyno Therapeutics shipment leave?"


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    cfg["ai"].update(auto_title=True, tools=False, compact_model="small-model")
    yield fake_llm.Handler
    fake_llm.Handler.title = None
    srv.shutdown()


@pytest.fixture
def h(client, db, cfg, folder, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    return login(client, "ed@x.io", "editor password 1")


def titling(seen):
    return [b for b in seen if b["messages"][0]["content"] == chat.TITLE_SYSTEM]


def ask(client, h, cid, content, **more):
    return sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": content, **more}).text)


def test_a_first_answer_titles_the_conversation(client, db, h, llm):
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (c["title"], c["title_by"]) == ("New conversation", "assistant")

    llm.title = '**Title:** "Dyno shipment date".'
    ev = ask(client, h, cid, Q)
    assert ev["done"] and ev["title"] == [{"title": "Dyno shipment date"}]  # streamed after the answer, cleaned up
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (c["title"], c["title_by"]) == ("Dyno shipment date", "assistant")
    [sent] = titling(llm.seen)
    assert sent["model"] == "small-model" and sent["max_tokens"] == chat.TITLE_TOKENS  # the cheaper model, a few words
    assert sent["messages"][1]["content"] == f"Person: {Q}\n\nAssistant: The shipment leaves on Friday [1]."
    # one model call, the conversation's in the ledger, never the answer's
    rows = client.get("/api/v1/activity", headers=h, params={"resource": f"chat:{cid}", "kind": "out"}).json()
    small = [r for r in rows if r.get("model") == "small-model"]
    assert len(small) == 1 and small[0]["action"] == "model.chat" and small[0]["kind"] == "out"
    assert not any(r.startswith("chat_message:") for r in small[0]["resources"])
    assert client.get("/api/v1/chats", headers=h).json()[0]["title_by"] == "assistant"

    # later answers don't retitle it, nor does editing a later question
    ask(client, h, cid, "Who ships it?")
    second = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][2]["id"]
    ask(client, h, cid, "Who ships it, exactly?", edit=second)
    assert len(titling(llm.seen)) == 1

    # editing the first question titles it again, as Lens's title is still Lens's
    first = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][0]["id"]
    llm.title = "Friday shipment"
    assert ask(client, h, cid, "When is the Friday shipment?", edit=first)["title"] == [{"title": "Friday shipment"}]
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == "Friday shipment"

    # a name someone gives it is theirs: kept when the first question is edited again
    assert client.patch(f"/api/v1/chats/{cid}", headers=h, json={"title": "Shipping"}).status_code == 200
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (c["title"], c["title_by"]) == ("Shipping", "person")
    ev = ask(client, h, cid, "When does it ship?", edit=c["messages"][0]["id"])
    assert "title" not in ev and client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == "Shipping"
    assert len(titling(llm.seen)) == 2

    # asked for, Lens titles it now from how it starts, and the title is Lens's again
    llm.title = "Shipment timing"
    assert client.post(f"/api/v1/chats/{cid}/title", headers=h).json() == {"title": "Shipment timing", "title_by": "assistant"}
    sent = titling(llm.seen)[-1]["messages"][1]["content"]
    assert sent.startswith("Person: When does it ship?\n\nAssistant: ") and sent.count("Person: ") == 1  # the version showing
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (c["title"], c["title_by"]) == ("Shipment timing", "assistant")


def test_a_title_you_give_is_kept(client, h, llm):
    cid = client.post("/api/v1/chats", headers=h, json={"title": "Episode 2"}).json()["id"]
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title_by"] == "person"
    assert "title" not in ask(client, h, cid, Q)
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == "Episode 2" and titling(llm.seen) == []


def test_off_failed_or_without_a_model_it_keeps_the_first_question(client, new_client, db, h, llm):
    make_user(db, "root@x.io", "root password 1", admin=True)
    admin = new_client()
    ha = login(admin, "root@x.io", "root password 1")
    assert admin.get("/api/v1/settings", headers=ha).json()["ai"]["values"]["auto_title"] is True
    assert admin.put("/api/v1/settings/ai", headers=ha, json={"auto_title": "yes"}).status_code == 400
    assert admin.put("/api/v1/settings/ai", headers=ha, json={"auto_title": False}).status_code == 200  # Settings › AI assistant
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert "title" not in ask(client, h, cid, Q) and titling(llm.seen) == []
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == Q

    assert admin.put("/api/v1/settings/ai", headers=ha, json={"auto_title": True}).status_code == 200
    llm.title = '""'  # the model said nothing usable
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert "title" not in ask(client, h, cid, Q) and len(titling(llm.seen)) == 1
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (c["title"], c["title_by"]) == (Q, "assistant")
    assert client.post(f"/api/v1/chats/{cid}/title", headers=h).status_code == 400

    empty = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert client.post(f"/api/v1/chats/{empty}/title", headers=h).status_code == 409  # nothing said yet
    assert admin.put("/api/v1/settings/llm", headers=ha, json={"base_url": None}).status_code == 200
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert "title" not in ask(client, h, cid, "shipment Friday")
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title"] == "shipment Friday"
    assert client.post(f"/api/v1/chats/{cid}/title", headers=h).status_code == 400


def test_a_slow_title_doesnt_hold_up_the_answer(db, cfg, llm, monkeypatch):
    cid = chat.create(db, 1)
    chat.title_from_question(db, cid, Q)
    go = threading.Event()

    def slow(cfg, said):
        go.wait(5)
        return "Late title"

    monkeypatch.setattr(chat, "suggest_title", slow)
    assert chat.title_soon(db, cfg, cid, Q, "On Friday.", wait=0.05) is None  # not yet
    go.set()
    for _ in range(100):
        if chat.get(db, cid, 1)["title"] == "Late title":
            break
        time.sleep(0.02)
    assert chat.get(db, cid, 1)["title"] == "Late title"  # saved when it came

    # someone who names it meanwhile keeps their name
    monkeypatch.setattr(chat, "suggest_title", lambda cfg, said: "Too late")
    chat.rename(db, cid, "Mine")
    assert chat.auto_title(db, cfg, cid, Q, "On Friday.") is None and chat.get(db, cid, 1)["title"] == "Mine"


def test_titles_are_cleaned():
    for said, title in (
        ("Dyno shipment date", "Dyno shipment date"),
        ('Title: "Dyno shipment date."', "Dyno shipment date"),
        ("**Title:** _Dyno shipment_\nbecause the person asked", "Dyno shipment"),
        ("\n\n# Shipments  to   Mars", "Shipments to Mars"),
        ("“Shipments to Mars”", "Shipments to Mars"),
        ("'Friday?'", "Friday?"),
        ('""', ""),
        ("x" * 200, "x" * chat.TITLE_MAX),
    ):
        assert chat.clean_title(said) == title, said


def test_conversations_from_before_titles_are_kept_or_titled(db):
    cid = chat.create(db, 1)
    db.q("UPDATE $r SET title_by = NONE", r=chat.R("chat", cid))  # as one made before Lens kept who titled it
    assert chat.lens_titles(chat.get(db, cid, 1))  # still untitled: Lens's
    db.q("UPDATE $r SET title = 'Planning'", r=chat.R("chat", cid))
    assert not chat.lens_titles(chat.get(db, cid, 1))  # named: kept


def test_agents_rename_and_retitle_over_mcp(client, h, llm):
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    llm.title = "Dyno shipment date"
    ask(client, h, cid, Q)
    _, tokens = _grant(client, h, scope="write")
    b = _bearer(tokens)
    [row] = tool(client, b, "list_chats")["chats"]
    assert (row["title"], row["title_by"]) == ("Dyno shipment date", "assistant")

    got = tool(client, b, "rename_chat", chat_id=cid, title="  Shipping  ")
    assert (got["title"], got["title_by"], got["url"]) == ("Shipping", "person", f"http://localhost:3000/chat/{cid}")
    assert tool(client, b, "read_chat", chat_id=cid)["title_by"] == "person"
    llm.title = "Shipment timing"
    assert tool(client, b, "rename_chat", chat_id=cid)["title"] == "Shipment timing"  # left out: Lens titles it
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["title_by"] == "assistant"

    _, read = _grant(client, h, scope="read")
    assert "read-only" in tool_error(client, _bearer(read), "rename_chat", chat_id=cid, title="x")
    assert "not found" in tool_error(client, b, "rename_chat", chat_id=999_999, title="x")
