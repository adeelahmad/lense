"""Deep research (research.py, docs/assistant.md#deep-research): a goal gets a plan with follow-up questions, which
waits for the person's approval (in the web app, the inbox or a chat room; an API key or an app only declines it unless
research.agents_skip_review); approved, it runs in stages, claimed by one process at a time, within its tool calls,
minutes and budgets, and stops when asked; its report comes back checked against its sources, and becomes a note or a
podcast episode."""

from __future__ import annotations

import json

import pytest

from app.api.v1.routes import podcasts as podcast_routes
from app.domain import activity, ai_tools, bridge, budgets, chat, research, store
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_web_search import _Site, site  # noqa: F401 - the fake web site, for a plan that looks on the web

GOAL = "What happened with the capsid work?"
REPORT = "# Capsid research\n\nThe capsid model beat the human designs [1]. Samples ship on Friday [2]. Mars has capsids too [2]."


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def now(monkeypatch):
    """A run carried out where it's launched, not in a thread of its own, so the test sees it end."""
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: research.run(db, cfg, rid))


@pytest.fixture
def launched(monkeypatch):
    """The runs launched, none of them carried out."""
    got: list[int] = []
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: got.append(rid))
    return got


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def archive():
    """The model's turns for the default plan's two sub-questions: one search of the archive each, then a finding."""
    return [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "capsid model"})]},
        {"content": "The capsid model beat the human designs [1]."},
        {"content": "", "tool_calls": [call(2, "search_transcripts", {"query": "exploit benchmark"})]},
        {"content": "The exploit benchmark measures working exploits [1]."},
    ]


def new_chat(c, h):
    return c.post("/api/v1/chats", headers=h, json={}).json()["id"]


def plan(c, h, cid, goal=GOAL, **body):
    r = c.post(f"/api/v1/chats/{cid}/research", headers=h, json={"goal": goal, **body})
    assert r.status_code == 200, r.text
    return r.json()["research"]


def approve(c, h, res, **body):
    return c.post(f"/api/v1/approvals/{res['approval']}", headers=h, json={"decision": "approve", **body})


def status(c, h, res):
    return c.get(f"/api/v1/research/{res['id']}", headers=h).json()


def token(c, h):
    return {"Authorization": f"Bearer {c.post('/api/v1/tokens', headers=h, json={'name': 'agent', 'scope': 'write'}).json()['token']}"}


def mcp(c, h, name, **args):
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["isError"] is False, res
    return res["structuredContent"]


def live(app):
    """The settings the app's requests read (a copy of cfg, kept until a setting is saved)."""
    return app.state.settings.current()


def set_up(s, section, **values):
    """An admin saves settings, as in the web app."""
    ca, ha = s.cl["admin"]
    r = ca.put(f"/api/v1/settings/{section}", headers=ha, json=values)
    assert r.status_code == 200, r.text


def schema(body):
    return ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}


def me(db, email="ed@x.io"):
    return {**db.one("SELECT record::id(id) AS id, email FROM account WHERE email = $e", e=email), "admin": False}


