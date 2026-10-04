"""Settings, the audit log and the health page (admins only)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import Ok, ResponseModel


class Started(Ok):
    """Work that carries on after the response (202 Accepted)."""

    status: str


class MailTestResult(ResponseModel):
    ok: bool
    to: str | None = Field(None, description="where the test message went: your own address")
    error: str | None = None


class BridgeStatus(ResponseModel):
    state: Literal["off", "incomplete", "starting", "running", "error"] = Field(
        description="off; incomplete (`error` says what's missing); starting (no server process has picked it up yet); "
        "running; error (the last look at Matterbridge failed, `error` says why)"
    )
    error: str | None = None
    holder: str | None = Field(None, description="the server process reading Matterbridge")
    at: str | None = Field(None, description="when it last looked")
    answered: int | None = Field(None, description="messages it answered since it started")


class TunnelStatus(ResponseModel):
    mode: Literal["off", "quick", "token", "managed"]
    running: bool = Field(description="cloudflared is running in a server process")
    connected: bool = Field(description="Cloudflare has the tunnel's connection: the address works")
    url: str | None = Field(None, description="the address people open Lens at through the tunnel")
    error: str | None = Field(None, description="why the tunnel isn't up, when it isn't")
    log: list[str] = Field(default_factory=list, description="cloudflared's last lines")
    origin: str = Field(description="where cloudflared sends visitors: the web app, as the server reaches it")
    process: str | None = Field(None, description="the server process running cloudflared")


class LocalModel(ResponseModel):
    id: str
    label: str
    repo: str
    file: str
    license: str | None = None
    about: str
    tools: bool = Field(description="good at the assistant's tool calls")
    size_gb: float
    memory_gb: float = Field(description="memory it needs to run, with room for Lens")
    downloaded: bool
    fits: bool = Field(description="this machine has the memory (or GPU memory) for it")
    room: bool = Field(description="there's disk for it, or it's downloaded")


class LocalModelFile(ResponseModel):
    path: str
    size_gb: float


class LocalLlmProgress(ResponseModel):
    done: int
    total: int | None = None


class LocalLlmStatus(ResponseModel):
    enabled: bool
    model: str | None = None
    phase: Literal["off", "fetching-server", "downloading", "starting", "running", "error"]
    progress: LocalLlmProgress | None = None
    url: str | None = Field(None, description="the OpenAI-compatible address it answers at, while running")
    error: str | None = None
    log: list[str] = Field(default_factory=list, description="llama-server's last lines")
    process: str | None = Field(None, description="the server process running it")
    server: str | None = Field(None, description="llama-server's path, when it's here")
    catalog: list[LocalModel]
    files: list[LocalModelFile]
    machine: dict[str, Any]


class Removed(ResponseModel):
    removed: bool


class BridgeTestResult(ResponseModel):
    ok: bool
    error: str | None = None


class LlmTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    reply: str | None = None
    ms: int | None = None
    model: str | None = None


class LayaModel(ResponseModel):
    id: str = Field(description="the Hugging Face repository")
    about: str


class LayaStatus(ResponseModel):
    available: bool
    where: str | None = Field(None, description="here (this machine runs it), server (a Laya server), or none")
    model: str
    reason: str | None = Field(None, description="why it can't answer, when it can't")


class DecisionStatus(ResponseModel):
    """Who takes routine decisions now, and whether a local Laya model could."""

    engine: str = Field(description="the setting: auto, jev, laya, llm or off")
    by: str | None = Field(None, description="who answers now: jev, laya, llm, or none")
    apple_silicon: bool = Field(description="this machine can run MLX models")
    laya: LayaStatus
    laya_models: list[LayaModel]


class DecisionTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    by: str | None = Field(None, description="who answered: jev, laya or llm (the language model when the decision model failed)")
    choice: str | None = None
    confidence: float | None = None
    ms: int | None = None


class EmbedTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    dimension: int | None = Field(None, description="how many numbers the model's vectors have")
    ms: int | None = None
    model: str | None = None


class SpeechTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    detail: str | None = Field(None, description="what the provider answered, when it did")
    ms: int | None = None


class SemanticStatus(ResponseModel):
    """Search by meaning: whether it's on, which model it uses, and how much of the archive it covers."""

    enabled: bool
    configured: bool = Field(description="on, with an embeddings server and a model")
    base_url: str | None = Field(None, description="the embeddings server: its own, else the LLM provider's")
    model: str | None = None
    indexed_model: str | None = Field(None, description="the model the stored vectors are from")
    current: bool = Field(description="the stored vectors are the configured model's, so searches can use them")
    dimension: int | None = None
    passages: int = Field(description="passages embedded")
    recordings: int = Field(description="recordings in the archive")
    indexed: int = Field(description="recordings indexed with the configured model")
    min_similarity: float = Field(description="how alike a passage must be to a query to be a hit")


class IndexQueued(ResponseModel):
    recordings: int = Field(description="recordings queued to be indexed")
    remaining: bool = Field(description="more are waiting than were queued; run it again, or let the hourly routine")


class TelemetryExport(ResponseModel):
    at: float
    ok: bool
    error: str | None = None


class TelemetryStatus(ResponseModel):
    """What the API process does with telemetry now; each worker follows the same settings on its own."""

    enabled: bool
    endpoint: str | None = None
    traces: bool
    metrics: bool
    last_traces: TelemetryExport | None = None
    last_metrics: TelemetryExport | None = None


class TelemetryTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    ms: int | None = None


class AuditEntry(ResponseModel):
    at: str
    email: str | None = None
    action: str
    target: str | None = None
    detail: Any = None


class HealthDatabase(ResponseModel):
    url: str
    embedded: bool
    fulltext: str | None = Field(
        default=None, description="the full-text index keyword this SurrealDB uses (FULLTEXT or SEARCH); null: none"
    )
    ok: bool


class HealthSource(ResponseModel):
    id: int
    name: str
    type: str
    health: dict[str, Any] | None = None


class HealthDisk(ResponseModel):
    path: str
    free_gb: float
    total_gb: float


class HealthCounts(ResponseModel):
    recordings: int
    speakers: int
    accounts: int


class Health(ResponseModel):
    database: HealthDatabase
    queue: dict[str, int] = Field(description="jobs by status")
    workers: list[dict[str, Any]]
    sources: list[HealthSource]
    disk: HealthDisk
    counts: HealthCounts
