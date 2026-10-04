"""How a namespace organises its entities, with an override for any collection of it, and the entity types it adds to
the built-in ones (docs/processing.md#entity-setup).

A setup has a mode, the types it keeps and a description of what the place is about. The mode is
- `self` (the default, and how Lens always worked): every name the extractors find becomes an entity, and people
  merge, rename and describe them. `types` lists the types kept (empty: all of them); names of any other type are left
  out.
- `fixed`: people with editor access define the entities, and what's found is mapped onto them, or onto "Unlabeled"
  (it belongs here: a type kept) or "Unknown" (it doesn't): see entity_map.py.
- `hybrid`: the defined entities first, then self-organising: a name of a type kept that fits none of them becomes an
  entity of its own; names of other types go to "Unknown". A collection's setup holds for the collections inside it, unless one of them has its own; a
recording follows the setup of its collection, else its namespace's. Callers check roles and write the audit log.
"""

from __future__ import annotations

import re

from . import hierarchy, store

R = store.R
MODES = ("self", "fixed", "hybrid")
MATCHING = ("rules", "model")
DESCRIPTION_MAX = 2000
LABEL_MAX = 40
MAX_KINDS = 50  # custom types in one namespace
DEFAULT = {"mode": "self", "types": [], "description": None, "matching": "rules"}
# what the built-in types are, for people and for the model that maps names to them
BUILTIN_HELP = {
    "PERSON": "A person, by name.",
    "ORG": "A company, institution, team or other organisation.",
    "PRODUCT": "A product, tool, model or service.",
    "PLACE": "A country, city, building or other place.",
    "EVENT": "A named event: a conference, a launch, a war.",
    "WORK": "A book, paper, film, law or other named work.",
    "TERM": "A topic or term that isn't one of the other types.",
    "DATE": "A date or time.",
    "NUMBER": "An amount, percentage or other number.",
}


def _sid_key(sid, cid):
    return f"{int(sid)}-{int(cid) if cid is not None else 0}"


def _text(v, most, what):
    v = str(v or "").strip()
    if len(v) > most:
        raise ValueError(f"{what} can have up to {most} characters.")
    return v or None


# ---------- types ----------
def kinds(db, sid):
    """The namespace's own types, by label."""
    rows = db.rows("SELECT code, label, description FROM entity_kind WHERE space = $s", s=int(sid))
    return sorted(rows, key=lambda k: k["label"].casefold())


def labels(db, spaces):
    """{namespace: {type: label}} for the namespaces' own types (the built-in ones are entities.TYPES)."""
    rows = db.rows("SELECT space, code, label FROM entity_kind WHERE space IN $s", s=sorted(spaces)) if spaces else []
    out = {}
    for k in rows:
        out.setdefault(k["space"], {})[k["code"]] = k["label"]
    return out


def types_of(db, sid=None):
    """Every type an entity of namespace `sid` may have: the built-in ones, then the namespace's own."""
    from .entities import QUIET, TYPES

    out = [{"type": k, "label": v, "description": BUILTIN_HELP.get(k), "quiet": k in QUIET, "builtin": True} for k, v in TYPES.items()]
    if sid is not None:
        out += [
            {"type": k["code"], "label": k["label"], "description": k.get("description"), "quiet": False, "builtin": False}
            for k in kinds(db, sid)
        ]
    return out


def type_codes(db, sid):
    return {t["type"] for t in types_of(db, sid)}


