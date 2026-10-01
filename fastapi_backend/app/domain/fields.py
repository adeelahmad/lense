"""Custom metadata fields (docs/api.md#fields): defined by editors on a namespace or on a collection, for the
collections, resources or files inside it.

A field has a label, a type, what it describes (`target`: resource, collection or file), help text and, for choices,
its options. A field defined on a namespace applies to everything in the namespace; one defined on a collection, to
what is inside that collection (its resources and their files, and the collections inside it). Each field is published
or internal: published ones appear on public pages and in IIIF metadata, internal ones only in the workspace; new
fields start internal.

Values are kept on the items themselves, in their `fields` object under "f<field id>", so lists can filter by them.
A resource's values are part of its metadata history (metadata.py), so changes can be reverted; a value of a field
that no longer applies (the resource moved) is kept but not shown. Deleting a field deletes its values.
"""

from __future__ import annotations

import datetime as dt
import math
import re

from . import hierarchy, store

R = store.R
TYPES = ("text", "longtext", "number", "date", "boolean", "choice", "choices", "link")
TARGETS = ("resource", "collection", "file")
TABLE = {"resource": "recording", "collection": "collection", "file": "resource_file"}
LABEL_MAX, HELP_MAX, OPTION_MAX, OPTIONS_MAX = 80, 300, 120, 200
TEXT_MAX, LONGTEXT_MAX, LINK_MAX = 500, 5000, 2000
MAX_FIELDS = 100  # in one namespace
DATE_RX = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")
LINK_RX = re.compile(r"^https?://[^\s]+$")
FIELDS = (
    "record::id(id) AS id, space, collection, target, label, type, options, help, published ?? false AS published, ord, "
    "created_at, created_by, updated_at"
)


def slot(fid):
    """Where an item keeps a field's value: fields.<slot>."""
    return f"f{int(fid)}"


def _key(sid, cid, target, label):
    return f"{sid}:{cid or 0}:{target}:{' '.join(label.split()).casefold()}"


def _label(v):
    v = " ".join(str(v or "").split())
    if not v:
        raise ValueError("Name the field.")
    if len(v) > LABEL_MAX:
        raise ValueError(f"A field's name can have up to {LABEL_MAX} characters.")
    return v


def _help(v):
    v = " ".join(str(v or "").split())
    if len(v) > HELP_MAX:
        raise ValueError(f"A field's help can have up to {HELP_MAX} characters.")
    return v or None


def _options(type_, v):
    if type_ not in ("choice", "choices"):
        return None
    out, seen = [], set()
    for o in v or []:
        o = " ".join(str(o or "").split())
        if not o or o.casefold() in seen:
            continue
        if len(o) > OPTION_MAX:
            raise ValueError(f"An option can have up to {OPTION_MAX} characters.")
        seen.add(o.casefold())
        out.append(o)
    if not out:
        raise ValueError("A choice field needs at least one option.")
    if len(out) > OPTIONS_MAX:
        raise ValueError(f"A choice field can have up to {OPTIONS_MAX} options.")
    return out


# ---------- definitions ----------
def get(db, fid):
    """The field, or KeyError."""
    f = db.one(f"SELECT {FIELDS} FROM $r", r=R("field", int(fid)))
    if not f:
        raise KeyError(fid)
    return f


def of_space(db, sid):
    """Every field of a namespace: the namespace's own first, then by where they're defined, each in its order."""
    rows = db.rows(f"SELECT {FIELDS} FROM field WHERE space = $s", s=sid)
    return sorted(rows, key=lambda f: (f.get("collection") is not None, f.get("collection") or 0, f.get("ord") or 0, f["id"]))


def _chain(db, cid):
    """A collection and the ones it's inside."""
    return {c["id"] for c in hierarchy.path(db, cid)} if cid is not None else set()


def applying(db, sid, target, cid=None, rows=None):
    """The fields of namespace `sid` that describe an item of `target` whose place is collection `cid`: those defined
    on the namespace, and on `cid` and the collections it's inside. (For a collection, `cid` is its parent: a field
    defined on a collection describes the collections inside it, not the collection itself.)"""
    chain = _chain(db, cid)
    rows = of_space(db, sid) if rows is None else rows
    return [f for f in rows if f["target"] == target and (f.get("collection") is None or f["collection"] in chain)]


