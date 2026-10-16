"""The machine side of Lens as activities (docs/automations.md, Kinds): what reaches Lens from outside that isn't a
file. Every kind here has its source named in the catalog; those read from the event log (`log`) are emitted where the
thing happens.

- `sensor.reading` (noisy; source `sensor_readings`): the readings a stream sensor keeps (`sensor_reading`, as its
  handling stores them: all, changes, or none for summary), read by (at, id) one sensor at a time over the index on
  (sensor, at), and only the sensors a follower's hint names (`{"actors": ["lens:sensor/5"]}`), so a busy sensor
  nobody listens to is never read. Their text and the sensor's names are outside text.
- `sensor.alert` (log): a log line whose pattern is labelled alert or notable, emitted by the intake (sensors.py)
  once per pattern per hour, whatever the sensor keeps of its lines.
- `sensor.joined` (source `sensors`): a device or host that made itself a sensor by reporting (`storage_source` rows
  discovery made), by `created_at`.
- `webhook.received`: a call to an automation's webhook address (automation_hooks.py), emitted by the route that
  takes it. Its payload and query are outside text.

A sensor with no namespace makes activities for admins (`lens.audience`), as its page is.
"""

from __future__ import annotations

import datetime as dt
import re

from . import events, events_content, sensors, store

R = store.R
SENSOR_RX = re.compile(r"^lens:sensor/(\d+)$")
READING_RX = re.compile(r"^reading-([a-z0-9]{1,100})$")
JOINED_RX = re.compile(r"^joined-sensor-(\d+)$")
TEXT_MOST = 1000  # characters of a reading's text an activity carries


def _s(title, typ="string", **more):
    return {"type": typ, "title": title, **more}


# ---------- webhooks ----------
events.kind(
    "webhook.received",
    label="Webhook called",
    source="log",
    as_type="Announce",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "hook": _s("Webhook", "integer"),
                "payload": {"title": "What was sent"},
                "content_type": _s("Content type"),
                "query": {"type": "object", "title": "Query"},
            },
        }
    },
    untrusted=("object.payload", "object.query", "object.content_type"),
    sample={
        "id": "log-31",
        "type": "Announce",
        "kind": "webhook.received",
        "published": "2026-10-15T18:02:11.000Z",
        "actor": {"type": "Service", "id": "lens:automation_hook/2", "name": "Webhook for Front door"},
        "object": {
            "type": "Note",
            "hook": 2,
            "payload": {"event": "doorbell", "camera": "front"},
            "content_type": "application/json",
            "query": {},
        },
        "target": None,
        "tag": [],
        "summary": "Webhook for Front door was called",
        "space": 3,
        "lens": {
            "source": "log",
            "pos": ["2026-10-15T18:02:11+00:00", 31],
            "untrusted": ["object.payload", "object.query", "object.content_type"],
            "cause": None,
        },
    },
)


# ---------- sensors ----------
def _stamp(t):
    """A time as sensor readings write theirs (sensors._ts: microseconds, +00:00), so they compare as text."""
    return sensors._ts(events._parse(t))


def _hinted(hint):
    """The sensor ids a follower's hint names, or None when it names none (then every sensor is read)."""
    ids = {int(m.group(1)) for m in (SENSOR_RX.match(str(a)) for a in (hint or {}).get("actors") or []) if m}
    return ids or None


def _sensors(db, ids=None):
    """Stream sensors: {id: {name, space, type}}, those in `ids` when given."""
    rows = db.rows("SELECT record::id(id) AS id, name, space, type FROM storage_source WHERE type IN $t", t=sorted(sensors.STREAM_TYPES))
    return {r["id"]: r for r in rows if ids is None or r["id"] in ids}


def _actor(sid, row):
    return {
        "type": "Service",
        "id": f"lens:sensor/{sid}",
        "name": " ".join(str((row or {}).get("name") or "").split())[:200] or f"Sensor {sid}",
    }


def _where(space):
    """`space` and `audience` for a sensor's activities: its namespace, else admins only."""
    return (
        {"space": space, "audience": None}
        if isinstance(space, int) and not isinstance(space, bool)
        else {"space": None, "audience": "admins"}
    )


def _number(v):
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else None


READING_FIELDS = "id, record::id(id) AS n, sensor, stream, at, value, text, fields, level, pattern"


