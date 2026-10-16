"""Automations' saved variables (docs/automations.md, Saved variables): what an automation keeps between runs, read
as vars.<name> in its templates, and what its namespace's automations share. An automation's variables are for whoever
may change it (admins, or people who can edit every namespace it works in); a namespace's are read by its readers and
changed by its editors. Automation ids use the int convertor, so static paths under /automations never collide."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, store, variables
from app.schemas.automation_vars import AutomationVar, AutomationVars, AutomationVarsIn

router = APIRouter(tags=["automations"])


def automation(db: Any, user: Principal, acl: Any, aid: int) -> int:
    """The automation's id, if this person may see and change it (404 otherwise, so ids don't leak)."""
    editable = sorted(store.space_names(db)) if user.admin else acl.editable()
    if not variables.manages(db, aid, user.admin, editable):
        raise HTTPException(404, "not found")
    return aid


def listing(rows: list[dict[str, Any]]) -> AutomationVars:
    return AutomationVars(vars=[AutomationVar(**r) for r in rows])


@router.get("/automations/{aid:int}/vars")
def get_automation_vars(aid: int, user: CurrentUser, acl: Acl, db: Db) -> AutomationVars:
    """The automation's own saved variables: what its runs keep between them (set_variable), by name."""
    return listing(variables.list_vars(db, automation=automation(db, user, acl, aid)))


@router.put("/automations/{aid:int}/vars")
def put_automation_vars(aid: int, body: AutomationVarsIn, user: Writer, acl: Acl, db: Db) -> AutomationVars:
    """Set or clear the automation's variables: each one named gets its value, null clears it, the others stay."""
    automation(db, user, acl, aid)
    with domain_errors():
        rows = variables.put_vars(db, body.vars, automation=aid, by=user.email)
    auth.audit(db, user.as_audit(), "automation.vars", f"automation:{aid}", {"names": sorted(body.vars)})
    return listing(rows)


@router.get("/namespaces/{name}/automation-vars")
def get_namespace_vars(name: str, user: CurrentUser, acl: Acl, db: Db) -> AutomationVars:
    """The namespace's saved variables: what its automations share, read as vars.<name> (an automation's own win)."""
    return listing(variables.list_vars(db, space=acl.namespace(name)))


@router.put("/namespaces/{name}/automation-vars")
def put_namespace_vars(name: str, body: AutomationVarsIn, user: Writer, acl: Acl, db: Db) -> AutomationVars:
    """Set or clear the namespace's variables (editors): each one named gets its value, null clears it."""
    sid = acl.namespace(name, "editor")
    with domain_errors():
        rows = variables.put_vars(db, body.vars, space=sid, by=user.email)
    auth.audit(db, user.as_audit(), "automation.vars", f"space:{sid}", {"names": sorted(body.vars)})
    return listing(rows)
