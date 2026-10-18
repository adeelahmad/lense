"""Secrets kept in a password manager (docs/secrets.md): the managers Lens can read from, which secret fields refer
to one and whether their values could be read, and a manager's items to refer to. Never a secret's value: a secret
field is set with ``PUT /settings/{section}`` (a value, or a reference like ``vaultwarden://Item/password``), and from
then on nothing shows it. Admins only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import AdminReader, AdminWriter, Cfg, Db
from app.domain import secret_providers, settings
from app.schemas.secrets import SecretsTestResult, SecretsTry, SecretsView, VaultItems

router = APIRouter(prefix="/secrets", tags=["admin"])


@router.get("")
def get_secrets(user: AdminReader, request: Request, db: Db) -> SecretsView:
    """The password managers Lens can read secrets from and whether each is set up; every secret field in Settings,
    whether it's set, and for one that refers to a password manager its reference and whether this server could read
    its value (and when, or why not)."""
    return SecretsView(**settings.secrets_view(db, request.app.state.archive.base))


@router.get("/items")
def list_vault_items(
    user: AdminReader,
    cfg: Cfg,
    provider: str = Query("vaultwarden", description="the password manager"),
    q: str | None = Query(None, max_length=200, description="part of an item's name"),
    limit: int = Query(200, ge=1, le=1000),
) -> VaultItems:
    """The items of a password manager Lens may refer to (those in its folder): each one's name, type, the fields that
    hold something and the reference to each. Never their values. 409 when the manager isn't set up or can't be
    reached, with why."""
    p = secret_providers.PROVIDERS.get(provider)
    if p is None or not p.scheme:
        raise HTTPException(400, f"provider is one of: {', '.join(k for k, v in secret_providers.PROVIDERS.items() if v.scheme)}")
    if not p.configured(cfg):
        raise HTTPException(409, f"{p.label} isn't set up: Settings › Secrets")
    try:
        items = p.items(cfg, q, limit)
    except secret_providers.Unavailable as e:
        raise HTTPException(409, str(e)) from None
    return VaultItems(provider=p.id, items=items)


@router.post("/test")
def test_secrets(user: AdminWriter, cfg: Cfg, body: SecretsTry | None = None) -> SecretsTestResult:
    """Sign in to a password manager with the settings saved and count the items Lens may read, to check them."""
    p = secret_providers.PROVIDERS.get((body or SecretsTry()).provider)
    if p is None:
        raise HTTPException(400, f"provider is one of: {', '.join(secret_providers.PROVIDERS)}")
    return SecretsTestResult(**p.test(cfg))
