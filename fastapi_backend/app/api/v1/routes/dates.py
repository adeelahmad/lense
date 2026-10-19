"""Dates said in assets, as days on the calendar (dates.py): what was said about a day, a month or a range."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, AdminWriter, Cfg, CurrentUser, Db
from app.domain import approvals, dates, event_proposals
from app.email import app_url
from app.schemas.dates import DateList, EventProposal, EventProposed

router = APIRouter(tags=["dates"])


@router.get("/dates")
def list_dates(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
    start: str = Query("", description="from this day on: YYYY-MM-DD, or YYYY-MM or YYYY for its first day"),
    end: str = Query("", description="up to this day: YYYY-MM-DD, or YYYY-MM or YYYY for its last day"),
    ns: str | None = Query(None, description="one namespace"),
    recording: int | None = Query(None, description="one asset"),
    q: str = Query("", description="only dates whose words or line have this text"),
    limit: int = 50,
    offset: int = 0,
) -> DateList:
    """The dates the assets you can read give that fall between `start` and `end` (a date that spans days counts when
    any of its days does), the earliest first, each with its line. Analysis reads them while Settings → Analysis → Read
    dates is on."""
    try:
        a, b = dates.day_bound(start), dates.day_bound(end, last=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    spaces, within = acl.scope(ns)
    out = dates.said(db, spaces, within, a, b, recording, q.strip() or None, max(1, min(limit, 200)), max(0, offset))
    reading = bool((cfg.get("analysis") or {}).get("dates"))
    return DateList.model_validate({**out, "reading": reading, "proposable": event_proposals.offered(db, user.admin)})


@router.post("/dates/propose")
def propose_event(body: EventProposal, acl: Acl, user: AdminWriter, db: Db, cfg: Cfg) -> EventProposed:
    """Propose a date said in an asset as a calendar event: an event for a Google Calendar connection that may add
    events, prefilled from the line (the day said, a time said in it, the line as its title, who it names), waiting in
    Approvals for your yes. Nothing is added or sent until you approve it there; declining it does nothing. Proposing
    the same date again while it waits gives the same approval."""
    acl.recording(body.recording_id)
    given = body.model_dump(exclude={"recording_id", "segment", "index"}, exclude_unset=True)
    try:
        out = event_proposals.propose(db, cfg, user.as_audit(), body.recording_id, body.segment, body.index, web=app_url(cfg, db), **given)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return EventProposed.model_validate({**out, "url": approvals.link(cfg, db, out["approval"])})
