"""Request dependencies: the database, the effective configuration, who is calling and what they may touch.

Who is calling comes from the ``Authorization: Bearer`` header, which carries either an access token issued at sign-in
(the web app, through NextAuth) or an API token (``la_...``). Access tokens can write; API tokens are read-only unless
created with the write scope. No cookies are involved, so there is nothing for CSRF to ride on.

Access is per namespace. A namespace you have no role in behaves as if it didn't exist (404); one you can read but not
change says so (403). Admins own every namespace.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request

from app.core import security
from app.domain import auth, store
from app.domain.store import DB

Config = dict[str, Any]


def get_db(request: Request) -> DB:
    return request.app.state.db


def get_cfg(request: Request) -> Config:
    return request.app.state.settings.current()


Db = Annotated[DB, Depends(get_db)]
Cfg = Annotated[Config, Depends(get_cfg)]


@dataclass
class Principal:
    id: int
    email: str
    name: str | None
    admin: bool
    via: Literal["access", "token"]  # a signed-in session, or an API token
    scope: Literal["read", "write"] = "write"
    sid: str | None = None
    roles: dict[int, str] = field(default_factory=dict)

    @property
    def can_write(self) -> bool:
        return self.via == "access" or self.scope == "write"

    def as_audit(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email}


def _principal(request: Request, db: DB) -> Principal | None:
    if hasattr(request.state, "principal"):
        return request.state.principal
    p: Principal | None = None
    h = request.headers.get("authorization", "")
    raw = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if raw.startswith("la_"):
        u = auth.token_account(db, raw)
        if u:
            p = Principal(
                u["id"], u["email"], u.get("name"), bool(u.get("admin")), "token", "write" if u.get("scope") == "write" else "read"
            )
    elif raw:
        claims = security.decode_access_token(raw)
        u = auth.active_account(db, claims.account) if claims and auth.session_active(db, claims.sid) else None
        if u and claims:
            p = Principal(u["id"], u["email"], u.get("name"), bool(u.get("admin")), "access", "write", claims.sid)
    if p:
        p.roles = auth.roles(db, {"id": p.id, "admin": p.admin})
    request.state.principal = p
    return p


def optional_user(request: Request, db: Db) -> Principal | None:
    return _principal(request, db)


def current_user(request: Request, db: Db) -> Principal:
    p = _principal(request, db)
    if not p:
        raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})
    return p


def writer(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    if not user.can_write:
        raise HTTPException(403, "this API token is read-only")
    return user


def admin_reader(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    if not user.admin:
        raise HTTPException(403, "admins only")
    return user


def admin_writer(user: Annotated[Principal, Depends(writer)]) -> Principal:
    if not user.admin:
        raise HTTPException(403, "admins only")
    return user


OptionalUser = Annotated[Principal | None, Depends(optional_user)]
CurrentUser = Annotated[Principal, Depends(current_user)]
Writer = Annotated[Principal, Depends(writer)]
AdminReader = Annotated[Principal, Depends(admin_reader)]
AdminWriter = Annotated[Principal, Depends(admin_writer)]


class Access:
    """Namespace and recording checks for one request."""

    def __init__(self, request: Request, db: DB, user: Principal | None):
        self.request, self.db, self.user = request, db, user

    @property
    def roles(self) -> dict[int, str]:
        return self.user.roles if self.user else {}

    def spaces(self) -> list[int]:
        """Namespaces this person can read."""
        return sorted(self.roles)

    def readable(self) -> set[int] | None:
        """None for admins (everything), else the namespaces this person can read."""
        return None if self.user and self.user.admin else set(self.roles)

    def editable(self) -> list[int]:
        return sorted(s for s in self.roles if auth.allows(self.roles, s, "editor"))

    def need(self, sid: int, role: str = "viewer") -> None:
        if not self.user:
            raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})
        if not auth.allows(self.roles, sid, "viewer"):
            raise HTTPException(404, "not found")
        if not auth.allows(self.roles, sid, role):
            raise HTTPException(403, f"needs {role} access to this namespace")

    def nsid(self, name: str) -> int:
        try:
            return store.ns_id(self.db, name, create=False)
        except KeyError:
            raise HTTPException(404, "not found") from None

    def namespace(self, name: str, role: str = "viewer") -> int:
        sid = self.nsid(name)
        self.need(sid, role)
        return sid

    def recording(self, rid: int, role: str = "viewer", share: str | None = None) -> dict[str, Any]:
        """The recording row, if this request may see it: a role in its namespace, a share link, or a signed link."""
        rec = self.db.one("SELECT * FROM $r", r=store.R("recording", rid))
        if not rec:
            raise HTTPException(404, "not found")
        if role == "viewer" and (self.signed() or (share and auth.share_ok(self.db, share, rid))):
            return rec
        self.need(rec["space"], role)
        return rec

    def permitted(self, rec: dict[str, Any]) -> bool:
        """Permission on a recording (docs/access.md): a role in its namespace. Admins have every role."""
        return auth.allows(self.roles, rec["space"])

    def signed(self) -> bool:
        q = self.request.query_params
        return security.verify_path(self.request.url.path, q.get("exp"), q.get("sig"))


def get_access(request: Request, db: Db, user: OptionalUser) -> Access:
    return Access(request, db, user)


Acl = Annotated[Access, Depends(get_access)]


@contextlib.contextmanager
def domain_errors() -> Iterator[None]:
    """Turn the domain layer's ValueError (bad input) into 400 and KeyError (unknown id) into 404."""
    try:
        yield
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "not found") from None


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
