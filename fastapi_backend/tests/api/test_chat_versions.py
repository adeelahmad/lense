"""Versions: a conversation is a tree; regenerating an answer or editing a question makes a version beside it, and
the one showing (the head) is what the model reads and the next question goes on from."""

from __future__ import annotations

import pytest

from app.domain import chat
from tests import fake_llm
from tests.api._assist import sse, start_llm
from tests.helpers import login, make_user, seed


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    cfg["ai"]["tools"] = False  # the plain search-and-answer path
    yield fake_llm.Handler
    srv.shutdown()


def ask(client, h, cid, **body):
    r = client.post(f"/api/v1/chats/{cid}/messages", headers=h, json=body)
    assert r.status_code == 200, r.text
    return sse(r.text)


def asked_of(seen):
    """The questions the model was given, earlier ones first, the one it answers last."""
    return [m["content"] for m in seen[-1]["messages"] if m["role"] == "user"]


def test_regenerate_edit_switch_and_go_on(client, new_client, db, cfg, folder, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    for q in ("When does the shipment leave?", "Who is shipping it?"):
        assert "branch" not in ask(client, h, cid, content=q)  # going on from the head makes no version
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    q1, a1, q2, a2 = (m["id"] for m in c["messages"])
    assert [m["parent"] for m in c["messages"]] == [None, q1, a1, q2] and c["head"] == a2
    assert c["messages"][1]["versions"] == {"index": 1, "count": 1, "ids": [a1]}
    assert [(n["parent"], n["role"]) for n in c["tree"]] == [(None, "user"), (q1, "assistant"), (a1, "user"), (q2, "assistant")]

    # regenerating the first answer, with another model: a version beside it, reading only what came before it
    llm.seen.clear()
    ev = ask(client, h, cid, regenerate=a1, model="fake-large")
    b1 = ev["done"][0]["message"]
    assert ev["branch"] == [{"message": b1, "parent": q1, "versions": {"index": 2, "count": 2, "ids": [a1, b1]}}]
    assert asked_of(llm.seen) == [asked_of(llm.seen)[-1]] and "When does the shipment leave?" in asked_of(llm.seen)[-1]
    assert llm.seen[-1]["model"] == "fake-large"
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert [m["id"] for m in c["messages"]] == [q1, b1] and c["messages"][1]["model"] == "fake-large"
    assert c["messages"][1]["versions"] == {"index": 2, "count": 2, "ids": [a1, b1]} and len(c["tree"]) == 5

    # going on from this version: the model reads it, not the other
    llm.seen.clear()
    ask(client, h, cid, content="And where to?")
    assert len(asked_of(llm.seen)) == 2 and not any("Who is shipping it?" in s for s in asked_of(llm.seen))
    q3, a3 = (m["id"] for m in client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][2:])

    # switching back shows the first version to its end, and the next question goes on from there
    c = client.put(f"/api/v1/chats/{cid}/head", headers=h, json={"message": a1}).json()
    assert [m["id"] for m in c["messages"]] == [q1, a1, q2, a2] and c["head"] == a2
    assert c["messages"][1]["versions"]["index"] == 1
    llm.seen.clear()
    ask(client, h, cid, content="Is it insured?")
    assert any("Who is shipping it?" in s for s in asked_of(llm.seen)) and not any("And where to?" in s for s in asked_of(llm.seen))

    # editing a question makes a version of it, beside it; the old one keeps what followed it
    ev = ask(client, h, cid, content="When does the dyno shipment leave?", edit=q1)
    e1 = ev["branch"][0]["message"]
    assert ev["branch"][0]["parent"] is None and ev["branch"][0]["versions"] == {"index": 2, "count": 2, "ids": [q1, e1]}
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert [m["content"] for m in c["messages"] if m["role"] == "user"] == ["When does the dyno shipment leave?"]
    assert c["messages"][0]["versions"] == {"index": 2, "count": 2, "ids": [q1, e1]}

    # a question can go on from any message, of any version: beside the others there, it's a version too
    llm.seen.clear()
    ev = ask(client, h, cid, content="Who packs it?", parent=b1)
    assert ev["branch"][0]["versions"] == {"index": 2, "count": 2, "ids": [q3, ev["branch"][0]["message"]]}
    assert "When does the shipment leave?" in asked_of(llm.seen)[0] and len(asked_of(llm.seen)) == 2
    c = client.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert [m["id"] for m in c["messages"]][:2] == [q1, b1] and c["messages"][2]["content"] == "Who packs it?"
    assert a3 in {n["id"] for n in c["tree"]}  # nothing is deleted


def test_what_versions_refuse(client, new_client, db, cfg, folder, llm):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "ed@x.io", "editor password 1")
    other = new_client()
    hv = login(other, "vi@x.io", "viewer password 1")
    cid = client.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ask(client, h, cid, content="When does the shipment leave?")
    ask(client, h, cid, content="/clear")
    q1, a1, clear, cleared = (m["id"] for m in client.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"])
    elsewhere = client.post("/api/v1/chats", headers=h, json={}).json()["id"]

    def post(json, c=client, hh=h, to=cid):
        return c.post(f"/api/v1/chats/{to}/messages", headers=hh, json=json).status_code

    assert post({"regenerate": q1}) == 404  # a question isn't an answer
    assert post({"regenerate": 999999}) == 404
    assert post({"regenerate": a1}, to=elsewhere) == 404
    assert post({"regenerate": a1}, other, hv) == 404
    assert post({"regenerate": cleared}) == 400  # a command's reply isn't the model's
    assert post({"regenerate": a1, "edit": q1, "content": "x"}) == 400
    assert post({"content": "x", "edit": q1, "parent": a1}) == 400
    assert post({"content": "x", "parent": 999999}) == 404
    assert post({"content": "x", "parent": a1}, to=elsewhere) == 404
    assert client.put(f"/api/v1/chats/{cid}/head", headers=h, json={"message": 999999}).status_code == 404
    assert client.put(f"/api/v1/chats/{elsewhere}/head", headers=h, json={"message": a1}).status_code == 404
    assert other.put(f"/api/v1/chats/{cid}/head", headers=hv, json={"message": a1}).status_code == 404
    assert len(client.get(f"/api/v1/chats/{cid}", headers=h).json()["tree"]) == 4  # nothing was added

    # /clear on another version applies to that version only
    client.put(f"/api/v1/chats/{cid}/head", headers=h, json={"message": a1})
    assert client.get(f"/api/v1/chats/{cid}", headers=h).json()["head"] == cleared  # the newest below it
    ask(client, h, cid, regenerate=a1)
    assert chat.memory(db, cfg, cid) == {"text": "", "upto": 0}  # the /clear was said on the other version
    ask(client, h, cid, content="/clear", parent=q1)  # a command can go on from any message too
    path = chat.path(db, cid)
    assert [m["content"] for m in path][-2:][0] == "/clear" and path[1]["parent"] == q1
    assert chat.memory(db, cfg, cid)["upto"] == path[-1]["id"]


def test_rooms_and_automations_go_on_in_one_line(db, cfg, llm):
    cid = chat.create(db, 1, kind="bridge")
    for i in range(4):
        chat.add(db, cid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    nodes = chat.tree(db, cid)
    assert [n.get("parent") for n in nodes] == [None, *(n["id"] for n in nodes[:-1])] and chat.head(db, cid) == nodes[-1]["id"]
    assert all(v["count"] == 1 for v in chat.versions(nodes).values())
