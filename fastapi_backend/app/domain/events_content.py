"""Content as activities (docs/automations.md, Sources and Kinds): the `recordings` source reads the assets Lens keeps
(the `recording` table: audio, video, documents, images, emails, web pages) by `created_at`, the id breaking ties, so
nothing is stored twice.

Each new asset is one activity, `create-recording-<id>`, the same every time it's read, of the most specific kind its
row tells: an email received from a mailbox (`email.received`), a calendar event (`calendar_event.added`), a file
from storage or Drive (`file.added`), an upload (`upload.added`), a page or video saved from the web
(`web_page.saved`), something imported (pasted text, a transcript, an import hook's: `import.received`), else
`asset.added`, their parent. The kind is read from the row's content type, where it came from and who brought it,
never from its file.

Its actor is whoever brought it: an automation (its first job was queued by `automation:<id>`), the storage source
or mailbox it came from, an import hook, the person whose upload, clip or import it was, or Lens itself (a folder
scan). Labels like `watch:<id>`, a person's email or `routine:<id>` are looked up a page at a time, a few queries for
all of them, and their names kept for NAME_SECONDS (`actors`), so other sources name their actors the same way.

Mail addressing and an event's times are stored on the recording when it's made (`mail`, `event`: sources.py) and
read from there; an email's own details (`email`: subject, who, a snippet, its attachments) once it's been read.
Older rows without them are read without those fields.
"""

from __future__ import annotations

import mimetypes
import pathlib
import re
import threading
import time
import urllib.parse
import weakref

from . import events, store

R = store.R
FIELDS = (
    "id AS rid, record::id(id) AS id, space, title, source, media, remote, content_type, created_at, path, web, engine, "
    "created_by, mail, event, email, size, duration_ms, attached_to, sensor, form"
)
TYPES = {"audio": "Audio", "video": "Video", "image": "Image", "document": "Document", "transcript": "Document"}
AUTOMATION_RX = re.compile(r"^automation:(\d+)$")
LABEL_RX = re.compile(r"^(account|watch|source|routine|import_hook|sensor|automation):(\d+)$")
HOOK_RX = re.compile(r"^import hook: (.{1,80})$")
KEY_RX = re.compile(r"^[A-Za-z0-9_.:@+-]{1,200}$")
UPLOAD_RX = re.compile(r"/uploads/[^/]+/[0-9a-f]{24}/[^/]+$")  # uploads.py keeps each upload in <ns>/<upload id>/<name>
MAIL_SOURCES = {"imap", "gmail"}
CALENDAR_SOURCES = {"ical", "gcal"}
NAME_SECONDS = 30  # how long a looked-up name is used before it's read again
APPS = {
    "assistant": {"type": "Application", "id": "lens:assistant", "name": "Assistant"},
    "mcp": {"type": "Application", "id": "lens:mcp", "name": "An agent (MCP)"},
}
MOST = {"from": 20, "to": 50, "cc": 50, "attendees": 50}  # addresses an activity carries at most


# ---------- names, a page at a time ----------
_names: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # per database: {(table, key): (when, row)}
_names_lock = threading.Lock()


def _lookup(db, table, keys, fields="name", by="id"):
    """{key: row} for these keys of a table (record ids, or the values of the indexed field `by`), one query for the
    ones not looked up in the last NAME_SECONDS."""
    keys = [k for k in dict.fromkeys(keys) if k is not None]
    now, out, todo = time.monotonic(), {}, []
    with _names_lock:
        mine = _names.setdefault(db, {})
        for k in keys:
            hit = mine.get((table, by, k))
            if hit and now - hit[0] < NAME_SECONDS:
                if hit[1] is not None:
                    out[k] = hit[1]
            else:
                todo.append(k)
    if todo:
        if by == "id":
            rows = db.rows(f"SELECT record::id(id) AS key, {fields} FROM {table} WHERE id IN $k", k=[R(table, k) for k in todo])
        else:
            rows = db.rows(f"SELECT record::id(id) AS id, {by} AS key, {fields} FROM {table} WHERE {by} IN $k", k=todo)
        got = {r["key"]: r for r in rows}
        with _names_lock:
            mine = _names.setdefault(db, {})
            if len(mine) > 20000:
                mine.clear()
            for k in todo:
                mine[(table, by, k)] = (now, got.get(k))
        out.update(got)
    return out


