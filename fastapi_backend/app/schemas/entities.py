"""Entities (people, organisations, products... mentioned in recordings), their curation and the graph explorer."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import Ok, RequestModel, ResponseModel


class EntityType(ResponseModel):
    type: str
    label: str
    quiet: bool = Field(description="extracted but hidden unless a filter asks for it (dates, numbers)")
    description: str | None = None
    builtin: bool = Field(True, description="one of Lens's types; false: the namespace's own")


class EntityList(ResponseModel):
    total: int
    items: list[dict[str, Any]]
    months: list[str] = []
    facets: dict[str, dict[str, int]] = {}


class EntityLink(ResponseModel):
    id: int
    name: str | None = None
    namespace: str | None = None


class EntityDetail(ResponseModel):
    id: int
    name: str
    key: str
    type: str
    type_label: str
    description: str | None = None
    namespace: str | None = None
    aliases: list[str] = []
    mentions: int = 0
    recordings: int = 0
    speakers: int = 0
    first: str | None = None
    last: str | None = None
    links: list[EntityLink] = []
    same_name_elsewhere: list[dict[str, Any]] = []
    hidden: bool = False
    defined: bool = Field(False, description="on the fixed list people defined")
    builtin: str | None = Field(None, description="unknown or unlabeled: one of the two entities that are always there")
    collection: int | None = Field(None, description="a defined entity of one collection (and those inside it)")


class MentionList(ResponseModel):
    total: int
    items: list[dict[str, Any]]


class EntityRetype(RequestModel):
    ids: list[int] = []
    type: str | None = None


class EntityMerge(RequestModel):
    keep: int = 0
    others: list[int] = []


class EntityMerged(Ok):
    merge: int


class EntityNotSame(RequestModel):
    a: int = 0
    b: int = 0


class EntityRename(RequestModel):
    name: str | None = None
    keep_alias: bool = True
    correct: bool = Field(False, description="also correct the transcript lines that say the old name")
    dry_run: bool = False


class EntityUpdate(RequestModel):
    description: str | None = Field(None, description="what the entity is; empty clears it")
    aliases: list[str] | None = Field(None, max_length=100, description="the other ways it's said (these replace the ones it has)")
    defined: bool | None = Field(None, description="on the fixed list of entities (namespaces in the fixed mode map names onto it)")


class EntityDefine(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = "TERM"
    description: str | None = Field(None, max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=100, description="other ways it's said")
    collection: int | None = Field(None, description="for this collection (and those inside it) only; default: the namespace")


class EntityHide(RequestModel):
    hidden: bool = True
    reason: str | None = None


class EntityLinkRequest(RequestModel):
    with_: int = Field(0, alias="with", description="the entity (in another namespace) that is the same thing")


class MentionMove(RequestModel):
    target: int | None = Field(None, description="an entity in the same namespace")
    new_name: str | None = Field(None, description="or a new (or existing) entity by name")
    new_type: str | None = "TERM"
    remove: bool = Field(False, description="or say this mention isn't an entity at all")


class MentionMoved(Ok):
    entity: int | None = None
