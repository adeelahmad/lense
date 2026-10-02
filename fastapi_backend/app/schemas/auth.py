from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel, Role


class UserPublic(ResponseModel):
    id: int
    email: str
    name: str | None = None
    admin: bool = False
    disabled: bool | None = None
    created_at: str | None = None
    last_login_at: str | None = None


class AuthStatus(ResponseModel):
    setup_required: bool


class LoginRequest(RequestModel):
    email: str
    password: str


class SetupRequest(RequestModel):
    code: str
    email: str
    password: str
    name: str | None = None


class RefreshRequest(RequestModel):
    refresh_token: str


class TokenPair(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="seconds until the access token expires")
    user: UserPublic


class Me(ResponseModel):
    user: UserPublic
    roles: dict[str, Role] = Field(description="namespace name -> role")
    partial: list[str] = Field(
        default_factory=list, description="namespaces you have no role in but see some collections of (roles on collections)"
    )
    via: Literal["access", "token", "oauth"]
    scope: Literal["read", "write"]


class ForgotPasswordRequest(RequestModel):
    email: str


class ResetPasswordRequest(RequestModel):
    token: str
    password: str


class PasswordChange(RequestModel):
    current_password: str
    new_password: str = Field(description="at least 10 characters")


class MeUpdate(RequestModel):
    name: str = Field(min_length=1, max_length=80, description="whitespace is collapsed")


class ApiTokenCreate(RequestModel):
    name: str = "token"
    scope: Literal["read", "write"] = "read"
    days: int | None = Field(
        None,
        ge=0,
        le=3650,
        description="how long it lasts; default: tokens.default_days; 0: never expires, when tokens.never_expire allows",
    )


class TokenLimits(ResponseModel):
    """How long API keys may last, set by admins (the tokens settings)."""

    default_days: int
    max_days: int
    never_expire: bool = Field(description="keys may be made that never expire (days: 0)")


class ApiToken(ResponseModel):
    id: int
    name: str
    scope: Literal["read", "write"]
    prefix: str
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None


class AccountToken(ApiToken):
    """Anyone's key, for admins."""

    account: int
    email: str | None = None


class ApiTokenCreated(ResponseModel):
    id: int
    token: str
    note: str = "copy it now; it won't be shown again"
