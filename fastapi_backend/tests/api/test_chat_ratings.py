"""Thumbs up or down on an answer (docs/assistant.md#rating-answers): saved on the answer, shown when the conversation
opens, carried in its JSON export, and given the same way by agents over MCP."""

from __future__ import annotations

import pytest

from tests import fake_llm
from tests.api._assist import sse, start_llm
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

Q = "When does the Dyno Therapeutics shipment leave?"


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    cfg["ai"].update(tools=False)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def h(client, db, cfg, folder, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    return login(client, "ed@x.io", "editor password 1")


def answered(client, h):
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": Q}).text)
    q, a = client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    return cid, q["id"], a


def test_rating_an_answer_is_saved_shown_and_exported(client, h):
    cid, qid, a = answered(client, h)
    assert a["rating"] is None  # nobody rated it yet
    url = f"/api/v1/chats/{cid}/messages/{a['id']}/rating"
    assert client.put(url, headers=h, json={"rating": "up"}).json() == {"id": a["id"], "rating": "up"}
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][1]["rating"] == "up"
    assert client.get(f"/api/v1/chats/{cid}/messages/{a['id']}", headers=h).json()["rating"] == "up"
    node = client.get(f"/api/v1/chats/{cid}/export.json", headers=h).json()["mapping"][str(a["id"])]
    assert node["lens"]["rating"] == "up"  # training data reads it from the export

    assert client.put(url, headers=h, json={"rating": "down"}).json()["rating"] == "down"  # changed
    assert client.put(url, headers=h, json={"rating": None}).json()["rating"] is None  # cleared
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][1]["rating"] is None

    assert client.put(url, headers=h, json={"rating": "meh"}).status_code == 422
    assert client.put(f"/api/v1/chats/{cid}/messages/{qid}/rating", headers=h, json={"rating": "up"}).status_code == 404
    assert client.put(f"/api/v1/chats/{cid}/messages/999999/rating", headers=h, json={"rating": "up"}).status_code == 404


def test_someone_elses_answer_cant_be_rated(client, new_client, db, h):
    cid, _, a = answered(client, h)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "editor"})
    other = new_client()
    ho = login(other, "vi@x.io", "viewer password 1")
    assert other.put(f"/api/v1/chats/{cid}/messages/{a['id']}/rating", headers=ho, json={"rating": "down"}).status_code == 404
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][1]["rating"] is None


def test_agents_rate_answers_over_mcp(client, h):
    cid, qid, a = answered(client, h)
    _, tokens = _grant(client, h, scope="write")
    b = _bearer(tokens)
    got = tool(client, b, "rate_answer", chat_id=cid, message=a["id"], rating="down")
    assert got == {"chat_id": cid, "message": a["id"], "rating": "down", "url": f"http://localhost:3000/chat/{cid}"}
    assert tool(client, b, "read_chat", chat_id=cid)["messages"][1]["rating"] == "down"
    assert tool(client, b, "rate_answer", chat_id=cid, message=a["id"], rating="clear")["rating"] is None
    assert "rating" not in tool(client, b, "read_chat", chat_id=cid)["messages"][1]

    assert "isn't an answer" in tool_error(client, b, "rate_answer", chat_id=cid, message=qid, rating="up")
    _, read = _grant(client, h, scope="read")
    assert "read-only" in tool_error(client, _bearer(read), "rate_answer", chat_id=cid, message=a["id"], rating="up")
