"""The stream of activities (docs/automations.md, Contract): what happens in Lens, from every source, as time-coded
activities in the W3C Activity Streams 2.0 shape, for automations to follow and the timeline to show.

An activity is a plain dict (`check_activity` says what it holds): an AS2 verb (`type`), a Lens `kind` that triggers
subscribe to (`email.received`), when it happened, who did it (`actor`), what it is about (`object`), a one-line
summary made by fixed rules, its namespace, and Lens's own fields under `lens`.

Most activities aren't stored twice. A source (`Source`, `register_source`) reads rows Lens already keeps, such as
recordings, and makes activities of them, with ids built from what it reads (`create-recording-7`), so they are the
same every time they're read. What has no row of its own (a deletion, a webhook call) is written to a small log,
`event_log`, by `emit`; the `log` source reads it back. Kinds (`kind`) are registered in a catalog with the source
that makes them, the fields of their objects, which of those hold outside text, and a sample.

Following the stream works across processes the way notify.py does it, since the API and `lens worker` are separate
processes on one database: each consumer has a cursor (`event_cursor:<consumer>`, a position per source), reads past
it in (time, id) order, and claims what it handles (`claim`: a CREATE only one process wins). A consumer seen for the
first time starts at now, so nothing that happened before replays. Once caught up, a read also reads the last
LOOKBACK seconds again, for a row written as the last look ran; claims keep those from being handled twice. A source
that can't make a kind the consumer wants isn't read, and its cursor moves to now, so a kind someone starts listening
to later doesn't replay a backlog.

The timeline reads the stream newest first (`page`), merging the sources, applying who may see what (`lens.audience`
and namespaces), and reading on while filters thin a page out. Nothing here runs on its own: until something follows
the stream, an install behaves as before. `tidy` drops old log rows and claims.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass

from . import store

R = store.R
ID_RX = re.compile(r"^[a-z0-9_.:-]{1,120}$")
KIND_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}(\.[a-z][a-z0-9_]{0,40}){1,3}$")
NAME_RX = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
REF_RX = re.compile(r"^lens:[a-z][a-z0-9_]{0,40}(/[A-Za-z0-9_.:@+-]{1,200})?$")
PATH_RX = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
TYPES = ("Create", "Update", "Delete", "Add", "Remove", "Move", "Join", "Leave", "Arrive", "Announce", "Like", "Accept", "Reject")
ACTOR_TYPES = ("Service", "Person", "Application")
AUDIENCES = ("space", "owner", "admins")
LENS = {"type": "Application", "id": "lens:app", "name": "Lens"}
LOOKBACK = 60  # seconds a caught-up read goes back over, for rows written as the last look ran
PAGE = 500  # rows a follow reads per source
PAGE_ROUNDS = 5  # source pages a timeline page reads at most, per source, when filters thin it out
SUMMARY_MAX = 300
MAX_TAGS = 50
FILTERS = ("kinds", "actor", "spaces", "tag", "since", "until", "q", "where")
SWEEP_SECONDS = 3600
CLAIM_DAYS = 1

_swept = {"at": 0.0}
_cause: contextvars.ContextVar[dict | None] = contextvars.ContextVar("lens_event_cause", default=None)
_wakes = threading.Condition()
_generation = [0]
_seen: dict[str, dict[str, None]] = {}  # per consumer: activities this process handled lately (not read again)
_claimed: dict[str, None] = {}  # claims this process knows are taken, so it doesn't ask the database again
_lock = threading.Lock()


# ---------- times ----------
def _parse(t):
    """An aware UTC datetime from an ISO 8601 string (naive is UTC) or a datetime."""
    if isinstance(t, dt.datetime):
        d = t
    else:
        s = str(t).strip()
        d = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def stamp(t=None):
    """`published` as activities have it: ISO 8601 in UTC with milliseconds (2026-10-15T16:21:58.000Z), so stamps
    from every source sort as text."""
    d = _parse(t) if t is not None else dt.datetime.now(dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def ago(seconds, now=None):
    """The time `seconds` ago, as store.now() writes times (to the second, +00:00)."""
    base = _parse(now) if now is not None else dt.datetime.now(dt.timezone.utc)
    return (base - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _tkey(t):
    if isinstance(t, str):
        try:
            return _parse(t).timestamp()
        except ValueError:
            return t
    return t


def _ikey(i):
    """A row id, for ordering: numbers before text, each in its own order."""
    return (0, i, "") if isinstance(i, (int, float)) and not isinstance(i, bool) else (1, 0, str(i))


def pos_key(pos):
    """A cursor position [time, id] for ordering. An id of None means every row at that time was read, so it comes
    after every [time, id] at the same time."""
    t, i = (list(pos) + [None, None])[:2]
    return (_tkey(t), 1, (0, 0, "")) if i is None else (_tkey(t), 0, _ikey(i))


# ---------- the shape ----------
def _ref(v, what):
    if not isinstance(v, str) or not REF_RX.match(v):
        raise ValueError(f"{what} is a Lens reference like lens:recording/7")


def check_activity(a):
    """The activity, if it has the shape every part of Lens reads (docs/automations.md, Activity): ValueError naming
    the first problem otherwise."""
    if not isinstance(a, dict):
        raise ValueError("an activity is an object")
    if not isinstance(a.get("id"), str) or not ID_RX.match(a["id"]):
        raise ValueError("an activity's id is 1 to 120 lowercase letters, digits and _ . : -")
    if a.get("type") not in TYPES:
        raise ValueError(f"an activity's type is one of {', '.join(TYPES)}")
    if a.get("kind") not in _KINDS:
        raise ValueError(f"no kind {a.get('kind')}; register it with events.kind first")
    try:
        if not isinstance(a.get("published"), str) or not a["published"].endswith("Z"):
            raise ValueError
        _parse(a["published"])
    except ValueError:
        raise ValueError("published is an ISO 8601 time in UTC, like 2026-10-15T16:21:58.000Z") from None
    actor = a.get("actor")
    if not isinstance(actor, dict) or actor.get("type") not in ACTOR_TYPES:
        raise ValueError(f"the actor is an object whose type is {', '.join(ACTOR_TYPES)}")
    _ref(actor.get("id"), "the actor's id")
    if not isinstance(actor.get("name"), str) or not actor["name"].strip():
        raise ValueError("the actor has a name")
    obj = a.get("object")
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str) or not obj["type"]:
        raise ValueError("the object is an object with a type")
    if obj.get("id") is not None:
        _ref(obj["id"], "the object's id")
    if a.get("target") is not None and not isinstance(a["target"], dict):
        raise ValueError("the target is an object, or null")
    tags = a.get("tag") or []
    if not isinstance(tags, list) or len(tags) > MAX_TAGS or not all(isinstance(t, dict) and t.get("type") for t in tags):
        raise ValueError(f"tag is a list of at most {MAX_TAGS} objects, each with a type")
    if not isinstance(a.get("summary"), str) or not a["summary"].strip() or len(a["summary"]) > SUMMARY_MAX:
        raise ValueError(f"the summary is one line of at most {SUMMARY_MAX} characters")
    if a.get("space") is not None and (not isinstance(a["space"], int) or isinstance(a["space"], bool)):
        raise ValueError("space is a namespace id, or null")
    lens = a.get("lens")
    if not isinstance(lens, dict) or not isinstance(lens.get("source"), str):
        raise ValueError("lens is an object naming the source that made the activity")
    if not isinstance(lens.get("pos"), list) or len(lens["pos"]) != 2:
        raise ValueError("lens.pos is the source's position, [time, id]")
    paths = lens.get("untrusted") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) and PATH_RX.match(p) for p in paths):
        raise ValueError("lens.untrusted is a list of dotted paths, like object.subject")
    cause = lens.get("cause")
    if cause is not None and (
        not isinstance(cause, dict) or not all(isinstance(cause.get(k), int) for k in ("automation", "run", "depth"))
    ):
        raise ValueError("lens.cause is {automation, run, depth}, or null")
    spaces = lens.get("spaces")
    if spaces is not None and (not isinstance(spaces, list) or not all(isinstance(s, int) for s in spaces)):
        raise ValueError("lens.spaces is a list of namespace ids")
    if lens.get("audience", "space") not in AUDIENCES:
        raise ValueError(f"lens.audience is one of {', '.join(AUDIENCES)}")
    if lens.get("audience") == "owner" and not isinstance(lens.get("owner"), int):
        raise ValueError("an activity only its owner sees names the owner's account (lens.owner)")
    return a


def build(
    aid,
    kind,
    *,
    actor,
    obj,
    summary,
    space,
    source,
    pos,
    published,
    target=None,
    tag=None,
    untrusted=None,
    cause=None,
    spaces=None,
    audience=None,
    owner=None,
):
    """An activity in the shape check_activity wants, for sources to build theirs with. `untrusted` defaults to the
    kind's outside-text paths; `type` is the kind's AS2 verb."""
    k = _KINDS[kind]
    lens = {"source": source, "pos": list(pos), "untrusted": list(k.untrusted if untrusted is None else untrusted), "cause": cause}
    if spaces:
        lens["spaces"] = sorted(set(spaces))
    if audience and audience != "space":
        lens["audience"] = audience
    if owner is not None:
        lens["owner"] = owner
    return {
        "id": aid,
        "type": k.as_type,
        "kind": kind,
        "published": stamp(published),
        "actor": actor,
        "object": obj,
        "target": target,
        "tag": list(tag or []),
        "summary": " ".join(str(summary).split())[:SUMMARY_MAX],
        "space": space,
        "lens": lens,
    }


def ref_id(ref, table):
    """The id in a `lens:<table>/<id>` reference (an object's id, say), a number where it is one; None for a reference
    to another table."""
    m = re.match(rf"^lens:{re.escape(table)}/(.+)$", ref if isinstance(ref, str) else "")
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else m.group(1)


# ---------- the catalog of kinds ----------
@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    source: str
    as_type: str
    object_type: str
    parent: str | None
    fields: dict
    untrusted: tuple[str, ...]
    sample: dict | None
    noisy: bool

    def info(self):
        return {
            "name": self.name,
            "label": self.label,
            "source": self.source,
            "type": self.as_type,
            "object_type": self.object_type,
            "parent": self.parent,
            "children": sorted(k for k, v in _KINDS.items() if v.parent == self.name),
            "fields": self.fields,
            "schema": schema(self.name),
            "untrusted": list(self.untrusted),
            "noisy": self.noisy,
            "sample": self.sample,
        }


_KINDS: dict[str, Kind] = {}
BASE_FIELDS = {
    "actor": {
        "type": "object",
        "title": "Who",
        "properties": {
            "type": {"type": "string", "title": "Kind of actor", "enum": list(ACTOR_TYPES)},
            "id": {"type": "string", "title": "Actor"},
            "name": {"type": "string", "title": "Actor's name"},
        },
    },
    "object": {
        "type": "object",
        "title": "What",
        "properties": {
            "type": {"type": "string", "title": "Object type"},
            "id": {"type": "string", "title": "Object"},
            "name": {"type": "string", "title": "Name"},
            "url": {"type": "string", "title": "Link"},
        },
    },
}


def kind(name, *, label, source, as_type, object_type, parent=None, fields=None, untrusted=(), sample=None, noisy=False):
    """Register a kind of activity: its name (`<noun>.<past verb>`), a label for people, the source that makes it, its
    AS2 verb and object type, a parent kind (a trigger on the parent matches it too), JSON Schemas for its `object`
    (and `actor` or `target`) in `fields`, the dotted paths that hold outside text, a realistic sample, and whether
    it is `noisy` (left out of the timeline unless asked for). Registering a name again replaces it."""
    if not KIND_RX.match(name or ""):
        raise ValueError(f"a kind is named like noun.past_verb (email.received), not {name}")
    if not NAME_RX.match(source or ""):
        raise ValueError(f"kind {name}: name the source that makes it")
    if as_type not in TYPES:
        raise ValueError(f"kind {name}: its type is one of {', '.join(TYPES)}")
    if parent is not None and (parent not in _KINDS or parent == name):
        raise ValueError(f"kind {name}: register its parent {parent} first")
    fields = dict(fields or {})
    if set(fields) - {"object", "actor", "target"} or not all(isinstance(v, dict) for v in fields.values()):
        raise ValueError(f"kind {name}: fields are JSON Schemas for object, actor or target")
    if not all(isinstance(p, str) and PATH_RX.match(p) for p in untrusted):
        raise ValueError(f"kind {name}: untrusted paths are dotted, like object.subject")
    old = _KINDS.get(name)
    _KINDS[name] = Kind(name, label, source, as_type, object_type, parent, fields, tuple(untrusted), sample, bool(noisy))
    if sample is not None:
        try:
            check_activity(sample)
            if sample["kind"] != name:
                raise ValueError(f"its sample is a {sample['kind']}")
        except ValueError as e:
            if old:
                _KINDS[name] = old
            else:
                del _KINDS[name]
            raise ValueError(f"kind {name}: {e}") from None
    return _KINDS[name]


def _s(title, typ="string", **more):
    """A field of a kind's JSON Schema."""
    return {"type": typ, "title": title, **more}


def _sample(aid, kind, typ, source, pos, actor, obj, summary, untrusted, tag=()):
    """A realistic activity of a kind, as kind(sample=) takes it."""
    return {
        "id": aid,
        "type": typ,
        "kind": kind,
        "published": "2026-10-15T16:24:10.000Z",
        "actor": actor,
        "object": obj,
        "target": None,
        "tag": list(tag),
        "summary": summary,
        "space": 3,
        "lens": {"source": source, "pos": pos, "untrusted": list(untrusted), "cause": None},
    }


def kinds():
    """Every kind, as the API and agents list them."""
    return [k.info() for k in _KINDS.values()]


def kind_info(name):
    """One kind; KeyError when there's none."""
    return _KINDS[name].info()


def descendants(name):
    """The kind and every kind under it (a trigger on asset.added matches email.received)."""
    out, todo = set(), [name]
    while todo:
        k = todo.pop()
        if k not in out:
            out.add(k)
            todo += [c for c, v in _KINDS.items() if v.parent == k]
    return out


def expand(names):
    """The kinds named and their descendants; None (every kind) stays None. ValueError for a kind that isn't there."""
    if names is None:
        return None
    unknown = [n for n in names if n not in _KINDS]
    if unknown:
        raise ValueError(f"no kind {unknown[0]}; GET /api/v1/activities/kinds lists them")
    return set().union(*(descendants(n) for n in names)) if names else set()


def _merged(base, extra):
    if not extra:
        return base
    out = {**base, **{k: v for k, v in extra.items() if k != "properties"}}
    out["properties"] = {**(base.get("properties") or {}), **(extra.get("properties") or {})}
    return out


def schema(name):
    """A JSON Schema of the kind's activities: every activity's own fields, its parents' fields, then its own."""
    chain, k = [], _KINDS.get(name)
    while k:
        chain.insert(0, k)
        k = _KINDS.get(k.parent) if k.parent else None
    parts = {p: dict(v) for p, v in BASE_FIELDS.items()}
    for k in chain:
        for p, v in k.fields.items():
            parts[p] = _merged(parts.get(p) or {"type": "object"}, v)
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "title": "Activity"},
            "kind": {"type": "string", "title": "Kind"},
            "published": {"type": "string", "format": "date-time", "title": "When"},
            "summary": {"type": "string", "title": "Summary"},
            "space": {"type": ["integer", "null"], "title": "Namespace"},
            "tag": {"type": "array", "title": "Tags", "items": {"type": "object"}},
            **parts,
        },
    }