def forget_names(db=None):
    """Look every name up again (tests; a rename shows at once)."""
    with _names_lock:
        if db is None:
            _names.clear()
        else:
            _names.pop(db, None)


def _num(v):
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def actors(db, labels):
    """{(label, space): actor} for who did something, as Lens writes it down: an account id, a person's email,
    `watch:<id>` and `source:<id>` (a storage source), `routine:<id>`, `import_hook:<id>` or `import hook: <name>`
    (the hook of that namespace), `sensor:<id>`, `automation:<id>`, `assistant` and `mcp`. Anything else, or None, is
    Lens. A few queries for all of them."""
    labels = list(dict.fromkeys(labels))
    want: dict[str, set] = {
        "account": set(),
        "email": set(),
        "watch": set(),
        "source": set(),
        "routine": set(),
        "hook": set(),
        "name": set(),
    }
    for label, _ in labels:
        if _num(label) is not None:
            want["account"].add(label)
            continue
        s = str(label or "")
        m = LABEL_RX.match(s)
        if m:
            table, n = m.group(1), int(m.group(2))
            key = {
                "account": "account",
                "watch": "watch",
                "source": "source",
                "sensor": "source",
                "routine": "routine",
                "import_hook": "hook",
            }
            if table in key:
                want[key[table]].add(n)
        elif HOOK_RX.match(s):
            want["name"].add(HOOK_RX.match(s).group(1))
        elif "@" in s:
            want["email"].add(s.strip().lower())
    watches = _lookup(db, "watch_path", want["watch"], "source")
    want["source"] |= {w["source"] for w in watches.values() if _num(w.get("source")) is not None}
    sources = _lookup(db, "storage_source", want["source"], "name, type")
    accounts = _lookup(db, "account", want["account"], "name, email")
    people = _lookup(db, "account", want["email"], "name", by="email")
    routines = _lookup(db, "routine", want["routine"])
    hooks = _lookup(db, "import_hook", want["hook"], "name, space")
    named: dict = {}
    if want["name"]:
        for h in db.rows("SELECT record::id(id) AS id, name, space FROM import_hook WHERE name IN $n", n=sorted(want["name"])):
            named.setdefault(h["name"], []).append(h)

    def service(table, n, name, fallback):
        return {"type": "Service", "id": f"lens:{table}/{n}", "name": name or f"{fallback} {n}"}

    def one(label, space):
        n = _num(label)
        if n is not None:
            a = accounts.get(n)
            return {"type": "Person", "id": f"lens:account/{n}", "name": (a or {}).get("name") or (a or {}).get("email") or "Someone"}
        s = str(label or "").strip()
        m = LABEL_RX.match(s)
        if m:
            table, n = m.group(1), int(m.group(2))
            if table == "automation":
                return {"type": "Application", "id": f"lens:automation/{n}", "name": f"Automation {n}"}
            if table == "account":
                return one(n, space)
            if table == "watch":
                sid = _num((watches.get(n) or {}).get("source"))
                return service("source", sid, (sources.get(sid) or {}).get("name"), "Source") if sid is not None else dict(events.LENS)
            if table == "source":
                return service("source", n, (sources.get(n) or {}).get("name"), "Source")
            if table == "sensor":
                return service("sensor", n, (sources.get(n) or {}).get("name"), "Sensor")
            if table == "routine":
                return {"type": "Application", "id": f"lens:routine/{n}", "name": (routines.get(n) or {}).get("name") or f"Routine {n}"}
            return service("import_hook", n, (hooks.get(n) or {}).get("name"), "Import hook")
        m = HOOK_RX.match(s)
        if m:
            found = named.get(m.group(1)) or []
            h = next((h for h in found if h.get("space") == space), found[0] if found else None)
            if h:
                return service("import_hook", h["id"], h["name"], "Import hook")
            return {"type": "Service", "id": "lens:import_hook", "name": m.group(1)}
        if s in APPS:
            return dict(APPS[s])
        if "@" in s:
            p = people.get(s.lower())
            if p:
                return {"type": "Person", "id": f"lens:account/{p['id']}", "name": p.get("name") or s}
            return {"type": "Person", "id": f"lens:email/{s.lower()}" if KEY_RX.match(s) else "lens:email", "name": s[:200]}
        return dict(events.LENS)

    return {(label, space): one(label, space) for label, space in labels}


