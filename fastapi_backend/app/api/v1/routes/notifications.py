"""A namespace's notifications (docs/notifications.md): owners add webhooks and Matterbridge gateways, choose the
events each gets, send a test and read what was sent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, notify
from app.schemas.common import Ok
from app.schemas.notifications import (
    EventInfo,
    NotifyDelivery,
    NotifySecret,
    NotifyTarget,
    NotifyTargetCreate,
    NotifyTargetCreated,
    NotifyTargets,
    NotifyTargetUpdate,
    NotifyTestResult,
)

router = APIRouter(prefix="/namespaces/{name}/notifications", tags=["notifications"])


def _detail(t: dict[str, Any]) -> dict[str, Any]:
    return {"id": t["id"], "name": t["name"], "kind": t["kind"], "url": t.get("url_hint"), "events": t["events"], "enabled": t["enabled"]}


@router.get("")
def list_notify_targets(name: str, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg) -> NotifyTargets:
    """The namespace's notification targets (owners), and the events they can get."""
    sid = acl.namespace(name, "owner")
    return NotifyTargets(
        events=[EventInfo(type=k, label=v) for k, v in notify.EVENTS.items()],
        targets=[NotifyTarget(**notify.view(t)) for t in notify.targets(db, sid)],
        enabled=bool((cfg.get("notifications") or {}).get("enabled", True)),
    )


@router.post("")
def create_notify_target(name: str, body: NotifyTargetCreate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> NotifyTargetCreated:
    """Add a target (owners). A webhook comes back with its signing secret, this once."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        t, secret = notify.create(
            db, cfg, sid, body.name, body.kind, body.url, body.events, body.gateway, body.username, body.token, body.enabled, user.email
        )
    auth.audit(db, user.as_audit(), "namespace.notification.create", f"space:{sid}", _detail(t))
    return NotifyTargetCreated(target=NotifyTarget(**notify.view(t)), secret=secret)


@router.patch("/{tid}")
def update_notify_target(name: str, tid: int, body: NotifyTargetUpdate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> NotifyTarget:
    """Change a target (owners): its name, address, events, whether it's on, and Matterbridge's gateway, name and token."""
    sid = acl.namespace(name, "owner")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "send something to change")
    with domain_errors():
        t = notify.update(db, cfg, tid, sid, changes, user.email)
    auth.audit(db, user.as_audit(), "namespace.notification.update", f"space:{sid}", {**_detail(t), "changed": sorted(changes)})
    return NotifyTarget(**notify.view(t))


@router.delete("/{tid}")
def delete_notify_target(name: str, tid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete a target and what it was sent (owners)."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        t = notify.delete(db, tid, sid)
    auth.audit(db, user.as_audit(), "namespace.notification.delete", f"space:{sid}", _detail(t))
    return Ok()


@router.post("/{tid}/test")
def test_notify_target(name: str, tid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> NotifyTestResult:
    """Send the target a test message now (owners) and say how it went; it shows in the target's deliveries too."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        return NotifyTestResult(**notify.test(db, cfg, tid, sid, user.email))


@router.post("/{tid}/secret")
def rotate_notify_secret(name: str, tid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> NotifySecret:
    """A webhook's new signing secret (owners), shown this once; the old one stops working at once."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        secret = notify.rotate_secret(db, cfg, tid, sid, user.email)
    auth.audit(db, user.as_audit(), "namespace.notification.secret", f"space:{sid}", {"id": tid})
    return NotifySecret(secret=secret)


@router.get("/{tid}/deliveries")
def list_notify_deliveries(name: str, tid: int, acl: Acl, user: CurrentUser, db: Db) -> list[NotifyDelivery]:
    """What the target was sent lately, newest first (owners): up to 50, kept for 30 days."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        notify.get(db, tid, sid)
    return [NotifyDelivery(**d) for d in notify.deliveries(db, tid)]
