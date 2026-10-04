"""Notes as pages: free notes in a tree and a page for every resource, entity and topic (docs/notes.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Place = Literal["project", "area", "resource", "archive"]
Sign = Literal["@", "#"]


class PageItem(ResponseModel):
    id: int
    title: str
    summary: str | None = Field(None, description="one line on what the page holds: the context the assistant reads first")
    summary_by: Literal["person", "assistant"] | None = Field(None, description="who wrote the summary")
    date: str | None = None
    place: Place | None = Field(None, description="where it's filed (PARA): project, area, resource or archive")
    parent: int | None = Field(None, description="the page it's inside, in the tree")
    position: float | None = Field(None, description="its order among its siblings")
    about: str | None = Field(None, description='what this is the page of, like "recording:12"; null for a free note')
    author: Literal["person", "assistant"] = Field("person", description="who wrote it")
    created_at: str | None = None
    updated_at: str | None = None


class PageLink(ResponseModel):
    sign: Sign
    target: str = Field(description='what it links to, like "recording:12" or "page:3"')
    label: str = Field(description="the text the link shows")
    name: str | None = Field(None, description="the target's current name; null when it's gone or you can't see it")
    namespace: str | None = None


class Backlink(ResponseModel):
    page: int
    title: str
    about: str | None = None
    updated_at: str | None = None


class Page(PageItem):
    namespace: str
    body: str = Field("", description="Markdown, with mentions written @[label](kind:id) and #[label](entity:id)")
    doc: str | None = Field(None, description="the editor's own document state, if it kept one")
    created_by: str | None = Field(None, description="its writer's email")
    links: list[PageLink] = []
    backlinks: list[Backlink] = []
    can_edit: bool = False


class PageDraft(ResponseModel):
    """A thing's page before anyone has written on it."""

    id: None = None
    namespace: str
    about: str
    title: str
    body: str = ""
    backlinks: list[Backlink] = []
    can_edit: bool = False


class PageTree(ResponseModel):
    namespace: str
    pages: list[PageItem]


class PageCreate(RequestModel):
    ns: str = Field(description="the namespace it goes in")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field("", max_length=200_000)
    summary: str | None = Field(None, max_length=300)
    date: str | None = Field(None, description="YYYY-MM-DD (default: today)")
    place: Place | None = None
    parent: int | None = Field(None, description="put it inside this page")
    about: str | None = Field(None, description='make the page of this thing, like "recording:12" or "entity:5"')
    doc: str | None = None


class PageUpdate(RequestModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    body: str | None = Field(None, max_length=200_000)
    summary: str | None = Field(None, max_length=300, description="empty to clear it")
    date: str | None = None
    place: Place | Literal[""] | None = Field(None, description='"" to unfile it')
    doc: str | None = Field(None, description="the editor's document state; a new body without it drops the old one")


class PageMove(RequestModel):
    parent: int | None = Field(None, description="the page to put it inside; null for the top")
    before: int | None = Field(None, description="put it before this sibling; null for the end")


class LinkTarget(ResponseModel):
    target: str
    label: str
    kind: Literal["page", "recording", "entity", "topic", "collection", "speaker"]
