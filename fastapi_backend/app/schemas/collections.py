"""Collections: a saved filter over the archive, or a fixed list of recordings. Private unless shared."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Filter = dict[str, Any]
FILTER_HELP = "keys: namespaces, speakers, entities, from, to, q, media, status"


class CollectionCreate(RequestModel):
    name: str
    description: str | None = None
    filter: Filter | None = Field(default=None, description=FILTER_HELP)
    recordings: list[int] | None = Field(default=None, description="a fixed list instead of a filter")
    shared: bool = False


class CollectionUpdate(RequestModel):
    name: str | None = None
    description: str | None = None
    filter: Filter | None = Field(default=None, description=FILTER_HELP)
    recordings: list[int] | None = None
    shared: bool | None = None


class CollectionBase(ResponseModel):
    id: int
    account: int
    name: str
    description: str | None = None
    kind: Literal["filter", "fixed"]
    filter: Filter | None = None
    shared: bool = False
    updated_at: str | None = None
    count: int = Field(description="recordings in it you can read")


class Collection(CollectionBase):
    recordings: list[int] | None = None


class CollectionRecording(ResponseModel):
    id: int
    title: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None


class CollectionDetail(CollectionBase):
    created_at: str | None = None
    recordings: list[CollectionRecording] = Field(description="the newest 200 you can read")
