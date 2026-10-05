from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class NamespaceAssistant(ResponseModel):
    enabled: bool = Field(description="conversations scoped to this namespace alone talk to its assistant")
    name: str
    instructions: str = Field("", description="what its owners tell it, read with every question")
    memories: int = Field(0, description="how many things it remembers")
    updated_at: str | None = None
    updated_by: str | None = None


class NamespaceAssistantUpdate(RequestModel):
    """Only the fields you send change."""

    enabled: bool | None = None
    name: str | None = Field(None, max_length=60)
    instructions: str | None = Field(None, max_length=4000)


class AssistantMemory(ResponseModel):
    id: int
    text: str
    recording: int | None = Field(None, description="the recording it came from")
    t0: int | float | None = Field(None, description="the moment in that recording (ms)")
    title: str | None = Field(None, description="that recording's title")
    time: str | None = Field(None, description='that moment as "12:34"')
    chat: int | None = Field(None, description="the conversation it was told in")
    author: str = Field(description="assistant (kept from a conversation) or person (written here)")
    pinned: bool = Field(False, description="always read with every question")
    created_at: str | None = None
    updated_at: str | None = None


class AssistantMemoryCreate(RequestModel):
    text: str = Field(max_length=500)
    pinned: bool = False


class AssistantMemoryUpdate(RequestModel):
    text: str | None = Field(None, max_length=500)
    pinned: bool | None = None