def field_at(name, path):
    """The schema of the field at a dotted path in a kind's activities (list items by number), or None."""
    node = schema(name) if name in _KINDS else None
    for part in (path or "").split("."):
        if not isinstance(node, dict):
            return None
        props = node.get("properties")
        if props and part in props:
            node = props[part]
        elif node.get("type") == "array" and part.isdigit():
            node = node.get("items")
        else:
            return None
    return node if isinstance(node, dict) else None


# Assets: the kind every new recording, document, image, email or web page is, and the parent of M2's kinds for each
# (email.received, file.added, ...). The recordings source makes it (events_content.py).
kind(
    "asset.added",
    label="Asset added",
    source="recordings",
    as_type="Create",
    object_type="Document",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "title": "Object type", "enum": ["Audio", "Video", "Image", "Document"]},
                "id": {"type": "string", "title": "Asset"},
                "name": {"type": "string", "title": "Title"},
                "url": {"type": "string", "title": "Link"},
                "media": {"type": "string", "title": "Media", "enum": ["audio", "video", "image", "document", "transcript"]},
                "content_type": {"type": "string", "title": "Content type"},
            },
        }
    },
    untrusted=("object.name",),
    sample={
        "id": "create-recording-7",
        "type": "Create",
        "kind": "asset.added",
        "published": "2026-10-15T08:12:03.000Z",
        "actor": {"type": "Person", "id": "lens:account/1", "name": "Sam"},
        "object": {"type": "Audio", "id": "lens:recording/7", "name": "Call with the school", "url": "/resources/7", "media": "audio"},
        "target": None,
        "tag": [],
        "summary": "Sam added “Call with the school”",
        "space": 1,
        "lens": {"source": "recordings", "pos": ["2026-10-15T08:12:03+00:00", 7], "untrusted": ["object.name"], "cause": None},
    },
)


