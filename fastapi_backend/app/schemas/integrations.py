from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class IntegrationTool(ResponseModel):
    name: str
    description: str


class NamespaceIntegrations(ResponseModel):
    mcp: bool = Field(description="the namespace's own MCP server answers, seeing this namespace alone")
    mcp_url: str = Field(description="where MCP clients connect to it")
    chat: bool = Field(False, description="the namespace's own chat completions answer apps, from this namespace alone")
    chat_url: str = Field("", description="the base URL OpenAI-compatible apps are given (they add /chat/completions)")
    tools: list[str] = Field(default_factory=list, description="the read tools the model may use there (none: one search)")
    tool_choices: list[IntegrationTool] = Field(default_factory=list, description="the tools that can be offered there")
    updated_at: str | None = None
    updated_by: str | None = None


class NamespaceIntegrationsUpdate(RequestModel):
    """Only the fields you send change."""

    mcp: bool | None = None
    chat: bool | None = None
    tools: list[str] | None = Field(None, max_length=50)
