"""Sensors: the MQTT hub and syslog listener taking readings in, stream sensors found as they report, webhooks, the
one list with storage sources in it, handling and suggestions, and retention as a routine action."""

from __future__ import annotations

import datetime as dt
import socket
import struct
import threading
import time

import pytest

from app.domain import mqtt, routines, sensors, sources, store, syslog
from tests.helpers import login, make_user

R = store.R


@pytest.fixture
def admin(client, db):
    make_user(db, "admin@example.com", "pw-admin-123", admin=True)
    return login(client, "admin@example.com", "pw-admin-123")


def _cfg_fn(cfg):
    return lambda: cfg


# ---------- a tiny MQTT client for the tests ----------
class Device:
    def __init__(self, port, client_id="dev", user=None, password=None, level=4):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.f = self.sock.makefile("rb")
        flags = 0x02 | (0x80 if user else 0) | (0x40 if password else 0)
        body = mqtt.string("MQTT") + bytes([level, flags]) + struct.pack("!H", 30) + mqtt.string(client_id)
        if user:
            body += mqtt.string(user)
        if password:
            body += mqtt.string(password)
        self.sock.sendall(mqtt.packet(mqtt.CONNECT, 0, body))
        kind, _, ack = mqtt.read_packet(self.f, 1 << 20)
        assert kind == mqtt.CONNACK
        self.code = ack[1]

    def publish(self, topic, payload, qos=0, retain=False):
        self.sock.sendall(
            mqtt.publish_packet(topic, payload.encode() if isinstance(payload, str) else payload, qos, retain, 7 if qos else None)
        )
        if qos == 1:
            kind, _, body = mqtt.read_packet(self.f, 1 << 20)
            assert kind == mqtt.PUBACK and body == struct.pack("!H", 7)
        if qos == 2:
            kind, _, _ = mqtt.read_packet(self.f, 1 << 20)
            assert kind == mqtt.PUBREC
            self.sock.sendall(mqtt.packet(mqtt.PUBREL, 2, struct.pack("!H", 7)))
            kind, _, _ = mqtt.read_packet(self.f, 1 << 20)
            assert kind == mqtt.PUBCOMP

    def subscribe(self, *filters):
        body = struct.pack("!H", 3) + b"".join(mqtt.string(f) + b"\x00" for f in filters)
        self.sock.sendall(mqtt.packet(mqtt.SUBSCRIBE, 2, body))
        kind, _, body = mqtt.read_packet(self.f, 1 << 20)
        assert kind == mqtt.SUBACK
        return list(body[2:])

    def receive(self):
        kind, flags, body = mqtt.read_packet(self.f, 1 << 20)
        assert kind == mqtt.PUBLISH
        topic, payload, _qos, retain, _ = mqtt.parse_publish(flags, body)
        return topic, payload, retain

    def close(self):
        self.sock.sendall(mqtt.packet(mqtt.DISCONNECT))
        self.sock.close()


def _wait(check, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.05)
    return check()


# ---------- the wire ----------
def test_topic_filters():
    assert mqtt.matches("home/+/temp", "home/kitchen/temp")
    assert not mqtt.matches("home/+/temp", "home/kitchen/hum")
    assert mqtt.matches("home/#", "home") and mqtt.matches("home/#", "home/a/b")
    assert mqtt.matches("#", "a/b") and not mqtt.matches("#", "$SYS/broker")
    assert not mqtt.matches("home/+", "home/a/b")
    assert mqtt.valid_filter("a/+/#") and not mqtt.valid_filter("a/#/b") and not mqtt.valid_filter("a+/b") and not mqtt.valid_filter("")


def test_devices_from_topics():
    assert sensors.mqtt_device("zigbee2mqtt/kitchen_sensor") == "zigbee2mqtt/kitchen_sensor"
    assert sensors.mqtt_device("zigbee2mqtt/kitchen_sensor/availability") == "zigbee2mqtt/kitchen_sensor"
    assert sensors.mqtt_device("tele/plug-1/SENSOR") == "tele/plug-1"
    assert sensors.mqtt_device("home/kitchen/temperature") == "home/kitchen"
    assert sensors.mqtt_device("router/load") == "router"
    assert sensors.mqtt_device("uptime") == "uptime"


def test_reading_payloads():
    assert sensors.read(b"21.5") == {"kind": "number", "value": 21.5}
    assert sensors.read(b"ON")["value"] == 1.0 and sensors.read(b"offline")["kind"] == "boolean"
    j = sensors.read(b'{"temperature": 21.5, "occupancy": true, "state": "OFF", "name": "x"}')
    assert j["kind"] == "json" and j["fields"] == {"temperature": 21.5, "occupancy": 1.0, "state": 0.0}
    assert sensors.read(b"nan")["kind"] == "text" and sensors.read("hello")["text"] == "hello"


