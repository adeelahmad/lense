"""Auto scaling through agents (docs/scaling.md, "Agents"): the assistant proposes groups that wait for the person's
yes in the web app and acts at once within approved terms; MCP clients do the same, and never approve."""

from __future__ import annotations

import json

import pytest

from app.domain import scaling, settings
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.api.test_scaling import fake  # noqa: F401 - the fake provider
from tests.helpers import login, make_user

GROUP = {"name": "Workers", "min": 0, "max": 3, "budget": 20}


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def provider(c, h):
    body = {"name": "Fake", "driver": "fake", "settings": {"region": "eu"}, "secrets": {"api_key": "good"}}
    return c.post("/api/v1/scaling/providers", headers=h, json=body).json()["id"]


def test_the_assistant_proposes_and_the_person_approves_in_the_web_app(app, db, cfg, folder, new_client, llm, fake):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    pid = provider(c, h)
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "scaling", {})]},
        {"content": "", "tool_calls": [call(2, "propose_scaling_group", {"group": {**GROUP, "provider": pid}})]},
        {"content": "I proposed a group of up to 3 workers."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "scale workers on the fake cloud"}).text)
    seen = llm.seen[-2]["messages"][3]["content"]
    read = json.loads(seen[seen.index("{") : seen.rindex("}") + 1])
    assert read["enabled"] is False and read["providers"][0]["name"] == "Fake" and "good" not in seen
    appr = ev["approval"][0]
    assert appr["summary"] == "Make the scaling group Workers on Fake: 0 to 3 node units, up to $20 a month"
    assert scaling.groups(db) == []
    out = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["group"]["approved"] is True  # the person's yes in the web app approves its terms
    gid = out["group"]["id"]

    # within the approved terms the assistant acts at once; widening them asks again
    llm.tool_script = [
        {"content": "", "tool_calls": [call(3, "set_scaling_desired", {"group_id": gid, "desired": 2})]},
        {"content": "", "tool_calls": [call(4, "change_scaling_group", {"group_id": gid, "changes": {"max": 10}})]},
        {"content": "Asked for 2 now; raising the max waits for you."},
    ]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "two now, and allow ten"}).text)
    assert scaling.get_group(db, gid)["desired"] == 2
    assert [a["summary"] for a in ev["approval"]] == [
        "Change the scaling group Workers (max): on Fake: 0 to 10 node units, up to $20 a month"
    ]
    assert scaling.get_group(db, gid)["max"] == 3


def test_a_setup_conversation_makes_a_group_that_still_waits_for_the_person(app, db, cfg, folder, new_client, llm, fake):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    pid = provider(c, h)
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "propose_scaling_group", {"group": {**GROUP, "provider": pid}})]},
        {"content": "", "tool_calls": [call(2, "change_settings", {"section": "scaling", "changes": {"enabled": True}})]},
        {"content": "Made it; approve its terms in Settings."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "set up scaling"}).text)
    (g,) = scaling.groups(db)
    assert g["approved"] is False and "approval" in g["waiting_for"]  # made at once, as the assistant, so not approved
    assert [a["summary"] for a in ev["approval"]] == ["Set scaling: enabled = True"]  # turning scaling on always asks
    assert settings.effective(db, cfg)["scaling"]["enabled"] is False


def test_the_assistant_asks_to_let_a_stray_go_and_the_person_says_yes(app, db, cfg, folder, new_client, llm, fake):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    pid = provider(c, h)
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    c.post("/api/v1/scaling/groups", headers=h, json={**GROUP, "provider": pid})
    fake.machines["lens-old-7"] = "running"
    c.post("/api/v1/scaling/tick", headers=h)
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "let_go_scaling_stray", {"provider_id": pid, "stray_id": "lens-old-7"})]},
        {"content": "Asked you to let it go."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]  # even where it acts, it asks
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "stop the stray"}).text)
    (appr,) = ev["approval"]
    assert appr["summary"] == "Let lens-old-7 go at Fake" and fake.machines["lens-old-7"] == "running"
    out = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["provider"]["strays"] == [] and "lens-old-7" not in fake.machines


def test_only_admins_get_the_scaling_tools(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    llm.tool_script = [{"content": "Hi."}]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "hi"}).text)
    offered = {t["function"]["name"] for t in llm.seen[-1].get("tools") or []}
    assert not offered & {"scaling", *scaling.TOOLS}


def test_mcp_clients_manage_scaling_but_never_approve(client, db, cfg, fake):  # noqa: F811
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    pid = provider(client, h)
    _, tokens = _grant(client, h, scope="read write")
    m = _bearer(tokens)
    assert tool(client, m, "scaling")["providers"][0]["secrets"] == {"api_key": True}
    out = tool(client, m, "propose_scaling_group", group={**GROUP, "provider": pid})
    g = out["group"]
    assert g["approved"] is False and out["approve"].endswith("/settings/scaling")
    assert "budget" in tool_error(client, m, "propose_scaling_group", group={**GROUP, "provider": pid, "budget": None})
    # the person approves in the web app; then the agent works within the terms
    assert client.post(f"/api/v1/scaling/groups/{g['id']}/approve", headers=h).json()["approved"] is True
    assert tool(client, m, "set_scaling_desired", group_id=g["id"], desired=1)["group"]["desired"] == 1
    assert "from 0 to 3" in tool_error(client, m, "set_scaling_desired", group_id=g["id"], desired=9)
    assert tool(client, m, "pause_scaling_group", group_id=g["id"], why="night")["group"]["paused_why"] == "night"
    assert tool(client, m, "change_scaling_group", group_id=g["id"], changes={"max": 5})["group"]["approved"] is False

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    _, et = _grant(client, login(client, "ed@x.io", "editor password 1"), scope="read write")
    assert "only admins" in tool_error(client, _bearer(et), "scaling")
