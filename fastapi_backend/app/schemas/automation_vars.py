"""Automations' saved variables (docs/automations.md, Saved variables): values an automation keeps between runs,
its own or its namespace's, read in templates as vars.<name>."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class AutomationVar(ResponseModel):
    name: str
    value: Any = Field(None, description="JSON up to 16 KB")
    untrusted: bool = Field(False, description="set from text from outside Lens: prompts mark it as data")
    updated_at: str | None = None
    updated_by: str | None = Field(None, description="who set it last: an email, or automation:<id>")


class AutomationVars(ResponseModel):
    vars: list[AutomationVar]


class AutomationVarsIn(RequestModel):
    vars: dict[str, Any] = Field(description="{name: value}: each one set to its value (JSON up to 16 KB), null clears it; the others stay")
