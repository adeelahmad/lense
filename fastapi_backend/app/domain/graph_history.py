"""The entity graph's history: every change is an append-only event, so the graph can be seen as of any version,
compared between two versions, and an entity's own history read (docs/graph-history.md).

What is versioned is what people and agents curate: the rows of `entity` (name, type, description, hidden, defined),
`entity_alias` (the other ways a name is said), `entity_link` (the same thing in two namespaces) and `entity_distinct`
(two things someone said aren't one). Mentions are not: analysis makes them from transcripts, whose edits have their
own history (segment_edit). An as-of view uses today's mentions.

A `graph_event:<n>` row is one change, and `n` is the graph's version after it:
{at, op, actor, via, why, spaces, entities, ops: [{t: table, k: key, b: row before, a: row after, s: spaces}], origin}.
`op` names what happened (entity.rename, entity.merge, link.add, analysis, ...); `actor` who did it (an email,
`routine:<id>`, `analysis`, `system`); `via` through what (web, token, oauth, assistant, mcp, routine, analysis, cli);
`origin` what it came from (a graph change, a merge, a routine run, a recording, an approval). Events are never
changed or deleted; undoing a change is a new event.

Writers wrap their writes: `with graph_history.change(db, "entity.rename", entities=[eid]):` snapshots the rows of the
entities (and their aliases, links and distinct pairs) before and after the block, and records what differs. Changes
inside a change join it, so a define that folds others in by a merge is one event. Who and why come from
`graph_history.acting(actor=..., via=..., why=...)` around the work (the API sets the caller for each request).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re

from . import store

R = store.R
log = logging.getLogger("lens")
TABLES = ("entity", "entity_alias", "entity_link", "entity_distinct")
VIAS = ("web", "token", "oauth", "assistant", "mcp", "routine", "workflow", "analysis", "cli", "system")
SUMMARY = "record::id(id) AS version, at, op, actor, via, why, spaces, entities, origin, array::len(ops) AS changes"
TAG_NAME = re.compile(r"^[\w .:+-]{1,80}$")

_acting: contextvars.ContextVar[dict | None] = contextvars.ContextVar("lens_graph_acting", default=None)
_open: contextvars.ContextVar[Change | None] = contextvars.ContextVar("lens_graph_change", default=None)


# ---------- who, through what, why ----------
@contextlib.contextmanager
def acting(actor=None, via=None, why=None, **origin):
    """Changes made inside are recorded as made by `actor`, through `via`, because of `why`, from `origin` (routine,
    run, workflow, graph_change, recording, approval, ...). Nested ones add to (and override) the outer ones."""
    outer = _acting.get() or {}
    mine = store.clean({"actor": actor, "via": via, "why": why})
    token = _acting.set({**outer, **mine, "origin": {**(outer.get("origin") or {}), **store.clean(origin)}})
    try:
        yield
    finally:
        _acting.reset(token)


def note(**kw):
    """Fill in the current `acting` block in place (the API learns who is calling after the request has started)."""
    cur = _acting.get()
    if cur is not None:
        cur.update(store.clean(kw))


def who():
    return dict(_acting.get() or {})


# ---------- snapshots ----------
def _key(table, k):
    return int(k) if table == "entity" else str(k)


def _pairs(rows):
    out = {}
    for r in rows:
        r = dict(r)
        k = r.pop("id")
        out[k] = r
    return out


def _snap(db, eids=(), keys=()):
    """{(table, key): row or None} for these entities, the aliases, links and distinct pairs that name them, and the
    records named in `keys` [(table, key)]."""
    out = {}
    ids = sorted({int(e) for e in eids})
    if ids:
        refs = [R("entity", i) for i in ids]
        got = _pairs(db.rows("SELECT * FROM entity WHERE id IN $ids", ids=refs))
        for i in ids:
            out[("entity", i)] = got.get(i)
        for k, r in _pairs(db.rows("SELECT * FROM entity_alias WHERE entity IN $e", e=ids)).items():
            out[("entity_alias", str(k))] = r
        for t in ("entity_link", "entity_distinct"):
            for k, r in _pairs(db.rows(f"SELECT * FROM {t} WHERE a IN $e OR b IN $e", e=ids)).items():
                out[(t, str(k))] = r
    by_table = {}
    for t, k in keys:
        if (t, _key(t, k)) not in out:
            by_table.setdefault(t, set()).add(_key(t, k))
    for t, ks in by_table.items():
        got = _pairs(db.rows(f"SELECT * FROM {t} WHERE id IN $ids", ids=[R(t, k) for k in sorted(ks, key=str)]))
        for k in ks:
            out[(t, k)] = got.get(k)
    return out


def _row_spaces(t, row, space_of):
    if not row:
        return set()
    if t in ("entity", "entity_alias"):
        return {row.get("space")} - {None}
    return {space_of(row.get("a")), space_of(row.get("b"))} - {None}


class Change:
    """One change being made: what it touches, what it was before."""

    def __init__(self, db, op, why, origin):
        self.db, self.op, self.why, self.origin = db, op, why, dict(origin)
        self.before: dict = {}
        self.ids: set[int] = set()
        self.keys: set = set()
        self.version: int | None = None
        self.always = False
        self.marked: set[int] = set()

    def touch(self, eids=(), aliases=(), keys=()):
        """Watch these entities (before they change), alias keys [(space, key)] and records [(table, key)]."""
        new_ids = {int(e) for e in eids if e is not None} - self.ids
        new_keys = {("entity_alias", f"{int(s)}:{k}") for s, k in aliases} | {(t, _key(t, k)) for t, k in keys}
        new_keys -= self.keys
        if new_ids or new_keys:
            for k, v in _snap(self.db, new_ids, new_keys).items():
                self.before.setdefault(k, v)
            self.ids |= new_ids
            self.keys |= new_keys

    def created(self, *eids):
        """Entities this change made: they were nothing before."""
        for e in eids:
            if e is not None:
                self.before.setdefault(("entity", int(e)), None)
                self.ids.add(int(e))

    def add(self, **origin):
        self.origin.update(store.clean(origin))

    def mark(self, *eids):
        """Record the event even when no versioned row changed (a moved mention), as about these entities."""
        self.always = True
        self.marked |= {int(e) for e in eids if e}

    def ops(self):
        after = _snap(self.db, self.ids, self.keys | set(self.before))
        out, ents = [], {}
        for (t, k), row in {**self.before, **after}.items():
            if t == "entity":
                ents[k] = after.get((t, k)) or self.before.get((t, k))
        for t, k in sorted(set(self.before) | set(after), key=lambda x: (TABLES.index(x[0]), str(x[1]))):
            b, a = self.before.get((t, k)), after.get((t, k))
            if b != a:
                out.append({"t": t, "k": k, "b": b, "a": a})
        missing = {
            int(x)
            for o in out
            if o["t"] in ("entity_link", "entity_distinct")
            for x in ((o.get("a") or o.get("b")).get("a"), (o.get("a") or o.get("b")).get("b"))
            if x is not None and int(x) not in ents
        }
        if missing:
            for r in self.db.rows("SELECT record::id(id) AS id, space FROM entity WHERE id IN $ids", ids=[R("entity", i) for i in missing]):
                ents[r["id"]] = r

        def space_of(e):
            return (ents.get(int(e)) or {}).get("space") if e is not None else None

        for o in out:
            o["s"] = sorted(_row_spaces(o["t"], o.get("a"), space_of) | _row_spaces(o["t"], o.get("b"), space_of))
        return out


@contextlib.contextmanager
def change(db, op, entities=(), aliases=(), keys=(), why=None, **origin):
    """Record what the block changes as one event (none when nothing changed). Inside another change, it joins it."""
    outer = _open.get()
    if outer is not None:
        outer.touch(entities, aliases, keys)
        outer.add(**origin)
        yield outer
        return
    ch = Change(db, op, why, origin)
    ch.touch(entities, aliases, keys)
    token = _open.set(ch)
    try:
        yield ch
    finally:
        _open.reset(token)
    ops = ch.ops()
    spaces = None
    if ch.marked and not ops:
        spaces = sorted(set(db.values("SELECT VALUE space FROM entity WHERE id IN $i", i=[R("entity", e) for e in ch.marked])))
    ch.version = record(db, ch.op, ops, why=ch.why, origin=ch.origin, entities=ch.marked, spaces=spaces, always=ch.always)


def record(db, op, ops, why=None, origin=None, actor=None, via=None, entities=(), spaces=None, always=False):
    """Write one event for these ops; its version (None when there is nothing to record)."""
    if not ops and not always:
        return None
    w = who()
    n = db.next_id("graph_event")
    ents = {int(o["k"]) for o in ops if o["t"] == "entity"} | {int(x) for o in ops if o["t"] != "entity" for x in _ends(o)}
    ents = sorted(ents | {int(e) for e in entities})
    row = store.clean(
        {
            "at": store.now(),
            "op": op,
            "actor": actor or w.get("actor") or (w.get("via") if w.get("via") in ("analysis", "routine", "cli") else None) or "system",
            "via": via or w.get("via") or "system",
            "why": (str(why or w.get("why") or "")[:500]) or None,
            "spaces": sorted({s for o in ops for s in o["s"]} | set(spaces or [])),
            "entities": ents,
            "ops": ops,
            "origin": {**(w.get("origin") or {}), **store.clean(origin or {})} or None,
        }
    )
    db.q("CREATE $r CONTENT $d", r=R("graph_event", n), d=row)
    if n == 1 or n % CHECKPOINT_EVERY == 0:  # the graph before its first change, then every so often, to replay from
        try:
            checkpoint(db, 0 if n == 1 else n)
        except Exception:  # noqa: BLE001 - a missed checkpoint is taken later; the change is still recorded
            log.exception("graph checkpoint at %s failed", n)
    return n


def _ends(o):
    row = o.get("a") or o.get("b") or {}
    if o["t"] == "entity_alias":
        return [row["entity"]] if row.get("entity") is not None else []
    return [x for x in (row.get("a"), row.get("b")) if x is not None]


# ---------- reading the history ----------
def head(db):
    """The graph's current version (0 before anything was recorded)."""
    got = db.values("SELECT VALUE n FROM $r", r=R("seq", "graph_event"))
    return int(got[0]) if got and got[0] is not None else 0


