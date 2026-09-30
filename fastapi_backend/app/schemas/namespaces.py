from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel, Role

GraphMode = Literal["shared", "isolated"]


class Namespace(ResponseModel):
    id: int
    name: str
    graph: str | None = None
    recordings: int = 0
    ms: int = Field(0, description="total duration of its recordings")
    analyzed: int = 0
    errors: int = 0
    speakers: int = 0
    role: Role
    wordcloud: str | None = Field(None, description="signed link to the namespace word cloud (SVG)")


class NamespaceCreate(RequestModel):
    name: str = Field(description="lowercase letters, digits, - and _")
    graph: GraphMode = "shared"


class NamespaceUpdate(RequestModel):
    """Only the fields you send change; send ``pipeline: null`` to go back to the default pipeline."""

    graph: GraphMode | None = None
    pipeline: int | None = None
