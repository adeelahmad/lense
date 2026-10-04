"""Sensors: everything that feeds Lens, in one list.

A sensor is one thing that reports to the archive. There are two families:

- File sensors are the storage sources of sources.py: cloud and local storage through rclone, email accounts (IMAP)
  and calendar feeds (iCal). Their channels are the folders they watch, and what they report becomes resources, as
  it always has. Nothing about them changes here; this module only lists them beside the rest.
- Stream sensors report values and log lines rather than files: devices publishing to the MQTT hub, hosts sending
  syslog, and webhooks. Their channels are streams (an MQTT topic, a syslog program), found as data arrives, and what
  they report is kept as readings, with hourly rollups of anything numeric.

Both live in the storage_source table (a stream sensor is a row whose type is in STREAM_TYPES), so a sensor has one id
whichever family it is. sources.py only ever sees the file family.

Stream sensors organise themselves: a device that publishes to the hub, or a host that sends syslog, becomes a sensor
the first time it does, marked new, with no namespace unless the hub login it used has one. Its streams, their kinds
(number, on/off, JSON, text, log) and its device are worked out from what it sends. Nothing calls a model: by default
readings are stored as they come, and kept for sensors.raw_days. When someone looks, each new sensor has a suggested
handling (how much to keep, for how long), applied only when they choose it.

Handling, per sensor (the sensors settings are the defaults):
- store: all (every reading), changes (a reading only when its value differs from the last one kept, or an hour has
  passed), summary (no readings, only the hourly rollups), none (counted and dropped).
- raw_days: how long readings are kept; important_days: how long log lines of warning or worse are kept (when longer);
  rollup_days: how long the hourly rollups are kept. None keeps them for good.
- max_per_minute: readings over this per stream are dropped (counted), so a chatty device can't flood the archive.

Retention runs as a routine action (`sensors`, routines.py): the "Tidy sensor data" routine does it every hour.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import math
import re
import secrets
import socket
import threading
import time

from . import auth, mqtt, settings, sources, store, syslog

R = store.R
STREAM_TYPES = {
    "mqtt": {
        "label": "MQTT device",
        "fields": {"prefix": ""},
        "secrets": [],
        "help": "a device publishing to the hub: its topics start with the prefix",
    },
    "syslog": {"label": "Syslog sender", "fields": {"address": ""}, "secrets": [], "help": "a host sending syslog from this address"},
    "webhook": {"label": "Webhook", "fields": {}, "secrets": [], "help": "anything that can send HTTP: push to its address with its token"},
    "bridge": {
        "label": "Another MQTT broker",
        "fields": {"host": "", "port": "1883", "tls": "false", "topics": "#", "user": ""},
        "secrets": ["pass"],
        "help": "a broker you already run (Mosquitto, Home Assistant): Lens subscribes to its topics, comma-separated",
    },
}
STATUSES = ("new", "active", "paused", "ignored")
STORE_MODES = ("all", "changes", "summary", "none")
HANDLING = ("store", "raw_days", "rollup_days", "important_days", "max_per_minute", "triage", "digest")
KINDS = ("number", "boolean", "json", "text", "log")
IMPORTANT_LEVEL = 4  # syslog warning: this and worse (lower) are kept for important_days
BOOLEAN = {
    "on": 1.0,
    "off": 0.0,
    "true": 1.0,
    "false": 0.0,
    "open": 1.0,
    "closed": 0.0,
    "yes": 1.0,
    "no": 0.0,
    "online": 1.0,
    "offline": 0.0,
}
MAX_TEXT = 4000
FLUSH_SECONDS, FLUSH_ROWS = 1.0, 500
CACHE_SECONDS = 15
CHANGES_EVERY = 3600  # store=changes still keeps one reading an hour, so a value that never changes shows it's alive
SERVICE_SECONDS = 10
RETRY_SECONDS = 60
BRIDGE_WAIT = (5, 300)  # a bridge that lost its broker tries again after 5 seconds, doubling up to 5 minutes
# a log line's pattern: what's left when the parts that change from one line to the next are taken out
PATTERN_RX = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (
        re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){5,7}[0-9a-f]{1,4}\b|(?<![\w:])[0-9a-f]{0,4}(?::[0-9a-f]{0,4})*::[0-9a-f:]*\b", re.I),
        "<ip>",
    ),
    (re.compile(r"\b(?=[a-z0-9_-]*[a-z])(?:[a-z0-9_-]+\.)+[a-z]{2,}\b\.?", re.I), "<name>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]*\d[0-9a-f]*[a-f][0-9a-f]*\b(?<=\w{12})|\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"[-+]?\d+(?:[.,:]\d+)*"), "<n>"),
]
MAX_PATTERN = 300
MAX_PATTERNS = 2000  # per stream; lines past it that don't fit one are counted under OTHER_PATTERN
OTHER_PATTERN = "(other lines)"
GATEWAYS = {
    "zigbee2mqtt",
    "zwave",
    "zwave2mqtt",
    "zwavejs",
    "shellies",
    "esphome",
    "rtl_433",
    "ble2mqtt",
    "frigate",
    "owntracks",
    "valetudo",
}
FIELDS = (
    "record::id(id) AS id, name, type, params, sealed, health, created_at, created_by, status, space, device, key, handling, "
    "suggested, last_seen_at, push_hash"
)
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}$")


def family(typ):
    return "stream" if typ in STREAM_TYPES else "files"


def types():
    """Every kind of sensor: the file kinds of sources.py and the stream kinds here."""
    out = {k: {**v, "family": "files"} for k, v in sources.BACKENDS.items()}
    out.update({k: {**v, "family": "stream"} for k, v in STREAM_TYPES.items()})
    return out


def _ts(t=None):
    t = t or dt.datetime.now(dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def handling(cfg, sensor):
    """The handling a sensor gets: its own choices over the sensors settings."""
    s = cfg.get("sensors") or {}
    base = {k: s.get(k) for k in HANDLING}
    base["store"] = base["store"] or "all"
    base["triage"], base["digest"] = bool(s.get("triage")), False  # a digest needs a namespace: per sensor only
    own = {k: v for k, v in (sensor.get("handling") or {}).items() if k in HANDLING and v is not None}
    return {**base, **{k: (None if v == 0 and k.endswith("_days") else v) for k, v in own.items()}}  # 0 days: for good


def check_handling(h):
    out = {}
    for k, v in (h or {}).items():
        if k not in HANDLING:
            raise ValueError(f"handling has no setting {k}")
        if k == "store":
            if v is not None and v not in STORE_MODES:
                raise ValueError(f"store is one of: {', '.join(STORE_MODES)}")
        elif k in ("triage", "digest"):
            if v is not None and not isinstance(v, bool):
                raise ValueError(f"{k} is true or false")
        elif v is not None:
            days = k.endswith("_days")
            if not isinstance(v, int) or isinstance(v, bool) or v < (0 if days else 1) or v > (36_500 if days else 100_000):
                raise ValueError(
                    f"{k} is a number of days (0 keeps them for good)" if days else f"{k} is a whole number of readings of at least 1"
                )
        out[k] = v
    return out


# ---------- reading what arrives ----------
def read(payload):
    """What a payload says: {kind, value?, text?, fields?}. A number or on/off becomes `value`; a JSON object keeps
    its text and its top-level numbers (and on/offs) as `fields`; anything else is text."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    s = text.strip()
    low = s.lower()
    if low in BOOLEAN:
        return {"kind": "boolean", "value": BOOLEAN[low], "text": s}
    try:
        v = float(s)
        if math.isfinite(v):
            return {"kind": "number", "value": v}
    except ValueError:
        pass
    if s[:1] in "{[":
        try:
            obj = json.loads(s)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            fields = {}
            for k, v in obj.items():
                if isinstance(v, bool):
                    fields[str(k)[:64]] = 1.0 if v else 0.0
                elif isinstance(v, (int, float)) and math.isfinite(v):
                    fields[str(k)[:64]] = float(v)
                elif isinstance(v, str) and v.strip().lower() in BOOLEAN:
                    fields[str(k)[:64]] = BOOLEAN[v.strip().lower()]
                if len(fields) >= 50:
                    break
            return {"kind": "json", "text": s[:MAX_TEXT], **({"fields": fields} if fields else {})}
        if obj is not None:
            return {"kind": "json", "text": s[:MAX_TEXT]}
    return {"kind": "text", "text": s[:MAX_TEXT]}


