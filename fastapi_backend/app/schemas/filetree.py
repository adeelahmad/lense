"""Assets as folders and files (docs/file-tree.md)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import ResponseModel


class TreeEntry(ResponseModel):
    name: str
    folder: bool
    modified: str | None = None
    size: int | None = Field(None, description="a file's size in bytes")
    content_type: str | None = None


class TreeFolder(ResponseModel):
    path: str = Field(description="the folder, from the top: '' for the namespaces")
    entries: list[TreeEntry]
