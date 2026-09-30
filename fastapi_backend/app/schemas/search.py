from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ResponseModel


class SearchHit(ResponseModel):
    id: int | str = Field(description="segment id (int) for transcript hits, OCR span id (str) for screen hits")
    recording_id: int
    idx: int | None = None
    t0: int
    t1: int
    emotion: str | None = None
    speaker_id: int | None = None
    speaker: str | None = None
    title: str | None = None
    recorded_at: str | None = None
    namespace: str | None = None
    snippet: str = Field(description="HTML: escaped text with <mark> around matches")
    source: str = Field(description='"said" (transcript) or "screen" (text on screen in a video)')
    frame: str | None = Field(None, description="screen hits: signed link to the video frame")
    box: Any = Field(None, description="screen hits: where the text is on the frame")


class SearchResults(ResponseModel):
    q: str
    query: str = Field(description="how the query was understood")
    total: int
    capped: bool
    hits: list[SearchHit]


class Graph(ResponseModel):
    scope: str
    namespaces: list[str]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class Mention(ResponseModel):
    recording_id: int
    title: str | None = None
    namespace: str | None = None
    t0: int = 0
    text: str | None = None
    speaker: str | None = None
    recorded_at: str = ""