def mqtt_device(topic):
    """The device a topic belongs to, for topics no sensor has claimed: a gateway's device (zigbee2mqtt/kitchen),
    Tasmota's (tele/plug-1), else the topic without its last level."""
    parts = [p for p in topic.split("/")]
    if len(parts) >= 2 and parts[0].lower() in GATEWAYS:
        return "/".join(parts[:2])
    if len(parts) >= 3 and parts[0] in ("tele", "stat", "cmnd"):
        return "/".join(parts[:2])
    if parts[0] == "homeassistant" and len(parts) >= 4:
        return "/".join(parts[:3])
    return parts[0] if len(parts) <= 2 else "/".join(parts[:-1])


def stream_key(sid, name):
    return f"{sid}-{hashlib.sha1(name.encode()).hexdigest()[:16]}"


def pattern(text):
    """A log line with what changes from one line to the next (addresses, names, ids, numbers) taken out:
    "query[A] example.com from 192.168.1.5" is "query[A] <name> from <ip>"."""
    t = " ".join((text or "").split())[:2000]
    for rx, sub in PATTERN_RX:
        t = rx.sub(sub, t)
    return t[:MAX_PATTERN] or "(empty)"


def pattern_id(stream, template):
    return f"{stream}-{hashlib.sha1(template.encode()).hexdigest()[:12]}"


def _field_id(field):
    return hashlib.sha1((field or "").encode()).hexdigest()[:8]


# ---------- sensors ----------
def get(db, sid):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("storage_source", int(sid)))
    if not row:
        raise KeyError(sid)
    return row


def _by_key(db, key):
    return db.one(f"SELECT {FIELDS} FROM storage_source WHERE key = $k LIMIT 1", k=key)


def _check_space(db, space):
    if space is not None and space not in store.space_names(db):
        raise ValueError(f"no namespace {space}")
    return space


def _key(typ, params, sid=None):
    p = params or {}
    if typ == "mqtt":
        prefix = str(p.get("prefix") or "").strip().strip("/")
        if not prefix or "+" in prefix or "#" in prefix:
            raise ValueError("an MQTT device needs a topic prefix, such as zigbee2mqtt/kitchen, without wildcards")
        return f"mqtt:{prefix}", {"prefix": prefix}
    if typ == "syslog":
        try:
            ip = str(ipaddress.ip_address(str(p.get("address") or "").strip()))
        except ValueError:
            raise ValueError("a syslog sender needs the IP address it sends from") from None
        return f"syslog:{ip}", {"address": ip}
    if typ == "bridge":
        host = str(p.get("host") or "").strip()
        if not host or "/" in host or " " in host:
            raise ValueError("a broker needs its host name or address")
        try:
            port = int(str(p.get("port") or "1883"))
            if not 0 < port < 65536:
                raise ValueError
        except ValueError:
            raise ValueError("the port is a number, such as 1883") from None
        tls = str(p.get("tls") or "false").lower()
        if tls not in ("true", "false"):
            raise ValueError("tls is true or false")
        topics = [t.strip() for t in str(p.get("topics") or "#").split(",") if t.strip()]
        if not topics or not all(mqtt.valid_filter(t) for t in topics):
            raise ValueError("topics are MQTT topic filters, comma-separated, such as zigbee2mqtt/#, tele/#")
        return f"bridge:{sid}", {
            "host": host,
            "port": str(port),
            "tls": tls,
            "topics": ", ".join(topics),
            "user": str(p.get("user") or "").strip(),
        }
    return f"webhook:{sid}", {}


