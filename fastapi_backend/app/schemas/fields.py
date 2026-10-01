"""Custom metadata fields: their definitions on namespaces and collections, and their values on collections, resources
and files."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

FieldType = Literal["text", "longtext", "number", "date", "boolean", "choice", "choices", "link"]
FieldTarget = Literal["resource", "collection", "file"]


class FieldDef(ResponseModel):
    id: int
    label: str
    type: FieldType
    target: FieldTarget = Field(description="what it describes: the resources, the collections or the files inside where it's defined")
    options: list[str] | None = Field(None, description="choice and choices fields: what may be chosen")
    help: str | None = None
    published: bool = Field(description="shown on public pages and in IIIF metadata; otherwise only in the workspace")
    collection: int | None = Field(None, description="the collection it's defined on; null: the namespace")
    collection_path: list[str] = Field(default_factory=list, description="that collection's names, from the top")
    ord: int | None = None
    can_change: bool = Field(description="you may change or delete it: editors of where it's defined")
    uses: int | None = Field(None, description="how many items have a value for it (when asked for one field)")


class FieldCreate(RequestModel):
    label: str = Field(min_length=1, max_length=80)
    type: FieldType
    target: FieldTarget = "resource"
    collection: int | None = Field(None, description="define it on this collection of the namespace (default: the namespace)")
    options: list[str] | None = Field(None, max_length=200, description="choice and choices fields")
    help: str | None = Field(None, max_length=300)
    published: bool = False


class FieldUpdate(RequestModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    options: list[str] | None = Field(None, max_length=200)
    help: str | None = Field(None, max_length=300, description="null clears it")
    published: bool | None = None
    ord: int | None = Field(None, description="its place among the namespace's fields, lowest first")


class FieldValue(ResponseModel):
    field: FieldDef
    value: Any = Field(None, description="text, a number, a date (YYYY, YYYY-MM or YYYY-MM-DD), yes/no, an option or options, a link")


class FieldValues(ResponseModel):
    fields: list[FieldValue] = Field(description="the fields that describe it, in order, with its values (null: none)")
    can_change: bool = Field(description="you may change its values")


class FieldValuesUpdate(RequestModel):
    values: dict[str, Any] = Field(description='{"<field id>": its value, or null to clear it}; fields not named keep theirs')
