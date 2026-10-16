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
- `chat.message` (source `chat_messages`): what a person said to Lens, in the web app or a chat app (Telegram, Slack,
  Matterbridge, the mailbox): `chat_message` rows with role user, read by (created_at, id) over the index on
  (role, created_at). Only messages addressed to Lens are stored, so a room's other talk never is; a question an
  automation asked its agent (`origin` on the row) isn't a person's. A web-app chat is its person's alone (`lens.audience`
  owner); a chat app's belongs to the namespace its room answers in, else to the account the channel answers as.
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
SAID_RX = re.compile(r"^said-(\d+)$")
RAN_RX = re.compile(r"^ran-routine_run-(\d+)$")
REF_UNSAFE = re.compile(r"[^A-Za-z0-9_.:@+-]")
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


# ---------- chat ----------
SAID_FIELDS = "id, record::id(id) AS n, chat, content, created_at, attachments"
SAID_WHERE = "role = 'user' AND origin = NONE"


def _short(text, most):
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


def _said(db, rows):
    """chat_message rows (role user) as chat.message activities; a message whose chat is gone, or whose time isn't one,
    is skipped."""
    rows = [r for r in rows if r and isinstance(r.get("n"), int)]
    if not rows:
        return []
    chats = events_content._lookup(db, "chat", {r.get("chat") for r in rows}, "account, kind, scope, via, title")
    ids = {name: sid for sid, name in store.space_names(db).items()}
    people = events_content.actors(db, [(c.get("account"), None) for c in chats.values() if (c.get("kind") or "chat") != "bridge"])
    out = []
    for r in rows:
        c = chats.get(r.get("chat"))
        if not c:
            continue
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError, KeyError):
            continue
        spaces = [ids[n] for n in ((c.get("scope") or {}).get("namespaces") or []) if n in ids]
        text = str(r.get("content") or "")[:TEXT_MOST]
        via = c.get("via") or {}
        if (c.get("kind") or "chat") == "bridge":
            label = str(via.get("label") or "a chat room")
            room = via.get("room") or None
            channel, direct = str(via.get("channel") or "bridge"), bool(via.get("private"))
            who = via.get("userid") or via.get("user")
            name = _short(via.get("user") or via.get("userid") or c.get("title") or "Someone", 200)
            key = REF_UNSAFE.sub("_", f"{channel}:{who}")[:200] if who else f"chat-{r['chat']}"
            actor = {"type": "Person", "id": f"lens:chat_user/{key}", "name": name}
            where = f"{label} · {room}" if room else label
            summary = f"{name} wrote in {where}: “{_short(text, 120)}”"
            seen = (
                {"space": spaces[0], "audience": None, "owner": None}
                if spaces
                else {"space": None, "audience": "owner", "owner": c.get("account")}
            )
        else:
            actor = people.get((c.get("account"), None)) or {"type": "Person", "id": "lens:account", "name": "Someone"}
            channel, room, direct = "web", None, True
            summary = f"{actor['name']} wrote: “{_short(text, 120)}”"
            seen = {"space": spaces[0] if spaces else None, "audience": "owner", "owner": c.get("account")}
        obj = store.clean(
            {
                "type": "Note",
                "id": f"lens:chat_message/{r['n']}",
                "name": _short(text, 120) or "a message",
                "url": f"/chat/{r['chat']}",
                "text": text,
                "channel": channel,
                "room": room,
                "chat": r["chat"],
                "direct": direct,
                "attachments": len(r["attachments"]) if isinstance(r.get("attachments"), list) and r["attachments"] else None,
            }
        )
        out.append(
            events.build(
                f"said-{r['n']}",
                "chat.message",
                actor=actor,
                obj=obj,
                summary=summary,
                source="chat_messages",
                pos=[r["created_at"], r["n"]],
                published=published,
                spaces=spaces if len(spaces) > 1 else None,
                **seen,
            )
        )
    return out


