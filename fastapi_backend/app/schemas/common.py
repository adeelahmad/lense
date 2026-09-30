"""Base classes shared by every schema.

Request bodies reject unknown fields, so typos fail loudly. Response models allow extra fields: the domain layer returns
plain dicts and may carry fields a schema doesn't list yet; they still reach the client, just untyped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["viewer", "editor", "owner"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Ok(ResponseModel):
    ok: bool = True


class Created(Ok):
    id: int
