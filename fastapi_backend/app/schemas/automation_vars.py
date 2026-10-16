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


class VariableCatalogIn(RequestModel):
    draft: dict[str, Any] = Field(
        description="the automation as drafted: {triggers, join?, workflow? (an id or a graph), spaces?, id?, name?}"
    )
    node: str | None = Field(None, max_length=100, description="a node of its workflow: what the nodes before it pass on is listed too")


class VariableEntry(ResponseModel):
    path: str = Field(description="what a template writes: {{ events.mail.object.subject }}")
    type: str
    label: str
    example: Any = None
    group: str = Field(description="where it comes from, for the picker's headings")
    untrusted: bool = Field(False, description="text from outside Lens: prompts mark it as data")
    description: str | None = None


class VariableCatalog(ResponseModel):
    variables: list[VariableEntry]


class TemplatePreviewIn(RequestModel):
    template: Any = Field(description="a param's value (text with {{ }}, or a list or object of them), or a prompt")
    draft: dict[str, Any] = Field(description="the automation as drafted, as for /automations/variables")
    activity: str | None = Field(None, max_length=200, description="an activity's id to render against; default the trigger kind's sample")
    trigger: str | None = Field(None, max_length=40, description="which trigger fired (as); default the first")
    node: str | None = Field(None, max_length=100, description="the node the template is in: earlier steps are filled with stand-ins")
    prompt: bool = Field(False, description="render it as a prompt, with outside text marked as the model sees it")


class PreviewActivity(ResponseModel):
    id: str | None = None
    kind: str | None = None
    sample: bool = Field(description="the kind's sample, not a real activity")


class TemplatePreviewOut(ResponseModel):
    text: str | None = Field(None, description="what it gives, written out")
    value: Any = Field(None, description="what a param gets: one {{ }} expression keeps its type")
    prompt: bool = False
    rules: str | None = Field(None, description="for a prompt: the system prompt's paragraph about marked text")
    system: str | None = Field(None, description="a saved prompt template's own instructions")
    error: str | None = Field(None, description="why it can't be rendered: the missing variable and its nearest name")
    line: int | None = None
    activity: PreviewActivity
