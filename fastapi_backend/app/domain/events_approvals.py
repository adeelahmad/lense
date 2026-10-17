"""Approvals as activities (docs/automations.md, Approvals): `approval.waiting` when something starts waiting for a
person's pick, read by `approval.created_at`, and `approval.decided` when it is approved (done or failed), declined
or expires, read by `approval.decided_at`. Both are the person's own (`lens.audience` owner): the timeline shows them
to that person alone, and their automations can react ("when an approval waits more than a day, remind me"). An
automation's approval carries its run as `lens.cause`.
"""

from __future__ import annotations

import re

from . import events
from .events_content import _num, _paging, _s, _sample, _text, actors
from .store import R

FIELDS = (
    "id AS rid, record::id(id) AS id, account, chat, tool, summary, status, created_at, decided_at, decided_by, origin, "
    "intent.action AS action, intent.effect AS effect, space, expires_at, why.activity AS about, automation, error"
)
DECIDED = ("done", "failed", "declined", "expired")
OUT = {"done": "approved", "failed": "approved, but it failed", "declined": "declined", "expired": "expired with no pick"}


def _depths(db, rows):
    """The run's own depth of cause plus one, for each run an approval came from (1 while runs aren't stored)."""
    runs = sorted({o["run"] for o in (r.get("origin") or {} for r in rows) if _num(o.get("run")) is not None})
    got = {}
    if runs:
        try:
            got = {
                x["id"]: x.get("cause_depth")
                for x in db.rows(
                    "SELECT record::id(id) AS id, cause_depth FROM automation_run WHERE id IN $ids",
                    ids=[R("automation_run", n) for n in runs],
                )
            }
        except Exception:  # noqa: BLE001 - runs aren't stored yet
            got = {}
    return {n: (got.get(n) if isinstance(got.get(n), int) else 0) + 1 for n in runs}


def _object(r):
    o = r.get("origin") or {}
    return {
        k: v
        for k, v in {
            "type": "Question",
            "id": f"lens:approval/{r['id']}",
            "name": _text(r.get("summary"), 500) or "An approval",
            "url": f"/approvals?id={r['id']}",
            "status": r.get("status"),
            "tool": r.get("tool"),
            "action": r.get("action"),
            "effect": r.get("effect"),
            "automation": _num(o.get("automation")),
            "automation_name": r.get("automation"),
            "run": _num(o.get("run")),
            "node": o.get("node"),
            "chat": _num(r.get("chat")),
            "about": r.get("about"),
            "expires_at": r.get("expires_at"),
            "error": _text(r.get("error"), 500),
        }.items()
        if v is not None
    }


def _cause(r, depths):
    o = r.get("origin") or {}
    if _num(o.get("automation")) is None or _num(o.get("run")) is None:
        return None
    return {"automation": o["automation"], "run": o["run"], "depth": depths.get(o["run"], 1)}


def _asker(r):
    o = r.get("origin") or {}
    return f"automation:{o['automation']}" if _num(o.get("automation")) is not None else "assistant"


def _waiting(db, rows):
    rows = [r for r in rows if r and r.get("created_at")]
    if not rows:
        return []
    who = actors(db, [(_asker(r), r.get("space")) for r in rows])
    depths = _depths(db, rows)
    out = []
    for r in rows:
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError):
            continue
        actor = who[(_asker(r), r.get("space"))]
        obj = _object(r)
        out.append(
            events.build(
                f"ask-approval-{r['id']}",
                "approval.waiting",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} asks for your pick: {obj['name']}",
                space=r.get("space"),
                source="approvals",
                pos=[r["created_at"], r["id"]],
                published=published,
                cause=_cause(r, depths),
                audience="owner",
                owner=r.get("account"),
            )
        )
    return out


def _decided(db, rows):
    rows = [r for r in rows if r and r.get("decided_at") and r.get("status") in DECIDED]
    if not rows:
        return []
    label = {r["id"]: None if r["status"] == "expired" else r.get("decided_by") for r in rows}
    who = actors(db, [(label[r["id"]], r.get("space")) for r in rows])
    depths = _depths(db, rows)
    out = []
    for r in rows:
        try:
            published = events.stamp(r["decided_at"])
        except (TypeError, ValueError):
            continue
        actor = who[(label[r["id"]], r.get("space"))]
        obj = _object(r)
        said = f"{actor['name']} {OUT[r['status']]}" if r["status"] != "expired" else "Expired with no pick"
        a = events.build(
            f"decide-approval-{r['id']}",
            "approval.decided",
            actor=actor,
            obj=obj,
            summary=f"{said}: {obj['name']}",
            space=r.get("space"),
            source="approval_decisions",
            pos=[r["decided_at"], r["id"]],
            published=published,
            cause=_cause(r, depths),
            audience="owner",
            owner=r.get("account"),
        )
        a["type"] = "Accept" if r["status"] in ("done", "failed") else "Reject"
        out.append(a)
    return out


