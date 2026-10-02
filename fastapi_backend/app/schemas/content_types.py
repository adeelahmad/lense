"""Content types: four base types read from the file, and a vocabulary of subtypes under them that pipelines tie to."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

BaseType = Literal["video", "audio", "image", "text"]


class ContentRules(RequestModel):
    """How a subtype is recognised when nobody chose it: every rule given has to hold."""

    extensions: list[str] | None = Field(default=None, description="e.g. [.srt, .vtt]")
    pattern: str | None = Field(default=None, description="a regular expression found in the file name or title")
    min_minutes: float | None = None
    max_minutes: float | None = None


class ContentType(ResponseModel):
    key: str
    base: BaseType
    label: str
    description: str | None = None
    pipeline: int | None = Field(default=None, description="its pipeline; none: the namespace default runs")
    rules: dict | None = None
    general: bool = Field(description="the base type's catch-all: can be renamed, not removed")
    builtin: bool = Field(description="one Lens started with")


class ContentTypeCatalog(ResponseModel):
    bases: list[str]
    types: list[ContentType]


class ContentTypeCreate(RequestModel):
    base: BaseType
    label: str
    key: str | None = Field(default=None, description="lowercase letters, digits and _; made from the label if left out")
    description: str | None = None
    pipeline: int | None = None
    rules: ContentRules | None = None


class ContentTypeUpdate(RequestModel):
    """Only what you send changes; its key and base type stay."""

    label: str | None = None
    description: str | None = None
    pipeline: int | None = None
    rules: ContentRules | None = None


class RecordingContentType(ResponseModel):
    content_type: ContentType
    chosen: bool = Field(description="someone chose it; otherwise it was recognised from the file")


class RecordingContentTypeSet(RequestModel):
    content_type: str | None = Field(description="a subtype of the resource's base type; null to recognise it again")
