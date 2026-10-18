"""The stream of activities (docs/automations.md, Contract): activities in the W3C Activity Streams 2.0 shape with a
Lens kind, pages of the timeline, and the kinds catalog."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import RequestModel, ResponseModel


class ActivityActor(ResponseModel):
    type: Literal["Service", "Person", "Application"]
    id: str = Field(description="a Lens reference, like lens:account/1 or lens:automation/4")
    name: str


class ActivityObject(ResponseModel):
    """What the activity is about; each kind adds its own fields (GET /activities/kinds has its schema)."""

    type: str
    id: str | None = Field(None, description="a Lens reference, like lens:recording/7")
    name: str | None = None
    url: str | None = Field(None, description="its page in the web app, relative to it")


class ActivityLens(ResponseModel):
    source: str = Field(description="the source that made it, like recordings or log")
    pos: list[Any] = Field(description="the source's position, [time, id]; for the stream machinery")
    untrusted: list[str] = Field(default_factory=list, description="dotted paths holding text from outside Lens")
    cause: dict[str, int] | None = Field(None, description="{automation, run, depth} when an automation caused it")
    spaces: list[int] | None = Field(None, description="every namespace it belongs to, when there are several")
    audience: Literal["space", "owner", "admins"] | None = Field(None, description="who may see it (default: space)")
    owner: int | None = Field(None, description="the account that alone sees it, with audience owner")


class StreamActivity(ResponseModel):
    id: str
    type: str = Field(description="the Activity Streams verb, like Create")
    kind: str = Field(description="the Lens kind triggers subscribe to, like asset.added")
    published: str = Field(description="when it happened, ISO 8601 in UTC")
    actor: ActivityActor
    object: ActivityObject
    target: dict[str, Any] | None = None
    tag: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    space: int | None = Field(None, description="its namespace's id, or null")
    lens: ActivityLens


class ActivityMark(ResponseModel):
    """Your own review marks on an activity; nobody else sees them."""

    reviewed: bool = False
    important: bool = False
    hidden: bool = False
    at: str | None = Field(None, description="when you last marked it")


class Reaction(ResponseModel):
    """One emoji on an activity: how many people gave it, and whether you did."""

    emoji: str
    count: int
    mine: bool = False


class ActivityPage(ResponseModel):
    items: list[StreamActivity] = Field(description="newest first")
    next: str | None = Field(None, description="give it as before for the page after; null at the end")
    namespaces: dict[str, str] = Field(default_factory=dict, description="the names of the namespaces in items, by id")
    marks: dict[str, ActivityMark] = Field(default_factory=dict, description="your marks on items, by id (those you marked)")
    reactions: dict[str, list[Reaction]] = Field(default_factory=dict, description="everyone's reactions on items, by id (those with any)")
    comments: dict[str, int] = Field(default_factory=dict, description="how many comments items have, by id (those with any)")
    why: dict[str, str] = Field(
        default_factory=dict,
        description="with order=for_you: why an item is where it is, by id, for those your own signals placed",
    )


class ReactIn(RequestModel):
    emoji: str = Field(max_length=8, description="one of the palette: 👍 ❤️ 🎉 👀 😂 😮 😢 🙏")
    on: bool = Field(True, description="give it (true) or take yours back (false)")


class ReactionsOut(ResponseModel):
    reactions: list[Reaction] = Field(description="the reactions on the activity now, in the palette's order")


class MarkIn(RequestModel):
    """Marks to set: each given true or false; one left out stays as it is."""

    reviewed: bool | None = None
    important: bool | None = None
    hidden: bool | None = None


class MarksIn(MarkIn):
    ids: list[str] = Field(min_length=1, max_length=500, description="the activities' ids")


class Mute(ResponseModel):
    """One of your mutes: an actor or a kind, until a time or for good."""

    actor: str | None = Field(None, description="the muted actor's id, like lens:source/12")
    kind: str | None = Field(None, description="the muted kind, like sensor.reading (the kinds under it count too)")
    name: str | None = Field(None, description="the actor's name as the page showed it, or the kind's label")
    until: str | None = Field(None, description="when it runs out (ISO 8601); null for good")
    at: str | None = None


class MuteIn(RequestModel):
    """Mute an actor or a kind (one of the two) for yourself, for good or until a time; `on` false lifts it."""

    actor: str | None = Field(None, max_length=240, description="an actor's id, like lens:source/12 or lens:account/1")
    kind: str | None = Field(None, max_length=120, description="a kind, like sensor.reading")
    name: str | None = Field(None, max_length=200, description="the actor's name, for the Muted list")
    until: str | None = Field(None, max_length=40, description="until this time (ISO 8601); left out: for good")
    on: bool = Field(True, description="mute (true) or lift the mute (false)")


class MutesOut(ResponseModel):
    mutes: list[Mute] = Field(description="your mutes now, the latest first")


class MarksOut(ResponseModel):
    marks: dict[str, ActivityMark] = Field(description="your marks now, by id")
    unknown: list[str] = Field(default_factory=list, description="ids of activities there are none of, or you can't see")


class ActivityKind(ResponseModel):
    # "schema" shadows a BaseModel attribute, so it is `json_schema` here and `schema` on the wire
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    label: str
    source: str
    type: str = Field(description="the Activity Streams verb its activities have")
    object_type: str
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict, description="JSON Schemas of its own object, actor or target fields")
    json_schema: dict[str, Any] = Field(alias="schema", description="a JSON Schema of its activities, with every field")
    untrusted: list[str] = Field(default_factory=list)
    noisy: bool = Field(False, description="left out of the timeline unless named")
    sample: dict[str, Any] | None = None
