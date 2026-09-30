from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

TranscriptFormat = Literal["auto", "text", "markdown", "mdx", "json", "jsonl", "srt", "vtt"]


class ImportPreviewRequest(RequestModel):
    """A file (``filename`` + base64 ``data``) or pasted ``text``."""

    format: TranscriptFormat = "auto"
    filename: str | None = None
    data: str | None = Field(None, description="the file, base64 encoded")
    text: str | None = None


class ImportRequest(ImportPreviewRequest):
    namespace: str = Field("", description="created if it doesn't exist (admins only)")
    title: str | None = None
    speakers: str | None = Field(None, description='rename speakers on the way in: "S1=Alice,S2=Bob"')


class ImportResult(ResponseModel):
    ok: bool = True
    id: int
    job: int


class PreviewLine(ResponseModel):
    time: str
    speaker: str | None = None
    text: str


class ImportPreview(ResponseModel):
    format: str
    title: str | None = None
    segments: int
    duration_ms: int
    speakers: list[str]
    preview: list[PreviewLine]
