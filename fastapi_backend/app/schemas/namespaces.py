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


class IpGroup(ResponseModel):
    """Address ranges whose visitors see all of some of the namespace's recordings without signing in (docs/access.md)."""

    id: int
    name: str
    ranges: list[str] = Field(description="addresses and CIDR ranges")
    everything: bool = Field(description="opens every recording in the namespace; else the ones chosen on each recording")
    chosen: int = Field(0, description="how many recordings it opens when it doesn't open everything")
    here: bool = Field(False, description="the address you're asking from is in it")
    by: str | None = None
    at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class IpGroups(ResponseModel):
    address: str | None = Field(
        None, description="your address as the server sees it; null when it can't tell (see server.trusted_proxies)"
    )
    groups: list[IpGroup]


class IpGroupCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    ranges: list[str] = Field(min_length=1, max_length=100, description="addresses (203.0.113.7) or CIDR ranges (203.0.113.0/24)")
    everything: bool = Field(False, description="open every recording in the namespace; else choose them on each recording")


class IpGroupUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    ranges: list[str] | None = Field(None, min_length=1, max_length=100)
    everything: bool | None = None
