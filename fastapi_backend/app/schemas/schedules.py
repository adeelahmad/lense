"""Schedules (docs/schedules.md): everything that runs on a clock, in one list: routines, automations' schedule and at
triggers, and the scheduled backup."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class Schedule(ResponseModel):
    id: str = Field(description="routine:<id>, automation:<id>:<trigger> or backups")
    kind: Literal["routine", "automation", "backups"]
    name: str = Field(description="the routine's or automation's name")
    trigger: str | None = Field(None, description="an automation's trigger (its `as`)")
    on: Literal["schedule", "at", "manual"] = Field(
        description="schedule: repeats on a cron; at: once, at a time; manual: no schedule (a routine run by hand, no backups)"
    )
    cron: str | None = Field(None, description="five-field cron (minute hour day month weekday) or @daily...")
    at: str | None = Field(None, description="an at trigger's time (ISO 8601, in its time zone when it names none)")
    timezone: str = "UTC"
    text: str = Field(description="when it runs, in words: every weekday at 07:00, once on Fri 16 Oct at 07:00, ...")
    enabled: bool = Field(description="whether it runs on its own (for an automation: the whole automation is on)")
    state: str = Field(
        description="on, off or running (routines, the backup); waiting, done (an at trigger whose time came), missed, "
        "failed or off (automations)"
    )
    next_run_at: str | None = Field(None, description="when it runs next (UTC); none while it's off or has no schedule")
    last_run_at: str | None = None
    last_status: str | None = Field(None, description="how its last run went (done, error, failed, partly, skipped, ...)")
    running: bool = False
    href: str = Field(description="its page in the web app")
    namespace_names: list[str | None] | None = Field(None, description="the namespaces it works in; none for every one")
    note: str | None = Field(None, description="something to know about it: why it doesn't run, its last error")


class ScheduleCatalog(ResponseModel):
    schedules: list[Schedule]


class ScheduleChange(RequestModel):
    cron: str | None = Field(
        None,
        description="a new schedule (five-field cron or @daily...); null for a routine runs it only by hand, and for the "
        "backup stops scheduled backups",
    )
    at: str | None = Field(None, description="a new time for an automation's at trigger, like 2026-11-02T07:30")
    timezone: str | None = Field(None, description="a time zone, like Europe/London")
    enabled: bool | None = Field(
        None, description="switch it on or off: an automation's whole automation; the backup off clears its schedule"
    )


class ScheduleRun(RequestModel):
    over_budget: bool = Field(False, description="run it even though it's over a budget (docs/budgets.md)")


class ScheduleStarted(ResponseModel):
    status: str = Field(
        description="asked (a routine starts within half a minute), done, waiting, partly, failed, held (over a budget), "
        "skipped (its If was false), limited or off (automations), or done for a backup"
    )
    why: str | None = Field(None, description="why it didn't run, or was held")
    run: int | None = Field(None, description="an automation's run (GET /api/v1/automations/runs/{run})")
    backup: str | None = Field(None, description="the backup it took")