def test_a_goal_gets_a_plan_that_waits_for_approval_and_can_be_revised(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    r = c.post(f"/api/v1/chats/{cid}/research", headers=h, json={"goal": GOAL})
    assert r.status_code == 200, r.text
    got = r.json()
    res = got["research"]
    assert (res["status"], res["stage"], res["goal"]) == ("planned", None, GOAL)
    assert res["plan"] == {
        "title": "Capsid research",
        "goal": GOAL,
        "sub_questions": ["What did the capsid model do?", "When do the samples ship?"],
        "sources": ["archive"],
        "max_tool_calls": 6,
        "report_outline": ["Findings", "Open questions"],
    }
    assert res["questions"] == [{"question": "Which period?", "answers": ["This year", "All time"]}]
    # what it will likely cost, labelled an estimate (no past runs yet: its tokens, and the fake model has no price)
    est = res["estimate"]
    assert est["estimate"] is True and est["tool_calls"] == 6 and est["usd"] is None and "fake has no price" in est["basis"]
    assert res["progress"] == {
        "sub_questions": 2,
        "done": 0,
        "sources": 0,
        "tool_calls": 0,
        "cost_usd": 0.0,
        "tokens": 0,
        "cost_estimated": False,
        "minutes": 0.0,
    }
    # the planner was told the sources this person has, and the most tool calls a plan may make
    asked = next(b for b in llm.seen if {"questions", "plan"} <= set(schema(b).get("properties", {})))
    assert "only from: archive, graph, notes" in asked["messages"][0]["content"] and "at most 40" in asked["messages"][0]["content"]
    assert asked["messages"][-1]["content"] == f"Goal: {GOAL}"
    # the conversation: the goal as a question asked for research, then the plan as the answer it waits on
    view = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert view["title"] == GOAL
    q, a = view["messages"]
    assert q["content"] == GOAL and q["meta"]["via"] == "web" and q["meta"]["options"]["research"] is True
    assert a["id"] == got["message"] and a["kind"] == "research_plan" and a["content"] == got["text"]
    assert a["meta"]["research"] == res["id"] and a["meta"]["outcome"] == "answered"
    assert "1. What did the capsid model do?" in got["text"] and "- Which period? (This year / All time)" in got["text"]
    # it waits as an approval of the assistant's: the person approves it (as it is or edited) or declines it
    ap = c.get(f"/api/v1/approvals/{res['approval']}", headers=h).json()
    assert ap["tool"] == "research" and ap["status"] == "pending" and ap["chat"] == cid
    assert ap["summary"] == "Research: Capsid research (2 sub-questions, in the archive, up to 6 tool calls)"
    assert ap["can"] == {"approve": True, "decline": True, "edit": True, "always": False}
    assert ap["estimate"]["tool_calls"] == 6
    assert [x["id"] for x in c.get(f"/api/v1/research?chat_id={cid}", headers=h).json()] == [res["id"]]
    # revised: the answers and the edits go back for one more planning call, and the plan and its approval change
    before = len(llm.seen)
    edits = {"sub_questions": ["What did the capsid model do?"], "max_tool_calls": 3}
    r = c.post(f"/api/v1/research/{res['id']}/revise", headers=h, json={"answers": {"Which period?": "This year"}, "plan": edits})
    assert r.status_code == 200, r.text
    rev = r.json()
    sent = llm.seen[before]["messages"][-1]["content"]
    assert "Sub-questions:\n- What did the capsid model do?\nSources: archive\nTool calls: 3" in sent
    assert sent.endswith("Their answers:\n- Which period?: This year")
    assert rev["plan"]["title"] == "Capsid research (revised)" and rev["questions"] == [] and rev["revisions"] == 1
    assert rev["answers"] == {"Which period?": "This year"} and rev["status"] == "planned"
    a = db.one("SELECT args, summary FROM $r", r=store.R("approval", res["approval"]))
    assert a["args"]["plan"]["title"] == "Capsid research (revised)" and a["summary"].startswith("Research: Capsid research (revised)")
    # both planning calls count for the research, and the conversation
    assert activity.totals(db, f"research:{res['id']}")["by_kind"]["out"]["calls"] == 2
    assert activity.totals(db, f"chat:{cid}")["by_kind"]["out"]["calls"] == 2
    # another person's research isn't there for them
    cv, hv = s.cl["viewer"]
    assert cv.get(f"/api/v1/research/{res['id']}", headers=hv).status_code == 404
    assert cv.post(f"/api/v1/research/{res['id']}/revise", headers=hv, json={}).status_code in (403, 404)


def test_a_question_marked_research_or_slash_research_streams_the_plan(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": f"/research {GOAL}"}).text)
    assert ev["research"][0]["status"] == "planned" and ev["research"][0]["goal"] == GOAL
    assert ev["approval"][0]["tool"] == "research" and ev["approval"][0]["id"] == ev["research"][0]["approval"]
    assert ev["token"][0]["text"].startswith("Here's a plan to research **Capsid research**")
    assert ev["meta"][0]["research"] == ev["research"][0]["id"] and "done" in ev
    msgs = chat.history(db, cid)
    assert msgs[0]["content"] == f"/research {GOAL}" and msgs[-1]["kind"] == "research_plan"
    # research: true does the same with the question as it is
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Capsid shipments", "research": True}).text)
    assert ev["research"][0]["goal"] == "Capsid shipments"
    # an ordinary question is answered as before
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What did Dyno say?"}).text)
    assert "research" not in ev and ev["token"]
    # with no model set up, research isn't offered
    live(app)["llm"]["base_url"] = None
    assert c.post(f"/api/v1/chats/{cid}/research", headers=h, json={"goal": GOAL}).status_code == 400


def test_an_approved_plan_runs_and_its_report_comes_back_checked(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    res = plan(c, h, cid)
    llm.tool_script = archive()
    r = approve(c, h, res)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["result"] == {"research": res["id"], "status": "queued"}
    got = status(c, h, res)
    assert (got["status"], got["stage"], got["ended"], got["error"]) == ("done", "done", None, None)
    assert got["progress"]["done"] == 2 and got["progress"]["tool_calls"] == 2 and got["progress"]["sources"] >= 2
    assert got["findings"] == [
        {"question": "What did the capsid model do?", "tool_calls": 1},
        {"question": "When do the samples ship?", "tool_calls": 1},
    ]
    assert got["started_at"] and got["finished_at"] and llm.tool_script == []
    # each sub-question was looked up with the archive's tools alone, within its share of the plan's tool calls
    rounds = [b for b in llm.seen if b.get("tools")]
    assert len(rounds) == 4
    assert {t["function"]["name"] for b in rounds for t in b["tools"]} <= set(research.SOURCE_TOOLS["archive"])
    assert "up to 3 tool calls" in rounds[0]["messages"][0]["content"]
    assert "Answer this part of it: What did the capsid model do?" in rounds[0]["messages"][-1]["content"]
    # the report was written from both findings, their citations numbered as one list
    writer = next(b for b in llm.seen if b["messages"][0]["content"].startswith("You write research reports"))
    told = writer["messages"][-1]["content"]
    assert "The capsid model beat the human designs [1]." in told and "The exploit benchmark measures working exploits [2]." in told
    assert "- Findings\n- Open questions" in told
    # checked: the first unsupported claim rewritten, the one the excerpts don't support at all marked
    view = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    report = view["messages"][-1]
    assert report["id"] == got["answer"] and report["kind"] == "research" and report["parent"] == res["plan_message"]
    assert report["content"] == REPORT + research.UNSUPPORTED
    assert [p["n"] for p in report["passages"]] == [1, 2]
    assert {p["recording_id"] for p in report["passages"]} <= {s.a, s.b}
    assert [x["tool"] for x in report["steps"]] == ["search_transcripts", "search_transcripts"]
    meta = report["meta"]
    assert meta["research"] == res["id"] and meta["outcome"] == "answered" and meta["tools"]["picked"] == ["archive"]
    assert meta["used"] == {"rounds": 2, "tool_calls": 2} and meta["limits"]["tool_calls"] == 6
    # every call it made counts for research:<id>: planning, four rounds, writing, checking, rewriting
    assert activity.totals(db, f"research:{res['id']}")["by_kind"]["out"]["calls"] == 8
    assert db.rows("SELECT * FROM research_run") == []  # its claim is let go
    # an agent follows it: the report, and its sources with links
    st = mcp(c, token(c, h), "research_status", id=res["id"])
    assert st["status"] == "done" and st["report"] == report["content"] and st["url"].endswith(f"/chat/{cid}")
    assert [x["n"] for x in st["sources"]] == [1, 2] and all(x["url"] for x in st["sources"])
    # the next plan's estimate is drawn from what this run cost per tool call
    nxt = plan(c, h, cid, "More about capsids")
    assert nxt["estimate"]["basis"] == "what each tool call of 1 past research run cost" and nxt["estimate"]["tool_calls"] == 6


def test_a_plan_that_looks_on_the_web_searches_without_asking_again(site, app, db, cfg, folder, new_client, llm, now):  # noqa: F811 - the site first: it widens the networks the app reads
    s = Assist(app, db, cfg, folder, new_client)
    set_up(s, "web", search="searxng", searxng_url=f"{site}/searx")
    cfg["web"].update(search="searxng", searxng_url=f"{site}/searx")
    llm.research_plan = {
        "title": "Harbour research",
        "goal": "When do boats come in?",
        "sub_questions": ["When do boats come in?"],
        "sources": ["archive", "web"],
        "max_tool_calls": 4,
        "report_outline": [],
    }
    llm.research_report = "# Harbour research\n\nBoats come in at dawn [1]."
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    res = plan(c, h, cid, "When do boats come in at the harbour?")
    assert (
        res["plan"]["sources"] == ["archive", "web"]
        and "the web" in c.get(f"/api/v1/approvals/{res['approval']}", headers=h).json()["summary"]
    )
    # the second search's words are the model's own, after it read the first's results: the plan approved the web,
    # so it searches without asking again
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "web_search", {"query": "harbour boats"})]},
        {"content": "", "tool_calls": [call(2, "web_search", {"query": "secret budget figures"})]},
        {"content": "Boats come in at dawn [1]."},
    ]
    assert approve(c, h, res).status_code == 200
    assert status(c, h, res)["status"] == "done"
    assert [g["query"]["q"] for g in _Site.got if g["path"] == "/searx/search"] == ["harbour boats", "secret budget figures"]
    assert db.rows("SELECT id FROM approval WHERE tool != 'research'") == []
    report = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert report["kind"] == "research" and report["passages"][0]["source"] == "web"
    assert report["passages"][0]["url"] == "https://www.harbour.example/news"
    # the web tools allowed for the run are the plan's alone
    box = research.toolbox(db, cfg, research.get(db, res["id"]), research.account_of(db, me(db)["id"]))
    assert box.allowed_out == {"web_search", "web_read"} and box.only == set(
        research.SOURCE_TOOLS["archive"] + research.SOURCE_TOOLS["web"]
    )


