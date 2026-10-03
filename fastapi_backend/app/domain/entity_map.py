"""Mapping what the extractors find onto a fixed list of entities (entity_setup.py, modes `fixed` and `hybrid`).

People with editor access define a namespace's entities (each with a name, a type, other ways it's said and a
description), for the whole namespace or for one collection and the collections inside it. When a recording is
analysed, each name found goes to:
- the defined entity it names (by its name or one of the other ways it's said; the names are looked for in the
  transcript too, so "acme" is found as well as "Acme");
- else "Unlabeled", when it belongs here but no defined entity fits: a type the setup keeps (with every type kept,
  any type but dates and numbers);
- else "Unknown".
Unknown and Unlabeled are always there in a namespace that uses the fixed or hybrid mode. People's corrections (a mention
moved to another entity) still win. In the fixed mode nothing is created from what's found. The hybrid mode is the fixed
list first, then self-organising: a name of a type that belongs here and fits no defined entity becomes an entity of its
own (or goes to the one it already is), and names of other types go to Unknown.

With `matching: model`, the names the rules can't place are given to the LLM with the entities' descriptions, their
other names and what the place is about (`judge`): in the self-organising mode it says which known entity a new name
is, if any; in the fixed mode which defined entity it is, or that it doesn't belong here; in the hybrid mode either. A
name it places on an entity becomes one of that entity's other names, so the rules place it from then on. When the
model can't be reached, the rules decide.
"""

from __future__ import annotations

import logging

from . import analyze, entity_setup, llm, store

R = store.R
log = logging.getLogger("lens")
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
    one of them): [{id, key, name, type, description, collection}]."""
    rows = db.rows(
        "SELECT record::id(id) AS id, key, name, type, description, collection FROM entity WHERE space = $s AND defined = true", s=int(sid)
    )
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


def _existing(db, sid, keys):
    """{key: entity id} for names that already are an entity of the namespace, or one's other name (hybrid mode)."""
    if not keys:
        return {}
    out = {
        r["key"]: r["id"]
        for r in db.rows("SELECT record::id(id) AS id, key FROM entity WHERE space = $s AND key IN $k", s=int(sid), k=sorted(keys))
    }
    for r in db.rows("SELECT key, entity FROM entity_alias WHERE space = $s AND key IN $k", s=int(sid), k=sorted(keys)):
        out.setdefault(r["key"], r["entity"])
    return out


def place(db, cfg, sid, cid, setup, found, seg_texts=()):
    """{key: entity id} for the names found ({key: (name, type)}) in a recording of collection `cid`, in the fixed or
    hybrid mode. A defined entity's name or other name goes to it. In the hybrid mode, a name that already is an entity
    goes to it too. With `matching: model` the model may place the rest on a listed (or, hybrid, described) entity, or
    say a name doesn't belong here. What's left goes by type: a type that doesn't belong here goes to Unknown; one that
    does goes to Unlabeled (fixed) or becomes a new entity (hybrid)."""
    hybrid = setup["mode"] == "hybrid"
    listed = defined(db, sid, chain_of(db, cid))
    idx = _index(db, listed)
    special = builtins(db, sid)
    out = {k: idx[k] for k in found if k in idx}
    if hybrid:
        out.update(_existing(db, sid, [k for k in found if k and k not in out]))
    rest = {k: v for k, v in found.items() if k and k not in out}
    if rest and setup["matching"] == "model":
        ents = list(listed)
        if hybrid:
            seen = {e["id"] for e in ents}
            ents += [e for e in described(db, sid) if e["id"] not in seen]
        picked = judge(db, cfg, sid, setup, rest, ents, ("new", UNKNOWN) if hybrid else (UNLABELED, UNKNOWN), seg_texts)
        placed = {k: v for k, v in picked.items() if isinstance(v, int)}
        learn(db, sid, placed)
        out.update(placed)
        out.update({k: special[UNKNOWN] for k, v in picked.items() if v == UNKNOWN})
    for key, (name, typ) in rest.items():
        if key in out:
            continue
        if not belongs(setup, typ):
            out[key] = special[UNKNOWN]
        elif not hybrid:
            out[key] = special[UNLABELED]
        else:
            out[key] = db.next_id("entity")
            db.q(
                "CREATE $r CONTENT $d",
                r=R("entity", out[key]),
                d={"space": int(sid), "key": key, "ekey": f"{sid}:{key}", "name": name, "type": typ},
            )
    return out


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


