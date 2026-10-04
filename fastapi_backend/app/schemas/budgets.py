"""Budgets: caps on what a routine, pipeline, workflow or namespace may cost (docs/budgets.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class BudgetSet(RequestModel):
    usd: float | None = Field(None, ge=0, description="the cap in USD (from the costs in the activity ledger)")
    tokens: int | None = Field(None, ge=0, description="the cap in tokens (in and out)")
    period: Literal["run", "day", "week", "month"] = Field(
        "month", description="what the cap is for: one run, or a day, week or month (UTC)"
    )
    on_over: Literal["ask", "skip", "assistant"] = Field(
        "ask",
        description="a run that would go over: ask (hold it until someone picks run or skip), skip, or assistant (the "
        "decision model weighs it; it runs only when sure, else asks)",
    )
    warn_at: float = Field(0.8, ge=0.1, le=1, description="the share spent at which it's marked near (and warned about)")
    note: str | None = Field(None, max_length=300)


class Budget(ResponseModel):
    resource: str
    usd: float | None = None
    tokens: int | None = None
    period: str
    on_over: str
    warn_at: float
    note: str | None = None
    by: str | None = None
    updated_at: str | None = None
    alert: dict[str, Any] | None = Field(None, description="the last warning: {state near|over, since, at, share}")


class Spent(ResponseModel):
    usd: float
    tokens: int
    estimate: bool = Field(description="true: some calls had no figure, so usd is a floor")


class NextRun(ResponseModel):
    usd: float | None = None
    tokens: int | None = None
    runs: int = Field(description="the past runs the estimate is drawn from")
    basis: str


class BudgetStatus(ResponseModel):
    resource: str
    budget: Budget | None = None
    state: Literal["none", "ok", "near", "over"]
    since: str | None = Field(None, description="the start of the period being counted")
    spent: Spent | None = None
    next: NextRun = Field(description="what one more run will likely cost: always an estimate")
    left: dict[str, float] | None = None
    share: float | None = Field(None, description="the largest share of a cap spent (1 is all of it)")
    share_after_next: float | None = Field(None, description="the share once the next run (estimated) is done")
