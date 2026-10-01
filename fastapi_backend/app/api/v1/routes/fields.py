"""Custom metadata fields (docs/api.md#fields): defined by editors on a namespace or a collection, for the collections,
resources or files inside it; their values on each of those.

Editors of the namespace define fields on it and on any of its collections; editors of a collection, on it. Values are
set by whoever may edit the item: editors of a resource for it and its files, and for a collection those who arrange
it. Defining, changing and deleting fields and saving values are audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.api.v1.routes.hierarchy import _can_change, _in
from app.domain import auth, files, hierarchy, store
from app.domain import fields as fieldmod
from app.domain import metadata as md
from app.domain.store import DB
from app.schemas.fields import FieldCreate, FieldDef, FieldUpdate, FieldValue, FieldValues, FieldValuesUpdate

R = store.R
router = APIRouter(tags=["fields"])
EDITOR = auth.ROLES["editor"]


def _may_define(acl: Access, sid: int, cid: int | None) -> bool:
    """Editors of the namespace define fields anywhere in it; editors of a collection, on it."""
    return auth.allows(acl.roles, sid, "editor") or (cid is not None and acl.rank_in(sid, cid) >= EDITOR)


def _paths(db: DB, sid: int) -> dict[int, list[str]]:
    """Each collection's names from the top."""
    return {n["id"]: n["path"] for n in hierarchy.tree(db, sid, counts={})}


def _def(acl: Access, f: dict[str, Any], paths: dict[int, list[str]], uses: int | None = None) -> FieldDef:
    cid = f.get("collection")
    return FieldDef(
        id=f["id"],
        label=f["label"],
        type=f["type"],
        target=f["target"],
        options=f.get("options"),
        help=f.get("help"),
        published=bool(f.get("published")),
        collection=cid,
        collection_path=paths.get(cid, []) if cid is not None else [],
        ord=f.get("ord"),
        can_change=_may_define(acl, f["space"], cid),
        uses=uses,
    )


