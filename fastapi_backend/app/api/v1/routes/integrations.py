"""A namespace's own ways in for other apps (integrations.py): owners turn on its MCP server and its chat completions,
which see that namespace alone, and pick the tools the model may use there; anyone who can read the namespace sees
what is on and where it is."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Db, Writer
from app.api.v1.routes.oauth import MCP, web_app_base
from app.domain import auth, integrations
from app.schemas.integrations import IntegrationTool, NamespaceIntegrations, NamespaceIntegrationsUpdate

router = APIRouter(prefix="/namespaces", tags=["namespaces"])


def _view(request: Request, db: Db, sid: int, name: str) -> NamespaceIntegrations:
    web = web_app_base(request)
    return NamespaceIntegrations(
        **integrations.profile(db, sid),
        mcp_url=f"{web}{MCP}/{name}",
        chat_url=f"{web}/api/v1/namespaces/{name}",
        tool_choices=[IntegrationTool(name=n, description=d) for n, d in integrations.tool_choices()],
    )


@router.get("/{name}/integrations")
def get_namespace_integrations(name: str, request: Request, acl: Acl, db: Db) -> NamespaceIntegrations:
    """Whether the namespace's own MCP server and chat completions are on, their addresses, and the tools offered."""
    return _view(request, db, acl.namespace(name), name)


@router.patch("/{name}/integrations")
def update_namespace_integrations(
    name: str, body: NamespaceIntegrationsUpdate, request: Request, acl: Acl, user: Writer, db: Db
) -> NamespaceIntegrations:
    """Owners: turn the namespace's own MCP server or chat completions on or off, or pick the read tools the model may
    use in its chat completions. Off, each answers 403; apps keep their access to the rest of Lens as before."""
    sid = acl.namespace(name, "manage")
    try:
        integrations.save(db, sid, body.mcp, body.chat, body.tools, user.email)
    except KeyError:
        raise HTTPException(404, "not found") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "namespace.integrations", name, body.model_dump(exclude_unset=True))
    return _view(request, db, sid, name)