# ---------- sources ----------
class Source:
    """One family of rows read as activities. A source names itself, may list the kinds it makes (the catalog's kinds
    whose `source` is its name count too), and reads:

    - `read(db, cfg, pos, spaces, limit, hint)`: activities strictly after `pos` ([time, id], or [time, None] for
      after every row at `time`), oldest first, at most `limit`; only namespaces in `spaces` unless it is None. Each
      activity's `lens.pos` is its row's position. `hint` is narrowing it may use, like {"actors": [...]}: what the
      triggers that want its kinds let through (follow gives each source its own).
    - `page(db, cfg, before, viewer, flt, limit)`: newest first, strictly before `before` (`key` of the last one
      shown) or from the newest, with what `viewer` may see and the filters it can apply cheaply; the core checks
      every filter again.
    - `get(db, cfg, activity_id)`: one activity by its id, or None (also for ids that aren't its own).
    - `get_many(db, cfg, ids)`: the activities among `ids` that are its own, in any order; by default one `get` each,
      so a source with a table to read overrides it with one query for all of them.
    - `now(db)` and `back(db, seconds)`: the position of now, and of `seconds` ago, in its own terms.
    - `key(a)`: the position `page` orders by, [time, id]; its `lens.pos` unless it pages by another column.
    """

    name = ""
    kinds: tuple[str, ...] = ()

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        raise NotImplementedError

    def page(self, db, cfg, before, viewer, flt, limit):
        raise NotImplementedError

    def get(self, db, cfg, activity_id):
        return None

    def get_many(self, db, cfg, ids):
        return [a for a in (self.get(db, cfg, i) for i in ids) if a]

    def now(self, db):
        return [store.now(), None]

    def back(self, db, seconds):
        return [ago(seconds), None]

    def key(self, a):
        return a["lens"]["pos"]


