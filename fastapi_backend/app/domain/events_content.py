"""Content as activities (docs/automations.md, Sources and Kinds): the `recordings` source reads the assets Lens keeps
(the `recording` table: audio, video, documents, images, emails, web pages) by `created_at`, the id breaking ties, so
nothing is stored twice.

Each new asset is one activity, `create-recording-<id>`, the same every time it's read, of the most specific kind its
row tells: an email received from a mailbox (`email.received`), a calendar event (`calendar_event.added`), a news
feed's post (`feed_post.received`), a file from storage or Drive (`file.added`), an upload (`upload.added`), a page or video saved from the web
(`web_page.saved`), something imported (pasted text, a transcript, an import hook's: `import.received`), else
`asset.added`, their parent. The kind is read from the row's content type, where it came from and who brought it,
never from its file.

Its actor is whoever brought it: an automation (its first job was queued by `automation:<id>`), the storage source
or mailbox it came from, an import hook, the person whose upload, clip or import it was, or Lens itself (a folder
scan). Labels like `watch:<id>`, a person's email or `routine:<id>` are looked up a page at a time, a few queries for
all of them, and their names kept for NAME_SECONDS (`actors`), so other sources name their actors the same way.

Mail addressing, an event's times and a feed post's address and author are stored on the recording when it's made
(`mail`, `event`, `post`: sources.py) and read from there; an email's own details (`email`: subject, who, a snippet, its attachments) once it's been read.
Older rows without them are read without those fields.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import pathlib
import re
import threading
import time
import urllib.parse
import weakref

from . import events, store
from .events import _s, _sample

R = store.R
FIELDS = (
    "id AS rid, record::id(id) AS id, space, title, source, media, remote, content_type, created_at, path, web, engine, "
    "created_by, mail, event, post, email, size, duration_ms, attached_to, converted_from, sensor, form"
)
TYPES = {"audio": "Audio", "video": "Video", "image": "Image", "document": "Document", "transcript": "Document"}
AUTOMATION_RX = re.compile(r"^automation:(\d+)$")
LABEL_RX = re.compile(r"^(account|watch|source|routine|import_hook|sensor|automation):(\d+)$")
HOOK_RX = re.compile(r"^import hook: (.{1,80})$")
KEY_RX = re.compile(r"^[A-Za-z0-9_.:@+-]{1,200}$")
UPLOAD_RX = re.compile(r"/uploads/[^/]+/[0-9a-f]{24}/[^/]+$")  # uploads.py keeps each upload in <ns>/<upload id>/<name>
MAIL_SOURCES = {"imap", "gmail"}
CALENDAR_SOURCES = {"ical", "gcal"}
FEED_SOURCES = {"rss"}
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
    ones not looked up (with these `fields`) in the last NAME_SECONDS."""
    keys = [k for k in dict.fromkeys(keys) if k is not None]
    now, out, todo = time.monotonic(), {}, []
    with _names_lock:
        mine = _names.setdefault(db, {})
        for k in keys:
            hit = mine.get((table, by, fields, k))
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
                mine[(table, by, fields, k)] = (now, got.get(k))
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
        "automation": set(),
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
                "automation": "automation",
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
    automations = _lookup(db, "automation", want["automation"])
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
                name = (automations.get(n) or {}).get("name") or f"Automation {n}"
                return {"type": "Application", "id": f"lens:automation/{n}", "name": name}
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


