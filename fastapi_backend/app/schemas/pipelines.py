"""Pipelines: the steps run on a recording, versioned; a namespace can make one its default."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import Ok, RequestModel, ResponseModel

Step = str | dict[str, Any]


class ContentTypeUse(ResponseModel):
    namespace: str
    content_type: str


class PipelineSummary(ResponseModel):
    id: int
    name: str
    description: str | None = None
    current: int
    updated_at: str | None = None
    namespaces: list[str] = Field(default_factory=list, description="namespaces that use it by default")
    content_types: list[ContentTypeUse] = Field(default_factory=list, description="namespaces that use it for one content type")


class PipelineGraph(BaseModel):
    """A pipeline as the canvas draws it: steps as nodes, an edge saying its target runs after its source."""

    nodes: list[dict[str, Any]] = Field(description="{id, step, x, y}")
    edges: list[dict[str, Any]] = Field(default_factory=list, description="{source, target}")


class PipelineCatalog(ResponseModel):
    standard: list[str] = Field(description="the built-in pipeline")
    step_types: list[str]
    asset_steps: list[str] = Field(default_factory=list, description="steps that make something of the media")
    content_types: list[str] = Field(default_factory=list, description="what a namespace can choose a pipeline for")
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
    graph: PipelineGraph
    notes: str | None = None
    created_by: str | None = None
    history: list[PipelineVersionInfo] = []


class PipelineCreate(RequestModel):
    name: str
    steps: list[Step] = Field(default_factory=list, description="the steps in order; or send a graph instead")
    graph: PipelineGraph | None = None
    description: str | None = None


class PipelineVersionCreate(RequestModel):
    steps: list[Step] = Field(default_factory=list, description="the steps in order; or send a graph instead")
    graph: PipelineGraph | None = None
    notes: str | None = None
    publish: bool = True


class PipelineRunRequest(RequestModel):
    recording: int


class JobQueued(Ok):
    job: int
