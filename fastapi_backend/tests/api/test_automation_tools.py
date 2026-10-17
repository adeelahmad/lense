"""Automations for agents (docs/automations.md, Agents): over MCP an agent lists, saves, tries and runs the person's
automations, with problems said in plain words; in the assistant saving is made at once and switched off, and switching
one on or running it waits for the person's approval. Neither lets a node run on its own."""

from __future__ import annotations

import json

from app.domain import ai_tools, auth, automations, ingest, store
from tests.helpers import login, make_user


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


SAY = {
    "nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "New: {{ event.object.name }}"})],
    "edges": [{"source": "i", "target": "a"}],
}
NEW = [{"as": "new", "on": "event", "kinds": ["asset.added"]}]


def tool(client, h, tool_name, **args):
    from tests.api.test_mcp import rpc

    res = rpc(client, h, "tools/call", {"name": tool_name, "arguments": args})["result"]
    assert res["isError"] is False, res
    return res["structuredContent"]


def tool_error(client, h, tool_name, **args):
    from tests.api.test_mcp import rpc

    res = rpc(client, h, "tools/call", {"name": tool_name, "arguments": args})["result"]
    assert res["isError"] is True, res
    return json.loads(res["content"][0]["text"])["error"]


def _env(client, new_client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "pods": store.ns_id(db, "pods"),
        "ed": auth.find_account(db, "ed@x.io")["id"],
        "vi": auth.find_account(db, "vi@x.io")["id"],
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(new_client(), "vi@x.io", "viewer password 1"),
    }


def test_agents_save_try_and_run_automations_over_mcp(client, new_client, db, cfg):
    env = _env(client, new_client, db)
    he = env["he"]
    assert "log_line" in {a["name"] for a in tool(client, he, "automation_actions")["actions"]}
    # saved switched off, unless the agent says otherwise; a body that doesn't fit is said in plain words
    got = tool(client, he, "save_automation", name="Say new", triggers=NEW, graph=SAY)
    aid = got["automation_id"]
    assert got["enabled"] is False and got["namespaces"] == ["pods"]
    assert "trigger" in tool_error(client, he, "save_automation", name="Bad", triggers=[{"as": "x", "on": "nope"}], graph=SAY)
    assert "doesn't fit" in tool_error(client, he, "save_automation", name="Bad", graph=SAY)
    assert "isn't JSON" in tool_error(client, he, "save_automation", name="Bad", triggers="[{", graph=SAY)
    assert "say what changes" in tool_error(client, he, "save_automation", automation_id=aid)
    # tried, saved or as a draft, keeping nothing
    t = tool(client, he, "test_automation", automation_id=aid, kind="asset.added")
    assert t["status"] == "done" and t["intents"][0]["result"]["line"].startswith("New: ")
    draft = {"name": "Never", "triggers": NEW, "graph": SAY, "if": {"all": [{"path": "event.kind", "op": "equals", "value": "x"}]}}
    assert tool(client, he, "test_automation", draft=json.dumps(draft))["status"] == "skipped"
    assert "give automation_id or draft" in tool_error(client, he, "test_automation")
    assert db.rows("SELECT id FROM automation_run") == []
    # changed and switched on; then run now on an activity, and read back
    got = tool(client, he, "save_automation", automation_id=aid, condition={"all": [{"path": "event.space", "op": "exists"}]}, enabled=True)
    assert got["enabled"] is True and automations.get(db, aid)["if"]["all"][0]["op"] == "exists"
    rec = ingest.import_text(db, cfg, "pods", "Sam: Hello.", title="Courier")
    ran = tool(client, he, "run_automation", automation_id=aid, activity=f"create-recording-{rec}")
    assert ran["status"] == "done" and ran["run"]
    full = tool(client, he, "automations", automation_id=aid)
    assert full["if"]["all"][0]["op"] == "exists" and full["graph"]["nodes"][1]["config"]["action"] == "log_line"
    assert [r["id"] for r in full["runs"]] == [ran["run"]]
    run = tool(client, he, "automations", run_id=ran["run"])
    assert run["intents"][0]["result"]["line"] == "New: Courier" and run["event"] == f"create-recording-{rec}"
    [listed] = tool(client, he, "automations")["automations"]
    assert listed["automation_id"] == aid and listed["last_status"] == "done" and listed["runs"] == 1
    # the If dropped with {}
    tool(client, he, "save_automation", automation_id=aid, condition={})
    assert automations.get(db, aid)["if"] is None
    # someone who doesn't manage it sees none of it
    hv = env["hv"]
    assert tool(client, hv, "automations")["automations"] == []
    assert "not found" in tool_error(client, hv, "run_automation", automation_id=aid)
    assert "not found" in tool_error(client, hv, "automations", run_id=ran["run"])
    assert "can't edit any namespace" in tool_error(client, hv, "save_automation", name="Mine", triggers=NEW, graph=SAY)


