"""Schedules (docs/schedules.md): everything in Lens that runs on a clock, in one list, like a crontab. A schedule is
one of:

- a routine (`routine:<id>`): its cron schedule and time zone, or none when it runs only by hand (routines.py);
- an automation's schedule or at trigger (`automation:<id>:<trigger>`): the timer that fires it (timers.py);
- the scheduled backup (`backups`): backups.schedule (backups.py).

Each comes as a row saying when it runs in words, when it runs next and last, how the last run went and whether it is
on. Nothing is kept here: changing a schedule changes the routine, automation or setting it belongs to, through that
one's own checks, so the list is always what Lens will do.
"""

from __future__ import annotations

import datetime as dt
import re

from . import automations, backups, routines, schedule, timers

KINDS = ("routine", "automation", "backups")
ID_RX = re.compile(r"^(?:routine:(\d+)|automation:(\d+):([a-z][a-z0-9_]{0,39})|backups)$")
CLOCK = ("schedule", "at")  # an automation's triggers that run on a clock


def parse_id(sid):
    """(kind, id, trigger) from a schedule's id: routine:3, automation:4:morning or backups; KeyError otherwise."""
    m = ID_RX.match(sid or "")
    if not m:
        raise KeyError(sid)
    if m.group(1):
        return "routine", int(m.group(1)), None
    if m.group(2):
        return "automation", int(m.group(2)), m.group(3)
    return "backups", None, None


# ---------- one row per schedule ----------
def routine_row(r):
    """A routine (routines.get's view) as a schedule."""
    on = bool(r.get("enabled"))
    return {
        "id": f"routine:{r['id']}",
        "kind": "routine",
        "name": r["name"],
        "trigger": None,
        "on": "schedule" if r.get("schedule") else "manual",
        "cron": r.get("schedule"),
        "at": None,
        "timezone": r.get("timezone") or "UTC",
        "text": r.get("schedule_text") or "only when run by hand",
        "enabled": on,
        "state": "running" if r.get("running") else ("on" if on and r.get("schedule") else "off"),
        "next_run_at": r.get("next_run_at") if on and r.get("schedule") else None,
        "last_run_at": r.get("last_run_at"),
        "last_status": r.get("last_status"),
        "running": bool(r.get("running")),
        "href": f"/routines/{r['id']}",
        "namespace_names": r.get("namespace_names"),
        "note": None if r.get("schedule") else "Runs only when someone presses Run now; give it a schedule to run on its own.",
    }


def _last_run(db, aid, alias, timer):
    """How an automation's clock trigger last went: its newest run, else the timer's last firing (a run its If
    skipped or a limit stopped leaves no run)."""
    run = db.one(
        "SELECT id, status, started_at FROM automation_run WHERE automation = $a AND trigger = $t ORDER BY id DESC LIMIT 1",
        a=aid,
        t=alias,
    )
    last = (timer or {}).get("last") or {}
    fired = last.get("fired_at")
    if run and (not fired or (run.get("started_at") or "") >= fired[:19]):
        return run.get("started_at"), run.get("status")
    if fired:
        return fired, "done" if last.get("ok", True) else "failed"
    return None, None


def automation_rows(db, a, now=None):
    """An automation's schedule and at triggers (automations.view), one row each."""
    out = []
    clock = [t for t in a.get("triggers") or [] if t.get("on") in CLOCK]
    if not clock:
        return out
    states = timers.clock_state(db, a, now)
    for t in clock:
        alias = t["as"]
        st = states.get(alias) or {"status": "off", "at": None}
        key = f"{'sched' if t['on'] == 'schedule' else 'at'}-{a['id']}-{alias}"
        last_at, last_status = _last_run(db, a["id"], alias, timers.get(db, key))
        tz = t.get("timezone") or "UTC"
        if t["on"] == "schedule":
            text = schedule.describe(t["cron"])
        else:
            text = f"once, on {timers.local(st['at'], tz, now)}" if st.get("at") else "once"
        note = {
            "done": "Its time has come and gone; give it a new time to run again.",
            "missed": "Lens was off when it was due.",
            "failed": "It couldn't be set; save the automation again.",
        }.get(st["status"])
        out.append(
            {
                "id": f"automation:{a['id']}:{alias}",
                "kind": "automation",
                "name": a["name"],
                "trigger": alias,
                "on": t["on"],
                "cron": t.get("cron"),
                "at": t.get("at"),
                "timezone": tz,
                "text": text,
                "enabled": bool(a.get("enabled")),
                "state": st["status"],
                "next_run_at": st["at"] if st["status"] == "waiting" else None,
                "last_run_at": last_at,
                "last_status": last_status,
                "running": False,
                "href": f"/automations/{a['id']}",
                "namespace_names": a.get("namespace_names"),
                "note": note,
            }
        )
    return out


