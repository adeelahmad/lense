from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class JobPipeline(ResponseModel):
    id: int | None = Field(None, description="null for the standard steps")
    version: int | None = Field(None, description="the version the run pinned when it was queued")
    name: str


class StepOutput(ResponseModel):
    key: str = Field(description="saved as outputs.<key> on the recording")
    template: int | None = None
    version: int | None = Field(None, description="the template version it was made with")
    model: str | None = None


class StepRun(ResponseModel):
    started_at: str | None = None
    finished_at: str | None = None
    seconds: float | None = Field(None, description="how long it took")
    outcome: Literal["running", "done", "skipped", "failed"] | None = None
    note: str | None = Field(None, description="its last message, why it skipped, or its error")
    worker: str | None = None
    log_from: int | None = Field(None, description="its first line in the run's log (GET /jobs/{jid}/log numbers lines from 0)")
    log_to: int | None = Field(None, description="the line after its last one")
    outputs: list[StepOutput] = Field(default_factory=list, description="the outputs it saved")


class Job(ResponseModel):
    id: int
    recording: int | None = None
    space: int | None = None
    batch: int | None = None
    pipeline: JobPipeline | None = Field(None, description="the pipeline it runs; null when its steps were chosen directly")
    steps: list[Any] = Field(default_factory=list)
    step_index: int | None = None
    next_step: str | None = None
    status: str
    error: str | None = None
    title: str | None = Field(None, description="the recording's title")
    progress: float | None = None
    worker: str | None = None
    attempts: int | None = None
    created_by: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    cancel_requested: bool | None = None
    log: list[str] | None = Field(None, description="the last 200 lines; GET /jobs/{jid}/log has them all")
    log_total: int | None = Field(None, description="how many lines the run has logged")
    step_runs: list[StepRun | None] | None = Field(
        None,
        description="GET /jobs/{jid}: one per step, in order: how its latest run went (null until it has run; "
        "runs from before these were kept have none)",
    )
    estimates: list[float | None] | None = Field(
        None,
        description="GET /jobs/{jid}: how long each step usually takes on this recording, in seconds, from recent runs "
        "(null for a step with none)",
    )
    eta_seconds: float | None = Field(
        None, description="GET /jobs/{jid}, while queued or running: about how long until it finishes, from `estimates`"
    )


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
