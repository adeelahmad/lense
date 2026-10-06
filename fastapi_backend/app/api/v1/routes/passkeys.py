"""Passkeys: making the first admin with one, signing in with one, sign-in links for adding one, and managing your own
(docs/access.md#signing-in; app/domain/passkeys.py).

The browser calls these through the web app, so the API knows the site it's on (web_origin). Signing in ends with a
one-time ticket; the web app's server swaps it for a session at POST /auth/ticket.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.api.deps import AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors, visitor_address, web_origin
from app.config import settings
from app.core.security import create_access_token
from app.domain import auth, external_login, passkeys
from app.email import app_url, send_signin_link_email
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginTicket,
    Passkey,
    PasskeyAnswer,
    PasskeyOptions,
    PasskeyRename,
    PasskeySetupStart,
    SigninLink,
    SigninLinkAnswer,
    SigninLinkInfo,
    SigninLinkToken,
    TicketRequest,
    TokenPair,
)
from app.schemas.common import Ok

router = APIRouter(prefix="/auth", tags=["auth"])
people = APIRouter(tags=["users"])


def _visitor(request: Request) -> str:
    """The visitor's address (through trusted proxies), else the peer's (for the session's record)."""
    addr = visitor_address(request)
    return str(addr) if addr else (request.client.host if request.client else "")


def _throttle(request: Request, what: str, shared: bool = False) -> str | None:
    """The throttle key for this visitor, or None when the server can't tell visitors apart (the web app isn't in
    server.trusted_proxies or LENS_TRUSTED_PROXY_HOSTS, so everyone arrives from its address): one bucket for everyone would let anybody lock
    everyone out. Passkey signatures and 256-bit links can't be guessed anyway; `shared` keeps one bucket for the
    setup code, which is shorter."""
    addr = visitor_address(request)
    if not addr and not shared:
        return None
    key = f"{what}|{addr or (request.client.host if request.client else '')}"
    if auth.throttled(key):
        raise HTTPException(429, "too many attempts; try again in a few minutes")
    return key


def _hit(key: str | None) -> None:
    if key:
        auth.hit(key)


def _options(fn, *args) -> PasskeyOptions:
    try:
        return PasskeyOptions(**fn(*args))
    except passkeys.PasskeyError as e:
        raise HTTPException(400, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ---------- the first admin ----------
@router.post("/passkey/setup/options")
def passkey_setup_options(body: PasskeySetupStart, request: Request, db: Db, cfg: Cfg) -> PasskeyOptions:
    """Start making the first admin with a passkey, with the one-time setup code from the server log."""
    key = _throttle(request, "setup", shared=True)
    code = request.app.state.archive.setup_code
    if not code or auth.account_count(db) or not secrets.compare_digest(body.code.strip(), code):
        _hit(key)
        raise HTTPException(403, "setup is closed or the code is wrong")
    return _options(passkeys.setup_options, db, cfg, web_origin(request), body.email, body.name)


@router.post("/passkey/setup")
def passkey_setup(body: PasskeyAnswer, request: Request, db: Db) -> LoginTicket:
    """Make the first admin with the passkey the browser just created. Answers a ticket for signing in."""
    archive = request.app.state.archive
    with auth.SETUP_LOCK:
        if not archive.setup_code:
            raise HTTPException(403, "setup is closed")
        try:
            uid = passkeys.setup_finish(db, body.flow, body.credential, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        archive.setup_code = None
    u = auth.get_account(db, uid)
    auth.audit(db, {"id": uid, "email": u["email"]}, "setup", detail=["passkey"])
    return LoginTicket(ticket=passkeys.issue_ticket(db, uid, "passkey"))


@router.post("/setup/no-passkey")
def setup_without_passkey(body: PasskeySetupStart, request: Request, db: Db) -> LoginTicket:
    """Make the first admin with the setup code alone, where the browser can't make passkeys (a plain http:// address
    other than localhost). No password: they sign in later with a passkey (at an https:// address) or a sign-in link."""
    key = _throttle(request, "setup", shared=True)
    archive = request.app.state.archive
    with auth.SETUP_LOCK:
        code = archive.setup_code
        if not code or auth.account_count(db) or not secrets.compare_digest(body.code.strip(), code):
            _hit(key)
            raise HTTPException(403, "setup is closed or the code is wrong")
        with domain_errors():
            uid = auth.create_account(db, body.email, None, body.name, admin=True)
        archive.setup_code = None
    auth.audit(db, {"id": uid, "email": body.email.strip().lower()}, "setup", detail=["setup-code"])
    return LoginTicket(ticket=passkeys.issue_ticket(db, uid, "setup"))


# ---------- signing in ----------
@router.post("/passkey/options")
def passkey_options(request: Request, db: Db) -> PasskeyOptions:
    """Start signing in with a passkey: the browser offers the ones made on this site."""
    _throttle(request, "passkey")
    return _options(passkeys.login_options, db, web_origin(request))


@router.post("/passkey")
def passkey_login(body: PasskeyAnswer, request: Request, db: Db) -> LoginTicket:
    """Sign in with the passkey the browser picked. Answers a ticket the web app swaps for a session; 404 for a passkey
    this server doesn't know (removed, or from before Lens was set up again)."""
    key = _throttle(request, "passkey")
    try:
        u = passkeys.login_finish(db, body.flow, body.credential)
    except passkeys.UnknownPasskey as e:
        # 404: the web app tells the browser to stop offering it (WebAuthn's signalUnknownCredential)
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        _hit(key)
        raise HTTPException(401, str(e)) from None
    return LoginTicket(ticket=passkeys.issue_ticket(db, u["id"], "passkey"))


@router.post("/ticket")
def redeem_ticket(body: TicketRequest, request: Request, db: Db, cfg: Cfg) -> TokenPair:
    """Swap a sign-in ticket (from a passkey or an outside account) for a session. Each ticket works once."""
    got = passkeys.redeem_ticket(db, body.ticket)
    if not got:
        raise HTTPException(401, "that sign-in has expired; try again")
    u, method = got
    refresh, sid = auth.start_session(db, cfg, u["id"], request.headers.get("user-agent", ""), _visitor(request))
    access, ttl = create_access_token(u["id"], sid)
    auth.audit(db, u, "login", f"account:{u['id']}", [method] if method else None)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl, user=auth.public(u))


# ---------- sign-in links ----------
def _link_account(db: Db, token: str) -> dict:
    u = passkeys.link_account(db, token)
    if not u:
        raise HTTPException(404, "this sign-in link is invalid or has expired; ask an admin for a new one")
    return u


@router.post("/signin-link/info")
def signin_link_info(body: SigninLinkToken, request: Request, db: Db) -> SigninLinkInfo:
    """Who a sign-in link is for (the page greets them)."""
    key = _throttle(request, "link")
    try:
        u = _link_account(db, body.token)
    except HTTPException:
        _hit(key)
        raise
    return SigninLinkInfo(email=u["email"], name=u.get("name"))


@router.post("/signin-link/options")
def signin_link_options(body: SigninLinkToken, request: Request, db: Db, cfg: Cfg) -> PasskeyOptions:
    _throttle(request, "link")
    return _options(passkeys.link_options, db, cfg, web_origin(request), body.token)


@router.post("/signin-link")
def signin_link(body: SigninLinkAnswer, request: Request, db: Db) -> LoginTicket:
    """Add the passkey the browser just made and sign in. The link stops working."""
    key = _throttle(request, "link")
    try:
        uid = passkeys.link_finish(db, body.token, body.flow, body.credential, body.name)
    except ValueError as e:
        _hit(key)
        raise HTTPException(400, str(e)) from None
    auth.audit(db, auth.get_account(db, uid), "passkey.add", f"account:{uid}", ["signin-link"])
    return LoginTicket(ticket=passkeys.issue_ticket(db, uid, "passkey"))


@router.post("/signin-link/use")
def signin_link_use(body: SigninLinkToken, request: Request, db: Db) -> LoginTicket:
    """Sign in with a sign-in link alone, without adding a passkey: for addresses browsers won't use passkeys on
    (plain http:// other than localhost). The link stops working. Audited as `login` with `signin-link`."""
    key = _throttle(request, "link")
    try:
        uid = passkeys.use_link(db, body.token)
    except ValueError as e:
        _hit(key)
        raise HTTPException(400, str(e)) from None
    return LoginTicket(ticket=passkeys.issue_ticket(db, uid, "signin-link"))


@router.post("/signin-link/lost")
def lost_passkey(body: ForgotPasswordRequest, request: Request, db: Db, cfg: Cfg, tasks: BackgroundTasks) -> Ok:
    """Email a sign-in link to this address, for adding a passkey. Answers the same whether or not it has an account."""
    # every request counts, per address asked for (it sends email), and per visitor where the server can tell them apart
    email = body.email.strip().lower()
    for key in (f"lost|{email}", _throttle(request, "lost")):
        if key and auth.throttled(key):
            raise HTTPException(429, "too many requests; try again in a few minutes")
        _hit(key)
    u = auth.find_account(db, email)
    if u and not u.get("disabled"):
        minutes = settings.PASSWORD_RESET_EXPIRE_MINUTES
        raw = passkeys.create_link(db, u["id"], hours=minutes / 60, replace=False)  # an admin's link keeps working
        tasks.add_task(send_signin_link_email, cfg, u["email"], u.get("name"), passkeys.link_url(raw, app_url(cfg)), minutes)
    return Ok()


# ---------- your passkeys ----------
@router.get("/passkeys")
def list_passkeys(user: CurrentUser, db: Db) -> list[Passkey]:
    return [Passkey(**p) for p in passkeys.list_for(db, user.id)]


def _signed_in(user: CurrentUser) -> None:
    if user.via != "access":
        raise HTTPException(403, "sign in to manage passkeys; API tokens and apps can't")


@router.post("/passkeys/options")
def add_passkey_options(user: Writer, request: Request, db: Db, cfg: Cfg) -> PasskeyOptions:
    """Start adding a passkey to your account (on the site you're on)."""
    _signed_in(user)
    return _options(passkeys.add_options, db, cfg, web_origin(request), {"id": user.id, "email": user.email, "name": user.name})


@router.post("/passkeys")
def add_passkey(body: PasskeyAnswer, user: Writer, db: Db) -> Passkey:
    """Add the passkey the browser just made. Audited as `passkey.add`."""
    _signed_in(user)
    try:
        pid = passkeys.add_finish(db, user.id, body.flow, body.credential, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "passkey.add", f"account:{user.id}")
    return Passkey(**next(p for p in passkeys.list_for(db, user.id) if p["id"] == pid))


@router.patch("/passkeys/{pid}")
def rename_passkey(pid: str, body: PasskeyRename, user: Writer, db: Db) -> Ok:
    _signed_in(user)
    if not passkeys.rename(db, user.id, pid, body.name):
        raise HTTPException(404, "not found")
    return Ok()


@router.delete("/passkeys/{pid}")
def remove_passkey(pid: str, user: Writer, request: Request, db: Db, cfg: Cfg) -> Ok:
    """Remove one of your passkeys; not the last one (you couldn't sign in). Audited as `passkey.remove`."""
    _signed_in(user)
    with domain_errors():
        try:
            here = passkeys.site(web_origin(request))[1]
        except ValueError:
            here = None
        if not passkeys.remove(db, user.id, pid, auth.passwords_on(cfg), here, external_login.count(db, user.id)):
            raise HTTPException(404, "not found")
    auth.audit(db, user.as_audit(), "passkey.remove", f"account:{user.id}")
    return Ok()


# ---------- admins ----------
@people.post("/users/{uid}/signin-link")
def make_signin_link(uid: int, user: AdminWriter, request: Request, db: Db) -> SigninLink:
    """A one-time link for this person to add a passkey and sign in (a new person, or one who lost theirs). It lasts
    three days; making another stops the last one. Share it privately. Audited as `user.signin_link`."""
    u = auth.active_account(db, uid)
    if not u:
        raise HTTPException(404, "not found (or disabled)")
    raw = passkeys.create_link(db, uid, by=user.email)
    auth.audit(db, user.as_audit(), "user.signin_link", f"account:{uid}")
    row = db.one("SELECT expires_at FROM $r", r=auth.R("signin_link", auth.sha(raw)))
    return SigninLink(
        url=passkeys.link_url(raw, web_origin(request) if request.headers.get("x-forwarded-host") else None), expires_at=row["expires_at"]
    )


@people.delete("/users/{uid}/passkeys")
def drop_passkeys(uid: int, user: AdminWriter, db: Db, lose_vaults: bool = False) -> Ok:
    """Remove all of this person's passkeys and end their sessions (a lost or stolen device). Send them a sign-in link
    to add a new one. Refused (409) when they are the only way into a vault, unless lose_vaults=true. Audited as
    `user.passkeys_remove`."""
    if uid == user.id:
        raise HTTPException(400, "remove your own passkeys one by one in your account")
    if not auth.get_account(db, uid):
        raise HTTPException(404, "not found")
    try:
        n = passkeys.remove_all(db, uid, lose_vaults)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "user.passkeys_remove", f"account:{uid}", [str(n)])
    return Ok()
