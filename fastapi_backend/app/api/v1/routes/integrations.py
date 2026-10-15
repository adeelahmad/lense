"""A namespace's own ways in for other apps (integrations.py): owners turn on its MCP server, which sees that
namespace alone; anyone who can read the namespace sees whether it's on and where it is."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Db, Writer
from app.api.v1.routes.oauth import MCP, web_app_base
from app.domain import auth, integrations
from app.schemas.integrations import NamespaceIntegrations, NamespaceIntegrationsUpdate

router = APIRouter(prefix="/namespaces", tags=["namespaces"])


def _view(request: Request, db: Db, sid: int, name: str) -> NamespaceIntegrations:
    return NamespaceIntegrations(**integrations.profile(db, sid), mcp_url=f"{web_app_base(request)}{MCP}/{name}")


@router.get("/{name}/integrations")
def get_namespace_integrations(name: str, request: Request, acl: Acl, db: Db) -> NamespaceIntegrations:
    """Whether the namespace's own MCP server is on, and its address."""
    return _view(request, db, acl.namespace(name), name)


@router.patch("/{name}/integrations")
def update_namespace_integrations(
    name: str, body: NamespaceIntegrationsUpdate, request: Request, acl: Acl, user: Writer, db: Db
) -> NamespaceIntegrations:
    """Owners: turn the namespace's own MCP server on or off. Off, it answers 403; apps keep their access to the rest of
    Lens as before."""
    sid = acl.namespace(name, "manage")
    try:
        integrations.save(db, sid, body.mcp, user.email)
    except KeyError:
        raise HTTPException(404, "not found") from None
    auth.audit(db, user.as_audit(), "namespace.integrations", name, body.model_dump(exclude_unset=True))
    return _view(request, db, sid, name)