def test_syslog_formats():
    now = dt.datetime(2026, 10, 3, 4, tzinfo=dt.UTC)
    m = syslog.parse(b"<30>Oct  3 03:20:01 dnsmasq[123]: query[A] example.com from 192.168.1.5", now)
    assert (m["app"], m["pid"], m["level"], m["host"]) == ("dnsmasq", "123", 6, None)
    assert m["message"].startswith("query[A]")
    m = syslog.parse(b"<12>Oct  3 03:20:01 router kernel: link down", now)
    assert (m["host"], m["app"], m["level"], m["message"]) == ("router", "kernel", 4, "link down")
    m = syslog.parse(b'<165>1 2003-10-11T22:14:15.003Z mymachine.example.com evntslog - ID47 [x@1 iut="3"] An application event')
    assert (m["host"], m["app"], m["level"], m["message"]) == ("mymachine.example.com", "evntslog", 5, "An application event")
    m = syslog.parse(b"<14>Dec 31 23:59:59 nas backup done", dt.datetime(2027, 1, 1, 0, 1, tzinfo=dt.UTC))
    assert m["at"].year == 2026 and m["host"] == "nas" and m["message"] == "backup done"
    assert syslog.parse("plain words")["message"] == "plain words"
    assert syslog.allowed("192.168.1.9", ["192.168.0.0/16"]) and not syslog.allowed("8.8.8.8", ["192.168.0.0/16"])
    assert syslog.allowed("::ffff:10.0.0.2", ["10.0.0.0/8"])


def test_syslog_tcp_framing():
    import io

    msgs = [f"<34>1 2026-10-03T01:00:00Z h app - - number {i} ".encode() + b"x" * 50 for i in range(3000)]  # well over 64 KB
    counted = b"".join(str(len(m)).encode() + b" " + m for m in msgs)
    assert list(syslog._frames(io.BufferedReader(io.BytesIO(counted)))) == msgs
    lines = b"<30>a b: c\n\n<30>d e: f\r\n"
    assert list(syslog._frames(io.BufferedReader(io.BytesIO(lines)))) == [b"<30>a b: c\n", b"<30>d e: f\r\n"]


# ---------- the broker on its own ----------
def test_broker_is_an_ordinary_local_broker():
    got = []
    b = mqtt.Broker(
        "127.0.0.1", 0, lambda u, p, cid, a: {"u": u} if (u, p) == ("dev", "secret12") else None, lambda t, p, lg, a: got.append((t, p, lg))
    ).start()
    try:
        assert Device(b.port, "x", "dev", "wrong").code == mqtt.BAD_LOGIN
        assert Device(b.port, "y").code == mqtt.NOT_AUTHORIZED
        assert Device(b.port, "z", "dev", "secret12", level=5).code == mqtt.BAD_PROTOCOL
        pub = Device(b.port, "pub", "dev", "secret12")
        pub.publish("home/kitchen/temp", "21.5", qos=1, retain=True)
        sub = Device(b.port, "sub", "dev", "secret12")
        assert sub.subscribe("home/#", "bad/#/x") == [0, 0x80]
        assert sub.receive() == ("home/kitchen/temp", b"21.5", True)  # the retained message
        pub.publish("home/kitchen/hum", "40", qos=2)
        pub.publish("other/x", "1")
        pub.publish("home/door", "open")
        assert sub.receive() == ("home/kitchen/hum", b"40", False)
        assert sub.receive() == ("home/door", b"open", False)  # other/x went to nobody
        _wait(lambda: len(got) == 4)  # Lens hears of a message just after its subscribers do
        assert [t for t, _, _ in got] == ["home/kitchen/temp", "home/kitchen/hum", "other/x", "home/door"]
        assert got[0][2] == {"u": "dev"}
        assert b.clients() == 2
        pub.close()
        sub.close()
    finally:
        b.stop()


def test_bridge_client_reads_another_broker():
    b = mqtt.Broker("127.0.0.1", 0, lambda *a: {"ok": 1}, lambda *a: None).start()
    try:
        got, stop = [], threading.Event()
        c = mqtt.Client("127.0.0.1", b.port, "u", "p", keepalive=5)
        th = threading.Thread(target=lambda: c.run(["sensors/#"], lambda t, p: got.append((t, p)), stop), daemon=True)
        th.start()
        assert _wait(lambda: b.clients() == 1)
        time.sleep(0.2)
        Device(b.port, "pub", "u", "p").publish("sensors/a", "1", qos=1)
        assert _wait(lambda: got) == [("sensors/a", b"1")]
        stop.set()
        th.join(5)
        assert not th.is_alive()
    finally:
        b.stop()


# ---------- the hub ----------
@pytest.fixture
def hub(db, cfg):
    cfg["sensors"].update(enabled=True, mqtt_port=0, syslog_port=0, bind="127.0.0.1")
    h = sensors.Hub(db, _cfg_fn(cfg), log=print, name="test")
    h.apply()
    yield h
    h.close()


