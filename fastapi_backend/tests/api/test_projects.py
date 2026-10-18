"""Projects (docs/projects.md): a goal, the namespaces it draws on, a coordinator conversation and threads; every
answer in one reads what the project is for; agents use them over MCP."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.domain import ai_tools, chat, projects, timers, untrusted
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


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def now(monkeypatch):
    """A brief answered where it's asked, not on a thread of its own, so the test sees it end."""
    monkeypatch.setattr(projects, "launch", lambda db, cfg, cid, qid: projects.work(db, cfg, cid, qid))


def _thread(c, h, cid):
    return c.get(f"/api/v1/chats/{cid}", headers=h).json()


def test_the_coordinator_starts_threads_with_a_brief(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h, instructions="Keep it cheap.")
    brief = "Find the cheapest wood for a small treehouse platform."
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "start_thread", {"title": "Wood", "brief": brief})]},
        {"content": "Pine is cheapest."},  # the thread's answer, written while the coordinator waits
        {"content": "I started a thread on wood."},
    ]
    ev = sse(c.post(f"/api/v1/chats/{p['coordinator']}/messages", headers=h, json={"content": "Let's plan the build"}).text)
    assert [x["tool"] for x in ev["step"]] == ["start_thread"] and ev["step"][0]["summary"] == "Started the thread “Wood”"
    assert "approval" not in ev
    got = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["thread_list"]
    assert [(t["title"], t["answering"], t["messages"]) for t in got] == [("Wood", False, 2)]
    t = _thread(c, h, got[0]["id"])
    q, a = t["messages"]
    assert (q["role"], q["content"], a["content"], a["meta"]["outcome"]) == ("user", brief, "Pine is cheapest.", "direct")
    # the thread read the project, as a thread; the brief isn't counted as the person's own message
    thread_system = next(m["content"] for m in llm.seen[1]["messages"] if m["role"] == "system")
    assert "one of its threads, in the project “Treehouse with Sam”" in thread_system and "Keep it cheap." in thread_system
    assert db.one("SELECT origin FROM $r", r=projects.R("chat_message", q["id"]))["origin"] == {"project": p["id"], "by": "coordinator"}

    # the coordinator reads threads and marks them done; a thread can't start threads
    box = ai_tools.Toolbox(
        db, cfg, {"id": t["account"], "email": "ed@x.io"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, p["coordinator"]
    )
    listed = json.loads(box.call("project_threads", {})[0])["threads"]
    assert [(x["thread_id"], x["last"]["text"]) for x in listed] == [(t["id"], "Pine is cheapest.")]
    read = json.loads(box.call("read_thread", {"thread_id": t["id"]})[0])
    assert [m["text"] for m in read["messages"]] == [brief, "Pine is cheapest."]
    assert json.loads(box.call("finish_thread", {"thread_id": t["id"]})[0])["done"] is True
    assert "no thread" in json.loads(box.call("read_thread", {"thread_id": 999999})[0])["error"]
    inside = ai_tools.Toolbox(db, cfg, {"id": t["account"], "email": "ed@x.io"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, t["id"])
    assert "coordinator" in json.loads(inside.call("start_thread", {"title": "x", "brief": "y"})[0])["error"]
    # a conversation outside a project has none of these tools
    plain = ai_tools.Toolbox(db, cfg, {"id": t["account"], "email": "ed@x.io"}, {s.pods}, {s.pods}, {}, chat.create(db, t["account"]))
    assert not {"project_threads", "start_thread"} & {x["function"]["name"] for x in plain.specs()}


def test_starting_threads_after_reading_outside_text_asks_first(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h)
    uid = c.get(f"/api/v1/chats/{p['coordinator']}", headers=h).json()["account"]
    box = ai_tools.Toolbox(
        db, cfg, {"id": uid, "email": "ed@x.io"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, p["coordinator"], outside=["page"]
    )
    out = json.loads(box.call("start_thread", {"title": "Wood", "brief": "Price pine."})[0])
    assert out["status"] == "waiting for the person's approval" and projects.threads(db, projects.get(db, p["id"], uid)) == []
    llm.tool_script = [{"content": "Pine is $3 a metre."}]
    assert c.post(f"/api/v1/approvals/{out['approval']}", headers=h, json={"decision": "approve"}).status_code == 200
    got = projects.threads(db, projects.get(db, p["id"], uid))
    assert [(t["title"], t["last"]["text"]) for t in got] == [("Wood", "Pine is $3 a metre.")]
    # one answer starts a few threads at most
    box = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {s.pods}, {s.pods}, {"namespaces": ["pods"]}, p["coordinator"])
    box.started = projects.STARTS_PER_ANSWER
    assert "at most" in json.loads(box.call("start_thread", {"title": "More", "brief": "More."})[0])["error"]


def test_a_brief_from_the_api_or_an_agent_is_answered_in_the_background(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h)
    ca, ha = s.cl["admin"]
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"auto_title": True}).status_code == 200
    llm.tool_script = [{"content": "Start with the platform."}]
    llm.title = "Where to start"
    tid = c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"brief": "Where do we start?"}).json()["id"]
    t = _thread(c, h, tid)
    assert [m["content"] for m in t["messages"]] == ["Where do we start?", "Start with the platform."]
    assert (t["title"], t["title_by"]) == ("Where to start", "assistant")
    assert db.one("SELECT origin FROM $r", r=projects.R("chat_message", t["messages"][0]["id"]))["origin"] is None  # the person's own
    # a brief over the limit, or into a thread still answering, is refused
    assert c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"brief": "x" * 4001}).status_code == 422
    db.q("UPDATE $r SET answering = true", r=projects.R("chat", tid))
    assert c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"chat": tid, "brief": "And then?"}).status_code == 400

    hw = _bearer(_grant(c, h, scope="write")[1])
    llm.tool_script = [{"content": "Buy pine."}]
    got = _ok(c, hw, "update_project", {"project_id": p["id"], "new_thread": "Wood", "brief": "What wood?"})
    assert _thread(c, h, got["started"]["chat_id"])["messages"][-1]["content"] == "Buy pine."
    assert "new_thread" in _error(c, hw, "update_project", {"project_id": p["id"], "brief": "What wood?"})


