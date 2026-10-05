"""Vaults: namespaces only their people's passkeys open (docs/encryption.md#vaults).

A namespace's data key (keyring.py) is normally also wrapped by the server's key, so background work can read it
whenever. Turning a namespace into a vault wraps the key for one of your passkeys, then removes the server's wrapper.
From then on, the server holds nothing that opens it: someone with one of its passkeys has to unlock it, and it stays
open in this process for encryption.vault_minutes (or until locked). While it's locked its files don't open and its
queued work waits.

A passkey opens a vault through the WebAuthn PRF extension: signing in with the passkey and a salt made for the vault
makes the authenticator compute 32 secret bytes that only it can make again. The browser hands them over with the
signed answer; the key-encryption key is HKDF-SHA-256 of them, and neither is kept. A vault has as many passkeys as
its admins add while it's open, and there is no other way in: lose every one of them and its files are gone for good.
Turning it back into an ordinary namespace wraps the key for the server again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading

from webauthn import generate_authentication_options, options_to_json
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import AuthenticatorTransport, PublicKeyCredentialDescriptor, UserVerificationRequirement

from . import keyring, passkeys, store

R = store.R
log = logging.getLogger(__name__)
PREFIX = "passkey:"
KINDS = ("seal", "unlock", "add")


class VaultError(ValueError):
    """What was asked doesn't fit the vault's state, or the passkey can't open vaults."""


def salt(sid):
    """The PRF salt for a namespace: the same every time, so a passkey always makes the same secret for it."""
    return hashlib.sha256(f"lens-vault:{int(sid)}".encode()).digest()


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _minutes(cfg):
    return int((cfg.get("encryption") or {}).get("vault_minutes") or 60)


def wrapper_of(pk):
    """The keyring wrapper name for a passkey."""
    return PREFIX + passkeys._pid(pk["cred_id"])


def status(db, sid):
    """{vault, unlocked, unlocked_until, passkeys: [{id, name, account, email}]} for a namespace."""
    until = keyring.unlocked_until(db, sid)  # first: one whose time is up closes
    st = keyring.status(db, sid)
    names = [w[len(PREFIX) :] for w in st["wrappers"] if w.startswith(PREFIX)]
    people = {}
    for pk in db.rows("SELECT cred_id, name, account FROM passkey"):
        pid = passkeys._pid(pk["cred_id"])
        if pid in names:
            people[pid] = pk
    emails = {a["id"]: a.get("email") for a in db.rows("SELECT record::id(id) AS id, email FROM account")} if people else {}
    return {
        "vault": st["vault"],
        "unlocked": st["vault"] and bool(st.get("unlocked")),
        "unlocked_until": until,
        "passkeys": [
            {
                "id": pid,
                "name": (people.get(pid) or {}).get("name"),
                "account": (people.get(pid) or {}).get("account"),
                "email": emails.get((people.get(pid) or {}).get("account")),
            }
            for pid in names
        ],
    }


def options(db, origin, uid, sid, kind):
    """Options for signing with one of your passkeys on this site, asking it for the vault's PRF secret."""
    if kind not in KINDS:
        raise VaultError(f"kind is one of {', '.join(KINDS)}")
    origin, rp_id = passkeys.site(origin)
    mine = db.rows("SELECT cred_id, transports FROM passkey WHERE account = $a AND rp_id = $p", a=uid, p=rp_id)
    if kind == "unlock":  # only the passkeys that open it
        opens = set(keyring.status(db, sid)["wrappers"])
        mine = [p for p in mine if wrapper_of(p) in opens]
    if not mine:
        raise VaultError(
            "none of your passkeys opens this namespace" if kind == "unlock" else "add a passkey to your account on this site first"
        )
    opts = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(p["cred_id"]),
                transports=[AuthenticatorTransport(t) for t in p.get("transports") or [] if t in passkeys.TRANSPORTS],
            )
            for p in mine
        ],
    )
    out = json.loads(options_to_json(opts))
    out["extensions"] = {"prf": {"eval": {"first": _b64url(salt(sid))}}}
    flow = passkeys._start(db, "vault", origin, rp_id, opts.challenge, uid, {"space": int(sid), "kind": kind})
    return {"flow": flow, "options": out}


