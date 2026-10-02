"""OAuth: apps that register, the consent page's questions and answers, tokens, and the apps a person gave access to."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import RequestModel, ResponseModel

Scope = Literal["read", "read write"]


class ClientRegistration(BaseModel):
    """Dynamic client registration (RFC 7591). Metadata Lens has no use for is ignored, as the RFC asks."""

    model_config = ConfigDict(extra="ignore")

    redirect_uris: list[str] = Field(description="https, http://localhost (any port), or the app's own scheme; 1 to 10")
    client_name: str | None = Field(None, description="shown on the consent page and in the list of apps with access")
    client_uri: str | None = None
    token_endpoint_auth_method: str = Field("none", description="none (public apps), client_secret_post or client_secret_basic")
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    scope: str | None = None


class ClientRegistered(ResponseModel):
    client_id: str
    client_secret: str | None = Field(None, description="only for apps that asked for one; shown once")
    client_id_issued_at: int
    client_secret_expires_at: int = Field(0, description="0: it doesn't expire")
    client_name: str
    client_uri: str | None = None
    redirect_uris: list[str]
    token_endpoint_auth_method: str
    grant_types: list[str] = ["authorization_code", "refresh_token"]
    response_types: list[str] = ["code"]
    scope: str = "read write"


class ConsentClient(ResponseModel):
    id: str
    name: str
    uri: str | None = None


class Consent(ResponseModel):
    """What the consent page shows."""

    client: ConsentClient
    redirect_uri: str
    scope: Scope = Field(description="what the app asks for: read, or read and write")
    granted: Scope | None = Field(None, description="what you gave this app before, if anything")


class ConsentAnswer(RequestModel):
    client_id: str
    redirect_uri: str
    response_type: str = "code"
    code_challenge: str
    code_challenge_method: str = "S256"
    scope: str | None = None
    state: str | None = Field(None, max_length=2000)
    resource: str | None = Field(None, max_length=500)
    approve: bool
    grant: Scope | None = Field(None, description="give less than was asked for (read); default: what was asked for")


class ConsentResult(ResponseModel):
    redirect_to: str = Field(description="send the browser here: the app's address with its code, or with error=access_denied")


class OAuthTokens(ResponseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = Field(description="seconds until the access token expires")
    refresh_token: str
    scope: Scope


class OAuthGrant(ResponseModel):
    """An app you gave access to."""

    id: int
    client: str
    name: str
    uri: str | None = None
    scope: Scope
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = Field(None, description="when its access ends unless the app renews it before")
