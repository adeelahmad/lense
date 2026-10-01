"""Saved views of the Library: its tab, filters and sort under a name."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.recordings import RecordingSort, RecordingState

LibraryTab = Literal["all", "attention", "processing"]
Tag = Annotated[str, Field(min_length=1, max_length=40)]


class ViewState(RequestModel):
    """What the Library shows. Dates and lengths are the Library's ranges, so "the last 7 days" stays the last 7 days."""

    tab: LibraryTab = "all"
    q: str = Field("", max_length=200, description="the filter box")
    statuses: list[RecordingState] = Field(default_factory=list, max_length=7)
    speaker: str | None = Field(None, max_length=200, description="a speaker's name: whoever has it in the namespaces shown")
    date: Literal["any", "today", "7d", "30d", "90d", "1y"] = "any"
    duration: Literal["any", "short", "medium", "long", "xlong"] = Field(
        "any", description="short: under 10 min; medium: 10–30; long: 30–60; xlong: over an hour"
    )
    media: Literal["any", "audio", "video", "transcript"] = "any"
    tags: list[Tag] = Field(default_factory=list, max_length=20, description="any of these")
    sort: RecordingSort = "-date"


class SavedView(ResponseModel):
    id: int
    name: str
    namespace: str | None = Field(None, description="the namespace it shows; null: every namespace you can read")
    shared: bool = Field(False, description="everyone with a role in its namespace sees it")
    state: ViewState
    created_by: str | None = Field(None, description="its maker's email")
    created_at: str | None = None
    updated_at: str | None = None
    mine: bool = Field(description="you made it: only you can change it")
    can_delete: bool = Field(description="you made it, or it's shared and you own its namespace")


class ViewCreate(RequestModel):
    name: str = Field(min_length=1, max_length=60)
    namespace: str | None = Field(None, description="the namespace it shows; null: every namespace you can read")
    shared: bool = Field(False, description="share it with its namespace (needs editor access there)")
    state: ViewState = Field(default_factory=ViewState)


class ViewUpdate(RequestModel):
    """Only what you send changes."""

    name: str | None = Field(None, min_length=1, max_length=60)
    shared: bool | None = Field(None, description="share it with its namespace (needs editor access there), or stop")
    state: ViewState | None = Field(None, description="what it shows now")
