"""Collections: the folders of a namespace (docs/api.md#collections-of-a-namespace).

Every recording lives in exactly one collection of its namespace, its home; collections nest. A namespace's default
collection ("General" unless renamed) takes the recordings nobody placed: imports, uploads, scans and watched folders
that don't name one, and recordings moved in from another namespace. Saved collections (recsets.py) are something
else: lists of recordings from anywhere, personal or shared.

Names are unique among a collection's siblings, ignoring case. A collection can only be deleted when it's empty and
isn't its namespace's default, so deleting one never takes a recording with it.

People can be given a role on a collection (viewer, editor or admin), which holds for the collections inside it too,
on top of their namespace role: someone without a role in the namespace sees just those collections. An admin of a
collection acts as an owner of its recordings and gives roles on it (docs/access.md#collection-roles).
"""

from __future__ import annotations

from . import store

R = store.R
ROLES = {"viewer": 1, "editor": 2, "admin": 3}  # on a collection; an admin acts as an owner of its recordings
MAX_DEPTH = 8
MAX_COLLECTIONS = 5000  # per namespace
NAME_MAX = 120
DESCRIPTION_MAX = 2000
FIELDS = "record::id(id) AS id, space, parent, name, description, created_at, created_by, updated_at"
UNSET = object()


def _name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("A collection needs a name.")
    if len(name) > NAME_MAX:
        raise ValueError(f"A collection's name can have up to {NAME_MAX} characters.")
    return name


def _description(text):
    text = str(text or "").strip()
    if len(text) > DESCRIPTION_MAX:
        raise ValueError(f"A description can have up to {DESCRIPTION_MAX} characters.")
    return text or None


def get(db, cid):
    """The collection, or KeyError."""
    c = db.one(f"SELECT {FIELDS} FROM $r", r=R("collection", int(cid)))
    if not c:
        raise KeyError(cid)
    return c


def of_space(db, sid):
    """Every collection of a namespace."""
    return db.rows(f"SELECT {FIELDS} FROM collection WHERE space = $s", s=sid)


def _children(rows):
    kids = {}
    for c in rows:
        kids.setdefault(c.get("parent"), []).append(c)
    for v in kids.values():
        v.sort(key=lambda c: (c["name"].casefold(), c["id"]))
    return kids


def subtree(db, sid, cid):
    """The ids of a collection and every collection inside it."""
    kids = _children(of_space(db, sid))
    out, todo = set(), [int(cid)]
    while todo:
        c = todo.pop()
        if c not in out:
            out.add(c)
            todo += [k["id"] for k in kids.get(c, [])]
    return out


def path(db, cid):
    """The collections from the namespace's top down to this one: [{id, name}]."""
    out, seen, c = [], set(), get(db, cid)
    while c and c["id"] not in seen:
        seen.add(c["id"])
        out.append({"id": c["id"], "name": c["name"]})
        c = db.one(f"SELECT {FIELDS} FROM $r", r=R("collection", c["parent"])) if c.get("parent") is not None else None
    return out[::-1]


def tree(db, sid, counts=None, only=None):
    """The namespace's collections, depth first and by name: each with its `depth`, `path` (names from the top), how
    many recordings it holds (`recordings`) and holds with everything inside it (`total`), its sub-collections'
    count (`children`) and whether it's the namespace's `default`. `counts` (collection id → recordings) defaults to
    every recording. `only` keeps to these collections (what someone sees): one whose parent isn't among them is at the
    top, and paths and depths start there."""
    rows = of_space(db, sid)
    if only is not None:
        rows = [c for c in rows if c["id"] in only]
    if counts is None:
        counts = {
            r["collection"]: r["n"]
            for r in db.rows("SELECT collection, count() AS n FROM recording WHERE space = $s GROUP BY collection", s=sid)
            if r.get("collection") is not None
        }
    default = (db.one("SELECT default_collection FROM $r", r=R("space", sid)) or {}).get("default_collection")
    kids, out = _children(rows), []

    def walk(c, depth, names):
        node = {**c, "depth": depth, "path": [*names, c["name"]], "recordings": counts.get(c["id"], 0), "default": c["id"] == default}
        out.append(node)
        below = kids.get(c["id"], [])
        node["children"] = len(below)
        node["total"] = node["recordings"] + sum(walk(k, depth + 1, node["path"]) for k in below)
        return node["total"]

    known = {c["id"] for c in rows}
    tops = [c for c in rows if c.get("parent") is None or c["parent"] not in known]
    for top in sorted(tops, key=lambda c: (c["name"].casefold(), c["id"])):
        walk(top, 0, [])
    return out


