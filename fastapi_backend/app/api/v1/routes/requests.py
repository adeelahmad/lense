"""Requests for access to recordings (docs/access.md), across the namespaces you own."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Acl, CurrentUser, Db
from app.domain import access as acc
from app.schemas.recordings import AccessRequest

router = APIRouter(prefix="/access-requests", tags=["access requests"])


@router.get("")
def list_pending_access_requests(acl: Acl, user: CurrentUser, db: Db) -> list[AccessRequest]:
    """Requests waiting for an answer, for recordings in the namespaces you own (all of them for admins), newest
    first. Each is answered in its recording's access settings."""
    owned = {sid for sid, role in acl.roles.items() if role == "owner"}
    return [AccessRequest.model_validate(x) for x in acc.requests(db, spaces=owned, status="pending")] if owned else []
