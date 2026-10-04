from __future__ import annotations

from typing import Any, Literal

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
    # a fresh install whose setup wizard (namespace, model provider, storage) the first admin hasn't finished
    wizard_pending: bool = False
    # whether passwords sign in at all (auth.passwords); passkeys always do
    passwords: bool = False


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


class PasskeyOptions(ResponseModel):
    """What to pass to the browser (navigator.credentials.create or .get, as JSON), and the flow to answer."""

    flow: str
    options: dict[str, Any]


class PasskeySetupStart(RequestModel):
    code: str
    email: str
    name: str | None = None


class PasskeyAnswer(RequestModel):
    flow: str
    credential: dict[str, Any] = Field(description="the browser's PublicKeyCredential, as JSON (toJSON())")
    name: str | None = Field(default=None, max_length=60, description="what to call a new passkey, like 'MacBook'")


class SigninLinkToken(RequestModel):
    token: str


class SigninLinkAnswer(PasskeyAnswer):
    token: str


class SigninLinkInfo(ResponseModel):
    email: str
    name: str | None = None


class SigninLink(ResponseModel):
    url: str = Field(description="open it on the device to sign in with; it works once")
    expires_at: str


class LoginTicket(ResponseModel):
    ticket: str = Field(description="swap it for a session at POST /auth/ticket within two minutes; it works once")


class TicketRequest(RequestModel):
    ticket: str


class Passkey(ResponseModel):
    id: str
    name: str
    rp_id: str = Field(description="the site it works on (a passkey only works there)")
    backed_up: bool = Field(False, description="synced by a password manager or the device's cloud account")
    created_at: str
    last_used_at: str | None = None


class PasskeyRename(RequestModel):
    name: str = Field(min_length=1, max_length=60)


# ---------- outside accounts (app/domain/external_login.py) ----------
ExternalKind = Literal["google", "github", "microsoft", "oidc"]


class ExternalProvider(ResponseModel):
    key: str
    kind: ExternalKind
    label: str


class ExternalProviderAdmin(ExternalProvider):
    client_id: str
    secret_set: bool = Field(description="a client secret is kept (never shown)")
    issuer: str = Field("", description="OpenID Connect: the provider's address")
    tenant: str = Field("", description="Microsoft: the directory (tenant) id, or common")
    signup: bool = Field(description="people without a Lens account get one when they sign in")
    domains: list[str] = Field(description="sign-up only for these email domains; empty means any")
    enabled: bool
    people: int = Field(description="Lens accounts connected through it")
    callback_path: str = Field(description="add this path on the web app's address as the redirect URI at the provider")


class ExternalProviderSave(RequestModel):
    kind: ExternalKind | None = Field(None, description="when adding one")
    label: str | None = Field(None, max_length=60)
    client_id: str | None = Field(None, max_length=500)
    client_secret: str | None = Field(None, max_length=2000, description="leave out to keep the one kept")
    issuer: str | None = Field(None, max_length=500)
    tenant: str | None = Field(None, max_length=100)
    signup: bool | None = None
    domains: list[str] | None = Field(None, max_length=50)
    enabled: bool | None = None


class ExternalStart(RequestModel):
    next: str = Field("/", max_length=500, description="the page to open after signing in")


class ExternalRedirect(ResponseModel):
    url: str = Field(description="the provider's sign-in page; open it in this browser")


class ExternalIdentity(ResponseModel):
    id: str
    provider: str
    label: str
    kind: ExternalKind
    email: str | None = None
    created_at: str
    last_used_at: str | None = None