def _visible(ev, spaces):
    return spaces is None or set(ev.get("spaces") or []) <= set(spaces)


def versions(db, spaces=None, entity=None, before=None, limit=50):
    """Events, newest first, whose namespaces are all in `spaces` (None: all); with `entity`, the ones that touched it."""
    where, args = [], {"n": int(limit)}
    if spaces is not None:
        where.append("spaces ALLINSIDE $s")
        args["s"] = sorted(spaces)
    if entity is not None:
        where.append("$e INSIDE entities")
        args["e"] = int(entity)
    if before is not None:
        where.append("id < $b")
        args["b"] = R("graph_event", int(before))
    sql = f"SELECT {SUMMARY} FROM graph_event" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY version DESC LIMIT $n"
    rows = db.rows(sql, **args)
    names = _names(db, {e for r in rows for e in r.get("entities") or []}, rows_of=None)
    tags = _tags_by_version(db)
    for r in rows:
        r["names"] = {str(e): names[e] for e in r.get("entities") or [] if e in names}
        r["tags"] = tags.get(r["version"], [])
    return rows


def activity(db, resource, before=None, limit=100):
    """The graph changes in a resource's activity history (activity.history), newest first: an entity's
    (`entity:<id>`), a namespace's other than what analysis found (`space:<id>`), and what analysing a recording
    changed (`recording:<id>`). None for other resources."""
    table, _, key = str(resource).partition(":")
    if not key.isdigit():
        return []
    where, args = (
        {"entity": "$k INSIDE entities", "space": "$k INSIDE spaces AND op != 'analysis'", "recording": "origin.recording = $k"}.get(table),
        {"k": int(key), "n": int(limit)},
    )
    if where is None:
        return []
    if before:
        where += " AND at < $b"
        args["b"] = str(before)
    rows = db.rows(f"SELECT {SUMMARY} FROM graph_event WHERE {where} ORDER BY version DESC LIMIT $n", **args)
    return [
        {
            "id": None,
            "at": r["at"],
            "kind": "change",
            "action": f"graph.{r['op']}",
            "resources": [resource],
            "email": r.get("actor"),
            "ok": True,
            "detail": store.clean({"version": r["version"], "via": r.get("via"), "why": r.get("why"), "changes": r.get("changes")}),
        }
        for r in rows
    ]


