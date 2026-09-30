"""Batch runs: a template, pipeline or steps over many recordings.

Estimate first (recordings, time, LLM calls and tokens, cost if prices are set, outputs that would be replaced); a big
run needs typed confirmation or starts with a sample; runs can be continued, paused, resumed, cancelled and retried, and
their outputs read (or downloaded) as one table and combined into one report.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.api.v1.routes.collections import own_collection
from app.domain import auth, batches, llm
from app.domain.store import DB
from app.schemas.batches import (
    Batch,
    BatchActionResult,
    BatchCombine,
    BatchCreate,
    BatchEstimate,
    BatchNeedsConfirmation,
    BatchPlan,
    BatchReport,
    BatchResults,
    BatchSummary,
)
from app.schemas.common import Created

router = APIRouter(prefix="/batches", tags=["batches"])
Action = Literal["continue", "pause", "resume", "cancel", "retry"]
EXPORTS = {"csv": "text/csv", "md": "text/markdown"}


def _plan(db: DB, acl: Access, user: Principal, body: BatchPlan) -> tuple[list[dict[str, Any]], str, list[int], int]:
    if body.selection.collection:
        own_collection(db, body.selection.collection, user)
    try:
        steps, label = batches.steps_for(db, body.run.model_dump(exclude_none=True))
        ids, skipped = batches.select(db, body.selection.model_dump(exclude_none=True), set(acl.roles), set(acl.editable()))
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e) if not isinstance(e, KeyError) else "no such template, pipeline or collection") from None
    return steps, label, ids, skipped


def own_batch(db: DB, bid: int, user: Principal) -> dict[str, Any]:
    """A batch its creator (or an admin) may see; else 404."""
    try:
        b = batches.get(db, bid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if b.get("created_by") != user.email and not user.admin:
        raise HTTPException(404, "not found")
    return b


@router.post("/estimate")
def estimate_batch(body: BatchPlan, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> BatchEstimate:
    """What a run would cost, before starting it. Only recordings you can change count; the rest are `skipped`."""
    steps, label, ids, skipped = _plan(db, acl, user, body)
    return BatchEstimate.model_validate({"label": label, "steps": steps, "skipped": skipped, **batches.estimate(db, cfg, ids, steps)})


@router.post("", responses={409: {"model": BatchNeedsConfirmation, "description": "a run this size needs `confirm` or `sample`"}})
def create_batch(body: BatchCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Created:
    steps, label, ids, skipped = _plan(db, acl, user, body)
    selection = body.selection.model_dump(exclude_none=True)
    try:
        bid = batches.create(db, cfg, user.email, ids, steps, label, selection, body.sample, body.confirm, skipped)
    except PermissionError as e:
        return JSONResponse({"detail": str(e), "estimate": batches.estimate(db, cfg, ids, steps)}, status_code=409)  # type: ignore[return-value]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "batch.create", f"batch:{bid}", {"label": label, "recordings": len(ids), "sample": body.sample})
    return Created(id=bid)


@router.get("")
def list_batches(user: CurrentUser, db: Db) -> list[BatchSummary]:
    """Your batch runs (everyone's, for admins), newest first."""
    rows = db.rows(
        "SELECT record::id(id) AS id, label, status, created_by, created_at FROM batch"
        + ("" if user.admin else " WHERE created_by = $e")
        + " ORDER BY created_at DESC LIMIT 100",
        e=user.email,
    )
    out = []
    for r in rows:
        b = batches.get(db, r["id"])
        out.append(BatchSummary.model_validate({**r, "status": b["status"], "progress": b["progress"]}))
    return out


@router.get("/{bid}")
def get_batch(bid: int, user: CurrentUser, db: Db) -> Batch:
    return Batch.model_validate(own_batch(db, bid, user))


@router.post("/{bid}/combine")
def combine_batch(bid: int, user: Writer, db: Db, cfg: Cfg, body: BatchCombine | None = None) -> BatchReport:
    """One overview written by the model from every recording's result, citing each as [n]; saved on the batch."""
    own_batch(db, bid, user)
    body = body or BatchCombine()
    try:
        return batches.combine(db, cfg, bid, body.instructions, body.key)
    except (ValueError, llm.LLMError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{bid}/{action}")
def control_batch(bid: int, action: Action, user: Writer, db: Db) -> BatchActionResult:
    """continue (after a sample), pause, resume, cancel, or retry the failed recordings."""
    own_batch(db, bid, user)
    fn = {
        "continue": lambda: batches.continue_run(db, bid, user.email),
        "pause": lambda: batches.pause(db, bid),
        "resume": lambda: batches.resume(db, bid),
        "cancel": lambda: batches.cancel(db, bid),
        "retry": lambda: batches.retry_failed(db, bid),
    }[action]
    out = fn()
    auth.audit(db, user.as_audit(), f"batch.{action}", f"batch:{bid}")
    return BatchActionResult(result=out)


@router.get("/{bid}/results")
def get_batch_results(bid: int, user: CurrentUser, db: Db, key: str | None = None) -> BatchResults:
    """The run's outputs as one table: list-valued fields become one row per item (e.g. every action item)."""
    own_batch(db, bid, user)
    return batches.results(db, bid, key)


@router.get(
    "/{bid}/results.{fmt}",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}, "text/markdown": {}}, "description": "the results table as a file"}},
)
def export_batch_results(bid: int, fmt: Literal["csv", "md"], user: CurrentUser, db: Db, key: str | None = None) -> Response:
    own_batch(db, bid, user)
    return Response(
        batches.export(batches.results(db, bid, key), fmt),
        media_type=EXPORTS[fmt],
        headers={"Content-Disposition": f'attachment; filename="batch-{bid}.{fmt}"'},
    )
