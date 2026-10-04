"""The activity ledger (docs/activity.md): a resource's history (its calls in and out, runs and changes) and what it
cost. Admins see every resource's; others see their own account's, their chats', and the recordings and namespaces they
can read."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, AdminReader, CurrentUser, Db
from app.domain import activity
from app.schemas.activity import ActivityCosts, ActivityEntry, ActivityTop, ActivityTotals

router = APIRouter(tags=["activity"])
Period = Literal["day", "week", "month", "all"]
RESOURCE = re.compile(r"^([a-z_]{1,40}):([A-Za-z0-9_.-]{1,80})$")


def _may_see(db: Db, user: CurrentUser, acl: Acl, resource: str) -> None:
    m = RESOURCE.match(resource)
    if not m:
        raise HTTPException(422, "resource is table:id, like routine:3 or recording:12")
    if user.admin:
        return
    table, key = m.groups()
    if table == "account" and key == str(user.id):
        return
    if key.isdigit():
        if table == "recording":
            acl.recording(int(key))
            return
        if table == "space":
            acl.need(int(key))
            return
        if table == "chat" and db.one("SELECT id FROM $r WHERE account = $a", r=activity.R("chat", int(key)), a=user.id):
            return
    raise HTTPException(403, "only admins see this resource's activity")


@router.get("/activity")
def resource_history(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    resource: str = Query(description="table:id, like routine:3, pipeline:2, workflow:4, recording:12, account:1"),
    kind: Literal["in", "out", "run", "change"] | None = None,
    before: str | None = Query(None, description="only entries before this time (the last `at` of the page before)"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[ActivityEntry]:
    """A resource's history, newest first: API requests that changed it, calls made for it (models, embeddings, the
    decision model, webhooks, web tools) with tokens and cost, runs that ended, and its audit log entries."""
    _may_see(db, user, acl, resource)
    return activity.history(db, resource, limit, before, kind)


@router.get("/activity/totals")
def resource_totals(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    resource: str | None = Query(None, description="table:id; none for everything (admins)"),
    period: Period = "month",
) -> ActivityTotals:
    """What a resource's calls cost this day, week (from Monday), month (UTC) or all time: calls, tokens, USD, time."""
    if resource is None:
        if not user.admin:
            raise HTTPException(403, "only admins see the totals for everything")
    else:
        _may_see(db, user, acl, resource)
    since = activity._since(period)
    return ActivityTotals(resource=resource, period=period, since=since, **activity.totals(db, resource, since))


@router.get("/activity/costs")
def resource_costs(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    resource: list[str] = Query(description="table:id, repeated (up to 500): every row of a list at once"),
    period: Period = "month",
) -> ActivityCosts:
    """What each of many resources cost this period, for lists; ones you can't see are left out. `estimate` says a
    figure is a floor (some calls had no price or token counts)."""
    seen = []
    for r in resource[:500]:
        try:
            _may_see(db, user, acl, r)
        except HTTPException:
            continue
        seen.append(r)
    since = activity._since(period)
    return ActivityCosts(period=period, since=since, costs=activity.costs(db, seen, since))


@router.get("/activity/top")
def top_resources(
    user: AdminReader,
    db: Db,
    table: str | None = Query(
        None, pattern=r"^[a-z_]{1,40}$", description="only this kind of resource: routine, pipeline, workflow, space"
    ),
    period: Period = "month",
    limit: int = Query(20, ge=1, le=200),
) -> ActivityTop:
    """The resources that cost most this period (admins)."""
    since = activity._since(period)
    return ActivityTop(period=period, since=since, resources=activity.top(db, table, since, limit))
