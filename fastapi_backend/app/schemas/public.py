"""What visitors see: a recording's public page (docs/access.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import AccessLevel, AccessPart, ResponseModel
from app.schemas.recordings import MediaKind

View = Literal["full", "public", "locked"]


class PublicMedia(ResponseModel):
    url: str = Field(description="signed link to the audio or video")
    kind: Literal["audio", "video"]
    width: int | None = None
    height: int | None = None
    poster: str | None = Field(None, description="signed link to a video's first frame")
    envelope: list[Any] | None = Field(None, description="loudness over time, for drawing the waveform")


class PublicSpeaker(ResponseModel):
    key: str
    name: str
    color: str


class PublicSegment(ResponseModel):
    t0: int = Field(description="start, in ms")
    t1: int = Field(description="end, in ms")
    s: str | None = Field(None, description="the speaker's key")
    text: str


class PublicDownload(ResponseModel):
    format: str
    label: str
    url: str


class PublicTranscript(ResponseModel):
    speakers: list[PublicSpeaker]
    segments: list[PublicSegment]
    downloads: list[PublicDownload] = Field(description="transcript files; offered when the transcript is open to everyone")


class PublicChapter(ResponseModel):
    t0: int
    t1: int | None = None
    title: str | None = None


class PublicRecording(ResponseModel):
    """A recording as this visitor may see it: all of it with permission, else its page, description and open parts,
    or (restricted, signed in) its title only. Parts left out are null."""

    id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    media_kind: MediaKind
    view: View = Field(description="full: with permission; public: a public recording; locked: restricted, listed with a lock")
    access: AccessLevel
    open: list[AccessPart] = Field(description="the parts a public recording opens to everyone")
    featured: bool
    member: bool = Field(description="the visitor has a role in the recording's namespace, so it opens in the workspace too")
    description: dict[str, Any] | None = Field(None, description="descriptive metadata, as IIIF publishes it")
    media: PublicMedia | None = None
    transcript: PublicTranscript | None = None
    chapters: list[PublicChapter] | None = None
    closed: list[AccessPart] = Field(description="parts the recording has that this visitor can't use")


class PublicCard(ResponseModel):
    """A recording in a list, as this visitor may see it: a locked one (restricted, signed in without permission) shows
    its title only."""

    id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    media_kind: MediaKind
    view: View
    access: AccessLevel
    featured: bool
    summary: str | None = None
    poster: str | None = Field(None, description="signed link to a video's first frame, when the visitor may play it")


class PublicCollectionSummary(ResponseModel):
    name: str
    label: str
    summary: str | None = None
    recordings: int = Field(description="how many of its recordings this visitor sees")
    member: bool


class PublicHome(ResponseModel):
    featured: list[PublicCard] = Field(description="featured public recordings, newest first")
    collections: list[PublicCollectionSummary] = Field(description="the collections this visitor sees anything in")


class PublicCollection(ResponseModel):
    name: str
    label: str
    summary: str | None = None
    rights: str | None = None
    attribution: str | None = None
    provider: dict[str, Any] | None = None
    member: bool = Field(description="the visitor has a role in this namespace, so they see all of its recordings")
    total: int = Field(description="how many recordings this visitor sees here, on all pages")
    items: list[PublicCard]


class PublicHit(ResponseModel):
    t0: int = Field(description="where the line starts, in ms")
    snippet: str = Field(description="the line around the match, HTML-escaped, with <mark> around what was found")


class PublicResult(PublicCard):
    hits: list[PublicHit] = Field(
        default_factory=list, description="matching transcript lines, best first; none where the transcript is closed"
    )


class PublicSearch(ResponseModel):
    q: str
    total: int = Field(description="how many recordings match, on all pages")
    capped: bool = Field(description="there were too many matching lines to rank them all")
    items: list[PublicResult]
