"""Extensions: tools, skills, hooks and plugins added to the assistant, as code, on the canvas or from chat."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Kind = Literal["tool", "skill", "hook", "plugin"]
Visibility = Literal["private", "namespace", "everyone"]
Origin = Literal["code", "canvas", "chat"]


class ExtensionVersionInfo(ResponseModel):
    version: int
    notes: str | None = None
    origin: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Extension(ResponseModel):
    id: int
    name: str = Field(description="what the assistant calls it: its tool or skill name")
    kind: Kind
    title: str | None = None
    description: str | None = None
    visibility: Visibility = "private"
    namespaces: list[str] = Field(default_factory=list, description="who sees it, when shared with namespaces")
    enabled: bool = Field(description="whether it's switched on in conversations")
    owner_email: str | None = None
    editable: bool = False
    current: int
    version: int
    spec: dict[str, Any] = Field(description="the tool, skill, hook or plugin itself, as its manifest has it")
    origin: str | None = Field(None, description="how this version was made: code, canvas or chat")
    updated_at: str | None = None


class ExtensionDetail(Extension):
    manifest: str = Field(description="the extension as a manifest to read, change and import again")
    history: list[ExtensionVersionInfo] = []


class ExtensionCreate(RequestModel):
    manifest: dict[str, Any] | None = Field(None, description="{name, kind, description, ...settings}")
    text: str | None = Field(None, description="the manifest as code: YAML, JSON, or Markdown with frontmatter")
    enabled: bool = True
    origin: Origin = "code"


class ExtensionVersionCreate(RequestModel):
    manifest: dict[str, Any] | None = None
    text: str | None = None
    notes: str | None = None
    origin: Origin = "code"


class ExtensionUpdate(RequestModel):
    title: str | None = None
    description: str | None = None
    enabled: bool | None = None
    visibility: Visibility | None = None
    namespaces: list[str] | None = None


class ManifestCheck(RequestModel):
    text: str


class ManifestChecked(ResponseModel):
    manifest: dict[str, Any] = Field(description="the manifest, read and checked")


class ExtensionTest(RequestModel):
    tool: str | None = Field(None, description="which of a plugin's tools (a tool extension: itself)")
    args: dict[str, Any] = {}
    confirm: bool = Field(False, description="needed to try a tool that changes something: it really runs")


class ExtensionTestResult(ResponseModel):
    output: Any = None
