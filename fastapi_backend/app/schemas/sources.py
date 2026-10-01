"""Storage sources (where files live, through rclone) and watched folders (admins)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import Created, Ok, RequestModel, ResponseModel

SourceType = Literal["s3", "dropbox", "drive", "onedrive", "sftp", "smb", "webdav", "local"]
WatchKinds = Literal["audio", "transcripts", "both", "documents", "all"]
KINDS_HELP = (
    "what to pick up: audio (and video), transcripts, both of those (PDFs read as transcripts), documents (PDFs and "
    "images), or all of them (PDFs as documents; the default)"
)


class Backend(ResponseModel):
    label: str
    fields: dict[str, str] = Field(description="parameters and their defaults")
    secrets: list[str]
    oauth: str | None = Field(default=None, description="the command that produces the token, for OAuth backends")


class SourceHealth(ResponseModel):
    ok: bool
    checked_at: str | None = None
    error: str | None = None


class SecretFlag(ResponseModel):
    secret: bool = True
    set: bool


class Source(ResponseModel):
    id: int
    name: str
    type: SourceType
    label: str
    params: dict[str, Any]
    secrets: dict[str, SecretFlag]
    oauth: str | None = None
    health: SourceHealth | None = None
    created_at: str | None = None
    watches: int = 0


class SourceCreate(RequestModel):
    name: str | None = None
    type: SourceType
    params: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = Field(default=None, description="stored encrypted; never shown again")


class SourceUpdate(RequestModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = Field(default=None, description="null or empty removes a secret")


class SourceCreated(Created):
    health: SourceHealth


class SourceUpdated(Ok):
    health: SourceHealth


class ImportedAs(ResponseModel):
    recording: int
    namespace: str


class BrowseEntry(ResponseModel):
    path: str
    rel: str
    name: str
    dir: bool
    size: int | None = None
    modified: str | None = None
    imported: list[ImportedAs] = Field(default_factory=list, description="the recordings this file already is, and where")


class WatchOptions(RequestModel):
    kinds: WatchKinds | None = Field(default=None, description=KINDS_HELP)
    poll_minutes: int | None = Field(default=None, ge=1)
    stable_seconds: int | None = Field(default=None, ge=0)
    backfill: bool | None = Field(default=None, description="also import files already there")
    include: list[str] | None = None
    exclude: list[str] | None = None
    steps: list[str] | None = None
    pipeline: int | None = None
    enabled: bool | None = None


class WatchCreate(WatchOptions):
    source: int
    path: str = ""
    namespace: str


class WatchUpdate(WatchOptions):
    pass


class WatchPreviewRequest(RequestModel):
    source: int
    path: str = ""
    kinds: WatchKinds | None = Field(default=None, description=KINDS_HELP)
    include: list[str] | None = None
    exclude: list[str] | None = None


class WatchPreview(ResponseModel):
    files: int
    audio: int = Field(description="audio and video")
    transcripts: int
    documents: int = 0
    images: int = 0


class Watch(ResponseModel):
    id: int
    source: int
    path: str
    space: int
    namespace: str | None = None
    source_name: str | None = None
    kinds: WatchKinds | None = None
    poll_minutes: int | None = None
    stable_seconds: int | None = None
    backfill: bool | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    steps: list[str] | None = None
    enabled: bool | None = None
    last_scan_at: str | None = None
    next_scan_at: str | None = None
    last_stats: dict[str, Any] | None = None
    last_error: str | None = None
