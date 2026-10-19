"""Circles of trust and what people share with them (app/domain/circles.py, docs/circles.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Member = int | str
Let = Literal["free_busy", "questions"]


class Person(ResponseModel):
    id: int
    name: str | None = None
    email: str | None = None


class Circle(ResponseModel):
    id: int
    name: str
    members: list[Person]
    lets: list[Let] = Field(default_factory=list, description="what its people may ask your agent: free_busy, questions")
    about: list[str] = Field(default_factory=list, description="the namespaces of yours their questions are answered from")
    created_at: str | None = None
    updated_at: str | None = None


class CircleList(ResponseModel):
    circles: list[Circle]
    suggested: list[str] = Field(description="names of circles people often make, which this person hasn't made yet")


class CircleCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    members: list[Member] = Field(default_factory=list, max_length=200, description="people by account id or email")
    lets: list[Let] = Field(default_factory=list, description="what its people may ask your agent: free_busy, questions")
    about: list[Member] = Field(
        default_factory=list, max_length=50, description="namespaces of yours, by name or id, their questions are answered from"
    )


class CircleUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    members: list[Member] | None = Field(None, max_length=200, description="everyone in it, replacing who was")
    add: list[Member] = Field(default_factory=list, max_length=200, description="people to add")
    remove: list[Member] = Field(default_factory=list, max_length=200, description="people to take out")
    lets: list[Let] | None = Field(None, description="what its people may ask your agent, replacing what they could")
    about: list[Member] | None = Field(None, max_length=50, description="namespaces of yours their questions are answered from")


class ShareIn(RequestModel):
    activity: str | None = Field(None, max_length=120, description="an activity's id, as the timeline gives it")
    recording: int | None = Field(None, description="a resource's id")
    circles: list[int] = Field(default_factory=list, max_length=50, description="your circles to share with")
    people: list[Member] = Field(default_factory=list, max_length=200, description="people in your circles, by id or email")
    note: str | None = Field(None, max_length=2000, description="what you want them to know")
    with_item: bool = Field(
        False, description="also give members who can't open the resource permission on it (needs the publish permission there)"
    )


class Shared(ResponseModel):
    id: int
    at: str
    object: dict[str, Any] = Field(description="what was shared, as it read then: type, id, name, summary, url, the note as content")
    circles: list[str]
    people: list[Person]
    with_item: bool
    granted: int = Field(description="how many members were given permission on the resource")


class CircleShare(ResponseModel):
    id: int
    at: str
    activity: str = Field(description="the share's own activity in your timeline")
    object: dict[str, Any]
    note: str | None = None
    with_item: bool = False
    by: Person | None = Field(None, description="who shared it (shares with you)")
    circles: list[str] | None = Field(None, description="the circles it went to (your shares)")
    people: list[Person] | None = Field(None, description="who it went to (your shares)")


class ShareUndone(ResponseModel):
    undone: int
    taken_back: int = Field(description="permissions on the resource taken back")


Direction = Literal["received", "sent"]


class WhenFreeIn(RequestModel):
    people: list[Member] = Field(min_length=1, max_length=20, description="people by account id or email; you are counted too")
    start: str = Field(max_length=40, description="the window's start, an ISO time (without an offset, in tz)")
    end: str = Field(max_length=40, description="the window's end, at most 31 days after its start")
    tz: str = Field("UTC", max_length=60, description="the time zone times are given and answered in, like Europe/London")
    minutes: int = Field(30, ge=5, le=1440, description="how long the free stretches must be")
    day_start: int = Field(9, ge=0, le=23, description="the hour each day starts, in tz")
    day_end: int = Field(18, ge=1, le=24, description="the hour each day ends, in tz")


class BusySpan(ResponseModel):
    start: str
    end: str


class BusyTimes(ResponseModel):
    id: int | None = None
    name: str | None = None
    busy: list[BusySpan] | None = Field(None, description="when they're busy; never what, where or with whom")
    person: str | None = Field(None, description="who was asked about, when they haven't let you see")
    refused: str | None = None


class WhenFree(ResponseModel):
    start: str
    end: str
    tz: str
    people: list[BusyTimes]
    free: list[BusySpan] = Field(description="when everyone who let you see, and you, are free, within the hours of the day asked for")


class AskIn(RequestModel):
    person: Member = Field(description="whose agent to ask, by account id or email")
    question: str = Field(min_length=1, max_length=1000)


class AnswerSource(ResponseModel):
    n: int
    title: str | None = None
    time: str | None = None
    cited: bool = False
    url: str | None = Field(None, description="its page, when you can open it")


class AgentAnswer(ResponseModel):
    id: int
    name: str
    answer: str
    sources: list[AnswerSource]
