"""Passkeys (WebAuthn): signing in with a fingerprint, face or device PIN instead of a password.

Every ceremony has two steps. The first hands the browser options with a fresh challenge and keeps the challenge, the
site it was for and what it's for in a `webauthn_flow` row (five minutes, used once). The second checks the browser's
answer against that row. Signing in ends with a login ticket (`login_ticket`, two minutes, used once) that the web
app's server swaps for a session (POST /auth/ticket): the browser talks to the API through the web app, which knows
the site the person is on, while the session is started by the web app's server, which doesn't.

A passkey belongs to the site it was made on (its RP ID, the host name): one made at https://lens.example.com doesn't
work at http://localhost:3000, so the passkeys list shows each one's site. Browsers only offer passkeys on https://
pages and on localhost.

People without a passkey yet get one through a sign-in link (`signin_link`): an admin makes one for a new person or
someone who lost theirs, `lens users link` prints one on the server, and "Lost your passkey?" emails one when mail is
set up. Opening it lets them add a passkey, once, before it expires.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import urllib.parse

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import auth, store

R = store.R
FLOW_MINUTES = 5
TICKET_SECONDS = 120
LINK_HOURS = 72
MAX_PER_ACCOUNT = 20
NAME_MAX = 60
TRANSPORTS = {t.value for t in AuthenticatorTransport}
UNKNOWN = (
    "this passkey isn't one Lens knows: it was removed, or made before Lens was set up again here. "
    "Pick another passkey, or get a sign-in link"
)


class PasskeyError(ValueError):
    """The browser's answer didn't check out, or the flow it answers is gone."""


class UnknownPasskey(PasskeyError):
    """A passkey this server doesn't know: removed, or made for an earlier install at the same address (it stays in the
    password manager when Lens is set up again, and clearing the site's data doesn't remove it)."""


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def site(origin):
    """(origin, rp id) for a web app address like https://lens.example.com; ValueError when browsers won't use passkeys
    there: plain http:// other than localhost, or an IP address."""
    u = urllib.parse.urlsplit(origin or "")
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        raise ValueError("passkeys need the web app's address")
    if host.replace(".", "").isdigit() or ":" in host:
        raise ValueError("passkeys don't work on an IP address; open Lens at its name (like localhost or its https:// address)")
    if u.scheme == "http" and host != "localhost" and not host.endswith(".localhost"):
        raise ValueError("passkeys only work on https:// addresses and on localhost; open Lens at its https:// address")
    return f"{u.scheme}://{u.netloc.lower()}", host


# ---------- flows (challenges) ----------
def _start(db, kind, origin, rp_id, challenge, account=None, data=None):
    db.q("DELETE webauthn_flow WHERE expires_at < $n", n=store.now())
    flow = secrets.token_urlsafe(24)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("webauthn_flow", auth.sha(flow)),
        d=store.clean(
            {
                "kind": kind,
                "challenge": bytes_to_base64url(challenge),
                "origin": origin,
                "rp_id": rp_id,
                "account": account,
                "data": json.dumps(data) if data is not None else None,
                "expires_at": _later(FLOW_MINUTES * 60),
            }
        ),
    )
    return flow


def _claim(db, flow, kind):
    """The flow's row, removed so it can't be answered twice; PasskeyError when it's gone, expired or for something else."""
    rows = db.rows("DELETE $r RETURN BEFORE", r=R("webauthn_flow", auth.sha(flow or ""))) if flow else []
    row = rows[0] if rows else None
    if not row or row.get("kind") != kind or (row.get("expires_at") or "") < store.now():
        raise PasskeyError("that took too long or was already used; try again")
    if row.get("data"):
        row["data"] = json.loads(row["data"])
    return row


def _user_handle(db, uid):
    """The account's WebAuthn user handle: random, made once (a passkey stores it, so it mustn't be an email or id)."""
    row = db.one("SELECT webauthn_user FROM $r", r=R("account", uid)) or {}
    if row.get("webauthn_user"):
        return row["webauthn_user"]
    handle = bytes_to_base64url(secrets.token_bytes(32))
    db.q("UPDATE $r SET webauthn_user = $h", r=R("account", uid), h=handle)
    return handle


