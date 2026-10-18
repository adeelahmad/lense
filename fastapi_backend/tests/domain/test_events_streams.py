"""Sensors as activities (docs/automations.md, Kinds): the readings a sensor keeps as `sensor.reading`, read only for
the sensors a follower names; an alert or notable log pattern as `sensor.alert`, once per pattern per hour; a device
that reported for the first time as `sensor.joined`."""

from __future__ import annotations

import time

import pytest

from app.domain import events, sensor_patterns, sensors, store

R = store.R
ADMIN = events.viewer(1, None, admin=True)


@pytest.fixture(autouse=True)
def fresh():
    events._seen.clear()
    events._claimed.clear()
    yield


def _cfg_fn(cfg):
    return lambda: cfg


def _next_second():
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def _alerts(db):
    return db.rows("SELECT record::id(id) AS id, activity FROM event_log WHERE kind = 'sensor.alert' ORDER BY id")


def test_readings_a_sensor_keeps_are_sensor_reading_activities(db, cfg):
    pods = store.ns_id(db, "pods")
    it = sensors.Intake(db, _cfg_fn(cfg))
    kitchen, _ = sensors.create(db, cfg, "mqtt", "Kitchen sensor", {"prefix": "kitchen"}, space=pods)
    plug, _ = sensors.create(db, cfg, "mqtt", "Plug", {"prefix": "plug"}, handling_={"store": "changes"})
    quiet, _ = sensors.create(db, cfg, "mqtt", "Meter", {"prefix": "meter"}, handling_={"store": "summary"})
    assert events.follow(db, cfg, "test.readings", kinds=["sensor.reading"])[0] == []
    only_kitchen = {"sensor_readings": {"actors": [f"lens:sensor/{kitchen}"]}}
    assert events.follow(db, cfg, "test.kitchen", kinds=["sensor.reading"], hint=only_kitchen)[0] == []

    k, p, m = (sensors.get(db, s) for s in (kitchen, plug, quiet))
    it.put(k, "temperature", "21.5")
    for v in ("1", "1", "2"):
        it.put(p, "power", v)
    it.put(k, "air", '{"temperature": 22.1, "humidity": 40}')
    it.put(m, "watts", "300")
    it.put(k, "door", "Front door opened")
    it.flush()

    got, commit = events.follow(db, cfg, "test.readings", kinds=["sensor.reading"])
    commit()
    for a in got:
        events.check_activity(a)
    assert [(a["actor"]["name"], a["object"]["name"]) for a in got] == [
        ("Kitchen sensor", "temperature: 21.5"),
        ("Plug", "power: 1"),
        ("Plug", "power: 2"),  # changes only
        ("Kitchen sensor", 'air: {"temperature": 22.1, "humidity": 40}'),
        ("Kitchen sensor", "door: Front door opened"),
    ]  # the meter keeps a summary, so no readings
    first = got[0]
    assert first["kind"] == "sensor.reading" and first["type"] == "Update" and first["space"] == pods
    assert first["actor"] == {"type": "Service", "id": f"lens:sensor/{kitchen}", "name": "Kitchen sensor"}
    assert (
        first["object"]["stream"] == "temperature" and first["object"]["value"] == 21.5 and first["object"]["url"] == f"/sensors/{kitchen}"
    )
    assert first["summary"] == "Kitchen sensor temperature: 21.5" and first["id"].startswith("reading-")
    assert "object.text" in first["lens"]["untrusted"] and "audience" not in first["lens"]
    assert got[1]["space"] is None and got[1]["lens"]["audience"] == "admins"  # no namespace: admins only
    assert got[3]["object"]["fields"] == {"temperature": 22.1, "humidity": 40.0}
    assert events.follow(db, cfg, "test.readings", kinds=["sensor.reading"])[0] == []  # read once

    # a follower naming its sensors reads only theirs
    mine, _ = events.follow(db, cfg, "test.kitchen", kinds=["sensor.reading"], hint=only_kitchen)
    assert [a["object"]["stream"] for a in mine] == ["temperature", "air", "door"]

    # left out of the timeline unless asked for; then newest first, for those who may see them
    assert not [a for a in events.page(db, cfg, ADMIN)["items"] if a["kind"] == "sensor.reading"]
    page = events.page(db, cfg, ADMIN, flt={"kinds": ["sensor.reading"]})["items"]
    assert [a["id"] for a in page] == [a["id"] for a in reversed(got)]
    reader = events.viewer(2, [pods])
    assert [a["id"] for a in events.page(db, cfg, reader, flt={"kinds": ["sensor.reading"]})["items"]] == [
        got[4]["id"],
        got[3]["id"],
        got[0]["id"],
    ]
    only = events.page(db, cfg, ADMIN, flt={"kinds": ["sensor.reading"], "actor": f"lens:sensor/{plug}"})["items"]
    assert [a["object"]["name"] for a in only] == ["power: 2", "power: 1"]
    assert events.page(db, cfg, ADMIN, flt={"kinds": ["sensor.reading"], "actor": "lens:user/1"})["items"] == []
    two = events.page(db, cfg, ADMIN, flt={"kinds": ["sensor.reading"]}, limit=2)
    rest = events.page(db, cfg, ADMIN, before=two["next"], flt={"kinds": ["sensor.reading"]}, limit=10)
    assert [a["id"] for a in two["items"] + rest["items"]] == [a["id"] for a in page]

    assert events.get(db, cfg, got[0]["id"], ADMIN)["object"] == first["object"]
    assert events.get(db, cfg, got[1]["id"], reader) is None
    assert events.get(db, cfg, "reading-nothere", ADMIN) is None