def code_for(label):
    """TYPE codes are what extractors and models say: capitals and underscores ("Client team" → CLIENT_TEAM)."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]", "_", label.upper())).strip("_")[:LABEL_MAX]


def add_kind(db, sid, label, description=None):
    label = " ".join(str(label or "").split())
    if not label:
        raise ValueError("Name the type.")
    if len(label) > LABEL_MAX:
        raise ValueError(f"A type's name can have up to {LABEL_MAX} characters.")
    code = code_for(label)
    if not code or not code[0].isalpha():
        raise ValueError("A type's name starts with a letter.")
    if code in type_codes(db, sid) or any(t["label"].casefold() == label.casefold() for t in types_of(db, sid)):
        raise ValueError(f"This namespace already has a type called {label}.")
    if len(kinds(db, sid)) >= MAX_KINDS:
        raise ValueError(f"A namespace can have up to {MAX_KINDS} types of its own.")
    row = {
        "space": int(sid),
        "code": code,
        "label": label,
        "description": _text(description, DESCRIPTION_MAX, "A description"),
        "at": store.now(),
    }
    db.q("CREATE $r CONTENT $d", r=R("entity_kind", f"{int(sid)}-{code}"), d=row)
    return {"type": code, "label": label, "description": row["description"], "quiet": False, "builtin": False}


def _kind(db, sid, code):
    k = db.one("SELECT code, label, description FROM $r", r=R("entity_kind", f"{int(sid)}-{code}"))
    if not k:
        raise KeyError(code)
    return k


def change_kind(db, sid, code, label=None, description=None):
    k = _kind(db, sid, code)
    if label is not None:
        label = " ".join(str(label).split())
        if not label or len(label) > LABEL_MAX:
            raise ValueError(f"A type's name has 1 to {LABEL_MAX} characters.")
        if any(t["label"].casefold() == label.casefold() and t["type"] != code for t in types_of(db, sid)):
            raise ValueError(f"This namespace already has a type called {label}.")
        k["label"] = label
    if description is not None:
        k["description"] = _text(description, DESCRIPTION_MAX, "A description")
    db.q("UPDATE $r SET label = $l, description = $d", r=R("entity_kind", f"{int(sid)}-{code}"), l=k["label"], d=k.get("description"))
    return {"type": code, "label": k["label"], "description": k.get("description"), "quiet": False, "builtin": False}


def remove_kind(db, sid, code):
    _kind(db, sid, code)
    n = len(db.rows("SELECT record::id(id) AS id FROM entity WHERE space = $s AND type = $t LIMIT 1000", s=int(sid), t=code))
    if n:
        raise ValueError(f"{n} entit{'y has' if n == 1 else 'ies have'} this type: give them another first.")
    db.q("DELETE $r", r=R("entity_kind", f"{int(sid)}-{code}"))
    for sc in db.rows("SELECT record::id(id) AS id, types FROM entity_scope WHERE space = $s", s=int(sid)):
        if code in (sc.get("types") or []):
            db.q("UPDATE $r SET types = $t", r=R("entity_scope", sc["id"]), t=[t for t in sc["types"] if t != code])


# ---------- setups ----------
def _clean(row):
    return {
        "mode": row.get("mode") if row.get("mode") in MODES else "self",
        "types": list(row.get("types") or []),
        "description": row.get("description"),
        "matching": row.get("matching") if row.get("matching") in MATCHING else "rules",
    }


def scopes(db, sid):
    """Every setup saved in the namespace: {collection id or None: setup (with updated_at, updated_by)}."""
    out = {}
    for r in db.rows(
        "SELECT collection, mode, types, description, matching, updated_at, updated_by FROM entity_scope WHERE space = $s", s=int(sid)
    ):
        out[r.get("collection")] = {**_clean(r), "updated_at": r.get("updated_at"), "updated_by": r.get("updated_by")}
    return out


def effective(db, sid, cid=None, saved=None):
    """The setup that holds for collection `cid` of namespace `sid` (None: the namespace itself), and where it comes
    from: {"collection": id} for a collection's own, {"namespace": true}, or {"default": true}."""
    saved = scopes(db, sid) if saved is None else saved
    try:
        chain = hierarchy.path(db, cid) if cid is not None else []
    except KeyError:  # a collection deleted since
        chain = []
    for c in reversed(chain):
        if c["id"] in saved:
            return {**saved[c["id"]], "from": {"collection": c["id"]}}
    if None in saved:
        return {**saved[None], "from": {"namespace": True}}
    return {**DEFAULT, "from": {"default": True}}


def for_recording(db, rid):
    """The setup a recording's entities follow."""
    rec = db.one("SELECT space, collection FROM $r", r=R("recording", int(rid))) or {}
    return effective(db, rec["space"], rec.get("collection")) if rec.get("space") is not None else {**DEFAULT, "from": {"default": True}}


def save(db, sid, cid=None, mode="self", types=None, description=None, matching="rules", by=None):
    """Set the namespace's setup, or (with `cid`) one collection's own."""
    if mode not in MODES:
        raise ValueError(f"The mode is one of {', '.join(MODES)}.")
    if matching not in MATCHING:
        raise ValueError(f"Matching is one of {', '.join(MATCHING)}.")
    if cid is not None and hierarchy.get(db, cid)["space"] != int(sid):
        raise KeyError(cid)
    known = type_codes(db, sid)
    types = list(dict.fromkeys(types or []))
    bad = [t for t in types if t not in known]
    if bad:
        raise ValueError(f"Unknown type: {', '.join(bad)}.")
    row = {
        "space": int(sid),
        "collection": int(cid) if cid is not None else None,
        "mode": mode,
        "types": types,
        "description": _text(description, DESCRIPTION_MAX, "A description"),
        "matching": matching,
        "updated_at": store.now(),
        "updated_by": by,
    }
    db.q("UPSERT $r CONTENT $d", r=R("entity_scope", _sid_key(sid, cid)), d=row)
    if mode != "self":  # Unknown and Unlabeled are always there
        from . import entity_map

        entity_map.builtins(db, sid)
    return effective(db, sid, cid)


def clear(db, sid, cid):
    """A collection follows its parents' setup again."""
    db.q("DELETE $r", r=R("entity_scope", _sid_key(sid, cid)))


def keeps(setup, typ):
    """Whether this setup keeps names of type `typ`."""
    return not setup["types"] or typ in setup["types"]
