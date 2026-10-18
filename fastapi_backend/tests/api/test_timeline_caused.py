"""What an item caused (docs/timeline.md): the runs it started of the automations the person manages and the
approvals those asked for, with Approve and Decline on the waiting ones, from the timeline, over MCP and for the
assistant; an approval.waiting item lists its own approval."""

from __future__ import annotations

import json

from app.domain import ai_tools, approvals, store
from tests.api.test_mcp import tool, tool_error
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"
LISTS = {
    "name": "Shopping lists",
    "triggers": [{"as": "imp", "on": "event", "kinds": ["import.received"]}],
    "graph": {
        "nodes": [
            {"id": "i", "type": "input", "config": {}},
            {"id": "me", "type": "action", "config": {"action": "notify", "params": {"to": "me", "text": "A list came in"}}},
        ],
        "edges": [{"source": "i", "target": "me"}],
    },
}


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    me = make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "cal@x.io", "viewer password 1", roles={"calls": "viewer"})
    h = login(client, "me@x.io", "my password 1")
    r = client.post("/api/v1/automations", headers=h, json=LISTS)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    pods1 = f"create-recording-{ids[0]}"
    # a run it started on the first episode, waiting on its action, as the engine would have stored it
    run = db.next_id("automation_run")
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("automation_run", run),
        d={
            "automation": aid,
            "name": "Shopping lists",
            "version": 1,
            "trigger": "imp",
            "event": pods1,
            "event_ids": [pods1],
            "status": "waiting",
            "started_at": store.now(),
            "counts": {"done": 0, "waiting": 1, "scheduled": 0, "failed": 0},
            "space": store.ns_id(db, "pods"),
            "cause_depth": 1,
        },
    )
    event = client.get(f"{URL}/{pods1}", headers=h).json()
    intent = {
        "key": f"{run}-me",
        "action": "notify",
        "params": {"to": "gateway:family", "text": "A list came in"},
        "effect": "out",
        "risk": "ask",
        "untrusted": True,
        "spaces": [store.ns_id(db, "pods")],
        "run_as": me,
        "node": "me",
        "automation": aid,
        "run": run,
        "when": None,
        "status": "waiting",
    }
    ap = approvals.ask(db, cfg, intent, event)
    return {"ids": ids, "pods1": pods1, "me": me, "h": h, "automation": aid, "run": run, "approval": ap}


def test_an_item_lists_what_it_caused_and_the_waiting_approval_is_decided_there(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    h, pods1, ap = env["h"], env["pods1"], env["approval"]
    got = client.get(f"{URL}/{pods1}/caused", headers=h).json()
    assert [r["id"] for r in got["runs"]] == [env["run"]]
    assert got["runs"][0]["automation"] == env["automation"] and got["runs"][0]["status"] == "waiting"
    (a,) = got["approvals"]
    assert a["id"] == ap and a["status"] == "pending" and a["why"]["activity"] == pods1 and a["can"]["approve"] and a["can"]["decline"]
    assert a["summary"].startswith("Post") or a["summary"]  # what it would do, in words
    # the approvals list takes the activity too; another item caused nothing
    assert [x["id"] for x in client.get("/api/v1/approvals", headers=h, params={"activity": pods1}).json()] == [ap]
    assert client.get(f"{URL}/create-recording-{env['ids'][1]}/caused", headers=h).json() == {"runs": [], "approvals": []}
    # the approval's own line on the timeline lists it, so it is decided from there
    waiting = client.get(f"{URL}/ask-approval-{ap}/caused", headers=h).json()
    assert [x["id"] for x in waiting["approvals"]] == [ap] and waiting["runs"] == []
    # another reader of the namespace manages no automation and was asked nothing; a stranger can't see the item
    hv = login(client, "vi@x.io", "viewer password 1")
    assert client.get(f"{URL}/{pods1}/caused", headers=hv).json() == {"runs": [], "approvals": []}
    hc = login(client, "cal@x.io", "viewer password 1")
    assert client.get(f"{URL}/{pods1}/caused", headers=hc).status_code == 404
    # declined where it was caused: the same approval endpoint, then the item says so
    assert client.post(f"/api/v1/approvals/{ap}", headers=h, json={"decision": "decline"}).json()["status"] == "declined"
    (a,) = client.get(f"{URL}/{pods1}/caused", headers=h).json()["approvals"]
    assert a["status"] == "declined" and not a["can"]["approve"]


def test_agents_read_what_an_item_caused_too(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    h, pods1, ap = env["h"], env["pods1"], env["approval"]
    got = tool(client, h, "activity_caused", id=pods1)
    assert [r["id"] for r in got["runs"]] == [env["run"]]
    assert [a["id"] for a in got["approvals"]] == [ap] and got["approvals"][0]["why"]["activity"] == pods1
    assert got["approvals"][0]["can"] == {"approve": False, "decline": True, "edit": False, "always": False}
    assert [a["id"] for a in tool(client, h, "approvals", activity=pods1)["approvals"]] == [ap]
    assert tool(client, h, "approvals", activity="create-recording-999")["approvals"] == []
    assert "no activity" in tool_error(client, h, "activity_caused", id="create-recording-999")
    # the assistant's approvals take the activity too
    box = ai_tools.Toolbox(db, cfg, {"id": env["me"], "email": "me@x.io"}, {store.ns_id(db, "pods")}, set(), {}, None)
    out, said = box.call("approvals", {"activity": pods1})
    assert said == "Read 1 approval" and json.loads(out)["approvals"][0]["id"] == ap
    out, said = box.call("approvals", {"activity": "create-recording-999"})
    assert said == "Read 0 approvals"