def event(db, version, spaces=None):
    ev = db.one("SELECT *, record::id(id) AS version FROM $r", r=R("graph_event", int(version)))
    if not ev or not _visible(ev, spaces):
        raise KeyError(version)
    ev.pop("id", None)
    ev["tags"] = _tags_by_version(db).get(ev["version"], [])
    return ev


def _events_after(db, version, upto=None):
    sql = "SELECT record::id(id) AS version, ops FROM graph_event WHERE id > $v" + (" AND id <= $u" if upto is not None else "")
    return db.rows(sql + " ORDER BY version", v=R("graph_event", int(version)), u=R("graph_event", int(upto or 0)))


def live(db):
    """Today's graph: {table: {key: row}}."""
    return {t: {_key(t, k): r for k, r in _pairs(db.rows(f"SELECT * FROM {t}")).items()} for t in TABLES}


def _scoped(state, spaces):
    if spaces is None:
        return state
    sp = set(spaces)
    ents = {k: r for k, r in state["entity"].items() if r.get("space") in sp}
    return {
        "entity": ents,
        "entity_alias": {k: r for k, r in state["entity_alias"].items() if r.get("space") in sp},
        "entity_link": {k: r for k, r in state["entity_link"].items() if r.get("a") in ents and r.get("b") in ents},
        "entity_distinct": {k: r for k, r in state["entity_distinct"].items() if r.get("a") in ents and r.get("b") in ents},
    }


