"""Routines: scheduled syncs, pipelines and workflows; and the graph changes graph workflows propose or make."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class Routine(ResponseModel):
    id: int
    name: str
    description: str | None = None
    enabled: bool
    schedule: str | None = Field(None, description="five-field cron (minute hour day month weekday) or @daily...; none: by hand only")
    schedule_text: str
    timezone: str = "UTC"
    namespaces: list[int] | None = Field(None, description="namespace ids; none for every namespace")
    namespace_names: list[str | None] | None = None
    actions: list[dict[str, Any]]
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    last_run: int | None = None
    running: bool = False
    created_at: str | None = None
    created_by: str | None = None


class RoutineCatalog(ResponseModel):
    routines: list[Routine]
    actions: list[str] = Field(description="what a routine can do: sync, pipeline, workflow, sensors (tidy sensor data)")
    recordings: list[str] = Field(
        description="which recordings a pipeline or workflow action takes: new, unprocessed, all, unindexed (not yet searchable by meaning)"
    )


class RoutineCreate(RequestModel):
    name: str
    actions: list[dict[str, Any]] = Field(
        description="in order: {type: sync, watches?}, {type: pipeline, pipeline?, steps?, recordings?, limit?}, "
        "{type: workflow, workflow, version?, recordings?, limit?, propose_only?}, {type: sensors, sensors?}"
    )
    schedule: str | None = None
    timezone: str = "UTC"
    namespaces: list[int] | None = None
    description: str | None = None
    enabled: bool = True


class RoutineUpdate(RequestModel):
    name: str | None = None
    actions: list[dict[str, Any]] | None = None
    schedule: str | None = None
    timezone: str | None = None
    namespaces: list[int] | None = None
    description: str | None = None
    enabled: bool | None = None


class RoutineRunRequest(RequestModel):
    propose_only: bool = Field(False, description="graph workflows propose every change instead of making the sure ones")


class RoutineRun(ResponseModel):
    id: int
    routine: int
    trigger: str
    by: str | None = None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    results: list[dict[str, Any]] = []
    error: str | None = None
    changes: dict[str, int] | None = None
    log: list[str] | None = None
    cost_usd: float | None = Field(None, description="what its calls cost, from the activity ledger (docs/activity.md); set when it ends")
    tokens: int | None = None
    cost_estimate: bool | None = Field(None, description="true: some calls had no price or token counts, so cost_usd is a floor")


class SchedulePreview(ResponseModel):
    schedule: str
    timezone: str
    text: str
    next: list[str]


class GraphChange(ResponseModel):
    id: int
    kind: Literal["merge", "link"]
    a: dict[str, Any]
    b: dict[str, Any]
    spaces: list[int]
    reason: str | None = None
    confidence: float | None = None
    verdict: dict[str, Any] | None = None
    keep: int | None = None
    status: Literal["proposed", "applied", "dismissed", "undone"]
    routine: int | None = None
    run: int | None = None
    workflow: int | None = None
    created_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None


class GraphChangeAccept(RequestModel):
    keep: int | None = Field(None, description="for a merge: the entity to keep (default: the one proposed)")


class Undone(ResponseModel):
    undone: int
    failed: int = Field(0, description="changes that couldn't be undone (an entity in them was deleted since)")