def test_who_may_approve_a_plan(app, db, cfg, folder, new_client, llm, launched):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ht = token(c, h)
    cid = new_chat(c, h)
    res = plan(c, h, cid)
    # an API key can only decline it, and can't start research without review
    assert c.get(f"/api/v1/approvals/{res['approval']}", headers=ht).json()["can"] == {
        "approve": False,
        "decline": True,
        "edit": False,
        "always": False,
    }
    r = approve(c, ht, res)
    assert r.status_code == 403 and "the person's own pick" in r.json()["detail"]
    assert c.post(f"/api/v1/chats/{cid}/research", headers=ht, json={"goal": GOAL, "review": False}).status_code == 403
    assert status(c, h, res)["status"] == "planned" and launched == []
    r = c.post(f"/api/v1/approvals/{res['approval']}", headers=ht, json={"decision": "decline"})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert status(c, h, res)["status"] == "declined" and approve(c, h, res).status_code == 400  # decided once
    # the person approves it edited, within the sources they have
    res = plan(c, h, cid)
    r = approve(c, h, res, params={"sources": ["web"]})
    assert r.status_code == 400 and r.json()["detail"] == "a plan looks in archive, graph, notes"
    assert status(c, h, res)["status"] == "planned"  # a bad edit doesn't decide it
    r = approve(c, h, res, params={"sub_questions": ["What did the capsid model do?"], "max_tool_calls": 2})
    assert r.status_code == 200, r.text
    got = status(c, h, res)
    assert got["status"] == "queued" and got["plan"]["sub_questions"] == ["What did the capsid model do?"]
    assert got["plan"]["max_tool_calls"] == 2 and launched == [res["id"]]
    # signed in, the person may skip the review; so may an agent once research.agents_skip_review lets it
    r = c.post(f"/api/v1/chats/{cid}/research", headers=h, json={"goal": GOAL, "review": False})
    assert r.status_code == 200 and r.json()["research"]["status"] == "queued"
    set_up(s, "research", agents_skip_review=True)
    res = plan(c, ht, cid)
    assert c.get(f"/api/v1/approvals/{res['approval']}", headers=ht).json()["can"]["approve"] is True
    assert approve(c, ht, res).status_code == 200 and status(c, h, res)["status"] == "queued"
    # a person has one run going at a time: the others wait their turn (none was running here, so each launched)
    assert len(launched) == 3


