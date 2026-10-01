"""A namespace's collections, which every recording lives in (docs/api.md#collections-of-a-namespace): list them as
a tree, make, rename, move, describe and delete them, and choose the default. Saved collections
(routes/collections.py) are lists of recordings from anywhere, and something else."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, hierarchy
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.hierarchy import CollectionNode, CollectionNodeCreate, CollectionNodeUpdate

router = APIRouter(prefix="/namespaces/{name}/collections", tags=["namespaces"])


def _in(db: DB, sid: int, cid: int) -> dict[str, Any]:
    """One of the namespace's collections, else 404."""
    try:
        c = hierarchy.get(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if c["space"] != sid:
        raise HTTPException(404, "not found")
    return c


def _node(db: DB, sid: int, cid: int) -> CollectionNode:
    return CollectionNode(**next(n for n in hierarchy.tree(db, sid) if n["id"] == cid))


@router.get("")
def list_namespace_collections(name: str, acl: Acl, user: CurrentUser, db: Db) -> list[CollectionNode]:
    """The namespace's collections, depth first and by name, each with its place in the tree and how many recordings
    it holds (with and without the collections inside it)."""
    sid = acl.namespace(name)
    return [CollectionNode(**n) for n in hierarchy.tree(db, sid)]


@router.post("")
def create_namespace_collection(name: str, body: CollectionNodeCreate, acl: Acl, user: Writer, db: Db) -> CollectionNode:
    """Make a collection (editors), at the top of the namespace or inside `parent`. Its name is unique among the
    collections next to it, ignoring case; collections go at most 8 deep. Audited as `collection.create`."""
    sid = acl.namespace(name, "editor")
    if body.parent is not None:
        _in(db, sid, body.parent)
    with domain_errors():
        cid = hierarchy.create(db, sid, body.name, body.parent, body.description, user.email)
    auth.audit(db, user.as_audit(), "collection.create", f"collection:{cid}", {"name": body.name, "namespace": name, "parent": body.parent})
    return _node(db, sid, cid)


@router.get("/{cid}")
def get_namespace_collection(name: str, cid: int, acl: Acl, user: CurrentUser, db: Db) -> CollectionNode:
    sid = acl.namespace(name)
    _in(db, sid, cid)
    return _node(db, sid, cid)


@router.patch("/{cid}")
def update_namespace_collection(name: str, cid: int, body: CollectionNodeUpdate, acl: Acl, user: Writer, db: Db) -> CollectionNode:
    """Rename it, describe it, move it inside another collection of the namespace (`parent`; null: to the top) or make
    it the default (editors). The recordings and collections inside it go with it. Audited as `collection.update`."""
    sid = acl.namespace(name, "editor")
    _in(db, sid, cid)
    sent = body.model_fields_set
    if "parent" in sent and body.parent is not None:
        _in(db, sid, body.parent)
    if body.default is False and hierarchy.is_default(db, cid):
        raise HTTPException(400, "make another collection the default instead")
    with domain_errors():
        changed = hierarchy.update(
            db,
            cid,
            body.name,
            body.description if "description" in sent else hierarchy.UNSET,
            body.parent if "parent" in sent else hierarchy.UNSET,
        )
    if body.default and not hierarchy.is_default(db, cid):
        hierarchy.make_default(db, cid)
        changed["default"] = [False, True]
    if changed:
        auth.audit(db, user.as_audit(), "collection.update", f"collection:{cid}", {"namespace": name, **changed})
    return _node(db, sid, cid)


@router.delete("/{cid}")
def delete_namespace_collection(name: str, cid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete an empty collection (editors): 409 while it holds recordings or collections, or is the namespace's
    default. Audited as `collection.delete`."""
    sid = acl.namespace(name, "editor")
    _in(db, sid, cid)
    try:
        gone = hierarchy.delete(db, cid)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "collection.delete", f"collection:{cid}", {"name": gone["name"], "namespace": name})
    return Ok()
