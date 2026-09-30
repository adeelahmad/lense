"""Batch runs: a template, pipeline or steps over many recordings, estimated first, confirmed when big."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class BatchSelection(RequestModel):
    """Which recordings: a collection, a list, or a filter (optionally narrowed to a namespace, entity or speaker)."""

    collection: int | None = None
    recordings: list[int] | None = None
    filter: dict[str, Any] | None = None
    namespace: str | None = None
    entity: int | None = None
    speaker: int | None = None


class BatchRun(RequestModel):
    """What to run: a template, a pipeline, or a list of steps."""

    template: int | None = None
    key: str | None = Field(default=None, description="output key for a prompt template")
    filename: str | None = Field(default=None, description="file name for an export template")
    pipeline: int | None = None
    steps: list[str | dict[str, Any]] | None = None


class BatchPlan(RequestModel):
    selection: BatchSelection = Field(default_factory=lambda: BatchSelection())
    run: BatchRun = Field(default_factory=lambda: BatchRun())


class BatchCreate(BatchPlan):
    sample: int | None = Field(default=None, ge=1, description="run on this many first; continue after review")
    confirm: str | None = Field(default=None, description='the confirmation text a big run asks for, e.g. "RUN 40"')


class LlmEstimate(ResponseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost: float | None = None
    model: str | None = None


class Estimate(ResponseModel):
    recordings: int
    by_kind: dict[str, int] = {}
    hours: float
    seconds: int
    llm: LlmEstimate
    would_replace: int = Field(description="existing outputs the run would overwrite")
    needs_confirmation: bool
    confirm_text: str | None = None


class BatchEstimate(Estimate):
    label: str
    steps: list[dict[str, Any]]
    skipped: int = Field(description="matching recordings you can't change")


class BatchNeedsConfirmation(ResponseModel):
    detail: str
    estimate: Estimate


class BatchProgress(ResponseModel):
    counts: dict[str, int]
    done: int
    total: int
    remaining: int


class BatchSummary(ResponseModel):
    id: int
    label: str
    status: str
    created_by: str | None = None
    created_at: str | None = None
    progress: BatchProgress


class BatchReportRef(ResponseModel):
    n: int
    recording_id: int
    title: str | None = None
    date: str | None = None


class BatchReport(ResponseModel):
    text: str
    instructions: str
    key: str | None = None
    at: str | None = None
    refs: list[BatchReportRef] = []


class Batch(BatchSummary):
    steps: list[dict[str, Any]]
    selection: dict[str, Any] | None = None
    recordings: list[int]
    started: list[int]
    skipped: int = 0
    estimate: Estimate | None = None
    report: BatchReport | None = None


class BatchCombine(RequestModel):
    instructions: str | None = None
    key: str | None = None


class BatchActionResult(Ok):
    result: int | None = Field(default=None, description="recordings started (continue) or jobs retried (retry)")


class BatchResults(ResponseModel):
    key: str | None = None
    columns: list[str]
    rows: list[dict[str, Any]]
