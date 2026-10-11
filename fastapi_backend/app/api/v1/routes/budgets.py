"""Budgets (admins set them; docs/budgets.md): caps on what a routine, pipeline, workflow or namespace may cost, and
where each stands."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, AdminReader, AdminWriter, CurrentUser, Db, domain_errors
from app.domain import auth, budgets, store
from app.schemas.budgets import BudgetMeter, BudgetSet, BudgetStatus
from app.schemas.common import Ok

router = APIRouter(tags=["budgets"])
RESOURCE = Query(description="all (everything Lens spends), routine:<id>, pipeline:<id>, workflow:<id> or space:<id>")


@router.get("/budgets")
def list_budgets(user: AdminReader, db: Db) -> list[BudgetStatus]:
    """Every budget, with where it stands now."""
    return [budgets.status(db, b["resource"], b) for b in budgets.all_budgets(db)]


def _named(db, rows):
    for r in rows:
        table, _, key = r["resource"].partition(":")
        row = db.one("SELECT name, title FROM $r", r=store.R(table, int(key))) if key.isdigit() else None
        r["name"] = ((row or {}).get("name") or (row or {}).get("title")) or None
    return rows


@router.get("/budgets/meter")
def budget_meter(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    ns: str | None = Query(None, description="a namespace; none for all of Lens (admins)"),
    period: Literal["day", "week", "month"] | None = Query(None, description="none: the budget's own period, else month"),
    detail: bool = Query(False, description="also by day, by model and what cost most"),
) -> BudgetMeter:
    """What a namespace (or all of Lens) spent this day, week or month, against its budget when it has one: the top
    bar's meter, and with `detail` the costs page. Anyone who can read a namespace sees its meter; admins see all of
    Lens's."""
    if ns:
        resource = f"space:{acl.namespace(ns)}"
        ref = resource
    elif user.admin:
        resource, ref = None, budgets.ALL
    else:
        raise HTTPException(403, "only admins see what all of Lens spent; pick a namespace")
    out = budgets.meter(db, resource, ref, period, detail)
    if detail:
        _named(db, out["top"])
        _named(db, out.get("by_namespace") or [])
    return BudgetMeter(namespace=ns, **out)


@router.get("/budgets/status")
def budget_status(user: AdminReader, db: Db, resource: str = RESOURCE) -> BudgetStatus:
    """Where a resource's budget stands, and what its next run will likely cost (also without a budget)."""
    with domain_errors():
        budgets.check_ref(resource)
    return budgets.status(db, resource)


@router.put("/budgets")
def set_budget(body: BudgetSet, user: AdminWriter, db: Db, resource: str = RESOURCE) -> BudgetStatus:
    with domain_errors():
        budgets.put(db, resource, by=user.email, **body.model_dump())
    auth.audit(db, user.as_audit(), "budget.set", resource, body.model_dump(exclude_none=True))
    return budgets.status(db, resource)


@router.delete("/budgets")
def remove_budget(user: AdminWriter, db: Db, resource: str = RESOURCE) -> Ok:
    with domain_errors():
        if not budgets.get(db, budgets.check_ref(resource)):
            raise HTTPException(404, "this resource has no budget")
        budgets.remove(db, resource)
    auth.audit(db, user.as_audit(), "budget.remove", resource)
    return Ok()
