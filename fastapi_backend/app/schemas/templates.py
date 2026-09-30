"""Templates: Jinja text rendered against a recording, as an LLM prompt, a report page or an export file. Versioned."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import Ok, RequestModel, ResponseModel

TemplateKind = Literal["prompt", "report", "export"]

# "schema" shadows a BaseModel attribute, so these models keep it as `json_schema` and use the alias on the wire.


class TemplateSummary(ResponseModel):
    id: int
    name: str
    kind: TemplateKind
    description: str | None = None
    current: int
    updated_at: str | None = None
    versions: int = 0


class TemplateVersionInfo(ResponseModel):
    version: int
    notes: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Template(ResponseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    id: int
    name: str
    kind: TemplateKind
    description: str | None = None
    current: int
    created_at: str | None = None
    updated_at: str | None = None
    version: int
    body: str
    json_schema: dict[str, Any] | None = Field(
        default=None, alias="schema", description="JSON schema the model's answer must match (prompts)"
    )
    system: str | None = None
    notes: str | None = None
    created_by: str | None = None
    history: list[TemplateVersionInfo] = []


class TemplateCreate(RequestModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    kind: TemplateKind
    body: str = ""
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    system: str | None = None
    description: str | None = None


class TemplateVersionCreate(RequestModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    body: str = ""
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    system: str | None = None
    notes: str | None = None
    publish: bool = Field(default=True, description="make this the current version")


class VersionSaved(Ok):
    version: int


class TemplatePreviewRequest(RequestModel):
    """A saved template (`template`, optional `version`), or the unsaved text in the editor (`body` and friends)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    recording: int
    template: int | None = None
    version: int | None = None
    kind: TemplateKind | None = None
    body: str | None = None
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    system: str | None = None
    filename: str | None = None
    run: bool = Field(default=False, description="also call the model (prompts only)")


class TemplatePreview(ResponseModel):
    ok: bool
    kind: TemplateKind | None = None
    rendered: str | None = None
    result: Any = None
    error: str | None = None
