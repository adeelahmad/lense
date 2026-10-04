"""Custom nodes: bodies of nodes saved under a name and used in workflows like any other node."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.workflows import WorkflowGraph, WorkflowVersionInfo

Visibility = Literal["private", "namespace", "everyone"]


class NodeParam(ResponseModel):
    name: str
    label: str | None = None
    kind: Literal["text", "number", "bool", "json", "choice"] = "text"
    default: Any = None
    options: list[Any] | None = None
    help: str | None = None


class CustomNode(ResponseModel):
    id: int
    name: str
    description: str | None = None
    icon: str | None = Field(None, description="a lucide icon name, e.g. sparkles")
    color: str | None = Field(None, description="blue, green, gold, red, purple or neutral")
    visibility: Visibility = "private"
    namespaces: list[str] = Field(default_factory=list, description="who sees it, when shared with namespaces")
    owner_email: str | None = None
    editable: bool = False
    current: int
    version: int
    graph: WorkflowGraph
    params: list[NodeParam] = []
    inputs: list[str] = Field(description="its input ports: the names of the arg nodes in its body")
    outputs: list[str] = Field(description="its output ports: the names of the return nodes in its body")
    scopes: list[str] = Field(description="the workflows it can be used in: recording, graph or both")
    keeps: bool = Field(False, description="whether it saves something itself")
    deleted_at: str | None = None
    updated_at: str | None = None
    history: list[WorkflowVersionInfo] = []


class CustomNodeCreate(RequestModel):
    name: str
    graph: WorkflowGraph
    params: list[dict[str, Any]] = []
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    visibility: Visibility = "private"
    namespaces: list[str] = []


class CustomNodeVersionCreate(RequestModel):
    graph: WorkflowGraph
    params: list[dict[str, Any]] = []
    notes: str | None = None


class CustomNodeUpdate(RequestModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    visibility: Visibility | None = None
    namespaces: list[str] | None = None
