"""Custom nodes: bodies of nodes saved under a name, kept private, shared with namespaces or with everyone."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, custom_nodes
from app.schemas.common import Created, Ok
from app.schemas.custom_nodes import CustomNode, CustomNodeCreate, CustomNodeUpdate, CustomNodeVersionCreate
from app.schemas.templates import VersionSaved

router = APIRouter(prefix="/custom-nodes", tags=["workflows"])


def me(user: Principal) -> dict:
    return custom_nodes.who(user.id, user.email, user.admin and user.via != "oauth", user.roles)


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    with domain_errors():
        try:
            yield
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None


@router.get("")
def list_custom_nodes(user: CurrentUser, db: Db, scope: str | None = None) -> list[CustomNode]:
    """The custom nodes you can use: yours, the ones shared with your namespaces or with everyone (admins: all)."""
    return custom_nodes.visible(db, me(user), scope)


@router.post("")
def create_custom_node(body: CustomNodeCreate, user: Writer, db: Db) -> Created:
    with _errors():
        nid = custom_nodes.create(
            db, me(user), body.name, body.graph.model_dump(), body.params, body.description, body.icon, body.color,
            body.visibility, body.namespaces,
        )  # fmt: skip
    auth.audit(db, user.as_audit(), "custom_node.create", f"custom_node:{nid}")
    return Created(id=nid)


@router.get("/{nid}")
def get_custom_node(nid: int, user: CurrentUser, db: Db, version: int | None = None) -> CustomNode:
    """One version (default: the current one) and the list of versions."""
    with _errors():
        return custom_nodes.visible_one(db, me(user), nid, version)


@router.post("/{nid}/versions")
def create_custom_node_version(nid: int, body: CustomNodeVersionCreate, user: Writer, db: Db) -> VersionSaved:
    with _errors():
        n = custom_nodes.save_version(db, me(user), nid, body.graph.model_dump(), body.params, body.notes)
    auth.audit(db, user.as_audit(), "custom_node.save", f"custom_node:{nid}", {"version": n})
    return VersionSaved(version=n)


@router.patch("/{nid}")
def update_custom_node(nid: int, body: CustomNodeUpdate, user: Writer, db: Db) -> Ok:
    """Its name, look, and who sees it."""
    with _errors():
        custom_nodes.update(db, me(user), nid, **body.model_dump(exclude_unset=True))
    auth.audit(db, user.as_audit(), "custom_node.update", f"custom_node:{nid}")
    return Ok()


@router.delete("/{nid}")
def delete_custom_node(nid: int, user: Writer, db: Db) -> Ok:
    """Takes it off the palette; workflows that use it keep running the version they pinned."""
    with _errors():
        custom_nodes.remove(db, me(user), nid)
    auth.audit(db, user.as_audit(), "custom_node.delete", f"custom_node:{nid}")
    return Ok()
