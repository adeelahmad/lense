"""Saved views: of the Library (its tab, filters and sort) or of a search (its words and filters), under a name, with
the namespace they show (or every one). Kind ``library`` is a Library view, ``search`` a saved search.

A view is its maker's. Shared with its namespace, everyone with a role there sees it, and the namespace's owners may
delete it; only its maker changes it.
"""

from __future__ import annotations

from . import store

R = store.R
MAX_VIEWS = 100  # per person, of each kind
KINDS = {"library": "view", "search": "saved search"}
FIELDS = "record::id(id) AS id, account, kind ?? 'library' AS kind, name, space, shared ?? false AS shared, state, created_at, updated_at"


def _name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("a view needs a name")
    return name


def _mine(db, account, kind):
    return db.rows("SELECT record::id(id) AS id, name FROM saved_view WHERE account = $a AND (kind ?? 'library') = $k", a=account, k=kind)


def _taken(db, account, name, kind, vid=None):
    return any(v["name"].casefold() == name.casefold() and v["id"] != vid for v in _mine(db, account, kind))


def get(db, vid, kind=None):
    """The view (of this kind, when given), or KeyError."""
    v = db.one(f"SELECT {FIELDS} FROM $r", r=R("saved_view", int(vid)))
    if not v or (kind and v["kind"] != kind):
        raise KeyError(vid)
    return v


def visible(db, account, spaces, kind="library"):
    """This person's views of this kind (of every namespace, or of one they can still read), then the ones shared with
    the namespaces they can read; the latest changed first in each."""
    rows = db.rows(
        f"SELECT {FIELDS} FROM saved_view WHERE (kind ?? 'library') = $k AND (account = $a OR (shared = true AND space IN $s)) "
        "ORDER BY updated_at DESC",
        a=account,
        s=sorted(spaces),
        k=kind,
    )
    rows = [v for v in rows if v.get("space") is None or v["space"] in spaces]
    return sorted(rows, key=lambda v: v["account"] != account)


def create(db, account, name, space=None, state=None, shared=False, kind="library"):
    """A new view; its id. ValueError for a blank name, one this person already uses for this kind, or too many."""
    name, what = _name(name), KINDS[kind]
    mine = _mine(db, account, kind)
    if len(mine) >= MAX_VIEWS:
        raise ValueError(f"You have {MAX_VIEWS} {what}s already; delete one first.")
    if any(v["name"].casefold() == name.casefold() for v in mine):
        raise ValueError(f"You already have a {what} called “{name}”.")
    vid = db.next_id("saved_view")
    t = store.now()
    view = {
        "account": account,
        "kind": kind,
        "name": name,
        "space": space,
        "shared": bool(shared),
        "state": state or {},
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("saved_view", vid), d=store.clean(view))
    return vid


def update(db, vid, account, name=None, shared=None, state=None):
    """Rename it, share or unshare it, or replace what it shows. ValueError for a name its maker already uses."""
    sets, p = ["updated_at = $t"], {"t": store.now()}
    if name is not None:
        name, kind = _name(name), get(db, vid)["kind"]
        if _taken(db, account, name, kind, int(vid)):
            raise ValueError(f"You already have a {KINDS[kind]} called “{name}”.")
        sets.append("name = $n")
        p["n"] = name
    if shared is not None:
        sets.append("shared = $sh")
        p["sh"] = bool(shared)
    if state is not None:
        sets.append("state = $st")
        p["st"] = state
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("saved_view", int(vid)), **p)


def delete(db, vid):
    db.q("DELETE $r", r=R("saved_view", int(vid)))
