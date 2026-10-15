"""Automations (docs/automations.md, Automations): when something happens, if a test passes, do something, for you in
namespaces you can edit. Admins see and change every automation; anyone else the ones that work only in namespaces
they can edit. Ids use the int convertor, so static paths under /automations never collide with them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import actions, auth, automation_draft, automations, events, store
from app.schemas.automations import (
    ActionInfo,
    Automation,
    AutomationCatalog,
    AutomationCreate,
    AutomationDrafted,
    AutomationDraftRequest,
    AutomationDraftTry,
    AutomationPolicy,
    AutomationRun,
    AutomationRunDetail,
    AutomationRunNow,
    AutomationRuns,
    AutomationSaved,
    AutomationStarted,
    AutomationSwitch,
    AutomationTried,
    AutomationTry,
    AutomationUpcoming,
    AutomationUpdate,
)
from app.schemas.common import Ok
from app.schemas.routines import HeldRunDecision

router = APIRouter(prefix="/automations", tags=["automations"])


def _editable(db: Any, user: Principal, acl: Any) -> list[int]:
    return sorted(store.space_names(db)) if user.admin else acl.editable()


def _mine(db: Any, user: Principal, acl: Any, aid: int, removed: bool = False) -> dict[str, Any]:
    """The automation, if this person may see and change it (404 otherwise, so ids don't leak)."""
    try:
        a = automations.get(db, aid, removed)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if not automations.manages(a, _editable(db, user, acl), user.admin):
        raise HTTPException(404, "not found")
    return a


def _who(user: Principal) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name, "admin": user.admin}


@router.get("")
def list_automations(user: CurrentUser, acl: Acl, db: Db, removed: bool = False) -> AutomationCatalog:
    """Your automations: every one for an admin, else those that work only in namespaces you can edit. `removed`
    lists the deleted ones instead, to restore."""
    spaces = None if user.admin else acl.editable()
    rows = automations.list_automations(db, spaces, removed)
    return AutomationCatalog(automations=automations.extras(db, rows))


@router.get("/actions")
def list_actions(user: CurrentUser) -> list[ActionInfo]:
    """What an action node can do: each action's params (a JSON Schema), what it reaches (its effect) and what it
    gives back."""
    return [ActionInfo(**a) for a in actions.catalog()]


@router.post("")
def create_automation(body: AutomationCreate, user: Writer, db: Db, cfg: Cfg) -> AutomationSaved:
    """A new automation that acts for you. Its Then is a graph (saved as a workflow of scope event) or a saved
    workflow; its namespaces default to each one you can edit. Nodes ask first unless you let one run on its own
    (POST /automations/{id}/policy)."""
    with domain_errors():
        got = automations.create(db, cfg, body.model_dump(exclude_unset=True, by_alias=True), _who(user))
    auth.audit(db, user.as_audit(), "automation.create", f"automation:{got['id']}")
    return AutomationSaved(**got)


@router.get("/{aid:int}")
def get_automation(aid: int, user: CurrentUser, acl: Acl, db: Db) -> Automation:
    return Automation(**_mine(db, user, acl, aid))


