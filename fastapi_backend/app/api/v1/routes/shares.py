"""File shares (docs/file-tree.md): whether the S3 and WebDAV servers are on, and people's access keys for them.

Admins turn the server on in the settings (`shares`). Anyone signed in makes keys for themselves, which act as them;
a key's secret is shown once. Making and revoking keys is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminReader, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, s3, shares
from app.schemas.common import Ok
from app.schemas.filetree import S3Key, S3KeyCreate, S3KeyCreated, S3Status, ShareServer

router = APIRouter(prefix="/shares", tags=["shares"])


@router.get("/s3")
def s3_status(user: CurrentUser, db: Db, cfg: Cfg) -> S3Status:
    """Whether the S3 server is on and on which port, what the worker running it says, the same for WebDAV, whether
    both are open at the web app's address too (/s3, /dav), and your access keys."""
    st = shares.status(db, cfg)
    keys = [S3Key(**k) for k in s3.keys_of(db, user.id)]
    return S3Status(**st["s3"], webdav=ShareServer(**st["webdav"]), remote=shares.remote(cfg), keys=keys)


@router.post("/s3/keys")
def create_s3_key(body: S3KeyCreate, user: Writer, db: Db, cfg: Cfg) -> S3KeyCreated:
    """An access key that reads the file tree as you, over S3 and WebDAV. Only while signed in to the web app (with your passkey),
    not with an API token. The secret is in this answer only."""
    if user.via != "access":
        raise HTTPException(403, "make access keys while signed in")
    with domain_errors():
        kid, secret = s3.create_key(db, cfg, user.id, body.name, body.days)
    auth.audit(db, user.as_audit(), "s3_key.create", f"file_key:{kid}")
    st = shares.status(db, cfg)
    return S3KeyCreated(id=kid, secret=secret, port=st["s3"]["port"], webdav_port=st["webdav"]["port"])


@router.delete("/s3/keys/{key_id}")
def revoke_s3_key(key_id: str, user: Writer, db: Db) -> Ok:
    """Revoke one of your keys (admins: anyone's). It stops working at once."""
    if s3.revoke_key(db, key_id, None if user.admin and user.via != "oauth" else user.id):
        auth.audit(db, user.as_audit(), "s3_key.revoke", f"file_key:{key_id}")
    return Ok()


@router.get("/s3/keys")
def all_s3_keys(user: AdminReader, db: Db) -> list[S3Key]:
    """Everyone's access keys, with their owners (admins)."""
    return [S3Key(**k) for k in s3.keys_of(db)]
