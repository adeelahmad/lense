"""Pages for visitors (docs/access.md). Nobody needs to be signed in; people with permission see more."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, Db, domain_errors
from app.api.media import sign_url
from app.domain import access as acc
from app.domain import public
from app.domain.store import R
from app.schemas.public import PublicCollection, PublicHome, PublicRecording

router = APIRouter(prefix="/public", tags=["public"])


def _signed(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for x in items:  # posters come only with media the visitor may play
        x["poster"] = sign_url(x["poster"])
    return items


@router.get("/home")
def get_public_home(acl: Acl, db: Db) -> PublicHome:
    """The home page: featured public recordings, for everyone, and the collections this visitor sees anything in."""
    d = public.home(db, set(acl.roles), acl.user is not None)
    _signed(d["featured"])
    return PublicHome.model_validate(d)


@router.get("/collections/{name}")
def get_public_collection(
    name: str,
    acl: Acl,
    db: Db,
    limit: int = Query(48, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PublicCollection:
    """A collection's page: its description and the recordings this visitor sees, newest first. Visitors see the public
    ones; signed-in people also see restricted ones, locked; members see all of them. A collection with nothing for
    this visitor answers 404, as a missing one does."""
    sid = acl.nsid(name)
    with domain_errors():
        d = public.collection(db, sid, set(acl.roles), acl.user is not None, limit, offset)
    _signed(d["items"])
    return PublicCollection.model_validate(d)


@router.get("/recordings/{rid}")
def get_public_recording(rid: int, acl: Acl, db: Db, cfg: Cfg) -> PublicRecording:
    """A recording's public page: what this visitor may see of it, and nothing more.

    Anyone sees a public recording's page, description and open parts; a signed-in person sees a restricted one's title
    with a lock; people with a role in its namespace see all of it. Everything else answers 404, as a recording that
    doesn't exist would."""
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    a = acc.of(db, rid)
    member = acl.permitted(rec)
    seen = acc.view(a, member, acl.user is not None)
    if seen is None:
        raise HTTPException(404, "not found")
    d = public.recording(db, cfg, rid, seen, a, member)
    if d["media"]:  # only the media this visitor may play is signed
        d["media"]["url"] = sign_url(d["media"]["url"])
        d["media"]["poster"] = sign_url(d["media"]["poster"])
    return PublicRecording.model_validate(d)