def state_at(db, version, spaces=None):
    """The graph as it was at `version`: today's rows with every later event walked back."""
    version = int(version)
    if version < 0 or version > head(db):
        raise ValueError(f"the graph has versions 0 to {head(db)}")
    s = live(db)
    for ev in reversed(_events_after(db, version)):
        for o in reversed(ev["ops"]):
            k = _key(o["t"], o["k"])
            if o.get("b") is None:
                s[o["t"]].pop(k, None)
            else:
                s[o["t"]][k] = o.get("b")
    return _scoped(s, spaces)


def as_of(db, version, spaces=None):
    """The graph at a version, for people: entities with their other names, links and distinct pairs."""
    s = state_at(db, version, spaces)
    al = {}
    for r in s["entity_alias"].values():
        al.setdefault(r["entity"], []).append(r["key"])
    return {
        "version": int(version),
        "entities": [
            store.clean({"id": k, **{f: r.get(f) for f in ENTITY_FIELDS}, "aliases": sorted(al.get(k, [])) or None})
            for k, r in sorted(s["entity"].items())
        ],
        "links": [[r["a"], r["b"]] for _, r in sorted(s["entity_link"].items())],
        "distinct": [[r["a"], r["b"]] for _, r in sorted(s["entity_distinct"].items())],
    }


ENTITY_FIELDS = ("space", "key", "name", "type", "description", "hidden", "hidden_reason", "defined", "builtin", "collection")


def diff(db, a, b, spaces=None):
    """What changed between versions a and b (a < b): entities added, removed and changed (field by field), and the
    aliases, links and distinct pairs added and removed."""
    a, b = int(a), int(b)
    if a > b:
        a, b = b, a
    top = head(db)
    if b > top:
        raise ValueError(f"the graph has versions 0 to {top}")
    first, last, events = {}, {}, 0
    for ev in _events_after(db, a, b):
        events += 1
        for o in ev["ops"]:
            k = (o["t"], _key(o["t"], o["k"]))
            first.setdefault(k, o.get("b"))
            last[k] = o.get("a")
    return {"from": a, "to": b, "events": events, **_describe(db, first, last, spaces)}