_SOURCES: dict[str, Source] = {}


def register_source(src):
    """Add a source to the stream (registering a name again replaces it)."""
    if not NAME_RX.match(getattr(src, "name", "") or ""):
        raise ValueError("a source has a name of lowercase letters, digits and _ . : -")
    _SOURCES[src.name] = src
    return src


def sources():
    return list(_SOURCES.values())


def produces(src):
    """The kinds a source can make: those it lists and the catalog's kinds that name it."""
    return set(src.kinds) | {k for k, v in _KINDS.items() if v.source == src.name}


def after_sql(col, pos, params, op=">", table=None):
    """The SQL for "after `pos`" ([time, id], id None for after every row at time) on a table read by (`col`, id),
    with its parameters added to `params`; op "<" for "before" when paging newest first. With `table`, the id is a
    key of that table's records, so it compares as SurrealDB orders record ids."""
    params["pt"] = pos[0]
    if pos[1] is None:
        return f"{col} {op} $pt"
    params["pi"] = R(table, pos[1]) if table else pos[1]
    return f"({col} {op} $pt OR ({col} = $pt AND id {op} $pi))"


# ---------- who sees what ----------
def viewer(account=None, spaces=None, admin=False):
    """Who reads the timeline: their account id, the namespaces they read (None: every one, for admins) and whether
    they are an admin."""
    return {"account": account, "spaces": None if spaces is None else set(spaces), "admin": bool(admin)}


def spaces_of(a):
    """Every namespace an activity belongs to."""
    out = [a.get("space")] + list((a.get("lens") or {}).get("spaces") or [])
    return [s for s in dict.fromkeys(out) if s is not None]


def visible(a, who):
    """Whether `who` (viewer) may see an activity: `lens.audience` owner is its owner alone, admins is admins, and
    space (the default) is everyone who reads one of its namespaces (admins read all; one with no namespace is for
    admins)."""
    lens = a.get("lens") or {}
    aud = lens.get("audience") or "space"
    if aud == "owner":
        return who.get("account") is not None and lens.get("owner") == who["account"]
    if aud == "admins" or who.get("admin"):
        return bool(who.get("admin"))
    readable = who.get("spaces") or set()
    return any(s in readable for s in spaces_of(a))


# ---------- the event log ----------
@contextlib.contextmanager
def caused_by(run):
    """Activities emitted inside are caused by an automation's run: {"automation": 4, "run": 51, "depth": 1}."""
    if not (isinstance(run, dict) and all(isinstance(run.get(k), int) for k in ("automation", "run", "depth"))):
        raise ValueError("a cause is {automation, run, depth}")
    token = _cause.set({k: run[k] for k in ("automation", "run", "depth")})
    try:
        yield
    finally:
        _cause.reset(token)


