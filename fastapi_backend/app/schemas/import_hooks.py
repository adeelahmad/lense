from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from app.schemas.common import RequestModel, ResponseModel


class ImportHook(ResponseModel):
    id: int
    name: str
    namespace: str
    collection: int | None = Field(None, description="where its imports go (default: the namespace's default collection)")
    pipeline: int | None = Field(None, description="the pipeline its imports run (default: the namespace's)")
    enabled: bool = Field(description="false while paused: pushes are refused, and the token keeps")
    token_tail: str | None = Field(None, description="the token's last four characters, to tell tokens apart")
    used: int = Field(0, description="how many things it has imported")
    last_used_at: str | None = None
    created_by: str | None = None
    created_at: str


class ImportHookCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80, description="what pushes to it, e.g. Scanner or Zapier")
    collection: int | None = None
    pipeline: int | None = None


class ImportHookUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    collection: int | None = None
    pipeline: int | None = None
    enabled: bool | None = None


class ImportHookCreated(ResponseModel):
    hook: ImportHook
    token: str = Field(description="shown this once: send it as a bearer token, or put it in the address")
    path: str = Field(description="where to push, on this server's address (POST, with Authorization: Bearer <token>)")


class ImportHookToken(ResponseModel):
    token: str = Field(description="shown this once; the old token has stopped working")


class HookPush(RequestModel):
    """What a JSON push carries: a web address or text (send a file as the raw body, or as multipart form files)."""

    url: HttpUrl | None = Field(None, description="a web page or PDF to keep as a document")
    text: str | None = Field(None, max_length=5_000_000, description="text to import as a transcript, as pasted text is")
    title: str | None = Field(None, max_length=200)


class HookItem(ResponseModel):
    kind: Literal["file", "url", "text"]
    name: str = Field(description="the file's name, the address, or the text's title")
    recording: int = Field(description="the recording or resource it became")
    job: int | None = Field(None, description="the job that runs its pipeline")
    duplicate: bool = Field(False, description="the namespace had this file already; it's that recording")


class HookPushed(ResponseModel):
    namespace: str
    items: list[HookItem]
