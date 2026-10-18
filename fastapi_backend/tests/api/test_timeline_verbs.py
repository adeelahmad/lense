"""The Move and Update verbs on the timeline (docs/automations.md, Kinds): moving an asset to another namespace is
`asset.moved`, a Move by whoever moved it, seen where it is now; switching a sensor on or off is `sensor.changed`, an
Update with the status it had before, seen in the sensor's namespace, else by admins alone; a change that changes
nothing logs nothing."""

from __future__ import annotations

from app.domain import sensors, store
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "cal@x.io", "viewer password 1", roles={"calls": "viewer"})
    return {
        "ids": ids,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "hc": login(client, "cal@x.io", "viewer password 1"),
    }


def _items(client, h, **params):
    return client.get(URL, params=params, headers=h).json()["items"]


def test_moving_an_asset_is_a_move_seen_where_it_is_now(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1 = env["ids"][0]
    assert _items(client, env["hr"], kinds="asset.moved") == []
    r = client.post(f"/api/v1/resources/{pods1}/move", json={"namespace": "calls"}, headers=env["hr"])
    assert r.status_code == 200, r.text
    (a,) = _items(client, env["hr"], kinds="asset.moved")
    assert a["type"] == "Move" and a["kind"] == "asset.moved" and a["actor"]["name"] == "root"
    obj = a["object"]
    title = client.get(f"/api/v1/resources/{pods1}", headers=env["hr"]).json()["title"]
    assert obj["id"] == f"lens:recording/{pods1}" and obj["url"] == f"/resources/{pods1}" and obj["name"] == title
    assert (obj["from"], obj["to"], obj["media"]) == ("pods", "calls", "transcript")
    assert a["summary"] == f"root moved “{obj['name']}” to calls"
    assert a["lens"]["untrusted"] == ["object.name"]
    # seen by the readers of the namespace it is in now, not by the one it left
    assert [x["id"] for x in _items(client, env["hc"], kinds="asset.moved")] == [a["id"]]
    assert _items(client, env["hv"], kinds="asset.moved") == []
    # the kind is listed for agents, as a Move
    kind = next(k for k in client.get(f"{URL}/kinds", headers=env["hv"]).json() if k["name"] == "asset.moved")
    assert kind["type"] == "Move" and kind["schema"]["properties"]["object"]["properties"]["to"]["title"] == "To (namespace)"


def test_switching_a_sensor_is_an_update_with_what_it_was(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    sid, _ = sensors.create(db, cfg, "mqtt", "Kitchen", {"prefix": "zigbee2mqtt/kitchen"})
    assert client.patch(f"/api/v1/sensors/{sid}", json={"status": "paused"}, headers=env["hr"]).status_code == 200
    (a,) = _items(client, env["hr"], kinds="sensor.changed")
    assert a["type"] == "Update" and a["summary"] == "root paused Kitchen" and a["actor"]["name"] == "root"
    assert a["object"] == {
        "type": "Service",
        "id": f"lens:sensor/{sid}",
        "name": "Kitchen",
        "url": f"/sensors/{sid}",
        "status": "paused",
        "before": "active",
    }
    # without a namespace it is admins' alone
    assert a["lens"]["audience"] == "admins"
    assert _items(client, env["hv"], kinds="sensor.changed") == []
    # the same status again, or a rename, is not news
    client.patch(f"/api/v1/sensors/{sid}", json={"status": "paused"}, headers=env["hr"])
    client.patch(f"/api/v1/sensors/{sid}", json={"name": "Kitchen hub"}, headers=env["hr"])
    assert len(_items(client, env["hr"], kinds="sensor.changed")) == 1
    # in a namespace, its readers see it
    client.patch(f"/api/v1/sensors/{sid}", json={"space": store.ns_id(db, "pods")}, headers=env["hr"])
    client.patch(f"/api/v1/sensors/{sid}", json={"status": "active"}, headers=env["hr"])
    got = _items(client, env["hv"], kinds="sensor.changed")
    assert [x["summary"] for x in got] == ["root resumed Kitchen hub"]
    assert got[0]["object"]["before"] == "paused" and got[0]["lens"].get("audience", "space") == "space"
    assert len(_items(client, env["hr"], kinds="sensor.changed")) == 2
