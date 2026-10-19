"""Saved views of the Library: its tab, filters and sort under a name."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.recordings import RecordingSort, RecordingState

LibraryTab = Literal["all", "attention", "processing", "mine"]
Tag = Annotated[str, Field(min_length=1, max_length=40)]
DAY = r"^\d{4}-\d{2}-\d{2}$"
ORIGIN = r"^(upload|paste|iiif|folder|file|source:\d+)$"


class ViewState(RequestModel):
    """What the Library shows. Dates and lengths are the Library's ranges, so "the last 7 days" stays the last 7 days."""

    tab: LibraryTab = "all"
    q: str = Field("", max_length=200, description="the filter box")
    statuses: list[RecordingState] = Field(default_factory=list, max_length=7)
    speaker: str | None = Field(None, max_length=200, description="a person's name: whoever has it in the namespaces shown")
    date: Literal["any", "today", "7d", "30d", "90d", "1y"] = "any"
    duration: Literal["any", "short", "medium", "long", "xlong"] = Field(
        "any", description="short: under 10 min; medium: 10–30; long: 30–60; xlong: over an hour"
    )
    media: Literal["any", "audio", "video", "transcript", "document", "image"] = "any"
    tags: list[Tag] = Field(default_factory=list, max_length=20, description="any of these")
    origins: list[Annotated[str, Field(pattern=ORIGIN)]] = Field(
        default_factory=list, max_length=20, description="where they came from (GET /recordings `origin`)"
    )
    languages: list[Annotated[str, Field(min_length=1, max_length=20)]] = Field(
        default_factory=list, max_length=20, description="language codes; none: not known"
    )
    objects: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=20, description="kinds of object seen in them (GET /recordings `object`): any of these"
    )
    sort: RecordingSort = "-date"
    collection: int | None = Field(None, description="a collection of the view's namespace (and the ones inside it)")
    field: int | None = Field(None, description="a custom field of the view's namespace: the assets with a value for it")
    value: str | None = Field(None, max_length=200, description="with field: the value to match (GET /recordings `value`)")


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


class SearchState(RequestModel):
    """A search as /search takes it (its namespace is the saved search's)."""

    q: str = Field(min_length=1, max_length=500, description='words, "phrases", OR between alternatives, NOT or -word to leave out')
    speaker: int | None = None
    emotion: str | None = Field(None, max_length=40)
    event: str | None = Field(None, max_length=40, description="a sound heard (laughter, applause, music …)")
    recording: int | None = None
    object: str | None = Field(None, max_length=60, description="a kind of object the assets have (person, car …)")
    date_from: str | None = Field(None, pattern=DAY, description="assets recorded on or after this day (/search `from`)")
    date_to: str | None = Field(None, pattern=DAY, description="assets recorded on or before this day (/search `to`)")
    tag: str | None = Field(None, max_length=100, description="assets with this tag")
    origin: str | None = Field(None, pattern=ORIGIN, description="assets that came from here (upload, folder, source:<id> …)")
    language: str | None = Field(None, max_length=20, description="assets in this language (a code; none: not known)")


class SavedSearch(ResponseModel):
    id: int
    name: str
    namespace: str | None = Field(None, description="the namespace it searches; null: every namespace you can read")
    shared: bool = Field(False, description="everyone with a role in its namespace sees it")
    q: str
    speaker: int | None = None
    speaker_name: str | None = Field(None, description="the person's name, when you can read their namespace")
    emotion: str | None = None
    event: str | None = None
    recording: int | None = None
    recording_title: str | None = Field(None, description="the asset's title, when you can read it")
    object: str | None = Field(None, description="a kind of object the assets have")
    date_from: str | None = Field(None, description="assets recorded on or after this day")
    date_to: str | None = Field(None, description="assets recorded on or before this day")
    tag: str | None = None
    origin: str | None = Field(None, description="where the assets came from (upload, folder, source:<id> …)")
    language: str | None = Field(None, description="the assets' language")
    created_by: str | None = Field(None, description="its maker's email")
    created_at: str | None = None
    updated_at: str | None = None
    mine: bool = Field(description="you made it: only you can change it")
    can_delete: bool = Field(description="you made it, or it's shared and you own its namespace")


class SearchCreate(SearchState):
    name: str = Field(min_length=1, max_length=60)
    namespace: str | None = Field(None, description="search one namespace; null: every namespace you can read")
    shared: bool = Field(False, description="share it with its namespace (needs editor access there)")


class SearchUpdate(RequestModel):
    """Only what you send changes."""

    name: str | None = Field(None, min_length=1, max_length=60)
    shared: bool | None = Field(None, description="share it with its namespace (needs editor access there), or stop")
