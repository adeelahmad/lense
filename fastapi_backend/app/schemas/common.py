"""Base classes shared by every schema.

Request bodies reject unknown fields, so typos fail loudly. Response models allow extra fields: the domain layer returns
plain dicts and may carry fields a schema doesn't list yet; they still reach the client, just untyped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["viewer", "editor", "owner"]
AccessLevel = Literal["public", "restricted", "private"]
AccessPart = Literal["media", "transcript", "index"]
# when a grant (a role in a namespace or on a collection, permission on an asset) ends (docs/access.md#grants-that-end)
GRANT_END = (
    "when it ends (UTC unless the time says otherwise): after that it opens nothing, and lists show it as expired. "
    "null: no end. Left out: the end it had, unless that has passed (given again, it starts over without one)."
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Ok(ResponseModel):
    ok: bool = True


class Created(Ok):
    id: int


class GrantState(ResponseModel):
    """A grant's end, as lists show it."""

    expires_at: str | None = Field(None, description="when it ends (UTC); null: no end")
    expired: bool = Field(False, description="it has ended: it opens nothing until it's given again")