def _creation(db, cfg, kind, origin, uid, email, name, data=None):
    origin, rp_id = site(origin)
    existing = db.rows("SELECT cred_id, transports FROM passkey WHERE account = $a AND rp_id = $p", a=uid, p=rp_id) if uid else []
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name="Lens",
        user_name=email,
        user_id=base64url_to_bytes(_user_handle(db, uid)) if uid else secrets.token_bytes(32),
        user_display_name=name or email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(p["cred_id"]),
                transports=[AuthenticatorTransport(t) for t in p.get("transports") or [] if t in TRANSPORTS],
            )
            for p in existing
        ],
    )
    if not uid:
        data = {**(data or {}), "user": bytes_to_base64url(opts.user.id)}
    flow = _start(db, kind, origin, rp_id, opts.challenge, uid, data)
    options = json.loads(options_to_json(opts))
    options["extensions"] = {"prf": {}}  # so a security key turns on what vaults need (app/domain/vaults.py)
    return {"flow": flow, "options": options}


def _verify_creation(db, row, credential):
    try:
        v = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(row["challenge"]),
            expected_rp_id=row["rp_id"],
            expected_origin=row["origin"],
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        raise PasskeyError(f"the passkey couldn't be checked: {e}") from None
    cred_id = bytes_to_base64url(v.credential_id)
    if db.one("SELECT id FROM $r", r=R("passkey", auth.sha(cred_id))):
        raise PasskeyError("that passkey is already in use")
    transports = (credential.get("response") or {}).get("transports") if isinstance(credential, dict) else None
    return {
        "cred_id": cred_id,
        "public_key": bytes_to_base64url(v.credential_public_key),
        "sign_count": v.sign_count,
        "aaguid": v.aaguid,
        "backed_up": bool(v.credential_backed_up),
        "transports": [t for t in (transports or []) if t in TRANSPORTS],
        "rp_id": row["rp_id"],
    }


def _room(db, uid):
    if len(db.values("SELECT VALUE id FROM passkey WHERE account = $a", a=uid)) >= MAX_PER_ACCOUNT:
        raise PasskeyError(f"an account can have at most {MAX_PER_ACCOUNT} passkeys; remove one first")


def _save(db, uid, cred, name=None):
    _room(db, uid)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("passkey", auth.sha(cred["cred_id"])),
        d={**cred, "account": uid, "name": _name(name, cred), "created_at": store.now()},
    )


def _name(name, cred=None):
    n = " ".join((name or "").split())[:NAME_MAX]
    if n:
        return n
    return "Synced passkey" if cred and cred.get("backed_up") else "Passkey"


# ---------- adding a passkey (signed in) ----------
def add_options(db, cfg, origin, user):
    return _creation(db, cfg, "add", origin, user["id"], user["email"], user.get("name"))


def add_finish(db, user_id, flow, credential, name=None):
    row = _claim(db, flow, "add")
    if row.get("account") != user_id:
        raise PasskeyError("that took too long or was already used; try again")
    cred = _verify_creation(db, row, credential)
    _save(db, user_id, cred, name)
    return _pid(cred["cred_id"])


# ---------- the first admin (setup) ----------
def setup_options(db, cfg, origin, email, name):
    """Options for the first admin's passkey; the account is only made when the passkey checks out."""
    email = (email or "").strip().lower()
    if not auth.EMAIL.match(email):
        raise ValueError("enter a valid email address")
    return _creation(db, cfg, "setup", origin, None, email, name, {"email": email, "name": (name or "").strip() or None})


def setup_finish(db, flow, credential, name=None):
    """Makes the first admin with the passkey. Returns their account id."""
    row = _claim(db, flow, "setup")
    if auth.account_count(db):
        raise PasskeyError("setup is closed")
    cred = _verify_creation(db, row, credential)
    d = row["data"]
    uid = auth.create_account(db, d["email"], None, d.get("name"), admin=True)
    db.q("UPDATE $r SET webauthn_user = $h", r=R("account", uid), h=d["user"])
    _save(db, uid, cred, name)
    return uid


# ---------- sign-in links (no passkey yet, or lost it) ----------
def create_link(db, uid, hours=LINK_HOURS, by=None, replace=True):
    """A one-time link for adding a passkey: the raw token (shown once). With `replace` (an admin's), older links for
    the account stop working; an emailed one leaves them, so a stranger asking for one can't cancel an admin's."""
    raw = secrets.token_urlsafe(32)
    if replace:
        db.q("DELETE signin_link WHERE account = $a", a=uid)
    else:
        db.q("DELETE signin_link WHERE account = $a AND expires_at < $n", a=uid, n=store.now())
    db.q(
        "CREATE $r CONTENT $d",
        r=R("signin_link", auth.sha(raw)),
        d=store.clean({"account": uid, "created_at": store.now(), "expires_at": _later(hours * 3600), "created_by": by}),
    )
    return raw


