"""Saved views of the Library: its tab, filters and sort under a name, with the namespace it shows (or every one).

A view is its maker's. Shared with its namespace, everyone with a role there sees it, and the namespace's owners may
delete it; only its maker changes it.
"""

from __future__ import annotations

from . import store

R = store.R
MAX_VIEWS = 100  # per person
FIELDS = "record::id(id) AS id, account, name, space, shared ?? false AS shared, state, created_at, updated_at"


def _name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("a view needs a name")
    return name


def _taken(db, account, name, vid=None):
    return any(
        v["name"].casefold() == name.casefold() and v["id"] != vid
        for v in db.rows("SELECT record::id(id) AS id, name FROM saved_view WHERE account = $a", a=account)
    )


def get(db, vid):
    """The view, or KeyError."""
    v = db.one(f"SELECT {FIELDS} FROM $r", r=R("saved_view", int(vid)))
    if not v:
        raise KeyError(vid)
    return v


def visible(db, account, spaces):
    """This person's views (of every namespace, or of one they can still read), then the views shared with the
    namespaces they can read; the latest changed first in each."""
    rows = db.rows(
        f"SELECT {FIELDS} FROM saved_view WHERE account = $a OR (shared = true AND space IN $s) ORDER BY updated_at DESC",
        a=account,
        s=sorted(spaces),
    )
    rows = [v for v in rows if v.get("space") is None or v["space"] in spaces]
    return sorted(rows, key=lambda v: v["account"] != account)


def create(db, account, name, space=None, state=None, shared=False):
    """A new view; its id. ValueError for a blank name, one this person already uses, or too many views."""
    name = _name(name)
    if len(db.values("SELECT VALUE id FROM saved_view WHERE account = $a", a=account)) >= MAX_VIEWS:
        raise ValueError(f"You have {MAX_VIEWS} views already; delete one first.")
    if _taken(db, account, name):
        raise ValueError(f"You already have a view called “{name}”.")
    vid = db.next_id("saved_view")
    t = store.now()
    view = {
        "account": account,
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
        name = _name(name)
        if _taken(db, account, name, int(vid)):
            raise ValueError(f"You already have a view called “{name}”.")
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