def test_a_plan_is_approved_with_a_yes_in_a_chat_room(app, db, cfg, folder, new_client, llm, launched):
    Assist(app, db, cfg, folder, new_client)
    account = me(db)
    cfg["bridge"] = {**cfg["bridge"], "users": ["alice"], "approve": "low_risk"}
    cid = chat.create(db, account["id"], kind="bridge")
    text, waiting = bridge.answer(db, cfg, cfg, account, cid, f"/research {GOAL}")
    assert text.startswith("Here's a plan to research **Capsid research**") and [a["tool"] for a in waiting] == ["research"]
    rid = db.one("SELECT record::id(id) AS id FROM research WHERE approval = $a", a=waiting[0]["id"])["id"]
    q = chat.history(db, cid)[0]
    assert q["content"] == f"/research {GOAL}" and q["meta"] == {"via": "room", "options": {"research": True}}
    # someone not let in to approve isn't heard; alice's yes approves it as it is
    assert bridge.decide_in_room(db, cfg, cfg, account, cid, {"username": "eve"}, "yes") is None
    assert bridge.decide_in_room(db, cfg, cfg, account, cid, {"username": "alice"}, "yes") == f"Done: {waiting[0]['summary']}"
    assert research.get(db, rid)["status"] == "queued" and launched == [rid]