def _describe(db, first, last, spaces):
    """{entities, aliases, links, distinct} that differ between rows `first` and `last` ({(table, key): row})."""
    sp = set(spaces) if spaces is not None else None
    ent_space = {}
    for (t, k), row in {**first, **last}.items():
        if t == "entity" and row:
            ent_space[k] = row.get("space")
    need = {
        int(x)
        for (t, _), row in {**first, **last}.items()
        if t in ("entity_link", "entity_distinct") and row
        for x in (row.get("a"), row.get("b"))
        if int(x) not in ent_space
    }
    if need:
        for r in db.rows("SELECT record::id(id) AS id, space FROM entity WHERE id IN $i", i=[R("entity", x) for x in need]):
            ent_space[r["id"]] = r["space"]

    def seen(t, row):
        if sp is None or not row:
            return True
        if t in ("entity", "entity_alias"):
            return row.get("space") in sp
        return ent_space.get(row.get("a")) in sp and ent_space.get(row.get("b")) in sp

    out = {
        "entities": {"added": [], "removed": [], "changed": []},
        "aliases": {"added": [], "removed": []},
        "links": {"added": [], "removed": []},
        "distinct": {"added": [], "removed": []},
    }
    named = set(ent_space) | {
        int(row["entity"]) for (t, _), row in {**first, **last}.items() if t == "entity_alias" and row and row.get("entity") is not None
    }
    names = _names(db, named, rows_of={k: (last.get(("entity", k)) or first.get(("entity", k))) for k in ent_space})
    for t, k in sorted(first, key=lambda x: (TABLES.index(x[0]), str(x[1]))):
        before, after = first[(t, k)], last[(t, k)]
        if before == after or not (seen(t, before) and seen(t, after)):
            continue
        if t == "entity":
            if before is None:
                out["entities"]["added"].append(store.clean({"id": k, **{f: after.get(f) for f in ENTITY_FIELDS}}))
            elif after is None:
                out["entities"]["removed"].append(store.clean({"id": k, **{f: before.get(f) for f in ENTITY_FIELDS}}))
            else:
                fields = {
                    f: [before.get(f), after.get(f)]
                    for f in sorted(set(before) | set(after))
                    if f != "ekey" and before.get(f) != after.get(f)
                }
                if fields:
                    out["entities"]["changed"].append({"id": k, "name": after.get("name"), "fields": fields})
        elif t == "entity_alias":
            for row, kind in ((before, "removed"), (after, "added")):
                if row:
                    out["aliases"][kind].append(
                        {"key": row["key"], "entity": row["entity"], "name": names.get(row["entity"]), "space": row["space"]}
                    )
        else:
            kind = {"entity_link": "links", "entity_distinct": "distinct"}[t]
            row = after or before
            pair = {"a": row["a"], "b": row["b"], "names": [names.get(row["a"]), names.get(row["b"])]}
            out[kind]["added" if after else "removed"].append(pair)
    return out


def _names(db, eids, rows_of=None):
    out = {k: r.get("name") for k, r in (rows_of or {}).items() if r}
    rest = [e for e in eids if e not in out]
    if rest:
        for r in db.rows("SELECT record::id(id) AS id, name FROM entity WHERE id IN $i", i=[R("entity", int(e)) for e in rest]):
            out[r["id"]] = r["name"]
        left = [e for e in rest if e not in out]
        if left:  # gone since: the name from the last event that had it
            for ev in db.rows(
                "SELECT record::id(id) AS version, ops FROM graph_event WHERE entities ANYINSIDE $e ORDER BY version DESC", e=left
            ):
                for o in ev["ops"]:
                    row = o.get("b") or o.get("a")
                    if o["t"] == "entity" and row and int(o["k"]) in left and int(o["k"]) not in out:
                        out[int(o["k"])] = row.get("name")
    return out


# ---------- named versions ----------
def _tags_by_version(db):
    out = {}
    for r in db.rows("SELECT name, version FROM graph_tag ORDER BY version"):
        out.setdefault(r["version"], []).append(r["name"])
    return out


def tags(db):
    return db.rows("SELECT name, version, note, by, at FROM graph_tag ORDER BY version DESC")


def tag(db, name, version=None, note=None, by=None):
    """Name a version (default: the current one). A name is used once; naming again moves it."""
    name = " ".join(str(name or "").split())
    if not TAG_NAME.match(name):
        raise ValueError("a name is 1 to 80 letters, digits, spaces or . : + -")
    top = head(db)
    v = top if version is None else int(version)
    if not 0 <= v <= top:
        raise ValueError(f"the graph has versions 0 to {top}")
    db.q(
        "UPSERT $r CONTENT $d",
        r=R("graph_tag", name.lower()),
        d=store.clean({"name": name, "version": v, "note": (str(note)[:300] if note else None), "by": by, "at": store.now()}),
    )
    return {"name": name, "version": v}