def _feed_post(r):
    """A feed post's fields: its address, site, feed, author, date and categories, as its feed listed them (`post`)."""
    p = r.get("post") if isinstance(r.get("post"), dict) else {}
    href = str(p.get("href") or "")[:2000]
    site = urllib.parse.urlsplit(href or str(p.get("site") or "")).hostname
    return store.clean(
        {
            "href": href or None,
            "site": site or None,
            "feed": _text(p.get("feed"), 300),
            "author": _text(p.get("author"), 200),
            "published": p.get("published"),
            "categories": [str(c)[:100] for c in p.get("categories") or []][:10] or None,
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
    if r.get("attached_to") or r.get("converted_from"):
        return "asset.added"  # a file an email or archive held (the email is what arrived), or one a convert step made
    if ctype == "email" or source_type in MAIL_SOURCES:
        return "email.received"
    if ctype == "calendar_event" or source_type in CALENDAR_SOURCES:
        return "calendar_event.added"
    if ctype == "feed_post" or source_type in FEED_SOURCES:
        return "feed_post.received"
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
    if kind == "feed_post.received":
        return f"{who} received the post “{title}”" + (f" from {obj['site']}" if obj.get("site") else "")
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


def _asset(db, r, types):
    """What every activity about an asset says of it: its type, id, title, link, media and content type."""
    from . import render

    media = render.kind(r)
    return {
        "type": TYPES.get(media, "Document"),
        "id": f"lens:recording/{r['id']}",
        "name": " ".join(str(r.get("title") or "").split()) or "Untitled",
        "url": f"/resources/{r['id']}",
        "media": media,
        "content_type": _content_type(db, r, types),
    }


def _activities(db, rows):
    """The rows as activities of their kinds (a row whose created_at isn't a time is skipped)."""
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
        actor = who[labels[r["id"]]]
        obj = _asset(db, r, types)
        kind = _kind(r, obj["content_type"], (srcs.get((r.get("remote") or {}).get("source")) or {}).get("type"), actor)
        if kind == "email.received":
            obj.update(_mail(r))
        elif kind == "calendar_event.added":
            obj.update(_event(r))
        elif kind == "feed_post.received":
            obj.update(_feed_post(r))
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


def _ms(t):
    """A time as note versions write it (to the millisecond, +00:00)."""
    return events._parse(t).isoformat(timespec="milliseconds")


def _bound(col, op, name):
    """`col op $name` for a page, newest first. Written so SurrealDB doesn't read it as a range of an index: 3.2.4
    gives a range's first rows, not its last, for ORDER BY ... DESC LIMIT n."""
    return f"({col} {op} ${name} OR {col} = NONE)"


def _paging(col, table, before, who, flt, p, at=_sec, many=False):
    """The WHERE parts every source's page shares: what `who` may read, before the cursor, the namespace and time
    filters (looser bounds; the stream checks each filter exactly again). `many`: the rows list their namespaces in
    `spaces`."""
    mine = None if who.get("admin") else (who.get("spaces") or set())
    where = [f"{col} != NONE"]
    if mine is not None:
        p["sp"] = sorted(mine)
        where.append("spaces CONTAINSANY $sp" if many else "space IN $sp")
    if before is not None and before[1] is None:
        p["pt"] = before[0]
        where.append(_bound(col, "<", "pt"))
    elif before is not None:
        where.append(events.after_sql(col, before, p, "<", table=table))
    if flt.get("spaces") is not None:
        p["fs"] = sorted(flt["spaces"])
        where.append("spaces CONTAINSANY $fs" if many else "space IN $fs")
    if flt.get("since"):
        p["since"] = at(flt["since"])
        where.append(_bound(col, ">", "since"))
    if flt.get("until"):
        p["until"] = at(flt["until"])
        where.append(_bound(col, "<=", "until"))
    return where


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
        where = _paging("created_at", "recording", before, who, flt, p)
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

    def get_many(self, db, cfg, ids):
        ns = [int(m.group(1)) for m in (re.match(r"^create-recording-(\d+)$", i) for i in ids) if m]
        if not ns:
            return []
        return _activities(db, db.rows(f"SELECT {FIELDS} FROM recording WHERE id IN $rs", rs=[R("recording", n) for n in ns]))


events.register_source(RecordingsSource())


# ---------- the kinds ----------
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
    "feed_post.received",
    "Feed post received",
    {
        "href": _s("Post address"),
        "site": _s("Site"),
        "feed": _s("Feed"),
        "author": _s("Author"),
        "published": _s("Published", format="date-time"),
        "categories": {"type": "array", "title": "Categories", "items": {"type": "string", "title": "Category"}},
    },
    ("object.name", "object.href", "object.site", "object.feed", "object.author", "object.categories"),
    {"type": "Service", "id": "lens:source/15", "name": "Harbour news"},
    {
        "type": "Document",
        "name": "Autumn regatta",
        "media": "transcript",
        "content_type": "feed_post",
        "href": "https://harbour.example.org/posts/regatta",
        "site": "harbour.example.org",
        "feed": "Harbour news",
        "author": "Mara Keane",
        "published": "2026-10-15T08:00:00+00:00",
        "categories": ["Harbour"],
    },
    "Harbour news received the post “Autumn regatta” from harbour.example.org",
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


# ---------- runs that finished ----------
JOB_FIELDS = "id AS rid, record::id(id) AS id, recording, space, steps, step_index, status, error, created_by, finished_at, batch"
ASSET_FIELDS = "record::id(id) AS id, title, source, media, path, content_type, duration_ms, form"
JOB_KINDS = {"succeeded": "asset.processed", "failed": "asset.failed"}
JOB_ID_RX = re.compile(r"^(processed|failed)-job-(\d+)-(\d+)$")
TAGS = 10  # entities a processed asset is tagged with, the most mentioned first


def entity_tags(db, rids, most=TAGS):
    """{recording: [Mention tags]}: each asset's entities, the most mentioned first, at most `most`, in two queries."""
    counts: dict = {}
    if rids:
        for m in db.rows(
            "SELECT recording, entity, count() AS n FROM mentions WHERE recording IN $r GROUP BY recording, entity", r=list(rids)
        ):
            if _num(m.get("entity")) is not None:
                counts.setdefault(m["recording"], []).append((-int(m.get("n") or 0), m["entity"]))
    top = {rid: [e for _, e in sorted(c)[:most]] for rid, c in counts.items()}
    names = _lookup(db, "entity", {e for es in top.values() for e in es})
    return {
        rid: [
            {"type": "Mention", "id": f"lens:entity/{e}", "name": str(names[e].get("name"))[:200]}
            for e in es
            if names.get(e, {}).get("name")
        ]
        for rid, es in top.items()
    }


def _step_names(steps):
    return [s if isinstance(s, str) else str((s or {}).get("type") or "") for s in steps or []]


def _epoch(t):
    return int(events._parse(t).timestamp())


def _job_activities(db, rows):
    """Finished runs as asset.processed (succeeded) and asset.failed, one per run, about their asset; a row whose
    finished_at isn't a time is skipped."""
    rows = [r for r in rows if r and r.get("status") in JOB_KINDS]
    if not rows:
        return []
    rids = list({r["recording"] for r in rows if _num(r.get("recording")) is not None})
    assets = {a["id"]: a for a in db.rows(f"SELECT {ASSET_FIELDS} FROM recording WHERE id IN $r", r=[R("recording", i) for i in rids])}
    who = actors(db, [(r.get("created_by"), r.get("space")) for r in rows])
    tags = entity_tags(db, [i for i in rids if i in assets])
    types = _content_types(db)
    out = []
    for r in rows:
        try:
            published, n = events.stamp(r["finished_at"]), _epoch(r["finished_at"])
        except (TypeError, ValueError):
            continue
        kind, rec = JOB_KINDS[r["status"]], assets.get(r.get("recording"))
        if rec:
            obj = _asset(db, rec, types)
        else:  # deleted since
            obj = {"type": "Document", "id": f"lens:recording/{r.get('recording')}", "name": "A deleted asset"}
        steps = _step_names(r.get("steps"))
        obj.update(job=f"lens:job/{r['id']}", steps=steps)
        if r.get("batch") is not None:
            obj["batch"] = f"lens:batch/{r['batch']}"
        if kind == "asset.failed":
            i = r.get("step_index") or 0
            obj.update(
                step=steps[i] if isinstance(i, int) and 0 <= i < len(steps) else None, error=_text(r.get("error")) or "no reason given"
            )
        obj = store.clean(obj)
        title = obj["name"][:200]
        if kind == "asset.processed":
            summary = f"“{title}” finished processing" + (f" ({', '.join(steps)})" if steps else "")
        else:
            summary = f"Processing “{title}” failed" + (f" at {obj['step']}" if obj.get("step") else "")
        out.append(
            events.build(
                f"{'processed' if kind == 'asset.processed' else 'failed'}-job-{r['id']}-{n}",
                kind,
                actor=who[(r.get("created_by"), r.get("space"))],
                obj=obj,
                summary=summary,
                space=r.get("space"),
                source="jobs",
                pos=[r["finished_at"], r["id"]],
                published=published,
                tag=tags.get(r.get("recording")) or [],
            )
        )
    return out


class JobsSource(events.Source):
    """job rows that finished (succeeded or failed) as asset.processed and asset.failed, read by (finished_at, id).
    A run tried again finishes again, as another activity: its id has the time it finished."""

    name = "jobs"

    def _statuses(self, kinds, p):
        want = sorted(st for st, k in JOB_KINDS.items() if kinds is None or k in kinds)
        p["st"] = want
        return "status IN $st"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{self._statuses(None, p)} AND {events.after_sql('finished_at', pos, p, table='job')} AND {events.space_sql(spaces, p)}"
        rows = db.rows(f"SELECT {JOB_FIELDS} FROM job WHERE {where} ORDER BY finished_at, rid LIMIT {int(limit)}", **p)
        return _job_activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = [self._statuses(flt.get("kinds"), p), *_paging("finished_at", "job", before, who, flt, p)]
        rows = db.rows(
            f"SELECT {JOB_FIELDS} FROM job WHERE {' AND '.join(where)} ORDER BY finished_at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _job_activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = JOB_ID_RX.match(activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {JOB_FIELDS} FROM $r", r=R("job", int(m.group(2))))
        got = _job_activities(db, [r]) if r else []
        return got[0] if got and got[0]["id"] == activity_id else None  # an earlier try of a run tried again is gone

    def get_many(self, db, cfg, ids):
        want = set(ids)
        ns = {int(m.group(2)) for m in (JOB_ID_RX.match(i) for i in ids) if m}
        if not ns:
            return []
        rows = db.rows(f"SELECT {JOB_FIELDS} FROM job WHERE id IN $rs", rs=[R("job", n) for n in sorted(ns)])
        return [a for a in _job_activities(db, rows) if a["id"] in want]


events.register_source(JobsSource())


# ---------- what no row of its own records: deletions, calendar moves ----------
def deleted(db, rid, rec, by=None):
    """Log that an asset was deleted (deletion.py calls it once the asset is gone): asset.deleted, by `by` (an email,
    `automation:<id>`, ...). Logged once per asset, whatever retries."""
    from . import render

    actor = actors(db, [(by, rec.get("space"))])[(by, rec.get("space"))]
    title = " ".join(str(rec.get("title") or "").split()) or "Untitled"
    media = render.kind(rec)
    obj = {"type": TYPES.get(media, "Document"), "id": f"lens:recording/{int(rid)}", "name": title, "media": media}
    return events.emit(
        db,
        "asset.deleted",
        actor=actor,
        obj=obj,
        space=rec.get("space"),
        summary=f"{actor['name']} deleted “{title[:200]}”",
        key=f"deleted-{int(rid)}",
    )


def calendar_moved(db, rid, space, src, title, before, after):
    """Log that a calendar event's time changed (sources.py calls it when an event it reads again starts at another
    time than it did): calendar_event.changed, by the calendar it came from. Logged once per event and new start."""
    start = after.get("startTime")
    if not start or not before.get("startTime") or before.get("startTime") == start:
        return None
    actor = {"type": "Service", "id": f"lens:source/{src['id']}", "name": src.get("name") or f"Source {src['id']}"}
    title = " ".join(str(title or "").split()) or "Untitled"
    obj = store.clean(
        {
            "type": "Document",
            "id": f"lens:recording/{int(rid)}",
            "name": title,
            "url": f"/resources/{int(rid)}",
            "startTime": start,
            "endTime": after.get("endTime"),
            "location": after.get("location"),
            "calendar": after.get("calendar"),
            "before": store.clean({"startTime": before.get("startTime"), "endTime": before.get("endTime")}),
        }
    )
    return events.emit(
        db,
        "calendar_event.changed",
        actor=actor,
        obj=obj,
        space=space,
        summary=f"{actor['name']} moved “{title[:200]}” to {str(start)[:16].replace('T', ' ')}",
        key=f"cal-{after.get('uid') or rid}-{start}",
    )


# ---------- their kinds ----------
JOB_OBJECT = {
    "job": _s("Run"),
    "steps": {"type": "array", "title": "Steps", "items": {"type": "string", "title": "Step"}},
    "batch": _s("Batch run"),
}
_ASSET = {
    "type": _s("Object type", enum=["Audio", "Video", "Image", "Document"]),
    "id": _s("Asset"),
    "name": _s("Title"),
    "url": _s("Link"),
    "media": _s("Media", enum=["audio", "video", "image", "document", "transcript"]),
    "content_type": _s("Content type"),
}


WORK_GMAIL = {"type": "Service", "id": "lens:source/12", "name": "Work Gmail"}
SCHOOL = {"type": "Mention", "id": "lens:entity/44", "name": "St Mary's School"}
events.kind(
    "asset.processed",
    label="Asset processed",
    source="jobs",
    as_type="Update",
    object_type="Document",
    fields={"object": {"type": "object", "properties": {**_ASSET, **JOB_OBJECT}}},
    untrusted=("object.name",),
    sample=_sample(
        "processed-job-51-1792081450",
        "asset.processed",
        "Update",
        "jobs",
        ["2026-10-15T16:24:10+00:00", 51],
        WORK_GMAIL,
        {
            "type": "Document",
            "id": "lens:recording/7",
            "name": "School trip on Friday",
            "url": "/resources/7",
            "media": "document",
            "content_type": "email",
            "job": "lens:job/51",
            "steps": ["pages", "analyze", "embed"],
        },
        "“School trip on Friday” finished processing (pages, analyze, embed)",
        ("object.name",),
        [SCHOOL],
    ),
)
events.kind(
    "asset.failed",
    label="Asset failed to process",
    source="jobs",
    as_type="Announce",
    object_type="Document",
    fields={"object": {"type": "object", "properties": {**_ASSET, **JOB_OBJECT, "step": _s("Failed at"), "error": _s("Error")}}},
    untrusted=("object.name", "object.error"),
    sample=_sample(
        "failed-job-52-1792081450",
        "asset.failed",
        "Announce",
        "jobs",
        ["2026-10-15T16:24:10+00:00", 52],
        {"type": "Person", "id": "lens:account/1", "name": "Sam"},
        {
            "type": "Audio",
            "id": "lens:recording/8",
            "name": "Call with the school",
            "url": "/resources/8",
            "media": "audio",
            "job": "lens:job/52",
            "steps": ["transcribe", "analyze"],
            "step": "transcribe",
            "error": "the transcription model isn't reachable",
        },
        "Processing “Call with the school” failed at transcribe",
        ("object.name", "object.error"),
    ),
)
events.kind(
    "asset.deleted",
    label="Asset deleted",
    source="log",
    as_type="Delete",
    object_type="Document",
    fields={"object": {"type": "object", "properties": {k: _ASSET[k] for k in ("type", "id", "name", "media")}}},
    untrusted=("object.name",),
    sample=_sample(
        "log-12",
        "asset.deleted",
        "Delete",
        "log",
        ["2026-10-15T16:24:10+00:00", 12],
        {"type": "Person", "id": "lens:account/1", "name": "Sam"},
        {"type": "Document", "id": "lens:recording/7", "name": "Old minutes", "media": "document"},
        "Sam deleted “Old minutes”",
        ("object.name",),
    ),
)
events.kind(
    "calendar_event.changed",
    label="Calendar event moved",
    source="log",
    as_type="Update",
    object_type="Document",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "id": _s("Asset"),
                "name": _s("Title"),
                "url": _s("Link"),
                "startTime": _s("Starts", format="date-time"),
                "endTime": _s("Ends", format="date-time"),
                "location": _s("Where"),
                "calendar": _s("Calendar"),
                "before": {
                    "type": "object",
                    "title": "Before",
                    "properties": {"startTime": _s("Started", format="date-time"), "endTime": _s("Ended", format="date-time")},
                },
            },
        }
    },
    untrusted=("object.name", "object.location"),
    sample=_sample(
        "log-13",
        "calendar_event.changed",
        "Update",
        "log",
        ["2026-10-15T16:24:10+00:00", 13],
        {"type": "Service", "id": "lens:source/14", "name": "Family calendar"},
        {
            "type": "Document",
            "id": "lens:recording/9",
            "name": "Parents' evening",
            "url": "/resources/9",
            "startTime": "2026-10-21T18:00:00+01:00",
            "endTime": "2026-10-21T19:30:00+01:00",
            "calendar": "Family calendar",
            "before": {"startTime": "2026-10-20T18:00:00+01:00", "endTime": "2026-10-20T19:30:00+01:00"},
        },
        "Family calendar moved “Parents' evening” to 2026-10-21 18:00",
        ("object.name", "object.location"),
    ),
)


