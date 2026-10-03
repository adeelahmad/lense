"""How a namespace (or a collection of it) organises its entities, and the entity types a namespace adds.

Everyone who sees the namespace reads them. Editors of the namespace change its setup, its types and any collection's
setup; editors of a collection change that collection's. Changes are audited, and apply to recordings analysed from
then on (re-analyse to apply them to the others).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, hierarchy
from app.domain import entity_setup as setup
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.entity_setup import (
    EntitySetup,
    EntitySetupSave,
    EntitySetupView,
    EntityTypeCreate,
    EntityTypeInfo,
    EntityTypeUpdate,
)

router = APIRouter(tags=["entities"])
EDITOR = auth.ROLES["editor"]


def _may_change(acl: Access, sid: int, cid: int | None) -> bool:
    return auth.allows(acl.roles, sid, "editor") or (cid is not None and acl.rank_in(sid, cid) >= EDITOR)


def _need(acl: Access, sid: int, cid: int | None = None) -> None:
    if not _may_change(acl, sid, cid):
        raise HTTPException(403, "needs editor access to the namespace" + ("" if cid is None else ", or to this collection"))


def _view(db: DB, acl: Access, sid: int, s: dict[str, Any], cid: int | None, paths: dict[int, list[str]]) -> EntitySetup:
    return EntitySetup(
        mode=s["mode"],
        types=s["types"],
        description=s.get("description"),
        matching=s["matching"],
        collection=cid,
        collection_path=paths.get(cid, []) if cid is not None else [],
        updated_at=s.get("updated_at"),
        updated_by=s.get("updated_by"),
        can_change=_may_change(acl, sid, cid),
    )


@router.get("/namespaces/{name}/entity-setup")
def get_entity_setup(name: str, user: CurrentUser, acl: Acl, db: Db) -> EntitySetupView:
    """The namespace's entity setup, the collections (you see) with their own, and the types entities may have."""
    sid = acl.nsid(name)
    only = acl.visible(sid)
    saved = setup.scopes(db, sid)
    paths = {n["id"]: n["path"] for n in hierarchy.tree(db, sid, counts={})}
    ns = saved.get(None) or setup.DEFAULT
    cols = [_view(db, acl, sid, s, cid, paths) for cid, s in saved.items() if cid is not None and (only is None or cid in only)]
    cols.sort(key=lambda c: [p.casefold() for p in c.collection_path])
    return EntitySetupView(
        namespace=_view(db, acl, sid, ns, None, paths),
        saved=None in saved,
        collections=cols,
        types=[EntityTypeInfo(**t) for t in setup.types_of(db, sid)],
        can_change=_may_change(acl, sid, None),
    )


@router.put("/namespaces/{name}/entity-setup")
def save_entity_setup(name: str, body: EntitySetupSave, user: Writer, acl: Acl, db: Db) -> EntitySetup:
    """Save the namespace's setup, or (with `collection`) one collection's own."""
    sid = acl.nsid(name)
    if body.collection is not None:
        acl.visible(sid)
    _need(acl, sid, body.collection)
    with domain_errors():
        setup.save(db, sid, body.collection, body.mode, body.types, body.description, body.matching, by=user.email)
    auth.audit(
        db,
        user.as_audit(),
        "entity_setup.save",
        f"collection:{body.collection}" if body.collection is not None else f"namespace:{name}",
        body.model_dump(),
    )
    paths = {n["id"]: n["path"] for n in hierarchy.tree(db, sid, counts={})}
    return _view(db, acl, sid, setup.scopes(db, sid)[body.collection], body.collection, paths)


@router.delete("/namespaces/{name}/entity-setup/collections/{cid}")
def clear_entity_setup(name: str, cid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """The collection follows its parents' setup again."""
    sid = acl.nsid(name)
    acl.visible(sid)
    with domain_errors():
        if hierarchy.get(db, cid)["space"] != sid:
            raise KeyError(cid)
    _need(acl, sid, cid)
    setup.clear(db, sid, cid)
    auth.audit(db, user.as_audit(), "entity_setup.clear", f"collection:{cid}", None)
    return Ok()


@router.post("/namespaces/{name}/entity-types", status_code=201)
def create_entity_type(name: str, body: EntityTypeCreate, user: Writer, acl: Acl, db: Db) -> EntityTypeInfo:
    """A type of the namespace's own, e.g. "Client" or "Project"; its code is the name in capitals."""
    sid = acl.nsid(name)
    _need(acl, sid)
    with domain_errors():
        t = setup.add_kind(db, sid, body.label, body.description)
    auth.audit(db, user.as_audit(), "entity_type.create", f"namespace:{name}", t)
    return EntityTypeInfo(**t)


@router.patch("/namespaces/{name}/entity-types/{code}")
def update_entity_type(name: str, code: str, body: EntityTypeUpdate, user: Writer, acl: Acl, db: Db) -> EntityTypeInfo:
    sid = acl.nsid(name)
    _need(acl, sid)
    with domain_errors():
        t = setup.change_kind(db, sid, code, body.label, body.description)
    auth.audit(db, user.as_audit(), "entity_type.update", f"namespace:{name}", t)
    return EntityTypeInfo(**t)


@router.delete("/namespaces/{name}/entity-types/{code}")
def delete_entity_type(name: str, code: str, user: Writer, acl: Acl, db: Db) -> Ok:
    """Only a type no entity has."""
    sid = acl.nsid(name)
    _need(acl, sid)
    with domain_errors():
        setup.remove_kind(db, sid, code)
    auth.audit(db, user.as_audit(), "entity_type.delete", f"namespace:{name}", {"type": code})
    return Ok()