def cause():
    """The run whose actions are running now ({automation, run, depth}), or None."""
    c = _cause.get()
    return dict(c) if c else None


def _key_ref(key):
    return R("event_log_key", hashlib.sha1(str(key).encode()).hexdigest())


def emit(
    db,
    kind,
    *,
    actor,
    obj,
    space,
    summary,
    target=None,
    tag=None,
    key=None,
    published=None,
    spaces=None,
    audience=None,
    owner=None,
    untrusted=None,
):
    """Log an activity that no row of its own records (a deletion, a webhook call) and wake the followers in this
    process. Its id (`log-<n>`). With `key`, emitting the same key again changes nothing and gives the first id, so a
    retry never doubles it. Inside `caused_by`, the activity carries that cause."""
    k = _KINDS.get(kind)
    if not k:
        raise ValueError(f"no kind {kind}; register it with events.kind first")
    if k.source != "log":
        raise ValueError(f"{kind} is read from its own rows ({k.source}), not emitted")
    if key is not None:
        known = db.one("SELECT log FROM $r", r=_key_ref(key))
        if known:
            return f"log-{known['log']}"
    n = db.next_id("event_log")
    at = store.now()
    a = build(
        f"log-{n}",
        kind,
        actor=actor,
        obj=obj,
        summary=summary,
        space=space,
        source="log",
        pos=[at, n],
        published=published or at,
        target=target,
        tag=tag,
        untrusted=untrusted,
        cause=cause(),
        spaces=spaces,
        audience=audience,
        owner=owner,
    )
    check_activity(a)
    row = store.clean(
        {
            "kind": kind,
            "type": a["type"],
            "published": a["published"],
            "logged_at": at,
            "actor": a["actor"],
            "object": a["object"],
            "target": a["target"],
            "tag": a["tag"],
            "summary": a["summary"],
            "space": space,
            "spaces": a["lens"].get("spaces"),
            "audience": a["lens"].get("audience"),
            "owner": owner,
            "untrusted": a["lens"]["untrusted"],
            "cause": a["lens"]["cause"],
            "key": key,
        }
    )
    st, p = ["CREATE $e CONTENT $d"], {"e": R("event_log", n), "d": row}
    if key is not None:
        st.insert(0, "CREATE $k CONTENT $kd")
        p.update(k=_key_ref(key), kd={"key": str(key)[:500], "log": n, "at": at})
    try:
        db.run(st, **p)
    except Exception as e:  # noqa: BLE001 - only a key that's already there means someone else logged it first
        if key is None or "already exists" not in str(e):
            raise
        return f"log-{db.one('SELECT log FROM $r', r=_key_ref(key))['log']}"
    wake()
    return a["id"]


LOG_FIELDS = (
    "record::id(id) AS n, kind, published, logged_at, actor, object, target, tag, summary, space, spaces, audience, owner, untrusted, cause"
)


def _from_log(r):
    return build(
        f"log-{r['n']}",
        r["kind"],
        actor=r["actor"],
        obj=r["object"],
        summary=r["summary"],
        space=r.get("space"),
        source="log",
        pos=[r["logged_at"], r["n"]],
        published=r["published"],
        target=r.get("target"),
        tag=r.get("tag"),
        untrusted=r.get("untrusted") or [],
        cause=r.get("cause"),
        spaces=r.get("spaces"),
        audience=r.get("audience"),
        owner=r.get("owner"),
    )


def space_sql(spaces, params, col="space", many=None):
    """SQL keeping rows of the namespaces in `spaces` (None: all), its parameters added to `params`."""
    if spaces is None:
        return "true"
    params["sp"] = sorted(spaces)
    return f"({col} IN $sp" + (f" OR {many} CONTAINSANY $sp)" if many else ")")


class LogSource(Source):
    """event_log: activities that no row of their own records, each with the kind it was emitted as. Followed by
    (logged_at, id): ids are given out in order, and a row written late is caught by the lookback. Paged by
    `published`."""

    name = "log"

    def _kinds_sql(self, flt, params):
        ks = (flt or {}).get("kinds")
        if ks is None:
            return "true"
        params["ks"] = sorted(ks)
        return "kind IN $ks"

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p = {}
        where = f"{after_sql('logged_at', pos, p, table='event_log')} AND {space_sql(spaces, p, many='spaces')}"
        rows = db.rows(f"SELECT {LOG_FIELDS}, id AS rid FROM event_log WHERE {where} ORDER BY logged_at, rid LIMIT {int(limit)}", **p)
        return [_from_log(r) for r in rows if r.get("kind") in _KINDS]

    def page(self, db, cfg, before, who, flt, limit):
        p = {}
        where = [self._kinds_sql(flt, p)]
        if before is not None:
            where.append(after_sql("published", before, p, "<", table="event_log"))
        if not who.get("admin"):
            p["me"] = who.get("account")
            readable = space_sql(who.get("spaces") or set(), p, many="spaces")
            where.append(f"(((audience = NONE OR audience = 'space') AND {readable}) OR (audience = 'owner' AND owner = $me))")
        else:
            p["me"] = who.get("account")
            where.append("(audience != 'owner' OR owner = $me)")
        for k, op in (("since", ">"), ("until", "<=")):
            if (flt or {}).get(k):
                p[k] = flt[k]
                where.append(f"published {op} ${k}")
        rows = db.rows(
            f"SELECT {LOG_FIELDS}, id AS rid FROM event_log WHERE {' AND '.join(where)} ORDER BY published DESC, rid DESC LIMIT {int(limit)}",
            **p,
        )
        return [_from_log(r) for r in rows if r.get("kind") in _KINDS]

    def get(self, db, cfg, activity_id):
        m = re.match(r"^log-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {LOG_FIELDS} FROM $r", r=R("event_log", int(m.group(1))))
        return _from_log(r) if r and r.get("kind") in _KINDS else None

    def get_many(self, db, cfg, ids):
        ns = [int(m.group(1)) for m in (re.match(r"^log-(\d+)$", i) for i in ids) if m]
        if not ns:
            return []
        rows = db.rows(f"SELECT {LOG_FIELDS} FROM event_log WHERE id IN $rs", rs=[R("event_log", n) for n in ns])
        return [_from_log(r) for r in rows if r.get("kind") in _KINDS]

    def key(self, a):
        return [a["published"], a["lens"]["pos"][1]]