def test_agents_start_research_over_mcp(app, db, cfg, folder, new_client, llm, launched):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ht = token(c, h)
    got = mcp(c, ht, "start_research", goal=GOAL)
    assert got["status"] == "planned" and got["approval"] and got["next"].startswith("waiting for the person to approve")
    assert got["url"].endswith(f"/chat/{got['chat_id']}") and got["questions"][0]["question"] == "Which period?"
    assert chat.get(db, got["chat_id"], me(db)["id"])["title"] == GOAL
    st = mcp(c, ht, "research_status", id=got["research"])
    assert st["status"] == "planned" and "report" not in st
    # declining is an agent's to do; approving isn't
    with pytest.raises(PermissionError):
        ai_tools.approve(db, cfg, got["approval"], me(db), {s.pods}, via="agent")
    # with research.agents_skip_review, it starts at once
    set_up(s, "research", agents_skip_review=True)
    again = mcp(c, ht, "start_research", goal="Capsid shipments", chat_id=got["chat_id"], tools=["archive"], max_tool_calls=3)
    assert again["status"] == "queued" and again["next"].startswith("running") and launched == [again["research"]]
    r = research.get(db, again["research"])
    assert r["plan"]["max_tool_calls"] == 3 and r["tools"] == ["archive"]


def test_the_assistant_plans_research_with_its_own_tool(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    groups = {g["name"]: g for g in c.get("/api/v1/chats/tools", headers=h).json()["groups"]}
    assert groups["research"]["available"] and [t["name"] for t in groups["research"]["tools"]] == ["start_research"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "start_research", {"goal": GOAL})]},
        {"content": "I planned it: approve it to start."},
    ]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Research the capsid work for me"}).text)
    assert ev["step"][0]["summary"].startswith("Planned Research: Capsid research (2 sub-questions")
    assert ev["approval"][0]["tool"] == "research"
    r = db.one("SELECT record::id(id) AS id, status, chat FROM research WHERE approval = $a", a=ev["approval"][0]["id"])
    assert r["status"] == "planned" and r["chat"] == cid
    assert activity.totals(db, f"research:{r['id']}")["by_kind"]["out"]["calls"] == 1  # its planning call
    # outside a conversation there's nothing to research in
    box = ai_tools.Toolbox(db, cfg, me(db), {s.pods}, {s.pods}, {}, None)
    assert "start_research" not in {t["function"]["name"] for t in box.specs()}
    # without a model that uses tools, the group says why
    live(app)["ai"]["tools"] = False
    groups = {g["name"]: g for g in c.get("/api/v1/chats/tools", headers=h).json()["groups"]}
    assert not groups["research"]["available"] and groups["research"]["setting"] == "ai.tools"
    cfg["llm"]["base_url"] = None
    assert research.unavailable(cfg) == ("no language model is set up", "llm.model")


def test_a_budget_holds_a_run_until_it_allows_it(app, db, cfg, folder, new_client, llm, now):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    res = plan(c, h, cid)
    budgets.put(db, "all", usd=0.5, on_over="ask")
    activity.record("out", "test.spend", db=db, cost_usd=1.0)
    llm.tool_script = archive()
    assert approve(c, h, res).status_code == 200
    got = status(c, h, res)
    assert got["status"] == "held" and got["held"]["budget"] == "all" and "spent" in got["held"]["why"]
    assert got["progress"]["done"] == 0 and len(llm.tool_script) == 4  # it asked nothing
    # the sweep leaves it while the budget is over, and starts it once the budget allows it
    assert research.sweep(db, cfg) == []
    budgets.put(db, "all", usd=100, on_over="ask")
    assert research.sweep(db, cfg) == [res["id"]]
    assert status(c, h, res)["status"] == "done" and status(c, h, res)["held"] is None
    # the person can let a held run go on anyway; only a held one
    budgets.put(db, "all", usd=0.5, on_over="ask")
    res = plan(c, h, cid)
    llm.tool_script = archive()
    approve(c, h, res)
    assert status(c, h, res)["status"] == "held"
    r = c.post(f"/api/v1/research/{res['id']}/resume", headers=h)
    assert r.status_code == 200, r.text
    assert status(c, h, res)["status"] == "done"
    assert c.post(f"/api/v1/research/{res['id']}/resume", headers=h).status_code == 409
    # a budget that skips stops the run with what it has
    budgets.put(db, "all", usd=0.5, on_over="skip")
    res = plan(c, h, cid)
    approve(c, h, res)
    got = status(c, h, res)
    assert (got["status"], got["ended"]) == ("stopped", "budget")
    last = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert last["id"] == got["answer"] and last["content"] == "# Capsid research\n\nStopped before it found anything."


