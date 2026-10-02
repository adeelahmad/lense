"""OAuth 2.1 for API and MCP clients: Lens issues the tokens itself (docs/authentication.md#oauth).

An app registers (``POST /oauth/register``), sends the person to the web app's consent page (``/oauth/authorize``,
which asks ``GET``/``POST /oauth/authorize`` here with the person's own session), swaps the code for tokens
(``POST /oauth/token``) and renews them there. People see the apps they gave access to and take it away
(``/oauth/grants``); an app can hand its token back (``POST /oauth/revoke``). ``/.well-known/…`` says where all of this
is, as MCP clients expect.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import time
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import Cfg, CurrentUser, Db, client_ip, domain_errors, visitor_address
from app.config import settings
from app.domain import auth, oauth
from app.schemas.common import Ok
from app.schemas.oauth import (
    ClientRegistered,
    ClientRegistration,
    Consent,
    ConsentAnswer,
    ConsentResult,
    OAuthGrant,
    OAuthTokens,
)

router = APIRouter(prefix="/oauth", tags=["oauth"])
well_known = APIRouter(include_in_schema=False, tags=["oauth-discovery"])

API = "/api/v1/oauth"
HOST = re.compile(r"[A-Za-z0-9.\-]+(:\d+)?|\[[0-9A-Fa-f:.]+\](:\d+)?")
RESOURCE = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
MCP, MCP_SCOPE = "/mcp", "read"  # the MCP server (app/api/mcp.py), and the scope its tools need


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def _from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    trusted = request.app.state.settings.current()["server"].get("trusted_proxies") or ()
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(t, strict=False) for t in trusted)


def public_base(request: Request) -> str:
    """The address clients reach Lens at. Through the web app, that's the web app's: what it says in
    X-Forwarded-Host when it's a trusted proxy (server.trusted_proxies), else FRONTEND_URL, so nobody who can reach
    the API can make it name another host. Reached directly, it's this server's (the Host header was checked)."""
    h = request.headers
    host = _first(h.get("x-forwarded-host"))
    if not host:
        return str(request.base_url).rstrip("/")
    proto = _first(h.get("x-forwarded-proto")) or request.url.scheme
    if _from_trusted_proxy(request) and HOST.fullmatch(host) and proto in ("http", "https"):
        return f"{proto}://{host}"
    return settings.FRONTEND_URL.rstrip("/")


def web_app_base(request: Request) -> str:
    """Where people open the web app's pages: this address when the request came through it, else FRONTEND_URL."""
    through_web_app = bool(_first(request.headers.get("x-forwarded-host")))
    return public_base(request) if through_web_app else settings.FRONTEND_URL.rstrip("/")


def _consent_page(request: Request) -> str:
    return web_app_base(request) + "/oauth/authorize"


def _discovery(doc: dict[str, Any]) -> JSONResponse:
    # no-store: the addresses depend on how the request arrived, so no cache may hand them to someone else
    return JSONResponse(doc, headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})


def _oauth_error(e: oauth.OAuthError) -> JSONResponse:
    headers = {**NO_STORE, **({"WWW-Authenticate": "Basic"} if e.status == 401 else {})}
    return JSONResponse({"error": e.error, "error_description": e.description}, status_code=e.status, headers=headers)


