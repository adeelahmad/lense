"""Request dependencies: the database, the effective configuration, who is calling and what they may touch.

Who is calling comes from the ``Authorization: Bearer`` header, which carries either an access token issued at sign-in
(the web app, through NextAuth), an API token (``la_...``) or the access token of an app the person gave access to
through OAuth (``lo_...``, app/domain/oauth.py). Access tokens can write; API tokens and apps are read-only unless
they have the write scope. No cookies are involved, so there is nothing for CSRF to ride on.

Access is per namespace. A namespace you have no role in behaves as if it didn't exist (404); one you can read but not
change says so (403). Admins own every namespace. A role on a collection adds to that for the recordings in it (and in
the collections inside it): someone without a role in the namespace sees just those, and an admin of a collection
acts as an owner of its recordings (docs/access.md#collection-roles).
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request

from app.core import security
from app.domain import access as acc
from app.domain import auth, hierarchy, ipgroups, oauth, store
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
    via: Literal["access", "token", "oauth"]  # a signed-in session, an API token, or an app given access (OAuth)
    scope: Literal["read", "write"] = "write"
    sid: str | None = None
    roles: dict[int, str] = field(default_factory=dict)
    collections: dict[int, dict[int, str]] = field(default_factory=dict)  # roles on collections: {space: {collection: role}}
    resource: str | None = None  # an app's token: the server it was given for (RFC 8707), when the app named one

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
    elif raw.startswith("lo_"):
        # with OAuth turned off, apps' tokens stop working at once (their grants stay, so turning it back on restores them)
        u = oauth.token_account(db, raw) if oauth.enabled(request.app.state.settings.current()) else None
        if u:
            p = Principal(u["id"], u["email"], u.get("name"), bool(u.get("admin")), "oauth", u["scope"], resource=u.get("resource"))
    elif raw:
        claims = security.decode_access_token(raw)
        u = auth.active_account(db, claims.account) if claims and auth.session_active(db, claims.sid) else None
        if u and claims:
            p = Principal(u["id"], u["email"], u.get("name"), bool(u.get("admin")), "access", "write", claims.sid)
    if p:
        p.roles = auth.roles(db, {"id": p.id, "admin": p.admin})
        p.collections = {} if p.admin else hierarchy.roles_of(db, p.id)
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


def _admin(user: Principal) -> Principal:
    """Administration (people, settings, the audit log) is for admins themselves, signed in or with their API key. An
    app an admin gave access to has the admin's roles in every namespace, not the administration."""
    if not user.admin:
        raise HTTPException(403, "admins only")
    if user.via == "oauth":
        raise HTTPException(403, "admins only: apps given access can't administer the archive")
    return user


