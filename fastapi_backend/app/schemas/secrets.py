from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class SecretProvider(ResponseModel):
    id: str
    label: str
    scheme: str | None = Field(None, description="how references to it start (vaultwarden://…); none for the built-in store")
    configured: bool = Field(description="set up, so a reference to it can be read")
    scope: str | None = Field(None, description="what it reads, in words: never a secret")
    url: str | None = None
    email: str | None = None
    folder: str | None = None
    installed: bool | None = Field(None, description="its client is fetched (a component), when it needs one")


class SecretReference(ResponseModel):
    section: str
    key: str
    ref: str = Field(description="the reference the field holds, like vaultwarden://OpenAI/password")
    provider: str
    read: bool | None = Field(None, description="whether its value could be read by this server process; null before the first read")
    error: str | None = Field(None, description="why it couldn't be read, last time it was tried")
    read_at: str | None = Field(None, description="when its value was last read")
    missing: bool = Field(False, description="the password manager answered that the item or field isn't there")


class SecretsView(ResponseModel):
    providers: list[SecretProvider]
    references: list[SecretReference] = Field(description="every secret field that refers to a password manager")
    fields: list[dict[str, Any]] = Field(description="every secret field in Settings: section, key, set, and its ref when it holds one")


class VaultItem(ResponseModel):
    name: str
    type: str = Field(description="login, note, card, identity or other")
    fields: list[str] = Field(description="the names of the fields that hold something")
    ref: str | None = Field(None, description="the reference to the item's default field (a login's password, a note's text)")
    refs: dict[str, str] = Field(default_factory=dict, description="the reference to each field")


class VaultItems(ResponseModel):
    provider: str
    items: list[VaultItem]


class SecretsTry(RequestModel):
    provider: str = "vaultwarden"


class SecretsTestResult(ResponseModel):
    ok: bool
    error: str | None = None
    items: int | None = Field(None, description="how many items the folder Lens reads holds")
    folder: str | None = None
    account: str | None = None
