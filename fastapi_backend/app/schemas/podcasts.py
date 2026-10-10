"""Podcast episodes made from picked sources (app/domain/podcasts.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Style = Literal["deep-dive", "recap", "debate", "beginner"]


class PickedLine(RequestModel):
    recording: int
    idx: int = Field(ge=0, description="the line (segment) number in the recording")


class PodcastSelection(RequestModel):
    recordings: list[int] = Field(default_factory=list, max_length=100, description="resources picked whole")
    excerpts: list[PickedLine] = Field(default_factory=list, max_length=200, description="lines picked in resources (search hits)")


class PodcastVoices(RequestModel):
    a: str | None = Field(None, max_length=100, description="the explaining host's voice")
    b: str | None = Field(None, max_length=100, description="the asking host's voice")


class PodcastCreate(RequestModel):
    selection: PodcastSelection
    prompt: str | None = Field(None, max_length=2000, description='an angle, e.g. "explain it for a beginner"')
    length: int = Field(10, ge=1, le=120, description="minutes (up to podcasts.max_minutes)")
    style: Style = "deep-dive"
    title: str | None = Field(None, max_length=200)
    voices: PodcastVoices | None = None


class PodcastStarted(ResponseModel):
    episode: int = Field(description="the episode's recording id")
    job: int
    namespace: str
    placed: Literal["podcasts", "sources"] = Field(description="podcasts: the podcasts namespace; sources: the sources' own")


class PodcastJob(ResponseModel):
    job: int


class PodcastSummary(ResponseModel):
    id: int
    title: str | None = None
    status: str
    space: int
    duration_ms: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PodcastCitation(ResponseModel):
    n: int
    kind: str
    recording: int | None = None
    idx0: int | None = None
    idx1: int | None = None


class PodcastLine(ResponseModel):
    idx: int
    speaker: Literal["a", "b"]
    kind: str
    text: str
    citations: list[int] = []
    sources: list[PodcastCitation] = []
    t0: int | None = None
    t1: int | None = None


class PodcastSource(ResponseModel):
    n: int
    ref: dict[str, Any]
    title: str
    namespace: str | None = None
    at: str | None = None
    t0: int | None = None
    page: int | None = None
    text: str
    picked: bool = True


class PodcastCheck(ResponseModel):
    idx: int
    verdict: str
    action: str
    before: str | None = None
    after: str | None = None
    why: str | None = None


class Podcast(ResponseModel):
    id: int
    title: str | None = None
    space: int
    namespace: str | None = None
    status: str = Field(description="queued, gathering, planning, writing, checking, rendering, publishing, ready, script_only or failed")
    error: str | None = None
    request: dict[str, Any] = {}
    sources: list[PodcastSource] = []
    outline: dict[str, Any] | None = None
    lines: list[PodcastLine] = []
    checks: list[PodcastCheck] = []
    audio: str | None = Field(None, description="a signed link to the episode's audio, once it has some")
    voicing: dict[str, Any] | None = Field(None, description="who read it: the provider, the hosts' voices, its length")
    duration_ms: int | None = None
    job: int | None = Field(None, description="the job making it now, if one is")
    created_at: str | None = None
    updated_at: str | None = None
