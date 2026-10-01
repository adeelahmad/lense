"""A resource's files: its primary file and the supplementary ones kept beside it."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

FileRole = Literal["transcript", "captions", "translation", "index", "thumbnail", "attachment"]


class PrimaryFile(ResponseModel):
    """The audio, video, document or image the resource's pipeline runs on."""

    name: str | None = None
    kind: Literal["audio", "video", "document", "image"]
    size: int | None = Field(None, description="bytes")
    content_type: str | None = None
    download: str = Field(description="a signed link to it")
    pdf: str | None = Field(None, description="a document that isn't a PDF: a signed link to the PDF made of it")


class ResourceFile(ResponseModel):
    id: int
    role: FileRole
    name: str
    size: int = Field(description="bytes")
    content_type: str
    language: str | None = Field(None, description="what language it's in (a code such as en or pt-BR)")
    label: str | None = None
    description: str | None = None
    lines: int | None = Field(None, description="transcripts, captions, translations and indexes: how many lines were read from it")
    timed: bool | None = Field(None, description="whether its lines say when they are (else they have no times)")
    resource: int | None = Field(None, description="an email's attachment: the resource it became (or the one it was already)")
    public: bool = Field(
        description="everyone may download it: the resource is public with the part its role follows open (attachments never are)"
    )
    created_at: str | None = None
    created_by: str | None = Field(None, description="who added it: their email")
    created_by_name: str | None = Field(None, description="their name, when they gave one")
    updated_at: str | None = None
    download: str = Field(description="a signed link to it")


class ResourceFiles(ResponseModel):
    primary: PrimaryFile | None = Field(None, description="none for a transcript without audio or video")
    files: list[ResourceFile] = Field(description="by role, then the earliest added first")
    max_mb: int = Field(description="the largest file that can be added (server.max_upload_mb)")
    can_change: bool = Field(description="you may add, change and delete its files (editors)")


class FileUpdate(RequestModel):
    role: FileRole | None = Field(None, description="a new role; transcripts, captions, translations and indexes are read again")
    language: str | None = Field(None, max_length=40, description="null clears it")
    label: str | None = Field(None, max_length=200, description="null clears it")
    description: str | None = Field(None, max_length=2000, description="null clears it")


class FileLine(ResponseModel):
    idx: int
    t0: int | None = Field(None, description="ms; none in a file that doesn't say when its lines are")
    t1: int | None = None
    text: str = Field(description="the line; for an index, its title, synopsis and keywords together")
    speaker: str | None = Field(None, description="who says it, as the file names them")
    title: str | None = Field(None, description="an index entry's title")
    synopsis: str | None = Field(None, description="an index entry's synopsis")
    keywords: list[str] | None = Field(None, description="an index entry's keywords")


class FileLines(ResponseModel):
    total: int
    lines: list[FileLine]
