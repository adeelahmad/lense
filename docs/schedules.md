# Schedules

Status: built. The Schedules page (Automate › Automations › Schedules) is Lens's crontab: everything that runs on a
clock, in one list, soonest first, with a switch, Run now and a way to change when it runs. Agents get the same list and
the same changes over the API and MCP. It is a tab of Automations, which has the one nav row for both (and where
Routines was), so the nav still fits a phone screen; for admins, the [Routines](processing.md#routines) page (the list,
proposed graph changes and history) and New routine are at the top of both tabs.

## What is on it

| Row | What it is | Who sees it |
| --- | --- | --- |
| A routine | its schedule (five-field cron, in a time zone), or "only when run by hand" ([Routines](processing.md#routines)) | admins |
| An automation's schedule or at trigger | one row per trigger that runs on the clock: "every weekday at 07:00", or "once, on Mon 2 Nov at 07:30" ([Time](automations.md#time)) | admins, and the editors who manage the automation |
| The backup | Settings › Backups' schedule, or "not scheduled" ([Backups](database.md#backups)) | admins |

Each row says when it runs in words and as cron with its time zone, when it runs next, how its last run went and when,
and whether it is on. Its name opens the routine, the automation or Settings › Backups. An automation's other triggers
(something happening, a webhook, by hand) aren't on the clock, so they stay on the automation's own page.

Nothing is kept for the list: it is read from the routines, the automations' timers and the backup setting each time,
so it always says what Lens will do.

## Changing one

- **The switch** turns a routine or the backup on or off. An automation's switch turns the whole automation on or
  off, whatever starts it. The backup has no off of its own: off clears its schedule, and switching it on asks for one.
- **Change when it runs** (the pencil) picks every hour, every day at 03:00, weekdays at 09:00, or a cron of your own,
  in any time zone, and shows the next five times before you save; Enter saves. A routine can also run only by hand
  and the backup can have no schedule. An at trigger takes a new date and time.
- **Run now** runs it even when it's off: a routine when the scheduler next looks (within half a minute), an
  automation here and now as its trigger would when its time comes (its If, limits and budgets apply, and actions
  that ask first wait for your pick), the backup now. A routine over one of its budgets asks before it runs anyway.

Every change goes through the routine's, the automation's or the setting's own checks and audit log, so a schedule
that never runs, an unknown time zone or an automation its person can no longer run is refused with the reason.

## For agents

```
GET    /api/v1/schedules
GET    /api/v1/schedules/{id}
PATCH  /api/v1/schedules/{id}        {cron?, at?, timezone?, enabled?}
POST   /api/v1/schedules/{id}/run    {over_budget?}
```

A schedule's id says what it is: `routine:3`, `automation:4:morning` (the automation and its trigger's `as`) or
`backups`. `cron: null` runs a routine only by hand and stops scheduled backups. Over MCP, `schedules` lists them
(optionally one kind), `set_schedule` changes one (`by_hand: true` takes the schedule away) and `run_schedule` runs
one; the two that change something need a token with the write scope. Someone who isn't an admin gets 404 for a
routine or the backup, and for an automation they don't manage, so ids don't leak.

## Refine later

- Add a schedule to an automation that has none, from this page.
- The assistant's own tools for schedules (agents use MCP today).
- Other things that repeat on their own clock (source polling, sensor digests, update checks) as read-only rows.