def _and_more(names, most=1):
    """“A”, “A” and “B”, or “A” and 3 more."""
    names = [f"“{str(n)[:200]}”" for n in names if n]
    if not names:
        return "an entity"
    if len(names) <= most + 1:
        return " and ".join(names)
    return f"{', '.join(names[:most])} and {len(names) - most} more"


# ---------- comments ----------
COMMENT_FIELDS = (
    "id AS rid, record::id(id) AS id, recording, activity, about, audience, owner, space, account, author, parent, text, t0, t1, "
    "quote, created_at"
)
CONTENT_MAX = 1000  # characters of a comment an activity carries


def _comment_activities(db, rows):
    """New comments as comment.added: the comment (a Note) on its asset, the target."""
    rows = [r for r in rows if r]
    if not rows:
        return []
    rids = list({r["recording"] for r in rows if _num(r.get("recording")) is not None})
    assets = {a["id"]: a for a in db.rows(f"SELECT {ASSET_FIELDS} FROM recording WHERE id IN $r", r=[R("recording", i) for i in rids])}
    who = actors(db, [(_note_label(r, "account"), r.get("space")) for r in rows])
    types = _content_types(db)
    out = []
    for r in rows:
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError):
            continue
        rec = assets.get(r.get("recording"))
        if rec:
            target = _asset(db, rec, types)
        elif isinstance(r.get("activity"), str) and r.get("recording") is None:  # on an activity of the timeline
            target = {"type": "Activity", "id": f"lens:activity/{r['activity']}", "name": _text(r.get("about")) or r["activity"]}
        else:
            target = {"type": "Document", "id": f"lens:recording/{r.get('recording')}", "name": "A deleted asset"}
        reply = _num(r.get("parent")) is not None
        obj = store.clean(
            {
                "type": "Note",
                "id": f"lens:comment/{r['id']}",
                "content": _text(r.get("text"), CONTENT_MAX) or "",
                "url": f"/resources/{r.get('recording')}" if r.get("recording") is not None else "/timeline",
                "inReplyTo": f"lens:comment/{r['parent']}" if reply else None,
                "quote": _text(r.get("quote")),
                "t0": _num(r.get("t0")),
                "t1": _num(r.get("t1")),
            }
        )
        actor = who[(_note_label(r, "account"), r.get("space"))]
        out.append(
            events.build(
                f"comment-{r['id']}",
                "comment.added",
                actor=actor,
                obj=obj,
                target=target,
                summary=f"{actor['name']} {'replied on' if reply else 'commented on'} “{target['name'][:200]}”",
                space=r.get("space"),
                source="comments",
                pos=[r["created_at"], r["id"]],
                published=published,
                audience=r.get("audience") if r.get("audience") in ("owner", "admins") else None,
                owner=_num(r.get("owner")),
            )
        )
    return out


