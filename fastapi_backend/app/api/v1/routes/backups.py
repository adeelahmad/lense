"""Restoring a backup, downloading one by link, and what the schedule and a restore are doing (app/domain/backups.py,
docs/database.md#backups; admins only). Listing, taking and deleting backups are in routes/updates.py.

Restoring (which replaces the database) and download links (a backup holds everything in it) need an admin signed in
with a passkey. A link works for LINK_SECONDS, so the browser saves the file itself however big it is; an embedded
database's folder comes as a .tar.gz.
"""

from __future__ import annotations

import tarfile
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, domain_errors
from app.core.security import sign_path, verify_path
from app.domain import auth, backups
from app.schemas.backups import BackupLink, CopyWork, RestoreStart
from app.schemas.common import Ok
from app.schemas.updates import Backup, RemoteBackups

router = APIRouter(prefix="/admin/backups", tags=["updates"])
LINK_SECONDS = 120


@router.get("/remote")
def remote_backups(user: AdminReader, db: Db, cfg: Cfg) -> RemoteBackups:
    """The copies on backups.connection, how copying the last backup there went, and a copy running now."""
    where, st = backups.remote(cfg), backups.remote_state(db)
    out = RemoteBackups(
        connection=where and where["connection"],
        folder=where and where["folder"],
        last_at=st.get("at"),
        last_name=st.get("name"),
        last_error=st.get("error"),
        copying=CopyWork(**backups.copying),
    )
    if where:
        try:
            out.backups = [Backup(**b) for b in backups.remote_listing(db, cfg)]
        except (RuntimeError, ValueError, OSError) as e:
            out.error = str(e)[:300] or type(e).__name__
    return out


def _copy(db, cfg, what, name):
    if not backups.remote(cfg):
        raise HTTPException(400, "choose a storage connection for backups first")
    try:
        backups.start_copy(db, cfg, what, name)
    except backups.Busy as e:
        raise HTTPException(409, str(e)) from None


@router.post("/{name}/send", status_code=202)
def send_backup(name: str, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Copy this backup to backups.connection now, in the background."""
    with domain_errors():
        backups.path(db, name)
    _copy(db, cfg, "send", name)
    auth.audit(db, user.as_audit(), "backup.send", None, {"name": name})
    return Ok()


@router.post("/{name}/fetch", status_code=202)
def fetch_backup(name: str, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Bring the copy of this backup back from backups.connection, in the background, to restore or download it."""
    if not backups.NAME.match(name):
        raise HTTPException(400, "no such backup")
    _copy(db, cfg, "fetch", name)
    auth.audit(db, user.as_audit(), "backup.fetch", None, {"name": name})
    return Ok()


@router.delete("/{name}/remote")
def delete_remote_backup(name: str, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Delete the copy of this backup on backups.connection."""
    try:
        backups.remote_delete(db, cfg, name)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "backup.delete_remote", None, {"name": name})
    return Ok()


def _signed_in(user) -> None:
    if user.via != "access":
        raise HTTPException(403, "sign in with a passkey for this; API tokens and apps can't")


@router.post("/{name}/restore", status_code=202)
def restore_backup(name: str, body: RestoreStart, user: AdminWriter, request: Request, db: Db) -> Ok:
    """Replace the whole database with this backup, in the background (GET /admin/backups follows it). What it held
    is backed up first (before-restore), and put back if the restore fails. Sign-ins made since the backup end."""
    _signed_in(user)
    if body.confirm != name:
        raise HTTPException(400, "confirm with the backup's name")
    with domain_errors():
        backups.usable(db, name)
    if backups.in_progress(db):
        raise HTTPException(409, "a backup is being written; wait for it to finish")
    cfg = request.app.state.archive.current()
    who = user.as_audit()
    auth.audit(db, who, "backup.restore", None, {"name": name})
    try:
        # the audit log was replaced too: the restore is recorded again in the restored one
        backups.start_restore(
            db, cfg, name, lambda out: auth.audit(db, who, "backup.restored", None, {"name": name, "before": out and out["name"]})
        )
    except backups.Busy as e:
        raise HTTPException(409, str(e)) from None
    return Ok()


@router.post("/{name}/link")
def backup_link(name: str, user: AdminReader, db: Db) -> BackupLink:
    """A link that downloads the backup for the next couple of minutes, for the browser to open."""
    _signed_in(user)
    with domain_errors():
        backups.path(db, name)
    auth.audit(db, user.as_audit(), "backup.download", None, {"name": name})
    return BackupLink(url=sign_path(f"/api/v1/admin/backups/{name}/file", ttl=LINK_SECONDS), expires_in=LINK_SECONDS)


def _tar(folder) -> Iterator[bytes]:
    """A folder as a .tar.gz, written as it is read."""

    class Sink:
        def __init__(self):
            self.parts: list[bytes] = []

        def write(self, b):
            self.parts.append(bytes(b))
            return len(b)

        def take(self):
            out, self.parts = b"".join(self.parts), []
            return out

    sink = Sink()
    with tarfile.open(fileobj=sink, mode="w|gz") as tar:  # type: ignore[call-overload]
        for p in sorted(folder.rglob("*")):
            tar.add(p, arcname=f"{folder.name}/{p.relative_to(folder)}", recursive=False)
            if chunk := sink.take():
                yield chunk
    yield sink.take()


@router.get(
    "/{name}/file",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/gzip": {}}, "description": "the backup (an embedded one as a .tar.gz)"}},
)
def backup_file(name: str, db: Db, exp: str | None = None, sig: str | None = None):
    """The backup itself, through a link from POST /admin/backups/{name}/link."""
    if not verify_path(f"/api/v1/admin/backups/{name}/file", exp, sig):
        raise HTTPException(403, "this download link has expired; download the backup again from Settings")
    with domain_errors():
        p = backups.path(db, name)
    if p.is_dir():
        return StreamingResponse(
            _tar(p), media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{name}.tar.gz"'}
        )
    return FileResponse(p, media_type="application/gzip", filename=name)
