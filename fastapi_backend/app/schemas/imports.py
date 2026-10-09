from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

TranscriptFormat = Literal["auto", "text", "markdown", "mdx", "json", "jsonl", "srt", "vtt", "chat"]


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
    pipeline: int | None = Field(None, description="run this pipeline afterwards instead of the namespace's (GET /pipelines)")
    collection: int | None = Field(None, description="a collection of the namespace to put it in; default: its default collection")


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


class SourceImportRequest(RequestModel):
    source: int
    paths: list[str] = Field(min_length=1, max_length=500, description="files of the source, as browsing it lists them")
    namespace: str
    pipeline: int | None = Field(None, description="run this pipeline afterwards instead of the namespace's")
    collection: int | None = Field(None, description="a collection of the namespace to put them in; default: its default collection")
    documents_as: Literal["document", "transcript"] = Field(
        "document",
        description="PDFs, Word and text files become documents (their pages kept and read), or transcripts (their text only)",
    )


class WebImportRequest(RequestModel):
    url: str = Field(min_length=1, max_length=2000, description="the page's address, http:// or https://")
    namespace: str = Field(description="a namespace you edit; admins may name a new one")
    title: str | None = Field(None, max_length=200, description="default: the page's own title, once it's captured")
    pipeline: int | None = Field(None, description="run this pipeline afterwards instead of the namespace's")
    collection: int | None = Field(None, description="a collection of the namespace to put it in; default: its default collection")


class LinksImportRequest(RequestModel):
    """Links pasted as ``text`` (one per line), or a file (``filename`` + base64 ``data``): a list of addresses, a
    browser's bookmark export (HTML) or Chrome's Bookmarks file (JSON)."""

    namespace: str = Field(description="a namespace you edit; admins may name a new one")
    text: str | None = Field(None, max_length=1_000_000, description="addresses, one per line")
    filename: str | None = None
    data: str | None = Field(None, description="the file, base64 encoded")
    folders_as_tags: bool = Field(True, description="tag each page with the bookmark folders it was in")
    pipeline: int | None = Field(None, description="run this pipeline afterwards instead of the namespace's")
    collection: int | None = Field(None, description="a collection of the namespace to put them in; default: its default collection")


class LinkImportResult(ResponseModel):
    url: str
    status: Literal["queued", "already", "skipped"] = Field(
        description="queued to be captured; already in the namespace (that resource); skipped (detail says why)"
    )
    recording: int | None = None
    job: int | None = None
    detail: str | None = None


class LinksImport(ResponseModel):
    results: list[LinkImportResult]


class SourceImportResult(ResponseModel):
    path: str
    status: Literal["queued", "already", "skipped", "error"]
    recording: int | None = None
    job: int | None = None
    detail: str | None = None


class SourceImport(ResponseModel):
    results: list[SourceImportResult]