def create(db, cfg, typ, name=None, params=None, space=None, handling_=None, user=None, secret_values=None):
    """Add a stream sensor by hand (a file sensor is added through sources.create). Returns (id, token): a webhook's
    token is shown this once."""
    if typ not in STREAM_TYPES:
        raise ValueError(f"type is one of: {', '.join(STREAM_TYPES)}")
    extra = sorted(set(params or {}) - set(STREAM_TYPES[typ]["fields"]))
    if extra:
        raise ValueError(f"{typ} has no option {extra[0]}")
    bad = sorted(set(secret_values or {}) - set(STREAM_TYPES[typ]["secrets"]))
    if bad:
        raise ValueError(f"{typ} has no secret {bad[0]}")
    sid = db.next_id("storage_source")
    key, params = _key(typ, params, sid)
    if _by_key(db, key):
        raise ValueError("there's a sensor for that already")
    token = secrets.token_urlsafe(24) if typ == "webhook" else None
    sealed = {k: settings.seal(cfg, v, f"source:{sid}:{k}") for k, v in (secret_values or {}).items() if isinstance(v, str) and v}
    db.q(
        "CREATE $r CONTENT $d",
        r=R("storage_source", sid),
        d=store.clean(
            {
                "name": (name or params.get("prefix") or params.get("address") or params.get("host") or STREAM_TYPES[typ]["label"])[:80],
                "type": typ,
                "params": params,
                "sealed": sealed,
                "key": key,
                "status": "active",
                "space": _check_space(db, space),
                "device": params.get("prefix") or params.get("address"),
                "handling": check_handling(handling_),
                "push_hash": _hash(token) if token else None,
                "created_at": store.now(),
                "created_by": user,
            }
        ),
    )
    return sid, token


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(db, sid):
    s = get(db, sid)
    if s["type"] != "webhook":
        raise ValueError("only a webhook has a token")
    token = secrets.token_urlsafe(24)
    db.q("UPDATE $r SET push_hash = $h", r=R("storage_source", int(sid)), h=_hash(token))
    return token


def discover(db, typ, key, name, device, params, space=None):
    """The sensor for `key`, made (marked new) the first time something reports under it."""
    row = _by_key(db, key)
    if row:
        return row
    sid = db.next_id("storage_source")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("storage_source", sid),
        d=store.clean(
            {
                "name": (name or key)[:80],
                "type": typ,
                "params": params,
                "sealed": {},
                "key": key,
                "status": "new",
                "space": space,
                "device": device,
                "handling": {},
                "created_at": store.now(),
                "created_by": "discovered",
            }
        ),
    )
    return get(db, sid)


def update(db, sid, cfg=None, **changes):
    s = get(db, sid)
    if family(s["type"]) != "stream":
        raise ValueError("change a file sensor through its source")
    sets, nones, sealed = {}, [], None
    if changes.get("params") is not None or changes.get("secrets") is not None:
        if s["type"] != "bridge":
            raise ValueError("only a broker's connection can be changed; add another sensor instead")
        if changes.get("params") is not None:
            extra = sorted(set(changes["params"]) - set(STREAM_TYPES["bridge"]["fields"]))
            if extra:
                raise ValueError(f"bridge has no option {extra[0]}")
            _k, sets["params"] = _key_(s, changes["params"])
        if changes.get("secrets") is not None:
            sealed = dict(s.get("sealed") or {})
            for k, v in changes["secrets"].items():
                if k not in STREAM_TYPES["bridge"]["secrets"]:
                    raise ValueError(f"bridge has no secret {k}")
                if v in (None, ""):
                    sealed.pop(k, None)
                elif isinstance(v, str):
                    sealed[k] = settings.seal(cfg, v, f"source:{sid}:{k}")
    if "name" in changes and changes["name"] is not None:
        if not changes["name"].strip():
            raise ValueError("give the sensor a name")
        sets["name"] = changes["name"].strip()[:80]
    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in STATUSES:
            raise ValueError(f"status is one of: {', '.join(STATUSES)}")
        sets["status"] = changes["status"]
    if "space" in changes:
        if changes["space"] is None:
            nones.append("space")
        else:
            sets["space"] = _check_space(db, changes["space"])
    if "handling" in changes and changes["handling"] is not None:
        h = {**(s.get("handling") or {}), **check_handling(changes["handling"])}
        sets["handling"] = {k: v for k, v in h.items() if v is not None}  # none: back to the settings' default
    if sealed is not None:  # only once everything else checked out
        db.q("UPDATE $r SET sealed = $s", r=R("storage_source", int(sid)), s=sealed)
    if sets:
        db.q("UPDATE $r MERGE $d", r=R("storage_source", int(sid)), d={k: v for k, v in sets.items() if k != "params"})
    if "params" in sets:
        db.q("UPDATE $r SET params = $p", r=R("storage_source", int(sid)), p=sets["params"])
    if "handling" in sets:  # MERGE keeps keys it isn't given: set the whole of it
        db.q("UPDATE $r SET handling = $h", r=R("storage_source", int(sid)), h=sets["handling"])
    for k in nones:
        db.q(f"UPDATE $r SET {k} = NONE", r=R("storage_source", int(sid)))
    _bump(db)


def _key_(s, params):
    return _key("bridge", {**(s.get("params") or {}), **params}, s["id"])


def _bump(db):
    db.next_id("sensors")  # processes holding sensors in memory read them again


def remove(db, sid):
    s = get(db, sid)
    if family(s["type"]) != "stream":
        sources.remove(db, int(sid))
        return
    db.run(
        [
            "DELETE sensor_reading WHERE sensor = $s",
            "DELETE sensor_rollup WHERE sensor = $s",
            "DELETE sensor_stream WHERE sensor = $s",
            "DELETE sensor_pattern WHERE sensor = $s",
            "DELETE $r",
        ],
        s=int(sid),
        r=R("storage_source", int(sid)),
    )
    _bump(db)


def streams(db, sid):
    return db.rows(
        "SELECT record::id(id) AS id, name, kind, fields, last_value, last_text, last_at, count, stored, dropped, created_at "
        "FROM sensor_stream WHERE sensor = $s ORDER BY name",
        s=int(sid),
    )


def view(db, cfg, s, names=None, extra=None):
    """A sensor as the API shows it, whichever family."""
    names = names if names is not None else store.space_names(db)
    if family(s["type"]) == "files":
        v = sources.view(s)
        return {
            **v,
            "family": "files",
            "status": "active",
            "space": None,
            "namespace": None,
            "channels": (extra or {}).get("watches", 0),
            "last_seen_at": (s.get("health") or {}).get("checked_at"),
        }
    return {
        "id": s["id"],
        "name": s["name"],
        "type": s["type"],
        "label": STREAM_TYPES[s["type"]]["label"],
        "family": "stream",
        "params": s.get("params") or {},
        "status": s.get("status") or "active",
        "space": s.get("space"),
        "namespace": names.get(s.get("space")),
        "device": s.get("device"),
        "handling": handling(cfg, s),
        "own_handling": s.get("handling") or {},
        "suggested": suggest(db, cfg, s) if (s.get("status") == "new") else None,
        "channels": (extra or {}).get("streams", 0),
        "readings": (extra or {}).get("count", 0),
        "has_token": bool(s.get("push_hash")),
        "secrets": {k: {"secret": True, "set": k in (s.get("sealed") or {})} for k in STREAM_TYPES[s["type"]]["secrets"]},
        "health": s.get("health"),
        "last_seen_at": s.get("last_seen_at"),
        "created_at": s.get("created_at"),
        "created_by": s.get("created_by"),
    }