@router.patch("/{aid:int}")
def update_automation(aid: int, body: AutomationUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> AutomationSaved:
    """Change what is sent: a new graph is saved as a new version of its workflow; `if: null` drops its If,
    `version: null` follows the published version."""
    _mine(db, user, acl, aid)
    changes = {
        k: v
        for k, v in body.model_dump(exclude_unset=True, by_alias=True).items()
        if v is not None or k not in ("name", "enabled", "triggers", "graph", "workflow")
    }
    with domain_errors():
        got = automations.update(db, cfg, aid, changes, user.email)
    auth.audit(db, user.as_audit(), "automation.update", f"automation:{aid}")
    return AutomationSaved(**got)


@router.post("/{aid:int}/enable")
def enable_automation(aid: int, body: AutomationSwitch, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Switch it on (it reacts to what happens from now on, never to what happened while it was off) or off."""
    _mine(db, user, acl, aid)
    with domain_errors():
        automations.enable(db, cfg, aid, body.on, user.email)
    auth.audit(db, user.as_audit(), "automation.enable" if body.on else "automation.disable", f"automation:{aid}")
    return Ok()


@router.delete("/{aid:int}")
def delete_automation(aid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Delete it: switched off and hidden; POST /automations/{id}/restore undoes it."""
    _mine(db, user, acl, aid)
    with domain_errors():
        automations.remove(db, cfg, aid, user.email)
    auth.audit(db, user.as_audit(), "automation.delete", f"automation:{aid}")
    return Ok()


@router.post("/{aid:int}/restore")
def restore_automation(aid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Undo a delete: it comes back switched off."""
    _mine(db, user, acl, aid, removed=True)
    with domain_errors():
        automations.restore(db, cfg, aid, user.email)
    auth.audit(db, user.as_audit(), "automation.restore", f"automation:{aid}")
    return Ok()


@router.post("/{aid:int}/policy")
def set_policy(aid: int, body: AutomationPolicy, user: Writer, acl: Acl, db: Db) -> dict[str, str]:
    """Whether an action node asks you first (ask) or runs on its own (auto). Only you, signed in to the web app,
    let a node run on its own: never an API key, an app or an agent."""
    _mine(db, user, acl, aid)
    if body.value == "auto" and user.via != "access":
        raise HTTPException(403, "only you, signed in to the web app, let a node run on its own")
    with domain_errors():
        got = automations.set_policy(db, aid, body.node, body.value, user.email)
    auth.audit(db, user.as_audit(), "automation.policy", f"automation:{aid}", {"node": body.node, "value": body.value})
    return got


def _run_of(db: Any, user: Principal, acl: Any, run_id: int) -> dict[str, Any]:
    """A run of an automation this person manages (404 otherwise), with its trace, log and intents."""
    try:
        r = automations.get_run(db, run_id)
    except KeyError:
        raise HTTPException(404, "not found") from None
    for removed in (False, True):
        try:
            a = automations.get(db, r["automation"], removed)
        except KeyError:
            continue
        if automations.manages(a, _editable(db, user, acl), user.admin):
            return r
    raise HTTPException(404, "not found")


def _activity(db: Any, cfg: Any, user: Principal, acl: Any, activity_id: str | None, kind: str | None) -> dict[str, Any] | None:
    """The activity to try or run it on: one you may see, by id, or a kind's sample; None for the default."""
    if activity_id:
        a = events.get(db, cfg, activity_id, events.viewer(user.id, acl.readable(), user.admin))
        if a is None:
            raise HTTPException(404, "no such activity")
        return a
    if kind:
        try:
            a = events.kind_info(kind)["sample"]
        except KeyError:
            raise HTTPException(400, f"no kind {kind}; GET /api/v1/activities/kinds lists them") from None
        if a is None:
            raise HTTPException(400, f"{kind} has no sample to try it on; give an activity")
        return a
    return None


@router.get("/runs")
def runs_caused(user: CurrentUser, acl: Acl, db: Db, activity: str = Query(description="an activity's id")) -> list[AutomationRun]:
    """What an activity caused: the runs of your automations it started (it was their event, or one of a join's)."""
    editable = _editable(db, user, acl)
    out = []
    for r in automations.caused(db, activity):
        try:
            a = automations.get(db, r["automation"])
        except KeyError:
            continue
        if automations.manages(a, editable, user.admin):
            out.append(AutomationRun(**r))
    return out


@router.get("/runs/{run:int}")
def get_run(run: int, user: CurrentUser, acl: Acl, db: Db) -> AutomationRunDetail:
    """One run: what each node did (trace), its log, and what its actions did, asked or wait to do (intents)."""
    return AutomationRunDetail(**_run_of(db, user, acl, run))


@router.post("/runs/{run:int}/decide")
def decide_held_run(run: int, body: HeldRunDecision, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> AutomationStarted:
    """Pick for a run held over a budget: run it now, once, whatever its budgets say, or skip it."""
    r = _run_of(db, user, acl, run)
    with domain_errors():
        got = automations.decide_held(db, cfg, run, body.run, user.email)
    auth.audit(db, user.as_audit(), "automation.release" if body.run else "automation.skip", f"automation:{r['automation']}", {"run": run})
    return AutomationStarted(run=got, status=None if got is None else automations.get_run(db, got)["status"])


@router.post("/draft")
def draft_automation(body: AutomationDraftRequest, user: Writer, db: Db, cfg: Cfg) -> AutomationDrafted:
    """Describe an automation in words: the model (one call, in the activity ledger, refused over budget) turns the
    sentence into a draft with the kinds of activity, the match operators and the actions as its vocabulary. What
    doesn't fit is dropped and said; nothing is saved. Try it with POST /automations/test, then save it."""
    with domain_errors():
        return AutomationDrafted(**automation_draft.draft(db, cfg, body.text, _who(user), body.spaces))


@router.post("/test")
def test_draft(body: AutomationDraftTry, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> AutomationTried:
    """Try an automation before you save it: a dry run on an activity, or on a kind's sample (default: its first
    trigger's kind). Says whether its If passes and what each action would do; keeps, sends and counts nothing."""
    a = _activity(db, cfg, user, acl, body.activity, body.kind)
    draft = body.draft.model_dump(exclude_unset=True, by_alias=True)
    with domain_errors():
        got = automations.test(db, cfg, draft=draft, activity=a, alias=body.as_, user=_who(user))
    return AutomationTried(**got)


@router.post("/{aid:int}/test")
def test_automation(aid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, body: AutomationTry | None = None) -> AutomationTried:
    """Try a saved automation: a dry run on an activity, or on a kind's sample (default: its first trigger's kind).
    Keeps, sends and counts nothing."""
    body = body or AutomationTry()
    _mine(db, user, acl, aid)
    a = _activity(db, cfg, user, acl, body.activity, body.kind)
    with domain_errors():
        got = automations.test(db, cfg, aid, activity=a, alias=body.as_)
    return AutomationTried(**got)


@router.post("/{aid:int}/run")
def run_automation(aid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg, body: AutomationRunNow | None = None) -> AutomationStarted:
    """Run it now: on an activity, else the newest one its triggers listen for, else as started by hand. It acts as it
    would on its own: limits and budgets apply, and actions that ask first wait for your pick."""
    body = body or AutomationRunNow()
    _mine(db, user, acl, aid)
    a = _activity(db, cfg, user, acl, body.activity, None)
    with domain_errors():
        got = automations.run_now(db, cfg, aid, _who(user), activity=a, alias=body.as_)
    auth.audit(db, user.as_audit(), "automation.run", f"automation:{aid}", {"run": got["run"]})
    return AutomationStarted(**got)


@router.get("/{aid:int}/runs")
def list_runs(
    aid: int,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    before: int | None = Query(None, description="a run id: the runs before it"),
    limit: int = Query(20, ge=1, le=100),
) -> AutomationRuns:
    """Its runs, newest first; `next` pages on."""
    _mine(db, user, acl, aid, removed=False)
    rows = automations.runs(db, aid, before, limit)
    return AutomationRuns(runs=[AutomationRun(**r) for r in rows], next=rows[-1]["id"] if len(rows) == limit else None)


@router.get("/{aid:int}/upcoming")
def upcoming(aid: int, user: CurrentUser, acl: Acl, db: Db, n: int = Query(10, ge=1, le=100)) -> AutomationUpcoming:
    """When it runs next, from its schedules and timers (coming soon: empty until schedules are built)."""
    _mine(db, user, acl, aid)
    with domain_errors():
        return AutomationUpcoming(times=automations.upcoming(db, aid, n))
