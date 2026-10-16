"""Automations (docs/automations.md, Automations): when something happens, if a test passes, do something, for you in
namespaces you can edit. Admins see and change every automation; anyone else the ones that work only in namespaces
they can edit. Ids use the int convertor, so static paths under /automations never collide with them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import actions, auth, automations, store
from app.schemas.automations import (
    ActionInfo,
    Automation,
    AutomationCatalog,
    AutomationCreate,
    AutomationPolicy,
    AutomationSaved,
    AutomationSwitch,
    AutomationUpdate,
)
from app.schemas.common import Ok

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
    return AutomationCatalog(automations=automations.list_automations(db, spaces, removed))


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