def admin_reader(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    return _admin(user)


def admin_writer(user: Annotated[Principal, Depends(writer)]) -> Principal:
    return _admin(user)


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

    def collection_role(self, sid: int, cid: int | None) -> str | None:
        """The role this person was given on a collection, or on one it's inside (viewer, editor, admin); None without."""
        if not self.user or cid is None:
            return None
        return self.user.collections.get(sid, {}).get(cid)

    def rank_in(self, sid: int, cid: int | None) -> int:
        """What this person may do with a recording of collection `cid` in namespace `sid`: the higher of their
        namespace role and their role on the collection, as auth.ROLES ranks (an admin of a collection counts as an
        owner); 0 when they may not see it."""
        return max(auth.ROLES.get(self.roles.get(sid, ""), 0), hierarchy.ROLES.get(self.collection_role(sid, cid) or "", 0))

    def role_in(self, sid: int, cid: int | None) -> str | None:
        """The same as a role name (viewer, editor or owner), or None."""
        return {1: "viewer", 2: "editor", 3: "owner"}.get(self.rank_in(sid, cid))

    def need_in(self, sid: int, cid: int | None, role: str = "viewer") -> None:
        """Like need, for a recording of collection `cid`: a namespace role or a role on the collection will do."""
        if not self.user:
            raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})
        have = self.rank_in(sid, cid)
        if not have:
            raise HTTPException(404, "not found")
        if have < auth.ROLES[role]:
            raise HTTPException(403, f"needs {role} access to this recording")

    def partial(self) -> dict[int, list[int]]:
        """The namespaces this person sees only some collections of (no role there, a role on collections in it):
        {space: [collection ids, with the ones inside them]}."""
        if not self.user:
            return {}
        return {sid: sorted(cols) for sid, cols in self.user.collections.items() if sid not in self.roles and cols}

    def scope(self, ns: str | None = None) -> tuple[list[int], dict[int, list[int]]]:
        """What a list of recordings may show: (the namespaces seen whole, {namespace: collections} for those seen in
        part), or just namespace `ns` (404 when this person sees nothing of it)."""
        if not ns:
            return self.spaces(), self.partial()
        sid = self.nsid(ns)
        if not self.user:
            raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})
        if auth.allows(self.roles, sid):
            return [sid], {}
        part = self.partial().get(sid)
        if not part:
            raise HTTPException(404, "not found")
        return [], {sid: part}

    def visible(self, sid: int) -> set[int] | None:
        """The collections of namespace `sid` this person sees: None for all of them (a role there), else those they
        have a role on (and the ones inside them); 404 when none."""
        if auth.allows(self.roles, sid):
            return None
        part = self.partial().get(sid)
        if not part:
            raise HTTPException(404, "not found")
        return set(part)

    def partial_recordings(self) -> set[int]:
        """The recordings this person sees in namespaces they see only some collections of."""
        return hierarchy.recordings_in(self.db, [c for cols in self.partial().values() for c in cols])

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
        """The recording row, if this request may see it: a role in its namespace or on its collection, a share link,
        or a signed link."""
        rec = self.db.one("SELECT * FROM $r", r=store.R("recording", rid))
        if not rec:
            raise HTTPException(404, "not found")
        if role == "viewer" and (self.signed() or (share and auth.share_ok(self.db, share, rid))):
            return rec
        self.need_in(rec["space"], rec.get("collection"), role)
        return rec

    def permitted(self, rid: int, space: int) -> bool:
        """Permission on a recording (docs/access.md): a role in its namespace (admins have every role), permission
        given on the recording, or an IP group the request's address is in."""
        return acc.permitted(self.db, self.roles, self.user.id if self.user else None, rid, space, self.network())

    def network(self) -> ipgroups.Network:
        """What the request's address opens (IP groups)."""
        return network(self.request, self.db)

    def who(self) -> acc.Who:
        """Who is asking, for the pages visitors see."""
        account = self.user.id if self.user else None
        return acc.Who(frozenset(self.roles), acc.granted(self.db, account), self.user is not None, self.network())

    def signed(self) -> bool:
        q = self.request.query_params
        return security.verify_path(self.request.url.path, q.get("exp"), q.get("sig"), full=q.get("full") == "1")

    def member(self, rec: dict[str, Any]) -> bool:
        """Whether this request is a member's: a role in the recording's namespace or on its collection, or a link
        the API signed for one (`full`). Visitors (anyone else: public pages, embeds, share links) aren't."""
        if self.user and self.rank_in(rec["space"], rec.get("collection")):
            return True
        q = self.request.query_params
        return q.get("full") == "1" and self.signed()


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
    """The visitor's address for throttles and the session's record: through the trusted proxies, else the peer's."""
    addr = visitor_address(request)
    return str(addr) if addr else (request.client.host if request.client else "")


_PROXY_HOSTS: dict[str, tuple[float, tuple[str, ...]]] = {}


