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
    cost_usd: float | None = Field(None, description="what its calls cost, from the activity ledger (docs/activity.md); set when it ends")
    tokens: int | None = None
    cost_estimate: bool | None = Field(None, description="true: some calls had no price or token counts, so cost_usd is a floor")
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
    counts: dict[str, int] = Field(description="jobs of each status (in `namespace` and `batch`, whatever `status`)")
    namespaces: dict[str, int] = Field(
        default_factory=dict, description="jobs in each namespace you can read (in `batch`, whatever `status`)"
    )
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


class ComponentState(ResponseModel):
    state: Literal["ready", "waiting", "fetching", "failed", "missing"] = Field(
        description="waiting/fetching: being fetched, and the steps that need it wait; missing: needed but fetching is off"
    )
    detail: str | None = Field(None, description="what it's doing, e.g. pulling nomic-embed-text: 40%")
    error: str | None = None


class Gpu(ResponseModel):
    name: str
    memory_gb: float | None = None


class Machine(ResponseModel):
    os: str
    arch: str
    cpus: int
    memory_gb: float | None = None
    gpus: list[Gpu] = Field(default_factory=list)
    cuda: bool = False
    apple_silicon: bool = False
    container: bool = False
    python: str | None = None
    disk_free_gb: float | None = Field(None, description="free space in the data folder, where models go")


class WorkerInfo(ResponseModel):
    name: str
    steps: list[str] = Field(default_factory=list)
    host: str | None = None
    heartbeat_at: str | None = Field(None, description="its last heartbeat: every 30 s when idle, every 15 s during a run")
    current: Any = Field(None, description="the job it's running")
    paused: bool = Field(False, description="it takes no new runs (POST /workers/{name}/pause)")
    draining: bool = Field(False, description="it hands the run it has back to the queue after the step it's on")
    paused_by: str | None = None
    paused_at: str | None = None
    load: float | None = Field(None, description="its machine's 1-minute load average per CPU (1.0: every CPU busy)")
    cpus: int | None = None
    steps_last_hour: int = Field(0, description="steps it finished (done or skipped) in the last hour")
    components: dict[str, ComponentState] = Field(
        default_factory=dict, description="what it needs, by component id (GET /components), and where each is"
    )
    machine: Machine | None = Field(None, description="its processors, memory, GPUs and free disk")
