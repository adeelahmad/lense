"""New releases, upgrading from the web app, and the database backups (admins only; docs/updates.md)."""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, domain_errors
from app.domain import auth, backups, monitoring, updates
from app.schemas.backups import BackupSchedule, RestoreWork
from app.schemas.common import Ok
from app.schemas.updates import Backup, Backups, Monitoring, MonitoringRequest, UpdateStatus, UpgradeRequest

router = APIRouter(tags=["updates"])


@router.get("/admin/updates")
def get_updates(user: AdminReader, db: Db, cfg: Cfg) -> UpdateStatus:
    """The version running, the latest release, and how the last upgrade went."""
    return UpdateStatus.model_validate(updates.view(db, cfg))


@router.post("/admin/updates/check")
def check_updates(user: AdminWriter, db: Db, cfg: Cfg) -> UpdateStatus:
    """Ask GitHub for the latest release now."""
    try:
        updates.check(db, cfg)
        updates.announce(db, cfg)
    except updates.UpdateError as e:
        raise HTTPException(400, str(e)) from None
    return UpdateStatus.model_validate(updates.view(db, cfg))


@router.post("/admin/updates/upgrade", status_code=202)
def start_upgrade(user: AdminWriter, db: Db, cfg: Cfg, body: UpgradeRequest | None = None) -> UpdateStatus:
    """Back the database up, then have this machine install the release (the latest unless one is named). Lens
    restarts on the new version; GET /admin/updates follows along."""
    body = body or UpgradeRequest()
    try:
        run = updates.upgrade(db, cfg, body.ref, by=user.email, backup=body.backup)
    except updates.UpdateError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "lens.upgrade", None, {"ref": (run or {}).get("ref"), "backup": body.backup})
    return UpdateStatus.model_validate(updates.view(db, cfg))


@router.get("/admin/monitoring")
def get_monitoring(user: AdminReader, db: Db) -> Monitoring:
    """Whether Prometheus, node-exporter and Grafana run next to Lens (the monitoring profile), and how the machine's
    last change went."""
    return Monitoring.model_validate(monitoring.view(db))


@router.put("/admin/monitoring", status_code=202)
def set_monitoring(body: MonitoringRequest, user: AdminWriter, request: Request, db: Db, cfg: Cfg) -> Monitoring:
    """Have this machine start or stop Prometheus, node-exporter and Grafana. On also turns /metrics on with a scrape
    token when it has none. GET /admin/monitoring follows along."""
    try:
        got = monitoring.turn(db, request.app.state.archive.base, cfg, body.on, by=user.email)
    except monitoring.MonitoringError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "lens.monitoring", None, {"on": body.on})
    return Monitoring.model_validate(got)


@router.get("/admin/backups")
def list_backups(user: AdminReader, db: Db, cfg: Cfg) -> Backups:
    """The database backups in <data_dir>/backups, newest first; those being written; the restore running here (or
    the last one); and the backup schedule."""
    b, st = cfg["backups"], backups.schedule_state(db)
    scheme = db.url.split(":", 1)[0]
    return Backups(
        folder=str(backups.folder(db)),
        backups=[Backup(**x) for x in backups.listing(db)],
        database="memory" if scheme in ("mem", "memory") else "embedded" if db.embedded else "server",
        in_progress=backups.in_progress(db),
        restore=RestoreWork(**backups.work),
        schedule=BackupSchedule(
            schedule=b.get("schedule"),
            timezone=b.get("timezone") or "UTC",
            keep=b.get("keep") or 7,
            next_at=st.get("at") if st.get("plan") == backups.plan(cfg) else backups.next_at(cfg),
            last_at=st.get("last_at"),
            last_name=st.get("last_name"),
            last_error=st.get("last_error"),
        ),
    )


@router.post("/admin/backups", status_code=201)
def take_backup(user: AdminWriter, db: Db, cfg: Cfg) -> Backup:
    """Back the database up now (it can take a while on a big archive). With backups.connection set, it is then
    copied there in the background (GET /admin/backups/remote follows it)."""
    got = backups.take(db)
    if got is None:
        raise HTTPException(400, "an in-memory database can't be backed up")
    auth.audit(db, user.as_audit(), "backup.create", None, {"name": got["name"]})
    if backups.remote(cfg):
        with contextlib.suppress(backups.Busy):  # another copy is running: this one waits for the next backup
            backups.start_copy(db, cfg, "send", got["name"])
    return Backup(**got)


@router.get("/admin/backups/{name}", response_class=FileResponse, responses={200: {"content": {"application/gzip": {}}}})
def download_backup(name: str, user: AdminReader, db: Db) -> FileResponse:
    """Download a backup (exports only: an embedded database's folder is copied from the data folder)."""
    with domain_errors():
        p = backups.path(db, name)
    if p.is_dir():
        raise HTTPException(400, "this backup is a folder: copy it from the data folder on the machine")
    auth.audit(db, user.as_audit(), "backup.download", None, {"name": name})
    return FileResponse(p, media_type="application/gzip", filename=name)


@router.delete("/admin/backups/{name}")
def delete_backup(name: str, user: AdminWriter, db: Db) -> Ok:
    """Delete a backup."""
    with domain_errors():
        backups.delete(db, name)
    auth.audit(db, user.as_audit(), "backup.delete", None, {"name": name})
    return Ok()
