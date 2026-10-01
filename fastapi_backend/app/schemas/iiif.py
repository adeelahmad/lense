"""The IIIF panel, shared moments (Content State) and importing from other IIIF archives."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import AccessLevel, AccessPart, Ok, RequestModel, ResponseModel


class ViewerLink(ResponseModel):
    name: str
    url: str


class IiifValidation(ResponseModel):
    checked: bool
    problems: list[Any]


class IiifPanel(ResponseModel):
    manifest: str
    collection: str
    access: AccessLevel
    open: list[AccessPart] = Field(description="the parts anyone may use, when the recording is public")
    featured: bool = False
    published: bool = Field(description="IIIF publishes public recordings")
    layers: list[Any]
    search: bool
    validation: IiifValidation
    viewers: list[ViewerLink]
    json_: dict[str, Any] = Field(alias="json")


class ContentState(ResponseModel):
    content_state: dict[str, Any]
    encoded: str
    link: str
    viewers: list[ViewerLink]


class IiifUrl(RequestModel):
    url: str = ""


class IiifImport(RequestModel):
    url: str = ""
    namespace: str = ""
    keep_transcripts: bool = True
    limit: int = 50
    wait: bool = Field(False, description="import before answering instead of in the background")
    collection: int | None = Field(None, description="a collection of the namespace to put them in; default: its default collection")


class IiifImported(Ok):
    recordings: list[int] | None = None
    status: str | None = None
