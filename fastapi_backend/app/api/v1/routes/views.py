"""Saved views of the Library: yours, and the ones shared with namespaces you can read (docs/api.md#views)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, store, views
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.views import SavedView, ViewCreate, ViewState, ViewUpdate

router = APIRouter(prefix="/views", tags=["views"])


def _can_delete(v: dict[str, Any], user: Principal, roles: dict[int, str]) -> bool:
    return v["account"] == user.id or (bool(v["shared"]) and v.get("space") is not None and auth.allows(roles, v["space"], "owner"))


def _out(db: DB, rows: list[dict[str, Any]], user: Principal, roles: dict[int, str]) -> list[SavedView]:
    people = (
        {
            a["id"]: a["email"]
            for a in db.rows(
                "SELECT record::id(id) AS id, email FROM account WHERE id IN $ids",
                ids=[store.R("account", a) for a in {v["account"] for v in rows}],
            )
        }
        if rows
        else {}
    )
    spaces = {s["id"]: s["name"] for s in db.rows("SELECT record::id(id) AS id, name FROM space")} if rows else {}
    return [
        SavedView(
            id=v["id"],
            name=v["name"],
            namespace=spaces.get(v.get("space")) if v.get("space") is not None else None,
            shared=bool(v["shared"]),
            state=ViewState(**(v.get("state") or {})),
            created_by=people.get(v["account"]),
            created_at=v.get("created_at"),
            updated_at=v.get("updated_at"),
            mine=v["account"] == user.id,
            can_delete=_can_delete(v, user, roles),
        )
        for v in rows
    ]


def _visible(db: DB, vid: int, user: Principal, roles: dict[int, str]) -> dict[str, Any]:
    """A view this person can see: theirs (unless it shows a namespace they can no longer read) or shared with a
    namespace they can read; else 404."""
    try:
        v = views.get(db, vid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    space = v.get("space")
    if (v["account"] == user.id and (space is None or space in roles)) or (v["shared"] and space in roles):
        return v
    raise HTTPException(404, "not found")


@router.get("")
def list_views(user: CurrentUser, acl: Acl, db: Db) -> list[SavedView]:
    """Your views, then the ones shared with namespaces you can read; the latest changed first in each."""
    return _out(db, views.visible(db, user.id, set(acl.roles)), user, acl.roles)


@router.post("")
def create_view(body: ViewCreate, user: Writer, acl: Acl, db: Db) -> SavedView:
    """Save what the Library shows under a name (unique among your views). Sharing it with its namespace needs
    editor access there."""
    sid = acl.namespace(body.namespace) if body.namespace else None
    if body.shared:
        if sid is None:
            raise HTTPException(400, "a view is shared with its namespace: pick one")
        acl.need(sid, "editor")
    with domain_errors():
        vid = views.create(db, user.id, body.name, sid, body.state.model_dump(), body.shared)
    if body.shared:
        auth.audit(db, user.as_audit(), "view.share", f"view:{vid}", {"name": body.name, "namespace": body.namespace})
    return _out(db, [views.get(db, vid)], user, acl.roles)[0]


@router.patch("/{vid}")
def update_view(vid: int, body: ViewUpdate, user: Writer, acl: Acl, db: Db) -> SavedView:
    """Rename it, share or unshare it, or save what the Library shows into it. Its maker only; sharing needs editor
    access to its namespace, and a view of every namespace can't be shared."""
    v = _visible(db, vid, user, acl.roles)
    if v["account"] != user.id:
        raise HTTPException(403, "only its maker can change a view")
    if body.shared and not v["shared"]:
        if v.get("space") is None:
            raise HTTPException(400, "a view of every namespace can't be shared: save one of a namespace")
        acl.need(v["space"], "editor")
    with domain_errors():
        views.update(db, vid, user.id, body.name, body.shared, body.state.model_dump() if body.state else None)
    if body.shared is not None and body.shared != bool(v["shared"]):
        action = "view.share" if body.shared else "view.unshare"
        auth.audit(db, user.as_audit(), action, f"view:{vid}", {"name": body.name or v["name"]})
    return _out(db, [views.get(db, vid)], user, acl.roles)[0]


@router.delete("/{vid}")
def delete_view(vid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: its maker, or for a shared view an owner of its namespace. Deleting a shared view is audited."""
    v = _visible(db, vid, user, acl.roles)
    if not _can_delete(v, user, acl.roles):
        raise HTTPException(403, "only its maker or an owner of its namespace can delete it")
    views.delete(db, vid)
    if v["shared"]:
        auth.audit(db, user.as_audit(), "view.delete", f"view:{vid}", {"name": v["name"], "maker": v["account"] == user.id})
    return Ok()
