from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class NamespaceIntegrations(ResponseModel):
    mcp: bool = Field(description="the namespace's own MCP server answers, seeing this namespace alone")
    mcp_url: str = Field(description="where MCP clients connect to it")
    updated_at: str | None = None
    updated_by: str | None = None


class NamespaceIntegrationsUpdate(RequestModel):
    """Only the fields you send change."""

    mcp: bool | None = None