class CommentsSource(events.Source):
    """comment rows (on assets, threaded) as comment.added, read by (created_at, id)."""

    name = "comments"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql('created_at', pos, p, table='comment')} AND {events.space_sql(spaces, p)}"
        rows = db.rows(f"SELECT {COMMENT_FIELDS} FROM comment WHERE {where} ORDER BY created_at, rid LIMIT {int(limit)}", **p)
        return _comment_activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = _paging("created_at", "comment", before, who, flt, p)
        rows = db.rows(
            f"SELECT {COMMENT_FIELDS} FROM comment WHERE {' AND '.join(where)} ORDER BY created_at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _comment_activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^comment-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {COMMENT_FIELDS} FROM $r", r=R("comment", int(m.group(1))))
        got = _comment_activities(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = sorted({int(m.group(1)) for m in (re.match(r"^comment-(\d+)$", i) for i in ids) if m})
        if not ns:
            return []
        return _comment_activities(db, db.rows(f"SELECT {COMMENT_FIELDS} FROM comment WHERE id IN $rs", rs=[R("comment", n) for n in ns]))


events.register_source(CommentsSource())


# ---------- notes ----------
NOTE_WINDOW = 600  # seconds: a page's versions in one window of the clock this long are one note.saved
NOTE_ROUNDS = 5  # reads of versions a look makes at most, while windows already told of thin them out
VERSION_FIELDS = "id AS rid, record::id(id) AS id, page, space, at, by, author, title, why"
PAGE_FIELDS = "id AS rid, record::id(id) AS id, space, title, summary, about, place, author, created_by, created_at"
NOTE_ID_RX = re.compile(r"^note-(\d+)-(\d+)$")


def _slot(t):
    return _epoch(t) // NOTE_WINDOW


def _slot_time(n):
    return _ms(dt.datetime.fromtimestamp(n * NOTE_WINDOW, dt.timezone.utc))


def _note_label(r, by="by"):
    return "assistant" if r.get("author") == "assistant" else r.get(by)


def _about(about):
    m = re.match(r"^([a-z_]+):(\d+)$", str(about or ""))
    return f"lens:{m.group(1)}/{m.group(2)}" if m else None


def _page_object(pid, pg):
    return store.clean(
        {
            "type": "Page",
            "id": f"lens:note_page/{pid}",
            "name": _text(pg.get("title"), 200) or "Untitled",
            "url": f"/notes/{pid}",
            "summary": _text(pg.get("summary")),
            "about": _about(pg.get("about")),
            "place": pg.get("place"),
        }
    )


def _slot_of(r):
    try:
        return _slot(r["at"])
    except (TypeError, ValueError):
        return None


def _told(db, rows, newest):
    """The versions that stand for their page's window: its first (its newest, when paging back), one query for all."""
    slots = {r["id"]: _slot_of(r) for r in rows if r and _num(r.get("page")) is not None}
    rows = [r for r in rows if r and slots.get(r["id"]) is not None]
    if not rows:
        return []
    near: dict = {}
    for v in db.rows(
        "SELECT page, at, record::id(id) AS id FROM note_version WHERE page IN $p AND at >= $lo AND at < $hi",
        p=sorted({r["page"] for r in rows}),
        lo=_slot_time(min(slots[r["id"]] for r in rows)),
        hi=_slot_time(max(slots[r["id"]] for r in rows) + 1),
    ):
        if _slot_of(v) is not None:
            near.setdefault((v["page"], _slot_of(v)), []).append((str(v["at"]), v["id"]))
    pick = max if newest else min
    return [r for r in rows if pick(near.get((r["page"], slots[r["id"]]), []) + [(str(r["at"]), r["id"])]) == (str(r["at"]), r["id"])]


def _note_activities(db, rows):
    """Versions as note.saved: someone (or the assistant) changed the page at the version's time."""
    if not rows:
        return []
    pages = {
        x["id"]: x
        for x in db.rows(
            "SELECT record::id(id) AS id, title, summary, about, place FROM note_page WHERE id IN $p",
            p=[R("note_page", i) for i in {r["page"] for r in rows}],
        )
    }
    who = actors(db, [(_note_label(r), r.get("space")) for r in rows])
    out = []
    for r in rows:
        try:
            published, slot = events.stamp(r["at"]), _slot(r["at"])
        except (TypeError, ValueError):
            continue
        obj = _page_object(r["page"], pages.get(r["page"]) or {"title": r.get("title")})
        obj.update(store.clean({"version": f"lens:note_version/{r['id']}", "author": r.get("author"), "why": _text(r.get("why"))}))
        actor = who[(_note_label(r), r.get("space"))]
        out.append(
            events.build(
                f"note-{r['page']}-{slot}",
                "note.saved",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} edited the note “{obj['name'][:200]}”",
                space=r.get("space"),
                source="note_versions",
                pos=[r["at"], r["id"]],
                published=published,
            )
        )
    return out


