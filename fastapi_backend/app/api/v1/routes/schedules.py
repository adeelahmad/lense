"""Schedules (docs/schedules.md): everything that runs on a clock, in one list, like a crontab: routines (admins),
automations' schedule and at triggers (the automations you manage) and the scheduled backup (admins). Changing or
running one goes through its routine's, automation's or setting's own route, with its checks and audit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.v1.routes import admin as admin_routes
from app.api.v1.routes import automations as automation_routes
from app.api.v1.routes import routines as routine_routes
from app.api.v1.routes import updates as update_routes
from app.domain import auth, schedules
from app.schemas.automations import AutomationSwitch, AutomationUpdate
from app.schemas.routines import RoutineRunRequest, RoutineUpdate
from app.schemas.schedules import Schedule, ScheduleCatalog, ScheduleChange, ScheduleRun, ScheduleStarted

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _editable(db: Any, user: Principal, acl: Any) -> list[int]:
    return automation_routes._editable(db, user, acl)


def _one(db: Any, cfg: Any, user: Principal, acl: Any, sid: str) -> dict[str, Any]:
    """The schedule, if this person manages it (404 otherwise, so ids don't leak)."""
    try:
        return schedules.one(db, cfg, sid, user.admin, _editable(db, user, acl))
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.get("")
def list_schedules(user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> ScheduleCatalog:
    """Everything that runs on a clock that you manage, soonest first: for admins every routine (those run only by
    hand too) and the scheduled backup; for everyone the schedule and at triggers of their automations. Each with
    when it runs in words, its next and last run, how that went and whether it's on."""
    return ScheduleCatalog(schedules=[Schedule(**r) for r in schedules.listing(db, cfg, user.admin, _editable(db, user, acl))])


@router.get("/{sid}")
def get_schedule(sid: str, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> Schedule:
    return Schedule(**_one(db, cfg, user, acl, sid))


@router.patch("/{sid}")
def change_schedule(sid: str, body: ScheduleChange, request: Request, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Schedule:
    """Change when it runs or switch it on or off; give only what changes. A routine's `cron: null` runs it only by
    hand. Switching an automation's schedule switches the whole automation (every trigger it has). Switching the
    backup off clears backups.schedule; switching it on needs a cron."""
    row = _one(db, cfg, user, acl, sid)
    given = body.model_dump(exclude_unset=True)
    if not given:
        raise HTTPException(400, "say what changes: cron, at, timezone or enabled")
    kind, xid, alias = schedules.parse_id(sid)
    with domain_errors():
        if kind == "routine":
            if "at" in given:
                raise ValueError("a routine repeats on a schedule (cron); it has no single time")
            changes = {"schedule": given["cron"]} if "cron" in given else {}
            changes |= {k: given[k] for k in ("timezone", "enabled") if given.get(k) is not None}
            routine_routes.update_routine(xid, RoutineUpdate(**changes), user, db)
        elif kind == "automation":
            if any(given.get(k) is not None for k in ("cron", "at", "timezone")):
                a = automation_routes._mine(db, user, acl, xid)
                triggers = schedules.retimed(a, alias, given.get("cron"), given.get("at"), given.get("timezone"))
                automation_routes.update_automation(xid, AutomationUpdate(triggers=triggers), user, acl, db, cfg)
            elif "cron" in given or "at" in given:
                raise ValueError("an automation's schedule needs a cron (or an at trigger a time); switch it off instead")
            if given.get("enabled") is not None:
                automation_routes.enable_automation(xid, AutomationSwitch(on=given["enabled"]), user, acl, db, cfg)
        else:
            if "at" in given:
                raise ValueError("the backup repeats on a schedule (cron); it has no single time")
            changes = schedules.backup_settings(given, row.get("cron"))
            if changes:
                admin_routes.update_settings("backups", changes, user, request, db)
    return Schedule(**_one(db, request.app.state.settings.current(), user, acl, sid))


@router.post("/{sid}/run")
def run_schedule(sid: str, user: Writer, acl: Acl, db: Db, cfg: Cfg, body: ScheduleRun | None = None) -> ScheduleStarted:
    """Run it now, even when it's off: a routine as soon as the scheduler next looks (within half a minute), an
    automation here and now as its trigger would when its time comes, the backup now (it can take a while). A run over
    a budget is refused (routines: 409) or held for your pick (automations) unless over_budget is true."""
    body = body or ScheduleRun()
    _one(db, cfg, user, acl, sid)
    kind, xid, alias = schedules.parse_id(sid)
    if kind == "routine":
        routine_routes.run_routine(xid, RoutineRunRequest(over_budget=body.over_budget), user, db, cfg)
        return ScheduleStarted(status="asked", why="it starts within half a minute")
    if kind == "automation":
        with domain_errors():
            got = schedules.run_automation(db, cfg, xid, alias, user.email, body.over_budget)
        auth.audit(db, user.as_audit(), "automation.run", f"automation:{xid}", {"run": got["run"], "trigger": alias})
        return ScheduleStarted(**got)
    taken = update_routes.take_backup(user, db, cfg)
    return ScheduleStarted(status="done", backup=taken.name)