def _proxy_hosts() -> tuple[str, ...]:
    """The addresses of the hosts named in LENS_TRUSTED_PROXY_HOSTS (the web app's container, `frontend` in the
    Docker Compose files, whose address changes when it's recreated), looked up at most every 30 seconds."""
    import os
    import socket
    import time

    names = [n.strip() for n in os.environ.get("LENS_TRUSTED_PROXY_HOSTS", "").split(",") if n.strip()]
    out: list[str] = []
    for name in names:
        hit = _PROXY_HOSTS.get(name)
        if not hit or time.monotonic() - hit[0] > 30:
            try:
                found = tuple(sorted({i[4][0] for i in socket.getaddrinfo(name, None)}))
            except OSError:
                found = ()
            hit = _PROXY_HOSTS[name] = (time.monotonic(), found)
        out += hit[1]
    return tuple(out)


def trusted_proxies(request: Request) -> tuple[str, ...]:
    """server.trusted_proxies, and the web app's own container (LENS_TRUSTED_PROXY_HOSTS)."""
    return tuple(request.app.state.settings.current()["server"].get("trusted_proxies") or ()) + _proxy_hosts()


def visitor_address(request: Request) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a visitor comes from, for IP groups and throttles: the peer, or what the trusted proxies
    (server.trusted_proxies, LENS_TRUSTED_PROXY_HOSTS) report in X-Forwarded-For. None when the server can't vouch
    for one (ipgroups.client_address())."""
    c = request.client
    trusted = trusted_proxies(request)
    forwarded = ", ".join(request.headers.getlist("x-forwarded-for"))
    return ipgroups.client_address(c.host if c else None, c.port if c else None, forwarded, trusted)


def network(request: Request, db: DB) -> ipgroups.Network:
    """What the request's address opens (IP groups), worked out once per request."""
    if not hasattr(request.state, "network"):
        request.state.network = ipgroups.of(db, visitor_address(request))
    return request.state.network


def _web_hosts(cfg: Config) -> set[str]:
    """Host names the web app is known to be served at: FRONTEND_URL's, localhost and server.allowed_hosts."""
    from urllib.parse import urlsplit

    from app.config import settings as env

    hosts = {h.lower() for h in cfg["server"].get("allowed_hosts") or () if h != "*"}
    hosts |= {(urlsplit(env.FRONTEND_URL).hostname or "").lower(), "localhost", "127.0.0.1"}
    return hosts - {""}


def _tunnel_hosts(request: Request, cfg: Config) -> set[str]:
    """The Cloudflare tunnel's public host names (Settings › Remote access): the fixed one, and the one it serves now."""
    from app.domain import tunnel

    hosts = {tunnel.hostname(cfg)}
    if (cfg.get("tunnel") or {}).get("mode", "off") != "off":
        try:
            hosts.add(tunnel.public_host(request.app.state.db))
        except Exception:  # noqa: BLE001 - the database is unreachable; the fixed name still counts
            pass
    return hosts - {""}


def web_origin(request: Request) -> str:
    """The web app's address the browser is on (for passkeys and sign-in redirects). Through the web app that's what
    it says in X-Forwarded-Host, when the host is one Lens is served at (or the web app is a trusted proxy); otherwise
    FRONTEND_URL. Called directly, the API's own address."""
    from app.config import settings as env
    from app.core.middleware import host_name

    h = request.headers
    host = (h.get("x-forwarded-host") or "").split(",")[0].strip()
    if not host:
        return str(request.base_url).rstrip("/")
    proto = (h.get("x-forwarded-proto") or "").split(",")[0].strip() or request.url.scheme
    cfg = request.app.state.settings.current()
    peer = request.client.host if request.client else ""
    try:
        trusted = any(
            ipaddress.ip_address(peer) in ipaddress.ip_network(t, strict=False) for t in cfg["server"].get("trusted_proxies") or ()
        )
    except ValueError:
        trusted = False
    shaped = re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?|\[[0-9A-Fa-f:.]+\](:\d+)?", host)
    if shaped and host.lower() in _tunnel_hosts(request, cfg):
        return f"https://{host.lower()}"  # Cloudflare serves it over https; cloudflared reaches the web app over http
    if shaped and proto in ("http", "https") and (trusted or host_name(host) in _web_hosts(cfg)):
        return f"{proto}://{host}"
    return env.FRONTEND_URL.rstrip("/")