class _ApprovalSource(events.Source):
    col = "created_at"
    rx = re.compile(r"^ask-approval-(\d+)$")
    more = ""

    def _make(self, db, rows):
        return _waiting(db, rows)

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql(self.col, pos, p, table='approval')} AND {events.space_sql(spaces, p)}{self.more}"
        rows = db.rows(f"SELECT {FIELDS} FROM approval WHERE {where} ORDER BY {self.col}, rid LIMIT {int(limit)}", **p)
        return self._make(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        if who.get("account") is None:
            return []
        p: dict = {"me": who["account"]}
        where = _paging(self.col, "approval", before, {**who, "admin": True}, flt, p) + ["account = $me"]
        rows = db.rows(
            f"SELECT {FIELDS} FROM approval WHERE {' AND '.join(where)}{self.more} ORDER BY {self.col} DESC, rid DESC LIMIT {int(limit)}",
            **p,
        )
        return self._make(db, rows)

    def get(self, db, cfg, activity_id):
        m = self.rx.match(activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {FIELDS} FROM $r", r=R("approval", int(m.group(1))))
        got = self._make(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = sorted({int(m.group(1)) for m in (self.rx.match(i) for i in ids) if m})
        if not ns:
            return []
        return self._make(db, db.rows(f"SELECT {FIELDS} FROM approval WHERE id IN $rs", rs=[R("approval", n) for n in ns]))


class ApprovalsSource(_ApprovalSource):
    """approval rows as approval.waiting, read by (created_at, id)."""

    name = "approvals"


class ApprovalDecisionsSource(_ApprovalSource):
    """decided approval rows as approval.decided, read by (decided_at, id)."""

    name = "approval_decisions"
    col = "decided_at"
    rx = re.compile(r"^decide-approval-(\d+)$")
    more = " AND status IN ['done', 'failed', 'declined', 'expired']"

    def _make(self, db, rows):
        return _decided(db, rows)


events.register_source(ApprovalsSource())
events.register_source(ApprovalDecisionsSource())

_OBJECT = {
    "type": "object",
    "properties": {
        "type": _s("Object type", enum=["Question"]),
        "id": _s("Approval"),
        "name": _s("What it would do"),
        "url": _s("Link"),
        "status": _s("Status", enum=["pending", "running", "done", "failed", "declined", "expired"]),
        "tool": _s("Tool"),
        "action": _s("Action"),
        "effect": _s("Effect", enum=["read", "self", "lens", "out"]),
        "automation": _s("Automation", "integer"),
        "automation_name": _s("Automation name"),
        "run": _s("Run", "integer"),
        "node": _s("Node"),
        "chat": _s("Conversation", "integer"),
        "about": _s("The activity that caused it"),
        "expires_at": _s("Expires", format="date-time"),
        "error": _s("Why it failed"),
    },
}
AUTOMATION = {"type": "Application", "id": "lens:automation/4", "name": "School emails"}
_ASKED = {
    "type": "Question",
    "id": "lens:approval/12",
    "name": "Post in the Matterbridge gateway family: “Trip on Friday, bring a packed lunch”",
    "url": "/approvals?id=12",
    "status": "pending",
    "tool": "action",
    "action": "notify",
    "effect": "out",
    "automation": 4,
    "automation_name": "School emails",
    "run": 51,
    "node": "n7",
    "about": "create-recording-7",
    "expires_at": "2026-10-22T16:24:10+00:00",
}
UNTRUSTED = ("object.name", "object.error")
events.kind(
    "approval.waiting",
    label="Waiting for your approval",
    source="approvals",
    as_type="Create",
    object_type="Question",
    fields={"object": _OBJECT},
    untrusted=UNTRUSTED,
    sample=_sample(
        "ask-approval-12",
        "approval.waiting",
        "Create",
        "approvals",
        ["2026-10-15T16:24:10+00:00", 12],
        AUTOMATION,
        _ASKED,
        "School emails asks for your pick: Post in the Matterbridge gateway family: “Trip on Friday, bring a packed lunch”",
        UNTRUSTED,
    ),
)
events.kind(
    "approval.decided",
    label="Approval decided",
    source="approval_decisions",
    as_type="Accept",
    object_type="Question",
    fields={"object": _OBJECT},
    untrusted=UNTRUSTED,
    sample=_sample(
        "decide-approval-12",
        "approval.decided",
        "Accept",
        "approval_decisions",
        ["2026-10-15T16:40:02+00:00", 12],
        {"type": "Person", "id": "lens:account/1", "name": "Sam"},
        {**_ASKED, "status": "done"},
        "Sam approved: Post in the Matterbridge gateway family: “Trip on Friday, bring a packed lunch”",
        UNTRUSTED,
    ),
)