# ---------- what an asset is ----------
_types: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _content_types(db):
    """Every content type (content_types.all_types), kept for NAME_SECONDS."""
    from . import content_types

    now = time.monotonic()
    with _names_lock:
        hit = _types.get(db)
    if hit and now - hit[0] < NAME_SECONDS:
        return hit[1]
    got = content_types.all_types(db)
    with _names_lock:
        _types[db] = (now, got)
    return got


def _content_type(db, r, types):
    """The asset's content type key, as content_types.of_recording says it, without a query per asset."""
    from . import content_types

    if r.get("content_type"):
        chosen = next((t for t in types if t["key"] == r["content_type"]), None)
        if chosen and chosen["base"] == content_types.base_of(r):
            return chosen["key"]
    t = content_types.recognise(db, r, types)
    return t["key"] if t else None


def _first_jobs(db, ids):
    """{recording: who queued its first job}."""
    out: dict = {}
    for j in db.rows("SELECT recording, created_by, created_at FROM job WHERE recording IN $ids ORDER BY created_at", ids=ids):
        out.setdefault(j["recording"], j.get("created_by"))
    return out


def _addresses(v, most):
    """Addresses, lowercased, from a list of them or a header's text ("Ann <ann@x.org>, bob@y.org")."""
    import email.utils

    if isinstance(v, str):
        v = [a for _, a in email.utils.getaddresses([v])]
    out = [str(a).strip().lower() for a in v or [] if isinstance(a, str) and "@" in a]
    return list(dict.fromkeys(out))[:most]


def _text(v, most=300):
    s = " ".join(str(v or "").split())
    return s[:most] or None


def _mail(r):
    """An email's fields: what its mailbox said when it came (`mail`), else what reading it found (`email`)."""
    mail, got = r.get("mail") if isinstance(r.get("mail"), dict) else {}, r.get("email") if isinstance(r.get("email"), dict) else {}
    box = mail.get("mailbox")
    return store.clean(
        {
            "from": _addresses(mail.get("from") or got.get("from"), MOST["from"]) or None,
            "to": _addresses(mail.get("to") or got.get("to"), MOST["to"]) or None,
            "cc": _addresses(mail.get("cc") or got.get("cc"), MOST["cc"]) or None,
            "subject": _text(mail.get("subject") or got.get("subject") or r.get("title")),
            "list": _text(mail.get("list"), 200),
            "mailbox": _text(", ".join(str(b) for b in box) if isinstance(box, list) else box, 200),
            "date": got.get("date") or mail.get("date"),
            "snippet": _text(mail.get("snippet") or got.get("snippet")),
            "attachments": got.get("attachments") if _num(got.get("attachments")) is not None else None,
        }
    )


def _event(r):
    ev = r.get("event") if isinstance(r.get("event"), dict) else {}
    return store.clean(
        {
            "startTime": ev.get("startTime"),
            "endTime": ev.get("endTime"),
            "location": _text(ev.get("location"), 300),
            "organizer": _text(ev.get("organizer"), 200),
            "attendees": [str(a)[:200] for a in ev.get("attendees") or []][: MOST["attendees"]] or None,
            "calendar": _text(ev.get("calendar"), 200),
        }
    )