def list_all(db, cfg):
    rows = db.rows(f"SELECT {FIELDS} FROM storage_source ORDER BY id")
    watches, counts = {}, {}
    for w in db.rows("SELECT source FROM watch_path"):
        watches[w["source"]] = watches.get(w["source"], 0) + 1
    for st in db.rows("SELECT sensor, count() AS streams, math::sum(count) AS count FROM sensor_stream GROUP BY sensor"):
        counts[st["sensor"]] = st
    names = store.space_names(db)
    out = []
    for s in rows:
        if s["type"] not in sources.BACKENDS and s["type"] not in STREAM_TYPES:
            continue
        extra = {"watches": watches.get(s["id"], 0)} if family(s["type"]) == "files" else (counts.get(s["id"]) or {})
        out.append(view(db, cfg, s, names, extra))
    return out


def detail(db, cfg, sid):
    s = get(db, sid)
    if s["type"] not in sources.BACKENDS and s["type"] not in STREAM_TYPES:
        raise KeyError(sid)
    if family(s["type"]) == "files":
        ws = [w for w in sources.list_watches(db) if w["source"] == s["id"]]
        return {**view(db, cfg, s, extra={"watches": len(ws)}), "watches": ws, "streams": []}
    st = streams(db, sid)
    return {**view(db, cfg, s, extra={"streams": len(st), "count": sum(x.get("count") or 0 for x in st)}), "streams": st, "watches": []}


# ---------- suggestions ----------
def suggest(db, cfg, s, st=None):
    """How a new stream sensor might be handled, from what it has sent so far: {handling, reason}. Rules, not a model."""
    st = st if st is not None else streams(db, s["id"])
    kinds = {x.get("kind") for x in st}
    count = sum(x.get("count") or 0 for x in st)
    first = min((x.get("created_at") for x in st if x.get("created_at")), default=None)
    minutes = 1.0
    if first:
        try:
            minutes = max(1.0, (dt.datetime.now(dt.timezone.utc) - sources._when(first)).total_seconds() / 60)
        except (TypeError, ValueError):
            pass
    rate = count / minutes
    if s["type"] == "bridge":
        return {"handling": {}, "reason": "A broker brings its devices in as sensors of their own: handle those."}
    if not st:
        return {"handling": {"store": "all"}, "reason": "Nothing has arrived yet: keep everything for now."}
    if kinds & {"log", "text"}:
        return {
            "handling": {"store": "all", "raw_days": 14, "important_days": 180, "rollup_days": 365},
            "reason": "Log lines: keep everything for two weeks, warnings and errors for six months, and hourly counts for a year.",
        }
    if kinds <= {"number", "boolean"} and rate > 2:
        return {
            "handling": {"store": "changes", "raw_days": 7, "rollup_days": 730},
            "reason": f"About {round(rate)} readings a minute that mostly repeat: keep only changes for a week, and hourly "
            "summaries for two years.",
        }
    if "json" in kinds:
        return {
            "handling": {"store": "all", "raw_days": 14, "rollup_days": 730},
            "reason": "Structured readings: keep them for two weeks, and hourly summaries of their numbers for two years.",
        }
    return {
        "handling": {"store": "all", "raw_days": 30, "rollup_days": 730},
        "reason": "Occasional readings: keep them for a month, and hourly summaries for two years.",
    }


def apply_suggestion(db, cfg, sid):
    s = get(db, sid)
    if family(s["type"]) != "stream":
        raise ValueError("only stream sensors have suggestions")
    sug = suggest(db, cfg, s)
    update(db, sid, handling=sug["handling"], status="active")
    return sug


# ---------- readings ----------
def readings(db, sid, stream=None, before=None, limit=100):
    q = "SELECT record::id(id) AS id, stream, at, value, text, fields, level FROM sensor_reading WHERE sensor = $s"
    if stream:
        q += " AND stream = $k"
    if before:
        q += " AND at < $b"
    rows = db.rows(q + " ORDER BY at DESC LIMIT $n", s=int(sid), k=stream, b=before, n=int(limit))
    names = {x["id"]: x["name"] for x in streams(db, sid)}
    for r in rows:
        r["stream_name"] = names.get(r.get("stream"))
    return rows


