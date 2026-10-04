"""Workflows: graphs of nodes, drawn on a canvas, that turn what a pipeline made into metadata."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import RequestModel, ResponseModel


class WorkflowGraph(BaseModel):
    nodes: list[dict[str, Any]] = Field(description="{id, type, config, label, x, y}")
    edges: list[dict[str, Any]] = Field(
        default_factory=list,
        description="{source, target, port (the source's output, default out), input (the target's input, default in)}",
    )


class NodeType(ResponseModel):
    type: str
    settings: list[str]
    scopes: list[str] = Field(default_factory=lambda: ["recording"], description="the workflow scopes it can be used in")
    inputs: int = Field(description="how many edges may come in: 0, 1, or -1 for any number")
    outputs: list[str] = Field(description="its outgoing ports: [] for none, ['out'], or a condition's ['yes', 'no']")
    input_ports: list[str] = Field(default_factory=lambda: ["in"], description="its input ports, by name")
    dynamic: str | None = Field(None, description="which ports its settings make: inputs, outputs or both")
    primitive: bool = Field(False, description="a building block every workflow has")
    keeps: bool = Field(False, description="it saves something")


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
    custom_nodes: list[dict[str, Any]] = Field(default_factory=list, description="the custom nodes you can use")


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


class WorkflowTryRequest(RequestModel):
    graph: WorkflowGraph
    scope: Literal["recording", "graph"] = "recording"
    recording: int | None = Field(None, description="the recording to try a recording workflow on")
    namespaces: list[str] = Field(default_factory=list, description="the namespaces to try a graph workflow over (default: all)")


class NodeTrace(ResponseModel):
    status: Literal["done", "skipped", "failed"]
    ms: int | None = None
    ports: list[str] = Field(default_factory=list, description="the output ports it passed something on along")
    value: str | None = Field(None, description="what it passed on (or took in, if it passes nothing on), as JSON, cut short")
    error: str | None = None


class WorkflowTry(ResponseModel):
    trace: dict[str, NodeTrace] = Field(description="by node id; nodes in a body as body node id/node id (first item)")
    log: list[str]
    error: str | None = None
    steps: int