def _client_credentials(request: Request, client_id: str | None, client_secret: str | None) -> tuple[str | None, str | None]:
    """The app's id and secret, from the form or from HTTP Basic (client_secret_basic)."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            cid, _, secret = base64.b64decode(h[6:].strip(), validate=True).decode().partition(":")
            return cid, secret
        except (binascii.Error, UnicodeDecodeError):
            raise oauth.OAuthError("invalid_client", "the Authorization header isn't Basic credentials", 401) from None
    return client_id, client_secret


async def bearer_challenge(request: Request, exc: StarletteHTTPException) -> Response:
    """401s that ask for a bearer token say where to find out how to get one (RFC 9728 §5.1), as MCP clients expect."""
    if exc.status_code == 401 and (exc.headers or {}).get("WWW-Authenticate") == "Bearer":
        # the MCP server is a resource of its own (/mcp), so clients that check what the metadata names see their URL;
        # its tools only read, so that's all it asks for
        mcp = request.url.path == MCP
        meta = f"{public_base(request)}/.well-known/oauth-protected-resource" + (MCP if mcp else "")
        challenge = f'Bearer resource_metadata="{meta}"' + (f', scope="{MCP_SCOPE}"' if mcp else "")
        exc = StarletteHTTPException(401, exc.detail, headers={**(exc.headers or {}), "WWW-Authenticate": challenge})
    return await http_exception_handler(request, exc)


@well_known.get("/.well-known/oauth-authorization-server")
@well_known.get("/.well-known/oauth-authorization-server/{rest:path}")
def authorization_server(request: Request) -> JSONResponse:
    """Authorization server metadata (RFC 8414)."""
    base = public_base(request)
    doc = {
        "issuer": base,
        "authorization_endpoint": _consent_page(request),
        "token_endpoint": f"{base}{API}/token",
        "registration_endpoint": f"{base}{API}/register",
        "revocation_endpoint": f"{base}{API}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": list(oauth.AUTH_METHODS),
        "revocation_endpoint_auth_methods_supported": list(oauth.AUTH_METHODS),
        "scopes_supported": list(oauth.SCOPES),
    }
    return _discovery(doc)


@well_known.get("/.well-known/oauth-protected-resource")
@well_known.get("/.well-known/oauth-protected-resource/{rest:path}")
def protected_resource(request: Request, rest: str = "") -> JSONResponse:
    """Protected resource metadata (RFC 9728): who issues the tokens this server takes."""
    base = public_base(request)
    if rest and not RESOURCE.fullmatch(rest):
        raise HTTPException(404, "not found")
    doc = {
        "resource": f"{base}/{rest}" if rest else base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [MCP_SCOPE] if f"/{rest}" == MCP else list(oauth.SCOPES),
        "resource_name": settings.PROJECT_NAME,
    }
    return _discovery(doc)


@router.post("/register", status_code=201, response_model=ClientRegistered, responses={400: {"description": "OAuth error"}})
def register(body: ClientRegistration, request: Request, db: Db) -> Any:
    """An app registers itself (RFC 7591), without signing in: it gets a `client_id`, and a `client_secret` if it
    asked for one. It can do nothing until someone gives it access. Audited as `oauth.client.register`."""
    key = f"oauth-register|{visitor_address(request) or client_ip(request)}"
    if auth.throttled(key):
        raise HTTPException(429, "too many apps registered from this address; try again in a few minutes")
    try:
        c = oauth.register_client(db, body.client_name, body.redirect_uris, body.client_uri, body.token_endpoint_auth_method)
    except oauth.OAuthError as e:
        return _oauth_error(e)
    auth.hit(key)
    auth.audit(
        db, None, "oauth.client.register", f"oauth_client:{c['client_id']}", {"name": c["name"], "redirect_uris": c["redirect_uris"]}
    )
    return JSONResponse(
        ClientRegistered(
            client_id=c["client_id"],
            client_secret=c.get("client_secret"),
            client_id_issued_at=int(time.time()),
            client_name=c["name"],
            client_uri=c.get("uri"),
            redirect_uris=c["redirect_uris"],
            token_endpoint_auth_method=c["auth_method"],
        ).model_dump(exclude_none=True),
        status_code=201,
        headers=NO_STORE,
    )


def _person(user: CurrentUser) -> None:
    if user.via != "access":
        raise HTTPException(403, "sign in to give an app access; API tokens and apps can't")


@router.get("/authorize")
def consent(
    user: CurrentUser,
    db: Db,
    client_id: str = Query(),
    redirect_uri: str = Query(),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query(""),
    response_type: str = Query("code"),
    scope: str | None = Query(None),
) -> Consent:
    """What the consent page shows for an app's request: the app, where it takes you back to and what it asks for.
    400 when the request can't be answered (an unknown app, an address it didn't register, no PKCE challenge)."""
    _person(user)
    with domain_errors():
        return Consent(
            **oauth.authorization(db, user.id, client_id, redirect_uri, response_type, code_challenge, code_challenge_method, scope)
        )


@router.post("/authorize")
def answer(body: ConsentAnswer, user: CurrentUser, db: Db) -> ConsentResult:
    """Your answer to an app's request. Yes gives it a one-time code (five minutes) at its redirect address, which
    it swaps for tokens that act as you, with your roles; no sends it `error=access_denied`. Audited as
    `oauth.grant`."""
    _person(user)
    with domain_errors():
        info = oauth.authorization(
            db, user.id, body.client_id, body.redirect_uri, body.response_type, body.code_challenge, body.code_challenge_method, body.scope
        )
    if not body.approve:
        return ConsentResult(redirect_to=oauth.with_params(body.redirect_uri, error="access_denied", state=body.state))
    code, scope = oauth.approve(db, user.id, info, body.code_challenge, body.grant, body.resource)
    auth.audit(db, user.as_audit(), "oauth.grant", f"oauth_client:{body.client_id}", {"name": info["client"]["name"], "scope": scope})
    return ConsentResult(redirect_to=oauth.with_params(body.redirect_uri, code=code, state=body.state))


@router.post("/token", response_model=OAuthTokens, responses={400: {"description": "OAuth error"}, 401: {"description": "OAuth error"}})
def token(
    request: Request,
    db: Db,
    cfg: Cfg,
    grant_type: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
) -> Any:
    """The app swaps its code (`grant_type=authorization_code`, with `code_verifier` and the same `redirect_uri`)
    or its refresh token (`grant_type=refresh_token`) for an access token and a new refresh token. Form-encoded, as
    OAuth has it; errors are `{error, error_description}`."""
    try:
        cid, secret = _client_credentials(request, client_id, client_secret)
        if grant_type == "authorization_code":
            tokens, _ = oauth.exchange_code(db, cfg, cid, secret, code, redirect_uri, code_verifier)
        elif grant_type == "refresh_token":
            tokens = oauth.refresh(db, cfg, cid, secret, refresh_token)
        else:
            raise oauth.OAuthError("unsupported_grant_type", "grant_type is authorization_code or refresh_token")
    except oauth.OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(tokens, headers=NO_STORE)


@router.post("/revoke", response_model=Ok, responses={401: {"description": "OAuth error"}})
def revoke(
    request: Request,
    db: Db,
    token: Annotated[str, Form()],
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
) -> Any:
    """An app hands back a token, access or refresh (RFC 7009): the access it was given ends. Answers the same
    whether or not the token was known. Audited as `oauth.revoke`."""
    try:
        cid, secret = _client_credentials(request, client_id, client_secret)
        g = oauth.revoke_token(db, cid, secret, token)
    except oauth.OAuthError as e:
        return _oauth_error(e)
    if g:
        acct = auth.active_account(db, g["account"]) or {"id": g["account"]}
        auth.audit(db, acct, "oauth.revoke", f"oauth_client:{g['client']}", {"name": g["name"], "by": "app"})
    return Ok()


@router.get("/grants")
def list_grants(user: CurrentUser, db: Db) -> list[OAuthGrant]:
    """The apps you gave access to, the latest first."""
    return [OAuthGrant(**g) for g in oauth.grants(db, user.id)]


@router.delete("/grants/{grant_id}")
def revoke_grant(grant_id: int, user: CurrentUser, db: Db) -> Ok:
    """Take an app's access away: its tokens stop working now. Audited as `oauth.revoke`."""
    _person(user)
    g = oauth.revoke_grant(db, user.id, grant_id)
    if not g:
        raise HTTPException(404, "not found")
    auth.audit(db, user.as_audit(), "oauth.revoke", f"oauth_client:{g['client']}", {"name": g["name"]})
    return Ok()
