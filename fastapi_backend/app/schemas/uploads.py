from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class Converters(ResponseModel):
    """What this server can make into PDFs to read as documents (docs/api.md#documents-and-images)."""

    office: bool = Field(description="Word, PowerPoint and spreadsheet files, OpenDocument and RTF (LibreOffice)")
    pages: bool = Field(description="text, Markdown, saved web pages and .eml emails (Chromium or LibreOffice)")
    msg: bool = Field(description="Outlook .msg emails (those, and the extract-msg package)")


class UploadLimits(ResponseModel):
    max_mb: int = Field(description="the largest file, in MB (uploads.max_mb)")
    extensions: list[str] = Field(description="the types that can be uploaded (uploads.extensions)")
    chunk_mb: int = Field(description="how much the web app sends per request (uploads.chunk_mb)")
    transcript_mb: int = Field(description="the largest transcript file for POST /import (server.max_upload_mb)")
    convert: Converters


class UploadStart(RequestModel):
    namespace: str = Field(
        "", description="a namespace you edit; admins may name a new one, created when the upload finishes (default: the recording's)"
    )
    recording: int | None = Field(
        None, description="attach the file to this transcript-only recording as its audio, instead of making a recording of it"
    )
    filename: str = Field(min_length=1, max_length=1000)
    size: int = Field(gt=0, description="the file's size in bytes")
    title: str | None = Field(None, max_length=200, description="the recording's title (default: the file's name)")
    pipeline: int | None = Field(None, description="run this pipeline once it's here instead of the namespace's (not with `recording`)")
    collection: int | None = Field(
        None, description="a collection of the namespace to put the recording in (default: its default collection; not with `recording`)"
    )
    modified: int | None = Field(
        None, ge=0, description="the file's last-modified time in milliseconds since 1970; dates the recording when its name doesn't"
    )


class Upload(ResponseModel):
    id: str
    namespace: str
    filename: str = Field(description="the name it's stored under")
    title: str | None = None
    size: int
    offset: int = Field(description="how many bytes have arrived: the next chunk starts here")
    state: Literal["receiving", "done"]
    attach: int | None = Field(None, description="the transcript-only recording it becomes the audio of")
    pipeline: int | None = Field(None, description="the pipeline chosen to run once it's here (default: the namespace's)")
    recording: int | None = Field(None, description="the recording it became (or was attached to), once done")
    job: int | None = Field(None, description="the processing queued for it, if any")
    duplicate: bool = Field(False, description="the namespace already had this file: `recording` is that one")
    created_at: str
    expires_at: str = Field(description="when it's removed unless more of it arrives (uploads.expire_hours after the last chunk)")
