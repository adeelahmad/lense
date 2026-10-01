from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class Job(ResponseModel):
    id: int
    recording: int | None = None
    space: int | None = None
    steps: list[Any] = Field(default_factory=list)
    step_index: int | None = None
    next_step: str | None = None
    status: str
    error: str | None = None
    title: str | None = None
    progress: float | None = None
    log: list[str] | None = Field(None, description="the last 200 lines; GET /jobs/{jid}/log has them all")
    log_total: int | None = Field(None, description="how many lines the run has logged")


class JobLog(ResponseModel):
    start: int = Field(description="the number of the first line here (0-based)")
    lines: list[str]
    total: int = Field(description="how many lines the run has logged so far")
    more: bool = Field(description="whether there are lines after these")


class JobList(ResponseModel):
    jobs: list[Job]
    counts: dict[str, int]
    running: bool
    step: Any = Field(None, description="the latest job's current (or last) step")
    returncode: int | None = Field(None, description="the latest job: null while active, 0 succeeded, 1 otherwise")
    log: str = ""


class JobsCreate(RequestModel):
    recordings: list[int] = Field(default_factory=list)
    steps: list[str | dict[str, Any]] | None = None
    pipeline: int | None = None
    namespace: str | None = Field(None, description="also queue everything in this namespace that still needs work")


class JobsQueued(Ok):
    jobs: list[int]


class StepQueued(Ok):
    queued: int | str = Field(description='how many recordings were queued, or "scanning"')


class WorkerInfo(ResponseModel):
    name: str
    steps: list[str] = Field(default_factory=list)
    host: str | None = None
    heartbeat_at: str | None = None
    current: Any = None
