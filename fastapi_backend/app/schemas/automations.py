"""Automations (docs/automations.md, Automations): triggers, an If and a Then (a workflow of scope event) that act
for one person in some namespaces; the actions they can take."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import RequestModel, ResponseModel

TRIGGERS = (
    "each {as, on, ...}: {as, on: event, kinds, where?, from_automations?}, {as, on: manual}; on schedule, at, relative "
    "and webhook are coming soon (docs/automations.md, Triggers)"
)
IF = "a match tree over the run context, tested before the Then runs, like {all: [{path, op, value}]}"
GRAPH = "the Then: a workflow graph of scope event (an input node, then action nodes and the flow primitives)"
POLICY = "{action node id: ask}; only you, signed in, let a node run on its own (POST /automations/{id}/policy)"


class Automation(ResponseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    id: int
    name: str
    description: str | None = None
    enabled: bool
    enabled_at: str | None = Field(None, description="when it was last switched on: it reacts only to what happens after")
    spaces: list[int] | None = Field(None, description="the namespaces it works in; none for every one (admins)")
    namespace_names: list[str | None] | None = None
    run_as: int = Field(description="the account it acts for, with that person's permissions: its maker")
    triggers: list[dict[str, Any]] = Field(description=TRIGGERS)
    join: dict[str, Any] = Field(description="how several triggers make one run: {mode: any} (each on its own)")
    if_: dict[str, Any] | None = Field(None, alias="if", description=IF)
    workflow: int = Field(description="its workflow (scope event)")
    version: int | None = Field(None, description="the workflow version it runs; none follows the published one")
    runs_version: int | None = Field(None, description="the version it runs now")
    graph: dict[str, Any] | None = Field(None, description=GRAPH)
    policy: dict[str, Literal["ask", "auto"]] = Field(default_factory=dict, description="{action node id: ask or auto}")
    limits: dict[str, Any] = Field(description="{cooldown (like 10m), max_per_hour}")
    counters: dict[str, int] = Field(description="{runs, skipped (its If was false), limited (over a limit), missed}")
    last_run_at: str | None = None
    last_status: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    removed_at: str | None = Field(None, description="when it was deleted (restore brings it back)")


class AutomationCatalog(ResponseModel):
    automations: list[Automation]


class AutomationCreate(RequestModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    description: str | None = None
    enabled: bool = Field(True, description="switched on, it reacts to what happens from now on")
    spaces: list[int] | None = Field(None, description="namespace ids you can edit; default: each one you can edit (every one, for admins)")
    triggers: list[dict[str, Any]] = Field(description=TRIGGERS)
    join: dict[str, Any] | None = Field(None, description="default {mode: any}: each trigger starts a run of its own")
    if_: dict[str, Any] | None = Field(None, alias="if", description=IF)
    graph: dict[str, Any] | None = Field(None, description=GRAPH + "; or name a saved one in workflow")
    workflow: int | None = Field(None, description="a saved workflow of scope event, instead of a graph")
    version: int | None = Field(None, description="with workflow: the version to run; none follows the published one")
    policy: dict[str, Literal["ask"]] | None = Field(None, description=POLICY)
    limits: dict[str, Any] | None = Field(None, description="{cooldown: 0s, max_per_hour: 60}")


class AutomationUpdate(RequestModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    spaces: list[int] | None = None
    triggers: list[dict[str, Any]] | None = None
    join: dict[str, Any] | None = None
    if_: dict[str, Any] | None = Field(None, alias="if", description=IF + "; null for none")
    graph: dict[str, Any] | None = Field(None, description="a new Then, saved as a new version of its workflow")
    workflow: int | None = None
    version: int | None = Field(None, description="the workflow version to run; null follows the published one")
    policy: dict[str, Literal["ask", "auto"]] | None = Field(
        None, description="{action node id: ask}; a node already set to auto may stay auto, and only you set auto (its own route)"
    )
    limits: dict[str, Any] | None = None


class AutomationSaved(ResponseModel):
    id: int
    hooks: list[dict[str, str]] = Field(
        default_factory=list, description="new webhook triggers' addresses and signing secrets {as, url, secret}, shown this once"
    )


class AutomationSwitch(RequestModel):
    on: bool = True


class AutomationPolicy(RequestModel):
    node: str = Field(description="an action node's id")
    value: Literal["ask", "auto"] = Field(description="ask: it asks you first; auto: it runs on its own")


class ActionInfo(ResponseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    name: str
    label: str
    group: str
    description: str = ""
    params: dict[str, Any] = Field(description="a JSON Schema (type object) of its params; a string holding {{ }} stands for any type")
    required: list[str] = Field(default_factory=list)
    effect: Literal["read", "self", "lens", "out", "depends"] = Field(
        description="what it reaches: read and self run at once; lens asks unless it changes the event's own object; out asks "
        "first unless you let it run on its own; depends: worked out from its params"
    )
    output: dict[str, Any] | None = Field(None, description="a JSON Schema of what it gives back, for later nodes (steps.<node id>)")
