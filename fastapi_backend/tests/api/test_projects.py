"""Projects (docs/projects.md): a goal, the namespaces it draws on, a coordinator conversation and threads; every
answer in one reads what the project is for; agents use them over MCP."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, chat, projects, untrusted
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_mcp import rpc, tool, tool_error
from tests.api.test_oauth import _bearer, _grant

WEB = "http://localhost:3000"


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def _ok(c, h, name, args):
    res = rpc(c, h, "tools/call", {"name": name, "arguments": args})["result"]
    assert res["isError"] is False, res
    return res["structuredContent"]


def _error(c, h, name, args):
    res = rpc(c, h, "tools/call", {"name": name, "arguments": args})["result"]
    assert res["isError"] is True
    return json.loads(res["content"][0]["text"])["error"]


def _system(llm):
    return next(m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "system")


def _new(c, h, **over):
    body = {"name": "Treehouse with Sam", "goal": "Build a treehouse with my son by summer.", "namespaces": ["pods"], **over}
    r = c.post("/api/v1/projects", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_project_with_a_goal_namespaces_and_threads(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h, instructions="Keep it cheap.")
    assert (p["name"], p["goal"], p["instructions"], p["namespaces"]) == (
        "Treehouse with Sam",
        "Build a treehouse with my son by summer.",
        "Keep it cheap.",
        ["pods"],
    )
    assert (p["threads"], p["open"], p["archived"], p["thread_list"]) == (0, 0, False, [])
    # it comes with its coordinator: a conversation of its own, named after it, in its namespaces
    coord = c.get(f"/api/v1/chats/{p['coordinator']}", headers=h).json()
    assert (coord["title"], coord["title_by"], coord["project"], coord["scope"]) == (p["name"], "person", p["id"], {"namespaces": ["pods"]})

    # a thread is a conversation in the project's namespaces, titled after its first answer unless named
    tid = c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"title": "Wood and tools"}).json()["id"]
    plain = c.post(f"/api/v1/projects/{p['id']}/threads", headers=h).json()["id"]
    got = c.get(f"/api/v1/chats/{tid}", headers=h).json()
    assert (got["title"], got["project"], got["done"], got["scope"]) == ("Wood and tools", p["id"], False, {"namespaces": ["pods"]})
    assert c.get(f"/api/v1/chats/{plain}", headers=h).json()["title_by"] == "assistant"
    listed = {x["id"]: x for x in c.get("/api/v1/chats", headers=h).json()}
    assert listed[tid]["project"] == p["id"] and listed[p["coordinator"]]["project"] == p["id"]

    # done threads stay listed; the coordinator is listed apart
    assert c.patch(f"/api/v1/projects/{p['id']}/threads/{tid}", headers=h, json={"done": True}).status_code == 200
    got = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()
    assert {t["id"]: t["done"] for t in got["thread_list"]} == {tid: True, plain: False}
    assert (got["threads"], got["open"]) == (2, 1)
    every = c.get(f"/api/v1/projects/{p['id']}/threads", headers=h).json()
    assert {t["id"] for t in every if t["coordinator"]} == {p["coordinator"]}

    # one of your conversations can join; a thread taken out stays a conversation
    other = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    assert c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"chat": other}).json()["id"] == other
    assert c.get(f"/api/v1/chats/{other}", headers=h).json()["scope"] == {"namespaces": ["pods"]}
    assert c.delete(f"/api/v1/projects/{p['id']}/threads/{other}", headers=h).status_code == 200
    assert c.get(f"/api/v1/chats/{other}", headers=h).json()["project"] is None
    assert c.delete(f"/api/v1/projects/{p['id']}/threads/{p['coordinator']}", headers=h).status_code == 400

    # a deleted coordinator is made again
    c.delete(f"/api/v1/chats/{p['coordinator']}", headers=h)
    again = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["coordinator"]
    assert again != p["coordinator"] and c.get(f"/api/v1/chats/{again}", headers=h).json()["project"] == p["id"]

    # archived projects are listed apart
    c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"archived": True})
    assert c.get("/api/v1/projects", headers=h).json() == []
    assert [x["id"] for x in c.get("/api/v1/projects?archived=true", headers=h).json()] == [p["id"]]

    # deleting it keeps its conversations, as ordinary ones
    assert c.delete(f"/api/v1/projects/{p['id']}", headers=h).status_code == 200
    kept = c.get(f"/api/v1/chats/{tid}", headers=h).json()
    assert (kept["project"], kept["done"]) == (None, False)
    assert c.get(f"/api/v1/projects/{p['id']}", headers=h).status_code == 404


def test_projects_are_their_owners_and_draw_on_readable_namespaces(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    # a namespace you can't read isn't there; a project needs one at least
    assert c.post("/api/v1/projects", headers=h, json={"name": "x", "namespaces": ["calls"]}).status_code == 404
    assert c.post("/api/v1/projects", headers=h, json={"name": "x", "namespaces": []}).status_code == 422
    assert c.post("/api/v1/projects", headers=h, json={"name": "   ", "namespaces": ["pods"]}).status_code == 400
    p = _new(c, h)
    # someone else's project isn't there for anyone else, not even to start a thread in
    assert cv.get(f"/api/v1/projects/{p['id']}", headers=hv).status_code == 404
    assert cv.post(f"/api/v1/projects/{p['id']}/threads", headers=hv).status_code == 404
    assert cv.get("/api/v1/projects", headers=hv).json() == []
    # nor can someone else's conversation be brought in
    theirs = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    assert c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"chat": theirs}).status_code == 404


def test_changing_its_namespaces_moves_its_conversations(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    p = _new(ca, ha)
    tid = ca.post(f"/api/v1/projects/{p['id']}/threads", headers=ha).json()["id"]
    got = ca.patch(f"/api/v1/projects/{p['id']}", headers=ha, json={"namespaces": ["calls", "pods"], "name": "Treehouse"}).json()
    assert got["namespaces"] == ["calls", "pods"]
    for cid in (tid, p["coordinator"]):
        assert ca.get(f"/api/v1/chats/{cid}", headers=ha).json()["scope"] == {"namespaces": ["calls", "pods"]}
    assert ca.get(f"/api/v1/chats/{p['coordinator']}", headers=ha).json()["title"] == "Treehouse"


def test_every_answer_in_a_project_reads_what_it_is_for(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h, instructions="Keep it cheap.")
    tid = c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"title": "Wood and tools"}).json()["id"]

    llm.tool_script = [{"content": "Pine is cheapest."}]
    ev = sse(c.post(f"/api/v1/chats/{tid}/messages", headers=h, json={"content": "Which wood?"}).text)
    assert "done" in ev
    system = _system(llm)
    assert "one of its threads, in the project “Treehouse with Sam”" in system
    assert "Build a treehouse with my son by summer." in system and "Keep it cheap." in system
    assert "The project's threads" not in system

    # the coordinator is told about the threads, as data
    llm.tool_script = [{"content": "One thread so far."}]
    sse(c.post(f"/api/v1/chats/{p['coordinator']}/messages", headers=h, json={"content": "Where are we?"}).text)
    system = _system(llm)
    assert "its coordinator: the conversation about the project as a whole" in system
    assert f"- thread {tid}: Wood and tools (last: Pine is cheapest.)" in system
    assert f"<{untrusted.TAG}-" in system.split("The project's threads")[1]

    # a conversation outside any project is told nothing of it
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, chat.create(db, 1))
    assert box.project is None and "project" not in box.system_note()
    assert projects.for_chat(db, tid)["id"] == p["id"]


def test_agents_use_projects_over_mcp(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    read = _bearer(_grant(c, h, scope="read")[1])
    hw = _bearer(_grant(c, h, scope="write")[1])
    assert "read-only" in _error(c, read, "create_project", {"name": "Treehouse", "namespaces": ["pods"]})
    made = _ok(c, hw, "create_project", {"name": "Treehouse", "goal": "Build it by summer.", "namespaces": ["pods"]})
    pid = made["project_id"]
    assert (made["goal"], made["namespaces"], made["threads"], made["url"]) == ("Build it by summer.", ["pods"], 0, f"{WEB}/projects/{pid}")
    assert made["coordinator"]["url"] == f"{WEB}/chat/{made['coordinator']['chat_id']}"
    assert "not found" in _error(c, hw, "create_project", {"name": "x", "namespaces": ["calls"]})

    got = tool(c, hw, "update_project", project_id=pid, new_thread="Wood and tools", instructions="Keep it cheap.")
    tid = got["started"]["chat_id"]
    assert got["instructions"] == "Keep it cheap." and [t["chat_id"] for t in got["thread_list"]] == [tid]
    got = tool(c, hw, "update_project", project_id=pid, thread_id=tid, done=True)
    assert got["thread_list"][0]["done"] is True and got["open"] == 0
    assert "give done" in tool_error(c, hw, "update_project", project_id=pid, thread_id=tid)

    listed = tool(c, read, "list_projects")["projects"]
    assert [x["project_id"] for x in listed] == [pid] and "thread_list" not in listed[0]
    assert tool(c, read, "read_project", project_id=pid)["thread_list"][0]["title"] == "Wood and tools"
    cv, hv = s.cl["viewer"]
    theirs = _bearer(_grant(cv, hv, scope="read")[1])
    assert tool(cv, theirs, "list_projects")["projects"] == []
    assert "not found" in tool_error(cv, theirs, "read_project", project_id=pid)
