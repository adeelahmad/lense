"""Saved views of the timeline (docs/timeline.md, Saved views): the filters and order under a name, each person's
own, through the API and MCP."""

from __future__ import annotations

import json

from tests.api.test_mcp import rpc, tool, tool_error
from tests.helpers import login, make_user

URL = "/api/v1/activities/views"


def save(client, h, **args):
    """save_timeline_view's answer, or {"error": …}; the shared helper can't pass on a `name` argument."""
    res = rpc(client, h, "tools/call", {"name": "save_timeline_view", "arguments": args})["result"]
    return json.loads(res["content"][0]["text"])


WORK = {"kinds": ["email.received"], "namespace": ["pods"], "unreviewed": True, "order": "for_you"}


def test_timeline_views_are_saved_renamed_changed_and_deleted_by_their_maker_alone(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hr, hv = login(client, "root@x.io", "root password 1"), login(client, "vi@x.io", "viewer password 1")
    assert client.get(URL, headers=hr).json() == []
    v = client.post(URL, json={"name": "Work mail", "state": WORK}, headers=hr).json()
    assert v["name"] == "Work mail" and v["state"]["kinds"] == ["email.received"] and v["state"]["order"] == "for_you"
    assert v["state"]["q"] == "" and v["state"]["from_day"] is None  # what isn't given is unset
    # a second name of the same, any case, is refused; a bad day too
    assert client.post(URL, json={"name": "work MAIL"}, headers=hr).status_code == 400
    bad = client.post(URL, json={"name": "Bad", "state": {"from_day": "yesterday"}}, headers=hr)
    assert bad.status_code == 422
    # the listing is the maker's alone
    assert [x["name"] for x in client.get(URL, headers=hr).json()] == ["Work mail"]
    assert client.get(URL, headers=hv).json() == []
    assert client.patch(f"{URL}/{v['id']}", json={"name": "Mine now"}, headers=hv).status_code == 404
    assert client.delete(f"{URL}/{v['id']}", headers=hv).status_code == 404
    # rename, then save new filters into it
    got = client.patch(f"{URL}/{v['id']}", json={"name": "Work"}, headers=hr).json()
    assert got["name"] == "Work" and got["state"]["kinds"] == ["email.received"]
    got = client.patch(f"{URL}/{v['id']}", json={"state": {"q": "invoice"}}, headers=hr).json()
    assert got["name"] == "Work" and got["state"]["q"] == "invoice" and got["state"]["kinds"] == []
    # the item route isn't confused with views
    assert client.get("/api/v1/activities/create-recording-999", headers=hr).status_code == 404
    assert client.delete(f"{URL}/{v['id']}", headers=hr).json() == {"ok": True}
    assert client.get(URL, headers=hr).json() == []
    assert client.delete(f"{URL}/{v['id']}", headers=hr).status_code == 404
    # library views and saved searches don't show here, nor these there
    client.post("/api/v1/views", json={"name": "Work"}, headers=hr)
    assert client.get(URL, headers=hr).json() == []


def test_agents_list_save_and_delete_timeline_views(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    hr = login(client, "root@x.io", "root password 1")
    assert tool(client, hr, "timeline_views") == {"views": []}
    v = save(client, hr, name="Sensors", state={"kinds": ["sensor.changed"]})
    assert v["name"] == "Sensors" and v["state"]["kinds"] == ["sensor.changed"]
    v = save(client, hr, id=v["id"], name="Sensor switches")
    assert v["name"] == "Sensor switches" and v["state"]["kinds"] == ["sensor.changed"]  # the filters stay
    assert [x["name"] for x in tool(client, hr, "timeline_views")["views"]] == ["Sensor switches"]
    assert "order" in save(client, hr, name="Odd", state={"order": "random"})["error"]
    assert "name" in save(client, hr, state={})["error"]
    assert tool(client, hr, "delete_timeline_view", id=v["id"]) == {"ok": True}
    assert "not found" in tool_error(client, hr, "delete_timeline_view", id=v["id"])
