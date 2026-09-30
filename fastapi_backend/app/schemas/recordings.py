from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import AccessLevel, AccessPart, Ok, RequestModel, ResponseModel, Role

# Recording statuses, plus two job states: a job queued or running (processing), the latest job failed (failed).
RecordingState = Literal["new", "transcribed", "diarized", "analyzed", "error", "processing", "failed"]
MediaKind = Literal["audio", "video", "transcript"]
RecordingSort = Literal[
    "date",
    "-date",
    "title",
    "-title",
    "duration",
    "-duration",
    "speakers",
    "-speakers",
    "status",
    "-status",
    "importance",
    "-importance",
]


class RecordingSummary(ResponseModel):
    id: int
    title: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    status: str | None = None
    error: str | None = None
    source: str | None = None
    space: int
    namespace: str | None = None
    media_kind: str = Field(description="audio, video or transcript")
    poster: str | None = Field(None, description="signed link to the first video frame")
    emotions: dict[str, Any] = Field(default_factory=dict)
    words: int | None = None
    importance: Any = None
    sentiment: Any = None
    speakers: str = Field("", description="speaker names, comma separated")
    access: AccessLevel = Field("private", description="its own access, or its namespace's default")
    open: list[AccessPart] = Field(default_factory=list, description="the parts anyone may use when it is public")
    featured: bool = False


class RecordingSpeaker(ResponseModel):
    id: int
    name: str
    method: str | None = None
    score: float | None = None


class Recording(ResponseModel):
    """The recording row (less its envelope) plus what the recording page needs."""

    id: int
    title: str | None = None
    status: str | None = None
    space: int
    namespace: str | None = None
    role: Role | None = Field(None, description="your role in its namespace")
    summary: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    report_url: str | None = Field(None, description="signed link to the built report page, if one exists")
    speakers: list[RecordingSpeaker] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    access: AccessLevel = Field("private", description="its own access, or its namespace's default")
    open: list[AccessPart] = Field(default_factory=list, description="the parts anyone may use when it is public")
    featured: bool = False
    access_inherited: bool = Field(True, description="the access comes from the namespace's default")


class Player(ResponseModel):
    """Everything the player shows: transcript, speakers, sections, entities and, for videos, shots and faces."""

    id: int
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    duration_ms: int | None = None
    audio: str | None = Field(None, description="signed audio link, or null when there is no audio")
    speakers: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    keywords: list[Any] = Field(default_factory=list)
    envelope: list[Any] | None = None
    summary: dict[str, Any] | None = None
    media: dict[str, Any] | None = None


class RecordingUpdate(RequestModel):
    """The fields to change; the others stay as they are."""

    title: str | None = Field(None, min_length=1, max_length=200, description="whitespace is collapsed")


class NamespaceAccess(ResponseModel):
    access: AccessLevel
    open: list[AccessPart]


class RecordingAccess(ResponseModel):
    """Who may see the recording: public, restricted or private (docs/access.md)."""

    access: AccessLevel
    open: list[AccessPart] = Field(description="the parts anyone may use when it is public: media, transcript, index")
    featured: bool
    inherited: bool = Field(description="access and open come from the namespace's default")
    default: NamespaceAccess = Field(description="the namespace's default")


class RecordingAccessUpdate(RequestModel):
    """Send the settings to change. access or open set to null follow the namespace's default again."""

    access: AccessLevel | None = None
    open: list[AccessPart] | None = None
    featured: bool | None = None


class ReprocessRequest(RequestModel):
    steps: list[str | dict[str, Any]] | None = Field(None, description="default: the namespace's pipeline")
    pipeline: int | None = None


class JobQueued(Ok):
    job: int


class ShareCreate(RequestModel):
    days: int = Field(30, ge=1, le=3650)


class ShareLink(ResponseModel):
    token: str
    embed: str = Field(description="embeddable player link carrying the share token (works without signing in)")


class Share(ResponseModel):
    id: str = Field(description="first characters of the link's id")
    created_by: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    active: bool


class SegmentUpdate(RequestModel):
    """Send ``text``, ``speaker`` (a speaker id in the same namespace, or null), or both."""

    text: str | None = None
    speaker: int | None = None


class SegmentEdit(ResponseModel):
    idx: int
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    by: str | None = None
    at: str | None = None


class Output(ResponseModel):
    key: str
    value: Any = None
    origin: Any = None
    created_at: str | None = None


class EmbedLink(ResponseModel):
    url: str = Field(description="signed link to the embeddable player")