def create(db, sid, label, type_, target, collection=None, options=None, help=None, published=False, by=None):
    """A new field; ValueError for a bad definition or a name its place already has, KeyError for a collection of
    another namespace."""
    if type_ not in TYPES:
        raise ValueError(f"A field's type is one of {', '.join(TYPES)}.")
    if target not in TARGETS:
        raise ValueError(f"A field describes one of {', '.join(TARGETS)}.")
    if collection is not None and hierarchy.get(db, collection)["space"] != sid:
        raise KeyError(collection)
    label = _label(label)
    key = _key(sid, collection, target, label)
    if db.values("SELECT VALUE id FROM field WHERE key = $k LIMIT 1", k=key):
        raise ValueError(f"There's a field called “{label}” there already.")
    if len(db.values("SELECT VALUE id FROM field WHERE space = $s", s=sid)) >= MAX_FIELDS:
        raise ValueError(f"A namespace can have up to {MAX_FIELDS} fields.")
    fid = db.next_id("field")
    ords = db.values("SELECT VALUE ord FROM field WHERE space = $s", s=sid)
    t = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("field", fid),
        d=store.clean(
            {
                "space": sid,
                "collection": collection,
                "target": target,
                "label": label,
                "key": key,
                "type": type_,
                "options": _options(type_, options),
                "help": _help(help),
                "published": bool(published),
                "ord": max([o for o in ords if isinstance(o, int)], default=0) + 1,
                "created_at": t,
                "created_by": by,
                "updated_at": t,
            }
        ),
    )
    return get(db, fid)


def uses(db, f, value=None):
    """How many items have a value for the field (or, with `value`, have that option chosen)."""
    s = slot(f["id"])
    table = TABLE[f["target"]]
    if value is None:
        cond = f"fields.{s} != NONE"
    elif f["type"] == "choices":
        cond = f"fields.{s} CONTAINS $v"
    else:
        cond = f"fields.{s} = $v"
    return len(db.values(f"SELECT VALUE id FROM {table} WHERE space = $s AND {cond}", s=f["space"], v=value))


def update(db, fid, changes):
    """Change a field's name, options, help, whether it's published, or its place in the order (the keys in
    `changes`). An option that items have chosen can't be taken away. Returns the field and what changed."""
    f = get(db, fid)
    after = {}
    if "label" in changes:
        label = _label(changes["label"])
        key = _key(f["space"], f.get("collection"), f["target"], label)
        if label != f["label"] and db.values("SELECT VALUE id FROM field WHERE key = $k AND id != $r LIMIT 1", k=key, r=R("field", fid)):
            raise ValueError(f"There's a field called “{label}” there already.")
        after.update(label=label, key=key)
    if "options" in changes:
        if f["type"] not in ("choice", "choices"):
            raise ValueError("Only choice fields have options.")
        options = _options(f["type"], changes["options"])
        for gone in [o for o in f.get("options") or [] if o not in options]:
            n = uses(db, f, gone)
            if n:
                raise ValueError(f"{n} item{'s' if n != 1 else ''} chose “{gone}”: change them before taking it away.")
        after["options"] = options
    if "help" in changes:
        after["help"] = _help(changes["help"])
    if "published" in changes:
        after["published"] = bool(changes["published"])
    if "ord" in changes:
        after["ord"] = int(changes["ord"])
    changed = {k: [f.get(k), v] for k, v in after.items() if k != "key" and f.get(k) != v}
    if after:
        sets = [f"{k} = NONE" if v is None else f"{k} = $v_{k}" for k, v in after.items()]
        db.q(
            f"UPDATE $r SET {', '.join(sets)}, updated_at = $t",
            r=R("field", int(fid)),
            t=store.now(),
            **{f"v_{k}": v for k, v in after.items() if v is not None},
        )
    return get(db, fid), changed


def delete(db, fid):
    """Delete a field and its values. Returns the field and how many values went."""
    f = get(db, fid)
    n = uses(db, f)
    s = slot(f["id"])
    db.run(
        [f"UPDATE {TABLE[f['target']]} SET fields.{s} = NONE WHERE space = $s AND fields.{s} != NONE", "DELETE $r"],
        s=f["space"],
        r=R("field", int(fid)),
    )
    return f, n


def delete_for_collection(db, cid):
    """Delete the fields defined on a collection (when it is deleted)."""
    for fid in db.values("SELECT VALUE record::id(id) FROM field WHERE collection = $c", c=int(cid)):
        delete(db, fid)


