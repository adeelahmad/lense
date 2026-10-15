"""The stream of activities (docs/automations.md, Contract): what happened in Lens, newest first, from every source,
as Activity Streams 2.0 activities; one activity by its id; and the kinds of activity there are. People see what their
namespaces and each activity's audience allow."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, domain_errors
from app.domain import events, store
from app.schemas.activities import ActivityKind, ActivityPage, StreamActivity

router = APIRouter(prefix="/activities", tags=["activities"])
# activities come back as the stream has them: fields a source leaves out stay out, rather than null


def viewer(db: Any, user: Principal, acl: Access) -> dict[str, Any]:
    """Who reads the stream: an admin signed in or with their own key reads everything; an app an admin gave access to
    reads every namespace but not what is for admins alone; anyone else reads their namespaces."""
    if user.admin and user.via != "oauth":
        return events.viewer(user.id, None, admin=True)
    spaces = acl.readable()
    return events.viewer(user.id, set(store.space_names(db)) if spaces is None else spaces)


def _many(values: list[str] | None) -> list[str] | None:
    """Repeated or comma-separated query values."""
    out = [v.strip() for x in values or [] for v in x.split(",") if v.strip()]
    return out or None


def filters(
    acl: Access,
    kinds: list[str] | None = None,
    actor: list[str] | None = None,
    namespace: list[str] | None = None,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
    q: str | None = None,
    where: Any = None,
) -> dict[str, Any]:
    """The timeline filters for events.page: namespaces by name (404 for one that isn't there), `where` as a match
    tree or its JSON."""
    if isinstance(where, str) and where.strip():
        try:
            where = json.loads(where)
        except ValueError:
            raise HTTPException(
                400, 'where is a match tree as JSON, like {"all": [{"path": "kind", "op": "equals", "value": "asset.added"}]}'
            ) from None
    names = _many(namespace)
    return {
        k: v
        for k, v in {
            "kinds": _many(kinds),
            "actor": _many(actor),
            "spaces": [acl.nsid(n) for n in names] if names else None,
            "tag": tag,
            "since": since,
            "until": until,
            "q": q,
            "where": where or None,
        }.items()
        if v not in (None, "")
    }


def timeline(db: Any, cfg: dict[str, Any], who: dict[str, Any], flt: dict[str, Any], before: str | None, limit: int) -> dict[str, Any]:
    with domain_errors():
        page = events.page(db, cfg, who, before, flt, limit)
    names = store.space_names(db)
    used = {s for a in page["items"] for s in events.spaces_of(a)}
    return {**page, "namespaces": {str(s): names[s] for s in sorted(used) if s in names}}


@router.get("", response_model_exclude_unset=True)
def list_activities(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    kinds: list[str] | None = Query(None, description="kinds of activity, like asset.added (the kinds under them count too)"),
    actor: list[str] | None = Query(None, description="who did it, by the actor's id, like lens:account/1"),
    namespace: list[str] | None = Query(None, description="only these namespaces, by name"),
    tag: str | None = Query(None, description="tagged with this entity, by its id (lens:entity/9) or name"),
    since: str | None = Query(None, description="published after this time (ISO 8601)"),
    until: str | None = Query(None, description="published at or before this time (ISO 8601)"),
    q: str | None = Query(None, max_length=200, description="words in the summary"),
    where: str | None = Query(None, max_length=20000, description="a match tree on the activity, as JSON"),
    before: str | None = Query(None, max_length=4000, description="the next of the page before"),
    limit: int = Query(50, ge=1, le=200),
) -> ActivityPage:
    """What happened in Lens, newest first: new assets and everything else the stream holds, each an Activity Streams
    2.0 activity with a Lens kind. Give `next` as `before` for the page after. Noisy kinds (like sensor readings) are
    left out unless named in `kinds`. Text from outside Lens is listed in each activity's `lens.untrusted`."""
    flt = filters(acl, kinds, actor, namespace, tag, since, until, q, where)
    return ActivityPage(**timeline(db, cfg, viewer(db, user, acl), flt, before, limit))


@router.get("/kinds", response_model_exclude_unset=True)
def list_activity_kinds(user: CurrentUser) -> list[ActivityKind]:
    """Every kind of activity: its label, the source that makes it, its parent and children (a trigger on a kind
    matches the kinds under it), a JSON Schema of its activities with each field's title, the fields holding text
    from outside Lens, and a sample."""
    return [ActivityKind(**k) for k in events.kinds()]


@router.get("/{activity_id}", response_model_exclude_unset=True)
def get_activity(activity_id: str, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> StreamActivity:
    """One activity by its id, like create-recording-7."""
    a = events.get(db, cfg, activity_id, viewer(db, user, acl))
    if not a:
        raise HTTPException(404, "not found")
    return StreamActivity(**a)
