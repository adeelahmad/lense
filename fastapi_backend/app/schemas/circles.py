"""Circles of trust and what people share with them (app/domain/circles.py, docs/circles.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Member = int | str


class Person(ResponseModel):
    id: int
    name: str | None = None
    email: str | None = None


class Circle(ResponseModel):
    id: int
    name: str
    members: list[Person]
    created_at: str | None = None
    updated_at: str | None = None


class Circles(ResponseModel):
    circles: list[Circle]
    suggested: list[str] = Field(description="names of circles people often make, which this person hasn't made yet")


class CircleCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    members: list[Member] = Field(default_factory=list, max_length=200, description="people by account id or email")


class CircleUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    members: list[Member] | None = Field(None, max_length=200, description="everyone in it, replacing who was")
    add: list[Member] = Field(default_factory=list, max_length=200, description="people to add")
    remove: list[Member] = Field(default_factory=list, max_length=200, description="people to take out")


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


class Share(ResponseModel):
    id: int
    at: str
    activity: str = Field(description="the share's own activity in your timeline")
    object: dict[str, Any]
    note: str | None = None
    with_item: bool = False
    by: Person | None = Field(None, description="who shared it (shares with you)")
    circles: list[str] | None = Field(None, description="the circles it went to (your shares)")
    people: list[Person] | None = Field(None, description="who it went to (your shares)")


class Undone(ResponseModel):
    undone: int
    taken_back: int = Field(description="permissions on the resource taken back")


Direction = Literal["received", "sent"]
