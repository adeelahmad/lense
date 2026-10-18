"""Auto scaling (docs/scaling.md): providers, scaling groups with their rules, and the units they launch."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class DriverField(ResponseModel):
    label: str
    type: Literal["string", "integer", "number", "boolean", "list"]
    required: bool = False
    default: Any = None
    help: str | None = None
    options: list[Any] | None = None
    secret: bool = Field(False, description="a credential: sealed when saved, shown only as set")


class DriverSpec(ResponseModel):
    id: str
    kind: Literal["node", "model"] = Field(description="node: machines that run `lens worker`; model: a model endpoint")
    label: str
    about: str
    priced: bool = Field(description="it charges money, so its groups need a budget")
    extra: str | None = Field(None, description="the optional packages it needs, fetched when a provider of it is saved")
    license: str | None = None
    ready: bool = Field(description="its packages are installed")
    fields: dict[str, DriverField] = Field(description="the provider's settings")
    template: dict[str, DriverField] = Field(description="what a group picks for each unit")


class ProviderIn(RequestModel):
    name: str = Field(max_length=80)
    driver: str = Field(description="the kind of provider (see /scaling/drivers)")
    settings: dict[str, Any] = Field(default_factory=dict, description="the driver's non-secret settings")
    secrets: dict[str, str | None] = Field(default_factory=dict, description="its credentials: sealed, never shown again")
    price: float | None = Field(None, ge=0, description="USD per unit-hour, when the driver doesn't know it; an estimate")
    note: str | None = Field(None, max_length=300)
    check: bool = Field(True, description="prove the credentials with a read-only call before answering")


class ProviderChange(RequestModel):
    name: str | None = Field(None, max_length=80)
    settings: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = Field(None, description="a secret given as null is removed; one left out is kept")
    price: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=300)
    check: bool = True


class Provider(ResponseModel):
    id: int
    name: str
    driver: str
    kind: str | None = None
    label: str | None = None
    priced: bool = True
    settings: dict[str, Any]
    secrets: dict[str, bool] = Field(description="which credentials are set")
    price: float | None = None
    note: str | None = None
    checked_at: str | None = None
    check: dict[str, Any] | None = Field(None, description="the last check: {ok, error?}")
    created_at: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


class Rule(RequestModel):
    type: Literal["target", "step", "schedule"]
    name: str | None = Field(None, max_length=80)
    metric: str | None = Field(None, description="target and step rules: see /scaling/metrics")
    target: float | None = Field(None, description="target rules: the value to keep the metric at")
    above: float | None = Field(None, description="step rules: fires while the metric is above this")
    below: float | None = Field(None, description="step rules: fires while the metric is below this")
    minutes: int | None = Field(None, ge=0, le=1440, description="step rules: for how long before it fires (default 1)")
    add: int | str | None = Field(None, description='step rules: units to add, a count or a percentage like "50%"')
    remove: int | str | None = Field(None, description="step rules: units to remove, a count or a percentage")
    cron: str | None = Field(None, description="schedule rules: when, as a cron expression")
    timezone: str | None = None
    min: int | None = Field(None, ge=0, le=100, description="schedule rules: the new min")
    max: int | None = Field(None, ge=0, le=100, description="schedule rules: the new max")
    desired: int | None = Field(None, ge=0, le=100, description="schedule rules: the new desired count")


class GroupIn(RequestModel):
    name: str = Field(max_length=80)
    provider: int = Field(description="the provider's id")
    kind: Literal["node", "model"] | None = Field(None, description="the provider's kind; filled in when left out")
    template: dict[str, Any] = Field(default_factory=dict, description="what each unit is (the driver's template fields)")
    min: int = Field(0, ge=0, le=100)
    max: int = Field(1, ge=0, le=100)
    desired: int | None = Field(None, ge=0, le=100, description="units wanted now (default min)")
    cooldown_seconds: int = Field(300, ge=0, le=86400, description="how long after a change before the next")
    warmup_seconds: int = Field(300, ge=0, le=7200, description="how long a new unit may take to join before more are judged")
    terminate: Literal["oldest", "newest"] = Field("oldest", description="which unit goes first when scaling in")
    rules: list[Rule] = Field(default_factory=list)
    at_cap: Literal["stop", "min"] = Field("stop", description="at the budget's cap: stop every unit, or keep min")
    enabled: bool = True
    budget: float | None = Field(None, gt=0, description="USD per month; needed on a priced provider")


class GroupChange(RequestModel):
    name: str | None = Field(None, max_length=80)
    provider: int | None = None
    template: dict[str, Any] | None = None
    min: int | None = Field(None, ge=0, le=100)
    max: int | None = Field(None, ge=0, le=100)
    desired: int | None = Field(None, ge=0, le=100)
    cooldown_seconds: int | None = Field(None, ge=0, le=86400)
    warmup_seconds: int | None = Field(None, ge=0, le=7200)
    terminate: Literal["oldest", "newest"] | None = None
    rules: list[Rule] | None = None
    at_cap: Literal["stop", "min"] | None = None
    enabled: bool | None = None
    budget: float | None = Field(None, gt=0)


class Desired(RequestModel):
    desired: int = Field(ge=0, le=100)


class Paused(RequestModel):
    paused: bool = True
    why: str | None = Field(None, max_length=200)


class Protected(RequestModel):
    protected: bool = True


class Terminate(RequestModel):
    why: str | None = Field(None, max_length=200)


class UnitCounts(ResponseModel):
    launching: int
    running: int
    draining: int


class GroupBudget(ResponseModel):
    usd: float | None = None
    period: str
    state: str
    spent: dict[str, Any]
    left: dict[str, Any]


class Group(ResponseModel):
    id: int
    name: str
    kind: str
    provider: int
    template: dict[str, Any]
    min: int
    max: int
    desired: int
    cooldown_seconds: int | None = None
    warmup_seconds: int | None = None
    terminate: str
    rules: list[dict[str, Any]] = Field(description="each with `says`, the rule in words")
    at_cap: str
    enabled: bool
    paused: bool
    paused_why: str | None = None
    capped: str | None = Field(None, description="why its budget stops it now, when it does")
    approval: dict[str, Any] | None = Field(None, description="a person's approval: {by, at, terms}")
    approved: bool = Field(description="the approval covers the terms as they stand")
    terms: dict[str, Any] = Field(description="what an approval binds: kind, provider, template, max, budget, at_cap")
    budget: GroupBudget | None = None
    units: UnitCounts
    waiting_for: str | None = Field(None, description="why it launches nothing now, when it doesn't")
    unit_steps: list[str] | None = Field(
        None,
        description="the steps its units run, which its queue metrics count (None: every step); on a machine elsewhere, only a worker node's",
    )
    last_out_at: str | None = None
    last_in_at: str | None = None
    last_eval: dict[str, Any] | None = Field(None, description="what the rules said last: {at, said}")
    failures: int = 0
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


class Unit(ResponseModel):
    id: int
    group: int
    provider: int
    driver: str | None = None
    name: str
    external_id: str | None = Field(None, description="the provider's id for it")
    state: Literal["launching", "running", "draining", "terminating", "terminated", "failed"]
    address: str | None = None
    endpoint: dict[str, Any] | None = Field(None, description="a model unit's endpoint: base_url, model; api_key true when set")
    price: float | None = Field(None, description="USD per hour, an estimate")
    launched_at: str | None = None
    ready_at: str | None = None
    ended_at: str | None = None
    by: str | None = None
    why: str | None = None
    protected: bool = False
    error: str | None = None
    accrued_at: str | None = None


class Overview(ResponseModel):
    enabled: bool
    settings: dict[str, Any]
    scaler: dict[str, Any] = Field(description="which process scales and when it last looked")
    providers: list[Provider]
    groups: list[Group]
    units: list[Unit]
    drivers: list[DriverSpec]
    metrics: dict[str, float | int]


class Metrics(ResponseModel):
    metrics: dict[str, float | int]
    about: dict[str, str] = Field(description="what each metric measures")
