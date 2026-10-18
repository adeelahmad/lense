from __future__ import annotations

from typing import Any, Literal

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
    by: str | None = Field(None, description="who merged them")
    recordings: int | None = Field(None, description="how many assets' lines moved to the remaining person")
    segments: int | None = Field(None, description="how many lines moved")
    undone_by: str | None = None
    undone_at: str | None = None


class SpeakerLink(ResponseModel):
    a: int
    b: int
    a_name: str | None = None
    b_name: str | None = None
    a_ns: str | None = None
    b_ns: str | None = None


class SpeakerCandidate(ResponseModel):
    a: int = Field(description="this namespace's person")
    b: int = Field(description="the person in another namespace")
    a_name: str | None = None
    b_name: str | None = None
    a_ns: str | None = None
    b_ns: str | None = None
    score: float = Field(description="how alike the two voices are (cosine similarity, at least speakers.match_threshold)")


class SpeakerDirectory(ResponseModel):
    speakers: list[Speaker]
    merges: list[SpeakerMerge]
    links: list[SpeakerLink]
    cross: list[SpeakerCandidate] = Field(
        default_factory=list,
        description="likely the same person in another shared namespace you can read (speakers.cross_namespace: suggest), "
        "best first; not linked, and not marked as different",
    )
    voices: Literal["recognize", "separate"] = Field(
        "recognize",
        description="recognize: voiceprints kept and people matched across recordings; separate: voices told apart in "
        "each recording only (PUT /namespaces/{name}/voices/mode)",
    )


class VoiceMode(RequestModel):
    mode: Literal["recognize", "separate"] = Field(
        description="recognize, or separate: voices told apart in each recording only, the namespace's voiceprints deleted"
    )


class SpeakerRecording(ResponseModel):
    id: int
    title: str | None = None
    recorded_at: str | None = None
    talk_ms: int = 0
    first_t0: int = 0


class SpeakerRename(RequestModel):
    name: str = Field("", description="empty to go back to the automatic label")


class SpeakerMergeRequest(RequestModel):
    into: int = Field(description="the person that remains")


class SpeakerLinkRequest(RequestModel):
    # ``with`` is a Python keyword. (FastAPI re-wraps aliased body fields and pydantic warns about it; harmless.)
    with_: int = Field(alias="with", description="a person in another namespace: the same person")


class SpeakerPair(RequestModel):
    with_: int = Field(alias="with", description="the other person")


class SpeakerMerged(Ok):
    merge_id: int