def _readings(db, rows, known=None):
    """sensor_reading rows as sensor.reading activities (a row whose sensor is gone, or whose time isn't one, is
    skipped)."""
    rows = [r for r in rows if r and isinstance(r.get("n"), str)]
    if not rows:
        return []
    known = known if known is not None else _sensors(db, {r.get("sensor") for r in rows})
    streams = events_content._lookup(db, "sensor_stream", {r.get("stream") for r in rows if r.get("stream")}, "name")
    patterns = events_content._lookup(db, "sensor_pattern", {r.get("pattern") for r in rows if r.get("pattern")}, "template, label")
    out = []
    for r in rows:
        sid = r.get("sensor")
        if sid not in known:
            continue
        try:
            published = events.stamp(r["at"])
        except (TypeError, ValueError):
            continue
        sensor = known[sid]
        stream = str((streams.get(r.get("stream")) or {}).get("name") or "default")[:255]
        text = " ".join(str(r.get("text") or "").split())[:TEXT_MOST] or None
        value = r.get("value") if isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool) else None
        pat = patterns.get(r.get("pattern")) or {}
        said = _number(value) if value is not None and not text else text or _number(value) or "a reading"
        obj = store.clean(
            {
                "type": "Note",
                "id": f"lens:sensor_reading/{r['n']}",
                "name": f"{stream}: {said}"[:300],
                "url": f"/sensors/{sid}",
                "stream": stream,
                "value": value,
                "text": text,
                "fields": r.get("fields") if isinstance(r.get("fields"), dict) and r["fields"] else None,
                "level": r.get("level") if isinstance(r.get("level"), int) else None,
                "pattern": pat.get("template"),
                "label": pat.get("label"),
            }
        )
        actor = _actor(sid, sensor)
        out.append(
            events.build(
                f"reading-{r['n']}",
                "sensor.reading",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} {stream}: {said}",
                source="sensor_readings",
                pos=[r["at"], r["n"]],
                published=published,
                **_where(sensor.get("space")),
            )
        )
    return out


def _key(a):
    return (a["lens"]["pos"][0], a["lens"]["pos"][1])


class ReadingsSource(events.Source):
    """sensor_reading rows as sensor.reading, read one sensor at a time over the (sensor, at) index: after a position
    by `at`, then those at its time with a later id, so the index's range and order do the work."""

    name = "sensor_readings"

    def now(self, db):
        return [sensors._ts(), None]

    def back(self, db, seconds):
        return [sensors._ts(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)), None]

    def _one(self, db, sid, pos, op, limit, since=None, until=None):
        """One sensor's readings past `pos` ([at, id]; op ">" oldest first, "<" newest first), at most `limit`."""
        order = "at, id" if op == ">" else "at DESC, id DESC"
        bounds, p = "", {"s": sid}
        if since:
            bounds += " AND at > $since"
            p["since"] = _stamp(since)
        if until:
            bounds += " AND at <= $until"
            p["until"] = _stamp(until)
        rows = []
        if pos is not None and pos[1] is not None:
            rows += db.rows(
                f"SELECT {READING_FIELDS} FROM sensor_reading WHERE sensor = $s AND at = $pt AND id {op} $pi{bounds} "
                f"ORDER BY {order} LIMIT {int(limit)}",
                pt=pos[0],
                pi=R("sensor_reading", pos[1]),
                **p,
            )
        if pos is None:
            where = "true"
        else:
            where = f"at {op} $pt"
            p["pt"] = pos[0]
        rows += db.rows(
            f"SELECT {READING_FIELDS} FROM sensor_reading WHERE sensor = $s AND {where}{bounds} ORDER BY {order} LIMIT {int(limit)}", **p
        )
        return rows

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        known = {sid: s for sid, s in _sensors(db, _hinted(hint)).items() if spaces is None or s.get("space") in spaces}
        rows = [r for sid in sorted(known) for r in self._one(db, sid, pos, ">", limit)]
        rows.sort(key=lambda r: (r["at"], r["n"]))
        return _readings(db, rows[: int(limit)], known)

    def page(self, db, cfg, before, who, flt, limit):
        ids = _hinted({"actors": flt["actor"]}) if flt.get("actor") else None
        if flt.get("actor") and not ids:
            return []  # the actors asked for aren't sensors
        known = _sensors(db, ids)
        if not who.get("admin"):
            readable = who.get("spaces") or set()
            known = {sid: s for sid, s in known.items() if s.get("space") in readable}
        if flt.get("spaces") is not None:
            known = {sid: s for sid, s in known.items() if s.get("space") in flt["spaces"]}
        rows = [r for sid in sorted(known) for r in self._one(db, sid, before, "<", limit, flt.get("since"), flt.get("until"))]
        rows.sort(key=lambda r: (r["at"], r["n"]), reverse=True)
        return _readings(db, rows[: int(limit)], known)

    def get(self, db, cfg, activity_id):
        got = self.get_many(db, cfg, [activity_id])
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = [m.group(1) for m in (READING_RX.match(i) for i in ids) if m]
        if not ns:
            return []
        return _readings(
            db, db.rows(f"SELECT {READING_FIELDS} FROM sensor_reading WHERE id IN $rs", rs=[R("sensor_reading", n) for n in ns])
        )