def untag(db, name):
    key = " ".join(str(name or "").split()).lower()
    if not db.one("SELECT id FROM $r", r=R("graph_tag", key)):
        raise KeyError(name)
    db.q("DELETE $r", r=R("graph_tag", key))


def resolve(db, ref):
    """A version from a number, a name, or `head`."""
    if ref is None or str(ref).strip().lower() in ("", "head", "now", "latest"):
        return head(db)
    s = str(ref).strip()
    if s.lstrip("v").isdigit():
        return int(s.lstrip("v"))
    got = db.one("SELECT version FROM $r", r=R("graph_tag", " ".join(s.split()).lower()))
    if not got:
        raise KeyError(ref)
    return int(got["version"])


# ---------- rolling back ----------
MERGES = ("entity.merge", "merge.apply", "merge.accept")
APPLIED = ("merge.apply", "link.apply", "merge.accept", "link.accept")
KEPT = ("analysis",)  # what analysis found stays: it comes from the transcripts, and mentions point at it


def _plan(db, to, spaces):
    """The events a rollback to `to` in namespaces `spaces` takes back, oldest first, and the ones it leaves."""
    to = int(to)
    top = head(db)
    if not 0 <= to <= top:
        raise ValueError(f"the graph has versions 0 to {top}")
    rows = db.rows("SELECT *, record::id(id) AS version FROM graph_event WHERE id > $v ORDER BY version", v=R("graph_event", to))
    sp = set(spaces) if spaces is not None else None
    mine = [ev for ev in rows if sp is None or set(ev.get("spaces") or []) & sp]
    return [ev for ev in mine if ev["op"] not in KEPT], [ev for ev in mine if ev["op"] in KEPT]


def rollback(db, to, spaces=None, editable=None, dry_run=True, by=None):
    """Take back every change made after version `to` in these namespaces (None: all), newest first, as one new event
    (so a rollback can be rolled back too). What analysis found stays. `dry_run` only says what would change.

    Merges are undone with their mentions, graph changes are marked undone, moved mentions go back; then every record
    is set to how it was at `to`. An entity made since that is mentioned now is kept (hide it instead)."""
    events, kept = _plan(db, to, spaces)
    if editable is not None:
        blocked = sorted({s for ev in events for s in ev.get("spaces") or []} - set(editable))
        if blocked:
            raise PermissionError("changes since then also touched namespaces you can't edit")
    target = {}
    for ev in events:
        for o in ev["ops"]:
            target.setdefault((o["t"], _key(o["t"], o["k"])), o.get("b"))
    now = _snap(db, (), list(target))
    out = {
        "to": int(to),
        "head": head(db),
        "undo": [{k: ev.get(k) for k in ("version", "op", "actor", "via", "at")} for ev in reversed(events)],
        "kept": len(kept),
        **_describe(db, now, target, None),
    }
    if dry_run or not events:
        return {**out, "done": False}
    with acting(actor=by, why=f"rolled back to version {int(to)}"), change(db, "graph.rollback", rollback_to=int(to)) as ch:
        ch.touch(keys=list(target))
        skipped = _take_back(db, events, by)
        skipped += _restore(db, target)
    return {**out, "done": True, "version": ch.version, "skipped": skipped}