class NoteVersionsSource(events.Source):
    """note_version rows (a page as it was before each change: notebook.snapshot) as note.saved, read by (at, id): one
    activity per page per NOTE_WINDOW, `note-<page>-<window>`. Followers hear of a window's first change, the timeline
    shows its newest."""

    name = "note_versions"

    def _rounds(self, db, where, p, cursor, limit, newest):
        """Versions from `cursor` (None: the newest), a batch at a time, until `limit` of them stand for their window
        or none are left."""
        op, order = ("<", "at DESC, rid DESC") if newest else (">", "at, rid")
        batch, out = max(int(limit), 50), []
        for _ in range(NOTE_ROUNDS):
            q = dict(p)
            parts = where + ([events.after_sql("at", cursor, q, op, table="note_version")] if cursor is not None else [])
            rows = db.rows(f"SELECT {VERSION_FIELDS} FROM note_version WHERE {' AND '.join(parts)} ORDER BY {order} LIMIT {batch}", **q)
            out += _told(db, rows, newest)
            if len(rows) < batch or len(out) >= limit:
                break
            cursor = [rows[-1]["at"], rows[-1]["id"]]
        return out[: int(limit)]

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        return _note_activities(db, self._rounds(db, [events.space_sql(spaces, p)], p, pos, limit, newest=False))

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = _paging("at", None, None, who, flt, p, at=_ms)
        return _note_activities(db, self._rounds(db, where, p, before, limit, newest=True))

    def get(self, db, cfg, activity_id):
        m = NOTE_ID_RX.match(activity_id)
        if not m:
            return None
        pid, slot = int(m.group(1)), int(m.group(2))
        r = db.one(
            f"SELECT {VERSION_FIELDS} FROM note_version WHERE page = $p AND {_bound('at', '>=', 'lo')} AND {_bound('at', '<', 'hi')} "
            "ORDER BY at DESC, rid DESC LIMIT 1",
            p=pid,
            lo=_slot_time(slot),
            hi=_slot_time(slot + 1),
        )
        got = _note_activities(db, [r]) if r else []
        return got[0] if got and got[0]["id"] == activity_id else None

    def get_many(self, db, cfg, ids):
        want = {(int(m.group(1)), int(m.group(2))) for m in (NOTE_ID_RX.match(i) for i in ids) if m}
        if not want:
            return []
        rows = db.rows(
            f"SELECT {VERSION_FIELDS} FROM note_version WHERE page IN $p AND at >= $lo AND at < $hi",
            p=sorted({p for p, _ in want}),
            lo=_slot_time(min(n for _, n in want)),
            hi=_slot_time(max(n for _, n in want) + 1),
        )
        newest: dict = {}
        for r in rows:
            k = (r.get("page"), _slot_of(r))
            if k in want and (k not in newest or (str(r["at"]), r["id"]) > (str(newest[k]["at"]), newest[k]["id"])):
                newest[k] = r
        return _note_activities(db, list(newest.values()))


