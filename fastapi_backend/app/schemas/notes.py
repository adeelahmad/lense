"""Notes on a recording: yours, or shared with everyone who can read it."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class Note(ResponseModel):
    id: int
    recording: int
    text: str
    t0: int | None = Field(None, description="the moment it's about, in ms from the start; null: the whole recording")
    t1: int | None = Field(None, description="where that moment ends, in ms")
    quote: str | None = Field(None, description="the words picked in the transcript")
    shared: bool = Field(False, description="everyone who can read the recording sees it")
    created_by: str | None = Field(None, description="its writer's email")
    created_by_name: str | None = Field(None, description="its writer's name, when they gave one")
    created_at: str | None = None
    updated_at: str | None = None
    edited_at: str | None = Field(None, description="when its text last changed")
    mine: bool = Field(description="you wrote it: only you can change it")
    can_delete: bool = Field(description="you wrote it, or it's shared and you own the recording's namespace")


class NoteCreate(RequestModel):
    text: str = Field(min_length=1, max_length=5000)
    t0: int | None = Field(None, ge=0, description="the moment it's about, in ms from the start; leave out for the whole recording")
    t1: int | None = Field(None, ge=0, description="where that moment ends, in ms (default: t0)")
    quote: str | None = Field(None, max_length=1000, description="the words picked in the transcript")
    shared: bool = Field(False, description="share it with everyone who can read the recording (needs editor access)")


class NoteUpdate(RequestModel):
    text: str | None = Field(None, min_length=1, max_length=5000)
    shared: bool | None = Field(None, description="share or unshare it (sharing needs editor access)")
