"""The copy of the archive in Fedora (domain/fedora.py, docs/fedora.md): how it stands, and sending everything now.
Admins only; the connection itself is set in Settings (the `fedora` section)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from app.api.deps import AdminReader, AdminWriter, Cfg, Db
from app.domain import auth, fedora
from app.schemas.common import ResponseModel

router = APIRouter(prefix="/admin/fedora", tags=["fedora"])


class FedoraStatus(ResponseModel):
    enabled: bool
    url: str | None = None
    root: str
    pending: int
    resources: int
    last_sync: str | None = None
    last_full: str | None = None
    last_counts: dict[str, Any] | None = None
    last_error: str | None = None
    full_requested: bool


class FedoraSyncResult(ResponseModel):
    sent: int
    files: int
    unchanged: int
    deleted: int
    failed: int
    errors: list[str]


@router.get("")
def get_fedora_status(user: AdminReader, db: Db, cfg: Cfg) -> FedoraStatus:
    return FedoraStatus.model_validate(fedora.summary(db, cfg))


@router.post("/sync")
async def sync_fedora(user: AdminWriter, db: Db, cfg: Cfg) -> FedoraSyncResult:
    """Compare everything with Fedora now and send what differs (the background sync does this every fedora.full_hours)."""
    if not fedora.enabled(cfg):
        raise HTTPException(409, "Fedora isn't set up: set fedora.url and turn it on in Settings first")
    out = await run_in_threadpool(fedora.sync, db, cfg)
    auth.audit(db, user.as_audit(), "fedora.sync", None, {k: v for k, v in out.items() if k != "errors"})
    return FedoraSyncResult.model_validate(out)
