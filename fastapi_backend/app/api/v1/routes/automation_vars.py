"""Automations' variables (docs/automations.md, Variables and templates). Saved variables: what an automation keeps
between runs, read as vars.<name> in its templates, and what its namespace's automations share. An automation's
variables are for whoever may change it (admins, or people who can edit every namespace it works in); a namespace's
are read by its readers and changed by its editors. The variable picker's catalog and a template's preview for a
draft automation, against the kind's sample or an activity the person may see. Automation ids use the int
convertor, so static paths under /automations never collide."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, events, store, variables
from app.schemas.automation_vars import (
    AutomationVar,
    AutomationVars,
    AutomationVarsIn,
    TemplatePreviewIn,
    TemplatePreviewOut,
    VariableCatalog,
    VariableCatalogIn,
    VariableEntry,
)

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


def vetted(db: Any, user: Principal, acl: Any, draft: dict[str, Any]) -> dict[str, Any]:
    """The draft as this person may preview it: its id only if they may change that automation (404 otherwise), and
    its namespaces only ones they read (404 for another; none named: theirs, or every one for an admin)."""
    out = dict(draft)
    if out.get("id") is not None:
        if not isinstance(out["id"], int) or isinstance(out["id"], bool):
            raise HTTPException(400, "the draft's id is an automation's id")
        automation(db, user, acl, out["id"])
    readable = acl.readable()
    spaces = out.get("spaces")
    if spaces is None:
        out["spaces"] = None if readable is None else sorted(readable)
    elif not isinstance(spaces, list) or not all(isinstance(s, int) and not isinstance(s, bool) for s in spaces):
        raise HTTPException(400, "the draft's spaces are namespace ids")
    elif readable is not None and any(s not in readable for s in spaces):
        raise HTTPException(404, "no such namespace")
    return out


def person(user: Principal) -> dict[str, Any]:
    return {"id": user.id, "name": user.name, "email": user.email}


def preview_for(db: Any, cfg: Any, user: Principal, acl: Any, body: TemplatePreviewIn) -> TemplatePreviewOut:
    """The preview of body's template, for the API and the MCP tool."""
    draft = vetted(db, user, acl, body.draft)
    act = None
    if body.activity:
        act = events.get(db, cfg, body.activity, events.viewer(user.id, acl.readable(), user.admin))
        if act is None:
            raise HTTPException(404, "no such activity")
    with domain_errors():
        got = variables.preview(db, cfg, body.template, draft, person(user), act, body.trigger, body.node, body.prompt)
    return TemplatePreviewOut(**got)


@router.post("/automations/variables")
def variable_catalog(body: VariableCatalogIn, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> VariableCatalog:
    """What a draft automation's templates can read, for the variable picker: the run context, each trigger's activity
    fields, the saved variables and, with node, what the nodes before it pass on; with types, labels and examples."""
    draft = vetted(db, user, acl, body.draft)
    with domain_errors():
        rows = variables.catalog(db, cfg, draft, body.node, person(user))
    return VariableCatalog(variables=[VariableEntry(**r) for r in rows])


@router.post("/automations/preview")
def preview_template(body: TemplatePreviewIn, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> TemplatePreviewOut:
    """What a template gives for a draft automation before it is saved, against an activity (by id; 404 for one you
    can't see) or the trigger kind's sample. A prompt shows outside text marked as the model sees it. A template that
    fails answers 200 with error and line; a draft that doesn't check answers 400."""
    return preview_for(db, cfg, user, acl, body)