def series(db, sid, stream, field=None, hours=168):
    """Hourly summaries of a stream (of one of its JSON fields): [{hour, n, min, max, avg, last}], oldest first."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=int(hours))).isoformat(timespec="hours")[:13]
    rows = db.rows(
        "SELECT hour, n, sum, min, max, last FROM sensor_rollup WHERE sensor = $s AND stream = $k AND field = $f AND hour >= $h ORDER BY hour",
        s=int(sid),
        k=stream,
        f=field or "",
        h=since,
    )
    return [{**r, "avg": (r["sum"] / r["n"]) if r.get("n") and r.get("sum") is not None else None} for r in rows]


# ---------- retention ----------
def _count(db, sql, **v):
    row = db.one(f"SELECT count() AS n FROM {sql} GROUP ALL", **v)
    return int((row or {}).get("n") or 0)


def tidy(db, cfg, ids=None, now=None, say=None):
    """Drop readings and rollups past each stream sensor's retention: {sensors, readings, rollups} removed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    say = say or (lambda *a: None)
    rows = [
        s for s in db.rows(f"SELECT {FIELDS} FROM storage_source WHERE type IN $t", t=list(STREAM_TYPES)) if ids is None or s["id"] in ids
    ]
    total = {"sensors": 0, "readings": 0, "rollups": 0}
    for s in rows:
        h = handling(cfg, s)
        raw, imp, roll = h.get("raw_days"), h.get("important_days"), h.get("rollup_days")
        gone = 0
        if raw:
            cut = _ts(now - dt.timedelta(days=raw))
            plain = "sensor_reading WHERE sensor = $s AND at < $c AND (level = NONE OR level > $lv)"
            gone += _count(db, plain, s=s["id"], c=cut, lv=IMPORTANT_LEVEL)
            db.q(f"DELETE {plain}", s=s["id"], c=cut, lv=IMPORTANT_LEVEL)
            keep = max(raw, imp) if imp else None  # important lines are kept for the longer of the two
            if keep:
                kcut = _ts(now - dt.timedelta(days=keep))
                important = "sensor_reading WHERE sensor = $s AND at < $c"
                gone += _count(db, important, s=s["id"], c=kcut)
                db.q(f"DELETE {important}", s=s["id"], c=kcut)
        keep = max(x for x in (raw, imp, roll) if x) if any((raw, imp, roll)) else None
        if keep:  # patterns not seen since, unless someone labelled them or chose their action
            db.q(
                "DELETE sensor_pattern WHERE sensor = $s AND last_at < $c AND action = NONE AND (label = NONE OR label_by IN ['jev', 'laya', 'llm'])",
                s=s["id"],
                c=_ts(now - dt.timedelta(days=keep)),
            )
        rgone = 0
        if roll:
            hcut = (now - dt.timedelta(days=roll)).isoformat(timespec="hours")[:13]
            q = "sensor_rollup WHERE sensor = $s AND hour < $h"
            rgone = _count(db, q, s=s["id"], h=hcut)
            db.q(f"DELETE {q}", s=s["id"], h=hcut)
        total["sensors"] += 1
        total["readings"] += gone
        total["rollups"] += rgone
        if gone or rgone:
            say(f"{s['name']}: {gone} readings and {rgone} hourly summaries past their time")
    return total


def any_streams(db):
    return bool(db.one("SELECT id FROM storage_source WHERE type IN $t LIMIT 1", t=list(STREAM_TYPES)))