def _check_parent(db, sid, parent, moving=None):
    """The parent a collection may go under (None: the top): one of the namespace's, not the collection itself or
    inside it, and not too deep."""
    if parent is None:
        return None
    p = db.one(f"SELECT {FIELDS} FROM $r", r=R("collection", int(parent)))
    if not p or p["space"] != sid:
        raise KeyError(parent)
    if moving is not None and int(parent) in subtree(db, sid, moving):
        raise ValueError("A collection can't go inside itself.")
    depth = len(path(db, p["id"]))
    below = 0
    if moving is not None:
        rows, todo, level = _children(of_space(db, sid)), [(int(moving), 0)], 0
        while todo:
            c, d = todo.pop()
            level = max(level, d)
            todo += [(k["id"], d + 1) for k in rows.get(c, [])]
        below = level
    if depth + 1 + below > MAX_DEPTH:
        raise ValueError(f"Collections go at most {MAX_DEPTH} deep.")
    return int(parent)


def _taken(db, sid, parent, name, cid=None):
    row = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=store.collection_key(sid, parent, name))
    return bool(row) and row["id"] != cid


def create(db, sid, name, parent=None, description=None, by=None):
    """A new collection in namespace `sid` (inside `parent`, or at the top); its id. ValueError for a blank name, one
    its siblings have, too many or too deep; KeyError for a parent from elsewhere."""
    name = _name(name)
    parent = _check_parent(db, sid, parent)
    if len(db.values("SELECT VALUE id FROM collection WHERE space = $s", s=sid)) >= MAX_COLLECTIONS:
        raise ValueError(f"A namespace can have up to {MAX_COLLECTIONS} collections.")
    if _taken(db, sid, parent, name):
        raise ValueError(f"There's already a collection called “{name}” there.")
    cid = db.next_id("collection")
    t = store.now()
    doc = {
        "space": sid,
        "parent": parent,
        "name": name,
        "key": store.collection_key(sid, parent, name),
        "description": _description(description),
        "created_at": t,
        "created_by": by,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("collection", cid), d=store.clean(doc))
    return cid


def update(db, cid, name=None, description=UNSET, parent=UNSET):
    """Rename it, describe it, or move it under another collection of its namespace (`parent` None: to the top).
    Returns what changed, {field: [before, after]}. ValueError and KeyError as for create, and for a move into itself."""
    c = get(db, cid)
    sid, changed = c["space"], {}
    new_name = _name(name) if name is not None else c["name"]
    new_parent = _check_parent(db, sid, parent, moving=c["id"]) if parent is not UNSET else c.get("parent")
    if (new_name != c["name"] or new_parent != c.get("parent")) and _taken(db, sid, new_parent, new_name, c["id"]):
        raise ValueError(f"There's already a collection called “{new_name}” there.")
    sets, p = ["updated_at = $t"], {"t": store.now()}
    if new_name != c["name"]:
        changed["name"] = [c["name"], new_name]
    if new_parent != c.get("parent"):
        changed["parent"] = [c.get("parent"), new_parent]
        sets.append("parent = $parent")
        p["parent"] = new_parent
    if changed.keys() & {"name", "parent"}:
        sets += ["name = $n", "key = $k"]
        p.update(n=new_name, k=store.collection_key(sid, new_parent, new_name))
    if description is not UNSET:
        d = _description(description)
        if d != c.get("description"):
            changed["description"] = [c.get("description"), d]
            sets.append("description = $d")
            p["d"] = d
    if changed:
        db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("collection", c["id"]), **p)
    return changed


def is_default(db, cid):
    c = get(db, cid)
    return (db.one("SELECT default_collection FROM $r", r=R("space", c["space"])) or {}).get("default_collection") == c["id"]


def make_default(db, cid):
    """Make it its namespace's default collection."""
    c = get(db, cid)
    db.q("UPDATE $r SET default_collection = $c", r=R("space", c["space"]), c=c["id"])


def delete(db, cid):
    """Delete an empty collection, and the roles given on it. ValueError when it's the default, holds collections or
    holds recordings."""
    c = get(db, cid)
    if is_default(db, cid):
        raise ValueError("This is the namespace's default collection: make another one the default first.")
    if db.values("SELECT VALUE id FROM collection WHERE parent = $c LIMIT 1", c=c["id"]):
        raise ValueError("It holds other collections: move or delete them first.")
    if db.values("SELECT VALUE id FROM recording WHERE collection = $c LIMIT 1", c=c["id"]):
        raise ValueError("It holds recordings: move them to another collection first.")
    db.q("DELETE collection_role WHERE collection = $c", c=c["id"])
    db.q("DELETE $r", r=R("collection", c["id"]))
    return c