def test_hub_takes_devices_in_and_organises_them(client, admin, db, cfg, hub):
    ns = store.ns_id(db, "pods")
    sensors.create_login(db, "zigbee", "gateway-pass", space=ns)
    port = hub.state["mqtt"]["port"]
    assert hub.state["mqtt"]["running"] and hub.state["syslog"]["running"]
    d = Device(port, "z2m", "zigbee", "gateway-pass")
    assert d.code == 0
    d.publish("zigbee2mqtt/kitchen", '{"temperature": 21.5, "humidity": 40, "battery": 90}')
    d.publish("zigbee2mqtt/kitchen", '{"temperature": 22.5, "humidity": 41, "battery": 90}', qos=1)
    d.publish("zigbee2mqtt/kitchen/availability", "online")
    d.publish("home/hall/door", "closed")
    d.close()
    _wait(lambda: len(hub.intake.rows) >= 4)
    hub.intake.flush()

    cat = client.get("/api/v1/sensors", headers=admin).json()
    found = {s["name"]: s for s in cat["sensors"]}
    kitchen = found["zigbee2mqtt/kitchen"]
    assert kitchen["family"] == "stream" and kitchen["type"] == "mqtt" and kitchen["status"] == "new"
    assert kitchen["namespace"] == "pods"  # the login's namespace
    assert kitchen["channels"] == 2 and kitchen["readings"] == 3
    assert kitchen["suggested"]["handling"]["store"] == "all"
    assert found["home/hall"]["channels"] == 1
    assert cat["hub"]["new"] == 2 and cat["types"]["imap"]["family"] == "files" and cat["types"]["mqtt"]["family"] == "stream"

    det = client.get(f"/api/v1/sensors/{kitchen['id']}", headers=admin).json()
    streams = {s["name"]: s for s in det["streams"]}
    main = streams["zigbee2mqtt/kitchen"]
    assert main["kind"] == "json" and sorted(main["fields"]) == ["battery", "humidity", "temperature"]
    assert streams["zigbee2mqtt/kitchen/availability"]["kind"] == "boolean"
    rd = client.get(f"/api/v1/sensors/{kitchen['id']}/readings", headers=admin, params={"stream": main["id"]}).json()
    assert [r["fields"]["temperature"] for r in rd] == [22.5, 21.5]
    pts = client.get(f"/api/v1/sensors/{kitchen['id']}/series", headers=admin, params={"stream": main["id"], "field": "temperature"}).json()
    assert len(pts) == 1 and pts[0]["n"] == 2 and pts[0]["min"] == 21.5 and pts[0]["max"] == 22.5 and pts[0]["avg"] == 22.0

    # a second batch adds to the same hour's rollup
    sensors.Intake.put(hub.intake, sensors.get(db, kitchen["id"]), "zigbee2mqtt/kitchen", '{"temperature": 30}')
    hub.intake.flush()
    pts = client.get(f"/api/v1/sensors/{kitchen['id']}/series", headers=admin, params={"stream": main["id"], "field": "temperature"}).json()
    assert pts[0]["n"] == 3 and pts[0]["max"] == 30

    # applying the suggestion marks it looked at; review does the rest
    r = client.post(f"/api/v1/sensors/{kitchen['id']}/suggestion", headers=admin)
    assert r.status_code == 200 and r.json()["handling"]["raw_days"] == 14
    assert client.post("/api/v1/sensors/review", headers=admin).json() == {"applied": 1}
    assert client.get("/api/v1/sensors", headers=admin).json()["hub"]["new"] == 0


def test_hub_logins_and_anonymous(client, admin, db, cfg, hub):
    port = hub.state["mqtt"]["port"]
    r = client.post("/api/v1/sensor-logins", headers=admin, json={"username": "router", "password": "short"})
    assert r.status_code == 400
    r = client.post("/api/v1/sensor-logins", headers=admin, json={"username": "router", "password": "long-enough"})
    assert r.status_code == 200
    lid = r.json()["id"]
    assert client.post("/api/v1/sensor-logins", headers=admin, json={"username": "router", "password": "long-enough"}).status_code == 400
    assert [x["username"] for x in client.get("/api/v1/sensor-logins", headers=admin).json()] == ["router"]
    assert Device(port, "a").code == mqtt.NOT_AUTHORIZED
    assert Device(port, "b", "router", "nope-nope").code == mqtt.BAD_LOGIN
    assert Device(port, "c", "router", "long-enough").code == 0
    cfg["sensors"]["mqtt_anonymous"] = True
    assert Device(port, "d").code == 0
    assert client.delete(f"/api/v1/sensor-logins/{lid}", headers=admin).status_code == 200
    assert Device(port, "e", "router", "long-enough").code == mqtt.BAD_LOGIN


