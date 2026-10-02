"""Settings, the audit log and the health page (admins only)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, ResponseModel


class Started(Ok):
    """Work that carries on after the response (202 Accepted)."""

    status: str


class LlmTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    reply: str | None = None
    ms: int | None = None
    model: str | None = None


class EmbedTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    dimension: int | None = Field(None, description="how many numbers the model's vectors have")
    ms: int | None = None
    model: str | None = None


class DecisionTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    ms: int | None = None
    model: str | None = None
    yes: float | None = Field(None, description="the probability it gave a question whose answer is yes")
    choice: str | None = Field(None, description="what it chose where `sea` was right")


class DecisionStatus(ResponseModel):
    """Typed decisions (Settings → Decisions)."""

    enabled: bool
    configured: bool = Field(description="on, with a model to ask")
    base_url: str
    hosted: bool = Field(description="the server is TypeSafe's own (api.typesafe.ai)")
    model: str | None = None
    key: bool = Field(description="an API key is set (saved in the app, or in the environment)")


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