def test_the_assistant_saves_at_once_and_asks_to_switch_on_or_run(client, new_client, db, cfg):
    env = _env(client, new_client, db)
    pods = {env["pods"]}
    me = {"id": env["ed"], "email": "ed@x.io"}
    box = ai_tools.Toolbox(db, cfg, me, pods, pods, {}, None)
    names = {s["function"]["name"] for s in box.specs()}
    assert {"automations", "test_automation", "save_automation", "run_automation"} <= names

    out, summary = box.call("save_automation", {"name": "Say new", "triggers": NEW, "graph": SAY, "enabled": True})
    got = json.loads(out)
    assert "automation_id" in got, out
    aid = got["automation_id"]
    assert summary == "Saved automation Say new, switched off" and got["enabled"] is False
    assert got["switching_on"]["status"] == "waiting for the person's approval"
    assert box.approvals[-1]["summary"] == "Switch on automation Say new: when asset.added happens, it does log_line"
    assert automations.get(db, aid)["enabled"] is False
    assert ai_tools.approve(db, cfg, box.approvals[-1]["id"], me, pods) == {"status": "done", "automation": aid, "enabled": True}
    assert automations.get(db, aid)["enabled"] is True

    out, summary = box.call("test_automation", {"automation_id": aid})
    assert json.loads(out)["status"] == "done" and summary == "Tried it: done"
    assert "no kind" in json.loads(box.call("test_automation", {"automation_id": aid, "kind": "no.such"})[0])["error"]

    rec = ingest.import_text(db, cfg, "pods", "Sam: Hello.", title="Courier")
    out, summary = box.call("run_automation", {"automation_id": aid, "activity": f"create-recording-{rec}"})
    assert json.loads(out)["status"] == "waiting for the person's approval" and summary.startswith("Asked for approval: Run automation")
    assert db.rows("SELECT id FROM automation_run") == []
    done = ai_tools.approve(db, cfg, box.approvals[-1]["id"], me, pods)
    assert done["status"] == "done" and done["run"] and automations.get_run(db, done["run"])["status"] == "done"

    listed = json.loads(box.call("automations", {})[0])["automations"]
    assert [a["automation_id"] for a in listed] == [aid] and listed[0]["last_status"] == "done"
    full = json.loads(box.call("automations", {"automation_id": aid})[0])
    assert full["graph"] == SAY and [r["id"] for r in full["runs"]] == [done["run"]]
    # switching off is made at once
    out, _ = box.call("save_automation", {"automation_id": aid, "enabled": False})
    assert json.loads(out)["enabled"] is False and automations.get(db, aid)["enabled"] is False

    # a viewer's assistant can't save or run them, and doesn't see this one
    vbox = ai_tools.Toolbox(db, cfg, {"id": env["vi"], "email": "vi@x.io"}, pods, set(), {}, None)
    vnames = {s["function"]["name"] for s in vbox.specs()}
    assert "automations" in vnames and "save_automation" not in vnames and "run_automation" not in vnames
    assert json.loads(vbox.call("automations", {})[0])["automations"] == []
    assert "no automation" in json.loads(vbox.call("automations", {"automation_id": aid})[0])["error"]