# ---------- taking readings in ----------
class Intake:
    """Readings on their way into the database: kept in memory and written in batches (every FLUSH_SECONDS or
    FLUSH_ROWS), with each stream's rollups and counts added up in between. Safe to use from many threads."""

    def __init__(self, db, cfg_fn, log=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log or (lambda *a: None)
        self.lock = threading.Lock()
        self.finding = threading.Lock()
        self.rows: list = []
        self.rollups: dict = {}
        self.patterns: dict = {}
        self.dropping: set = set()  # patterns whose lines are counted but not kept
        self.known: dict = {}  # stream -> the patterns it has
        self.stats: dict = {}
        self.seen: dict = {}
        self.kept: dict = {}  # stream -> (what was last stored, when): for store=changes
        self.buckets: dict = {}  # stream -> [allowance, at]: for max_per_minute
        self.cache: dict = {}
        self.prefixes: list = []
        self.version = None
        self.checked = 0.0
        self.last = None

    def _stamp(self):
        """Now, later than any reading this intake stamped before, so readings keep their order."""
        t = dt.datetime.now(dt.timezone.utc)
        if self.last is not None and t <= self.last:
            t = self.last + dt.timedelta(microseconds=1)
        self.last = t
        return _ts(t)

    def _fresh(self):
        """Forget sensors held in memory when someone changed one, or every CACHE_SECONDS."""
        if time.monotonic() - self.checked < 1:
            return
        self.checked = time.monotonic()
        v = self.db.values("SELECT VALUE n FROM $r", r=R("seq", "sensors"))
        if v != self.version or time.monotonic() - getattr(self, "loaded", 0) > CACHE_SECONDS:
            self.version, self.loaded = v, time.monotonic()
            self.cache.clear()
            rows = self.db.rows("SELECT key, params FROM storage_source WHERE type = 'mqtt'")
            self.prefixes = sorted(((r["params"] or {}).get("prefix") or "" for r in rows), key=len, reverse=True)
            self.dropping = set(self.db.values("SELECT VALUE record::id(id) FROM sensor_pattern WHERE action = 'drop'"))
            self.known.clear()  # retention may have removed some

    def sensor(self, typ, key, name, device, params, space=None):
        with self.lock:
            self._fresh()
            got = self.cache.get(key)
        if got:
            return got
        with self.finding:  # two messages from a new device at once make one sensor
            got = self.cache.get(key)
            if got:
                return got
            s = discover(self.db, typ, key, name, device, params, space)
            with self.lock:
                self.cache[key] = s
                if typ == "mqtt" and params.get("prefix") not in self.prefixes:
                    self.prefixes = sorted([*self.prefixes, params["prefix"]], key=len, reverse=True)
        return s

    def mqtt_sensor(self, topic, space=None):
        with self.lock:
            self._fresh()
            prefix = next((p for p in self.prefixes if p and (topic == p or topic.startswith(p + "/"))), None)
        device = prefix or mqtt_device(topic)
        return self.sensor("mqtt", f"mqtt:{device}", device, device, {"prefix": device}, space)

    def _known(self, k):
        """The patterns a stream has (read once per process)."""
        got = self.known.get(k)
        if got is None:
            got = self.known[k] = set(
                self.db.values("SELECT VALUE record::id(id) FROM sensor_pattern WHERE stream = $k LIMIT $n", k=k, n=MAX_PATTERNS + 1)
            )
        return got

    def _allow(self, k, per_minute):
        if not per_minute:
            return True
        now = time.monotonic()
        b = self.buckets.setdefault(k, [float(per_minute), now])
        b[0] = min(float(per_minute), b[0] + (now - b[1]) * per_minute / 60.0)
        b[1] = now
        if b[0] < 1:
            return False
        b[0] -= 1
        return True

    def put(self, sensor, stream, payload, at=None, level=None, log=False):
        """One reading of `sensor`'s `stream`. `log` marks a log line (text, with a syslog `level`)."""
        cfg = self.cfg_fn()
        h = handling(cfg, sensor)
        sid = sensor["id"]
        stream = (stream or "default")[:255]
        k = stream_key(sid, stream)
        with self.lock:
            when = at or self._stamp()
        got = (
            {"kind": "log", "text": (payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload))[:MAX_TEXT]}
            if log
            else read(payload)
        )
        tmpl = pattern(got["text"]) if got["kind"] in ("log", "text") else None
        with self.lock:
            self._fresh()  # which patterns are dropped, whichever way the reading came in
            known = self._known(k) if tmpl else set()
            pid = pattern_id(k, tmpl) if tmpl else None
            if pid and pid not in known:
                if len(known) >= MAX_PATTERNS:  # lines too varied to group: one catch-all pattern, not a row each
                    tmpl, pid = OTHER_PATTERN, pattern_id(k, OTHER_PATTERN)
                known.add(pid)
            if pid:
                pt = self.patterns.setdefault(
                    pid, {"sensor": sid, "stream": k, "template": tmpl, "example": got["text"][:500], "n": 0, "first": when}
                )
                pt["n"] += 1
                pt["last"] = when
            st = self.stats.setdefault(
                k, {"sensor": sid, "name": stream, "count": 0, "stored": 0, "dropped": 0, "fields": set(), "first": when}
            )
            st["count"] += 1
            st["kind"], st["last_at"] = got["kind"], when
            if "value" in got:
                st["last_value"] = got["value"]
            if got.get("text") is not None:
                st["last_text"] = got["text"][:500]
            st["fields"].update((got.get("fields") or {}).keys())
            self.seen[sid] = when
            if (
                (sensor.get("status") or "active") in ("paused", "ignored")
                or h["store"] == "none"
                or (pid and pid in self.dropping)
                or not self._allow(k, h.get("max_per_minute"))
            ):
                st["dropped"] += 1
                return False
            hour = when[:13]
            numbers = [("", got["value"])] if "value" in got else []
            numbers += sorted((got.get("fields") or {}).items())
            if not numbers:
                numbers = [("", None)]  # a line of text: counted per hour
            for field, v in numbers:
                rid = f"{k}-{_field_id(field)}-{hour.replace('-', '').replace('T', '')}"
                r = self.rollups.setdefault(
                    rid,
                    {"sensor": sid, "stream": k, "field": field, "hour": hour, "n": 0, "sum": None, "min": None, "max": None, "last": None},
                )
                r["n"] += 1
                lv = v if v is not None else (float(level) if level is not None else None)
                if lv is not None:
                    r["sum"] = (r["sum"] or 0.0) + lv
                    r["min"] = lv if r["min"] is None else min(r["min"], lv)
                    r["max"] = lv if r["max"] is None else max(r["max"], lv)
                    r["last"] = lv
            if h["store"] == "summary":
                return True
            if h["store"] == "changes":
                same = got.get("value", got.get("text"))
                last = self.kept.get(k)
                if last and last[0] == same and time.monotonic() - last[1] < CHANGES_EVERY:
                    return True
                self.kept[k] = (same, time.monotonic())
            row = {"sensor": sid, "stream": k, "at": when}
            for f in ("value", "text", "fields"):
                if got.get(f) is not None and not (f == "text" and got["kind"] == "boolean"):
                    row[f] = got[f]
            if level is not None:
                row["level"] = int(level)
            if pid:
                row["pattern"] = pid
            self.rows.append(row)
            st["stored"] += 1
            full = len(self.rows) >= FLUSH_ROWS
        if full:
            self.flush()
        return True

    def flush(self):
        with self.lock:
            rows, self.rows = self.rows, []
            rollups, self.rollups = self.rollups, {}
            patterns, self.patterns = self.patterns, {}
            stats, self.stats = self.stats, {}
            seen, self.seen = self.seen, {}
        db = self.db
        try:
            for i in range(0, len(rows), FLUSH_ROWS):
                db.q("INSERT INTO sensor_reading $rows", rows=rows[i : i + FLUSH_ROWS])
            for rid, r in rollups.items():
                db.q(
                    "UPSERT $r SET sensor = $sensor, stream = $stream, field = $field, hour = $hour, n += $n, "
                    "sum = IF $sum = NONE THEN sum ELSE IF sum = NONE THEN $sum ELSE sum + $sum END, "
                    "min = IF $min = NONE THEN min ELSE IF min = NONE THEN $min ELSE math::min([min, $min]) END, "
                    "max = IF $max = NONE THEN max ELSE IF max = NONE THEN $max ELSE math::max([max, $max]) END, "
                    "last = IF $last = NONE THEN last ELSE $last END",
                    r=R("sensor_rollup", rid),
                    **r,
                )
            for pid, pt in patterns.items():
                db.q(
                    "UPSERT $r SET sensor = $sensor, stream = $stream, template = $template, count += $n, last_at = $last, "
                    "example = IF example = NONE THEN $example ELSE example END, "
                    "first_at = IF first_at = NONE THEN $first ELSE first_at END",
                    r=R("sensor_pattern", pid),
                    **pt,
                )
            for k, st in stats.items():
                db.q(
                    "UPSERT $r SET sensor = $sensor, name = $name, kind = $kind, last_at = $last_at, count += $count, "
                    "stored += $stored, dropped += $dropped, "
                    "fields = array::union(IF fields = NONE THEN [] ELSE fields END, $fields), "
                    "last_value = IF $last_value = NONE THEN last_value ELSE $last_value END, "
                    "last_text = IF $last_text = NONE THEN last_text ELSE $last_text END, "
                    "created_at = IF created_at = NONE THEN $first ELSE created_at END",
                    r=R("sensor_stream", k),
                    **{**st, "fields": sorted(st["fields"]), "last_value": st.get("last_value"), "last_text": st.get("last_text")},
                )
            for sid, when in seen.items():
                db.q("UPDATE $r SET last_seen_at = $t", r=R("storage_source", sid), t=when)
        except Exception as e:  # noqa: BLE001 - lose this batch rather than stop taking readings
            self.log(f"sensors: couldn't save {len(rows)} readings: {type(e).__name__}: {e}")
        return len(rows)


_INTAKES: dict = {}
_IL = threading.Lock()


def intake(db, cfg_fn, log=None):
    """This process's intake for a database."""
    with _IL:
        it = _INTAKES.get(id(db))
        if it is None or it.db is not db:
            it = _INTAKES[id(db)] = Intake(db, cfg_fn, log)
        return it


# ---------- webhooks ----------
def by_token(db, token):
    if not token:
        raise KeyError("token")
    row = db.one(f"SELECT {FIELDS} FROM storage_source WHERE push_hash = $h LIMIT 1", h=_hash(token))
    if not row or row["type"] != "webhook":
        raise KeyError("token")
    return row