def backup_row(db, cfg):
    """The scheduled backup (Settings → Backups) as a schedule: off while backups.schedule is empty."""
    b = (cfg or {}).get("backups") or {}
    st = backups.schedule_state(db)
    cron = b.get("schedule") or None
    nxt = (st.get("at") if st.get("plan") == backups.plan(cfg) else backups.next_at(cfg)) if cron else None
    return {
        "id": "backups",
        "kind": "backups",
        "name": "Back up the database",
        "trigger": None,
        "on": "schedule" if cron else "manual",
        "cron": cron,
        "at": None,
        "timezone": b.get("timezone") or "UTC",
        "text": schedule.describe(cron) if cron else "not scheduled",
        "enabled": bool(cron),
        "state": "on" if cron else "off",
        "next_run_at": nxt,
        "last_run_at": st.get("last_at"),
        "last_status": ("failed" if st.get("last_error") else "done") if st.get("last_at") else None,
        "running": False,
        "href": "/settings/backups",
        "namespace_names": None,
        "note": st.get("last_error") or (None if cron else f"Keeps the newest {b.get('keep') or 7} once it has a schedule."),
    }


def _order(row):
    """Soonest first; then what has no next time (off, by hand, done), by kind and name."""
    return (row["next_run_at"] is None, row["next_run_at"] or "", KINDS.index(row["kind"]), row["name"].lower(), row["id"])


def listing(db, cfg, admin=False, editable=None, now=None):
    """Every schedule someone may manage: admins every routine, the scheduled backup and every automation's clock
    triggers; anyone else the clock triggers of the automations they manage (those in namespaces they edit)."""
    rows = []
    if admin:
        rows += [routine_row(r) for r in routines.list_routines(db)]
        rows.append(backup_row(db, cfg))
    for a in automations.list_automations(db, None if admin else sorted(editable or [])):
        rows += automation_rows(db, a, now)
    return sorted(rows, key=_order)


def one(db, cfg, sid, admin=False, editable=None, now=None):
    """One schedule by its id, if this person manages it; KeyError otherwise (so ids don't leak)."""
    kind, xid, alias = parse_id(sid)
    if kind in ("routine", "backups") and not admin:
        raise KeyError(sid)
    if kind == "routine":
        return routine_row(routines.get(db, xid))
    if kind == "backups":
        return backup_row(db, cfg)
    a = automations.get(db, xid)
    if not automations.manages(a, editable or [], admin):
        raise KeyError(sid)
    got = next((r for r in automation_rows(db, a, now) if r["trigger"] == alias), None)
    if got is None:
        raise KeyError(sid)
    return got


# ---------- changing one ----------
def retimed(a, alias, cron=None, at=None, timezone=None):
    """An automation's triggers with one schedule or at trigger moved: a new cron (schedule) or time (at), and/or time
    zone. The rest are kept as they are; automations.update checks the result."""
    out, found = [], False
    for t in a.get("triggers") or []:
        t = dict(t)
        if t.get("as") == alias and t.get("on") in CLOCK:
            found = True
            if cron is not None:
                if t["on"] != "schedule":
                    raise ValueError(f"trigger {alias} runs once at a time: change its time (at), not a cron")
                t["cron"] = cron
            if at is not None:
                if t["on"] != "at":
                    raise ValueError(f"trigger {alias} repeats: change its schedule (cron), not a time")
                t["at"] = at
            if timezone is not None:
                t["timezone"] = timezone
        out.append(t)
    if not found:
        raise KeyError(alias)
    return out


def run_automation(db, cfg, aid, alias, by=None, over_budget=False, now=None):
    """Run an automation now as its clock trigger `alias` would: on the clock.tick its time makes, here and now.
    Limits and budgets apply as to any run. {run, status, why}."""
    a = automations.get(db, aid)
    t = next((x for x in a["triggers"] if x["as"] == alias and x.get("on") in CLOCK), None)
    if t is None:
        raise KeyError(alias)
    when = now or dt.datetime.now(dt.timezone.utc)
    tick = timers.tick(aid, alias, when, t.get("timezone") or "UTC", t.get("cron"), a.get("spaces"))
    got = automations.run(db, cfg, aid, alias, tick, by=by, over_budget=over_budget)
    return {"run": got["run"], "status": got["status"], "why": got["why"]}


def backup_settings(given, had=None):
    """The backups settings a change ({cron, timezone, enabled}, as given) makes: switched off, or with no cron, there
    is no schedule; switched on, it needs one. settings.save checks the values."""
    out = {}
    if given.get("enabled") is False or ("cron" in given and not given["cron"]):
        out["schedule"] = None
    elif given.get("cron"):
        out["schedule"] = given["cron"]
    elif given.get("enabled") and not had:
        raise ValueError("give the backup a schedule (cron), like 0 3 * * * for every night at 03:00, to switch it on")
    if given.get("timezone") is not None:
        out["timezone"] = given["timezone"] or "UTC"
    return out