def _file(r, kind):
    """A file's fields: its name in its source (or the name it was uploaded with), media type, size and length."""
    remote = r.get("remote") or {}
    path = remote.get("path") if kind == "file.added" else pathlib.PurePosixPath(str(r.get("path") or "")).name
    media_type = mimetypes.guess_type(str(path or ""))[0]
    return store.clean(
        {
            "path": str(path)[:500] if path else None,
            "mediaType": media_type,
            "size": _num(r.get("size")),
            "duration": round(r["duration_ms"] / 1000, 3) if isinstance(r.get("duration_ms"), (int, float)) and r["duration_ms"] else None,
        }
    )


def _web(r):
    url = str((r.get("web") or {}).get("url") or "")
    return store.clean({"href": url[:2000] or None, "site": (urllib.parse.urlsplit(url).hostname or None) if url else None})


def _imported(r, actor):
    """How an import came: an import hook, pasted text, or a file read as text."""
    if actor["id"].startswith("lens:import_hook"):
        return "hook"
    path = str(r.get("path") or "")
    return "paste" if path.startswith("paste:") else "file"


def _kind(r, ctype, source_type, actor):
    """The most specific kind the row tells (module docstring)."""
    if r.get("attached_to"):
        return "asset.added"  # a file an email or archive held: the email is what arrived
    if ctype == "email" or source_type in MAIL_SOURCES:
        return "email.received"
    if ctype == "calendar_event" or source_type in CALENDAR_SOURCES:
        return "calendar_event.added"
    if actor["id"].startswith("lens:import_hook"):
        return "import.received"
    if (r.get("web") or {}).get("url"):
        return "web_page.saved"
    if (r.get("remote") or {}).get("source") is not None:
        return "file.added"
    if UPLOAD_RX.search(str(r.get("path") or "")):
        return "upload.added"
    if str(r.get("engine") or "").startswith("import:") or str(r.get("path") or "").startswith(("paste:", "hook:")):
        return "import.received"
    return "asset.added"


def _summary(kind, actor, obj):
    who, title = actor["name"], obj["name"][:200]
    if kind == "email.received":
        sender = (obj.get("from") or [None])[0]
        return f"{who} received “{title}”" + (f" from {sender}" if sender else "")
    if kind == "calendar_event.added":
        start = str(obj.get("startTime") or "")
        return f"{who} added the event “{title}”" + (f" on {start[:10]}" if start else "")
    if kind == "upload.added":
        return f"{who} uploaded “{title}”"
    if kind == "web_page.saved":
        return f"{who} saved “{title}”" + (f" from {obj['site']}" if obj.get("site") else "")
    if kind == "import.received":
        return f"{who} imported “{title}”"
    return f"{who} added “{title}”"


def _label_of(r, first, upload):
    """Who brought it, as Lens wrote it down, in the order that says most: an automation, the storage source, whoever
    made it (a web clip's person), the upload's account, a sensor, then whoever queued its first job."""
    if AUTOMATION_RX.match(str(first or "")):
        return first
    sid = (r.get("remote") or {}).get("source")
    if _num(sid) is not None:
        return f"source:{sid}"
    if r.get("created_by"):
        return r["created_by"]
    if upload and (upload.get("account") is not None or upload.get("email")):
        return upload["account"] if upload.get("account") is not None else upload["email"]
    if _num(r.get("sensor")) is not None:
        return f"sensor:{r['sensor']}"
    return first


