"""Pages for visitors (docs/access.md). Nobody needs to be signed in; people with permission see more."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.deps import Acl, Cfg, Db, Writer, domain_errors
from app.api.media import sign_url
from app.domain import access as acc
from app.domain import public, store
from app.domain.store import R
from app.email import send_access_request_email
from app.schemas.public import (
    PublicCollection,
    PublicHome,
    PublicRecording,
    PublicRequest,
    PublicRequestCreate,
    PublicSearch,
)

router = APIRouter(prefix="/public", tags=["public"])


def _signed(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for x in items:  # posters come only with media the visitor may play
        x["poster"] = sign_url(x["poster"])
    return items


@router.get("/home")
def get_public_home(acl: Acl, db: Db) -> PublicHome:
    """The home page: featured public recordings, for everyone, and the collections this visitor sees anything in."""
    d = public.home(db, acl.who())
    _signed(d["featured"])
    _signed(d["shared"])
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
        d = public.collection(db, sid, acl.who(), limit, offset)
    _signed(d["items"])
    return PublicCollection.model_validate(d)


@router.get("/search")
def search_public(
    acl: Acl,
    db: Db,
    q: str = Query("", description='words, "phrases", OR between alternatives'),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PublicSearch:
    """Search what this visitor may see: titles of the recordings they see listed, and the lines of the transcripts
    they may read. Title matches come first. Restricted recordings (for signed-in people) and public ones with the
    transcript closed match on their title only."""
    d = public.search(db, q, acl.who(), limit, offset)
    _signed(d["items"])
    return PublicSearch.model_validate(d)


@router.get("/recordings/{rid}")
def get_public_recording(rid: int, acl: Acl, db: Db, cfg: Cfg) -> PublicRecording:
    """A recording's public page: what this visitor may see of it, and nothing more.

    Anyone sees a public recording's page, description and open parts; a signed-in person sees a restricted one's title
    with a lock; people with a role in its namespace, or given permission on the recording, see all of it. Everything
    else answers 404, as a recording that doesn't exist would."""
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    a = acc.of(db, rid)
    who = acl.who()
    member, granted = rec["space"] in who.member_of, rid in who.granted
    seen = acc.view(a, member or granted, who.signed_in)
    if seen is None:
        raise HTTPException(404, "not found")
    d = public.recording(db, cfg, rid, seen, a, member, granted and not member)
    if who.signed_in and not (member or granted):
        d["can_request"] = seen == "locked" or bool(d["closed"])
        d["request"] = acc.request_of(db, rid, acl.user.id if acl.user else None)
    if d["media"]:  # only the media this visitor may play is signed
        d["media"]["url"] = sign_url(d["media"]["url"])
        d["media"]["poster"] = sign_url(d["media"]["poster"])
    return PublicRecording.model_validate(d)


@router.post("/recordings/{rid}/request")
def request_access(rid: int, body: PublicRequestCreate, acl: Acl, user: Writer, db: Db, tasks: BackgroundTasks) -> PublicRequest:
    """Ask for access to a recording (signed in, without permission): its closed parts, or a restricted one. Asking
    again replaces the earlier request. The namespace's owners hear of it by email when mail is set up; they approve
    (which gives permission) or decline in the recording's access settings."""
    rec = db.one("SELECT space, title FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    a = acc.of(db, rid)
    if acl.permitted(rid, rec["space"]):
        raise HTTPException(400, "You already see all of it.")
    seen = acc.view(a, False, True)
    if seen is None:
        raise HTTPException(404, "not found")
    if seen == "public" and set(a["open"]) >= set(acc.PARTS):
        raise HTTPException(400, "All of it is open to everyone.")
    before = acc.request_of(db, rid, user.id)
    req = acc.ask(db, rid, user.id, (body.message or "").strip() or None)
    # one email per request: asking again while it's pending (for a day) only updates the message
    recent = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat(timespec="seconds")  # as store.now() writes it
    if not before or before.get("status") != "pending" or (before.get("at") or "") < recent:
        who = f"{user.name} ({user.email})" if user.name else user.email
        names = store.space_names(db)
        tasks.add_task(
            send_access_request_email, acc.owners(db, rec["space"]), who, rid, rec.get("title"), names.get(rec["space"]), req.get("message")
        )
    return PublicRequest.model_validate(req)
