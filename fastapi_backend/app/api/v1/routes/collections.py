"""Collections: a saved filter over the archive, or a fixed list of recordings. Yours, and others' shared ones.

A collection only ever lists recordings the viewer can read, whoever saved it. So does its podcast feed: each person
who may read it has an address of their own (app/domain/syndication.py, docs/feeds.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api import evidence as evidence_api
from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, web_origin
from app.domain import auth, evidence, recsets, store, syndication
from app.domain.store import DB
from app.schemas.collections import Collection, CollectionCreate, CollectionDetail, CollectionUpdate
from app.schemas.common import Created, Ok
from app.schemas.feeds import CollectionFeed
from app.schemas.recordings import EvidencePart

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
    """The collection with how many assets you can read in it, and the newest 200 of them."""
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
def collection_evidence(
    cid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, include: list[EvidencePart] | None = evidence_api.INCLUDE
) -> Response:
    """The collection's assets you can read (a list in its order, a filter's oldest first) as one evidence PDF made by anytopdf (a download): each
    one's pages and what Lens found about it, then a provenance page with every file's SHA-256 and size. `include` picks the parts, as for a
    resource's evidence PDF. At most 50 assets."""
    col = own_collection(db, cid, user)
    ids = recsets.members(db, col, set(acl.roles))
    if len(ids) > evidence.MAX:
        raise HTTPException(400, f"an evidence PDF holds {evidence.MAX} assets at most; this collection has {len(ids)}")
    recs = (
        db.rows(
            "SELECT record::id(id) AS id, space, collection FROM recording WHERE id IN $ids", ids=[store.R("recording", i) for i in ids]
        )
        if ids
        else []
    )
    places = {r["id"] for r in recs if acl.member(r)}
    return evidence_api.download(db, cfg, ids if col.get("recordings") is not None else ids[::-1], col["name"], include, places)


@router.patch("/{cid}")
def update_collection(cid: int, body: CollectionUpdate, user: Writer, db: Db) -> Ok:
    """Change your collection. A filter collection takes a new filter; a fixed one a new list of assets."""
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
    syndication.drop_collection(db, cid)  # its podcast feeds' addresses stop working
    return Ok()


# ---------- its podcast feed (docs/feeds.md#a-collections-podcast-feed) ----------
def _feed_id(db: DB, cfg: dict[str, Any], cid: int, user: Principal) -> int:
    """This person's feed of the collection, made (and audited as `feed.create`) the first time they ask."""
    fid, made = syndication.for_collection(db, cfg, cid, user.id, by=user.email)
    if made:
        auth.audit(db, user.as_audit(), "feed.create", f"feed:{fid}", {"kind": "podcast", "collection": cid})
    return fid


def _feed(db: DB, cfg: dict[str, Any], request: Request, cid: int, user: Principal) -> CollectionFeed:
    fid = _feed_id(db, cfg, cid, user)
    return CollectionFeed.model_validate(syndication.collection_feed(db, cfg, fid, web_origin(request)))


@router.get("/{cid}/feed")
def get_collection_feed(cid: int, request: Request, user: CurrentUser, db: Db, cfg: Cfg) -> CollectionFeed:
    """Your private podcast feed of a collection you may read: its address, which a podcast app subscribes to with no
    session (the token in it is the credential), made the first time you ask (audited as `feed.create`) and the same
    one after. Its episodes are
    the collection's assets with audio or video that you can read, as they are when the app reads it."""
    own_collection(db, cid, user)
    return _feed(db, cfg, request, cid, user)


@router.post("/{cid}/feed/token")
def rotate_collection_feed(cid: int, request: Request, user: Writer, db: Db, cfg: Cfg) -> CollectionFeed:
    """A new address for your podcast feed of the collection: the old one stops working at once, so a podcast app
    subscribed to it needs the new one. Audited as `feed.token`."""
    own_collection(db, cid, user)
    fid = _feed_id(db, cfg, cid, user)
    syndication.rotate(db, cfg, fid)
    auth.audit(db, user.as_audit(), "feed.token", f"feed:{fid}", {"collection": cid})
    return _feed(db, cfg, request, cid, user)


@router.delete("/{cid}/feed")
def delete_collection_feed(cid: int, user: Writer, db: Db) -> Ok:
    """Stop your podcast feed of the collection: its address stops working at once (asking for it again makes a new
    one). Audited as `feed.delete`."""
    own_collection(db, cid, user)
    syndication.drop_collection(db, cid, user.id)
    auth.audit(db, user.as_audit(), "feed.delete", f"saved_collection:{cid}")
    return Ok()