def link_url(raw, base=None):
    """The web app's page for a sign-in link."""
    from app.config import settings as env

    return f"{(base or env.FRONTEND_URL).rstrip('/')}/signin-link#{raw}"


def link_account(db, raw):
    """The active account a live link is for, or None."""
    row = db.one("SELECT account, expires_at FROM $r", r=R("signin_link", auth.sha(raw or ""))) if raw else None
    if not row or (row.get("expires_at") or "") < store.now():
        return None
    return auth.active_account(db, row["account"])


def link_options(db, cfg, origin, raw):
    u = link_account(db, raw)
    if not u:
        raise PasskeyError("this sign-in link is invalid or has expired; ask an admin for a new one")
    return _creation(db, cfg, "link", origin, u["id"], u["email"], u.get("name"), {"link": auth.sha(raw)})


def link_finish(db, raw, flow, credential, name=None):
    """Adds the passkey and uses the link up. Returns the account id."""
    row = _claim(db, flow, "link")
    u = link_account(db, raw)
    if not u or u["id"] != row.get("account") or row["data"].get("link") != auth.sha(raw):
        raise PasskeyError("this sign-in link is invalid or has expired; ask an admin for a new one")
    cred = _verify_creation(db, row, credential)
    _room(db, u["id"])  # before the link is used up
    if not db.rows("DELETE $r RETURN BEFORE", r=R("signin_link", auth.sha(raw))):
        raise PasskeyError("this sign-in link was already used")
    _save(db, u["id"], cred, name)
    db.q("DELETE signin_link WHERE account = $a", a=u["id"])  # signed in: the others aren't needed
    return u["id"]


def use_link(db, raw):
    """Sign in with the link alone, without adding a passkey (for addresses browsers won't use passkeys on): the
    account id, with the link used up; PasskeyError when it's invalid or expired."""
    u = link_account(db, raw)
    if not u or not db.rows("DELETE $r RETURN BEFORE", r=R("signin_link", auth.sha(raw))):
        raise PasskeyError("this sign-in link is invalid, used or expired; ask for a new one")
    db.q("DELETE signin_link WHERE account = $a", a=u["id"])
    db.q("UPDATE $r SET last_login_at = $t", r=R("account", u["id"]), t=store.now())
    return u["id"]


# ---------- signing in ----------
def login_options(db, origin):
    """Options for signing in with any passkey made on this site (the browser lists them: no email needed)."""
    origin, rp_id = site(origin)
    opts = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    return {"flow": _start(db, "login", origin, rp_id, opts.challenge), "options": json.loads(options_to_json(opts))}


def login_finish(db, flow, credential):
    """The account the passkey belongs to (active), after checking the browser's signature. PasskeyError otherwise."""
    row = _claim(db, flow, "login")
    pk = verified(db, row, credential)
    u = auth.active_account(db, pk["account"])
    if not u:
        raise PasskeyError("this account is disabled")
    db.q("UPDATE $r SET last_login_at = $t", r=R("account", u["id"]), t=store.now())
    return u


def verified(db, row, credential):
    """The passkey that signed the browser's answer to a flow (`row`, claimed), with its use recorded; PasskeyError
    when it isn't one of this site's or the signature doesn't check out."""
    cred_id = credential.get("id") if isinstance(credential, dict) else None
    pk = db.one("SELECT * FROM $r", r=R("passkey", auth.sha(cred_id))) if isinstance(cred_id, str) and cred_id else None
    if not pk or pk.get("rp_id") != row["rp_id"]:
        raise UnknownPasskey(UNKNOWN)
    handle = ((credential.get("response") or {}).get("userHandle")) or None
    acct = db.one("SELECT webauthn_user FROM $r", r=R("account", pk["account"])) or {}
    if handle and acct.get("webauthn_user") and handle != acct["webauthn_user"]:
        raise UnknownPasskey(UNKNOWN)
    try:
        v = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(row["challenge"]),
            expected_rp_id=row["rp_id"],
            expected_origin=row["origin"],
            credential_public_key=base64url_to_bytes(pk["public_key"]),
            credential_current_sign_count=int(pk.get("sign_count") or 0),
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        raise PasskeyError(f"the passkey couldn't be checked: {e}") from None
    db.q(
        "UPDATE $r SET sign_count = $c, last_used_at = $t, backed_up = $b",
        r=R("passkey", auth.sha(cred_id)),
        c=v.new_sign_count,
        t=store.now(),
        b=bool(v.credential_backed_up),
    )
    return pk