def place(db, rids, cid):
    """Move recordings into a collection of their namespace; how many moved. ValueError for one in another
    namespace, KeyError for one that doesn't exist."""
    c = get(db, cid)
    rows = db.rows(
        "SELECT record::id(id) AS id, space, collection FROM recording WHERE id IN $ids", ids=[R("recording", int(r)) for r in rids]
    )
    if len(rows) != len(set(int(r) for r in rids)):
        raise KeyError("recording")
    if any(r["space"] != c["space"] for r in rows):
        raise ValueError("A recording can only go into a collection of its own namespace; move it to that namespace first.")
    moving = [r["id"] for r in rows if r.get("collection") != c["id"]]
    if moving:
        db.q("UPDATE recording SET collection = $c WHERE id IN $ids", c=c["id"], ids=[R("recording", r) for r in moving])
    return moving


def migrate_homes(db):
    """Give every namespace its default collection and every recording a home: those without one go into their
    namespace's default. Runs once per database (see store.migrate) and is safe to repeat."""
    for sid in db.values("SELECT VALUE record::id(id) FROM space"):
        cid = store.default_collection(db, sid)
        db.q("UPDATE recording SET collection = $c WHERE space = $s AND collection = NONE", c=cid, s=sid)


# ---------- roles on collections ----------
def roles_of(db, account):
    """{space: {collection id: role}}: the collections someone was given a role on, and the ones inside them; where
    two of their roles reach a collection, the higher one."""
    grants = db.rows("SELECT collection, space, role FROM collection_role WHERE account = $a", a=account) if account else []
    out = {}
    for sid in sorted({g["space"] for g in grants}):
        kids, seen = _children(of_space(db, sid)), out.setdefault(sid, {})
        for g in sorted((g for g in grants if g["space"] == sid), key=lambda g: -ROLES.get(g["role"], 0)):
            todo = [g["collection"]]
            while todo:
                c = todo.pop()
                if ROLES.get(seen.get(c), 0) >= ROLES.get(g["role"], 0):
                    continue  # it and everything inside it have this role or a higher one already
                seen[c] = g["role"]
                todo += [k["id"] for k in kids.get(c, [])]
    return {sid: m for sid, m in out.items() if m}


def recordings_in(db, collections):
    """The recordings these collections hold (ids)."""
    cols = sorted({int(c) for c in collections})
    return set(db.values("SELECT VALUE record::id(id) FROM recording WHERE collection IN $c", c=cols)) if cols else set()


def _people(db, accounts):
    ids = sorted(set(accounts))
    rows = (
        db.rows("SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids", ids=[R("account", a) for a in ids]) if ids else []
    )
    return {r["id"]: r for r in rows}


def members(db, cid):
    """Who was given a role on this collection (`role`, by whom and when), then who has one through a collection it's
    inside (`inherited_from`): [{account, email, name, role, by, at, inherited_from}]."""
    steps = path(db, cid)
    above = {s["id"]: s for s in steps[:-1]}
    rows = db.rows(
        "SELECT collection, account, role, by, at FROM collection_role WHERE collection IN $c",
        c=[s["id"] for s in steps],
    )
    people = _people(db, [r["account"] for r in rows])
    own = [r for r in rows if r["collection"] == int(cid)]
    out = [{**r, "inherited_from": None} for r in sorted(own, key=lambda r: (-ROLES.get(r["role"], 0), r.get("at") or ""))]
    for r in sorted((r for r in rows if r["collection"] in above), key=lambda r: -ROLES.get(r["role"], 0)):
        out.append({**r, "inherited_from": above[r["collection"]]})
    return [
        store.clean(
            {
                "account": r["account"],
                "email": people.get(r["account"], {}).get("email"),
                "name": people.get(r["account"], {}).get("name"),
                "role": r["role"],
                "by": r.get("by"),
                "at": r.get("at"),
                "inherited_from": r["inherited_from"],
            }
        )
        for r in out
        if r["account"] in people
    ]


def give(db, cid, account, role, by=None):
    """Give someone a role on a collection, change it, or (role None) take it away; the role they had before."""
    c = get(db, cid)
    rid = R("collection_role", f"{c['id']}-{int(account)}")
    before = (db.one("SELECT role FROM $r", r=rid) or {}).get("role")
    if role is None:
        db.q("DELETE $r", r=rid)
    elif role in ROLES:
        doc = {"collection": c["id"], "space": c["space"], "account": int(account), "role": role, "by": by, "at": store.now()}
        db.q("UPSERT $r CONTENT $d", r=rid, d=store.clean(doc))
    else:
        raise ValueError("A collection role is viewer, editor or admin.")
    return before
