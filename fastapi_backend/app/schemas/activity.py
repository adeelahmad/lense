"""The activity ledger: what happened to a resource and what it cost (docs/activity.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ResponseModel


class ActivityEntry(ResponseModel):
    id: str | None = Field(None, description="the ledger row's id; none for an audit log entry")
    at: str
    kind: Literal["in", "out", "run", "change"] = Field(
        description="in: an API request; out: a call Lens made (a model, embeddings, the decision model, a webhook, a "
        "web tool); run: a job or routine run ended; change: an audit log entry"
    )
    action: str = Field(description="what it was: `POST /api/v1/routines/{rid}/run`, `model.chat`, `job.succeeded`, `routine.update`")
    resources: list[str] = Field(default_factory=list, description="every resource it counts for, as table:id")
    actor: int | None = Field(None, description="the account that made it, when a person did")
    email: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = Field(None, description="estimated from the prices in Settings; none when the model has no price")
    ms: int | None = None
    ok: bool = True
    error: str | None = Field(None, description="the error's type or HTTP status; never its message")
    detail: Any = None


class KindTotals(ResponseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0
    ms: int = 0
    failed: int = 0


class ActivityTotals(ResponseModel):
    resource: str | None = None
    period: str
    since: str | None = None
    calls: int
    failed: int
    tokens_in: int
    tokens_out: int
    cost_usd: float = Field(description="what its calls cost (run rows repeat their calls' cost and aren't added again)")
    ms: int
    unpriced: int = Field(
        0, description="calls that cost something but have no figure (a model with no price, a reply without token counts)"
    )
    estimate: bool = Field(False, description="true when unpriced calls make cost_usd a floor rather than exact")
    by_kind: dict[str, KindTotals]


class ResourceCost(ResponseModel):
    resource: str
    cost_usd: float
    tokens: int
    calls: int


class ResourceSpend(ResponseModel):
    cost_usd: float
    tokens: int
    calls: int
    unpriced: int
    estimate: bool = Field(description="true: cost_usd is a floor (unpriced calls); show it as an estimate")


class ActivityCosts(ResponseModel):
    period: str
    since: str | None = None
    costs: dict[str, ResourceSpend] = Field(description="by resource; ones you can't see are left out")


class ActivityTop(ResponseModel):
    period: str
    since: str | None = None
    resources: list[ResourceCost]
