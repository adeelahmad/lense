from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class SetupAdmin(ResponseModel):
    from_env: bool


class SetupNamespaceView(ResponseModel):
    existing: list[str]
    locked: bool


class SetupLlmView(ResponseModel):
    values: dict[str, Any]
    locked: list[str]


class SetupStorageView(ResponseModel):
    data_dir: str
    database: str
    embedded: bool
    local_roots: list[str]
    max_upload_mb: int
    watches: int


class SetupAnalyticsView(ResponseModel):
    enabled: bool
    measurement_id: str | None = None
    # an API secret is saved (it is never shown)
    secret_set: bool
    # what .env sets (LENS_ANALYTICS, LENS_GA_MEASUREMENT_ID, LENS_GA_API_SECRET): shown locked
    locked: list[str]


class SetupTelemetryView(ResponseModel):
    enabled: bool
    endpoint: str | None = None
    # what .env sets (LENS_TELEMETRY, LENS_TELEMETRY_ENDPOINT): shown locked
    locked: list[str]
    # usage statistics sent to the admin's own Google Analytics property (docs/analytics.md); off by default
    analytics: SetupAnalyticsView


class SetupOAuthView(ResponseModel):
    # whether apps and MCP clients may sign people in with their Lens account (tokens.oauth_enabled)
    enabled: bool
    access_minutes: int
    refresh_days: int


class SetupBudgetView(ResponseModel):
    # the overall budget (budgets.py, resource `all`): none set is no limit
    hard_usd: float | None = None
    soft_usd: float | None = None
    period: str = "month"
    on_over: str = "ask"
    # the chat model's price, USD per million tokens in and out (ai.price_in, ai.price_out): a budget in money
    # counts only what has a price
    price_in: float | None = None
    price_out: float | None = None
    model: str | None = None


class SetupView(ResponseModel):
    pending: bool
    admin: SetupAdmin
    namespace: SetupNamespaceView
    llm: SetupLlmView
    storage: SetupStorageView
    telemetry: SetupTelemetryView
    oauth: SetupOAuthView
    budget: SetupBudgetView


class SetupNamespace(RequestModel):
    name: str = Field(min_length=1, max_length=41)
    graph: Literal["shared", "isolated"] = "shared"


class SetupLlm(RequestModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    # empty: leave the saved key as it is
    api_key: str | None = Field(default=None, max_length=2000)


class LocalModelServer(ResponseModel):
    kind: str
    base_url: str
    # chat models first
    models: list[str]
    suggested: str


class CloudModelProvider(ResponseModel):
    name: str
    base_url: str
    # the model picking it fills in, then other suggestions
    model: str
    models: list[str]
    # where the provider hands out API keys
    key_url: str


class SetupStorage(RequestModel):
    max_upload_mb: int | None = None
    # a folder inside sources.local_roots to watch into `namespace`
    folder: str | None = Field(default=None, max_length=4096)
    namespace: str | None = None


class SetupTelemetry(RequestModel):
    enabled: bool = False
    # the OTLP/HTTP address of a collector, like http://localhost:4318; needed to turn telemetry on
    endpoint: str | None = Field(default=None, max_length=500)
    # Google Analytics (docs/analytics.md): left out, unchanged
    analytics: bool | None = None
    measurement_id: str | None = Field(default=None, max_length=40)
    # empty: leave the saved secret as it is
    api_secret: str | None = Field(default=None, max_length=200)


class SetupOAuth(RequestModel):
    enabled: bool = True
    # left out: unchanged
    access_minutes: int | None = None
    refresh_days: int | None = None


class SetupBudget(RequestModel):
    # the hard limit in USD per period; none: no limit (the overall budget is removed)
    hard_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    # the soft limit, which warns; none: 80% of the hard limit
    soft_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    period: Literal["day", "week", "month"] = "month"
    # at the hard limit, runs wait for a person (ask) or are skipped (skip); paid model calls stop either way
    on_over: Literal["ask", "skip"] = "ask"
    price_in: float | None = Field(default=None, ge=0, le=100_000)
    price_out: float | None = Field(default=None, ge=0, le=100_000)


class SetupFinish(RequestModel):
    skipped: bool = False


class SetupSaved(ResponseModel):
    ok: bool = True
    saved: list[str] = []
    watch: int | None = None
