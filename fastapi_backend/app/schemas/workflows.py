"""Workflows: graphs of nodes, drawn on a canvas, that turn what a pipeline made into metadata."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import RequestModel, ResponseModel


class WorkflowGraph(BaseModel):
    nodes: list[dict[str, Any]] = Field(description="{id, type, config, label, x, y}")
    edges: list[dict[str, Any]] = Field(default_factory=list, description="{source, target, branch (a condition's yes or no)}")


class NodeType(ResponseModel):
    type: str
    settings: list[str]
    scopes: list[str] = Field(default_factory=lambda: ["recording"], description="the workflow scopes it can be used in")
    inputs: int = Field(description="how many edges may come in: 0, 1, or -1 for any number")
    outputs: list[str] = Field(description="its outgoing ports: [] for none, ['out'], or a condition's ['yes', 'no']")


class WorkflowSummary(ResponseModel):
    id: int
    name: str
    description: str | None = None
    scope: str = Field("recording", description="recording (run by pipelines) or graph (run over namespaces by routines)")
    current: int
    updated_at: str | None = None
    pipelines: list[str] = Field(default_factory=list, description="pipelines whose current version runs it")


class WorkflowCatalog(ResponseModel):
    node_types: list[NodeType]
    operators: list[str] = Field(description="what a condition node can test")
    entity_types: list[str]
    scopes: list[str] = Field(default_factory=lambda: ["recording", "graph"])
    workflows: list[WorkflowSummary]


class WorkflowVersionInfo(ResponseModel):
    version: int
    notes: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Workflow(ResponseModel):
    id: int
    name: str
    description: str | None = None
    scope: str = "recording"
    current: int
    created_at: str | None = None
    updated_at: str | None = None
    version: int
    graph: WorkflowGraph
    notes: str | None = None
    created_by: str | None = None
    history: list[WorkflowVersionInfo] = []


class WorkflowCreate(RequestModel):
    name: str
    graph: WorkflowGraph
    description: str | None = None
    scope: Literal["recording", "graph"] = "recording"


class WorkflowVersionCreate(RequestModel):
    graph: WorkflowGraph
    notes: str | None = None
    publish: bool = True


class WorkflowUpdate(RequestModel):
    name: str | None = None
    description: str | None = None


class WorkflowRunRequest(RequestModel):
    recording: int
    version: int | None = None
