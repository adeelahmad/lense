"""Mutes on the timeline (docs/timeline.md, Muting): each person mutes a source, a person or a kind for good or until
a time; the timeline leaves their activities out for them alone, as it leaves out what they hid; agents mute over MCP
and in the assistant, where it waits for the person's yes after outside text."""

from __future__ import annotations

import json

from app.domain import ai_tools, auth, event_mutes, store
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"
ASSETS = {"kinds": "asset.added"}


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    for rid in ids[:2]:  # the two episodes were brought in by Vi, the call by Lens itself
        db.q("UPDATE $r SET created_by = $who", r=store.R("recording", rid), who=vi)
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "root": root,
        "vi": vi,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def _ids(page):
    return [a["id"] for a in page["items"]]


def test_a_mute_hides_an_actor_or_a_kind_for_the_person_alone(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h, vi = env["hr"], f"lens:account/{env['vi']}"
    assert client.get(f"{URL}/mutes", headers=h).json() == {"mutes": []}
    got = client.post(f"{URL}/mute", json={"actor": vi, "name": "Vi"}, headers=h).json()["mutes"]
    assert len(got) == 1 and got[0]["actor"] == vi and got[0]["name"] == "Vi" and got[0]["until"] is None and got[0]["at"]
    # left out, with or without the page's hidden=false; shown by hidden=true, as what was hid is
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == [call]
    assert _ids(client.get(URL, params={**ASSETS, "hidden": "false"}, headers=h).json()) == [call]
    assert _ids(client.get(URL, params={**ASSETS, "hidden": "true"}, headers=h).json()) == [pods2, pods1]
    # with the other mark filters too, and only for this person
    assert _ids(client.get(URL, params={**ASSETS, "reviewed": "false"}, headers=h).json()) == [call]
    assert _ids(client.get(URL, params=ASSETS, headers=env["hv"]).json()) == [pods2, pods1]
    # lifted
    assert client.post(f"{URL}/mute", json={"actor": vi, "on": False}, headers=h).json() == {"mutes": []}
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == [call, pods2, pods1]
    # a kind, with the kinds under it: the three are import.received, under asset.added
    got = client.post(f"{URL}/mute", json={"kind": "import.received"}, headers=h).json()["mutes"]
    assert got[0]["kind"] == "import.received" and got[0]["name"] == "Import received"
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == []
    assert _ids(client.get(URL, params={**ASSETS, "hidden": "true"}, headers=h).json()) == [call, pods2, pods1]
    client.post(f"{URL}/mute", json={"kind": "import.received", "on": False}, headers=h)
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == [call, pods2, pods1]
    client.post(f"{URL}/mute", json={"kind": "asset.added"}, headers=h)
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == []
    assert _ids(client.get(URL, params={"kinds": "import.received"}, headers=h).json()) == []
    assert [m["kind"] for m in client.get(f"{URL}/mutes", headers=h).json()["mutes"]] == ["asset.added"]
    client.post(f"{URL}/mute", json={"kind": "asset.added", "on": False}, headers=h)
    # snoozed until a time: listed with it, gone once it has passed
    got = client.post(f"{URL}/mute", json={"actor": vi, "until": "2099-01-01T00:00:00Z"}, headers=h).json()["mutes"]
    assert got[0]["until"] == "2099-01-01T00:00:00.000Z"
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == [call]
    db.q("UPDATE event_mute SET until = '2020-01-01T00:00:00.000Z' WHERE account = $a", a=env["root"])
    assert client.get(f"{URL}/mutes", headers=h).json() == {"mutes": []}
    assert _ids(client.get(URL, params=ASSETS, headers=h).json()) == [call, pods2, pods1]
    # bad asks
    assert client.post(f"{URL}/mute", json={"actor": vi, "kind": "asset.added"}, headers=h).status_code == 400
    assert client.post(f"{URL}/mute", json={}, headers=h).status_code == 400
    assert client.post(f"{URL}/mute", json={"kind": "no.such"}, headers=h).status_code == 400
    assert client.post(f"{URL}/mute", json={"actor": vi, "until": "2020-01-01T00:00:00Z"}, headers=h).status_code == 400
    assert client.post(f"{URL}/mute", json={"actor": vi, "until": "soon"}, headers=h).status_code == 400
    assert client.post(f"{URL}/mute", json={"actor": vi}).status_code == 401
    # a person removed takes their mutes with them
    client.post(f"{URL}/mute", json={"actor": vi}, headers=env["hv"])
    assert len(event_mutes.of(db, env["vi"])) == 1
    auth.remove_account(db, env["vi"])
    assert db.rows("SELECT * FROM event_mute") == []


def test_agents_mute_too_and_the_assistant_asks_first_after_outside_text(client, db, cfg, folder):
    from tests.api.test_mcp import rpc, tool, tool_error

    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h, vi = env["hr"], f"lens:account/{env['vi']}"
    assert tool(client, h, "activity_mutes") == {"mutes": []}
    res = rpc(client, h, "tools/call", {"name": "mute_activities", "arguments": {"actor": vi, "name": "Vi"}})["result"]
    assert res["isError"] is False and res["structuredContent"]["mutes"][0]["actor"] == vi
    assert [a["id"] for a in tool(client, h, "timeline", kinds=["asset.added"])["items"]] == [call]
    assert [a["id"] for a in tool(client, h, "timeline", kinds=["asset.added"], hidden=True)["items"]] == [pods2, pods1]
    assert tool(client, h, "mute_activities", actor=vi, on=False) == {"mutes": []}
    assert "one of the two" in tool_error(client, h, "mute_activities")
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=h).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert tool(client, ro, "activity_mutes") == {"mutes": []}
    assert "read-only" in tool_error(client, ro, "mute_activities", actor=vi)
    # the assistant: at once, until it has read text from outside Lens, then it asks first
    spaces = {store.ns_id(db, "pods"), store.ns_id(db, "calls")}
    box = ai_tools.Toolbox(db, cfg, {"id": env["root"], "email": "root@x.io"}, spaces, set(), {}, None, admin=True)
    assert "mute_activities" in {s["function"]["name"] for s in box.specs()}
    out, said = box.call("mute_activities", {"actor": vi, "name": "Vi"})
    assert said == "Muted Vi" and json.loads(out)["mutes"][0]["actor"] == vi
    assert [a["id"] for a in json.loads(box.call("timeline", {"kinds": ["asset.added"]})[0])["activities"]] == [call]
    out, said = box.call("mute_activities", {"actor": vi, "on": False})
    assert said == "Lifted the mute on Vi"
    assert box.wary()
    out, said = box.call("mute_activities", {"actor": vi, "name": "Vi"})
    assert json.loads(out)["status"] == "waiting for the person's approval" and said.startswith("Asked for approval: Mute Vi")
    assert event_mutes.of(db, env["root"]) == []
    aid = json.loads(out)["approval"]
    assert ai_tools.approve(db, cfg, aid, {"id": env["root"], "email": "root@x.io"}, set()) == {"status": "done", "mutes": 1}
    assert event_mutes.of(db, env["root"])[0]["name"] == "Vi"
