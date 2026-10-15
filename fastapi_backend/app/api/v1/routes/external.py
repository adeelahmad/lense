"""Signing in with an outside account (Google, GitHub, Microsoft, OpenID Connect): the providers admins set up, the
round trip through the provider, and the outside accounts connected to yours (app/domain/external_login.py).

The browser asks for the provider's page through the web app (so Lens knows the address to come back to) and gets a
cookie tying the sign-in to this browser. The provider sends it back to the callback, which ends on the web app's
/external-signin page with a one-time ticket in the URL fragment; the web app's server swaps it for a session at
POST /auth/ticket, as for passkeys.
"""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors, web_origin
from app.domain import auth, external_login, passkeys
from app.schemas.auth import (
    ExternalIdentity,
    ExternalProvider,
    ExternalProviderAdmin,
    ExternalProviderSave,
    ExternalRedirect,
    ExternalStart,
)
from app.schemas.common import Ok

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "lens_external"


def _cookie(response: Response, origin: str, value: str) -> None:
    response.set_cookie(
        COOKIE,
        value,
        max_age=external_login.FLOW_MINUTES * 60,
        path="/api/v1/auth/external",
        httponly=True,
        secure=origin.startswith("https://"),
        samesite="lax",
    )


def _redirect(response: Response, request: Request, db: Db, cfg: Cfg, key: str, **kw) -> ExternalRedirect:
    origin = web_origin(request)
    try:
        url, browser = external_login.start(db, cfg, key, origin, **kw)
    except KeyError:
        raise HTTPException(404, "no such sign-in provider") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _cookie(response, origin, browser)
    return ExternalRedirect(url=url)


@router.get("/external")
def external_providers(db: Db) -> list[ExternalProvider]:
    """The outside accounts people can sign in with here (for the sign-in page)."""
    return [ExternalProvider(**p) for p in external_login.providers(db)]


@router.post("/external/{key}/start")
def external_start(key: str, body: ExternalStart, request: Request, response: Response, db: Db, cfg: Cfg) -> ExternalRedirect:
    """Start signing in with an outside account: open the returned address in this browser."""
    return _redirect(response, request, db, cfg, key, next_path=body.next)


@router.post("/external/{key}/connect")
def external_connect(key: str, user: Writer, request: Request, response: Response, db: Db, cfg: Cfg) -> ExternalRedirect:
    """Connect an outside account to yours, so you can sign in with it: open the returned address in this browser."""
    if user.via != "access":
        raise HTTPException(403, "sign in to connect accounts; API tokens and apps can't")
    return _redirect(response, request, db, cfg, key, mode="connect", account=user.id, next_path="/account")


def _back(origin: str, **fragment: str) -> RedirectResponse:
    r = RedirectResponse(f"{origin}/external-signin#{urllib.parse.urlencode(fragment)}", status_code=303)
    r.delete_cookie(COOKIE, path="/api/v1/auth/external")
    return r


@router.get("/external/{key}/callback", include_in_schema=False)
def external_callback(
    key: str,
    request: Request,
    db: Db,
    cfg: Cfg,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse:
    """Where the provider sends the browser back. Ends on the web app's /external-signin page."""
    try:
        flow = external_login.claim(db, state, request.cookies.get(COOKIE))
    except ValueError as e:
        return _back(web_origin(request), error=str(e), next="/login")
    origin, connect = flow["origin"], flow.get("mode") == "connect"
    away = "/account" if connect else "/login"
    if error or not code:
        why = "you cancelled" if error == "access_denied" else (error_description or error or "no code came back")
        return _back(origin, error=f"signing in didn't finish: {why}", next=away)
    try:
        u = external_login.finish(db, cfg, key, flow, code)
    except KeyError:
        return _back(origin, error="this sign-in provider was removed", next=away)
    except ValueError as e:
        return _back(origin, error=str(e), next=away)
    label = external_login.provider(db, key).get("label") or key
    if connect:
        auth.audit(db, u, "external.connect", f"account:{u['id']}", [key])
        return _back(origin, connected=label, next="/account")
    return _back(origin, ticket=passkeys.issue_ticket(db, u["id"], f"external:{key}"), next=flow.get("next") or "/")


# ---------- your outside accounts ----------
def _signed_in(user: CurrentUser) -> None:
    if user.via != "access":
        raise HTTPException(403, "sign in to manage connected accounts; API tokens and apps can't")


@router.get("/identities")
def list_identities(user: CurrentUser, db: Db) -> list[ExternalIdentity]:
    """The outside accounts you can sign in with."""
    return [ExternalIdentity(**i) for i in external_login.identities(db, user.id)]


@router.delete("/identities/{iid}")
def disconnect_identity(iid: str, user: Writer, db: Db, cfg: Cfg) -> Ok:
    """Disconnect an outside account; not your only way to sign in. Audited as `external.disconnect`."""
    _signed_in(user)
    others = passkeys.count(db, user.id) > 0 or (auth.passwords_on(cfg) and passkeys.has_password(db, user.id))
    with domain_errors():
        if not external_login.disconnect(db, user.id, iid, others):
            raise HTTPException(404, "not found")
    auth.audit(db, user.as_audit(), "external.disconnect", f"account:{user.id}")
    return Ok()


# ---------- providers (admins) ----------
def _admin_view(db: Db, key: str) -> ExternalProviderAdmin:
    p = next(p for p in external_login.providers(db, admin=True) if p["key"] == key)
    return ExternalProviderAdmin(**p, callback_path=external_login.callback_path(key))


@router.get("/providers")
def list_login_providers(user: AdminWriter, db: Db) -> list[ExternalProviderAdmin]:
    """Every sign-in provider and its settings (never the client secret)."""
    return [
        ExternalProviderAdmin(**p, callback_path=external_login.callback_path(p["key"])) for p in external_login.providers(db, admin=True)
    ]


@router.post("/providers")
def add_login_provider(body: ExternalProviderSave, user: AdminWriter, db: Db, cfg: Cfg) -> ExternalProviderAdmin:
    """Add a sign-in provider. Audited as `login_provider.add`."""
    with domain_errors():
        key = external_login.save_provider(db, cfg, None, body.model_dump(exclude_unset=True))
    auth.audit(db, user.as_audit(), "login_provider.add", f"login_provider:{key}")
    return _admin_view(db, key)


@router.patch("/providers/{key}")
def change_login_provider(key: str, body: ExternalProviderSave, user: AdminWriter, db: Db, cfg: Cfg) -> ExternalProviderAdmin:
    """Change a sign-in provider; leave the secret out to keep it. Audited as `login_provider.change`."""
    with domain_errors():
        external_login.save_provider(db, cfg, key, body.model_dump(exclude_unset=True, exclude={"kind"}))
    auth.audit(db, user.as_audit(), "login_provider.change", f"login_provider:{key}")
    return _admin_view(db, key)


@router.delete("/providers/{key}")
def remove_login_provider(key: str, user: AdminWriter, db: Db) -> Ok:
    """Remove a sign-in provider and the accounts connected through it. Audited as `login_provider.remove`."""
    with domain_errors():
        external_login.delete_provider(db, key)
    auth.audit(db, user.as_audit(), "login_provider.remove", f"login_provider:{key}")
    return Ok()
