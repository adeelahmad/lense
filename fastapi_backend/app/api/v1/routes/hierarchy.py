"""A namespace's collections, which every recording lives in (docs/api.md#collections-of-a-namespace): list them as
a tree, make, rename, move, describe and delete them, choose the default, and give people roles on them. Saved
collections (routes/collections.py) are lists of recordings from anywhere, and something else."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, hierarchy
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.hierarchy import CollectionMember, CollectionMemberSet, CollectionNode, CollectionNodeCreate, CollectionNodeUpdate

router = APIRouter(prefix="/namespaces/{name}/collections", tags=["namespaces"])
AS_COLLECTION = {1: "viewer", 2: "editor", 3: "admin"}  # a namespace owner is an admin of every collection in it


def _seen(acl: Access, name: str) -> tuple[int, set[int] | None]:
    """The namespace, and the collections of it this person sees (None: all of them); 404 when none."""
    sid = acl.nsid(name)
    if not acl.user:
        raise HTTPException(401, "sign in first")
    return sid, acl.visible(sid)


def _in(db: DB, sid: int, cid: int, only: set[int] | None = None) -> dict[str, Any]:
    """One of the namespace's collections that this person sees, else 404."""
    try:
        c = hierarchy.get(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if c["space"] != sid or (only is not None and c["id"] not in only):
        raise HTTPException(404, "not found")
    return c


def _can_change(acl: Access, sid: int, cid: int | None) -> bool:
    """Editors of the namespace arrange all its collections; an admin of a collection, it and those inside it."""
    return auth.allows(acl.roles, sid, "editor") or (cid is not None and acl.collection_role(sid, cid) == "admin")


def _can_grant(acl: Access, sid: int, cid: int) -> bool:
    """Owners of the namespace give roles on any of its collections; an admin of a collection, on it and those inside."""
    return auth.allows(acl.roles, sid, "owner") or acl.collection_role(sid, cid) == "admin"


def _change(acl: Access, sid: int, cid: int | None) -> None:
    if not _can_change(acl, sid, cid):
        raise HTTPException(403, "needs editor access to the namespace" + ("" if cid is None else ", or admin of this collection"))


def _out(acl: Access, sid: int, n: dict[str, Any]) -> CollectionNode:
    rank = acl.rank_in(sid, n["id"])
    return CollectionNode(
        **n, role=AS_COLLECTION.get(rank), can_change=_can_change(acl, sid, n["id"]), can_grant=_can_grant(acl, sid, n["id"])
    )


def _node(db: DB, acl: Access, sid: int, cid: int) -> CollectionNode:
    """One collection as this person sees it now: after a change, the roles they hold through the tree may differ."""
    if acl.user and not acl.user.admin:
        acl.user.collections = hierarchy.roles_of(db, acl.user.id)
    only = acl.visible(sid)
    return _out(acl, sid, next(n for n in hierarchy.tree(db, sid, only=only) if n["id"] == cid))


@router.get("")
def list_namespace_collections(name: str, acl: Acl, user: CurrentUser, db: Db) -> list[CollectionNode]:
    """The namespace's collections, depth first and by name, each with its place in the tree, how many recordings it
    holds (with and without the collections inside it), and what you may do with it. Someone who sees only some
    collections of the namespace gets those, starting from the ones they were given."""
    sid, only = _seen(acl, name)
    return [_out(acl, sid, n) for n in hierarchy.tree(db, sid, only=only)]


@router.post("")
def create_namespace_collection(name: str, body: CollectionNodeCreate, acl: Acl, user: Writer, db: Db) -> CollectionNode:
    """Make a collection at the top of the namespace (its editors) or inside `parent` (them, or an admin of `parent`).
    Its name is unique among the collections next to it, ignoring case; collections go at most 8 deep. Audited as
    `collection.create`."""
    sid, only = _seen(acl, name)
    if body.parent is not None:
        _in(db, sid, body.parent, only)
    _change(acl, sid, body.parent)
    with domain_errors():
        cid = hierarchy.create(db, sid, body.name, body.parent, body.description, user.email)
    auth.audit(db, user.as_audit(), "collection.create", f"collection:{cid}", {"name": body.name, "namespace": name, "parent": body.parent})
    return _node(db, acl, sid, cid)


@router.get("/{cid}")
def get_namespace_collection(name: str, cid: int, acl: Acl, user: CurrentUser, db: Db) -> CollectionNode:
    sid, only = _seen(acl, name)
    _in(db, sid, cid, only)
    return _node(db, acl, sid, cid)


@router.patch("/{cid}")
def update_namespace_collection(name: str, cid: int, body: CollectionNodeUpdate, acl: Acl, user: Writer, db: Db) -> CollectionNode:
    """Rename it, describe it, move it inside another collection of the namespace (`parent`; null: to the top) or make
    it the default. Editors of the namespace may do all of that; an admin of the collection all but moving it to the
    top or making it the default, and only into a collection they're an admin of. The recordings and collections
    inside it go with it. Audited as `collection.update`."""
    sid, only = _seen(acl, name)
    _in(db, sid, cid, only)
    _change(acl, sid, cid)
    sent = body.model_fields_set
    if "parent" in sent:
        if body.parent is not None:
            _in(db, sid, body.parent, only)
        _change(acl, sid, body.parent)
    if "default" in sent and body.default is not None and not auth.allows(acl.roles, sid, "editor"):
        raise HTTPException(403, "needs editor access to the namespace: the default is the namespace's")
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
    return _node(db, acl, sid, cid)


@router.delete("/{cid}")
def delete_namespace_collection(name: str, cid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete an empty collection (editors of the namespace, or an admin of it), with the roles given on it: 409 while
    it holds recordings or collections, or is the namespace's default. Audited as `collection.delete`."""
    sid, only = _seen(acl, name)
    _in(db, sid, cid, only)
    _change(acl, sid, cid)
    try:
        gone = hierarchy.delete(db, cid)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "collection.delete", f"collection:{cid}", {"name": gone["name"], "namespace": name})
    return Ok()


@router.get("/{cid}/members")
def list_collection_members(name: str, cid: int, acl: Acl, user: CurrentUser, db: Db) -> list[CollectionMember]:
    """Who was given a role on the collection, then who has one through a collection it's inside (owners of the
    namespace, and admins of the collection). People with a role in the namespace aren't listed: theirs holds in
    every collection."""
    sid, only = _seen(acl, name)
    _in(db, sid, cid, only)
    if not _can_grant(acl, sid, cid):
        raise HTTPException(403, "needs owner access to the namespace, or admin of this collection")
    return [CollectionMember.model_validate(m) for m in hierarchy.members(db, cid)]


@router.put("/{cid}/members")
def set_collection_member(name: str, cid: int, body: CollectionMemberSet, acl: Acl, user: Writer, db: Db) -> list[CollectionMember]:
    """Give someone a role on the collection (and the collections inside it), change it, or (role null) take it away.
    They needn't have a role in the namespace: then they see just this collection. Owners of the namespace and admins
    of the collection. Answers with the members; audited as `collection.member`."""
    sid, only = _seen(acl, name)
    c = _in(db, sid, cid, only)
    if not _can_grant(acl, sid, cid):
        raise HTTPException(403, "needs owner access to the namespace, or admin of this collection")
    acct = auth.find_account(db, body.email) if body.email else auth.get_account(db, body.account or 0)
    if not acct:
        raise HTTPException(404, "no such person")
    with domain_errors():
        before = hierarchy.give(db, cid, acct["id"], body.role, user.email)
    if before != body.role:
        detail = {"namespace": name, "name": c["name"], "account": acct["id"], "email": acct["email"], "role": body.role, "before": before}
        auth.audit(db, user.as_audit(), "collection.member", f"collection:{cid}", detail)
    return [CollectionMember.model_validate(m) for m in hierarchy.members(db, cid)]
