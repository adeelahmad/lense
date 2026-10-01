"""Conversations with the archive, the assistant's approvals and checking an answer against its sources."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import RequestModel, ResponseModel


class ChatScope(RequestModel):
    """What a conversation may draw on. Every field narrows it; leave them out to use everything you can read."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    namespaces: list[str] | None = None
    recordings: list[int] | None = None
    speakers: list[int] | None = None
    date_from: str | None = Field(default=None, alias="from", description="YYYY-MM-DD")
    date_to: str | None = Field(default=None, alias="to", description="YYYY-MM-DD")


class ChatCreate(RequestModel):
    title: str | None = None
    scope: ChatScope | None = None


class ChatUpdate(RequestModel):
    title: str | None = None
    scope: ChatScope | None = None


class ChatSummary(ResponseModel):
    id: int
    title: str
    scope: dict[str, Any] = {}
    created_at: str | None = None
    updated_at: str | None = None


class Passage(ResponseModel):
    n: int
    recording_id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    t0: int | float | None = None
    time: str | None = None
    speaker: str | None = None
    text: str
    used: bool | None = Field(default=None, description="cited in the answer")


class ChatMessage(ResponseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    passages: list[Passage] | None = None
    created_at: str | None = None
    stopped: bool = Field(False, description="the answer was stopped (POST /chats/{cid}/stop): `content` is what came before")


class ChatCapabilities(ResponseModel):
    configured: bool = Field(description="a language model is set up; without one, answers are the best-matching passages")
    model: str | None = Field(None, description="the model answers come from")
    tools: bool = Field(description="the assistant looks things up with tools (and may propose work for approval)")
    max_steps: int = Field(description="the most tool calls it makes for one answer")
    check: bool = Field(description="answers can be checked against their sources")


class StopResult(ResponseModel):
    ok: bool = True
    stopping: bool = Field(description="an answer was being written, and stops after its current piece or step")


class Chat(ChatSummary):
    account: int
    messages: list[ChatMessage] = []


class MessageCreate(RequestModel):
    content: str = Field(description="the question (up to 4000 characters)")


class AnswerCheck(ResponseModel):
    claims: int = Field(description="sentences that cite a source")
    supported: int = Field(description="claims the cited excerpts support")
    verdicts: list[dict[str, Any]] = []
    uncited: list[str] = []


class Approval(ResponseModel):
    id: int
    chat: int | None = None
    tool: str
    summary: str | None = None
    estimate: Any = None
    status: str
    created_at: str | None = None
    result: Any = None


class ApprovalDecision(RequestModel):
    decision: Literal["approve", "sample", "decline"] = "approve"


class ApprovalOutcome(ResponseModel):
    status: str
    batch: int | None = Field(default=None, description="the batch run started, for run_template")
