"""Collections: a saved filter over the archive, or a fixed list of recordings. Yours, and others' shared ones.

A collection only ever lists recordings the viewer can read, whoever saved it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api import evidence as evidence_api
from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.domain import evidence, recsets, store
from app.domain.store import DB
from app.schemas.collections import Collection, CollectionCreate, CollectionDetail, CollectionUpdate
from app.schemas.common import Created, Ok

router = APIRouter(prefix="/collections", tags=["collections"])


def own_collection(db: DB, cid: int, user: Principal, write: bool = False) -> dict[str, Any]:
    """A collection this person may see (theirs, or shared) or, with write, change (theirs only); else 404."""
    try:
        col = recsets.get(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if col["account"] != user.id and (write or not col.get("shared")):
        raise HTTPException(404, "not found")
    return col


@router.get("")
def list_collections(user: CurrentUser, acl: Acl, db: Db) -> list[Collection]:
    readable = set(acl.roles)
    return [Collection.model_validate({**x, "count": len(recsets.members(db, x, readable))}) for x in recsets.visible(db, user.id)]


@router.post("")
def create_collection(body: CollectionCreate, user: Writer, db: Db) -> Created:
    try:
        cid = recsets.create(db, user.id, body.name, body.filter, body.recordings, body.description, body.shared)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    return Created(id=cid)


@router.get("/{cid}")
def get_collection(cid: int, user: CurrentUser, acl: Acl, db: Db) -> CollectionDetail:
    """The collection with how many recordings you can read in it, and the newest 200 of them."""
    col = own_collection(db, cid, user)
    ids = recsets.members(db, col, set(acl.roles))
    rows = (
        db.rows(
            "SELECT record::id(id) AS id, title, recorded_at, duration_ms FROM recording WHERE id IN $ids",
            ids=[store.R("recording", i) for i in ids[:200]],
        )
        if ids
        else []
    )
    return CollectionDetail.model_validate(
        {**col, "count": len(ids), "recordings": sorted(rows, key=lambda r: r.get("recorded_at") or "", reverse=True)}
    )


@router.get("/{cid}/evidence.pdf", response_class=Response, responses=evidence_api.PDF)
def collection_evidence(cid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> Response:
    """The collection's recordings you can read (a list in its order, a filter's oldest first) as one evidence PDF made by anytopdf (a download): each
    one's pages, then a provenance page with every file's SHA-256 and size. At most 50 recordings."""
    col = own_collection(db, cid, user)
    ids = recsets.members(db, col, set(acl.roles))
    if len(ids) > evidence.MAX:
        raise HTTPException(400, f"an evidence PDF holds {evidence.MAX} recordings at most; this collection has {len(ids)}")
    return evidence_api.download(db, cfg, ids if col.get("recordings") is not None else ids[::-1], col["name"])


@router.patch("/{cid}")
def update_collection(cid: int, body: CollectionUpdate, user: Writer, db: Db) -> Ok:
    """Change your collection. A filter collection takes a new filter; a fixed one a new list of recordings."""
    own_collection(db, cid, user, write=True)
    try:
        recsets.update(db, cid, body.name, body.description, body.filter, body.recordings, body.shared)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    return Ok()


@router.delete("/{cid}")
def delete_collection(cid: int, user: Writer, db: Db) -> Ok:
    own_collection(db, cid, user, write=True)
    db.q("DELETE $r", r=store.R("saved_collection", cid))
    return Ok()
