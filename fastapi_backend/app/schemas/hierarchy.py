"""Collections: the folders of a namespace, which every recording lives in."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class CollectionNode(ResponseModel):
    id: int
    name: str
    description: str | None = None
    parent: int | None = Field(None, description="the collection it's in; null: the top of its namespace")
    depth: int = Field(0, description="0 at the top")
    path: list[str] = Field(default_factory=list, description="names from the top of the namespace down to it")
    recordings: int = Field(0, description="recordings it holds")
    total: int = Field(0, description="recordings it and the collections inside it hold")
    children: int = Field(0, description="collections directly inside it")
    default: bool = Field(False, description="new recordings go here when nobody says where")
    created_at: str | None = None
    created_by: str | None = None
    updated_at: str | None = None


class CollectionNodeCreate(RequestModel):
    name: str = Field(min_length=1, max_length=120, description="unique among the collections next to it, ignoring case")
    parent: int | None = Field(None, description="the collection to put it in; null: the top of the namespace")
    description: str | None = Field(None, max_length=2000)


class CollectionNodeUpdate(RequestModel):
    """Send what changes: `parent` null moves it to the top; `default: true` makes it the namespace's default."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    parent: int | None = Field(None, description="move it inside this collection; null: to the top")
    default: bool | None = Field(None, description="true: new recordings go here when nobody says where")


class CollectionStep(ResponseModel):
    id: int
    name: str
