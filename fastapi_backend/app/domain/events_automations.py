"""Automations as activities (docs/automations.md, Runs): the `automations` source reads the runs automations kept
(the `automation_run` table) as `automation.ran` activities, by `finished_at` with the id breaking ties, so nothing is
stored twice; and `automation.limited`, emitted at most once an hour when an automation is over one of its limits.

A run's activity id is `ran-automation_run-<id>`; its actor is the automation (Application `lens:automation/<id>`,
by its name), its object the run with its status and what its actions did, and its `lens.cause` what started it
when that was an automation's run too, so a chain of automations can be followed back.
"""

from __future__ import annotations

import re

from . import events, store

R = store.R
STATUSES = ["done", "partly", "failed", "waiting"]  # a run that ran its graph (held, skipped or running ones didn't)
FIELDS = "id AS rid, record::id(id) AS id, automation, name, status, counts, space, finished_at, started_at, trigger, event, cause"
RAN = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "title": "Object type", "enum": ["Activity"]},
        "id": {"type": "string", "title": "Run"},
        "name": {"type": "string", "title": "Run"},
        "url": {"type": "string", "title": "Link"},
        "status": {"type": "string", "title": "Status", "enum": STATUSES},
        "trigger": {"type": "string", "title": "Trigger"},
        "event": {"type": "string", "title": "What started it"},
        "counts": {
            "type": "object",
            "title": "What its actions did",
            "properties": {s: {"type": "integer", "title": s.title()} for s in ("done", "waiting", "scheduled", "failed")},
        },
    },
}
ACTOR = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "title": "Actor type", "enum": ["Application"]},
        "id": {"type": "string", "title": "Automation"},
        "name": {"type": "string", "title": "Automation's name"},
    },
}

events.kind(
    "automation.ran",
    label="Automation ran",
    source="automations",
    as_type="Announce",
    object_type="Activity",
    fields={"object": RAN, "actor": ACTOR},
    sample={
        "id": "ran-automation_run-51",
        "type": "Announce",
        "kind": "automation.ran",
        "published": "2026-10-15T16:22:01.000Z",
        "actor": {"type": "Application", "id": "lens:automation/4", "name": "School emails"},
        "object": {
            "type": "Activity",
            "id": "lens:automation_run/51",
            "name": "School emails run 51",
            "url": "/automations/4?run=51",
            "status": "waiting",
            "trigger": "mail",
            "event": "create-recording-7",
            "counts": {"done": 1, "waiting": 1, "scheduled": 0, "failed": 0},
        },
        "target": None,
        "tag": [],
        "summary": "School emails ran: 1 done, 1 waiting for your pick",
        "space": 3,
        "lens": {"source": "automations", "pos": ["2026-10-15T16:22:01+00:00", 51], "untrusted": [], "cause": None},
    },
)
events.kind(
    "automation.limited",
    label="Automation over its limit",
    source="log",
    as_type="Reject",
    object_type="Application",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "title": "Object type", "enum": ["Application"]},
                "id": {"type": "string", "title": "Automation"},
                "name": {"type": "string", "title": "Automation's name"},
                "why": {"type": "string", "title": "Which limit"},
            },
        }
    },
)


def actor(aid, name):
    return {"type": "Application", "id": f"lens:automation/{int(aid)}", "name": (name or "").strip() or f"Automation {aid}"}


def _summary(r):
    c = r.get("counts") or {}
    name = (r.get("name") or "").strip() or f"Automation {r['automation']}"
    if r["status"] == "failed" and not any(c.values()):
        return f"{name} failed"
    parts = [
        f"{c[k]} {w}"
        for k, w in (("done", "done"), ("waiting", "waiting for your pick"), ("scheduled", "scheduled"), ("failed", "failed"))
        if c.get(k)
    ]
    return f"{name} ran" + (": " + ", ".join(parts) if parts else "")


def _activities(rows):
    out = []
    for r in rows:
        try:
            published = events.stamp(r["finished_at"])
        except (TypeError, ValueError):
            continue
        cause = r.get("cause")
        if not (isinstance(cause, dict) and all(isinstance(cause.get(k), int) for k in ("automation", "run", "depth"))):
            cause = None
        obj = store.clean(
            {
                "type": "Activity",
                "id": f"lens:automation_run/{r['id']}",
                "name": f"{(r.get('name') or '').strip() or 'Automation ' + str(r['automation'])} run {r['id']}",
                "url": f"/automations/{r['automation']}?run={r['id']}",
                "status": r["status"],
                "trigger": r.get("trigger") if isinstance(r.get("trigger"), str) else None,
                "event": r.get("event") if isinstance(r.get("event"), str) else None,
                "counts": {k: int((r.get("counts") or {}).get(k) or 0) for k in ("done", "waiting", "scheduled", "failed")},
            }
        )
        out.append(
            events.build(
                f"ran-automation_run-{r['id']}",
                "automation.ran",
                actor=actor(r["automation"], r.get("name")),
                obj=obj,
                summary=_summary(r),
                space=r.get("space"),
                source="automations",
                pos=[r["finished_at"], r["id"]],
                published=published,
                cause=cause,
            )
        )
    return out


class AutomationsSource(events.Source):
    """automation_run rows that ran their graph, as automation.ran, read by (finished_at, id)."""

    name = "automations"
    kinds = ("automation.ran",)

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {"st": STATUSES}
        where = (
            f"status IN $st AND finished_at != NONE AND {events.after_sql('finished_at', pos, p, table='automation_run')} "
            f"AND {events.space_sql(spaces, p)}"
        )
        rows = db.rows(f"SELECT {FIELDS} FROM automation_run WHERE {where} ORDER BY finished_at, rid LIMIT {int(limit)}", **p)
        return _activities(rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {"st": STATUSES}
        where = [
            "status IN $st",
            "finished_at != NONE",
            events.space_sql(None if who.get("admin") else (who.get("spaces") or set()), p),
        ]
        if before is not None:
            where.append(events.after_sql("finished_at", before, p, "<", table="automation_run"))
        rows = db.rows(
            f"SELECT {FIELDS} FROM automation_run WHERE {' AND '.join(where)} ORDER BY finished_at DESC, rid DESC LIMIT {int(limit)}",
            **p,
        )
        return _activities(rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^ran-automation_run-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {FIELDS} FROM $r", r=R("automation_run", int(m.group(1))))
        if not r or r.get("status") not in STATUSES:
            return None
        got = _activities([r])
        return got[0] if got else None


events.register_source(AutomationsSource())
