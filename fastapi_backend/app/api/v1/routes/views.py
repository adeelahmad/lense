"""Saved views of the Library: yours, and the ones shared with namespaces you can read (docs/api.md#views).

Saved searches (routes/searches.py) are the same kind of record and follow the same rules; the helpers here serve both.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, store, views
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.views import SavedView, ViewCreate, ViewState, ViewUpdate

router = APIRouter(prefix="/views", tags=["views"])


def can_delete(v: dict[str, Any], user: Principal, roles: dict[int, str]) -> bool:
    """Its maker, or for a shared one an owner of its namespace."""
    return v["account"] == user.id or (bool(v["shared"]) and v.get("space") is not None and auth.allows(roles, v["space"], "owner"))


def common(db: DB, rows: list[dict[str, Any]], user: Principal, roles: dict[int, str]) -> list[dict[str, Any]]:
    """What every saved view says about itself: name, namespace, sharing, maker, times, and what you may do with it."""
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
    spaces = store.space_names(db) if rows else {}
    return [
        {
            "id": v["id"],
            "name": v["name"],
            "namespace": spaces.get(v.get("space")) if v.get("space") is not None else None,
            "shared": bool(v["shared"]),
            "state": v.get("state") or {},
            "created_by": people.get(v["account"]),
            "created_at": v.get("created_at"),
            "updated_at": v.get("updated_at"),
            "mine": v["account"] == user.id,
            "can_delete": can_delete(v, user, roles),
        }
        for v in rows
    ]


def visible(db: DB, vid: int, user: Principal, roles: dict[int, str], kind: str) -> dict[str, Any]:
    """One this person can see: theirs (unless it shows a namespace they can no longer read) or shared with a
    namespace they can read; else 404."""
    try:
        v = views.get(db, vid, kind)
    except KeyError:
        raise HTTPException(404, "not found") from None
    space = v.get("space")
    if (v["account"] == user.id and (space is None or space in roles)) or (v["shared"] and space in roles):
        return v
    raise HTTPException(404, "not found")


def check_share(acl: Access, space: int | None, what: str) -> None:
    """Sharing is with a namespace, by its editors."""
    if space is None:
        raise HTTPException(400, f"a {what} is shared with its namespace: one of every namespace can't be shared")
    acl.need(space, "editor")


def change(
    db: DB, acl: Access, user: Principal, vid: int, kind: str, name: str | None, shared: bool | None, state: dict[str, Any] | None
) -> None:
    """Rename, share or unshare, or replace what it shows: its maker only. Sharing changes are audited."""
    v = visible(db, vid, user, acl.roles, kind)
    what = views.KINDS[kind]
    if v["account"] != user.id:
        raise HTTPException(403, f"only its maker can change a {what}")
    if shared and not v["shared"]:
        check_share(acl, v.get("space"), what)
    with domain_errors():
        views.update(db, vid, user.id, name, shared, state)
    if shared is not None and shared != bool(v["shared"]):
        prefix = "search" if kind == "search" else "view"
        action = f"{prefix}.share" if shared else f"{prefix}.unshare"
        auth.audit(db, user.as_audit(), action, f"{prefix}:{vid}", {"name": name or v["name"]})


def remove(db: DB, acl: Access, user: Principal, vid: int, kind: str) -> None:
    """Delete it: its maker, or for a shared one an owner of its namespace. Deleting a shared one is audited."""
    v = visible(db, vid, user, acl.roles, kind)
    if not can_delete(v, user, acl.roles):
        raise HTTPException(403, "only its maker or an owner of its namespace can delete it")
    views.delete(db, vid)
    if v["shared"]:
        prefix = "search" if kind == "search" else "view"
        auth.audit(db, user.as_audit(), f"{prefix}.delete", f"{prefix}:{vid}", {"name": v["name"], "maker": v["account"] == user.id})


def _out(db: DB, rows: list[dict[str, Any]], user: Principal, roles: dict[int, str]) -> list[SavedView]:
    return [SavedView(**{**c, "state": ViewState(**c["state"])}) for c in common(db, rows, user, roles)]


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
        check_share(acl, sid, "view")
    with domain_errors():
        vid = views.create(db, user.id, body.name, sid, body.state.model_dump(), body.shared)
    if body.shared:
        auth.audit(db, user.as_audit(), "view.share", f"view:{vid}", {"name": body.name, "namespace": body.namespace})
    return _out(db, [views.get(db, vid)], user, acl.roles)[0]


@router.patch("/{vid}")
def update_view(vid: int, body: ViewUpdate, user: Writer, acl: Acl, db: Db) -> SavedView:
    """Rename it, share or unshare it, or save what the Library shows into it. Its maker only; sharing needs editor
    access to its namespace, and a view of every namespace can't be shared."""
    change(db, acl, user, vid, "library", body.name, body.shared, body.state.model_dump() if body.state else None)
    return _out(db, [views.get(db, vid)], user, acl.roles)[0]


@router.delete("/{vid}")
def delete_view(vid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: its maker, or for a shared view an owner of its namespace. Deleting a shared view is audited."""
    remove(db, acl, user, vid, "library")
    return Ok()
