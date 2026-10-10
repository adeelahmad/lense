"""Marketplaces: where plugins and skills in Anthropic's format are installed from. Admins add them; people who write
extensions install from them."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminWriter, Cfg, CurrentUser, Db, Writer
from app.api.v1.routes.extensions import _errors, me
from app.domain import auth, marketplace
from app.schemas.common import Created, Ok
from app.schemas.extensions import (
    ExtensionImported,
    ExtensionImportPreview,
    Marketplace,
    MarketplaceCreate,
    MarketplacePlugin,
    MarketplaceSuggestion,
)

router = APIRouter(prefix="/marketplaces", tags=["extensions"])


@router.get("")
def list_marketplaces(user: CurrentUser, db: Db) -> list[Marketplace]:
    return marketplace.listing(db)


@router.get("/suggested")
def suggested_marketplaces(user: CurrentUser, db: Db) -> list[MarketplaceSuggestion]:
    """Anthropic's marketplaces, when they aren't added yet."""
    return marketplace.suggested(db)


@router.post("")
def add_marketplace(body: MarketplaceCreate, user: AdminWriter, db: Db, cfg: Cfg) -> Created:
    """Add a marketplace by its address; its list of plugins is read now."""
    with _errors():
        mid = marketplace.add(db, cfg, me(user), body.source, body.title)
    auth.audit(db, user.as_audit(), "marketplace.add", f"marketplace:{mid}", {"source": body.source})
    return Created(id=mid)


@router.post("/{mid}/refresh")
def refresh_marketplace(mid: int, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Read its list of plugins again."""
    with _errors():
        marketplace.refresh(db, cfg, me(user), mid)
    return Ok()


@router.delete("/{mid}")
def delete_marketplace(mid: int, user: AdminWriter, db: Db) -> Ok:
    """Takes it away; what was installed from it stays."""
    with _errors():
        marketplace.remove(db, me(user), mid)
    auth.audit(db, user.as_audit(), "marketplace.delete", f"marketplace:{mid}")
    return Ok()


@router.get("/{mid}/plugins")
def list_marketplace_plugins(mid: int, user: CurrentUser, db: Db) -> list[MarketplacePlugin]:
    with _errors():
        return marketplace.plugins(db, me(user), mid)


@router.post("/{mid}/plugins/{name}/preview")
def preview_marketplace_plugin(mid: int, name: str, user: Writer, db: Db, cfg: Cfg) -> ExtensionImportPreview:
    """What installing it would add to the assistant, and what Lens leaves out."""
    with _errors():
        return marketplace.preview(db, cfg, me(user), mid, name)


@router.post("/{mid}/plugins/{name}/install")
def install_marketplace_plugin(mid: int, name: str, user: Writer, db: Db, cfg: Cfg) -> ExtensionImported:
    """Install it as your own plugin, private until you share it; installing it again brings its new version."""
    with _errors():
        eid, n = marketplace.install(db, cfg, me(user), mid, name)
    auth.audit(db, user.as_audit(), "extension.import", f"extension:{eid}", {"marketplace": mid, "plugin": name, "version": n})
    return ExtensionImported(id=eid, version=n)
