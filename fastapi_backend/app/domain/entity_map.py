"""Mapping what the extractors find onto a fixed list of entities (entity_setup.py, mode `fixed`).

People with editor access define a namespace's entities (each with a name, a type, other ways it's said and a
description), for the whole namespace or for one collection and the collections inside it. When a recording is
analysed, each name found goes to:
- the defined entity it names (by its name or one of the other ways it's said; the names are looked for in the
  transcript too, so "acme" is found as well as "Acme");
- else "Unlabeled", when it belongs here but no defined entity fits: a type the setup keeps (with every type kept,
  any type but dates and numbers);
- else "Unknown".
Unknown and Unlabeled are always there in a namespace that uses the fixed mode. People's corrections (a mention moved to
another entity) still win. Nothing is created from what's found.
"""

from __future__ import annotations

from . import analyze, entity_setup, store

R = store.R
UNKNOWN, UNLABELED = "unknown", "unlabeled"
BUILTIN_NAMES = {UNKNOWN: "Unknown", UNLABELED: "Unlabeled"}
BUILTIN_HELP = {
    UNKNOWN: "Names found here that don't belong to this namespace or collection.",
    UNLABELED: "Names that belong here but aren't one of the defined entities yet.",
}
QUIET = ("DATE", "NUMBER")


def _bkey(kind):
    return f"§{kind}"  # never a name's key: ent_key keeps no §


def builtins(db, sid):
    """The namespace's Unknown and Unlabeled entities, made when missing: {kind: id}."""
    out = {}
    for kind, name in BUILTIN_NAMES.items():
        key = _bkey(kind)
        row = db.one("SELECT record::id(id) AS id FROM entity WHERE ekey = $k", k=f"{sid}:{key}")
        if row:
            out[kind] = row["id"]
            continue
        eid = db.next_id("entity")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("entity", eid),
            d={
                "space": int(sid),
                "key": key,
                "ekey": f"{sid}:{key}",
                "name": name,
                "type": "TERM",
                "builtin": kind,
                "description": BUILTIN_HELP[kind],
            },
        )
        out[kind] = eid
    return out


def defined(db, sid, chain=None):
    """The namespace's defined entities that hold in a collection whose path is `chain` (ids, top down; None: every
    one of them): [{id, key, name, type, collection}]."""
    rows = db.rows("SELECT record::id(id) AS id, key, name, type, collection FROM entity WHERE space = $s AND defined = true", s=int(sid))
    if chain is None:
        return rows
    ok = set(chain)
    return [e for e in rows if e.get("collection") is None or e["collection"] in ok]


def _index(db, ents):
    """{key: entity id} for the entities' names and the other ways they're said."""
    idx = {e["key"]: e["id"] for e in ents}
    ids = [e["id"] for e in ents]
    for a in db.rows("SELECT key, entity FROM entity_alias WHERE entity IN $e", e=ids) if ids else []:
        idx.setdefault(a["key"], a["entity"])
    return idx


def terms(db, sid, chain):
    """Gazetteer lines ("Name|TYPE") so the extractor finds the defined entities however they're written."""
    ents = defined(db, sid, chain)
    out = [f"{e['name']}|{e['type']}" for e in ents]
    ids = {e["id"]: e for e in ents}
    if ids:
        for a in db.rows("SELECT key, entity FROM entity_alias WHERE entity IN $e", e=list(ids)):
            out.append(f"{a['key']}|{ids[a['entity']]['type']}")
    return out


def chain_of(db, cid):
    from . import hierarchy

    try:
        return [c["id"] for c in hierarchy.path(db, cid)] if cid is not None else []
    except KeyError:
        return []


def belongs(setup, typ):
    """Whether a name of type `typ` that fits no defined entity belongs here (Unlabeled) or not (Unknown)."""
    return typ in setup["types"] if setup["types"] else typ not in QUIET


