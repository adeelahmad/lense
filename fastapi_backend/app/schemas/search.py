from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ResponseModel


class SearchHit(ResponseModel):
    id: int | str = Field(description="segment id (int) for transcript hits, OCR span or file line id (str) for the others")
    recording_id: int
    idx: int | None = None
    t0: int | None = Field(description="ms; None for a line of a file that doesn't say when its lines are")
    t1: int | None
    emotion: str | None = None
    speaker_id: int | None = None
    speaker: str | None = None
    title: str | None = None
    recorded_at: str | None = None
    namespace: str | None = None
    snippet: str = Field(description="HTML: escaped text with <mark> around matches")
    source: Literal["said", "screen", "file", "page", "object", "described"] = Field(
        description='"said" (transcript), "screen" (text on screen in a video), "file" (a line of a supplementary file), '
        '"page" (text on a page of a document or an image, whose times are only a reading pace), "object" (a kind of '
        "object seen in a video, a document or an image, where it's first seen; its snippet is the kind) or "
        '"described" (what a model that can see said a shot of a video, or a page, shows)'
    )
    frame: str | None = Field(None, description="screen, object and described hits: signed link to the video frame or the page")
    box: Any = Field(None, description="screen, page and object hits: where it is on the frame or the page ([x, y, w, h] fractions)")
    page: int | None = Field(None, description="page hits, and object and described hits on pages: which page (from 0)")
    file: int | None = Field(None, description="file hits: the supplementary file the line is in")
    file_role: str | None = Field(None, description="file hits: its role (transcript, captions, translation or index)")
    file_label: str | None = Field(None, description="file hits: its label, or its name")
    line: int | None = Field(None, description="file hits: which of its lines (from 0)")


class FacetCount(ResponseModel):
    name: str
    count: int = Field(description="matching moments")


class SpeakerFacet(ResponseModel):
    id: int
    name: str
    namespace: str | None = None
    count: int = Field(description="matching moments")


class RecordingFacet(ResponseModel):
    id: int
    title: str | None = None
    count: int = Field(description="matching moments")


class ObjectFacet(ResponseModel):
    name: str = Field(description="a kind of object (person, car …)")
    count: int = Field(description="how many of the recordings with matching moments it's seen in")


class SearchFacets(ResponseModel):
    """How many of all the matching moments are in each namespace, speaker, emotion and recording (up to 50 of each,
    most first), and the kinds of object seen in the recordings they're in. Text on screen has no speaker or emotion."""

    moments: int = Field(description="the matching moments counted: all of them, unless `partial`")
    partial: bool = Field(description="more than 20,000 moments match; the counts cover 20,000 of them")
    namespaces: list[FacetCount] = Field(default_factory=list)
    speakers: list[SpeakerFacet] = Field(default_factory=list)
    emotions: list[FacetCount] = Field(default_factory=list)
    recordings: list[RecordingFacet] = Field(default_factory=list)
    objects: list[ObjectFacet] = Field(default_factory=list)


class SearchResults(ResponseModel):
    q: str
    query: str = Field(description="how the query was understood")
    total: int
    capped: bool
    hits: list[SearchHit]
    facets: SearchFacets | None = Field(None, description="with `facets=true`: counts over all the matching moments")


class TermSuggestion(ResponseModel):
    word: str
    count: int = Field(description="how often it's said")
    recordings: int = Field(description="in how many recordings")


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
