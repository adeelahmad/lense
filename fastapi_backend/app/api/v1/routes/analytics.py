"""Analytics (docs/analytics.md): what's done with the archive's resources, kept with the account and counted per day.

A namespace's owners see its numbers, an admin of a collection that collection's, admins everything, and each person
their own activity. Admins set how long actions are kept and can purge them.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.deps import Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db, Track
from app.domain import access as acc
from app.domain import auth, hierarchy, store, telemetry
from app.domain.store import R
from app.schemas.analytics import ActivityEntry, AnalyticsReport, AnalyticsStatus, Purged, PurgeRequest

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model_by_alias=True)
def get_analytics(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    ns: str | None = Query(None, description="a namespace you own; without it, the whole archive (admins)"),
    collection: int | None = Query(
        None, description="one of its collections, with the ones inside it (its admins, or the namespace's owners)"
    ),
    days: int = Query(30, ge=1, le=366, description="how many days back from today, when `from` isn't given"),
    date_from: dt.date | None = Query(None, alias="from", description="the first day"),
    date_to: dt.date | None = Query(None, alias="to", description="the last day (default: today)"),
) -> AnalyticsReport:
    """Views, plays, searches, downloads and comments for a range of days: totals, per day, per collection and the most
    used resources. Days are UTC."""
    last = date_to or dt.datetime.now(dt.UTC).date()
    first = date_from or last - dt.timedelta(days=days - 1)
    if first > last or (last - first).days > 366:
        raise HTTPException(400, "the range is from a day to a later one, a year at most")
    sid, cols = None, None
    if ns:
        sid = acl.nsid(ns)
        if collection is None:
            acl.need(sid, "owner")
        else:
            try:
                col = hierarchy.get(db, collection)
            except KeyError:
                col = None
            if not col or col["space"] != sid:
                raise HTTPException(404, "not found")
            acl.need_in(sid, collection, "owner")  # an admin of the collection counts as its owner
            cols = hierarchy.subtree(db, sid, collection)
    elif collection is not None:
        raise HTTPException(400, "a collection needs its namespace (ns)")
    elif not user.admin or user.via == "oauth":
        raise HTTPException(403, "the whole archive's analytics are for admins; name a namespace you own (ns)")
    d = telemetry.summary(db, first.isoformat(), last.isoformat(), sid, cols)
    return AnalyticsReport.model_validate({**d, "namespace": ns, "collection": collection})


@router.get("/analytics/me")
def my_activity(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None, description="only what's earlier than this time (the `at` of the last entry you have)"),
) -> list[ActivityEntry]:
    """Your own activity, the latest first: what Lens keeps about what you did, for analytics.retention_days."""
    rows = telemetry.mine(db, user.id, limit, before)
    ids = sorted({r["recording"] for r in rows if r.get("recording") is not None})
    recs = (
        {
            x["id"]: x
            for x in db.rows(
                "SELECT record::id(id) AS id, title, space, collection FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in ids],
            )
        }
        if ids
        else {}
    )
    names = store.space_names(db)
    out = []
    for r in rows:
        rec = recs.get(r.get("recording"))
        readable = rec is not None and acl.rank_in(rec["space"], rec.get("collection")) > 0
        title = rec["title"] if rec is not None and readable else None
        named = readable or (r.get("recording") is None and r.get("space") in acl.roles)
        out.append(
            ActivityEntry(
                at=r["at"],
                action=r["action"],
                resource=r.get("recording"),
                title=title,
                namespace=names.get(r.get("space")) if named else None,
            )
        )
    return out


@router.post("/recordings/{rid}/played", status_code=204, response_class=Response)
def played(rid: int, acl: Acl, track: Track, s: str = "") -> Response:
    """The player started playing this resource (the web app says so once per page load). Counted as a play."""
    rec = acl.recording(rid, share=s)
    track("play", rid, rec["space"], rec.get("collection"))
    return Response(status_code=204)


@router.post("/public/recordings/{rid}/played", status_code=204, response_class=Response)
def played_public(rid: int, acl: Acl, db: Db, track: Track) -> Response:
    """The same from a resource's public page, for whoever may see it there."""
    rec = db.one("SELECT space, collection FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    who = acl.who()
    permitted = rec["space"] in who.member_of or rid in who.granted or who.network.name(rid, rec["space"]) is not None
    if acc.view(acc.of(db, rid), permitted, who.signed_in) in (None, "locked"):
        raise HTTPException(404, "not found")
    track("play", rid, rec["space"], rec.get("collection"))
    return Response(status_code=204)


@router.get("/admin/analytics")
def analytics_status(user: AdminReader, db: Db, cfg: Cfg) -> AnalyticsStatus:
    """How much is kept: actions with their accounts, since when, and for how long (analytics.retention_days)."""
    return AnalyticsStatus(**telemetry.status(db, cfg))


@router.post("/admin/analytics/purge")
def purge_analytics(body: PurgeRequest, user: AdminWriter, db: Db, cfg: Cfg) -> Purged:
    """Delete the actions past their retention now (a job does it every hour anyway), or with `everything` all of
    them and the daily counts. Audited as `analytics.purge`."""
    n = telemetry.purge(db, cfg, body.everything)
    auth.audit(db, user.as_audit(), "analytics.purge", None, {"deleted": n, "everything": body.everything})
    return Purged(deleted=n)
