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

from . import auth, hierarchy, ipgroups, store

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
    the recordings they were given permission on, whether they're signed in, and what their address opens (IP
    groups)."""

    member_of: frozenset
    granted: frozenset
    signed_in: bool
    network: ipgroups.Network = ipgroups.NOWHERE

    def permitted(self, rid, space):
        """Permission on a recording: a role in its namespace, permission given on the recording itself, or an IP group
        the visitor's address is in."""
        return space in self.member_of or rid in self.granted or self.network.opens(rid, space)

    @property
    def spaces(self):
        """The namespaces they see all of: a role there, or an IP group that opens everything in it."""
        return self.member_of | frozenset(self.network.spaces)

    @property
    def recordings(self):
        """The recordings they see all of beyond those namespaces: given to them, or opened to their address."""
        return self.granted | frozenset(self.network.recordings)


def granted(db, account):
    """The recordings someone was given permission on, and those of the collections they were given a role on (and of
    the collections inside them)."""
    if not account:
        return frozenset()
    given = set(db.values("SELECT VALUE recording FROM permission WHERE account = $a", a=account))
    return frozenset(given | hierarchy.recordings_in(db, [c for cols in hierarchy.roles_of(db, account).values() for c in cols]))


def in_collection(db, rid, account):
    """Whether someone was given a role on the recording's collection, or on a collection it's inside."""
    if not account:
        return False
    rec = db.one("SELECT space, collection FROM $r", r=R("recording", int(rid)))
    if not rec or rec.get("collection") is None:
        return False
    return bool(hierarchy.roles_of(db, account).get(rec["space"], {}).get(rec["collection"]))


def has_permission(db, rid, account):
    return bool(account and db.one("SELECT id FROM $p", p=R("permission", f"{rid}-{account}")))


def permitted(db, roles, account, rid, space, network=ipgroups.NOWHERE):
    """Permission on a recording (docs/access.md): a role in its namespace or on its collection, permission given on
    the recording, or an IP group the visitor's address is in (network: ipgroups.of())."""
    return auth.allows(roles, space) or network.opens(rid, space) or has_permission(db, rid, account) or in_collection(db, rid, account)


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


# ---------- asking for access ----------
REQUEST_FIELDS = "recording, account, message, at, status, decided_by, decided_at"


def _request_id(rid, account):
    return R("access_request", f"{rid}-{account}")


def ask(db, rid, account, message=None):
    """Someone without permission asks for it (their latest request is the one that counts)."""
    db.q(
        "UPSERT $r CONTENT $d",
        r=_request_id(rid, account),
        d=store.clean({"recording": rid, "account": account, "message": message or None, "at": store.now(), "status": "pending"}),
    )
    return request_of(db, rid, account)


def request_of(db, rid, account):
    """Someone's latest request for a recording, or None."""
    return db.one(f"SELECT {REQUEST_FIELDS} FROM $r", r=_request_id(rid, account)) if account else None


def requests(db, rids=None, spaces=None, status=None):
    """Requests with who asked and for what, newest first: for some recordings, or for the recordings of some
    namespaces; only those with this status when given."""
    cond, p = [], {}
    if rids is not None:
        cond.append("recording IN $rids")
        p["rids"] = sorted(rids)
    if status:
        cond.append("status = $st")
        p["st"] = status
    rows = db.rows(f"SELECT {REQUEST_FIELDS} FROM access_request" + (f" WHERE {' AND '.join(cond)}" if cond else ""), **p)
    if not rows:
        return []
    recs = {
        r["id"]: r
        for r in db.rows(
            "SELECT record::id(id) AS id, title, space FROM recording WHERE id IN $ids",
            ids=[R("recording", i) for i in {x["recording"] for x in rows}],
        )
    }
    accounts = {
        a["id"]: a
        for a in db.rows(
            "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids",
            ids=[R("account", i) for i in {x["account"] for x in rows}],
        )
    }
    names = store.space_names(db)
    out = []
    for x in rows:
        rec, who = recs.get(x["recording"]), accounts.get(x["account"])
        if not rec or not who or (spaces is not None and rec["space"] not in spaces):
            continue
        out.append({**x, "title": rec.get("title"), "namespace": names.get(rec["space"]), "email": who["email"], "name": who.get("name")})
    return sorted(out, key=lambda x: x.get("at") or "", reverse=True)


def decide(db, rid, account, approve, by=None):
    """Answer a pending request: approving it gives permission. False when there is no pending request."""
    req = request_of(db, rid, account)
    if not req or req.get("status") != "pending":
        return False
    if approve:
        give(db, rid, account, by)
    db.q(
        "UPDATE $r SET status = $s, decided_by = $by, decided_at = $at",
        r=_request_id(rid, account),
        s="approved" if approve else "declined",
        by=by,
        at=store.now(),
    )
    return True


def owners(db, space):
    """The email addresses of a namespace's owners (members with the owner role; admins when it has none)."""
    ids = db.values("SELECT VALUE account FROM membership WHERE space = $s AND role = 'owner'", s=space)
    cond = "id IN $ids" if ids else "admin = true"
    return db.values(f"SELECT VALUE email FROM account WHERE {cond} AND disabled != true", ids=[R("account", i) for i in ids])


def view(a, permitted, signed_in):
    """What someone sees of a recording, after Aviary's matrix (docs/access.md).

    full: they have permission (a role in its namespace, permission on the recording, an IP group), so all of it.
    public: a public recording's page, description and open parts. locked: a restricted recording, for someone signed
    in: listed with a lock, its page closed. None: hidden (restricted ones from visitors who aren't signed in, private
    ones from everyone without permission).
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