def _field(db: DB, acl: Access, sid: int, fid: int) -> dict[str, Any]:
    """One of the namespace's fields this person sees (the namespace's own, or one of a collection they see)."""
    try:
        f = fieldmod.get(db, fid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    only = acl.visible(sid)
    if f["space"] != sid or (f.get("collection") is not None and only is not None and f["collection"] not in only):
        raise HTTPException(404, "not found")
    return f


def _define(acl: Access, sid: int, cid: int | None) -> None:
    if not _may_define(acl, sid, cid):
        raise HTTPException(403, "needs editor access to the namespace" + ("" if cid is None else ", or to this collection"))


# ---------- definitions ----------
@router.get("/namespaces/{name}/fields")
def list_fields(name: str, acl: Acl, user: CurrentUser, db: Db) -> list[FieldDef]:
    """The namespace's custom fields: its own, then those of its collections (the ones you see), each in order."""
    sid = acl.nsid(name)
    only = acl.visible(sid)
    rows = [f for f in fieldmod.of_space(db, sid) if f.get("collection") is None or only is None or f["collection"] in only]
    paths = _paths(db, sid)
    return [_def(acl, f, paths) for f in rows]


@router.post("/namespaces/{name}/fields")
def create_field(name: str, body: FieldCreate, acl: Acl, user: Writer, db: Db) -> FieldDef:
    """Define a field on the namespace, or on one of its collections (`collection`). It describes the resources, the
    collections or the files inside (`target`); new fields are internal unless `published`. Editors; audited as
    `field.create`."""
    sid = acl.nsid(name)
    only = acl.visible(sid)  # 404 for a namespace they see nothing of
    if body.collection is not None:
        _in(db, sid, body.collection, only)
    _define(acl, sid, body.collection)
    with domain_errors():
        f = fieldmod.create(db, sid, body.label, body.type, body.target, body.collection, body.options, body.help, body.published, user.id)
    detail = {"field": f["id"], "label": f["label"], "type": f["type"], "target": f["target"], "collection": f.get("collection")}
    auth.audit(db, user.as_audit(), "field.create", f"space:{sid}", detail)
    return _def(acl, f, _paths(db, sid))


@router.get("/namespaces/{name}/fields/{fid}")
def get_field(name: str, fid: int, acl: Acl, user: CurrentUser, db: Db) -> FieldDef:
    """One field, with how many items have a value for it (`uses`)."""
    sid = acl.nsid(name)
    f = _field(db, acl, sid, fid)
    return _def(acl, f, _paths(db, sid), fieldmod.uses(db, f))


@router.patch("/namespaces/{name}/fields/{fid}")
def update_field(name: str, fid: int, body: FieldUpdate, acl: Acl, user: Writer, db: Db) -> FieldDef:
    """Rename a field, change its options (400 for one that items have chosen), its help, whether it's published, or
    its place in the order. Its type and what it describes stay. Editors of where it's defined; audited as
    `field.update`."""
    sid = acl.nsid(name)
    f = _field(db, acl, sid, fid)
    _define(acl, sid, f.get("collection"))
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    for k in ("label", "options", "published", "ord"):
        if k in changes and changes[k] is None:
            raise HTTPException(400, f"{k} can't be cleared")
    with domain_errors():
        f, changed = fieldmod.update(db, fid, changes)
    if changed:
        auth.audit(db, user.as_audit(), "field.update", f"space:{sid}", {"field": fid, "label": f["label"], "changes": changed})
    return _def(acl, f, _paths(db, sid))


@router.delete("/namespaces/{name}/fields/{fid}")
def delete_field(name: str, fid: int, acl: Acl, user: Writer, db: Db) -> FieldDef:
    """Delete a field and every value it has. Editors of where it's defined; audited as `field.delete` with how many
    values went. Answers with the field as it was, `uses` saying how many values were deleted."""
    sid = acl.nsid(name)
    f = _field(db, acl, sid, fid)
    _define(acl, sid, f.get("collection"))
    paths = _paths(db, sid)
    f, n = fieldmod.delete(db, fid)
    auth.audit(db, user.as_audit(), "field.delete", f"space:{sid}", {"field": fid, "label": f["label"], "values": n})
    return _def(acl, f, paths, n)


# ---------- values ----------
def _values(acl: Access, defs: list[dict[str, Any]], row: dict[str, Any], paths: dict[int, list[str]], can: bool) -> FieldValues:
    return FieldValues(
        fields=[FieldValue(field=_def(acl, x["field"], paths), value=x["value"]) for x in fieldmod.shown(defs, row)],
        can_change=can,
    )


def _changes(body: FieldValuesUpdate) -> dict[int, Any]:
    try:
        return {int(k): v for k, v in body.values.items()}
    except ValueError:
        raise HTTPException(400, "values are keyed by field ids") from None


def _changed_labels(defs: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [f["label"] for f in defs if before.get(fieldmod.slot(f["id"])) != after.get(fieldmod.slot(f["id"]))]


@router.get("/recordings/{rid}/fields")
def get_resource_fields(rid: int, acl: Acl, user: CurrentUser, db: Db) -> FieldValues:
    """The custom fields that describe this resource (defined on its namespace and on the collections it's in), with
    its values."""
    rec = acl.recording(rid)
    defs, row = fieldmod.resource_fields(db, rid)
    can = acl.rank_in(rec["space"], rec.get("collection")) >= EDITOR
    return _values(acl, defs, row, _paths(db, rec["space"]), can)


@router.put("/recordings/{rid}/fields")
def save_resource_fields(rid: int, body: FieldValuesUpdate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> FieldValues:
    """Set or clear (null) the resource's values for the fields named; the others keep theirs. Editors. Kept in its
    metadata history (so a revert puts them back) and audited as `fields.save`."""
    rec = acl.recording(rid, "editor")
    defs, row = fieldmod.resource_fields(db, rid)
    with domain_errors():
        after = fieldmod.merged(row, defs, _changes(body))
    before = row.get("fields") or {}
    if after != before:
        md.save_fields(db, cfg, rid, after, user.email)
        detail = {"fields": _changed_labels(defs, before, after)}
        auth.audit(db, user.as_audit(), "fields.save", f"recording:{rid}", detail)
    row = {**row, "fields": after}
    return _values(acl, defs, row, _paths(db, rec["space"]), True)


def _collection(db: DB, acl: Access, name: str, cid: int) -> tuple[int, dict[str, Any]]:
    sid = acl.nsid(name)
    c = _in(db, sid, cid, acl.visible(sid))
    return sid, db.one("SELECT space, parent, fields FROM $r", r=R("collection", cid)) or c


@router.get("/namespaces/{name}/collections/{cid}/fields")
def get_collection_fields(name: str, cid: int, acl: Acl, user: CurrentUser, db: Db) -> FieldValues:
    """The custom fields that describe this collection (defined on the namespace, and on the collections it's inside),
    with its values."""
    sid, row = _collection(db, acl, name, cid)
    defs = fieldmod.applying(db, sid, "collection", row.get("parent"))
    return _values(acl, defs, row, _paths(db, sid), _can_change(acl, sid, cid))


@router.put("/namespaces/{name}/collections/{cid}/fields")
def save_collection_fields(name: str, cid: int, body: FieldValuesUpdate, acl: Acl, user: Writer, db: Db) -> FieldValues:
    """Set or clear (null) the collection's values for the fields named. Those who arrange it (editors of the namespace,
    admins of the collection); audited as `fields.save`."""
    sid, row = _collection(db, acl, name, cid)
    if not _can_change(acl, sid, cid):
        raise HTTPException(403, "needs editor access to the namespace, or admin of this collection")
    defs = fieldmod.applying(db, sid, "collection", row.get("parent"))
    with domain_errors():
        after = fieldmod.merged(row, defs, _changes(body))
    before = row.get("fields") or {}
    if after != before:
        fieldmod.save(db, "collection", cid, after)
        auth.audit(db, user.as_audit(), "fields.save", f"collection:{cid}", {"fields": _changed_labels(defs, before, after)})
    return _values(acl, defs, {**row, "fields": after}, _paths(db, sid), True)


def _file_row(db: DB, rid: int, fid: int) -> dict[str, Any]:
    try:
        files.get(db, rid, fid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return db.one("SELECT fields FROM $r", r=R("resource_file", fid)) or {}


@router.get("/recordings/{rid}/files/{fid}/fields")
def get_file_fields(rid: int, fid: int, acl: Acl, user: CurrentUser, db: Db) -> FieldValues:
    """The custom fields that describe this file (defined on its resource's namespace and collections), with its
    values."""
    rec = acl.recording(rid)
    row = _file_row(db, rid, fid)
    defs = fieldmod.applying(db, rec["space"], "file", rec.get("collection"))
    can = acl.rank_in(rec["space"], rec.get("collection")) >= EDITOR
    return _values(acl, defs, row, _paths(db, rec["space"]), can)


@router.put("/recordings/{rid}/files/{fid}/fields")
def save_file_fields(rid: int, fid: int, body: FieldValuesUpdate, acl: Acl, user: Writer, db: Db) -> FieldValues:
    """Set or clear (null) the file's values for the fields named. Editors of the resource; audited as `fields.save`."""
    rec = acl.recording(rid, "editor")
    row = _file_row(db, rid, fid)
    defs = fieldmod.applying(db, rec["space"], "file", rec.get("collection"))
    with domain_errors():
        after = fieldmod.merged(row, defs, _changes(body))
    before = row.get("fields") or {}
    if after != before:
        fieldmod.save(db, "file", fid, after)
        detail = {"file": fid, "fields": _changed_labels(defs, before, after)}
        auth.audit(db, user.as_audit(), "fields.save", f"recording:{rid}", detail)
    return _values(acl, defs, {**row, "fields": after}, _paths(db, rec["space"]), True)
