"""The job queue: listing, queueing, cancelling and retrying work, the workers, and a live feed of job changes (SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, AdminReader, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import ingest, jobs, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.jobs import Job, JobList, JobsCreate, JobsQueued, StepQueued, WorkerInfo

router = APIRouter(tags=["jobs"])
log = logging.getLogger("lens")

# which recordings each batch step applies to
STEP_STATUS = {
    "transcribe": ["new", "error"],
    "diarize": ["transcribed"],
    "analyze": ["transcribed", "diarized", "analyzed"],
    "summarize": ["analyzed"],
    "report": ["analyzed"],
}


@router.get("/jobs")
def list_jobs(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    status: str | None = Query(None, description="comma separated, e.g. queued,running"),
    recording: int | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> JobList:
    """Recent jobs with counts by status, plus the latest job's step and log (for the progress bar)."""
    sp = acl.readable()
    rows = jobs.list_jobs(db, sp, status.split(",") if status else None, recording, limit)
    counts = jobs.counts(db, sp)
    latest = (jobs.get(db, rows[0]["id"]) if rows else None) or {}
    return JobList(
        jobs=rows,
        counts=counts,
        running=bool(counts.get("queued") or counts.get("running")),
        step=latest.get("next_step") or (latest.get("steps") or [None])[-1],
        returncode=None if not latest or latest["status"] in jobs.ACTIVE else (0 if latest["status"] == "succeeded" else 1),
        log="\n".join(latest.get("log") or []),
    )


def _job(db: DB, acl: Access, jid: int, role: str = "viewer") -> dict[str, Any]:
    j = jobs.get(db, jid)
    if not j:
        raise HTTPException(404, "not found")
    acl.need(j["space"], role)
    return j


@router.get("/jobs/{jid}")
def get_job(jid: int, acl: Acl, user: CurrentUser, db: Db) -> Job:
    return Job.model_validate(_job(db, acl, jid))


@router.post("/jobs/steps/{step}")
def queue_step(step: str, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> StepQueued:
    """The web app's batch buttons: queue one step for everything that needs it (``transcribe``, ``diarize``,
    ``analyze``, ``summarize``, ``report``), or (admins) ``run``/``scan`` the configured folders."""
    if step in ("run", "scan"):
        if not user.admin:
            raise HTTPException(403, "admins only")

        def scan() -> None:
            try:
                ingest.scan(db, cfg, log=log.info)
                jobs.enqueue_pending(db, by=user.email)
            except Exception:  # noqa: BLE001 - background thread; report and carry on
                log.exception("scan failed")

        threading.Thread(target=scan, daemon=True).start()
        return StepQueued(queued="scanning")
    if step not in STEP_STATUS:
        raise HTTPException(404, f"steps: run, scan, {', '.join(STEP_STATUS)}")
    where = "space IN $sp AND status IN $st" + (" AND source = 'audio'" if step in ("transcribe", "diarize") else "")
    rows = db.rows(f"SELECT record::id(id) AS id FROM recording WHERE {where}", sp=acl.editable(), st=STEP_STATUS[step])
    queued = [jobs.enqueue(db, r["id"], [step], by=user.email) for r in rows]
    return StepQueued(queued=len(queued))


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    _job(db, acl, jid, "editor")
    with domain_errors():
        jobs.cancel(db, jid)
    return Ok()


@router.post("/jobs/{jid}/retry")
def retry_job(jid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    _job(db, acl, jid, "editor")
    with domain_errors():
        jobs.retry(db, jid)
    return Ok()


@router.post("/jobs")
def create_jobs(body: JobsCreate, acl: Acl, user: Writer, db: Db) -> JobsQueued:
    """Queue recordings (these steps, a pipeline, or their namespace's pipeline) and/or everything pending in a namespace."""
    out: list[int] = []
    for rid in body.recordings:
        acl.recording(rid, "editor")
        try:
            out.append(jobs.enqueue(db, rid, body.steps or None, by=user.email, pipeline=body.pipeline))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from None
    if body.namespace:
        sid = acl.namespace(body.namespace, "editor")
        out += jobs.enqueue_pending(db, sid, by=user.email)
    return JobsQueued(jobs=out)


@router.get("/workers")
def list_workers(user: AdminReader, db: Db) -> list[WorkerInfo]:
    return db.rows("SELECT record::id(id) AS name, steps, host, heartbeat_at, current FROM worker")


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "`event: job` with the job as JSON data"}},
)
async def stream_events(request: Request, user: CurrentUser, since: str = "", once: bool = False) -> StreamingResponse:
    """Server-sent events: one ``job`` event per job change in namespaces you can read, from ``since`` (default: now).
    ``once=true`` sends what has changed and closes. Read it with fetch (it needs the Authorization header)."""
    db = request.app.state.db
    spaces = None if user.admin else set(user.roles)

    async def gen() -> AsyncIterator[str]:
        # A comment first, so clients (and proxies that hold headers until the first byte) see the stream open now
        # rather than at the first change or keep-alive.
        yield ": open\n\n"
        last = since or store.now()
        quiet = 0
        while True:
            rows = await run_in_threadpool(jobs.changes, db, last, spaces)
            for r in rows:
                last = max(last, r.get("updated_at") or last)
                yield f"event: job\ndata: {json.dumps(r, default=str)}\n\n"
            if once:
                break
            quiet = 0 if rows else quiet + 1
            if quiet >= 15:
                quiet = 0
                yield ": keep-alive\n\n"
            if await request.is_disconnected():
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    )