def _activities(db, rows):
    """The rows as activities of their kinds (a row whose created_at isn't a time is skipped)."""
    from . import render

    rows = [r for r in rows if r]
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    first = _first_jobs(db, ids)
    uploads = {u["recording"]: u for u in db.rows("SELECT recording, account, email FROM upload WHERE recording IN $ids", ids=ids)}
    labels = {r["id"]: (_label_of(r, first.get(r["id"]), uploads.get(r["id"])), r.get("space")) for r in rows}
    who = actors(db, labels.values())
    srcs = _lookup(
        db,
        "storage_source",
        [(r.get("remote") or {}).get("source") for r in rows if _num((r.get("remote") or {}).get("source")) is not None],
        "name, type",
    )
    types = _content_types(db)
    out = []
    for r in rows:
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError):
            continue
        media = render.kind(r)
        title = " ".join(str(r.get("title") or "").split()) or "Untitled"
        actor = who[labels[r["id"]]]
        ctype = _content_type(db, r, types)
        kind = _kind(r, ctype, (srcs.get((r.get("remote") or {}).get("source")) or {}).get("type"), actor)
        obj = {
            "type": TYPES.get(media, "Document"),
            "id": f"lens:recording/{r['id']}",
            "name": title,
            "url": f"/resources/{r['id']}",
            "media": media,
            "content_type": ctype,
        }
        if kind == "email.received":
            obj.update(_mail(r))
        elif kind == "calendar_event.added":
            obj.update(_event(r))
        elif kind in ("file.added", "upload.added"):
            obj.update(_file(r, kind))
        elif kind == "web_page.saved":
            obj.update(_web(r))
        elif kind == "import.received":
            obj["via"] = _imported(r, actor)
        obj = store.clean(obj)
        out.append(
            events.build(
                f"create-recording-{r['id']}",
                kind,
                actor=actor,
                obj=obj,
                summary=_summary(kind, actor, obj),
                space=r.get("space"),
                source="recordings",
                pos=[r["created_at"], r["id"]],
                published=published,
            )
        )
    return out


def _sec(t):
    """A time as `created_at` is written (store.now(): to the second, +00:00), cut down to the second."""
    return events._parse(t).replace(microsecond=0).isoformat(timespec="seconds")


class RecordingsSource(events.Source):
    """recording rows as asset.added and its children, read by (created_at, id)."""

    name = "recordings"
    kinds = ("asset.added",)

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql('created_at', pos, p, table='recording')} AND {events.space_sql(spaces, p)}"
        rows = db.rows(f"SELECT {FIELDS} FROM recording WHERE {where} ORDER BY created_at, rid LIMIT {int(limit)}", **p)
        return _activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = [events.space_sql(None if who.get("admin") else (who.get("spaces") or set()), p), "created_at != NONE"]
        if before is not None:
            where.append(events.after_sql("created_at", before, p, "<", table="recording"))
        if flt.get("spaces") is not None:
            p["fs"] = sorted(flt["spaces"])
            where.append("space IN $fs")
        if flt.get("since"):  # a looser bound, to the second; the stream checks each filter exactly again
            p["since"] = _sec(flt["since"])
            where.append("created_at > $since")
        if flt.get("until"):
            p["until"] = _sec(flt["until"])
            where.append("created_at <= $until")
        rows = db.rows(
            f"SELECT {FIELDS} FROM recording WHERE {' AND '.join(where)} ORDER BY created_at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^create-recording-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {FIELDS} FROM $r", r=R("recording", int(m.group(1))))
        got = _activities(db, [r]) if r else []
        return got[0] if got else None


events.register_source(RecordingsSource())


# ---------- the kinds ----------
def _s(title, typ="string", **more):
    return {"type": typ, "title": title, **more}


def _addrs(title, item="Address"):
    return {"type": "array", "title": title, "items": {"type": "string", "title": item}}


def _child(name, label, props, untrusted, sample_actor, sample_obj, summary):
    events.kind(
        name,
        label=label,
        source="recordings",
        as_type="Create",
        object_type="Document",
        parent="asset.added",
        fields={"object": {"type": "object", "properties": props}},
        untrusted=untrusted,
        sample={
            "id": "create-recording-7",
            "type": "Create",
            "kind": name,
            "published": "2026-10-15T16:21:58.000Z",
            "actor": sample_actor,
            "object": {"id": "lens:recording/7", "url": "/resources/7", **sample_obj},
            "target": None,
            "tag": [],
            "summary": summary,
            "space": 3,
            "lens": {"source": "recordings", "pos": ["2026-10-15T16:21:58+00:00", 7], "untrusted": list(untrusted), "cause": None},
        },
    )


FILE_FIELDS = {
    "path": _s("File name"),
    "mediaType": _s("Media type"),
    "size": _s("Size (bytes)", "integer"),
    "duration": _s("Length (seconds)", "number"),
}

