"""How a namespace, or a collection of it, organises its entities; and the entity types a namespace adds."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Mode = Literal["self", "fixed"]
Matching = Literal["rules", "model"]


class EntityTypeInfo(ResponseModel):
    type: str = Field(description="the code extractors and models use, e.g. ORG or CLIENT_TEAM")
    label: str
    description: str | None = None
    quiet: bool = Field(False, description="extracted but hidden unless a filter asks for it (dates, numbers)")
    builtin: bool = Field(True, description="one of Lens's types; false: the namespace's own")


class EntityTypeCreate(RequestModel):
    label: str = Field(min_length=1, max_length=40)
    description: str | None = Field(None, max_length=2000)


class EntityTypeUpdate(RequestModel):
    label: str | None = Field(None, min_length=1, max_length=40)
    description: str | None = Field(None, max_length=2000, description="empty clears it")


class EntitySetup(ResponseModel):
    mode: str = Field(
        description="self: every name found becomes an entity, and people curate them; fixed: names found are mapped onto "
        "the entities people defined, or onto Unlabeled (it belongs here) or Unknown"
    )
    types: list[str] = Field(default_factory=list, description="the types kept; empty: all of them")
    description: str | None = Field(None, description="what this place is about")
    matching: str = "rules"
    collection: int | None = Field(None, description="the collection this setup is saved on; null: the namespace")
    collection_path: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    updated_by: str | None = None
    can_change: bool = False


class EntitySetupView(ResponseModel):
    namespace: EntitySetup = Field(description="the namespace's setup (the default when none is saved)")
    saved: bool = Field(description="whether the namespace has a setup of its own")
    collections: list[EntitySetup] = Field(default_factory=list, description="collections with a setup of their own")
    types: list[EntityTypeInfo] = []
    can_change: bool = Field(description="you may change the namespace's setup and types: its editors")


class EntitySetupSave(RequestModel):
    collection: int | None = Field(None, description="save it on this collection (default: the namespace)")
    mode: Mode = "self"
    types: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(None, max_length=2000)
    matching: Matching = "rules"


class EntitySetupApply(RequestModel):
    collection: int | None = Field(None, description="only this collection's recordings (and those inside it)")


class EntitySetupApplied(ResponseModel):
    recordings: int = Field(description="how many recordings are analysed again")