# ---------- login tickets ----------
def issue_ticket(db, uid, method):
    """A one-time ticket the web app's server swaps for a session (two minutes)."""
    db.q("DELETE login_ticket WHERE expires_at < $n", n=store.now())
    raw = "lt_" + secrets.token_urlsafe(32)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("login_ticket", auth.sha(raw)),
        d={"account": uid, "method": method, "expires_at": _later(TICKET_SECONDS)},
    )
    return raw


def redeem_ticket(db, raw):
    """(the active account, how it signed in) for a live ticket, used up; None otherwise."""
    rows = db.rows("DELETE $r RETURN BEFORE", r=R("login_ticket", auth.sha(raw or ""))) if raw else []
    row = rows[0] if rows else None
    if not row or (row.get("expires_at") or "") < store.now():
        return None
    u = auth.active_account(db, row["account"])
    return (u, row.get("method")) if u else None


# ---------- managing passkeys ----------
def _pid(cred_id):
    return auth.sha(cred_id)[:16]


def list_for(db, uid):
    rows = db.rows(
        "SELECT cred_id, name, rp_id, backed_up, created_at, last_used_at FROM passkey WHERE account = $a ORDER BY created_at",
        a=uid,
    )
    return [{"id": _pid(r.pop("cred_id")), **r} for r in rows]


def _find(db, uid, pid):
    for cid in db.values("SELECT VALUE cred_id FROM passkey WHERE account = $a", a=uid):
        if _pid(cid) == pid:
            return R("passkey", auth.sha(cid))
    return None


def rename(db, uid, pid, name):
    r = _find(db, uid, pid)
    if not r:
        return False
    db.q("UPDATE $r SET name = $n", r=r, n=_name(name))
    return True


def remove(db, uid, pid, passwords_on=False, here=None, others=0):
    """Remove one of the account's passkeys. Refused for the last way in (no other passkey, no password that works and
    no outside account, `others`), and for the last one that works on the site you're on (`here`, an RP ID): others
    for another site don't help here."""
    r = _find(db, uid, pid)
    if not r:
        return False
    from . import vaults

    if only := vaults.guards(db, [pid]):
        names = store.space_names(db)
        raise ValueError(
            f"this passkey is the only one that opens {', '.join(names.get(s, str(s)) for s in only)}: add another passkey to "
            "the vault first, or its files are lost"
        )
    if not (passwords_on and has_password(db, uid)) and not others:
        if count(db, uid) <= 1:
            raise ValueError("this is your last passkey: add another one first, or you couldn't sign in")
        rp = (db.one("SELECT rp_id FROM $r", r=r) or {}).get("rp_id")
        if here and rp == here and len(db.values("SELECT VALUE id FROM passkey WHERE account = $a AND rp_id = $p", a=uid, p=here)) <= 1:
            raise ValueError(f"this is your last passkey for {here}: add another one here first, or you couldn't sign in here")
    db.q("DELETE $r", r=r)
    return True


def count(db, uid):
    return len(db.values("SELECT VALUE id FROM passkey WHERE account = $a", a=uid))


def has_password(db, uid):
    return bool((db.one("SELECT pw FROM $r", r=R("account", uid)) or {}).get("pw"))


def remove_all(db, uid, lose_vaults=False):
    """An admin removes everyone's passkeys for an account (a lost or stolen device); their sessions end too. Refused
    when they are the only way into a vault, unless `lose_vaults` says its files may be lost."""
    from . import vaults

    pids = [_pid(c) for c in db.values("SELECT VALUE cred_id FROM passkey WHERE account = $a", a=uid)]
    if not lose_vaults and (only := vaults.guards(db, pids)):
        names = store.space_names(db)
        raise ValueError(
            f"these passkeys are the only ones that open {', '.join(names.get(s, str(s)) for s in only)}: its files "
            "would be lost for good. Have an owner unlock it and add another passkey (or make it ordinary) first"
        )
    n = len(db.rows("DELETE passkey WHERE account = $a RETURN BEFORE", a=uid))
    db.q("DELETE login_session WHERE account = $a", a=uid)
    return n