def resolve(db, sid, cid, setup, found):
    """{key: entity id} for the names found ({key: (name, type)}) in a recording of collection `cid`."""
    idx = _index(db, defined(db, sid, chain_of(db, cid)))
    special = builtins(db, sid)
    return {key: idx.get(key) or special[UNLABELED if belongs(setup, typ) else UNKNOWN] for key, (_, typ) in found.items()}


def define(db, sid, name, typ, description=None, aliases=(), collection=None, user=None):
    """Add an entity to the fixed list (or put an existing one of that name on it). Returns its id."""
    name = " ".join(str(name or "").split())
    key = analyze.ent_key(name)
    if not key:
        raise ValueError("Give the entity a name.")
    if typ not in entity_setup.type_codes(db, sid):
        raise ValueError(f"Unknown type: {typ}.")
    if collection is not None:
        from . import hierarchy

        if hierarchy.get(db, collection)["space"] != int(sid):
            raise KeyError(collection)
    row = db.one("SELECT record::id(id) AS id, builtin FROM entity WHERE ekey = $k", k=f"{sid}:{key}")
    if row and row.get("builtin"):
        raise ValueError(f"{name} is one of the entities that are always there.")
    plan = _alias_plan(db, int(sid), key, row["id"] if row else None, aliases)  # check everything before writing
    if description is not None and len(str(description).strip()) > 2000:
        raise ValueError("A description can have up to 2000 characters.")
    if row:
        eid = row["id"]
        db.q(
            "UPDATE $r SET defined = true, type = $t, collection = $c",
            r=R("entity", eid),
            t=typ,
            c=int(collection) if collection is not None else None,
        )
    else:
        eid = db.next_id("entity")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("entity", eid),
            d=store.clean(
                {
                    "space": int(sid),
                    "key": key,
                    "ekey": f"{sid}:{key}",
                    "name": name,
                    "type": typ,
                    "defined": True,
                    "collection": int(collection) if collection is not None else None,
                }
            ),
        )
    if description is not None:
        from . import entities

        entities.describe(db, eid, description)
    set_aliases(db, eid, aliases, user=user, plan=plan)
    builtins(db, sid)
    return eid


def _alias_plan(db, space, key, eid, aliases):
    """The keys to keep as the entity's other names, and the entities found so far under one of them (they fold into
    it). A name that's a defined entity's, Unknown, Unlabeled or another entity's other name is refused."""
    keys = [k for k in dict.fromkeys(analyze.ent_key(a) for a in aliases or []) if k and k != key]
    fold = []
    for k in keys:
        other = db.one("SELECT record::id(id) AS id, name, defined, builtin FROM entity WHERE ekey = $k", k=f"{space}:{k}")
        if other and other["id"] != eid:
            if other.get("defined") or other.get("builtin"):
                raise ValueError(f"“{k}” is the name of another entity on the list, {other['name']}.")
            fold.append(other["id"])
        taken = db.one("SELECT entity FROM $r", r=R("entity_alias", f"{space}:{k}"))
        if taken and eid is not None and taken["entity"] != eid and taken["entity"] not in fold:
            raise ValueError(f"“{k}” is already another entity's other name.")
    return keys, fold


def set_aliases(db, eid, aliases, user=None, plan=None):
    """The other ways an entity is said: these replace the ones it has. Entities found under one of them so far are
    merged into it (the merge can be undone)."""
    e = db.one("SELECT space, key FROM $r", r=R("entity", int(eid)))
    keys, fold = plan or _alias_plan(db, e["space"], e["key"], int(eid), aliases)
    if fold:
        from . import entities

        entities.merge(db, int(eid), fold, user=user)
    db.q("DELETE entity_alias WHERE entity = $e", e=int(eid))
    for k in keys:
        db.q("UPSERT $r CONTENT $d", r=R("entity_alias", f"{e['space']}:{k}"), d={"space": e["space"], "key": k, "entity": int(eid)})