def _page_activities(db, rows):
    """New pages as note.created, by whoever made them (the assistant, for its own)."""
    rows = [r for r in rows if r]
    who = actors(db, [(_note_label(r, "created_by"), r.get("space")) for r in rows])
    out = []
    for r in rows:
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError):
            continue
        obj = _page_object(r["id"], r)
        if r.get("author"):
            obj["author"] = r["author"]
        actor = who[(_note_label(r, "created_by"), r.get("space"))]
        out.append(
            events.build(
                f"note-created-{r['id']}",
                "note.created",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} wrote the note “{obj['name'][:200]}”",
                space=r.get("space"),
                source="note_pages",
                pos=[r["created_at"], r["id"]],
                published=published,
            )
        )
    return out


class NotePagesSource(events.Source):
    """note_page rows as note.created when they're made, read by (created_at, id)."""

    name = "note_pages"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql('created_at', pos, p, table='note_page')} AND {events.space_sql(spaces, p)}"
        rows = db.rows(f"SELECT {PAGE_FIELDS} FROM note_page WHERE {where} ORDER BY created_at, rid LIMIT {int(limit)}", **p)
        return _page_activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = _paging("created_at", "note_page", before, who, flt, p)
        rows = db.rows(
            f"SELECT {PAGE_FIELDS} FROM note_page WHERE {' AND '.join(where)} ORDER BY created_at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _page_activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^note-created-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {PAGE_FIELDS} FROM $r", r=R("note_page", int(m.group(1))))
        got = _page_activities(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = sorted({int(m.group(1)) for m in (re.match(r"^note-created-(\d+)$", i) for i in ids) if m})
        if not ns:
            return []
        return _page_activities(db, db.rows(f"SELECT {PAGE_FIELDS} FROM note_page WHERE id IN $rs", rs=[R("note_page", n) for n in ns]))


events.register_source(NoteVersionsSource())
events.register_source(NotePagesSource())


# ---------- the entity graph ----------
GRAPH_FIELDS = (
    "id AS rid, record::id(id) AS id, at, op, actor, via, spaces, entities, origin, "
    "ops[WHERE t = 'entity'].{k, b: b.name, a: a.name, ty: (a ?? b).type} AS named"
)
GRAPH_TAGS = 20  # entities an entity.changed activity is tagged with
CREATED_OPS = {"entity.found", "entity.learned", "entity.builtin"}
GRAPH_OPS = {
    "entity.rename": "renamed",
    "entity.merge": "merged",
    "entity.unmerge": "split",
    "link.add": "linked",
    "link.remove": "unlinked",
    "entity.delete": "deleted",
    "graph.rollback": "rolled back",
}
OPS = ("created", "renamed", "merged", "split", "linked", "unlinked", "deleted", "rolled back", "changed")


def _graph_op(op, named):
    """What a graph event did, in a word: an entity made (found by analysis or defined from a phrase), renamed, merged,
    split again, linked across namespaces or unlinked, deleted, rolled back, else changed."""
    if op in CREATED_OPS or (op in ("analysis", "entity.define") and any(n.get("b") is None and n.get("a") for n in named.values())):
        return "created"
    return GRAPH_OPS.get(op, "changed")


def _graph_label(r):
    """Who changed the graph, as actors() reads it: an automation (its id as actor or `via`), the assistant or an
    agent over MCP (through `via`, for the person who asked), else the actor (an email, `routine:<id>`; analysis and
    system are Lens)."""
    for v in (r.get("actor"), r.get("via")):
        if AUTOMATION_RX.match(str(v or "")):
            return v
    return r.get("via") if r.get("via") in APPS else r.get("actor")


def _graph_summary(op, actor, r, named, ents, name, alive):
    who = actor["name"]
    if op == "renamed" and ents and named.get(ents[0], {}).get("b"):
        n = named[ents[0]]
        return f"{who} renamed “{str(n['b'])[:200]}” to “{str(n.get('a') or '')[:200]}”"
    if op == "merged" and alive:
        gone = [name(e) for e in ents if e not in alive]
        return f"{who} merged {_and_more(gone)} into “{str(name(alive[0]))[:200]}”"
    if op == "rolled back" and _num((r.get("origin") or {}).get("rollback_to")) is not None:
        return f"{who} rolled the graph back to version {r['origin']['rollback_to']}"
    verb = {"created": "found", "split": "split", "linked": "linked", "unlinked": "unlinked", "deleted": "deleted"}.get(op, op)
    return f"{who} {verb} {_and_more([name(e) for e in ents], 2 if op in ('linked', 'unlinked') else 1)}"


def _graph_activities(db, rows):
    """Graph events as entity.changed, one per event: about its first entity still there, tagged with its entities,
    in the first of its namespaces. One that spans namespaces is for admins, since its names come from each."""
    rows = [r for r in rows if r]
    if not rows:
        return []
    named_of = {r["id"]: {n["k"]: n for n in r.get("named") or [] if isinstance(n, dict) and _num(n.get("k")) is not None} for r in rows}
    ents_of = {r["id"]: [e for e in r.get("entities") or [] if _num(e) is not None] for r in rows}
    looked = _lookup(db, "entity", {e for r in rows for e in ents_of[r["id"]][:GRAPH_TAGS] if e not in named_of[r["id"]]}, "name, type")
    labels = {r["id"]: (_graph_label(r), ([s for s in r.get("spaces") or [] if _num(s) is not None] or [None])[0]) for r in rows}
    who = actors(db, labels.values())
    out = []
    for r in rows:
        try:
            published = events.stamp(r["at"])
        except (TypeError, ValueError):
            continue
        named, ents = named_of[r["id"]], ents_of[r["id"]]

        def name(e, named=named):
            n = named.get(e)
            return (n.get("a") or n.get("b")) if n else (looked.get(e) or {}).get("name")

        op = _graph_op(r.get("op"), named)
        alive = [e for e in ents if not (e in named and named[e].get("a") is None)]
        first = (alive or ents or [None])[0]
        n = named.get(first) or looked.get(first) or {}
        obj = {
            "type": "Object",
            "id": f"lens:entity/{first}" if first is not None else "lens:graph",
            "name": _text(name(first), 200) or "an entity",
            "url": f"/entities/{first}" if first in alive else None,
            "entityType": n.get("ty") or n.get("type"),
            "op": op,
            "change": r.get("op"),
            "version": r["id"],
            "count": len(ents),
        }
        if op == "renamed" and (named.get(first) or {}).get("b"):
            obj["before"] = _text(named[first]["b"], 200)
        spaces = sorted({s for s in r.get("spaces") or [] if _num(s) is not None})
        actor = who[labels[r["id"]]]
        out.append(
            events.build(
                f"graph-{r['id']}",
                "entity.changed",
                actor=actor,
                obj=store.clean(obj),
                summary=_graph_summary(op, actor, r, named, ents, name, alive),
                space=spaces[0] if spaces else None,
                source="graph",
                pos=[r["at"], r["id"]],
                published=published,
                tag=[{"type": "Mention", "id": f"lens:entity/{e}", "name": _text(name(e), 200)} for e in ents[:GRAPH_TAGS] if name(e)],
                spaces=spaces if len(spaces) > 1 else None,
                audience="admins" if len(spaces) > 1 else None,
            )
        )
    return out


class GraphSource(events.Source):
    """graph_event rows (graph_history.py: one per change to the entity graph, never changed) as entity.changed, read
    by (at, event number); the number gives versions out in order, so the two agree."""

    name = "graph"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = [events.after_sql("at", pos, p, table="graph_event")]
        if spaces is not None:
            p["sp"] = sorted(spaces)
            where.append("spaces CONTAINSANY $sp")
        rows = db.rows(f"SELECT {GRAPH_FIELDS} FROM graph_event WHERE {' AND '.join(where)} ORDER BY at, rid LIMIT {int(limit)}", **p)
        return _graph_activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = _paging("at", "graph_event", before, who, flt, p, many=True)
        rows = db.rows(
            f"SELECT {GRAPH_FIELDS} FROM graph_event WHERE {' AND '.join(where)} ORDER BY at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _graph_activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^graph-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {GRAPH_FIELDS} FROM $r", r=R("graph_event", int(m.group(1))))
        got = _graph_activities(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = sorted({int(m.group(1)) for m in (re.match(r"^graph-(\d+)$", i) for i in ids) if m})
        if not ns:
            return []
        return _graph_activities(
            db, db.rows(f"SELECT {GRAPH_FIELDS} FROM graph_event WHERE id IN $rs", rs=[R("graph_event", n) for n in ns])
        )


events.register_source(GraphSource())


# ---------- their kinds ----------
SAM = {"type": "Person", "id": "lens:account/1", "name": "Sam"}
_PAGE = {
    "type": _s("Object type", enum=["Page"]),
    "id": _s("Note"),
    "name": _s("Title"),
    "url": _s("Link"),
    "summary": _s("Summary"),
    "about": _s("About"),
    "place": _s("Place", enum=["project", "area", "resource", "archive"]),
    "author": _s("Written by", enum=["person", "assistant"]),
}
events.kind(
    "comment.added",
    label="Comment added",
    source="comments",
    as_type="Create",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "type": _s("Object type", enum=["Note"]),
                "id": _s("Comment"),
                "content": _s("Text"),
                "url": _s("Link"),
                "inReplyTo": _s("Reply to"),
                "quote": _s("Quoted passage"),
                "t0": _s("From (ms)", "integer"),
                "t1": _s("To (ms)", "integer"),
            },
        },
        "target": {"type": "object", "properties": _ASSET},
    },
    untrusted=("object.content", "object.quote", "target.name"),
    sample=_sample(
        "comment-31",
        "comment.added",
        "Create",
        "comments",
        ["2026-10-15T16:24:10+00:00", 31],
        SAM,
        {
            "type": "Note",
            "id": "lens:comment/31",
            "content": "Can we bring this up with the school on Friday?",
            "url": "/resources/8",
            "t0": 61000,
            "t1": 65000,
        },
        "Sam commented on “Call with the school”",
        ("object.content", "object.quote", "target.name"),
    )
    | {"target": {"type": "Audio", "id": "lens:recording/8", "name": "Call with the school", "url": "/resources/8", "media": "audio"}},
)
events.kind(
    "note.saved",
    label="Note saved",
    source="note_versions",
    as_type="Update",
    object_type="Page",
    fields={
        "object": {
            "type": "object",
            "properties": {**_PAGE, "version": _s("Version kept from before"), "why": _s("Why")},
        }
    },
    untrusted=("object.name", "object.summary"),
    sample=_sample(
        "note-5-2986802",
        "note.saved",
        "Update",
        "note_versions",
        ["2026-10-15T16:24:10.120+00:00", 40],
        SAM,
        {
            "type": "Page",
            "id": "lens:note_page/5",
            "name": "School trip plans",
            "url": "/notes/5",
            "summary": "What to pack and who drives",
            "place": "project",
            "version": "lens:note_version/40",
            "author": "person",
        },
        "Sam edited the note “School trip plans”",
        ("object.name", "object.summary"),
    ),
)
events.kind(
    "note.created",
    label="Note written",
    source="note_pages",
    as_type="Create",
    object_type="Page",
    fields={"object": {"type": "object", "properties": _PAGE}},
    untrusted=("object.name", "object.summary"),
    sample=_sample(
        "note-created-6",
        "note.created",
        "Create",
        "note_pages",
        ["2026-10-15T16:24:10+00:00", 6],
        APPS["assistant"],
        {
            "type": "Page",
            "id": "lens:note_page/6",
            "name": "St Mary's School",
            "url": "/notes/6",
            "about": "lens:entity/44",
            "author": "assistant",
        },
        "Assistant wrote the note “St Mary's School”",
        ("object.name", "object.summary"),
    ),
)
events.kind(
    "entity.changed",
    label="Entity changed",
    source="graph",
    as_type="Update",
    object_type="Object",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "type": _s("Object type", enum=["Object"]),
                "id": _s("Entity"),
                "name": _s("Name"),
                "url": _s("Link"),
                "entityType": _s("Entity type"),
                "op": _s("What happened", enum=list(OPS)),
                "change": _s("Graph change"),
                "version": _s("Graph version", "integer"),
                "count": _s("Entities changed", "integer"),
                "before": _s("Name before"),
            },
        }
    },
    untrusted=("object.name", "object.before"),
    sample=_sample(
        "graph-88",
        "entity.changed",
        "Update",
        "graph",
        ["2026-10-15T16:24:10+00:00", 88],
        SAM,
        {
            "type": "Object",
            "id": "lens:entity/44",
            "name": "St Mary's School",
            "url": "/entities/44",
            "entityType": "organization",
            "op": "renamed",
            "change": "entity.rename",
            "version": 88,
            "count": 1,
            "before": "St Marys",
        },
        "Sam renamed “St Marys” to “St Mary's School”",
        ("object.name", "object.before"),
        [SCHOOL],
    ),
)
