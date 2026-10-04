from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ResponseModel
from app.schemas.jobs import ComponentState, Machine


class Component(ResponseModel):
    id: str
    label: str
    purpose: str
    kind: Literal["program", "package", "model", "server-model"] = Field(
        description="program: the image provides it (only checked); package and model: fetched into the data folder; "
        "server-model: pulled on the Ollama server"
    )
    steps: list[str] = Field(default_factory=list, description="the job steps that wait for it")
    size_mb: int | None = Field(None, description="about how much it downloads")
    optional: bool = Field(False, description="fetched only when listed in components.also")
    license: str | None = None
    needed: bool = Field(description="this server's settings ask for it")
    hint: str | None = Field(None, description="for a program: how to get it")
    here: bool | None = Field(None, description="for a program: whether the API's machine has it")


class WorkerComponents(ResponseModel):
    name: str
    host: str | None = None
    heartbeat_at: str | None = None
    machine: Machine | None = None
    components: dict[str, ComponentState] = Field(default_factory=dict)


class Components(ResponseModel):
    auto: bool = Field(description="components.auto: fetch what's needed without asking")
    machine: Machine = Field(description="the API's machine")
    recommended: dict[str, Any] = Field(description="the transcription settings that suit the API's machine")
    components: list[Component]
    workers: list[WorkerComponents]