def _rows_past(db, table, fields, where, col, pos, op, limit, since=None, until=None):
    """Rows of `table` matching `where` past `pos` ([col, id]; op ">" oldest first, "<" newest first): those at its
    time with a later id, then those after it, so an index on (..., col) does the range and the order. `since` and
    `until` bound `col` (to the second)."""
    order = f"{col}, id" if op == ">" else f"{col} DESC, id DESC"
    bounds, p = "", {}
    if since:
        bounds += f" AND {col} > $since"
        p["since"] = events_content._sec(since)
    if until:
        bounds += f" AND {col} <= $until"
        p["until"] = events_content._sec(until)
    rows = []
    if pos is not None and pos[1] is not None:
        rows += db.rows(
            f"SELECT {fields} FROM {table} WHERE {where} AND {col} = $pt AND id {op} $pi{bounds} ORDER BY {order} LIMIT {int(limit)}",
            pt=pos[0],
            pi=R(table, pos[1]),
            **p,
        )
    if pos is not None:
        p["pt"] = pos[0]
    after = "true" if pos is None else f"{col} {op} $pt"
    rows += db.rows(f"SELECT {fields} FROM {table} WHERE {where} AND {after}{bounds} ORDER BY {order} LIMIT {int(limit)}", **p)
    return rows[: int(limit)]


def _said_rows(db, pos, op, limit, since=None, until=None):
    """Messages past `pos`, over the (role, created_at) index."""
    return _rows_past(db, "chat_message", SAID_FIELDS, SAID_WHERE, "created_at", pos, op, limit, since, until)


class ChatSource(events.Source):
    """What people said to Lens (chat_message rows with role user) as chat.message, read by (created_at, id)."""

    name = "chat_messages"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        return _said(db, _said_rows(db, pos, ">", limit))  # follow keeps the namespaces asked for

    def page(self, db, cfg, before, who, flt, limit):
        return _said(db, _said_rows(db, before, "<", limit, flt.get("since"), flt.get("until")))

    def get(self, db, cfg, activity_id):
        got = self.get_many(db, cfg, [activity_id])
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = [int(m.group(1)) for m in (SAID_RX.match(i) for i in ids) if m]
        if not ns:
            return []
        return _said(
            db, db.rows(f"SELECT {SAID_FIELDS} FROM chat_message WHERE id IN $rs AND {SAID_WHERE}", rs=[R("chat_message", n) for n in ns])
        )


events.register_source(ChatSource())


# ---------- system ----------
RAN_FIELDS = "id, record::id(id) AS n, routine, trigger, status, started_at, finished_at, error, cost_usd, tokens, changes, results"
RAN_WHERE = "finished_at != NONE AND status != 'released'"  # released: a held run someone let run, which runs anew
RAN_SAYS = {"done": "ran", "partly": "ran with errors", "error": "failed", "skipped": "was skipped (over budget)"}


def _system(spaces):
    """`space` and `audience` for something about Lens itself: the one namespace it is about, else admins only."""
    spaces = [s for s in spaces or [] if isinstance(s, int) and not isinstance(s, bool)]
    return _where(spaces[0] if len(spaces) == 1 else None)


def _ran(db, rows):
    rows = [r for r in rows if r and isinstance(r.get("n"), int)]
    if not rows:
        return []
    labels = [(f"routine:{r['routine']}", None) for r in rows if isinstance(r.get("routine"), int)]
    who = events_content.actors(db, labels)
    routines = events_content._lookup(db, "routine", {r.get("routine") for r in rows}, "name, namespaces")
    out = []
    for r in rows:
        rid = r.get("routine")
        try:
            published = events.stamp(r["finished_at"])
        except (TypeError, ValueError, KeyError):
            continue
        actor = who.get((f"routine:{rid}", None)) or {"type": "Application", "id": f"lens:routine/{rid}", "name": f"Routine {rid}"}
        results = [x for x in r.get("results") or [] if isinstance(x, dict)]
        changes = r.get("changes") if isinstance(r.get("changes"), dict) else None
        obj = store.clean(
            {
                "type": "Note",
                "id": f"lens:routine_run/{r['n']}",
                "name": f"{actor['name']}, run {r['n']}",
                "url": f"/routines/{rid}",
                "status": r.get("status"),
                "trigger": r.get("trigger"),
                "startTime": events.stamp(r["started_at"]) if r.get("started_at") else None,
                "endTime": published,
                "actions": len(results),
                "failed": sum(1 for x in results if x.get("status") == "error"),
                "error": str(r["error"])[:300] if r.get("error") else None,
                "changes": changes,
                "cost_usd": r.get("cost_usd") if isinstance(r.get("cost_usd"), (int, float)) else None,
                "tokens": r.get("tokens") if isinstance(r.get("tokens"), int) else None,
            }
        )
        out.append(
            events.build(
                f"ran-routine_run-{r['n']}",
                "routine.ran",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} {RAN_SAYS.get(r.get('status'), 'finished')}",
                source="routine_runs",
                pos=[r["finished_at"], r["n"]],
                published=published,
                **_system((routines.get(rid) or {}).get("namespaces")),
            )
        )
    return out