register_source(LogSource())


# ---------- following ----------
def _check_name(consumer):
    if not NAME_RX.match(consumer or ""):
        raise ValueError("a consumer is named with lowercase letters, digits and _ . : - (automations, say)")


def _cursor_ref(consumer):
    return R("event_cursor", consumer)


def _later_of(a, b):
    return a if b is None or pos_key(a) >= pos_key(b) else b


def _start(db, consumer, at=None):
    """Every source's cursor at now: what happened before isn't read, not even by the lookback."""
    pos = {s.name: s.now(db) for s in sources()}
    db.q("UPSERT $c SET pos = $p, floor = $p, at = $t", c=_cursor_ref(consumer), p=pos, t=at or store.now())
    return pos


def idle(db, consumer):
    """A consumer with nothing to do moves every cursor to now, so nothing that happens meanwhile replays later."""
    _check_name(consumer)
    _start(db, consumer)


def _in(a, want, spaces):
    if want is not None and a.get("kind") not in want:
        return False
    return spaces is None or any(s in spaces for s in spaces_of(a))


def _order(a):
    """Oldest first across sources: by the time of each one's position (its published time when the position isn't a
    time), then source and id."""
    p = a["lens"]["pos"]
    t = _tkey(p[0])
    if not isinstance(t, float):
        t = _parse(a["published"]).timestamp()
    return (t, a["lens"]["source"], _ikey(p[1]))


def follow(db, cfg, consumer, kinds=None, spaces=None, limit=PAGE, hint=None):
    """What happened since this consumer's last commit: (activities oldest first, commit). Handle them in order, then
    call commit() to save where the next look starts. Only `kinds` (and their descendants; None: every kind) in
    `spaces` (None: every namespace and none). A source with more waiting gives `limit` at a time; once caught up, a
    read gives the last LOOKBACK seconds again (minus what this process already handled), so claim each before acting
    on it. The first look only marks where this consumer starts. `hint` gives sources narrowing they may use, by
    source name: {"sensor_readings": {"actors": [...]}} (match.compile_triggers makes it)."""
    _check_name(consumer)
    want = expand(kinds)
    spaces = None if spaces is None else set(spaces)
    st = db.one("SELECT pos, floor FROM $c", c=_cursor_ref(consumer))
    if not st or not isinstance(st.get("pos"), dict):
        _start(db, consumer)
        return [], lambda: None
    pos, floor = dict(st["pos"]), dict(st.get("floor") or {})
    moved, floors, out = {}, {}, []
    with _lock:
        seen = dict(_seen.get(consumer) or {})
    for s in sources():
        here = pos.get(s.name)
        if here is None or (want is not None and not produces(s) & want):
            moved[s.name] = floors[s.name] = s.now(db)  # nobody wants these: when someone does, they start from then
            continue
        mine = (hint or {}).get(s.name)
        rows = s.read(db, cfg, here, spaces, limit, mine)
        if rows:
            moved[s.name] = rows[-1]["lens"]["pos"]
        if len(rows) < limit:  # caught up: the last LOOKBACK seconds again, not before where this consumer started
            have = {a["id"] for a in rows}
            since = s.back(db, LOOKBACK)
            if floor.get(s.name) is not None and pos_key(floor[s.name]) > pos_key(since):
                since = floor[s.name]
            for _ in range(PAGE_ROUNDS):
                if pos_key(since) >= pos_key(here):
                    break
                back = s.read(db, cfg, since, spaces, limit, mine)
                rows += [a for a in back if pos_key(a["lens"]["pos"]) <= pos_key(here) and a["id"] not in have and a["id"] not in seen]
                if len(back) < limit:
                    break
                since = back[-1]["lens"]["pos"]
        out += [a for a in rows if _in(a, want, spaces)]
    out.sort(key=_order)

    def commit():
        cur = db.one("SELECT pos, floor FROM $c", c=_cursor_ref(consumer)) or {}
        p, f = dict(cur.get("pos") or pos), dict(cur.get("floor") or floor)
        for name, at in moved.items():
            p[name] = _later_of(at, p.get(name))
        for name, at in floors.items():
            f[name] = _later_of(at, f.get(name))
        db.q("UPSERT $c SET pos = $p, floor = $f, at = $t", c=_cursor_ref(consumer), p=p, f=f, t=store.now())
        with _lock:
            mine = _seen.setdefault(consumer, {})
            for a in out:
                mine[a["id"]] = None
            while len(mine) > 20000:
                mine.pop(next(iter(mine)))

    return out, commit


def _claim_ref(consumer, key):
    return R("event_claim", hashlib.sha1(f"{consumer}|{key}".encode()).hexdigest())


