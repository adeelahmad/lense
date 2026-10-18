"""Email replies (app/domain/responder.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class ReplyDraft(RequestModel):
    instructions: str | None = Field(None, max_length=2000, description="what the reply should say, in the person's words")
    template: int | None = Field(None, description="another prompt template to draft it with (default: Reply to an email)")
    version: int | None = Field(None, ge=1, description="that template's version (default: its current one)")


class ReplyEdit(RequestModel):
    text: str | None = Field(None, max_length=20000, description="the reply, as it will be sent (its [n] citations are left out)")
    to: list[str] | None = Field(None, max_length=50)
    cc: list[str] | None = Field(None, max_length=50)
    subject: str | None = Field(None, max_length=300)
    status: Literal["draft", "dismissed"] | None = Field(None, description="dismissed sets it aside; draft brings it back (undo)")


class ReplySource(ResponseModel):
    n: int
    recording_id: int
    title: str | None = None
    time: str | None = None
    page: int | None = None
    t0: int | None = None
    text: str
    used: bool = Field(description="the draft cites it")


class ReplyBefore(ResponseModel):
    text: str | None = None
    at: str | None = None


class Reply(ResponseModel):
    recording: int | None = Field(None, description="the email it answers")
    status: Literal["none", "drafting", "draft", "skipped", "dismissed", "sent", "failed"] = Field(
        description="draft: waiting for the person; drafting: a job is on it (job); skipped: mail not to answer (why); "
        "failed: the last job to draft it failed (error)"
    )
    why: str | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    others: list[str] | None = Field(None, description="the email's other recipients, to answer all of them")
    from_: str | None = Field(None, alias="from", description="the address it answers from: the one the email came in to")
    subject: str | None = None
    in_reply_to: str | None = Field(None, description="the email's Message-ID, which threads the reply")
    references: list[str] | None = None
    text: str | None = Field(None, description="the draft, with [n] after each fact taken from source n")
    plain: str | None = Field(None, description="the draft as it would be sent, without its citations")
    gaps: list[str] | None = Field(None, description="what the archive doesn't say, marked [to confirm: …] in the draft")
    unconfirmed: int | None = Field(None, description="how many [to confirm: …] marks are still in the draft")
    sources: list[ReplySource] | None = None
    job: int | None = None
    error: str | None = None
    model: str | None = None
    template: int | None = None
    version: int | None = None
    instructions: str | None = None
    edited: bool | None = Field(None, description="someone changed the draft's text")
    previous: ReplyBefore | None = Field(None, description="the text it had before the last edit or draft, to undo it")
    drafted_at: str | None = None
    updated_at: str | None = None


class ReplyEmail(ResponseModel):
    recording: int
    title: str | None = None
    namespace: str | None = None
    received_at: str | None = None
    from_: str | None = Field(None, alias="from")
    sender: str | None = Field(None, description="who sent it, as its From says")
    subject: str = ""
    snippet: str | None = None
    automatic: str | None = Field(None, description="why it looks like mail not to answer")
    reply: Reply


class ReplyList(ResponseModel):
    emails: list[ReplyEmail]
    drafts: int = Field(description="drafts waiting in these namespaces")