events.register_source(ReadingsSource())

JOINED_FIELDS = "id AS rid, record::id(id) AS id, name, type, space, device, status, created_at"


def _joined(db, rows):
    out = []
    for r in rows:
        if not r or r.get("type") not in sensors.STREAM_TYPES:
            continue
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError, KeyError):
            continue
        actor = _actor(r["id"], r)
        label = sensors.STREAM_TYPES[r["type"]]["label"]
        label = label if label[1:2].isupper() else label[:1].lower() + label[1:]  # "MQTT device", "syslog sender"
        obj = store.clean(
            {
                "type": "Service",
                "id": f"lens:sensor/{r['id']}",
                "name": actor["name"],
                "url": f"/sensors/{r['id']}",
                "sensor_type": r["type"],
                "device": " ".join(str(r.get("device") or "").split())[:200] or None,
                "status": r.get("status"),
            }
        )
        out.append(
            events.build(
                f"joined-sensor-{r['id']}",
                "sensor.joined",
                actor=actor,
                obj=obj,
                summary=f"A new {label} reported: {actor['name']}",
                source="sensors",
                pos=[r["created_at"], r["id"]],
                published=published,
                **_where(r.get("space")),
            )
        )
    return out


class JoinedSource(events.Source):
    """storage_source rows discovery made (a device or host reporting for the first time) as sensor.joined, read by
    (created_at, id)."""

    name = "sensors"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {"t": sorted(sensors.STREAM_TYPES)}
        where = f"created_by = 'discovered' AND type IN $t AND {events.after_sql('created_at', pos, p, table='storage_source')}"
        rows = db.rows(
            f"SELECT {JOINED_FIELDS} FROM storage_source WHERE {where} AND {events.space_sql(spaces, p)} ORDER BY created_at, rid LIMIT {int(limit)}",
            **p,
        )
        return _joined(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {"t": sorted(sensors.STREAM_TYPES)}
        where = ["created_by = 'discovered'", "type IN $t", "created_at != NONE"]
        if not who.get("admin"):
            where.append(events.space_sql(who.get("spaces") or set(), p))
        if before is not None:
            where.append(events.after_sql("created_at", before, p, "<", table="storage_source"))
        if flt.get("spaces") is not None:
            p["fs"] = sorted(flt["spaces"])
            where.append("space IN $fs")
        # bounds that can't be an index range: newest first over one hits SurrealDB's range-order bug (docs/automations.md)
        if flt.get("since"):
            p["since"] = events_content._sec(flt["since"])
            where.append("(created_at > $since OR created_at = NONE)")
        if flt.get("until"):
            p["until"] = events_content._sec(flt["until"])
            where.append("(created_at <= $until OR created_at = NONE)")
        rows = db.rows(
            f"SELECT {JOINED_FIELDS} FROM storage_source WHERE {' AND '.join(where)} ORDER BY created_at DESC, rid DESC LIMIT {int(limit)}",
            **p,
        )
        return _joined(db, rows)

    def get(self, db, cfg, activity_id):
        m = JOINED_RX.match(activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {JOINED_FIELDS} FROM $r WHERE created_by = 'discovered'", r=R("storage_source", int(m.group(1))))
        got = _joined(db, [r]) if r else []
        return got[0] if got else None


events.register_source(JoinedSource())


def alert(db, pid, pt, sensor, count):
    """A sensor.alert for a pattern labelled alert or notable that new lines fit (`pt`: the intake's tally for this
    pattern since its last write, with `label`); once per pattern per hour, whichever process sees it first. Its id."""
    hour = str(pt["first"])[:13]
    actor = _actor(pt["sensor"], sensor)
    template = str(pt.get("template") or "")[:300]
    label = pt.get("label") or "alert"
    obj = store.clean(
        {
            "type": "Note",
            "id": f"lens:sensor_pattern/{pid}",
            "name": template or "a log line",
            "url": f"/sensors/{pt['sensor']}",
            "label": label,
            "pattern": template,
            "example": " ".join(str(pt.get("example") or "").split())[:500] or None,
            "count": count if isinstance(count, int) else None,
            "lines": pt.get("n"),
            "hour": hour,
        }
    )
    where = _where((sensor or {}).get("space"))
    return events.emit(
        db,
        "sensor.alert",
        actor=actor,
        obj=obj,
        space=where["space"],
        audience=where["audience"],
        summary=f"{actor['name']} logged {'an alert' if label == 'alert' else 'something notable'}: {template or 'a log line'}",
        published=pt["first"],
        key=f"sensor.alert:{pid}:{hour}",
    )


_SENSOR_OBJECT = {
    "stream": _s("Stream"),
    "value": _s("Value", "number"),
    "text": _s("Text"),
    "fields": {"type": "object", "title": "Fields", "additionalProperties": {"type": "number"}},
    "level": _s("Syslog level (0 emergency to 7 debug)", "integer"),
    "pattern": _s("Log pattern"),
    "label": _s("Pattern's label", enum=["routine", "notable", "alert"]),
}


def _sample(aid, kind, as_type, actor, obj, summary, source, pos, untrusted, space=None):
    return {
        "id": aid,
        "type": as_type,
        "kind": kind,
        "published": "2026-10-15T16:21:58.000Z",
        "actor": actor,
        "object": obj,
        "target": None,
        "tag": [],
        "summary": summary,
        "space": space,
        "lens": {"source": source, "pos": pos, "untrusted": list(untrusted), "cause": None, **({} if space else {"audience": "admins"})},
    }


KITCHEN = {"type": "Service", "id": "lens:sensor/5", "name": "Kitchen sensor"}
ROUTER = {"type": "Service", "id": "lens:sensor/8", "name": "192.168.1.1"}
READING_UNTRUSTED = ("actor.name", "object.name", "object.stream", "object.text", "object.fields", "object.pattern")
events.kind(
    "sensor.reading",
    label="Sensor reading",
    source="sensor_readings",
    as_type="Update",
    object_type="Note",
    noisy=True,
    fields={
        "object": {
            "type": "object",
            "description": "Only the readings a sensor keeps are read: set its handling to store changes (or all) to "
            "trigger on every change; a sensor keeping only a summary has none.",
            "properties": _SENSOR_OBJECT,
        }
    },
    untrusted=READING_UNTRUSTED,
    sample=_sample(
        "reading-k3v9q2m7x1c8z5b4n6d0",
        "sensor.reading",
        "Update",
        KITCHEN,
        {
            "type": "Note",
            "id": "lens:sensor_reading/k3v9q2m7x1c8z5b4n6d0",
            "name": "temperature: 31.2",
            "url": "/sensors/5",
            "stream": "temperature",
            "value": 31.2,
        },
        "Kitchen sensor temperature: 31.2",
        "sensor_readings",
        ["2026-10-15T16:21:58.120391+00:00", "k3v9q2m7x1c8z5b4n6d0"],
        READING_UNTRUSTED,
        space=3,
    ),
)
ALERT_UNTRUSTED = ("actor.name", "object.name", "object.pattern", "object.example")
events.kind(
    "sensor.alert",
    label="Sensor alert",
    source="log",
    as_type="Announce",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "label": _s("Label", enum=["alert", "notable"]),
                "pattern": _s("Log pattern"),
                "example": _s("A line it fits"),
                "count": _s("Lines seen in all", "integer"),
                "lines": _s("Lines when it was raised", "integer"),
                "hour": _s("Hour (UTC)"),
            },
        }
    },
    untrusted=ALERT_UNTRUSTED,
    sample=_sample(
        "log-40",
        "sensor.alert",
        "Announce",
        ROUTER,
        {
            "type": "Note",
            "id": "lens:sensor_pattern/8-3f2a9c1e0b7d4a6c-1a2b3c4d5e6f",
            "name": "wlan<n>: authentication failed for <mac>",
            "url": "/sensors/8",
            "label": "alert",
            "pattern": "wlan<n>: authentication failed for <mac>",
            "example": "wlan0: authentication failed for 3c:22:fb:10:aa:01",
            "count": 412,
            "lines": 3,
            "hour": "2026-10-15T16",
        },
        "192.168.1.1 logged an alert: wlan<n>: authentication failed for <mac>",
        "log",
        ["2026-10-15T16:21:58+00:00", 40],
        ALERT_UNTRUSTED,
    ),
)
JOINED_UNTRUSTED = ("actor.name", "object.name", "object.device")
events.kind(
    "sensor.joined",
    label="New sensor",
    source="sensors",
    as_type="Join",
    object_type="Service",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "sensor_type": _s("Kind of sensor", enum=sorted(sensors.STREAM_TYPES)),
                "device": _s("Device"),
                "status": _s("Status", enum=list(sensors.STATUSES)),
            },
        }
    },
    untrusted=JOINED_UNTRUSTED,
    sample=_sample(
        "joined-sensor-5",
        "sensor.joined",
        "Join",
        KITCHEN,
        {
            "type": "Service",
            "id": "lens:sensor/5",
            "name": "Kitchen sensor",
            "url": "/sensors/5",
            "sensor_type": "mqtt",
            "device": "zigbee2mqtt/kitchen",
            "status": "new",
        },
        "A new MQTT device reported: Kitchen sensor",
        "sensors",
        ["2026-10-15T16:21:58+00:00", 5],
        JOINED_UNTRUSTED,
    ),
)