class RoutineRunSource(events.Source):
    """routine_run rows that finished (done, partly, error, skipped) as routine.ran, read by (finished_at, id)."""

    name = "routine_runs"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        return _ran(db, _rows_past(db, "routine_run", RAN_FIELDS, RAN_WHERE, "finished_at", pos, ">", limit))

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = [RAN_WHERE]
        if before is not None:
            where.append(events.after_sql("finished_at", before, p, "<", table="routine_run"))
        # bounds that can't be an index range: newest first over one hits SurrealDB's range-order bug (docs/automations.md)
        if flt.get("since"):
            p["since"] = events_content._sec(flt["since"])
            where.append("(finished_at > $since OR finished_at = NONE)")
        if flt.get("until"):
            p["until"] = events_content._sec(flt["until"])
            where.append("(finished_at <= $until OR finished_at = NONE)")
        rows = db.rows(
            f"SELECT {RAN_FIELDS} FROM routine_run WHERE {' AND '.join(where)} ORDER BY finished_at DESC, id DESC LIMIT {int(limit)}", **p
        )
        return _ran(db, rows)

    def get(self, db, cfg, activity_id):
        m = RAN_RX.match(activity_id)
        if not m:
            return None
        got = _ran(db, db.rows(f"SELECT {RAN_FIELDS} FROM $r WHERE {RAN_WHERE}", r=R("routine_run", int(m.group(1)))))
        return got[0] if got else None


events.register_source(RoutineRunSource())


def _budget_name(db, resource):
    """A budget's resource in words: all of Lens, a namespace, a routine, a pipeline or a workflow."""
    if resource == "all":
        return "Lens", None
    table, _, n = str(resource).partition(":")
    n = int(n) if n.isdigit() else None
    if table == "space" and n is not None:
        return f"The {store.space_names(db).get(n) or f'namespace {n}'} namespace", n
    row = events_content._lookup(db, table, [n], "name").get(n) if table in ("routine", "pipeline", "workflow") and n is not None else None
    return (str((row or {}).get("name") or "").strip() or f"{table.capitalize()} {n}"), None


def budget_warned(db, budget, alert, st):
    """A budget.warned for a budget that crossed its warning share or its cap this period (budgets.sweep, once per
    period and state). Its id."""
    res = budget["resource"]
    name, space = _budget_name(db, res)
    over = alert["state"] == "over"
    spent = st.get("spent") or {}
    obj = store.clean(
        {
            "type": "Note",
            "id": f"lens:budget/{REF_UNSAFE.sub('_', res)}",
            "name": f"Budget for {name[:1].lower() + name[1:] if name.startswith('The ') else name}",
            "url": "/costs",
            "resource": res,
            "state": alert["state"],
            "share": alert.get("share"),
            "usd": spent.get("usd"),
            "tokens": spent.get("tokens"),
            "estimate": bool(spent.get("estimate")) or None,
            "cap_usd": budget.get("usd"),
            "cap_tokens": budget.get("tokens"),
            "period": budget.get("period") or "month",
            "since": st.get("since"),
        }
    )
    share = alert.get("share") or 0
    where = _system([space] if space is not None else None)
    return events.emit(
        db,
        "budget.warned",
        actor=dict(events.LENS),
        obj=obj,
        space=where["space"],
        audience=where["audience"],
        summary=f"{name} is over its budget" if over else f"{name} has used {share:.0%} of its budget",
        key=f"budget.warned:{res}:{alert['state']}:{st.get('since')}",
    )


