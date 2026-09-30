"""Pipelines: the steps run on a recording, versioned; a namespace can make one its default."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel

Step = str | dict[str, Any]


class PipelineSummary(ResponseModel):
    id: int
    name: str
    description: str | None = None
    current: int
    updated_at: str | None = None
    namespaces: list[str] = Field(default_factory=list, description="namespaces that use it by default")


class PipelineCatalog(ResponseModel):
    standard: list[str] = Field(description="the built-in pipeline")
    step_types: list[str]
    conditions: list[str] = Field(description="keys a step's `when` may use")
    pipelines: list[PipelineSummary]


class PipelineVersionInfo(ResponseModel):
    version: int
    notes: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Pipeline(ResponseModel):
    id: int
    name: str
    description: str | None = None
    current: int
    created_at: str | None = None
    updated_at: str | None = None
    version: int
    steps: list[dict[str, Any]]
    notes: str | None = None
    created_by: str | None = None
    history: list[PipelineVersionInfo] = []


class PipelineCreate(RequestModel):
    name: str
    steps: list[Step]
    description: str | None = None


class PipelineVersionCreate(RequestModel):
    steps: list[Step]
    notes: str | None = None
    publish: bool = True


class PipelineRunRequest(RequestModel):
    recording: int


class JobQueued(Ok):
    job: int
