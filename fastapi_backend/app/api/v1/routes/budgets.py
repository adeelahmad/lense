"""Budgets (admins set them; docs/budgets.md): caps on what a routine, pipeline, workflow or namespace may cost, and
where each stands."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminReader, AdminWriter, Db, domain_errors
from app.domain import auth, budgets
from app.schemas.budgets import BudgetSet, BudgetStatus
from app.schemas.common import Ok

router = APIRouter(tags=["budgets"])
RESOURCE = Query(description="routine:<id>, pipeline:<id>, workflow:<id> or space:<id>")


@router.get("/budgets")
def list_budgets(user: AdminReader, db: Db) -> list[BudgetStatus]:
    """Every budget, with where it stands now."""
    return [budgets.status(db, b["resource"], b) for b in budgets.all_budgets(db)]


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