def test_stop_ends_a_run_and_keeps_what_it_found(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    # one waiting its turn stops at once; a plan that isn't approved is declined, not stopped
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: None)
    res = plan(c, h, cid)
    approve(c, h, res)
    assert c.post(f"/api/v1/research/{res['id']}/stop", headers=h).json() == {"ok": True, "stopping": True}
    got = status(c, h, res)
    assert (got["status"], got["ended"], got["answer"]) == ("stopped", "stopped", None)
    assert c.post(f"/api/v1/research/{plan(c, h, cid)['id']}/stop", headers=h).json()["stopping"] is False
    # a running one stops after the step it's on: here, as the second sub-question's search comes back
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: research.run(db, cfg, rid))
    res = plan(c, h, cid)
    first, found, second, _ = archive()

    def stop_now(body):
        research.request_stop(db, cfg, res["id"])
        return second

    llm.tool_script = [first, found, stop_now, {"content": "never asked"}]
    approve(c, h, res)
    got = status(c, h, res)
    assert (got["status"], got["ended"], got["progress"]["done"]) == ("stopped", "stopped", 1)
    assert llm.tool_script == [{"content": "never asked"}]
    last = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert last["id"] == got["answer"] and last["kind"] == "research" and last["stopped"] is True
    assert last["content"] == (
        "# Capsid research\n\nStopped. What it found so far:\n\n## What did the capsid model do?\nThe capsid model beat the human designs [1]."
    )
    assert last["meta"]["outcome"] == "stopped" and [p["n"] for p in last["passages"]] == [1]
    assert c.post(f"/api/v1/research/{res['id']}/stop", headers=h).json()["stopping"] is False  # it's over


def test_a_run_whose_process_went_away_is_taken_over_where_it_left_off(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: None)
    res = plan(c, h, cid)
    approve(c, h, res)
    rid = res["id"]
    # one process holds a run at a time, kept by its heartbeat
    assert research.claim(db, rid, "a") and not research.claim(db, rid, "b")
    assert research.beat(db, rid, "a") and not research.beat(db, rid, "b")
    # process a answered the first sub-question, then went away
    box = ai_tools.Toolbox(db, cfg, me(db), {s.pods}, {s.pods}, {}, cid)
    box.call("search_transcripts", {"query": "capsid model"})
    finding = {
        "question": "What did the capsid model do?",
        "text": "The capsid model beat the human designs [1].",
        "passages": box.cited("[1]"),
        "tool_calls": 1,
        "steps": [],
    }
    db.q("UPDATE $r SET status = 'running', findings = [$f], used = {tool_calls: 1}", r=store.R("research", rid), f=finding)
    assert research.sweep(db, cfg) == []  # its claim is fresh: a process is on it
    db.q("UPDATE $r SET heartbeat_at = $t", r=store.R("research_run", rid), t=research._ago(research.STALE + 5))
    monkeypatch.setattr(research, "launch", lambda db, cfg, rid: research.run(db, cfg, rid))
    llm.tool_script = archive()[2:]
    assert research.sweep(db, cfg) == [rid]
    got = status(c, h, res)
    assert got["status"] == "done" and got["progress"]["done"] == 2 and got["progress"]["tool_calls"] == 2
    # only the second sub-question was looked up again
    rounds = [b for b in llm.seen if b.get("tools")]
    assert len(rounds) == 2 and "When do the samples ship?" in rounds[0]["messages"][-1]["content"]
    assert not research.beat(db, rid, "a") and research.run(db, cfg, rid) is False  # done: nothing to take


