"""What visitors see: a recording's public page (docs/access.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import AccessLevel, AccessPart, RequestModel, ResponseModel
from app.schemas.recordings import MediaKind

View = Literal["full", "public", "locked"]
RequestStatus = Literal["pending", "approved", "declined"]


class PublicRequest(ResponseModel):
    """Someone's latest request for access to a recording."""

    status: RequestStatus
    at: str | None = None
    message: str | None = None
    decided_at: str | None = None


class PublicRequestCreate(RequestModel):
    message: str | None = Field(None, max_length=1000, description="why they'd like access, for the owners")


class PublicPage(ResponseModel):
    """A page of a document, or an image, drawn."""

    idx: int = Field(description="from 0")
    width: int | None = None
    height: int | None = None
    image: str | None = Field(None, description="signed link to it, drawn; none when it couldn't be")
    thumb: str | None = Field(None, description="signed link to it, small")
    label: str | None = Field(None, description="the PDF's own name for it, when it isn't its number")


class PublicMedia(ResponseModel):
    url: str = Field(description="signed link to the audio or video; to a document's or an image's file, to save")
    kind: Literal["audio", "video", "document", "image"]
    width: int | None = None
    height: int | None = None
    poster: str | None = Field(None, description="signed link to a video's first frame, or a document's first page")
    envelope: list[Any] | None = Field(None, description="loudness over time, for drawing the waveform")
    pages: list[PublicPage] | None = Field(None, description="a document's or an image's pages")


class PublicSpeaker(ResponseModel):
    key: str
    name: str
    color: str


class PublicSegment(ResponseModel):
    t0: int = Field(description="start, in ms (for a document's text, only a reading pace)")
    t1: int = Field(description="end, in ms")
    s: str | None = Field(None, description="the speaker's key")
    text: str
    p: int | None = Field(None, description="a document's or an image's text: the page it's on (from 0)")


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


class PublicFile(ResponseModel):
    """A supplementary file a visitor may download: it follows the part its role does (transcripts, captions and
    translations the transcript, indexes the index, thumbnails the media); attachments need permission."""

    id: int
    role: str
    name: str
    label: str | None = None
    language: str | None = None
    size: int = Field(description="bytes")
    content_type: str | None = None
    url: str = Field(description="a signed link to download it")


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
    granted: bool = Field(False, description="the visitor was given permission on this recording, so they see all of it")
    network: str | None = Field(None, description="the IP group whose addresses see all of it, when that's why the visitor does")
    description: dict[str, Any] | None = Field(None, description="descriptive metadata, as IIIF publishes it")
    media: PublicMedia | None = None
    transcript: PublicTranscript | None = None
    chapters: list[PublicChapter] | None = None
    closed: list[AccessPart] = Field(description="parts the recording has that this visitor can't use")
    files: list[PublicFile] = Field(default_factory=list, description="its supplementary files this visitor may download")
    files_closed: int = Field(0, description="how many of its files this visitor can't download")
    can_request: bool = Field(False, description="signed in without permission, with something closed: they may ask for access")
    request: PublicRequest | None = Field(None, description="their latest request for access, if they made one")


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
    network: str | None = Field(None, description="the IP group that opens all of it to the visitor's address")


class PublicHome(ResponseModel):
    featured: list[PublicCard] = Field(description="featured public recordings, newest first")
    shared: list[PublicCard] = Field(
        default_factory=list, description="recordings the visitor was given permission on, outside their namespaces"
    )
    collections: list[PublicCollectionSummary] = Field(description="the collections this visitor sees anything in")


class PublicCollection(ResponseModel):
    name: str
    label: str
    summary: str | None = None
    rights: str | None = None
    attribution: str | None = None
    provider: dict[str, Any] | None = None
    member: bool = Field(description="the visitor has a role in this namespace, so they see all of its recordings")
    network: str | None = Field(None, description="the IP group that opens all of its recordings to the visitor's address")
    total: int = Field(description="how many recordings this visitor sees here, on all pages")
    items: list[PublicCard]


class PublicHit(ResponseModel):
    t0: int = Field(description="where the line starts, in ms")
    snippet: str = Field(description="the line around the match, HTML-escaped, with <mark> around what was found")
    page: int | None = Field(None, description="a document's or an image's text: the page it's on (from 0)")


class PublicResult(PublicCard):
    hits: list[PublicHit] = Field(
        default_factory=list, description="matching transcript lines, best first; none where the transcript is closed"
    )


class PublicSearch(ResponseModel):
    q: str
    total: int = Field(description="how many recordings match, on all pages")
    capped: bool = Field(description="there were too many matching lines to rank them all")
    items: list[PublicResult]
