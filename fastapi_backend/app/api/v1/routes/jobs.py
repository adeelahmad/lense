"""The job queue: listing, queueing, cancelling and retrying work, the workers, and a live feed of job changes (SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, ingest, jobs, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.jobs import Job, JobList, JobLog, JobsCreate, JobsQueued, StepQueued, WorkerInfo
from app.schemas.routines import HeldRunDecision

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
    batch: int | None = Query(None, description="only this batch run's jobs"),
    namespace: str | None = Query(None, description="only this namespace's jobs (404 when you can't read it)"),
    limit: int = Query(100, ge=1, le=2000),
) -> JobList:
    """Recent jobs, newest first, with counts by status and by namespace (of the jobs in `namespace` and `batch`, of
    any status), plus the latest job's step and log (for the progress bar). With `recording`, that recording's runs,
    also for someone who sees it through a role on its collection (then the counts are of those runs only)."""
    statuses = status.split(",") if status else None
    rec = acl.recording(recording) if recording is not None and not namespace else None
    if rec and not auth.allows(acl.roles, rec["space"]):
        rows = jobs.list_jobs(db, {rec["space"]}, statuses, recording, limit, batch)
        counts: dict[str, int] = dict(Counter(r["status"] for r in rows))
        by_ns: dict[str, int] = {}
    else:
        sp = {acl.namespace(namespace)} if namespace else acl.readable()
        rows = jobs.list_jobs(db, sp, statuses, recording, limit, batch)
        counts = jobs.counts(db, sp, batch)
        names = store.space_names(db)
        by_ns = {names[k]: n for k, n in jobs.per_space(db, acl.readable(), batch).items() if k in names}
    latest = (jobs.get(db, rows[0]["id"]) if rows else None) or {}
    return JobList(
        jobs=rows,
        counts=counts,
        namespaces=by_ns,
        running=bool(counts.get("queued") or counts.get("running")),
        step=latest.get("next_step") or (latest.get("steps") or [None])[-1],
        returncode=None if not latest or latest["status"] in jobs.ACTIVE else (0 if latest["status"] == "succeeded" else 1),
        log="\n".join(latest.get("log") or []),
    )


def _job(db: DB, acl: Access, jid: int, role: str = "viewer") -> dict[str, Any]:
    """A run, for someone with this role on its recording (through the namespace, or the recording's collection)."""
    j = jobs.get(db, jid)
    if not j:
        raise HTTPException(404, "not found")
    rec = db.one("SELECT collection FROM $r", r=store.R("recording", j["recording"])) if j.get("recording") is not None else None
    acl.need_in(j["space"], (rec or {}).get("collection"), role)
    return j


@router.get("/jobs/{jid}")
def get_job(jid: int, acl: Acl, user: CurrentUser, db: Db) -> Job:
    """One run: its steps and how each went (`step_runs`), the pipeline it runs, its last log lines, and how long its
    steps usually take on this recording (`estimates`, `eta_seconds`)."""
    j = _job(db, acl, jid)
    est = jobs.estimates(db, j)
    return Job.model_validate({**j, "estimates": est, "eta_seconds": jobs.eta(j, est)})


@router.get("/jobs/{jid}/log")
def get_job_log(
    jid: int,
    acl: Acl,
    user: CurrentUser,
    db: Db,
    after: int = Query(0, ge=0, description="start at this line (0-based)"),
    limit: int = Query(1000, ge=1, le=5000),
) -> JobLog:
    """A run's whole log, a page at a time: up to `limit` lines from line `after`, and how many there are. Runs from
    before whole logs were kept have their last 200 lines."""
    _job(db, acl, jid)
    lines, total = jobs.log_lines(db, jid, after, limit)
    return JobLog(start=after, lines=lines, total=total, more=after + len(lines) < total)


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


@router.post("/jobs/{jid}/release")
def release_job(jid: int, body: HeldRunDecision, acl: Acl, user: AdminWriter, db: Db) -> Ok:
    """Pick for a job held over a budget (docs/budgets.md): run it now, once, whatever its budgets say, or skip it
    (admins, who set the budgets)."""
    _job(db, acl, jid, "editor")
    with domain_errors():
        jobs.release(db, jid, body.run, user.email)
    auth.audit(db, user.as_audit(), "job.release" if body.run else "job.skip", f"job:{jid}")
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


def _worker(w: dict[str, Any]) -> WorkerInfo:
    draining = bool(w.get("drain")) and w.get("current") is not None  # only while it has a run to hand back
    return WorkerInfo(**{**w, "paused": bool(w.get("paused")), "draining": draining, "components": w.get("components") or {}})


@router.get("/workers")
def list_workers(user: AdminReader, db: Db) -> list[WorkerInfo]:
    """Every worker that has checked in: the steps it runs, its heartbeat and load, and whether it's paused."""
    return [_worker(w) for w in jobs.workers(db)]


@router.post("/workers/{name}/{action}")
def control_worker(name: str, action: Literal["pause", "drain", "resume"], user: AdminWriter, db: Db) -> WorkerInfo:
    """``pause``: the worker takes no new runs; the one it has carries on to the end. ``drain``: it also hands that run
    back to the queue after the step it's on, so another worker carries on, and stays paused. ``resume``: it takes runs
    again. A paused worker stays paused when it restarts under the same name. Audited as ``worker.<action>``."""
    with domain_errors():
        w = jobs.control(db, name, action, user.email)
    auth.audit(db, user.as_audit(), f"worker.{action}", f"worker:{name}", None)
    return _worker(w)


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "`event: job` with the job as JSON data; with `logs`, `event: log` with {job, start, lines}",
        }
    },
)
async def stream_events(
    request: Request,
    user: CurrentUser,
    since: str = "",
    once: bool = False,
    logs: int | None = Query(None, description="follow this job only, with its new log lines as `log` events"),
) -> StreamingResponse:
    """Server-sent events: one ``job`` event per job change in namespaces you can read, from ``since`` (default: now).
    ``once=true`` sends what has changed and closes. ``logs=<job>`` follows that one job: its changes, and each batch of
    new log lines as a ``log`` event ``{job, start, lines}`` (``start`` numbers the first line, so a client can tell
    an overlap or a gap; the first one carries up to the last 200 lines). Read it with fetch (it needs the
    Authorization header)."""
    db = request.app.state.db
    spaces = None if user.admin else set(user.roles)
    if logs is not None:
        j = await run_in_threadpool(jobs.get, db, logs)
        if not j:
            raise HTTPException(404, "not found")
        if spaces is not None and j["space"] not in spaces:
            # someone who sees the job's recording through a role on its collection follows just that job
            rec = await run_in_threadpool(db.one, "SELECT collection FROM $r", r=store.R("recording", j.get("recording") or 0))
            cid = (rec or {}).get("collection")
            if cid is None or not user.collections.get(j["space"], {}).get(cid):
                raise HTTPException(404, "not found")
            spaces = {j["space"]}

    async def gen() -> AsyncIterator[str]:
        # A comment first, so clients (and proxies that hold headers until the first byte) see the stream open now
        # rather than at the first change or keep-alive.
        yield ": open\n\n"
        last = since or store.now()
        quiet = 0
        sent: int | None = None  # log lines of the followed job sent so far
        while True:
            rows = await run_in_threadpool(jobs.changes, db, last, spaces, logs)
            for r in rows:
                last = max(last, r.get("updated_at") or last)
                yield f"event: job\ndata: {json.dumps(r, default=str)}\n\n"
            if logs is not None and (rows or sent is None):
                total = int(((await run_in_threadpool(jobs.get, db, logs)) or {}).get("log_total") or 0)
                start = max(0, total - jobs.LOG_TAIL) if sent is None else sent
                if total > start:
                    lines, _ = await run_in_threadpool(jobs.log_lines, db, logs, start, 5000)
                    yield f"event: log\ndata: {json.dumps({'job': logs, 'start': start, 'lines': lines})}\n\n"
                sent = max(start, total)
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
