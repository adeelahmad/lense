"""Starter automations (docs/automations.md, Starters): ready-made drafts, switched off, that the studio opens in its
builder and agents read before saving their own. Nothing is stored until someone saves one. Automation ids use the
int convertor, so this static path never collides with /automations/{id}."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.domain import automation_starters
from app.schemas.automation_starters import AutomationStarter, AutomationStarters

router = APIRouter(prefix="/automations", tags=["automations"])


@router.get("/starters")
def list_starters(user: CurrentUser) -> AutomationStarters:
    """Starter automations: each a draft to open, change and save (POST /automations), switched off until you turn
    it on."""
    return AutomationStarters(starters=[AutomationStarter(**s) for s in automation_starters.starters()])
