"""Pages for visitors (docs/access.md). Nobody needs to be signed in; people with permission see more."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, Db
from app.api.media import sign_url
from app.domain import access as acc
from app.domain import public
from app.domain.store import R
from app.schemas.public import PublicRecording

router = APIRouter(prefix="/public", tags=["public"])


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