# ---------- values ----------
def check_value(f, v):
    """A value for the field, as it is kept; None clears it. ValueError when it doesn't fit."""
    if v is None or (isinstance(v, str) and not v.strip()) or v == []:
        return None
    t, name = f["type"], f["label"]
    if t in ("text", "longtext"):
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            raise ValueError(f"{name} is text.")
        v = str(v).strip() if t == "longtext" else " ".join(str(v).split())
        most = TEXT_MAX if t == "text" else LONGTEXT_MAX
        if len(v) > most:
            raise ValueError(f"{name} can have up to {most} characters.")
        return v
    if t == "number":
        try:
            n = float(v) if not isinstance(v, bool) else math.nan
        except (TypeError, ValueError):
            n = math.nan
        if not math.isfinite(n):
            raise ValueError(f"{name} is a number.")
        return int(n) if n.is_integer() and abs(n) < 2**53 else n
    if t == "date":
        v = str(v).strip()
        if not DATE_RX.match(v):
            raise ValueError(f"{name} is a date: YYYY, YYYY-MM or YYYY-MM-DD.")
        if len(v) == 10:
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"{name}: {v} isn't a day of the calendar.") from None
        return v
    if t == "boolean":
        if isinstance(v, bool):
            return v
        raise ValueError(f"{name} is yes or no.")
    if t == "choice":
        if not isinstance(v, str) or v not in (f.get("options") or []):
            raise ValueError(f"{name} is one of: {', '.join(f.get('options') or [])}.")
        return v
    if t == "choices":
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or any(not isinstance(x, str) or x not in (f.get("options") or []) for x in v):
            raise ValueError(f"{name} takes some of: {', '.join(f.get('options') or [])}.")
        return [o for o in f.get("options") or [] if o in v]  # in the options' order, once each
    if t == "link":
        v = str(v).strip()
        if not LINK_RX.match(v) or len(v) > LINK_MAX:
            raise ValueError(f"{name} is a link starting with http:// or https://.")
        return v
    raise ValueError(f"{name} has a type Lens doesn't know.")


def values_of(row):
    """An item's values, by field id."""
    return {int(k[1:]): v for k, v in (row.get("fields") or {}).items() if re.fullmatch(r"f\d+", k) and v is not None}


def merged(row, defs, changes):
    """An item's `fields` object after `changes` ({field id: value or None}), each checked against its definition in
    `defs` (the fields that apply to it). ValueError for a value that doesn't fit, or a field that doesn't apply."""
    out = dict(row.get("fields") or {})
    by_id = {f["id"]: f for f in defs}
    for fid, v in changes.items():
        f = by_id.get(int(fid))
        if f is None:
            raise ValueError(f"Field {fid} doesn't describe this.")
        value = check_value(f, v)
        if value is None:
            out.pop(slot(fid), None)
        else:
            out[slot(fid)] = value
    return out


def save(db, target, item, fields):
    """Keep an item's `fields` object (NONE when empty)."""
    db.q(
        f"UPDATE $r SET fields = {'$f' if fields else 'NONE'}",
        r=R(TABLE[target], int(item)),
        f=fields or None,
    )


def shown(defs, row):
    """The fields that apply to an item with their values: [{field, value}]."""
    vals = values_of(row)
    return [{"field": f, "value": vals.get(f["id"])} for f in defs]


def display(f, v):
    """A value in words, as public pages and IIIF show it."""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def published_pairs(defs, row):
    """IIIF label/value pairs for the item's published fields that have a value."""
    vals = values_of(row)
    return [
        {"label": {"none": [f["label"]]}, "value": {"none": [display(f, vals[f["id"]])]}}
        for f in defs
        if f.get("published") and vals.get(f["id"]) is not None
    ]


def resource_fields(db, rid):
    """(the fields that apply to a resource, its row with `fields`)."""
    rec = db.one("SELECT space, collection, fields FROM $r", r=R("recording", int(rid))) or {}
    if not rec:
        raise KeyError(rid)
    return applying(db, rec["space"], "resource", rec.get("collection")), rec


def filter_condition(f, raw):
    """A WHERE condition (and its parameter) on recordings for a Library filter on the field: any value when `raw` is
    empty, else text that contains it, the same number, a date that starts with it, yes or no, or the option."""
    s = slot(f["id"])
    raw = (raw or "").strip()
    if not raw:
        return f"fields.{s} != NONE", None
    t = f["type"]
    if t in ("text", "longtext", "link"):
        return f"string::contains(string::lowercase(fields.{s} ?? ''), $fv)", raw.lower()
    if t == "number":
        try:
            return f"fields.{s} = $fv", check_value(f, raw)
        except ValueError:
            return "false", None
    if t == "date":
        return f"string::starts_with(fields.{s} ?? '', $fv)", raw
    if t == "boolean":
        return f"fields.{s} = $fv", raw.lower() in ("true", "yes", "1")
    if t == "choices":
        return f"fields.{s} CONTAINS $fv", raw
    return f"fields.{s} = $fv", raw