def test_syslog_senders_become_sensors(client, admin, db, cfg, hub):
    port = hub.state["syslog"]["port"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"<30>Oct  3 03:20:01 pihole dnsmasq[12]: query[A] example.com from 192.168.1.5", ("127.0.0.1", port))
    s.sendto(b"<11>Oct  3 03:20:02 pihole kernel: disk error", ("127.0.0.1", port))
    t = socket.create_connection(("127.0.0.1", port))
    t.sendall(b"<30>Oct  3 03:20:03 pihole dnsmasq[12]: cached example.com\n")
    t.close()
    _wait(lambda: len(hub.intake.rows) >= 3)
    hub.intake.flush()
    row = sensors._by_key(db, "syslog:127.0.0.1")
    assert row and row["name"] == "pihole" and row["status"] == "new"
    det = sensors.detail(db, cfg, row["id"])
    assert {x["name"]: x["count"] for x in det["streams"]} == {"dnsmasq": 2, "kernel": 1}
    assert {x["kind"] for x in det["streams"]} == {"log"}
    levels = sorted(r["level"] for r in sensors.readings(db, row["id"]))
    assert levels == [3, 6, 6]
    assert sensors.suggest(db, cfg, row)["handling"]["important_days"] == 180

    # senders outside syslog_networks are dropped unread
    hub.listener.networks = ["10.0.0.0/8"]
    s.sendto(b"<30>Oct  3 03:20:01 x y: z", ("127.0.0.1", port))
    time.sleep(0.3)
    hub.intake.flush()
    assert len(sensors.readings(db, row["id"])) == 3


def test_hub_follows_the_settings(db, cfg):
    cfg["sensors"].update(enabled=False, mqtt_port=0, syslog_port=0, bind="127.0.0.1")
    h = sensors.Hub(db, _cfg_fn(cfg), name="t2")
    try:
        h.apply()
        assert h.broker is None and h.listener is None
        cfg["sensors"]["enabled"] = True
        cfg["sensors"]["syslog"] = False
        h.apply()
        assert h.broker is not None and h.listener is None
        h.heartbeat()
        st = sensors.hub_status(db, cfg)
        assert st["processes"][0]["process"] == "t2" and st["processes"][0]["mqtt"]["running"]
        # a port someone else holds: recorded, retried later
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        cfg["sensors"].update(syslog=True, syslog_port=busy.getsockname()[1])
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", busy.getsockname()[1]))
        h.apply()
        assert h.listener is None and not h.state["syslog"]["running"] and "port" in h.state["syslog"]["error"]
        busy.close()
        udp.close()
        cfg["sensors"]["enabled"] = False
        h.apply()
        assert h.broker is None
    finally:
        h.close()


# ---------- handling ----------
def test_handling_changes_summary_paused_and_rate(db, cfg):
    it = sensors.Intake(db, _cfg_fn(cfg))
    sid, _ = sensors.create(db, cfg, "mqtt", "Plug", {"prefix": "tele/plug"}, handling_={"store": "changes"})
    s = sensors.get(db, sid)
    for v in ("1", "1", "1", "2", "2", "1"):
        it.put(s, "tele/plug/POWER", v)
    it.flush()
    assert [r["value"] for r in sensors.readings(db, sid)] == [1.0, 2.0, 1.0]
    st = sensors.streams(db, sid)[0]
    assert (st["count"], st["stored"], st["last_value"]) == (6, 3, 1.0)

    sensors.update(db, sid, handling={"store": "summary"})
    it.put(sensors.get(db, sid), "tele/plug/POWER", "5")
    it.flush()
    assert len(sensors.readings(db, sid)) == 3
    assert sensors.series(db, sid, st["id"])[0]["n"] == 7

    sensors.update(db, sid, status="paused")
    assert not it.put(sensors.get(db, sid), "tele/plug/POWER", "9")
    it.flush()
    assert sensors.streams(db, sid)[0]["dropped"] == 1

    sensors.update(db, sid, status="active", handling={"store": "all", "max_per_minute": 3})
    kept = [it.put(sensors.get(db, sid), "tele/plug/other", str(i)) for i in range(6)]
    assert kept == [True, True, True, False, False, False]
    with pytest.raises(ValueError):
        sensors.update(db, sid, handling={"store": "sometimes"})
    with pytest.raises(ValueError):
        sensors.update(db, sid, handling={"raw_days": -1})
    sensors.update(db, sid, handling={"max_per_minute": None})
    assert sensors.handling(cfg, sensors.get(db, sid))["max_per_minute"] == cfg["sensors"]["max_per_minute"]
    sensors.update(db, sid, handling={"store": None})
    assert sensors.handling(cfg, sensors.get(db, sid))["store"] == "all"
    sensors.update(db, sid, handling={"raw_days": 0})
    assert sensors.handling(cfg, sensors.get(db, sid))["raw_days"] is None  # for good
    sensors.update(db, sid, handling={"raw_days": None})
    assert sensors.handling(cfg, sensors.get(db, sid))["raw_days"] == cfg["sensors"]["raw_days"]


