"""Topics: each namespace's controlled vocabulary (SKOS concepts) and the recordings about them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class TopicRef(ResponseModel):
    id: int
    label: str


class TopicAbout(ResponseModel):
    recording: int
    title: str | None = None
    source: str | None = Field(None, description="how it got there: person, entity, analysis or assistant")
    weight: float | None = None
    status: Literal["accepted", "suggested", "dismissed"] | None = None


class TopicItem(ResponseModel):
    id: int
    namespace: str | None = None
    label: str
    alt: list[str] = Field([], description="other labels it goes by (skos:altLabel)")
    definition: str | None = None
    broader: list[int] = []
    related: list[int] = []
    recordings: int = Field(0, description="recordings about it (accepted)")
    narrower: int = Field(0, description="topics that have it as broader")
    from_entity: int | None = Field(None, description="the entity it was made from, hidden while the topic exists")
    updated: str | None = None


class TopicList(ResponseModel):
    total: int
    items: list[TopicItem]


class TopicDetail(ResponseModel):
    id: int
    namespace: str | None = None
    label: str
    alt: list[str] = []
    definition: str | None = None
    broader: list[TopicRef] = []
    narrower: list[TopicRef] = []
    related: list[TopicRef] = []
    recordings: int = 0
    from_entity: int | None = None
    updated: str | None = None
    about: list[TopicAbout] = []


class TopicCreate(RequestModel):
    label: str = Field(min_length=1, max_length=200)
    alt: list[str] = []
    definition: str | None = Field(None, max_length=2000)
    broader: list[int] = []
    related: list[int] = []


class TopicUpdate(RequestModel):
    label: str | None = Field(None, min_length=1, max_length=200)
    alt: list[str] | None = None
    definition: str | None = Field(None, max_length=2000, description="an empty string clears it")
    broader: list[int] | None = None
    related: list[int] | None = None


class TopicMerge(RequestModel):
    keep: int
    others: list[int] = Field(min_length=1)


class TopicRecordings(RequestModel):
    recordings: list[int] = Field(min_length=1, max_length=500)
    remove: bool = Field(False, description="say they aren't about it")


class RecordingTopic(ResponseModel):
    id: int
    label: str
    source: str | None = None
    weight: float | None = None
    status: str | None = None


class TopicCandidate(ResponseModel):
    label: str = Field(description="what summaries say recordings are about, which no topic covers yet")
    recordings: int = Field(description="how many recordings' summaries say it")


class TopicSkip(RequestModel):
    label: str = Field(min_length=1, max_length=200)
