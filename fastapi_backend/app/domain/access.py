"""Who may see a recording, and how much of it: public, restricted or private (after Aviary's roles and permissions).

A recording's access is its own setting, else its namespace's default (from the namespace's metadata profile), else
private. A public recording opens some of its parts to everyone (media, transcript, index: all of them unless set
otherwise) and can be featured on the home page. The settings live in their own fields on the recording (`access`,
`access_parts`, `featured`) so lists can filter by them; the metadata API reads and writes them as the fields
access, open and featured, with history. See docs/access.md.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from . import auth, store

R = store.R
LEVELS = ("public", "restricted", "private")
PARTS = ("media", "transcript", "index")
# The IIIF-only levels from before access was one setting for everything -> (level, open parts). Converted once, on
# the first start (migrate_legacy), and again whenever an old metadata history entry is reverted.
LEGACY = {
    "public": ("public", list(PARTS)),
    "transcript": ("public", ["transcript", "index"]),
    "signed-in": ("restricted", None),
    "private": ("private", None),
}


def parts(v):
    """A canonical list of parts (in PARTS order); raises ValueError for anything else."""
    v = [v] if isinstance(v, str) else v
    if not isinstance(v, list) or any(p not in PARTS for p in v):
        raise ValueError(f"open lists parts among {', '.join(PARTS)}")
    return [p for p in PARTS if p in v]


def _profile(raw):
    p = _json(raw)
    level, legacy_parts = LEGACY.get(p.get("default_access")) or ("private", None)
    if p.get("default_access") in LEVELS:
        level, legacy_parts = p["default_access"], None
    return level, p.get("default_open") if isinstance(p.get("default_open"), list) else legacy_parts


def namespace_defaults(db, sids=None):
    """{space id: (default level, default open parts)} from the namespaces' metadata profiles."""
    q = "SELECT record::id(id) AS id, profile_json FROM space"
    rows = db.rows(q + " WHERE id IN $ids", ids=[R("space", s) for s in sids]) if sids is not None else db.rows(q)
    out = {}
    for r in rows:
        level, open_ = _profile(r.get("profile_json"))
        out[r["id"]] = (level, list(PARTS) if open_ is None else [p for p in PARTS if p in open_])
    return out


def effective(rec, defaults):
    """A recording row's access ({space, access, access_parts, featured} are enough), given namespace_defaults()."""
    level, open_ = defaults.get(rec.get("space"), ("private", list(PARTS)))
    own = rec.get("access") if rec.get("access") in LEVELS else None
    return {
        "access": own or level,
        "open": [p for p in PARTS if p in rec["access_parts"]] if isinstance(rec.get("access_parts"), list) else list(open_),
        "featured": bool(rec.get("featured")),
        "inherited": own is None,
    }


