"""The stream of activities over the API, MCP and the assistant (docs/automations.md, docs/api.md): new assets as
asset.added newest first, filters, one activity, the kinds; each person sees only their namespaces; and a follower
in two processes handles each new asset once."""

from __future__ import annotations

import json
import time

from app.domain import ai_tools, events, ingest, store
from tests.helpers import login, make_user, quiet, seed

URL = "/api/v1/activities"


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ca@x.io", "calls password 1", roles={"calls": "viewer"})
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "hc": login(client, "ca@x.io", "calls password 1"),
    }


def test_the_timeline_lists_new_assets_newest_first(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    assert client.get(URL).status_code == 401
    page = client.get(URL, headers=env["hr"]).json()
    assert [a["id"] for a in page["items"]] == [call, pods2, pods1] and page["next"] is None
    first = page["items"][0]
    assert first["kind"] == "asset.added" and first["type"] == "Create" and first["actor"] == events.LENS
    assert first["object"]["id"] == "lens:recording/" + call.split("-")[-1] and first["lens"]["untrusted"] == ["object.name"]
    assert first["summary"].startswith("Lens added “") and sorted(page["namespaces"].values()) == ["calls", "pods"]
    # each person sees their namespaces' alone
    assert [a["id"] for a in client.get(URL, headers=env["hv"]).json()["items"]] == [pods2, pods1]
    mine = client.get(URL, headers=env["hc"]).json()
    assert [a["id"] for a in mine["items"]] == [call] and list(mine["namespaces"].values()) == ["calls"]
    # a page at a time
    one = client.get(URL, params={"limit": 1}, headers=env["hr"]).json()
    two = client.get(URL, params={"limit": 1, "before": one["next"]}, headers=env["hr"]).json()
    assert [a["id"] for a in one["items"] + two["items"]] == [call, pods2] and two["next"]
    # filters
    assert [a["id"] for a in client.get(URL, params={"namespace": "pods"}, headers=env["hr"]).json()["items"]] == [pods2, pods1]
    assert client.get(URL, params={"kinds": "asset.added"}, headers=env["hc"]).json()["items"][0]["id"] == call
    title = first["object"]["name"]
    where = json.dumps({"all": [{"path": "object.name", "op": "equals", "value": title}]})
    assert [a["id"] for a in client.get(URL, params={"where": where}, headers=env["hr"]).json()["items"]] == [call]
    assert client.get(URL, params={"q": title.split()[0]}, headers=env["hr"]).json()["items"][0]["id"] == call
    assert client.get(URL, params={"where": "{nope"}, headers=env["hr"]).status_code == 400
    r = client.get(URL, params={"kinds": "email.nope"}, headers=env["hr"])
    assert r.status_code == 400 and "no kind email.nope" in r.json()["detail"]
    assert client.get(URL, params={"namespace": "nowhere"}, headers=env["hr"]).status_code == 404
    assert client.get(URL, params={"before": "nope"}, headers=env["hr"]).status_code == 400
    assert client.get(URL, params={"limit": 201}, headers=env["hr"]).status_code == 422
    # one activity; one of another namespace is not found
    assert client.get(f"{URL}/{call}", headers=env["hc"]).json()["summary"] == first["summary"]
    assert client.get(f"{URL}/{call}", headers=env["hv"]).status_code == 404
    assert client.get(f"{URL}/create-recording-999", headers=env["hr"]).status_code == 404
    # the kinds
    kinds = client.get(f"{URL}/kinds", headers=env["hv"]).json()
    added = next(k for k in kinds if k["name"] == "asset.added")
    assert added["source"] == "recordings" and added["schema"]["properties"]["object"]["properties"]["name"]["title"] == "Title"
    assert added["sample"]["kind"] == "asset.added"


def test_agents_read_the_timeline_too(client, db, cfg, folder):
    from tests.api.test_mcp import tool, tool_error

    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    got = tool(client, env["hv"], "timeline")
    assert got == client.get(URL, params={"limit": 20}, headers=env["hv"]).json()  # the same as the API
    assert [a["id"] for a in got["items"]] == [pods2, pods1]
    assert [a["id"] for a in tool(client, env["hr"], "timeline", namespace="calls", kinds=["asset.added"])["items"]] == [call]
    assert "any needs at least one condition" in tool_error(client, env["hr"], "timeline", where='{"any": []}')
    assert any(k["name"] == "asset.added" for k in tool(client, env["hv"], "event_kinds")["kinds"])
    # the assistant's tool: only the namespaces of the conversation
    pods = {store.ns_id(db, "pods")}
    box = ai_tools.Toolbox(db, cfg, {"id": 99, "email": "v"}, pods, set(), {}, None)
    assert "timeline" in {s["function"]["name"] for s in box.specs()} and box.untrusted("timeline")
    out, summary = box.call("timeline", {})
    assert [a["id"] for a in json.loads(out)["activities"]] == [pods2, pods1] and summary == "Read 2 activities from the timeline"
    assert "isn't in this conversation" in json.loads(box.call("timeline", {"namespace": "calls"})[0])["error"]


def test_a_follower_in_two_processes_handles_each_new_asset_once(db, cfg, folder):
    events._seen.clear()
    events._claimed.clear()
    seed(db, cfg, folder)
    assert events.follow(db, cfg, "test.uploads", kinds=["asset.added"])[0] == []  # starts at now: nothing before
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)
    new = []
    for n in range(3):
        p = folder / f"new{n}.txt"
        p.write_text(f"[0:00] Sam: new asset number {n}\n")
        new.append(f"create-recording-{ingest.import_transcript(db, cfg, 'pods', p, log=quiet)}")
    handled = []

    def look():  # the API's follower, or the worker's
        acts, commit = events.follow(db, cfg, "test.uploads", kinds=["asset.added"])
        handled.extend(a["id"] for a in acts if events.claim(db, "test.uploads", a["id"]))
        commit()

    look()
    events._seen.clear()  # the other process: nothing seen or claimed in its memory
    events._claimed.clear()
    look()
    look()
    assert sorted(handled) == sorted(new)
