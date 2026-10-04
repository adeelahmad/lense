"""People (admins manage accounts) and namespace members (owners manage roles)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.auth import UserPublic
from app.schemas.common import RequestModel, ResponseModel, Role


class UserWithRoles(UserPublic):
    roles: dict[str, Role] = Field(default_factory=dict, description="namespace name -> role")
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


class Member(ResponseModel):
    account: int
    role: Role
    email: str | None = None
    name: str | None = None


class MemberSet(RequestModel):
    """Who (by email, or by account id) and their role; a null role removes them from the namespace."""

    email: str | None = None
    account: int | None = None
    role: Role | None = None