def push(db, cfg_fn, token, stream, body, content_type=""):
    """What a webhook was sent, taken in: a JSON body {readings: [{stream?, value | payload, at?}]} is several readings,
    any other JSON one, and plain text one per line. How many were kept."""
    s = by_token(db, token)
    it = intake(db, cfg_fn)
    kept = 0
    items = []
    if "json" in (content_type or "") or body[:1] in (b"{", b"["):
        try:
            obj = json.loads(body or b"null")
        except ValueError:
            raise ValueError("the body isn't JSON") from None
        if isinstance(obj, dict) and isinstance(obj.get("readings"), list):
            for r in obj["readings"][:1000]:
                if isinstance(r, dict):
                    v = r.get("value", r.get("payload"))
                    items.append((r.get("stream") or stream, v if isinstance(v, str) else json.dumps(v)))
        else:
            items.append((stream, body.decode("utf-8", "replace")))
    else:
        items = [(stream, line) for line in body.decode("utf-8", "replace").splitlines() if line.strip()][:1000]
    for st, payload in items:
        kept += bool(it.put(s, str(st or "default"), payload))
    it.flush()
    return kept


# ---------- hub logins ----------
def logins(db):
    names = store.space_names(db)
    rows = db.rows("SELECT record::id(id) AS id, username, space, enabled, created_at, last_seen_at FROM sensor_login ORDER BY username")
    return [{**r, "namespace": names.get(r.get("space"))} for r in rows]


def create_login(db, username, password, space=None, user=None):
    username = (username or "").strip()
    if not NAME_RX.match(username):
        raise ValueError("a username is letters, digits and . _ @ + -, up to 64")
    if len(password or "") < 8:
        raise ValueError("a password has at least 8 characters")
    if db.one("SELECT id FROM sensor_login WHERE username = $u", u=username):
        raise ValueError("that username is taken")
    lid = db.next_id("sensor_login")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("sensor_login", lid),
        d=store.clean(
            {
                "username": username,
                "password": auth.hash_password(password),
                "space": _check_space(db, space),
                "enabled": True,
                "created_at": store.now(),
                "created_by": user,
            }
        ),
    )
    return lid


def remove_login(db, lid):
    if not db.one("SELECT id FROM $r", r=R("sensor_login", int(lid))):
        raise KeyError(lid)
    db.q("DELETE $r", r=R("sensor_login", int(lid)))


_LOGINS: dict = {}


def authenticate(db, cfg, username, password):
    """What a hub client runs as: {login, space} or {anonymous} when allowed; None to refuse."""
    if not username:
        return {"anonymous": True} if (cfg.get("sensors") or {}).get("mqtt_anonymous") else None
    row = db.one("SELECT record::id(id) AS id, password, space, enabled FROM sensor_login WHERE username = $u", u=username)
    if not row or not row.get("enabled", True):
        return None
    memo = hashlib.sha256(f"{row['password']}:{password}".encode()).hexdigest()
    if _LOGINS.get(row["id"]) != memo:  # scrypt is slow on purpose; a device reconnecting needn't pay it each time
        if not auth.verify_password(password or "", row["password"]):
            return None
        _LOGINS[row["id"]] = memo
    db.q("UPDATE $r SET last_seen_at = $t", r=R("sensor_login", row["id"]), t=store.now())
    return {"login": row["id"], "space": row.get("space")}


