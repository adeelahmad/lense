"""Comments on a resource: threads everyone who can read it joins; and highlights, passages its editors mark in
colour."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Colour = Literal["yellow", "green", "blue", "red"]


class FlagReason(ResponseModel):
    reason: Literal["spam", "abuse", "personal"]
    label: str
    p: float = Field(description="how sure the decision model was, 0 to 1")


class Comment(ResponseModel):
    id: int
    recording: int
    parent: int | None = Field(None, description="the comment this replies to (the thread's first); null: it starts a thread")
    text: str
    t0: int | None = Field(None, description="the moment it's about, in ms from the start; null: the whole resource, or a reply")
    t1: int | None = Field(None, description="where that moment ends, in ms")
    quote: str | None = Field(None, description="the words picked in the text")
    resolved: bool = Field(False, description="the thread is resolved (its first comment says so)")
    resolved_by: str | None = Field(None, description="who resolved it: their email")
    resolved_by_name: str | None = None
    resolved_at: str | None = None
    created_by: str | None = Field(None, description="its writer's email")
    created_by_name: str | None = Field(None, description="its writer's name, when they gave one")
    created_at: str | None = None
    updated_at: str | None = None
    edited_at: str | None = Field(None, description="when its text last changed")
    mine: bool = Field(description="you wrote it: only you can change its text")
    can_resolve: bool = Field(description="you can resolve or reopen the thread: you started it, or you edit the resource")
    can_delete: bool = Field(description="you wrote it, or you own the resource")
    flagged: list[FlagReason] = Field(
        default_factory=list, description="why a decision model flagged it for review; only owners of the resource see this"
    )


class FlaggedComment(ResponseModel):
    """A comment waiting for an owner's look."""

    id: int
    recording: int
    title: str | None = Field(None, description="the resource's title")
    namespace: str | None = None
    text: str
    created_by: str | None = None
    created_by_name: str | None = None
    created_at: str | None = None
    flagged: list[FlagReason]
    flagged_at: str | None = None


class CommentCreate(RequestModel):
    text: str = Field(min_length=1, max_length=5000)
    parent: int | None = Field(None, description="reply on this comment's thread (its moment is the thread's)")
    t0: int | None = Field(None, ge=0, description="the moment it's about, in ms from the start; leave out for the whole resource")
    t1: int | None = Field(None, ge=0, description="where that moment ends, in ms (default: t0)")
    quote: str | None = Field(None, max_length=1000, description="the words picked in the text")


class CommentUpdate(RequestModel):
    text: str | None = Field(None, min_length=1, max_length=5000, description="its writer only")
    resolved: bool | None = Field(None, description="resolve or reopen the thread: its writer, or an editor of the resource")


class Highlight(ResponseModel):
    id: int
    recording: int
    t0: int = Field(description="where the passage starts, in ms from the start")
    t1: int = Field(description="where it ends, in ms")
    quote: str | None = Field(None, description="the words picked in the text")
    colour: Colour
    label: str | None = Field(None, description="what it marks, in a few words")
    created_by: str | None = Field(None, description="who made it: their email")
    created_by_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    mine: bool = Field(description="you made it")
    can_edit: bool = Field(description="you can change or delete it: you edit the resource")


class HighlightCreate(RequestModel):
    t0: int = Field(ge=0, description="where the passage starts, in ms from the start")
    t1: int | None = Field(None, ge=0, description="where it ends, in ms (default: t0)")
    quote: str | None = Field(None, max_length=1000, description="the words picked in the text")
    colour: Colour = "yellow"
    label: str | None = Field(None, max_length=200, description="what it marks, in a few words")


class HighlightUpdate(RequestModel):
    colour: Colour | None = None
    label: str | None = Field(None, max_length=200, description="a new label; empty clears it")