def test_a_report_becomes_a_note_and_a_podcast(app, db, cfg, folder, new_client, llm, now, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    res = plan(c, h, cid)
    assert c.post(f"/api/v1/research/{res['id']}/note", headers=h).status_code == 400  # no report yet
    llm.tool_script = archive()
    approve(c, h, res)
    r = c.post(f"/api/v1/research/{res['id']}/note", headers=h)
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["namespace"] == "pods"
    page = c.get(f"/api/v1/notes/{made['page']}", headers=h).json()
    passages = research.get(db, res["id"])["passages"]
    first = passages[0]
    assert page["title"] == "Capsid research" and page["author"] == "assistant"
    assert research.mention(first) == f"@[{first.get('title') or 'Asset'}](recording:{first['recording_id']})"
    assert f"The capsid model beat the human designs ({research.mention(first)})." in page["body"]
    assert page["body"].rstrip().endswith(f"@[Research: Capsid research](chat:{cid})")
    links = {x["target"]: x for x in page["links"]}
    assert links[f"chat:{cid}"]["name"] == GOAL and links[f"recording:{first['recording_id']}"]["name"]
    assert status(c, h, res)["notes"] == [made["page"]]
    # someone else who reads the note sees the conversation's link, but not its name
    cv, hv = s.cl["viewer"]
    seen = {x["target"]: x for x in cv.get(f"/api/v1/notes/{made['page']}", headers=hv).json()["links"]}
    assert seen[f"chat:{cid}"]["name"] is None and seen[f"recording:{first['recording_id']}"]["name"]
    # a podcast episode from the assets the report cites, about the goal, titled after the report
    asked = []

    def start(acl, user, db, cfg, body):
        asked.append(body)
        return {"episode": 99, "job": 1, "namespace": "podcasts", "placed": "podcasts"}

    monkeypatch.setattr(podcast_routes, "start", start)
    r = c.post(f"/api/v1/research/{res['id']}/podcast", headers=h, json={"length": 5})
    assert r.status_code == 202, r.text
    assert asked[0].selection.recordings == list(dict.fromkeys(p["recording_id"] for p in passages))
    assert (asked[0].prompt, asked[0].title, asked[0].length) == (GOAL, "Capsid research", 5)


def test_research_settings(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    for bad in ({"max_tool_calls": 0}, {"max_minutes": 241}, {"agents_skip_review": "yes"}, {"model": 3}, {"max_tool_calls": True}):
        assert ca.put("/api/v1/settings/research", headers=ha, json=bad).status_code == 400, bad
    r = ca.put("/api/v1/settings/research", headers=ha, json={"max_tool_calls": 12, "max_minutes": 5, "model": " fake-large "})
    assert r.status_code == 200, r.text
    got = ca.get("/api/v1/settings", headers=ha).json()["research"]["values"]
    assert got == {"max_tool_calls": 12, "max_minutes": 5, "model": "fake-large", "agents_skip_review": False}
    # a plan's tool calls stay within research.max_tool_calls
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    assert c.post(f"/api/v1/chats/{cid}/research", headers=h, json={"goal": GOAL, "max_tool_calls": 13}).status_code == 400
    assert research.clean_plan(cfg | {"research": got}, {"sub_questions": ["a"], "max_tool_calls": 99}, ["archive"])["max_tool_calls"] == 12
    assert ca.put("/api/v1/settings/research", headers=s.cl["editor"][1], json={"max_minutes": 9}).status_code == 403


def test_a_run_out_of_time_writes_its_report_with_what_it_found(app, db, cfg, folder, new_client, llm, now, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cid = new_chat(c, h)
    res = plan(c, h, cid)
    # out of time once the first sub-question is answered
    monkeypatch.setattr(research.Run, "out_of_time", lambda self: bool(research.get(self.db, self.rid).get("findings") or []))
    llm.tool_script = archive()
    approve(c, h, res)
    got = status(c, h, res)
    assert (got["status"], got["ended"], got["progress"]["done"]) == ("done", "timeout", 1)
    writer = next(b for b in llm.seen if b["messages"][0]["content"].startswith("You write research reports"))
    assert "ran out of time before every sub-question was looked into" in writer["messages"][-1]["content"]
    assert c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]["meta"]["outcome"] == "limit"