def backup_finished(db, label, got=None, error=None, copied=None, at=None):
    """A backup.finished for a backup taken by hand or on the schedule (backups.py): `got` its entry, else `error` for
    one that failed. Its id."""
    name = (got or {}).get("name")
    ok = got is not None and not error
    obj = store.clean(
        {
            "type": "Document",
            "id": f"lens:backup/{name}" if name else "lens:backup",
            "name": name or f"{label} backup",
            "url": "/settings/backups",
            "label": label,
            "ok": ok,
            "error": str(error)[:500] if error else None,
            "kind": (got or {}).get("kind"),
            "bytes": (got or {}).get("bytes"),
            "files": (got or {}).get("files"),
            "copied": copied,
        }
    )
    what = "A scheduled backup" if label == "scheduled" else "A backup"
    return events.emit(
        db,
        "backup.finished",
        actor=dict(events.LENS),
        obj=obj,
        space=None,
        audience="admins",
        summary=f"{what} finished: {name}" if ok else f"{what} failed: {str(error)[:200]}",
        key=f"backup.finished:{name or f'{label}:{at}'}",
    )


def lens_release(db, rel, current):
    """A lens.release for a newer Lens release (updates.announce, once per release). Its id."""
    tag = str(rel["tag"])
    url = rel.get("url") if isinstance(rel.get("url"), str) and rel["url"].startswith("https://") else None
    obj = store.clean(
        {
            "type": "Note",
            "id": f"lens:release/{REF_UNSAFE.sub('_', tag)[:200]}",
            "name": f"Lens {tag}",
            "url": url,
            "tag": tag,
            "title": " ".join(str(rel.get("name") or "").split())[:200] or None,
            "current": current,
            "published_at": rel.get("published_at"),
        }
    )
    return events.emit(
        db,
        "lens.release",
        actor=dict(events.LENS),
        obj=obj,
        space=None,
        audience="admins",
        summary=f"Lens {tag} is out (this server runs {current})",
        key=f"lens.release:{tag}",
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
CHAT_UNTRUSTED = ("actor.name", "object.name", "object.text", "object.room")
events.kind(
    "chat.message",
    label="Message to Lens",
    source="chat_messages",
    as_type="Create",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "text": _s("What was said"),
                "channel": _s("Where", enum=["web", "telegram", "slack", "bridge", "mailbox"]),
                "room": _s("Room"),
                "chat": _s("Conversation", "integer"),
                "direct": _s("A private chat with Lens", "boolean"),
                "attachments": _s("Files sent with it", "integer"),
            },
        }
    },
    untrusted=CHAT_UNTRUSTED,
    sample=_sample(
        "said-81",
        "chat.message",
        "Create",
        {"type": "Person", "id": "lens:chat_user/telegram:5528", "name": "sam"},
        {
            "type": "Note",
            "id": "lens:chat_message/81",
            "name": "groceries: milk, eggs",
            "url": "/chat/12",
            "text": "groceries: milk, eggs",
            "channel": "telegram",
            "chat": 12,
            "direct": True,
        },
        "sam wrote in Telegram: “groceries: milk, eggs”",
        "chat_messages",
        ["2026-10-15T16:21:58+00:00", 81],
        CHAT_UNTRUSTED,
        space=3,
    ),
)
LENS = dict(events.LENS)
RAN_UNTRUSTED = ("actor.name", "object.name")
events.kind(
    "routine.ran",
    label="Routine ran",
    source="routine_runs",
    as_type="Update",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "status": _s("Status", enum=["done", "partly", "error", "skipped"]),
                "trigger": _s("Started by", enum=["schedule", "manual"]),
                "startTime": _s("Started", format="date-time"),
                "endTime": _s("Finished", format="date-time"),
                "actions": _s("Actions", "integer"),
                "failed": _s("Actions that failed", "integer"),
                "error": _s("Error"),
                "changes": {"type": "object", "title": "Graph changes (applied, proposed)"},
                "cost_usd": _s("Cost (USD)", "number"),
                "tokens": _s("Tokens", "integer"),
            },
        }
    },
    untrusted=RAN_UNTRUSTED,
    sample=_sample(
        "ran-routine_run-51",
        "routine.ran",
        "Update",
        {"type": "Application", "id": "lens:routine/4", "name": "Nightly tidy"},
        {
            "type": "Note",
            "id": "lens:routine_run/51",
            "name": "Nightly tidy, run 51",
            "url": "/routines/4",
            "status": "done",
            "trigger": "schedule",
            "startTime": "2026-10-15T02:00:00.000Z",
            "endTime": "2026-10-15T02:03:12.000Z",
            "actions": 2,
            "failed": 0,
            "changes": {"applied": 3, "proposed": 1},
        },
        "Nightly tidy ran",
        "routine_runs",
        ["2026-10-15T02:03:12+00:00", 51],
        RAN_UNTRUSTED,
    ),
)
SYSTEM_UNTRUSTED = ("object.name",)
events.kind(
    "budget.warned",
    label="Budget warning",
    source="log",
    as_type="Announce",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "resource": _s("What the budget is for (all, space:<id>, routine:<id>, pipeline:<id>, workflow:<id>)"),
                "state": _s("State", enum=["near", "over"]),
                "share": _s("Share of the budget used", "number"),
                "usd": _s("Spent (USD)", "number"),
                "tokens": _s("Spent (tokens)", "integer"),
                "estimate": _s("The USD is an estimate", "boolean"),
                "cap_usd": _s("Budget (USD)", "number"),
                "cap_tokens": _s("Budget (tokens)", "integer"),
                "period": _s("Period", enum=["day", "week", "month"]),
                "since": _s("Period started", format="date-time"),
            },
        }
    },
    untrusted=SYSTEM_UNTRUSTED,
    sample=_sample(
        "log-52",
        "budget.warned",
        "Announce",
        LENS,
        {
            "type": "Note",
            "id": "lens:budget/space_3",
            "name": "Budget for the pods namespace",
            "url": "/costs",
            "resource": "space:3",
            "state": "near",
            "share": 0.82,
            "usd": 4.1,
            "tokens": 812000,
            "cap_usd": 5.0,
            "period": "month",
            "since": "2026-10-01T00:00:00+00:00",
        },
        "The pods namespace has used 82% of its budget",
        "log",
        ["2026-10-15T16:21:58+00:00", 52],
        SYSTEM_UNTRUSTED,
        space=3,
    ),
)
events.kind(
    "backup.finished",
    label="Backup finished",
    source="log",
    as_type="Create",
    object_type="Document",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "label": _s("Taken", enum=["manual", "scheduled"]),
                "ok": _s("It worked", "boolean"),
                "error": _s("Why it failed"),
                "kind": _s("Kind", enum=["export", "folder"]),
                "bytes": _s("Size (bytes)", "integer"),
                "files": _s("Has the data folder's files", "boolean"),
                "copied": _s("Copied to the storage connection", "boolean"),
            },
        }
    },
    untrusted=(),
    sample=_sample(
        "log-53",
        "backup.finished",
        "Create",
        LENS,
        {
            "type": "Document",
            "id": "lens:backup/scheduled-20261015-020000.surql.gz",
            "name": "scheduled-20261015-020000.surql.gz",
            "url": "/settings/backups",
            "label": "scheduled",
            "ok": True,
            "kind": "export",
            "bytes": 48213004,
            "files": True,
            "copied": True,
        },
        "A scheduled backup finished: scheduled-20261015-020000.surql.gz",
        "log",
        ["2026-10-15T02:00:41+00:00", 53],
        (),
    ),
)
RELEASE_UNTRUSTED = ("object.title",)
events.kind(
    "lens.release",
    label="New Lens release",
    source="log",
    as_type="Announce",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "tag": _s("Version"),
                "title": _s("Release name"),
                "current": _s("This server runs"),
                "published_at": _s("Published", format="date-time"),
            },
        }
    },
    untrusted=RELEASE_UNTRUSTED,
    sample=_sample(
        "log-54",
        "lens.release",
        "Announce",
        LENS,
        {
            "type": "Note",
            "id": "lens:release/v0.7.0",
            "name": "Lens v0.7.0",
            "url": "https://github.com/adeelahmad/lense/releases/tag/v0.7.0",
            "tag": "v0.7.0",
            "title": "v0.7.0",
            "current": "0.6.0",
            "published_at": "2026-10-15T09:00:00Z",
        },
        "Lens v0.7.0 is out (this server runs 0.6.0)",
        "log",
        ["2026-10-15T16:21:58+00:00", 54],
        RELEASE_UNTRUSTED,
    ),
)
