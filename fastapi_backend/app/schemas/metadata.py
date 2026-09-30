"""Descriptive metadata of recordings and namespaces (IIIF / Dublin Core / schema.org fields)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class RecordingMetadata(ResponseModel):
    meta: dict[str, Any] = Field(description="the effective metadata: stored values over derived defaults")
    stored: dict[str, Any]
    defaults: dict[str, Any]
    problems: list[dict[str, Any]]


class RecordingMetadataUpdate(RequestModel):
    set: dict[str, Any] = Field(default_factory=dict, description="fields to save; null clears one")
    reset: list[str] = Field(default_factory=list, description="fields to put back to their derived values")


class MetadataEdit(ResponseModel):
    id: int
    by: str | None = None
    at: str | None = None
    changed: list[str]
    before: dict[str, Any]
    after: dict[str, Any]


class NamespaceMetadata(ResponseModel):
    name: str | None = None
    meta: dict[str, Any]
    profile: dict[str, Any]


class NamespaceMetadataUpdate(RequestModel):
    meta: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None


class MetadataBulk(RequestModel):
    recordings: list[int] = Field(default_factory=list)
    namespace: str | None = Field(None, description="every recording in this namespace (added to `recordings`)")
    set: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)
    dry_run: bool = Field(True, description="report what would change without changing it")


class MetadataBulkResult(ResponseModel):
    recordings: int
    fields: list[str]
    would_change: int | None = None
    changed: int | None = None