def test_a_brief_cut_off_by_a_stop_is_marked_by_the_sweep(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h)
    monkeypatch.setattr(projects, "launch", lambda db, cfg, cid, qid: None)  # the process stopped before it answered
    tid = c.post(f"/api/v1/projects/{p['id']}/threads", headers=h, json={"title": "Wood", "brief": "What wood?"}).json()["id"]
    assert _thread(c, h, tid)["messages"][-1]["role"] == "user"
    projects.sweep(db, cfg)  # not yet: it may still be working
    assert len(_thread(c, h, tid)["messages"]) == 1
    db.q("UPDATE $r SET working.beat = '2020-01-01T00:00:00+00:00'", r=projects.R("chat", tid))
    projects.sweep(db, cfg)
    last = _thread(c, h, tid)["messages"][-1]
    assert (last["role"], last["error"]) == ("assistant", projects.STOPPED)
    row = db.one("SELECT answering, working FROM $r", r=projects.R("chat", tid))
    assert row["answering"] is False and row.get("working") is None


def test_a_schedule_asks_its_prompt_in_a_new_thread_or_the_same_one(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h)
    url = f"/api/v1/projects/{p['id']}/schedules"
    assert c.post(url, headers=h, json={"prompt": "What's next?", "cron": "every day"}).status_code == 400
    assert c.post(url, headers=h, json={"prompt": "What's next?", "cron": "0 8 * * *", "timezone": "Mars/Base"}).status_code == 400
    daily = c.post(url, headers=h, json={"prompt": "What's next?", "cron": "0 8 * * *", "timezone": "Europe/London"}).json()
    assert (daily["id"], daily["when"], daily["thread"], daily["on"]) == ("s1", "every day at 08:00", "new", True)
    first = dt.datetime.fromisoformat(daily["next_at"])

    # each time in a new thread, asked as a brief and answered in the background
    llm.tool_script = [{"content": "Order the wood."}]
    assert timers.due(db, cfg, now=first) == 1
    got = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()
    [t] = got["thread_list"]
    assert [m["content"] for m in _thread(c, h, t["id"])["messages"]] == ["What's next?", "Order the wood."]
    assert got["schedules"][0]["last"]["ok"] is True and dt.datetime.fromisoformat(got["schedules"][0]["next_at"]) > first
    llm.tool_script = [{"content": "Cut it."}]
    timers.due(db, cfg, now=first + dt.timedelta(days=1))
    assert len(c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["thread_list"]) == 2

    # always the same thread; one still answering is left to finish
    c.patch(f"{url}/s1", headers=h, json={"thread": "same"})
    llm.tool_script = [{"content": "Paint it."}]
    timers.due(db, cfg, now=first + dt.timedelta(days=2))
    sc = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["schedules"][0]
    assert sc["chat"] is not None and _thread(c, h, sc["chat"])["messages"][-1]["content"] == "Paint it."
    db.q("UPDATE $r SET answering = true", r=projects.R("chat", sc["chat"]))
    timers.due(db, cfg, now=first + dt.timedelta(days=3))
    sc = c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["schedules"][0]
    assert sc["last"]["ok"] is False and "still answering" in sc["last"]["error"]
    assert len(_thread(c, h, sc["chat"])["messages"]) == 2


def test_schedules_wait_while_off_or_archived_and_agents_set_them(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    p = _new(c, h)
    url = f"/api/v1/projects/{p['id']}/schedules"
    sid = c.post(url, headers=h, json={"prompt": "Weekly check-in", "cron": "0 9 * * 1"}).json()["id"]
    key = projects._key(p["id"], sid)
    assert c.patch(f"{url}/{sid}", headers=h, json={"on": False}).json()["next_at"] is None
    assert timers.get(db, key)["status"] == "cancelled"
    assert c.patch(f"{url}/{sid}", headers=h, json={"on": True}).json()["next_at"] is not None
    c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"archived": True})
    assert c.get(f"/api/v1/projects/{p['id']}", headers=h).json()["schedules"][0]["next_at"] is None
    c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"archived": False})
    assert timers.get(db, key)["status"] == "waiting"
    assert c.patch(f"{url}/s9", headers=h, json={"on": False}).status_code == 404
    # only its owner sees or changes it
    cv, hv = s.cl["viewer"]
    assert cv.post(url, headers=hv, json={"prompt": "x", "cron": "@daily"}).status_code == 404

    hw = _bearer(_grant(c, h, scope="write")[1])
    got = _ok(c, hw, "schedule_project", {"project_id": p["id"], "prompt": "Daily summary", "cron": "@daily", "thread": "same"})
    assert [x["when"] for x in got["schedules"]] == ["every Monday at 09:00", "every day at 00:00"]
    assert "prompt and cron" in _error(c, hw, "schedule_project", {"project_id": p["id"], "prompt": "x"})
    got = _ok(c, hw, "schedule_project", {"project_id": p["id"], "schedule_id": got["schedule_id"], "remove": True})
    assert [x["schedule_id"] for x in got["schedules"]] == [sid]
    c.delete(f"/api/v1/projects/{p['id']}", headers=h)
    assert timers.get(db, key)["status"] == "cancelled"
