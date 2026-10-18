"""Starter automations (docs/automations.md, Starters): drafts to open in the studio, change and save."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ResponseModel


class AutomationStarter(ResponseModel):
    id: str
    name: str
    description: str = Field(description="what it does, and what to change before turning it on")
    draft: dict[str, Any] = Field(description="what POST /automations takes, switched off")


class AutomationStarters(ResponseModel):
    starters: list[AutomationStarter]