def _known(ref_id):
    with _lock:
        _claimed[ref_id] = None
        while len(_claimed) > 50000:
            _claimed.pop(next(iter(_claimed)))


def claim(db, consumer, key):
    """True for the one process (and the one time) that claims `key` for `consumer`, such as an activity's id."""
    _check_name(consumer)
    ref = _claim_ref(consumer, key)
    with _lock:
        if ref.id in _claimed:
            return False
    try:
        db.run(["CREATE $r CONTENT $d"], r=ref, d={"consumer": consumer, "key": str(key)[:500], "at": store.now()})
    except Exception as e:  # noqa: BLE001 - only a claim that's already there means another process has it
        if "already exists" not in str(e):
            raise
        _known(ref.id)
        return False
    _known(ref.id)
    return True


def unclaimed(db, consumer, keys):
    """The keys nobody has claimed yet, in one query: for skipping what is taken before claiming one by one (it claims
    nothing)."""
    refs_ = {k: _claim_ref(consumer, k) for k in keys}
    with _lock:
        todo = {k: r for k, r in refs_.items() if r.id not in _claimed}
    if not todo:
        return []
    taken = set(db.values("SELECT VALUE record::id(id) FROM event_claim WHERE id IN $ids", ids=list(todo.values())))
    for r in todo.values():
        if r.id in taken:
            _known(r.id)
    return [k for k, r in todo.items() if r.id not in taken]


def wake():
    """Have the followers in this process look at once (emit calls it)."""
    with _wakes:
        _generation[0] += 1
        _wakes.notify_all()


def generation():
    """How many wakes this process has had: take it before a look, and give it to `wait` after."""
    with _wakes:
        return _generation[0]


def wait(seconds, stop=None, since=None):
    """Sleep until a wake after `since` (a `generation()`; default: now), `stop` (a threading.Event) is set, or
    `seconds` pass; True when woken. Every follower waiting is woken, and a wake that came during the look counts."""
    until = time.monotonic() + max(0.0, float(seconds))
    with _wakes:
        seen = _generation[0] if since is None else since
        while _generation[0] == seen:
            left = until - time.monotonic()
            if left <= 0 or (stop is not None and stop.is_set()):
                return False
            _wakes.wait(min(0.5, left))
        return True


def section(cfg):
    return {"keep_days": 30, "poll_seconds": 5, **(cfg.get("events") or {})}


def poll_seconds(cfg):
    """How often followers look when nothing wakes them (events.poll_seconds)."""
    return max(1, int(section(cfg)["poll_seconds"] or 5))


# ---------- the timeline ----------
def check_filter(flt):
    """Timeline filters, cleaned: kinds (names; their descendants count too), actor (an actor's id, or several),
    spaces (namespace ids), tag (an entity's id or name), since (published after) and until (at or before), q (words in
    the summary) and where (a match tree on the activity). ValueError naming the first problem."""
    from . import match

    flt = dict(flt or {})
    extra = sorted(set(flt) - set(FILTERS))
    if extra:
        raise ValueError(f"no filter {extra[0]}; filters are {', '.join(FILTERS)}")
    out = {}
    if flt.get("kinds"):
        ks = [flt["kinds"]] if isinstance(flt["kinds"], str) else list(flt["kinds"])
        expand(ks)
        out["kinds"] = ks
    if flt.get("actor"):
        a = [flt["actor"]] if isinstance(flt["actor"], str) else list(flt["actor"])
        for x in a:
            _ref(x, "actor")
        out["actor"] = a
    if flt.get("spaces") is not None:
        if not isinstance(flt["spaces"], (list, set, tuple)) or not all(isinstance(s, int) for s in flt["spaces"]):
            raise ValueError("spaces is a list of namespace ids")
        out["spaces"] = set(flt["spaces"])
    if flt.get("tag"):
        out["tag"] = str(flt["tag"]).strip()[:200]
    for k in ("since", "until"):
        if flt.get(k):
            try:
                out[k] = stamp(flt[k])
            except ValueError:
                raise ValueError(f"{k} is a time like 2026-10-15T16:21:58Z") from None
    if flt.get("q"):
        out["q"] = [w for w in str(flt["q"]).casefold().split() if w][:20]
    if flt.get("where"):
        out["where"] = match.check(flt["where"], "where")
    return out


def _matches(a, flt):
    from . import match

    if flt.get("actor") and (a.get("actor") or {}).get("id") not in flt["actor"]:
        return False
    if flt.get("spaces") is not None and not any(s in flt["spaces"] for s in spaces_of(a)):
        return False
    if flt.get("tag"):
        t = flt["tag"].casefold()
        if not any(str(x.get("id") or "").casefold() == t or str(x.get("name") or "").casefold() == t for x in a.get("tag") or []):
            return False
    if flt.get("since") and not a["published"] > flt["since"]:
        return False
    if flt.get("until") and not a["published"] <= flt["until"]:
        return False
    if flt.get("q"):
        text = a.get("summary", "").casefold()
        if not all(w in text for w in flt["q"]):
            return False
    return not flt.get("where") or match.test(flt["where"], a)


def _wanted(flt):
    """The kinds a timeline page shows: those asked for (with descendants, noisy ones only when named), else every
    kind that isn't noisy."""
    if flt.get("kinds"):
        named = set(flt["kinds"])
        return {k for k in expand(flt["kinds"]) if not _KINDS[k].noisy or k in named}
    return {k for k, v in _KINDS.items() if not v.noisy}