# ---------- matching by description (the model) ----------
JUDGE_SYSTEM = (
    "You sort the names found in a transcript into a knowledge base's entities. Use each entity's description, its other "
    "names and what the place is about. Say an entity's id only when the name means that entity (a nickname, a "
    "misspelling, a description of it); never because it is merely related. Answer for every name."
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "integer"}, "to": {"type": "string"}, "confidence": {"type": "number"}},
                "required": ["name", "to"],
            },
        }
    },
    "required": ["mappings"],
}
MAX_NAMES, MAX_ENTITIES, MIN_CONFIDENCE = 60, 300, 0.6
ANSWERS = {"new": "a new entity", "unlabeled": "belongs here but is none of the entities", "unknown": "doesn't belong here"}


def _context(seg_texts, names):
    """A line each name was said on, cut to a sentence's length."""
    out = {}
    for key, (name, _) in names.items():
        low = name.lower()
        line = next((t for t in seg_texts if low in (t or "").lower()), "")
        out[key] = line[:200]
    return out


def judge(db, cfg, sid, setup, names, entities, answers, seg_texts=()):
    """Ask the model where each name ({key: (name, type)}) goes: {key: entity id | one of `answers`}. Names it isn't sure
    of (or all of them, when the model can't be reached) are left out."""
    if not names or not llm.configured(cfg):
        return {}
    keys = list(names)[:MAX_NAMES]
    ents = entities[:MAX_ENTITIES]
    al = {}
    ids = [e["id"] for e in ents]
    for a in db.rows("SELECT key, entity FROM entity_alias WHERE entity IN $e", e=ids) if ids else []:
        al.setdefault(a["entity"], []).append(a["key"])
    types = {t["type"]: t for t in entity_setup.types_of(db, sid)}
    said = _context(seg_texts, {k: names[k] for k in keys})
    parts = []
    if setup.get("description"):
        parts.append(f"About this place: {setup['description']}")
    parts.append(
        "Entities:\n"
        + "\n".join(
            f"e{e['id']}: {e['name']} ({types.get(e['type'], {}).get('label', e['type'])})"
            + (f" - {e['description']}" if e.get("description") else "")
            + (f"; also said as: {', '.join(al[e['id']])}" if al.get(e["id"]) else "")
            for e in ents
        )
        if ents
        else "Entities: none yet."
    )
    shown = {names[k][1] for k in keys}
    parts.append(
        "Types:\n"
        + "\n".join(
            f"{t}: {types[t]['label']}" + (f" - {types[t]['description']}" if types[t].get("description") else "")
            for t in sorted(shown)
            if t in types
        )
    )
    parts.append("Answers: an entity's id (e12), or " + "; ".join(f'"{a}" ({ANSWERS[a]})' for a in answers) + ".")
    parts.append(
        "Names found:\n"
        + "\n".join(f'{i}. "{names[k][0]}" ({names[k][1]})' + (f' in: "{said[k]}"' if said.get(k) else "") for i, k in enumerate(keys))
    )
    try:
        reply = llm.json_out(cfg, JUDGE_SYSTEM, "\n\n".join(parts), JUDGE_SCHEMA)
    except Exception as e:  # noqa: BLE001 - the rules decide when the model can't
        log.warning("entity matching: the model couldn't be asked (%s); using the rules", e)
        return {}
    known = {e["id"] for e in ents}
    out = {}
    for m in (reply or {}).get("mappings") or []:
        i, to = m.get("name"), str(m.get("to") or "").strip().lower()
        if not isinstance(i, int) or not 0 <= i < len(keys) or float(m.get("confidence", 1)) < MIN_CONFIDENCE:
            continue
        if to.startswith("e") and to[1:].isdigit() and int(to[1:]) in known:
            out[keys[i]] = int(to[1:])
        elif to in answers:
            out[keys[i]] = to
    return out


def described(db, sid):
    """The entities a model can map names onto in the self-organising mode: those people described or gave other names,
    most mentioned first."""
    rows = db.rows("SELECT record::id(id) AS id, key, name, type, description, builtin, hidden FROM entity WHERE space = $s", s=int(sid))
    aliased = set(db.values("SELECT VALUE entity FROM entity_alias WHERE space = $s", s=int(sid)))
    keep = [e for e in rows if not e.get("builtin") and not e.get("hidden") and (e.get("description") or e["id"] in aliased)]
    return keep


def learn(db, sid, mapped):
    """Names the model placed become those entities' other names ({key: entity id})."""
    for key, eid in mapped.items():
        db.q("UPSERT $r CONTENT $d", r=R("entity_alias", f"{int(sid)}:{key}"), d={"space": int(sid), "key": key, "entity": int(eid)})
