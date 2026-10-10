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

from app.api.deps import AdminReader, AdminWriter, Db, domain_errors
from app.core.security import sign_path, verify_path
from app.domain import auth, backups
from app.schemas.backups import BackupLink, RestoreStart
from app.schemas.common import Ok

router = APIRouter(prefix="/admin/backups", tags=["updates"])
LINK_SECONDS = 120


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