def _encode(cur):
    raw = json.dumps(cur, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(before):
    try:
        cur = json.loads(base64.urlsafe_b64decode(before + "=" * (-len(before) % 4)))
        if not isinstance(cur, dict) or not all(v is None or (isinstance(v, list) and len(v) == 2) for v in cur.values()):
            raise ValueError
        return cur
    except (ValueError, TypeError):
        raise ValueError("before is the `next` of an earlier page") from None


def page(db, cfg, who, before=None, flt=None, limit=50, keep=None):
    """A page of the timeline for `who` (viewer), newest first: {items, next}. `next` goes in `before` for the page
    after (None at the end). Filters (check_filter) thin a page out; it reads on, up to PAGE_ROUNDS source pages each,
    so a filtered page isn't short while there is more. `keep(activities) -> ids kept` thins it out further by what
    another table holds (the timeline's review marks), called once for each source page read, not for each row."""
    flt = check_filter(flt)
    limit = max(1, min(int(limit), 200))
    want = _wanted(flt)
    sflt = {**flt, "kinds": want}
    cur = _decode(before) if before else None
    srcs = [s for s in sources() if produces(s) & want and (cur is None or s.name in cur)]
    at = {s.name: (cur or {}).get(s.name) for s in srcs}
    got: dict[str, list] = {s.name: [] for s in srcs}
    reads = dict.fromkeys(got, 0)
    done: set[str] = set()
    kept: set[str] = set()

    def fits(a):
        return a.get("kind") in want and visible(a, who) and _matches(a, flt)

    def ok(a):
        return fits(a) and (keep is None or a["id"] in kept)

    def k(s, a):  # the source's own position breaks ties, so rows a millisecond apart keep its order
        return (a["published"], s.name, pos_key(s.key(a)))

    def fetch(s):
        rows = s.page(db, cfg, at[s.name], who, sflt, limit)[:limit]
        reads[s.name] += 1
        got[s.name] += rows
        if keep is not None:
            kept.update(keep([a for a in rows if fits(a)]))
        if rows:
            at[s.name] = s.key(rows[-1])
        if len(rows) < limit:
            done.add(s.name)

    for s in srcs:
        fetch(s)
    while True:
        # past the oldest row read from a source with more to read, that source may have something newer unread
        open_ = [s for s in srcs if s.name not in done]
        horizon = max((k(s, got[s.name][-1]) for s in open_), default=None)
        fit = sorted(
            ((k(s, a), s.name, a) for s in srcs for a in got[s.name] if ok(a) and (horizon is None or k(s, a) >= horizon)),
            key=lambda x: x[0],
            reverse=True,
        )
        if len(fit) >= limit or not open_:
            break
        more = [s for s in open_ if k(s, got[s.name][-1]) == horizon and reads[s.name] < PAGE_ROUNDS]
        if not more:
            break
        for s in more:
            fetch(s)
    items = [a for _, _, a in fit[:limit]]
    shown = {a["id"] for a in items}
    nxt = {}
    for s in srcs:
        last, stopped = (cur or {}).get(s.name), False
        for a in got[s.name]:
            if ok(a) and a["id"] not in shown:
                stopped = True
                break
            last = s.key(a)
        if stopped or s.name not in done:
            nxt[s.name] = last
    return {"items": items, "next": _encode(nxt) if nxt else None}


def get(db, cfg, activity_id, who):
    """One activity `who` may see, by its id; None when there's none or it isn't theirs to see."""
    if not isinstance(activity_id, str) or not ID_RX.match(activity_id):
        return None
    for s in sources():
        a = s.get(db, cfg, activity_id)
        if a:
            return a if visible(a, who) else None
    return None


def get_many(db, cfg, ids, who):
    """The activities among `ids` that `who` may see, by id ({} for none), with one look per source for all of them
    (Source.get_many) rather than one per id."""
    left = [i for i in dict.fromkeys(ids) if isinstance(i, str) and ID_RX.match(i)]
    out = {}
    for s in sources():
        if not left:
            break
        found = {a["id"]: a for a in s.get_many(db, cfg, left) if a and a.get("id") in left}
        out |= {i: a for i, a in found.items() if visible(a, who)}
        left = [i for i in left if i not in found]
    return out


# ---------- keeping it small ----------
def tidy(db, cfg, now=None):
    """Drop event_log rows older than events.keep_days and claims older than a day, at most once an hour per process
    (routines.run_due calls it every 30 seconds); the cut-off, or 0 when it didn't look."""
    t = time.monotonic()
    if now is None and _swept["at"] and t - _swept["at"] < SWEEP_SECONDS:
        return 0
    _swept["at"] = t
    days = int(section(cfg)["keep_days"] or 30)
    cut = ago(days * 86400, now)
    db.q("DELETE event_log WHERE logged_at < $c", c=cut)
    db.q("DELETE event_log_key WHERE at < $c", c=cut)
    db.q("DELETE event_claim WHERE at < $c", c=ago(CLAIM_DAYS * 86400, now))
    return cut


from . import events_content  # noqa: E402, F401 - the recordings source, registered with the stream once it is loaded
from . import events_streams  # noqa: E402, F401 - sensors, chat, webhooks and system kinds, the same way
from . import events_approvals  # noqa: E402, F401 - approvals waiting and decided, after the content sources it reuses
from . import events_automations  # noqa: E402, F401 - automations' runs, and the automation kinds
from . import circles  # noqa: E402, F401 - what people share with their circles, as share.sent and share.received
from . import event_reactions  # noqa: E402, F401 - reactions on activities (reaction.added), logged by people and agents
