"""Routines (admins): syncs, pipelines and workflows run on a schedule. Graph changes (namespace editors): what
graph-organising workflows proposed or made, to accept, dismiss or undo."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, AdminReader, AdminWriter, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, organize, routines, schedule
from app.schemas.common import Created, Ok
from app.schemas.routines import (
    GraphChange,
    GraphChangeAccept,
    Routine,
    RoutineCatalog,
    RoutineCreate,
    RoutineRun,
    RoutineRunRequest,
    RoutineUpdate,
    SchedulePreview,
    Undone,
)

router = APIRouter(tags=["routines"])


@router.get("/routines")
def list_routines(user: AdminReader, db: Db) -> RoutineCatalog:
    return RoutineCatalog(routines=routines.list_routines(db), actions=list(routines.ACTIONS), recordings=list(routines.PICKS))


@router.get("/routines/schedule")
def preview_schedule(user: CurrentUser, schedule_expr: str = Query(alias="schedule"), timezone: str = "UTC") -> SchedulePreview:
    """What a schedule means and the next five times it runs, to check one before saving it."""
    with domain_errors():
        schedule.check(schedule_expr, timezone)
        times, t = [], None
        for _ in range(5):
            t = schedule.next_after(schedule_expr, timezone, t)
            if t is None:
                break
            times.append(routines._iso(t))
    return SchedulePreview(schedule=schedule_expr, timezone=timezone, text=schedule.describe(schedule_expr), next=times)


@router.post("/routines")
def create_routine(body: RoutineCreate, user: AdminWriter, db: Db) -> Created:
    with domain_errors():
        rid = routines.create(
            db, body.name, body.actions, body.schedule, body.timezone, body.namespaces, body.description, body.enabled, user.email
        )
    auth.audit(db, user.as_audit(), "routine.create", f"routine:{rid}")
    return Created(id=rid)


@router.get("/routines/{rid}")
def get_routine(rid: int, user: AdminReader, db: Db) -> Routine:
    with domain_errors():
        return routines.get(db, rid)


@router.patch("/routines/{rid}")
def update_routine(rid: int, body: RoutineUpdate, user: AdminWriter, db: Db) -> Ok:
    """Change what is sent: `schedule: null` makes it a routine run only by hand, `namespaces: null` runs it over
    every namespace."""
    with domain_errors():
        changes = {
            k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k in ("schedule", "namespaces", "description")
        }
        routines.update(db, rid, **changes)
    auth.audit(db, user.as_audit(), "routine.update", f"routine:{rid}")
    return Ok()


@router.delete("/routines/{rid}")
def delete_routine(rid: int, user: AdminWriter, db: Db) -> Ok:
    with domain_errors():
        routines.remove(db, rid)
    auth.audit(db, user.as_audit(), "routine.delete", f"routine:{rid}")
    return Ok()


@router.post("/routines/{rid}/run")
def run_routine(rid: int, body: RoutineRunRequest, user: AdminWriter, db: Db) -> Ok:
    """Run it as soon as the scheduler next looks (within half a minute), even when it is off."""
    with domain_errors():
        routines.request_run(db, rid, user.email, body.propose_only)
    auth.audit(db, user.as_audit(), "routine.run", f"routine:{rid}")
    return Ok()


@router.get("/routines/{rid}/runs")
def list_runs(rid: int, user: AdminReader, db: Db, limit: int = Query(20, ge=1, le=200)) -> list[RoutineRun]:
    with domain_errors():
        routines.get(db, rid)
    return routines.runs(db, rid, limit)


@router.get("/routine-runs/{run_id}")
def get_run(run_id: int, user: AdminReader, db: Db) -> RoutineRun:
    with domain_errors():
        return routines.get_run(db, run_id)


@router.post("/routine-runs/{run_id}/undo")
def undo_run(run_id: int, user: AdminWriter, db: Db) -> Undone:
    """Take back every graph change the run made."""
    with domain_errors():
        routines.get_run(db, run_id)
        n, failed = organize.undo_run(db, run_id, user.email)
    auth.audit(db, user.as_audit(), "routine.undo_run", f"routine_run:{run_id}", {"undone": n, "failed": failed})
    return Undone(undone=n, failed=failed)


# ---------- graph changes ----------
def _change(db, acl, cid: int, role: str = "viewer") -> dict:
    try:
        ch = organize.get_change(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    for s in ch.get("spaces") or []:
        acl.need(s, role)
    return ch


@router.get("/graph-changes")
def list_graph_changes(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    status: Literal["proposed", "applied", "dismissed", "undone"] | None = None,
    run: int | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[GraphChange]:
    """Changes to the entities of namespaces you can read, newest first."""
    spaces = sorted(acl.roles) if not user.admin else sorted(db.values("SELECT VALUE record::id(id) FROM space"))
    return organize.list_changes(db, spaces, status, run, limit)


@router.post("/graph-changes/{cid}/accept")
def accept_graph_change(cid: int, body: GraphChangeAccept, user: Writer, acl: Acl, db: Db) -> Ok:
    _change(db, acl, cid, "editor")
    with domain_errors():
        organize.accept(db, cid, user.email, body.keep)
    auth.audit(db, user.as_audit(), "graph_change.accept", f"graph_change:{cid}")
    return Ok()


@router.post("/graph-changes/{cid}/dismiss")
def dismiss_graph_change(cid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    _change(db, acl, cid, "editor")
    with domain_errors():
        organize.dismiss(db, cid, user.email)
    auth.audit(db, user.as_audit(), "graph_change.dismiss", f"graph_change:{cid}")
    return Ok()


@router.post("/graph-changes/{cid}/undo")
def undo_graph_change(cid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    _change(db, acl, cid, "editor")
    with domain_errors():
        organize.undo(db, cid, user.email)
    auth.audit(db, user.as_audit(), "graph_change.undo", f"graph_change:{cid}")
    return Ok()