def of(db, rid):
    rec = db.one("SELECT space, access, access_parts, featured FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    return effective(rec, namespace_defaults(db, [rec["space"]]))


def many(db, rows):
    """{recording id: access} for rows that carry id, space, access, access_parts and featured."""
    defaults = namespace_defaults(db, sorted({r["space"] for r in rows})) if rows else {}
    return {r["id"]: effective(r, defaults) for r in rows}


def published(a):
    """IIIF publishes public recordings; restricted and private ones need permission."""
    return a["access"] == "public"


def is_open(a, part):
    """Whether anyone may use this part (media, transcript or index) without permission."""
    return a["access"] == "public" and part in a["open"]


class Who(NamedTuple):
    """Who is asking, for the pages visitors see and IIIF: the namespaces they have a role in (admins have every role),
    the recordings they were given permission on, and whether they're signed in."""

    member_of: frozenset
    granted: frozenset
    signed_in: bool

    def permitted(self, rid, space):
        """Permission on a recording: a role in its namespace, or permission given on the recording itself."""
        return space in self.member_of or rid in self.granted


def granted(db, account):
    """The recordings someone was given permission on."""
    return frozenset(db.values("SELECT VALUE recording FROM permission WHERE account = $a", a=account)) if account else frozenset()


def has_permission(db, rid, account):
    return bool(account and db.one("SELECT id FROM $p", p=R("permission", f"{rid}-{account}")))


def permitted(db, roles, account, rid, space):
    """Permission on a recording (docs/access.md): a role in its namespace, or permission given on the recording."""
    return auth.allows(roles, space) or has_permission(db, rid, account)


def people(db, rid):
    """The people given permission on a recording, newest first."""
    rows = db.rows("SELECT account, by, at FROM permission WHERE recording = $r", r=rid)
    accounts = (
        {
            a["id"]: a
            for a in db.rows(
                "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids", ids=[R("account", r["account"]) for r in rows]
            )
        }
        if rows
        else {}
    )
    out = [
        {
            "account": r["account"],
            "email": accounts[r["account"]]["email"],
            "name": accounts[r["account"]].get("name"),
            "by": r.get("by"),
            "at": r.get("at"),
        }
        for r in rows
        if r["account"] in accounts
    ]
    return sorted(out, key=lambda x: x.get("at") or "", reverse=True)


def give(db, rid, account, by=None):
    """Give someone permission on a recording (again: keeps the first grant)."""
    db.q(
        "UPSERT $p SET recording = $r, account = $a, by = by ?? $by, at = at ?? $at",
        p=R("permission", f"{rid}-{account}"),
        r=rid,
        a=account,
        by=by,
        at=store.now(),
    )


def take(db, rid, account):
    """Take someone's permission on a recording away; False when they had none."""
    had = has_permission(db, rid, account)
    db.q("DELETE $p", p=R("permission", f"{rid}-{account}"))
    return had


def view(a, permitted, signed_in):
    """What someone sees of a recording, after Aviary's matrix (docs/access.md).

    full: they have permission (a role in its namespace, or permission on the recording), so all of it. public: a public recording's page, description
    and open parts. locked: a restricted recording, for someone signed in: listed with a lock, its page closed.
    None: hidden (restricted ones from visitors who aren't signed in, private ones from everyone without permission).
    """
    if permitted:
        return "full"
    if a["access"] == "public":
        return "public"
    if a["access"] == "restricted" and signed_in:
        return "locked"
    return None


def usable(a, seen, part):
    """Whether someone who sees the recording this way (view()) may use a part: media, transcript or index."""
    return seen == "full" or (seen == "public" and part in a["open"])


def activity(db, rid, kind):
    """A IIIF Change Discovery activity (Create, Update or Delete) for this recording."""
    db.q("CREATE $r CONTENT $d", r=R("iiif_activity", db.next_id("iiif_activity")), d={"type": kind, "recording": rid, "at": store.now()})


def announce(db, rid, before, after):
    """Tell harvesters what changed: published (public) now and not before is a Create, the reverse a Delete."""
    was, now = before == "public", after == "public"
    if now or was:
        activity(db, rid, "Update" if was and now else "Create" if now else "Delete")


def _json(raw):
    """A stored JSON object, or {} when it isn't one (a bad value must not stop the server from starting)."""
    try:
        v = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def migrate_legacy(db):
    """Convert the IIIF-only access levels (public, transcript, signed-in, private) into the access setting.

    Runs once per database (see store.migrate) and is safe to repeat. Recordings that were signed-in were listed in
    IIIF and restricted ones are not, so each of those gets a Delete activity for harvesters.
    """
    for r in db.rows("SELECT record::id(id) AS id, meta_json FROM recording WHERE meta_json != NONE"):
        meta = _json(r["meta_json"])
        if "access" not in meta:
            continue
        old = meta.pop("access")
        level, open_ = LEGACY.get(old) or ((old, None) if old in LEVELS else ("private", None))
        sets = ["meta_json = $m", "access = $a", "access_parts = $p" if open_ is not None else "access_parts = NONE"]
        db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("recording", r["id"]), m=json.dumps(meta), a=level, p=open_)
        if old == "signed-in":
            activity(db, r["id"], "Delete")
    for s in db.rows("SELECT record::id(id) AS id, profile_json FROM space WHERE profile_json != NONE"):
        p = _json(s["profile_json"])
        old = p.get("default_access")
        if old not in ("transcript", "signed-in"):
            continue
        level, open_ = LEGACY[old]
        p["default_access"] = level
        if open_ is not None:
            p["default_open"] = open_
        db.q("UPDATE $r SET profile_json = $p", r=R("space", s["id"]), p=json.dumps(p))
        if old == "signed-in":
            for rid in db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s AND access = NONE", s=s["id"]):
                activity(db, rid, "Delete")
