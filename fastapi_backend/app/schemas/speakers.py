from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class SpeakerSuggestion(ResponseModel):
    id: int
    name: str
    score: float


class Speaker(ResponseModel):
    id: int
    label: str
    name: str | None = None
    display: str
    has_voice: bool = False
    talk_ms: int = 0
    segments: int = 0
    recordings: int = 0
    suggestions: list[SpeakerSuggestion] = Field(default_factory=list)


class SpeakerMerge(ResponseModel):
    id: int
    from_id: int
    into_id: int
    at: str | None = None
    undone: Any = None
    from_label: str | None = None
    from_name: str | None = None
    into_name: str | None = None


class SpeakerLink(ResponseModel):
    a: int
    b: int
    a_name: str | None = None
    b_name: str | None = None
    a_ns: str | None = None
    b_ns: str | None = None


class SpeakerDirectory(ResponseModel):
    speakers: list[Speaker]
    merges: list[SpeakerMerge]
    links: list[SpeakerLink]


class SpeakerRecording(ResponseModel):
    id: int
    title: str | None = None
    recorded_at: str | None = None
    talk_ms: int = 0
    first_t0: int = 0


class SpeakerRename(RequestModel):
    name: str = Field("", description="empty to go back to the automatic label")


class SpeakerMergeRequest(RequestModel):
    into: int = Field(description="the speaker that remains")


class SpeakerLinkRequest(RequestModel):
    # ``with`` is a Python keyword. (FastAPI re-wraps aliased body fields and pydantic warns about it; harmless.)
    with_: int = Field(alias="with", description="a speaker in another namespace: the same person")


class SpeakerMerged(Ok):
    merge_id: int
