"""Dates said in assets, as days on the calendar (dates.py): what was said about a day, a month or a range."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, CurrentUser, Db
from app.domain import dates
from app.schemas.dates import DateList

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
    return DateList.model_validate({**out, "reading": bool((cfg.get("analysis") or {}).get("dates"))})
