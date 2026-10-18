"""Published RSS and podcast feeds (app/domain/syndication.py, docs/feeds.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

FeedKind = Literal["rss", "podcast"]
ItemKind = Literal["asset", "episode", "activity", "text"]


class FeedSettings(RequestModel):
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)
    author: str | None = Field(None, max_length=200, description="who a podcast app shows it's by (default Lens)")
    language: str | None = Field(None, max_length=20, description="a language tag, like en or en-GB (default en)")
    category: str | None = Field(None, max_length=60, description="an Apple Podcasts category (podcast feeds; default Education)")
    explicit: bool | None = None
    image: str | None = Field(None, max_length=2000, description="artwork: an https:// address of a square picture")
    max_items: int | None = Field(None, ge=1, le=500, description="how many items the feed lists, newest first (default 50)")
    kind: FeedKind | None = Field(None, description="rss lists anything; podcast lists items with audio, with Apple Podcasts' tags")


class FeedCreate(FeedSettings):
    namespace: str = Field(description="the namespace the feed belongs to: its editors manage it, its readers see it")
    title: str = Field(min_length=1, max_length=200)
    kind: FeedKind = "rss"


class Feed(ResponseModel):
    id: int
    space: int
    namespace: str | None = None
    kind: FeedKind
    title: str
    description: str | None = None
    author: str | None = None
    language: str | None = None
    category: str | None = None
    explicit: bool = False
    image: str | None = None
    max_items: int
    url: str = Field(description="the feed's private address: anyone with it can read the feed, so share it like a password")
    path: str = Field(description="the address without the server")
    page: str = Field(description="the feed's page in the web app")
    items: int = Field(description="how many items it holds")
    last_item_at: str | None = None
    fetched_at: str | None = Field(None, description="when a feed reader or podcast app last read it")
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FeedText(RequestModel):
    title: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=4000, description="the item's text")
    link: str | None = Field(None, max_length=2000, description="an http or https address")
    guid: str | None = Field(None, max_length=120, description="what the item is about, so the same item isn't added twice")


class FeedPublish(RequestModel):
    recordings: list[int] = Field(default_factory=list, max_length=200, description="resources (and podcast episodes) to list")
    activities: list[str] = Field(default_factory=list, max_length=200, description="timeline activities to list, by id")
    texts: list[FeedText] = Field(default_factory=list, max_length=50, description="text items")


class FeedRefused(ResponseModel):
    what: str
    why: str


class FeedPublished(ResponseModel):
    added: list[int] = Field(description="the new items' ids")
    already: int = Field(description="how many were in the feed already")
    refused: list[FeedRefused] = Field(description="what can't go in this feed, and why")


class FeedItem(ResponseModel):
    id: int
    feed: int
    kind: ItemKind
    guid: str
    title: str | None = None
    summary: str | None = None
    link: str | None = None
    published_at: str
    recording: int | None = None
    activity: dict[str, Any] | None = None
    categories: list[str] | None = None
    origin: dict[str, Any] | None = Field(None, description="the routine run and workflow node that added it")
    by: str | None = None
    created_at: str | None = None