# ---------- the hub ----------
class Hub:
    """The MQTT broker and syslog listener this process runs while sensors are on, following the settings as they
    change; and the intake writing what they take in."""

    def __init__(self, db, cfg_fn, log=None, name=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log or (lambda *a: None)
        self.name = name or f"{socket.gethostname()}-{id(self) % 10000}"
        self.intake = intake(db, cfg_fn, self.log)
        self.broker = self.listener = None
        self.state = {"mqtt": {}, "syslog": {}}
        self.retry = {"mqtt": 0.0, "syslog": 0.0}
        self.bridges: dict = {}  # sensor id -> {sig, stop, thread}

    def _on_mqtt(self, topic, payload, login, address):
        s = self.intake.mqtt_sensor(topic, (login or {}).get("space"))
        self.intake.put(s, topic, payload)

    def _on_syslog(self, msg, address):
        ip = address.split("%")[0]
        s = self.intake.sensor("syslog", f"syslog:{ip}", msg.get("host") or ip, msg.get("host") or ip, {"address": ip})
        self.intake.put(s, msg.get("app") or "syslog", msg.get("message") or "", level=msg.get("level"), log=True)

    def apply(self):
        """Start, stop or move the broker and listener to match the settings."""
        cfg = self.cfg_fn()
        s = cfg.get("sensors") or {}
        on = bool(s.get("enabled"))
        bind = s.get("bind") or "0.0.0.0"
        want = {
            "mqtt": (bind, int(s.get("mqtt_port", 1883)), int(s.get("max_payload_kb") or 256)) if on and s.get("mqtt") else None,
            "syslog": (bind, int(s.get("syslog_port", 5514)), tuple(s.get("syslog_networks") or ())) if on and s.get("syslog") else None,
        }
        for what in ("mqtt", "syslog"):
            have = self.state[what].get("want")
            running = (self.broker if what == "mqtt" else self.listener) is not None
            if want[what] == have and (running or want[what] is None):
                continue
            if running and want[what] != have:
                self._stop(what)
            if want[what] is None:
                self.state[what] = {}
                continue
            if time.monotonic() < self.retry[what] and want[what] == have:
                continue
            try:
                if what == "mqtt":
                    self.broker = mqtt.Broker(
                        want[what][0],
                        want[what][1],
                        lambda u, p, cid, addr: authenticate(self.db, self.cfg_fn(), u, p),
                        self._on_mqtt,
                        max_bytes=want[what][2] * 1024,
                        log=self.log,
                    ).start()
                    port = self.broker.port
                else:
                    self.listener = syslog.Listener(want[what][0], want[what][1], want[what][2], self._on_syslog, log=self.log).start()
                    port = self.listener.port
                self.state[what] = {"want": want[what], "running": True, "port": port, "since": store.now()}
                self.log(f"sensors: {what} listening on port {port}")
            except OSError as e:
                self.state[what] = {"want": want[what], "running": False, "error": f"port {want[what][1]}: {e.strerror or e}"}
                self.retry[what] = time.monotonic() + RETRY_SECONDS
        if self.listener is not None and want["syslog"]:
            self.listener.networks = list(want["syslog"][2])
        self._bridges(on)

    # ---------- bridges to other brokers ----------
    def _bridges(self, on):
        """Run a connection for every bridge sensor that isn't paused, one process each (the first to claim it)."""
        rows = self.db.rows(f"SELECT {FIELDS} FROM storage_source WHERE type = 'bridge'") if on else []
        wanted = {}
        for r in rows:
            if (r.get("status") or "active") in ("paused", "ignored"):
                continue
            sig = json.dumps([r.get("params"), r.get("sealed"), r.get("space")], sort_keys=True, default=str)
            if self._claim(r["id"]):
                wanted[r["id"]] = (r, sig)
        for bid in list(self.bridges):
            if bid not in wanted or self.bridges[bid]["sig"] != wanted[bid][1]:
                self.bridges.pop(bid)["stop"].set()
        for bid, (r, sig) in wanted.items():
            if bid not in self.bridges:
                stop = threading.Event()
                th = threading.Thread(target=self._bridge, args=(r, stop), daemon=True, name=f"mqtt-bridge-{bid}")
                self.bridges[bid] = {"sig": sig, "stop": stop, "thread": th}
                th.start()

    def _claim(self, bid):
        now = dt.datetime.now(dt.timezone.utc)
        return bool(
            self.db.rows(
                "UPDATE $r SET bridge_owner = $me, bridge_at = $t WHERE bridge_owner = NONE OR bridge_owner = $me OR bridge_at < $stale "
                "RETURN AFTER",
                r=R("storage_source", bid),
                me=self.name,
                t=_ts(now),
                stale=_ts(now - dt.timedelta(seconds=SERVICE_SECONDS * 6)),
            )
        )

    def _health(self, bid, ok, error=None):
        h = store.clean({"ok": ok, "checked_at": store.now(), "error": (error or "")[:300] or None})
        self.db.q("UPDATE $r SET health = $h", r=R("storage_source", bid), h=h)

    def _bridge(self, row, stop):
        bid, p = row["id"], row.get("params") or {}
        sealed = (row.get("sealed") or {}).get("pass")
        try:
            pw = settings.unseal(self.cfg_fn(), sealed, f"source:{bid}:pass") if sealed else None
        except Exception:  # noqa: BLE001 - the key changed since it was saved
            self._health(bid, False, "the saved password can't be read: give it again")
            return
        filters = [t.strip() for t in str(p.get("topics") or "#").split(",") if t.strip()]
        wait = BRIDGE_WAIT[0]

        def on_message(topic, payload):
            with self.intake.lock:
                self.intake.seen[bid] = _ts()
            self.intake.put(self.intake.mqtt_sensor(topic, row.get("space")), topic, payload)

        def on_connect():
            nonlocal wait
            wait = BRIDGE_WAIT[0]
            self._health(bid, True)
            self.log(f"sensors: bridge {row['name']} connected to {p.get('host')}")

        while not stop.is_set():
            client = mqtt.Client(
                p.get("host"),
                int(p.get("port") or 1883),
                p.get("user") or None,
                pw,
                tls=str(p.get("tls")).lower() == "true",
                client_id=f"lens-bridge-{bid}-{secrets.token_hex(3)}",
            )
            try:
                client.run(filters, on_message, stop, on_connect)
            except (OSError, mqtt.ProtocolError) as e:
                self._health(bid, False, f"{p.get('host')}: {e}")
            except Exception as e:  # noqa: BLE001 - anything else: say so, and try again
                self.log(f"sensors: bridge {row['name']}: {type(e).__name__}: {e}")
                try:
                    self._health(bid, False, f"{type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
            if stop.wait(wait):
                break
            wait = min(wait * 2, BRIDGE_WAIT[1])

    def _stop(self, what):
        thing = self.broker if what == "mqtt" else self.listener
        if thing is not None:
            try:
                thing.stop()
            except Exception:  # noqa: BLE001
                pass
        if what == "mqtt":
            self.broker = None
        else:
            self.listener = None
        self.state[what] = {}

    def heartbeat(self):
        if not self.state["mqtt"] and not self.state["syslog"] and not self.bridges:  # sensors are off: say nothing, once
            if getattr(self, "said", True):
                self.db.q("DELETE $r", r=R("sensor_service", self.name))
                self.said = False
            return
        self.said = True
        st = {
            "process": self.name,
            "at": store.now(),
            "mqtt": {k: v for k, v in self.state["mqtt"].items() if k != "want"},
            "syslog": {k: v for k, v in self.state["syslog"].items() if k != "want"},
            "bridges": sorted(self.bridges),
        }
        if self.broker is not None:
            st["mqtt"]["clients"] = self.broker.clients()
        self.db.q("UPSERT $r CONTENT $d", r=R("sensor_service", self.name), d=st)

    def close(self):
        self._stop("mqtt")
        self._stop("syslog")
        for b in self.bridges.values():
            b["stop"].set()
        self.bridges.clear()
        self.intake.flush()

    def loop(self, stop):
        last = 0.0
        while True:
            try:
                if time.monotonic() - last >= SERVICE_SECONDS:
                    last = time.monotonic()
                    self.apply()
                    self.heartbeat()
                self.intake.flush()
            except Exception as e:  # noqa: BLE001 - keep the hub up
                self.log(f"sensors: {type(e).__name__}: {e}")
            if stop.wait(FLUSH_SECONDS):
                break
        self.close()


def start(db, cfg_fn, stop, log=None, name=None):
    """The sensor hub thread, until `stop` is set. It does nothing while sensors are off."""
    hub = Hub(db, cfg_fn, log, name)
    th = threading.Thread(target=hub.loop, args=(stop,), daemon=True, name="sensor-hub")
    th.start()
    return hub


def hub_status(db, cfg):
    """Which processes run the hub, and what they listen on (those heard from in the last minute)."""
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)).isoformat(timespec="seconds")
    s = cfg.get("sensors") or {}
    rows = db.rows("SELECT * FROM sensor_service WHERE at >= $c", c=cut)
    for r in rows:
        r.pop("id", None)
    return {
        "enabled": bool(s.get("enabled")),
        "mqtt": bool(s.get("mqtt")),
        "mqtt_port": s.get("mqtt_port"),
        "syslog": bool(s.get("syslog")),
        "syslog_port": s.get("syslog_port"),
        "processes": rows,
        "new": len(db.values("SELECT VALUE id FROM storage_source WHERE status = 'new'")),
    }