def _answer(db, uid, sid, kind, flow, credential, prf):
    """(the passkey, its key-encryption key) once the browser's answer checks out for this vault and purpose."""
    row = passkeys._claim(db, flow, "vault")
    data = row.get("data") or {}
    if row.get("account") != uid or data.get("space") != int(sid) or data.get("kind") != kind:
        raise passkeys.PasskeyError("that answer was for something else; try again")
    pk = passkeys.verified(db, row, credential)
    if pk["account"] != uid:
        raise passkeys.PasskeyError("use one of your own passkeys")
    try:
        secret = base64.urlsafe_b64decode((prf or "") + "=" * (-len(prf or "") % 4))
    except (ValueError, TypeError):
        secret = b""
    if len(secret) != 32:
        raise VaultError(
            "this passkey can't open vaults (it doesn't support the PRF extension); use a newer phone, computer or security key"
        )
    return pk, keyring.derive(secret, "lens/vault/v1", salt=salt(sid))


def seal(db, cfg, uid, sid, flow, credential, prf):
    """Make the namespace a vault opened by this passkey. Its files not encrypted yet (encryption.files was off) are
    encrypted first, while the server's key still opens it, so none is left plain. It then stays open here for
    encryption.vault_minutes."""
    if keyring.status(db, sid)["vault"]:
        raise VaultError("this namespace is a vault already")
    pk, kek = _answer(db, uid, sid, "seal", flow, credential, prf)
    if (keyring.conversion["to"] == "plain") and keyring.conversion["running"]:
        keyring.stop_converting()  # it would turn this namespace's files back to plain
    keyring.add_wrapper(db, cfg, sid, wrapper_of(pk), kek)
    keyring.encrypt_all(db, cfg, space=sid, log=log.info)
    keyring.remove_wrapper(db, sid, keyring.SERVER)
    keyring.keep_open(db, sid, _minutes(cfg))
    keyring.encrypt_all(db, cfg, space=sid, log=log.info)  # any stored while that ran
    return status(db, sid)


def _catch_up(db, cfg, sid):
    """Encrypt, in the background, a vault's files that arrived plain (while it was being made a vault)."""
    encrypting[int(sid)] = th = threading.Thread(
        target=keyring.encrypt_all, args=(db, cfg), kwargs={"space": sid, "log": log.info}, daemon=True, name=f"vault-{sid}"
    )
    th.start()


encrypting: dict[int, threading.Thread] = {}


def unlock(db, cfg, uid, sid, flow, credential, prf):
    """Open the vault with one of its passkeys, for encryption.vault_minutes."""
    pk, kek = _answer(db, uid, sid, "unlock", flow, credential, prf)
    keyring.unlock(db, sid, wrapper_of(pk), kek, _minutes(cfg))
    _catch_up(db, cfg, sid)
    return status(db, sid)


def add(db, cfg, uid, sid, flow, credential, prf):
    """Let another passkey (yours, or another admin's) open the vault; it must be open."""
    _open(db, sid)
    pk, kek = _answer(db, uid, sid, "add", flow, credential, prf)
    keyring.add_wrapper(db, cfg, sid, wrapper_of(pk), kek)
    return status(db, sid)


def remove(db, sid, pid):
    """Stop a passkey opening the vault; never the last one."""
    name = PREFIX + pid
    if name not in keyring.status(db, sid)["wrappers"]:
        raise KeyError(pid)
    try:
        keyring.remove_wrapper(db, sid, name)
    except ValueError:
        raise VaultError("this is the only passkey that opens this vault; add another first") from None
    return status(db, sid)


def unseal(db, cfg, sid):
    """Make the vault an ordinary namespace again (the server can open it); it must be open."""
    _open(db, sid)
    keyring.add_wrapper(db, cfg, sid, keyring.SERVER, keyring.server_kek(cfg))
    keyring.keep_open(db, sid, None)
    for pid in [p["id"] for p in status(db, sid)["passkeys"]]:
        keyring.remove_wrapper(db, sid, PREFIX + pid)
    return status(db, sid)


def lock(db, sid):
    keyring.lock(db, sid)
    return status(db, sid)


def _open(db, sid):
    st = keyring.status(db, sid)
    if not st["vault"]:
        raise VaultError("this namespace isn't a vault")
    keyring.unlocked_until(db, sid)  # a vault whose time is up closes first
    if not keyring.status(db, sid).get("unlocked"):
        raise keyring.Locked(sid)


def guards(db, pids):
    """The vaults these passkeys (ids) are the only way into: removing them would lose them. Wrappers of passkeys
    already gone don't count, since nothing can answer for them."""
    gone = {PREFIX + p for p in pids}
    live = {PREFIX + passkeys._pid(c) for c in db.values("SELECT VALUE cred_id FROM passkey")} - gone
    out = []
    for row in db.rows("SELECT space, keys, current FROM data_key WHERE vault = true"):
        w = set(row["keys"][str(row["current"])]["wrapped"])
        if w & gone and not w & live:
            out.append(row["space"])
    return out