def _take_back(db, events, by):
    """What has more to it than rows: merges (mentions), graph changes (their status) and moved mentions."""
    from . import entities, organize

    skipped = []
    merged = {ev["origin"]["merge"] for ev in events if ev["op"] in MERGES and (ev.get("origin") or {}).get("merge")}
    unmerged = {ev["origin"]["merge"] for ev in events if ev["op"] == "entity.unmerge" and (ev.get("origin") or {}).get("merge")}
    for ev in reversed(events):
        o = ev.get("origin") or {}
        try:
            if ev["op"] in APPLIED and o.get("graph_change"):
                ch = db.one("SELECT status FROM $r", r=R("graph_change", int(o["graph_change"])))
                if ch and ch["status"] == "applied":
                    organize.undo(db, o["graph_change"], by)
                    continue
            if ev["op"] in MERGES and o.get("merge") and o["merge"] not in unmerged:
                m = db.one("SELECT undone FROM $r", r=R("entity_merge", int(o["merge"])))
                if m and not m.get("undone"):
                    entities.undo_merge(db, o["merge"])
            elif ev["op"] == "entity.unmerge" and o.get("merge") and o["merge"] not in merged:
                m = db.one("SELECT keep, snapshots FROM $r", r=R("entity_merge", int(o["merge"])))
                others = [sn["entity"]["id"] for sn in (m or {}).get("snapshots") or []]
                if m and others:
                    entities.merge(db, m["keep"], others, by)
            elif ev["op"] in ("mention.move", "mention.remove") and o.get("mention"):
                _mention_back(db, o["mention"])
        except (ValueError, KeyError) as e:
            skipped.append({"version": ev["version"], "op": ev["op"], "why": str(e)[:200]})
    return skipped


def _mention_back(db, mn):
    """Point a moved mention back at the entity it was on (or say it again, when it was removed)."""
    from . import analyze, entities

    src = mn.get("from")
    if not src or not db.one("SELECT id FROM $r", r=R("entity", int(src))):
        raise ValueError("the entity the mention was on is gone")
    seg = R("segment", mn["segment"])
    if mn.get("to"):
        row = db.one(
            "SELECT record::id(id) AS id FROM mentions WHERE in = $s AND text = $t AND entity = $e LIMIT 1", s=seg, t=mn["text"], e=mn["to"]
        )
        if row:
            entities.move_mention(db, row["id"], target=int(src))
        return
    rec = db.one("SELECT recording, space, speaker FROM $r", r=seg)
    if not rec:
        raise ValueError("the line the mention was on is gone")
    db.q(
        "RELATE $a->mentions->$b CONTENT $d",
        a=seg,
        b=R("entity", int(src)),
        d=store.clean(
            {"recording": rec["recording"], "space": rec["space"], "entity": int(src), "speaker": rec.get("speaker"), "text": mn["text"]}
        ),
    )
    db.q("DELETE $r", r=R("entity_override", f"{mn['segment']}:{analyze.ent_key(mn['text'])}"))


def _alive(db, eid):
    return bool(db.one("SELECT id FROM $r", r=R("entity", int(eid))))


def _restore(db, target):
    """Set every record to its row in `target`: deletions first (an entity a name moves back to may need its key)."""
    skipped = []
    now = _snap(db, (), list(target))
    gone = [(t, k) for (t, k), row in target.items() if row is None and now.get((t, k)) is not None]
    back = [(t, k) for (t, k), row in target.items() if row is not None and now.get((t, k)) != row]
    order = {t: n for n, t in enumerate(("entity_link", "entity_distinct", "entity_alias", "entity"))}
    for t, k in sorted(gone, key=lambda x: order[x[0]]):
        if t == "entity" and db.rows("SELECT id FROM mentions WHERE entity = $e LIMIT 1", e=int(k)):
            row = now[(t, k)]
            into = (target.get(("entity_alias", f"{row['space']}:{row['key']}")) or {}).get("entity")
            stays = target[("entity", int(into))] is not None if ("entity", int(into or 0)) in target else bool(into and _alive(db, into))
            if into and into != int(k) and stays:
                from . import entities  # its name was another entity's other name then: it was merged into that one

                entities.merge(db, int(into), [int(k)])
            else:
                skipped.append({"entity": int(k), "why": "it is mentioned now: hide it instead"})
            continue
        if db.one("SELECT id FROM $r", r=R(t, k)):
            db.q("DELETE $r", r=R(t, k))
    for t, k in sorted(back, key=lambda x: -order[x[0]]):
        db.q("UPSERT $r CONTENT $d", r=R(t, k), d=target[(t, k)])
    return skipped


# ---------- checkpoints, replay and verify ----------
CHECKPOINT_EVERY = 1000  # events between automatic checkpoints
PART_ROWS = 2000  # records per stored part of a checkpoint


