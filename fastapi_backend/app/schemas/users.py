"""People (admins manage accounts) and namespace members (owners manage roles)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import Field

from app.schemas.auth import UserPublic
from app.schemas.common import GRANT_END, GrantState, RequestModel, ResponseModel

RoleName = Annotated[str, Field(min_length=1, max_length=40, description="viewer, editor, owner or a custom role's name")]


class UserWithRoles(UserPublic):
    roles: dict[str, str] = Field(default_factory=dict, description="namespace name -> role (built-in or custom)")
    passkeys: int = Field(0, description="how many passkeys they have")
    password: bool = Field(False, description="whether they have a password")


class UserCreate(RequestModel):
    email: str
    password: str | None = Field(default=None, description="only where passwords are on; without one, send them a sign-in link")
    name: str | None = None
    admin: bool = False


class UserUpdate(RequestModel):
    name: str | None = None
    admin: bool | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, description="a new password; signs the person out everywhere")


class Member(GrantState):
    account: int
    role: str
    email: str | None = None
    name: str | None = None


class MemberSet(RequestModel):
    """Who (by email, or by account id) and their role; a null role removes them from the namespace."""

    email: str | None = None
    account: int | None = None
    role: RoleName | None = None
    expires_at: dt.datetime | None = Field(None, description=GRANT_END)


class RolePermission(ResponseModel):
    name: str
    label: str
    first: str = Field(description="the built-in role that first has it")


class RoleOut(ResponseModel):
    name: str
    label: str
    description: str = ""
    permissions: list[str]
    builtin: bool
    rank: str = Field(description="the largest built-in role it covers; the checks that ask for a rank give it that much")
    members: int = Field(0, description="how many people hold it, across namespaces")


class Roles(ResponseModel):
    permissions: list[RolePermission]
    roles: list[RoleOut]


class RoleCreate(RequestModel):
    name: str = Field(description="2 to 40 lowercase letters, digits, - and _; can't change later")
    label: str | None = None
    description: str | None = None
    permissions: list[str] = Field(default_factory=list, description="read is always included")


class RoleUpdate(RequestModel):
    label: str | None = None
    description: str | None = None
    permissions: list[str] | None = None
