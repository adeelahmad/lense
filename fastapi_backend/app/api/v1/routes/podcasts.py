"""Podcasts: two-host learning episodes made from picked resources and passages, every claim cited
(app/domain/podcasts.py, docs/podcasts.md). An episode is a recording, so its audio, transcript, sharing and deletion
also go through /recordings; these routes start, show and remake episodes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.domain import auth, deletion, jobs, podcasts, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.podcasts import Podcast, PodcastCreate, PodcastJob, PodcastStarted, PodcastSummary

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def start(acl: Access, user: Principal, db: DB, cfg: dict, body: PodcastCreate) -> PodcastStarted:
    """Start an episode about what was picked, for this person: every picked resource must be one they can read."""
    sel = body.selection.model_dump()
    try:
        picked = podcasts.selected_recordings(podcasts.clean_selection(sel))
    except podcasts.Problem as e:
        raise HTTPException(400, str(e)) from None
    for rid in picked:
        acl.recording(rid)  # 404 for one they can't see
    readable = acl.readable()
    try:
        out = podcasts.create(
            db,
            cfg,
            sel,
            by=user.email,
            roles=user.roles,
            admin=user.admin,
            readable=readable,
            also=picked if readable is not None else (),
            prompt=body.prompt,
            length=body.length,
            style=body.style,
            title=body.title,
            voices=body.voices.model_dump(exclude_none=True) if body.voices else None,
        )
    except podcasts.Problem as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "not found") from None
    auth.audit(db, user.as_audit(), "podcast.create", f"recording:{out['episode']}", {"sources": picked})
    return PodcastStarted(**{**out, "namespace": store.space_names(db).get(out["namespace"], "")})


@router.post("", status_code=202)
def create_podcast(body: PodcastCreate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> PodcastStarted:
    """Make an episode about these resources and lines, in the background (watch its job on /events). It goes in the
    podcasts namespace when everyone there can read every source, else in the sources' own namespace (400 when they
    span several); you need editor access where it goes. Audited as `podcast.create`."""
    return start(acl, user, db, cfg, body)


@router.get("")
def list_podcasts(acl: Acl, user: CurrentUser, db: Db, limit: int = 50, offset: int = 0) -> list[PodcastSummary]:
    """Episodes in the namespaces you can read, newest first."""
    return [PodcastSummary.model_validate(e) for e in podcasts.list_episodes(db, acl.readable(), min(max(limit, 1), 200), max(offset, 0))]


def _episode(acl: Access, db: DB, rid: int, role: str = "viewer") -> dict:
    rec = acl.recording(rid, role)
    if not db.one("SELECT id FROM $r", r=store.R("podcast", rid)):
        raise HTTPException(404, "not found")
    return rec


@router.get("/{rid}")
def get_podcast(rid: int, acl: Acl, user: CurrentUser, db: Db) -> Podcast:
    """An episode: what was asked for, how far it has got, the excerpts it drew on, the outline, the script with each
    line's citations and times, and what the fact-check changed. Its audio is /recordings/{id}/audio."""
    _episode(acl, db, rid)
    ep = podcasts.view(db, rid)
    live = jobs.list_jobs(db, recording=rid, status=jobs.ACTIVE, limit=1)
    return Podcast.model_validate({**ep, "namespace": store.space_names(db).get(ep.get("space")), "job": live[0]["id"] if live else None})


@router.post("/{rid}/regenerate", status_code=202)
def regenerate_podcast(rid: int, acl: Acl, user: Writer, db: Db) -> PodcastJob:
    """Make the episode again from what was asked for, reading its sources afresh (editors). A job already making it
    is returned instead."""
    _episode(acl, db, rid, "editor")
    jid = podcasts.regenerate(db, rid, by=user.email)
    auth.audit(db, user.as_audit(), "podcast.regenerate", f"recording:{rid}")
    return PodcastJob(job=jid)


@router.delete("/{rid}")
def delete_podcast(rid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg, request: Request) -> Ok:
    """Delete an episode with its audio (editors of its namespace, unlike other recordings: Lens made it). 409 while a
    job is working on it."""
    _episode(acl, db, rid, "editor")
    try:
        gone = deletion.delete(db, cfg, rid, user.email)
    except deletion.Running as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "podcast.delete", f"recording:{rid}", gone)
    request.app.state.graph_cache.clear()
    return Ok()
