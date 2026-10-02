"""Analytics: what's done with a namespace's or a collection's resources, and each person's own activity."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel

Action = Literal["view", "play", "search", "download", "comment"]


class ActionCounts(ResponseModel):
    view: int = 0
    play: int = 0
    search: int = 0
    download: int = 0
    comment: int = 0


class DayCounts(ActionCounts):
    day: str = Field(description="YYYY-MM-DD (UTC)")


class CollectionCounts(ActionCounts):
    id: int
    name: str
    path: list[str] = Field(default_factory=list, description="the collections from the namespace's top down to it")


class NamespaceCounts(ActionCounts):
    name: str


class ResourceCounts(ActionCounts):
    id: int
    title: str | None = None
    namespace: str | None = None


class AnalyticsReport(ResponseModel):
    """The numbers for a range of days."""

    from_: str = Field(alias="from", description="the first day, YYYY-MM-DD")
    to: str = Field(description="the last day")
    namespace: str | None = None
    collection: int | None = None
    totals: ActionCounts
    people: int = Field(description="different accounts that did any of it (within what's still kept: analytics.retention_days)")
    anonymous: int = Field(description="how many of the actions had no account: visitors, and files fetched by a signed link")
    days: list[DayCounts] = Field(description="every day of the range, in order")
    collections: list[CollectionCounts] | None = Field(None, description="for a namespace: its collections with any activity")
    namespaces: list[NamespaceCounts] | None = Field(None, description="for the whole archive (admins): the namespaces with any")
    resources: list[ResourceCounts] = Field(default_factory=list, description="the 20 most used resources")


class ActivityEntry(ResponseModel):
    at: str
    action: Action
    resource: int | None = None
    title: str | None = Field(None, description="the resource's title, while you can still read it")
    namespace: str | None = None


class AnalyticsStatus(ResponseModel):
    events: int = Field(description="actions kept with their account")
    oldest: str | None = None
    days: int = Field(description="daily counts kept (they hold no accounts)")
    retention_days: int


class PurgeRequest(RequestModel):
    everything: bool = Field(False, description="delete all of it, the daily counts too; default: only what's past its retention")


class Purged(Ok):
    deleted: int = Field(description="actions deleted")