def test_an_alert_pattern_is_a_sensor_alert_once_an_hour(db, cfg):
    it = sensors.Intake(db, _cfg_fn(cfg))
    sid, _ = sensors.create(db, cfg, "syslog", None, {"address": "192.168.1.1"})
    s = sensors.get(db, sid)
    line = "wlan0: authentication failed for 3c:22:fb:10:aa:{:02x}"
    it.put(s, "hostapd", line.format(1), at="2026-10-15T16:01:00.000000+00:00", level=4, log=True)
    it.flush()
    assert not _alerts(db)  # not labelled yet
    pid = sensor_patterns.list_patterns(db, sid)[0]["id"]
    sensor_patterns.update(db, pid, label="alert")
    it.checked = 0  # the intake looks for changes at most once a second

    for i in range(3):
        it.put(s, "hostapd", line.format(i + 2), at=f"2026-10-15T16:2{i}:00.000000+00:00", level=4, log=True)
    it.flush()
    it.put(s, "hostapd", line.format(9), at="2026-10-15T16:40:00.000000+00:00", level=4, log=True)
    it.flush()
    other = sensors.Intake(db, _cfg_fn(cfg))  # another process sees the same hour
    other.put(s, "hostapd", line.format(10), at="2026-10-15T16:50:00.000000+00:00", level=4, log=True)
    other.flush()
    [row] = _alerts(db)
    a = events.get(db, cfg, f"log-{row['id']}", ADMIN)
    events.check_activity(a)
    assert a["kind"] == "sensor.alert" and a["type"] == "Announce" and a["space"] is None and a["lens"]["audience"] == "admins"
    assert a["actor"] == {"type": "Service", "id": f"lens:sensor/{sid}", "name": "192.168.1.1"}
    assert a["object"]["pattern"] == "wlan<n>: authentication failed for <mac>" and a["object"]["label"] == "alert"
    assert a["object"]["lines"] == 3 and a["object"]["count"] == 4 and a["object"]["hour"] == "2026-10-15T16"
    assert a["object"]["example"] == line.format(2) and a["published"].startswith("2026-10-15T16:20:00")
    assert a["summary"] == "192.168.1.1 logged an alert: wlan<n>: authentication failed for <mac>"

    it.put(s, "hostapd", line.format(11), at="2026-10-15T17:05:00.000000+00:00", level=4, log=True)
    it.flush()
    assert len(_alerts(db)) == 2  # the next hour

    # notable says so; a paused sensor, or a label the model wasn't sure of, raises none
    sensor_patterns.update(db, pid, label="notable")
    it.checked = 0
    it.put(s, "hostapd", line.format(12), at="2026-10-15T18:00:00.000000+00:00", level=4, log=True)
    it.flush()
    assert events.get(db, cfg, f"log-{_alerts(db)[-1]['id']}", ADMIN)["summary"].startswith("192.168.1.1 logged something notable:")
    sensors.update(db, sid, status="paused")
    it.checked = 0
    it.put(sensors.get(db, sid), "hostapd", line.format(13), at="2026-10-15T19:00:00.000000+00:00", level=4, log=True)
    it.flush()
    sensors.update(db, sid, status="active")
    db.q("UPDATE $r SET label = 'alert', sure = false", r=R("sensor_pattern", pid))
    sensors._bump(db)
    it.checked = 0
    it.put(sensors.get(db, sid), "hostapd", line.format(14), at="2026-10-15T20:00:00.000000+00:00", level=4, log=True)
    it.flush()
    assert len(_alerts(db)) == 3


def test_a_device_reporting_first_is_sensor_joined(db, cfg):
    pods = store.ns_id(db, "pods")
    it = sensors.Intake(db, _cfg_fn(cfg))
    assert events.follow(db, cfg, "test.joined", kinds=["sensor.joined"])[0] == []
    _next_second()

    s = it.mqtt_sensor("zigbee2mqtt/kitchen/temperature")
    sensors.create(db, cfg, "mqtt", "By hand", {"prefix": "byhand"})  # added by a person: not news
    it.sensor("syslog", "syslog:10.0.0.9", "10.0.0.9", "10.0.0.9", {"address": "10.0.0.9"}, space=pods)
    got, commit = events.follow(db, cfg, "test.joined", kinds=["sensor.joined"])
    commit()
    for a in got:
        events.check_activity(a)
    assert [a["summary"] for a in got] == [
        f"A new MQTT device reported: {s['name']}",
        "A new syslog sender reported: 10.0.0.9",
    ]
    a = got[0]
    assert a["id"] == f"joined-sensor-{s['id']}" and a["type"] == "Join" and a["lens"]["audience"] == "admins"
    assert a["object"] == {
        "type": "Service",
        "id": f"lens:sensor/{s['id']}",
        "name": s["name"],
        "url": f"/sensors/{s['id']}",
        "sensor_type": "mqtt",
        "device": s["device"],
        "status": "new",
    }
    assert got[1]["space"] == pods
    assert events.follow(db, cfg, "test.joined", kinds=["sensor.joined"])[0] == []

    page = events.page(db, cfg, ADMIN, flt={"kinds": ["sensor.joined"]})["items"]
    assert [x["id"] for x in page] == [got[1]["id"], got[0]["id"]]
    assert [x["id"] for x in events.page(db, cfg, events.viewer(2, [pods]), flt={"kinds": ["sensor.joined"]})["items"]] == [got[1]["id"]]
    assert events.get(db, cfg, a["id"], ADMIN)["object"]["status"] == "new"
    assert events.get(db, cfg, a["id"], events.viewer(2, [pods])) is None
