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
