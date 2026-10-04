"""Vaults: namespaces only their people's passkeys open (app/domain/vaults.py, docs/encryption.md#vaults).

Owners make a namespace a vault, add and remove the passkeys that open it, and turn it back; anyone whose passkey opens
it can unlock it for a while, or lock it now. Every step that needs a passkey is two calls, like signing in: options
for the browser (asking for the PRF extension's secret for this vault), then its answer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, web_origin
from app.domain import auth, keyring, passkeys, vaults
from app.schemas.auth import PasskeyOptions
from app.schemas.namespaces import VaultAnswer, VaultStart, VaultStatus

router = APIRouter(prefix="/namespaces", tags=["vaults"])


def _signed_in(user: CurrentUser) -> None:
    if user.via != "access":
        raise HTTPException(403, "sign in with a passkey for this; API tokens and apps can't")


def _space(acl: Acl, name: str, kind: str) -> int:
    return acl.namespace(name, "owner") if kind in ("seal", "add") else acl.namespace(name)


def _run(fn, *args):
    try:
        return fn(*args)
    except keyring.Locked:
        raise HTTPException(423, "this vault is locked; unlock it with one of its passkeys first") from None
    except RuntimeError:  # its keys changed meanwhile (keyring._save)
        raise HTTPException(409, "the vault changed just now; try again") from None
    except (passkeys.PasskeyError, vaults.VaultError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.get("/{name}/vault")
def get_vault(name: str, acl: Acl, db: Db) -> VaultStatus:
    """Whether the namespace is a vault, whether it's open now, and which passkeys open it (whose, for owners)."""
    sid = acl.namespace(name)
    out = vaults.status(db, sid)
    try:
        acl.need(sid, "owner")
    except HTTPException:
        out["passkeys"] = [{**p, "account": None, "email": None} for p in out["passkeys"]]
    return VaultStatus(**out)


@router.post("/{name}/vault/options")
def vault_options(name: str, body: VaultStart, user: Writer, acl: Acl, request: Request, db: Db) -> PasskeyOptions:
    """Start making the namespace a vault (owners), unlocking it, or adding a passkey to it (owners): options for the
    browser, which ask the passkey for this vault's PRF secret."""
    _signed_in(user)
    sid = _space(acl, name, body.kind)
    return PasskeyOptions(**_run(vaults.options, db, web_origin(request), user.id, sid, body.kind))


def _answer(kind: str, name: str, body: VaultAnswer, user: Writer, acl: Acl, db: Db, cfg: dict) -> VaultStatus:
    _signed_in(user)
    sid = _space(acl, name, kind)
    fn = {"seal": vaults.seal, "unlock": vaults.unlock, "add": vaults.add}[kind]
    out = _run(fn, db, cfg, user.id, sid, body.flow, body.credential, body.prf)
    auth.audit(db, user.as_audit(), f"vault.{kind}", f"space:{sid}")
    return VaultStatus(**out)


@router.post("/{name}/vault")
def seal_vault(name: str, body: VaultAnswer, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> VaultStatus:
    """Make the namespace a vault opened by the passkey that answered. From then on only its passkeys open its files;
    lose every one and they're gone. Audited as `vault.seal`."""
    return _answer("seal", name, body, user, acl, db, cfg)


@router.post("/{name}/vault/unlock")
def unlock_vault(name: str, body: VaultAnswer, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> VaultStatus:
    """Open the vault on this server for encryption.vault_minutes; its waiting work runs. Audited as `vault.unlock`."""
    return _answer("unlock", name, body, user, acl, db, cfg)


@router.post("/{name}/vault/passkeys")
def add_vault_passkey(name: str, body: VaultAnswer, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> VaultStatus:
    """Let the passkey that answered open the vault too (it must be open). Audited as `vault.add`."""
    return _answer("add", name, body, user, acl, db, cfg)


@router.delete("/{name}/vault/passkeys/{pid}")
def remove_vault_passkey(name: str, pid: str, user: Writer, acl: Acl, db: Db) -> VaultStatus:
    """Stop a passkey opening the vault; never the last one. Audited as `vault.remove`."""
    sid = acl.namespace(name, "owner")
    out = _run(vaults.remove, db, sid, pid)
    auth.audit(db, user.as_audit(), "vault.remove", f"space:{sid}", [pid])
    return VaultStatus(**out)


@router.post("/{name}/vault/lock")
def lock_vault(name: str, user: Writer, acl: Acl, db: Db) -> VaultStatus:
    """Close the vault on this server now (owners). Audited as `vault.lock`."""
    sid = acl.namespace(name, "owner")
    out = _run(vaults.lock, db, sid)
    auth.audit(db, user.as_audit(), "vault.lock", f"space:{sid}")
    return VaultStatus(**out)


@router.delete("/{name}/vault")
def unseal_vault(name: str, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> VaultStatus:
    """Make the vault an ordinary namespace again, which the server can open (it must be unlocked). Audited as
    `vault.unseal`."""
    sid = acl.namespace(name, "owner")
    out = _run(vaults.unseal, db, cfg, sid)
    auth.audit(db, user.as_audit(), "vault.unseal", f"space:{sid}")
    return VaultStatus(**out)