def _flat(state):
    return [[t, k, row] for t in TABLES for k, row in sorted(state[t].items(), key=lambda x: str(x[0]))]


def checkpoint(db, version=None):
    """Keep the whole graph as it was at `version` (default: now), so it can be replayed from there. Its version."""
    v = head(db) if version is None else int(version)
    state = state_at(db, v)
    rows = _flat(state)
    db.q("DELETE graph_checkpoint_part WHERE checkpoint = $v", v=v)
    parts = [rows[i : i + PART_ROWS] for i in range(0, len(rows), PART_ROWS)]
    for n, part in enumerate(parts):
        db.q("CREATE $r CONTENT $d", r=R("graph_checkpoint_part", f"{v}-{n}"), d={"checkpoint": v, "n": n, "rows": part})
    db.q(
        "UPSERT $r CONTENT $d",
        r=R("graph_checkpoint", v),
        d={"version": v, "at": store.now(), "parts": len(parts), "counts": {t: len(state[t]) for t in TABLES}},
    )
    return v


def checkpoints(db):
    return db.rows("SELECT version, at, counts FROM graph_checkpoint ORDER BY version DESC")


def _load(db, v):
    s = {t: {} for t in TABLES}
    for part in db.rows("SELECT n, rows FROM graph_checkpoint_part WHERE checkpoint = $v ORDER BY n", v=int(v)):
        for t, k, row in part["rows"]:
            s[t][_key(t, k)] = row
    return s


def replay(db, upto=None):
    """The graph rebuilt from the newest checkpoint at or before `upto` (default: now) and the events after it, without
    reading today's rows. (state, the checkpoint it started from)."""
    top = head(db)
    upto = top if upto is None else int(upto)
    base = db.one("SELECT version FROM graph_checkpoint WHERE version <= $u ORDER BY version DESC LIMIT 1", u=upto)
    if not base:
        ensure_checkpoint(db)
        base = db.one("SELECT version FROM graph_checkpoint WHERE version <= $u ORDER BY version DESC LIMIT 1", u=upto)
    s = _load(db, base["version"])
    for ev in _events_after(db, base["version"], upto):
        for o in ev["ops"]:
            k = _key(o["t"], o["k"])
            if o.get("a") is None:
                s[o["t"]].pop(k, None)
            else:
                s[o["t"]][k] = o["a"]
    return s, base["version"]


def verify(db, fix=False):
    """Replay the history and compare it with today's rows. Anything that differs was written without being recorded;
    `fix` records it as one `graph.drift` event, so the history matches again. {from, head, same, differences}."""
    replayed, base = replay(db)
    live_rows = live(db)
    first = {(t, k): row for t in TABLES for k, row in replayed[t].items()}
    last = {(t, k): row for t in TABLES for k, row in live_rows[t].items()}
    keys = set(first) | set(last)
    first = {k: first.get(k) for k in keys}
    last = {k: last.get(k) for k in keys}
    differ = [k for k in keys if first[k] != last[k]]
    out = {"from": base, "head": head(db), "same": not differ, "differences": len(differ), **_describe(db, first, last, None)}
    if differ and fix:
        ents = {k: r for (t, k), r in {**first, **last}.items() if t == "entity" and r}

        def space_of(e):
            return (ents.get(int(e)) or {}).get("space") if e is not None else None

        ops = [
            store.clean({"t": t, "k": k, "b": first[(t, k)], "a": last[(t, k)]})
            | {"s": sorted(_row_spaces(t, first[(t, k)], space_of) | _row_spaces(t, last[(t, k)], space_of))}
            for t, k in sorted(differ, key=lambda x: (TABLES.index(x[0]), str(x[1])))
        ]
        out["version"] = record(db, "graph.drift", ops, why="written without being recorded; found by verify", actor="system", via="system")
    return out


def ensure_checkpoint(db):
    """A first checkpoint (the graph before its first recorded change) once there is none, and another every
    CHECKPOINT_EVERY events. Cheap when nothing is due."""
    last = db.one("SELECT version FROM graph_checkpoint ORDER BY version DESC LIMIT 1")
    if not last:
        return checkpoint(db, 0)
    if head(db) - last["version"] >= CHECKPOINT_EVERY:
        return checkpoint(db)
    return None