def test_registered_prefix_wins_over_guessing(db, cfg):
    it = sensors.Intake(db, _cfg_fn(cfg))
    sid, _ = sensors.create(db, cfg, "mqtt", "Router", {"prefix": "openwrt"})
    s = it.mqtt_sensor("openwrt/wan/rx_bytes")
    assert s["id"] == sid
    with pytest.raises(ValueError):
        sensors.create(db, cfg, "mqtt", None, {"prefix": "openwrt"})
    with pytest.raises(ValueError):
        sensors.create(db, cfg, "mqtt", None, {"prefix": "a/#"})
    with pytest.raises(ValueError):
        sensors.create(db, cfg, "syslog", None, {"address": "router"})


# ---------- webhooks and the one list ----------
def test_webhooks_and_one_list_of_sensors(client, admin, db, cfg, folder):
    (folder / "inbox").mkdir()
    src = sources.create(db, cfg, "Inbox", "local")
    r = client.post("/api/v1/sensors", headers=admin, json={"type": "webhook", "name": "Speedtest"})
    assert r.status_code == 200
    wid, token = r.json()["id"], r.json()["token"]
    assert token

    assert client.post("/api/v1/sensors/push/nope", content=b"1").status_code == 401
    r = client.post(f"/api/v1/sensors/push/{token}/down", content=b"412.5", headers={"content-type": "text/plain"})
    assert r.status_code == 202 and r.json() == {"kept": 1}
    r = client.post(
        "/api/v1/sensors/push",
        json={"readings": [{"stream": "up", "value": 40}, {"stream": "ping", "value": 12}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.json() == {"kept": 2}
    r = client.post(f"/api/v1/sensors/push/{token}", content=b"line one\nline two\n", headers={"content-type": "text/plain"})
    assert r.json() == {"kept": 2}
    assert client.post(f"/api/v1/sensors/push/{token}", content=b"{bad", headers={"content-type": "application/json"}).status_code == 400
    names = sorted(s["name"] for s in client.get(f"/api/v1/sensors/{wid}", headers=admin).json()["streams"])
    assert names == ["default", "down", "ping", "up"]

    # a new token; the old one stops working
    new = client.post(f"/api/v1/sensors/{wid}/token", headers=admin).json()["token"]
    assert client.post(f"/api/v1/sensors/push/{token}", content=b"1").status_code == 401
    assert client.post(f"/api/v1/sensors/push/{new}", content=b"1").status_code == 202

    # storage sources are sensors too; sources don't see stream sensors
    every = {s["id"]: s for s in client.get("/api/v1/sensors", headers=admin).json()["sensors"]}
    assert every[src]["family"] == "files" and every[src]["type"] == "local" and every[wid]["family"] == "stream"
    assert [s["id"] for s in client.get("/api/v1/sources", headers=admin).json()] == [src]
    assert client.get(f"/api/v1/sources/{wid}/browse", headers=admin).status_code == 404
    assert client.get(f"/api/v1/sensors/{src}", headers=admin).json()["family"] == "files"
    assert client.patch(f"/api/v1/sensors/{src}", headers=admin, json={"status": "paused"}).status_code == 400

    # a namespace, then none; then away with everything it kept
    ns = store.ns_id(db, "calls")
    assert client.patch(f"/api/v1/sensors/{wid}", headers=admin, json={"space": ns, "name": "Speed"}).status_code == 200
    got = client.get(f"/api/v1/sensors/{wid}", headers=admin).json()
    assert (got["namespace"], got["name"]) == ("calls", "Speed")
    client.patch(f"/api/v1/sensors/{wid}", headers=admin, json={"space": None})
    assert client.get(f"/api/v1/sensors/{wid}", headers=admin).json()["namespace"] is None
    assert client.delete(f"/api/v1/sensors/{wid}", headers=admin).status_code == 200
    assert not db.rows("SELECT id FROM sensor_reading WHERE sensor = $s", s=wid)
    assert client.get(f"/api/v1/sensors/{wid}", headers=admin).status_code == 404

    # not for people who aren't admins
    make_user(db, "ed@example.com", "pw-editor-123", roles={"pods": "editor"})
    h = login(client, "ed@example.com", "pw-editor-123")
    assert client.get("/api/v1/sensors", headers=h).status_code == 403


# ---------- retention, as a routine ----------
def test_retention_runs_as_a_routine(db, cfg):
    routines.seed(db)
    tidy = next(r for r in routines.list_routines(db) if r["name"] == routines.SENSORS_NAME)
    assert tidy["enabled"] and tidy["actions"] == [{"type": "sensors"}]
    assert routines._idle(db, cfg, tidy)  # no stream sensors: nothing to do

    sid, _ = sensors.create(
        db, cfg, "syslog", "NAS", {"address": "192.168.1.10"}, handling_={"raw_days": 7, "important_days": 30, "rollup_days": 60}
    )
    assert not routines._idle(db, cfg, tidy)
    now = dt.datetime.now(dt.UTC)

    def at(days):
        return sensors._ts(now - dt.timedelta(days=days))

    rows = [
        {"sensor": sid, "stream": "s", "at": at(1), "text": "fresh"},
        {"sensor": sid, "stream": "s", "at": at(10), "text": "old info", "level": 6},
        {"sensor": sid, "stream": "s", "at": at(10), "text": "old error", "level": 3},
        {"sensor": sid, "stream": "s", "at": at(40), "text": "older error", "level": 3},
    ]
    db.q("INSERT INTO sensor_reading $rows", rows=rows)
    for days in (1, 90):
        hour = (now - dt.timedelta(days=days)).isoformat(timespec="hours")[:13]
        db.q("CREATE $r CONTENT $d", r=R("sensor_rollup", f"x-{days}"), d={"sensor": sid, "stream": "s", "field": "", "hour": hour, "n": 1})
    run_id = routines.run(db, cfg, tidy["id"])
    run = routines.get_run(db, run_id)
    assert run["status"] == "done"
    assert run["results"][0]["result"] == {"sensors": 1, "readings": 2, "rollups": 1, "triaged": 0, "digests": 0}
    assert sorted(r["text"] for r in sensors.readings(db, sid)) == ["fresh", "old error"]

    # a routine can name its sensors; only stream sensors
    with pytest.raises(ValueError):
        routines.create(db, "x", [{"type": "sensors", "sensors": [999]}])
    routines.create(db, "y", [{"type": "sensors", "sensors": [sid]}])


def test_sensor_settings(client, admin):
    put = lambda body: client.put("/api/v1/settings/sensors", headers=admin, json=body)  # noqa: E731
    assert put({"enabled": True, "mqtt_port": 1884, "raw_days": None, "syslog_networks": ["192.168.1.0/24"]}).status_code == 200
    got = client.get("/api/v1/settings", headers=admin).json()["sensors"]["values"]
    assert got["enabled"] and got["mqtt_port"] == 1884 and got["raw_days"] is None and got["syslog_networks"] == ["192.168.1.0/24"]
    assert "bind" not in got
    assert put({"mqtt_port": 70000}).status_code == 400
    assert put({"store": "maybe"}).status_code == 400
    assert put({"syslog_networks": ["not a network"]}).status_code == 400
    assert put({"bind": "0.0.0.0"}).status_code == 400


# ---------- log patterns and triage ----------
def test_log_lines_are_grouped_into_patterns():
    assert sensors.pattern("query[A] example.com from 192.168.1.5") == "query[A] <name> from <ip>"
    assert sensors.pattern("DHCPACK(br-lan) 192.168.1.20 aa:bb:cc:dd:ee:ff phone") == "DHCPACK(br-lan) <ip> <mac> phone"
    assert sensors.pattern("query[AAAA] api.github.com from fe80::1c2b:3cff:fe4d:5e6f") == "query[AAAA] <name> from <ip>"
    assert sensors.pattern("session 4f9a2c1e8b7d6a5f started after 12.5ms") == "session <hex> started after <n>ms"
    assert sensors.pattern("  ") == "(empty)"


def _syslog_sensor(db, cfg, it, lines):
    sid, _ = sensors.create(db, cfg, "syslog", "Pi-hole", {"address": "192.168.1.2"})
    s = sensors.get(db, sid)
    for prog, line, level in lines:
        it.put(s, prog, line, level=level, log=True)
    it.flush()
    return sid


def test_patterns_count_drop_and_label(client, admin, db, cfg):
    it = sensors.intake(db, _cfg_fn(cfg))
    lines = [("dnsmasq", f"query[A] site{i}.example.com from 192.168.1.{i}", 6) for i in range(5)]
    lines += [("dnsmasq", "reply site1.example.com is 1.2.3.4", 6), ("kernel", "disk error on sda", 3)]
    sid = _syslog_sensor(db, cfg, it, lines)
    pats = client.get(f"/api/v1/sensors/{sid}/patterns", headers=admin).json()
    assert [(p["template"], p["count"]) for p in pats][:1] == [("query[A] <name> from <ip>", 5)]
    assert len(pats) == 3 and all(p["action"] == "keep" and p["label"] is None for p in pats)
    query = pats[0]
    assert query["example"].startswith("query[A] site0") and query["stream_name"] == "dnsmasq"

    # drop: counted, not kept
    assert client.patch(f"/api/v1/sensor-patterns/{query['id']}", headers=admin, json={"action": "drop"}).status_code == 200
    it._fresh()
    it.checked = 0
    it.put(sensors.get(db, sid), "dnsmasq", "query[A] other.example.com from 192.168.1.9", level=6, log=True)
    it.flush()
    got = client.get(f"/api/v1/sensors/{sid}/patterns", headers=admin).json()
    assert next(p for p in got if p["id"] == query["id"])["count"] == 6
    assert len(sensors.readings(db, sid)) == 7

    # a person's label; cleared again
    assert client.patch(f"/api/v1/sensor-patterns/{query['id']}", headers=admin, json={"label": "routine"}).status_code == 200
    p = client.get(f"/api/v1/sensors/{sid}/patterns", headers=admin, params={"label": "routine"}).json()
    assert [(x["id"], x["label_by"], x["sure"]) for x in p] == [(query["id"], "admin@example.com", True)]
    client.patch(f"/api/v1/sensor-patterns/{query['id']}", headers=admin, json={"label": None})
    assert len(client.get(f"/api/v1/sensors/{sid}/patterns", headers=admin, params={"label": "none"}).json()) == 3
    assert client.patch(f"/api/v1/sensor-patterns/{query['id']}", headers=admin, json={"label": "panic"}).status_code == 422
    assert client.patch("/api/v1/sensor-patterns/nope", headers=admin, json={"label": "alert"}).status_code == 404


def test_patterns_have_a_ceiling_and_drops_reach_every_way_in(db, cfg, monkeypatch):
    from app.domain import sensor_patterns

    monkeypatch.setattr(sensors, "MAX_PATTERNS", 3)
    it = sensors.Intake(db, _cfg_fn(cfg))
    sid = _syslog_sensor(db, cfg, it, [("app", f"user{chr(97 + i)} did thing{chr(97 + i)}", 6) for i in range(6)])
    got = {p["template"]: p["count"] for p in sensor_patterns.list_patterns(db, sid)}
    assert len(got) == 4 and got[sensors.OTHER_PATTERN] == 3

    # a webhook's intake never looks a sensor up, and still drops what's dropped
    wid, token = sensors.create(db, cfg, "webhook", "Hook")
    sensors.push(db, _cfg_fn(cfg), token, "log", b"backup took 12s", "text/plain")
    pid = sensor_patterns.list_patterns(db, wid)[0]["id"]
    sensor_patterns.update(db, pid, action="drop")
    fresh = sensors.Intake(db, _cfg_fn(cfg))
    sensors._INTAKES[id(db)] = fresh
    assert sensors.push(db, _cfg_fn(cfg), token, "log", b"backup took 15s", "text/plain") == 0
    assert len(sensors.readings(db, wid)) == 1

    # patterns nobody chose anything for go when they haven't been seen for as long as anything is kept
    old = sensors._ts(dt.datetime.now(dt.UTC) - dt.timedelta(days=400))
    db.q("UPDATE sensor_pattern SET last_at = $t WHERE sensor = $s", t=old, s=sid)
    keep = sensor_patterns.list_patterns(db, sid)[0]["id"]
    sensor_patterns.update(db, keep, label="notable")
    sensors.tidy(db, cfg, [sid])
    assert [p["id"] for p in sensor_patterns.list_patterns(db, sid)] == [keep]


def test_triage_asks_the_decision_model_once_per_pattern(db, cfg):
    from app.domain import sensor_patterns
    from tests import fake_jev

    srv = fake_jev.start(cfg)
    try:
        it = sensors.Intake(db, _cfg_fn(cfg))
        lines = [("dnsmasq", f"query[A] s{i}.example.com from 10.0.0.{i}", 6) for i in range(30)]
        lines += [("kernel", "disk error on sda", 3)]
        sid = _syslog_sensor(db, cfg, it, lines)
        assert sensor_patterns.triage(db, cfg) == 0  # triage is off
        assert not fake_jev.Handler.seen
        sensors.update(db, sid, handling={"triage": True})
        fake_jev.Handler.answer = {"type": "choice", "choice": "alert", "confidence": 0.6, "probabilities": {"alert": 0.6, "notable": 0.4}}
        assert sensor_patterns.triage(db, cfg) == 2
        assert len(fake_jev.Handler.seen) == 2  # one question per pattern, not per line
        state = fake_jev.Handler.seen[0][1]["state"]
        assert "query[A] <name> from <ip>" in state and "Pi-hole" in state
        labelled = sensor_patterns.list_patterns(db, sid)
        assert {(p["label"], p["label_by"], p["sure"]) for p in labelled} == {("alert", "jev", False)}
        assert sensor_patterns.triage(db, cfg) == 0  # labelled once
    finally:
        srv.shutdown()


# ---------- digests ----------
def test_daily_digests_become_documents(db, cfg):
    from app.domain import sensor_digests

    ns = store.ns_id(db, "pods")
    sid, _ = sensors.create(db, cfg, "mqtt", "Kitchen", {"prefix": "zigbee2mqtt/kitchen"}, space=ns, handling_={"digest": True})
    it = sensors.Intake(db, _cfg_fn(cfg))
    s = sensors.get(db, sid)
    day = dt.date(2026, 10, 1)
    for h, t in ((8, 20.0), (9, 22.0), (10, 24.0)):
        at = sensors._ts(dt.datetime(2026, 10, 1, h, tzinfo=dt.UTC))
        it.put(s, "zigbee2mqtt/kitchen", f'{{"temperature": {t}, "battery": 90}}', at=at)
        it.put(s, "zigbee2mqtt/kitchen/power", "1", at=at)
    it.put(s, "zigbee2mqtt/kitchen/log", "link lost to 192.168.1.4", at=sensors._ts(dt.datetime(2026, 10, 1, 11, tzinfo=dt.UTC)))
    it.flush()
    body = sensor_digests.text(db, s, day)
    assert body.startswith("# Kitchen: 2026-10-01")
    assert "- zigbee2mqtt/kitchen/power: 3 readings, from 1 to 1, averaging 1" in body
    assert "  - temperature: from 20 to 24, averaging 22" in body
    assert "link lost to <ip>" in body
    assert sensor_digests.text(db, s, dt.date(2026, 9, 30)) is None

    db.q("UPDATE $r SET created_at = '2026-09-30T10:00:00+00:00'", r=R("storage_source", sid))
    assert sensor_digests.write(db, cfg, today=dt.date(2026, 10, 3)) == 1  # the 30th and 2nd had nothing
    rec = db.one("SELECT record::id(id) AS id, title, space, sensor, recorded_at FROM recording WHERE sensor = $s", s=sid)
    assert rec["title"] == "Kitchen: 2026-10-01" and rec["space"] == ns and rec["recorded_at"].startswith("2026-10-01")
    assert db.one("SELECT id FROM job WHERE recording = $r", r=rec["id"])
    assert sensor_digests.write(db, cfg, today=dt.date(2026, 10, 3)) == 0  # days are written once

    # no namespace, no digest
    sensors.update(db, sid, space=None)
    db.q("UPDATE $r SET digest_until = NONE", r=R("storage_source", sid))
    lines = []
    assert sensor_digests.write(db, cfg, today=dt.date(2026, 10, 3), say=lines.append) == 0
    assert "needs a namespace" in lines[0]


# ---------- bridges ----------
def test_bridges_bring_another_brokers_devices_in(client, admin, db, cfg, hub):
    other = mqtt.Broker(
        "127.0.0.1", 0, lambda u, p, cid, a: {"u": u} if (u, p) == ("lens", "bridge-pass") else None, lambda *a: None
    ).start()
    try:
        r = client.post(
            "/api/v1/sensors",
            headers=admin,
            json={
                "type": "bridge",
                "name": "Home Assistant",
                "params": {"host": "127.0.0.1", "port": str(other.port), "topics": "zigbee2mqtt/#, tele/#", "user": "lens"},
                "secrets": {"pass": "wrong-pass"},
            },
        )
        assert r.status_code == 200, r.text
        bid = r.json()["id"]
        hub.apply()
        assert _wait(lambda: (sensors.get(db, bid).get("health") or {}).get("ok") is False)
        assert "bad username or password" in sensors.get(db, bid)["health"]["error"]

        before = sensors.get(db, bid)["sealed"]
        bad = client.patch(f"/api/v1/sensors/{bid}", headers=admin, json={"secrets": {"pass": "x"}, "name": " "})
        assert bad.status_code == 400 and sensors.get(db, bid)["sealed"] == before  # nothing saved
        assert client.patch(f"/api/v1/sensors/{bid}", headers=admin, json={"secrets": {"pass": "bridge-pass"}}).status_code == 200
        hub.apply()  # the connection changed: it starts again
        assert _wait(lambda: (sensors.get(db, bid).get("health") or {}).get("ok") is True, 10)
        assert _wait(lambda: other.clients() == 1)
        time.sleep(0.2)
        Device(other.port, "z", "lens", "bridge-pass").publish("zigbee2mqtt/porch", '{"temperature": 11}', qos=1)
        Device(other.port, "y", "lens", "bridge-pass").publish("ignored/topic", "1", qos=1)
        _wait(lambda: hub.intake.rows)
        hub.intake.flush()
        porch = sensors._by_key(db, "mqtt:zigbee2mqtt/porch")
        assert porch and porch["status"] == "new"
        assert not sensors._by_key(db, "mqtt:ignored")
        det = client.get(f"/api/v1/sensors/{bid}", headers=admin).json()
        assert det["secrets"] == {"pass": {"secret": True, "set": True}} and det["health"]["ok"]

        assert client.patch(f"/api/v1/sensors/{bid}", headers=admin, json={"params": {"topics": "a/#/b"}}).status_code == 400
        assert client.patch(f"/api/v1/sensors/{porch['id']}", headers=admin, json={"params": {"prefix": "x"}}).status_code == 400
        assert client.patch(f"/api/v1/sensors/{bid}", headers=admin, json={"status": "paused"}).status_code == 200
        hub.apply()
        assert _wait(lambda: other.clients() == 0)
        assert not hub.bridges
    finally:
        other.stop()
