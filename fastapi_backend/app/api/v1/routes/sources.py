"""Storage sources (local folders and cloud storage, through rclone) and the watched folders that import from them."""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.api.deps import Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db
from app.domain import auth, sources, store
from app.domain.store import DB
from app.schemas.admin import Started
from app.schemas.common import Created, Ok
from app.schemas.sources import (
    Backend,
    BrowseEntry,
    Source,
    SourceCreate,
    SourceCreated,
    SourceHealth,
    SourceUpdate,
    SourceUpdated,
    Watch,
    WatchCreate,
    WatchPreview,
    WatchPreviewRequest,
    WatchUpdate,
)

log = logging.getLogger("lens")
router = APIRouter(tags=["sources"])


def _source(db: DB, sid: int) -> None:
    try:
        sources.get(db, sid)
    except KeyError:
        raise HTTPException(404, "not found") from None


def _watch(db: DB, wid: int) -> None:
    if not db.one("SELECT id FROM $r", r=store.R("watch_path", wid)):
        raise HTTPException(404, "not found")


@router.get("/sources/backends")
def list_backends(user: CurrentUser) -> dict[str, Backend]:
    """The kinds of storage a source can be, with their settings and secrets."""
    keys = {"label", "fields", "secrets", "oauth"}
    return {k: Backend.model_validate({f: x for f, x in v.items() if f in keys}) for k, v in sources.BACKENDS.items()}


@router.get("/sources")
def list_sources(user: AdminReader, db: Db) -> list[Source]:
    return sources.list_sources(db)


@router.post("/sources")
def create_source(body: SourceCreate, user: AdminWriter, db: Db, cfg: Cfg) -> SourceCreated:
    """Add a source and test it; secrets are stored encrypted and never shown again."""
    try:
        sid = sources.create(db, cfg, body.name, body.type, body.params, body.secrets, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "source.create", f"storage_source:{sid}")
    return SourceCreated(id=sid, health=sources.test(db, cfg, sid))


@router.patch("/sources/{sid}")
def update_source(sid: int, body: SourceUpdate, user: AdminWriter, db: Db, cfg: Cfg) -> SourceUpdated:
    _source(db, sid)
    try:
        sources.update(db, cfg, sid, body.name, body.params, body.secrets)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "source.update", f"storage_source:{sid}")
    return SourceUpdated(health=sources.test(db, cfg, sid))


@router.delete("/sources/{sid}")
def delete_source(sid: int, user: AdminWriter, db: Db) -> Ok:
    """Remove a source and its watched folders."""
    sources.remove(db, sid)
    auth.audit(db, user.as_audit(), "source.delete", f"storage_source:{sid}")
    return Ok()


@router.post("/sources/{sid}/test")
def test_source(sid: int, user: AdminWriter, db: Db, cfg: Cfg) -> SourceHealth:
    _source(db, sid)
    return sources.test(db, cfg, sid)


@router.get("/sources/{sid}/browse")
def browse_source(sid: int, user: AdminReader, db: Db, cfg: Cfg, path: str = "") -> list[BrowseEntry]:
    """The folders and files at one path of a source (for local sources, no path lists the allowed roots), with the
    recordings each file already is."""
    _source(db, sid)
    try:
        entries = sources.browse(db, cfg, sid, path)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    have = sources.imported(db, sid, [e["path"] for e in entries if not e["dir"]])
    return [BrowseEntry(**e, imported=have.get(e["path"], [])) for e in entries]


@router.get("/watches")
def list_watches(user: CurrentUser, acl: Acl, db: Db) -> list[Watch]:
    """Watched folders: all of them for admins, else those feeding namespaces you own."""
    roles = acl.roles
    return sources.list_watches(db, None if user.admin else {s for s in roles if auth.allows(roles, s, "owner")})


@router.post("/watches")
def create_watch(body: WatchCreate, user: AdminWriter, db: Db, cfg: Cfg) -> Created:
    """Watch a folder of a source; new files there are imported into the namespace (which is created if needed)."""
    opts = body.model_dump(exclude_unset=True, exclude={"source", "path", "namespace"})
    try:
        sources.get(db, body.source)
    except KeyError:
        raise HTTPException(400, "no such source") from None
    try:
        wid = sources.create_watch(db, cfg, body.source, body.path, store.ns_id(db, body.namespace), user.email, **opts)
    except (ValueError, SystemExit) as e:  # ns_id exits on a malformed namespace name
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "watch.create", f"watch_path:{wid}")
    return Created(id=wid)


@router.post("/watches/preview")
def preview_watch(body: WatchPreviewRequest, user: AdminWriter, db: Db, cfg: Cfg) -> WatchPreview:
    """How many files a watched folder would pick up (the backfill count), before creating it."""
    try:
        files = sources.list_files(db, cfg, sources.get(db, body.source), body.path)
    except KeyError:
        raise HTTPException(400, "no such source") from None
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    kinds = Counter(sources.kind_of(cfg, body.model_dump(exclude_unset=True, include={"kinds", "include", "exclude"}), f) for f in files)
    return WatchPreview(
        files=sum(n for k, n in kinds.items() if k),
        audio=kinds["audio"],
        transcripts=kinds["transcript"],
        documents=kinds["document"],
        images=kinds["image"],
    )


@router.patch("/watches/{wid}")
def update_watch(wid: int, body: WatchUpdate, user: AdminWriter, db: Db) -> Ok:
    _watch(db, wid)
    try:
        sources.update_watch(db, wid, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Ok()


@router.delete("/watches/{wid}")
def delete_watch(wid: int, user: AdminWriter, db: Db) -> Ok:
    sources.remove_watch(db, wid)
    auth.audit(db, user.as_audit(), "watch.delete", f"watch_path:{wid}")
    return Ok()


def _scan(request: Request, wid: int) -> None:
    try:
        sources.poll_watch(request.app.state.db, request.app.state.settings.current(), wid, log=log.info)
    except Exception:  # noqa: BLE001 - runs after the response; log it
        log.exception("scanning watched folder %s failed", wid)


@router.post("/watches/{wid}/scan", status_code=202)
def scan_watch(wid: int, user: AdminWriter, request: Request, db: Db, tasks: BackgroundTasks) -> Started:
    """Look for new files now instead of waiting for the next poll."""
    _watch(db, wid)
    tasks.add_task(_scan, request, wid)
    return Started(status="scanning")