_child(
    "email.received",
    "Email received",
    {
        "from": _addrs("Sender"),
        "to": _addrs("Recipients"),
        "cc": _addrs("Copied to"),
        "subject": _s("Subject"),
        "list": _s("Mailing list"),
        "mailbox": _s("Mailbox"),
        "date": _s("Sent", format="date-time"),
        "snippet": _s("Start of the text"),
        "attachments": _s("Attachments", "integer"),
    },
    ("object.name", "object.subject", "object.from", "object.to", "object.cc", "object.list", "object.snippet"),
    {"type": "Service", "id": "lens:source/12", "name": "Work Gmail"},
    {
        "type": "Document",
        "name": "School trip on Friday",
        "media": "document",
        "content_type": "email",
        "from": ["office@stmarys.org"],
        "to": ["sam@example.org"],
        "subject": "School trip on Friday",
        "mailbox": "INBOX",
        "date": "2026-10-15T16:20:41+00:00",
        "snippet": "Dear parents, the trip to the museum leaves at 9 on Friday.",
        "attachments": 1,
    },
    "Work Gmail received “School trip on Friday” from office@stmarys.org",
)
_child(
    "calendar_event.added",
    "Calendar event added",
    {
        "startTime": _s("Starts", format="date-time"),
        "endTime": _s("Ends", format="date-time"),
        "location": _s("Where"),
        "organizer": _s("Organizer"),
        "attendees": _addrs("Attendees", "Attendee"),
        "calendar": _s("Calendar"),
    },
    ("object.name", "object.location", "object.organizer", "object.attendees"),
    {"type": "Service", "id": "lens:source/14", "name": "Family calendar"},
    {
        "type": "Document",
        "name": "Parents' evening",
        "media": "transcript",
        "content_type": "calendar_event",
        "startTime": "2026-10-20T18:00:00+01:00",
        "endTime": "2026-10-20T19:30:00+01:00",
        "location": "St Mary's School hall",
        "organizer": "office@stmarys.org",
        "attendees": ["sam@example.org"],
        "calendar": "Family calendar",
    },
    "Family calendar added the event “Parents' evening” on 2026-10-20",
)
_child(
    "file.added",
    "File added",
    FILE_FIELDS,
    ("object.name", "object.path"),
    {"type": "Service", "id": "lens:source/9", "name": "Office share"},
    {
        "type": "Document",
        "name": "Minutes",
        "media": "document",
        "content_type": "document",
        "path": "board/2026-10 minutes.docx",
        "mediaType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": 48213,
    },
    "Office share added “Minutes”",
)
_child(
    "upload.added",
    "Upload added",
    FILE_FIELDS,
    ("object.name", "object.path"),
    {"type": "Person", "id": "lens:account/1", "name": "Sam"},
    {
        "type": "Audio",
        "name": "Call with the school",
        "media": "audio",
        "content_type": "meeting_audio",
        "path": "Call with the school.m4a",
        "mediaType": "audio/mp4",
        "size": 5120000,
        "duration": 754.2,
    },
    "Sam uploaded “Call with the school”",
)
_child(
    "web_page.saved",
    "Web page saved",
    {"href": _s("Page address"), "site": _s("Site")},
    ("object.name", "object.href"),
    {"type": "Person", "id": "lens:account/1", "name": "Sam"},
    {
        "type": "Document",
        "name": "Museum opening hours",
        "media": "document",
        "content_type": "web_page",
        "href": "https://museum.example.org/visit",
        "site": "museum.example.org",
    },
    "Sam saved “Museum opening hours” from museum.example.org",
)
_child(
    "import.received",
    "Import received",
    {"via": _s("Came as", enum=["hook", "paste", "file"])},
    ("object.name",),
    {"type": "Service", "id": "lens:import_hook/2", "name": "Phone shortcut"},
    {"type": "Document", "name": "Shopping list", "media": "transcript", "content_type": "text", "via": "hook"},
    "Phone shortcut imported “Shopping list”",
)
