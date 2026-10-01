"""Signing in (for NextAuth and API clients), first-run setup, password reset and API tokens."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.api.deps import Cfg, CurrentUser, Db, Writer, client_ip, domain_errors
from app.config import settings
from app.core.security import create_access_token
from app.domain import auth, store
from app.email import send_reset_password_email
from app.schemas.auth import (
    ApiToken,
    ApiTokenCreate,
    ApiTokenCreated,
    AuthStatus,
    ForgotPasswordRequest,
    LoginRequest,
    Me,
    MeUpdate,
    PasswordChange,
    RefreshRequest,
    ResetPasswordRequest,
    SetupRequest,
    TokenPair,
    UserPublic,
)
from app.schemas.common import Ok

router = APIRouter(prefix="/auth", tags=["auth"])
tokens = APIRouter(prefix="/tokens", tags=["tokens"])


def _pair(db: Db, cfg: Cfg, request: Request, user: dict) -> TokenPair:
    refresh, sid = auth.start_session(db, cfg, user["id"], request.headers.get("user-agent", ""), client_ip(request))
    access, ttl = create_access_token(user["id"], sid)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl, user=auth.public(user))


@router.get("/status")
def status(request: Request, db: Db) -> AuthStatus:
    """Whether the archive still needs its first admin (the sign-in page shows the setup form instead)."""
    return AuthStatus(setup_required=request.app.state.archive.setup_code is not None and auth.account_count(db) == 0)


@router.post("/setup")
def setup(body: SetupRequest, request: Request, db: Db, cfg: Cfg) -> TokenPair:
    """Create the first admin with the one-time code printed in the server log."""
    archive = request.app.state.archive
    code = archive.setup_code
    if not code or auth.account_count(db) or not secrets.compare_digest(body.code, code):
        raise HTTPException(403, "setup is closed or the code is wrong")
    with domain_errors():
        uid = auth.create_account(db, body.email, body.password, body.name, admin=True)
    archive.setup_code = None
    auth.audit(db, {"id": uid, "email": body.email}, "setup")
    return _pair(db, cfg, request, auth.get_account(db, uid))


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Db, cfg: Cfg) -> TokenPair:
    key = f"{body.email.strip().lower()}|{client_ip(request)}"
    if auth.throttled(key):
        raise HTTPException(429, "too many attempts; try again in a few minutes")
    user = auth.login(db, body.email, body.password, key)
    if not user:
        raise HTTPException(401, "wrong email or password")
    return _pair(db, cfg, request, user)


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Db, cfg: Cfg) -> TokenPair:
    """Swap a refresh token for a new pair. Each refresh token works once."""
    got = auth.refresh_session(db, cfg, body.refresh_token)
    if not got:
        raise HTTPException(401, "the session has ended; sign in again")
    user, new_refresh, sid = got
    access, ttl = create_access_token(user["id"], sid)
    return TokenPair(access_token=access, refresh_token=new_refresh, expires_in=ttl, user=user)


@router.post("/logout")
def logout(body: RefreshRequest, db: Db) -> Ok:
    auth.end_session(db, body.refresh_token)
    return Ok()


@router.get("/me")
def me(user: CurrentUser, db: Db) -> Me:
    names = store.space_names(db)
    roles: dict[str, Any] = {names.get(k, str(k)): v for k, v in user.roles.items()}
    return Me(user=UserPublic(**(auth.active_account(db, user.id) or {})), roles=roles, via=user.via, scope=user.scope)


@router.patch("/me")
def update_me(body: MeUpdate, user: Writer, db: Db) -> Me:
    """Change your own name."""
    with domain_errors():
        auth.rename_account(db, user.id, body.name)
    return me(user, db)


@router.post("/password")
def change_password(body: PasswordChange, user: CurrentUser, db: Db) -> Ok:
    """Change your own password, with your current one (signed in; not with an API token). Your other sessions end
    and this one stays; API tokens keep working. Audited as `password.change`."""
    if user.via != "access":
        raise HTTPException(403, "sign in to change your password; API tokens can't")
    key = f"password|{user.id}"
    if auth.throttled(key):
        raise HTTPException(429, "too many attempts; try again in a few minutes")
    with domain_errors():
        auth.change_password(db, user.id, body.current_password, body.new_password, user.sid, key)
    auth.audit(db, user.as_audit(), "password.change", f"account:{user.id}")
    return Ok()


@router.post("/password/forgot")
def forgot_password(body: ForgotPasswordRequest, db: Db, tasks: BackgroundTasks) -> Ok:
    """Email a reset link. Answers the same whether or not the address has an account."""
    raw, user = auth.start_reset(db, body.email, settings.PASSWORD_RESET_EXPIRE_MINUTES)
    if raw and user:
        tasks.add_task(send_reset_password_email, user["email"], user.get("name"), raw)
    return Ok()


@router.post("/password/reset")
def reset_password(body: ResetPasswordRequest, db: Db) -> Ok:
    with domain_errors():
        uid = auth.finish_reset(db, body.token, body.password)
    auth.audit(db, {"id": uid}, "password.reset", f"account:{uid}")
    return Ok()


@tokens.get("")
def list_tokens(user: CurrentUser, db: Db) -> list[ApiToken]:
    return auth.list_tokens(db, user.id)


@tokens.post("")
def create_token(body: ApiTokenCreate, user: Writer, db: Db) -> ApiTokenCreated:
    if user.via != "access":
        raise HTTPException(403, "create tokens while signed in")
    with domain_errors():
        tid, raw = auth.create_token(db, user.id, body.name, body.scope, body.days)
    auth.audit(db, user.as_audit(), "token.create", f"api_token:{tid}")
    return ApiTokenCreated(id=tid, token=raw)


@tokens.delete("/{token_id}")
def revoke_token(token_id: int, user: Writer, db: Db) -> Ok:
    auth.revoke_token(db, user.id, token_id)
    return Ok()
