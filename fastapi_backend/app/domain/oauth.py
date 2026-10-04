"""Lens as an OAuth 2.1 authorization server, for API and MCP clients (docs/authentication.md).

An app registers itself (RFC 7591), sends the person to the consent page with a PKCE challenge (S256, required), and
swaps the code it gets back for an access token and a refresh token. The tokens act as the person who consented, with
their roles, read only or read and write. Like API keys they are random and only their hashes are kept, so revoking a
grant works at once. Refresh tokens rotate; one that comes back after it was swapped ends the grant (it was probably
copied). No account is created here: people sign in with the one they have.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import urllib.parse

from . import auth, store

R = store.R
SCOPES = ("read", "write")
CODE_SECONDS = 300
CODE_REMEMBERED_SECONDS = 86400  # a spent code is remembered this long, so that its coming back ends what it gave
REUSE_GRACE_SECONDS = auth.REUSE_GRACE_SECONDS
UNUSED_CLIENT_DAYS = 7  # a registered app nobody gave access to is forgotten after this
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")
# schemes an app's own redirect address may never use: they'd run or read things in the browser that sends people there
BAD_SCHEMES = {
    *("javascript", "data", "vbscript", "file", "blob", "about", "view-source", "ws", "wss", "ftp", "sftp", "ssh", "smb", "nfs"),
    *("ldap", "ldaps", "telnet", "mailto", "tel", "sms", "intent", "chrome", "edge", "resource", "jar", "search-ms"),
}
BAD_SCHEME_PREFIXES = ("ms-", "x-apple", "itms")  # the operating system's own handlers
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")
PKCE = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class OAuthError(Exception):
    """An error in OAuth's own words (RFC 6749 §5.2): `error`, a sentence for people, and the HTTP status."""

    def __init__(self, error, description, status=400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def lifetimes(cfg):
    """How long an app's tokens last (tokens settings): {access_minutes, refresh_days}."""
    t = {**store.DEFAULTS["tokens"], **(cfg.get("tokens") or {})}
    return {"access_minutes": int(t["oauth_access_minutes"]), "refresh_days": int(t["oauth_refresh_days"])}


# ---------- scopes and redirect addresses ----------
def scopes(raw, default="read"):
    """The scopes asked for, as Lens knows them: `read`, or `read write`. Others are ignored; nothing asks for read."""
    asked = set((raw or "").split()) & set(SCOPES)
    if not asked:
        asked = set(default.split())
    return "read write" if "write" in asked else "read"


def can_write(scope):
    return "write" in (scope or "").split()


def check_redirect(uri):
    """A redirect address an app may register: https, http on this machine (loopback), or the app's own scheme.
    Returns it; ValueError otherwise."""
    if not isinstance(uri, str) or not uri or len(uri) > 2000 or any(c.isspace() or ord(c) < 32 for c in uri):
        raise ValueError("a redirect address is one URL")
    p = urllib.parse.urlsplit(uri)
    scheme = p.scheme.lower()
    if "\\" in uri or p.username is not None or p.password is not None or "@" in p.netloc:
        # browsers read a backslash as a slash and what's before an @ as a sign-in, so the host isn't what it seems
        raise ValueError("a redirect address has no backslash and no user name in it")
    if not SCHEME.match(scheme) or scheme in BAD_SCHEMES or scheme.startswith(BAD_SCHEME_PREFIXES):
        raise ValueError(f"redirect addresses can't use {scheme or 'no scheme'}:")
    if p.fragment or "#" in uri:
        raise ValueError("a redirect address has no fragment")
    if scheme == "https":
        if not p.hostname:
            raise ValueError("a redirect address needs a host")
    elif scheme == "http":
        if (p.hostname or "") not in LOOPBACK:
            raise ValueError("http redirect addresses are for this machine only (localhost); use https")
    return uri


def _loopback(p):
    return p.scheme.lower() == "http" and (p.hostname or "") in LOOPBACK


def redirect_ok(registered, uri):
    """Whether `uri` is one of the app's redirect addresses: the same string, or, on this machine, the same but for
    its port (apps listen on whichever is free, RFC 8252 §7.3)."""
    if uri in registered:
        return True
    try:
        p = urllib.parse.urlsplit(check_redirect(uri))
        if not _loopback(p):
            return False
        for r in registered:
            q = urllib.parse.urlsplit(r)
            if _loopback(q) and (q.hostname, q.path, q.query) == (p.hostname, p.path, p.query):
                return True
    except ValueError:
        return False
    return False


def with_params(uri, **params):
    """`uri` with these query parameters added to the ones it has."""
    p = urllib.parse.urlsplit(uri)
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(query), ""))


# ---------- apps (clients) ----------
def register_client(db, name, redirect_uris, uri=None, auth_method="none"):
    """A new app (RFC 7591): {client_id, client_secret?, …}. Apps that can keep a secret get one, shown once."""
    name = " ".join(str(name or "").split())[:80] or "An app"
    if not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > 10:
        raise OAuthError("invalid_redirect_uri", "give 1 to 10 redirect addresses")
    try:
        uris = [check_redirect(u) for u in redirect_uris]
    except ValueError as e:
        raise OAuthError("invalid_redirect_uri", str(e)) from None
    if auth_method not in AUTH_METHODS:
        raise OAuthError("invalid_client_metadata", f"token_endpoint_auth_method is one of: {', '.join(AUTH_METHODS)}")
    if uri is not None and not (isinstance(uri, str) and re.match(r"^https?://[^\s]+$", uri) and len(uri) <= 500):
        uri = None
    forget_unused_clients(db)
    cid = "lc_" + secrets.token_urlsafe(16)
    secret = None if auth_method == "none" else "ls_" + secrets.token_urlsafe(32)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("oauth_client", cid),
        d=store.clean(
            {
                "name": name,
                "redirect_uris": uris,
                "uri": uri,
                "auth_method": auth_method,
                "secret": auth.sha(secret) if secret else None,
                "created_at": store.now(),
                "used": False,
            }
        ),
    )
    return store.clean(
        {"client_id": cid, "client_secret": secret, "name": name, "redirect_uris": uris, "uri": uri, "auth_method": auth_method}
    )


def forget_unused_clients(db):
    """Registering is open to anyone, so apps nobody ever gave access to don't pile up."""
    db.q("DELETE oauth_client WHERE used != true AND created_at < $t", t=_later(-UNUSED_CLIENT_DAYS * 86400))


def get_client(db, cid):
    if not cid or not isinstance(cid, str) or not cid.startswith("lc_"):
        return None
    return db.one("SELECT record::id(id) AS id, name, redirect_uris, uri, auth_method, secret FROM $r", r=R("oauth_client", cid))


def _client(db, cid, secret=None, check_secret=False):
    c = get_client(db, cid)
    if not c:
        raise OAuthError("invalid_client", "unknown client_id", 401)
    if check_secret and c.get("secret") and not hmac.compare_digest(c["secret"], auth.sha(secret or "")):
        raise OAuthError("invalid_client", "wrong client_secret", 401)
    return c


# ---------- consent ----------
def authorization(db, uid, client_id, redirect_uri, response_type, code_challenge, code_challenge_method, scope):
    """What the consent page shows: the app, where it takes the person back to and what it asks for. ValueError when
    the request can't be answered (the page says so and sends nobody anywhere)."""
    c = get_client(db, client_id)
    if not c:
        raise ValueError("This app isn't registered here (unknown client_id).")
    if not redirect_uri or not redirect_ok(c["redirect_uris"], redirect_uri):
        raise ValueError("The address this app wants to return to isn't one it registered.")
    if response_type != "code":
        raise ValueError("Only the authorization code flow is supported (response_type=code).")
    if code_challenge_method != "S256" or not PKCE.fullmatch(code_challenge or "") or len(code_challenge) != 43:
        raise ValueError("The app has to send a PKCE challenge (code_challenge with code_challenge_method=S256).")
    before = db.one("SELECT scope FROM oauth_grant WHERE account = $a AND client = $c LIMIT 1", a=uid, c=c["id"])
    return {
        "client": {"id": c["id"], "name": c["name"], "uri": c.get("uri")},
        "redirect_uri": redirect_uri,
        "scope": scopes(scope),
        "granted": (before or {}).get("scope"),
    }


def approve(db, uid, info, code_challenge, granted_scope=None, resource=None):
    """The person said yes: a one-time code for the app, good for five minutes. The scope given may be less than
    what was asked for (read only), never more."""
    scope = scopes(granted_scope, info["scope"]) if granted_scope else info["scope"]
    if can_write(scope) and not can_write(info["scope"]):
        scope = "read"
    code = secrets.token_urlsafe(32)
    db.q("DELETE oauth_code WHERE expires_at < $now", now=store.now())  # codes never swapped, and spent codes' marks
    db.q(
        "CREATE $r CONTENT $d",
        r=R("oauth_code", auth.sha(code)),
        d=store.clean(
            {
                "client": info["client"]["id"],
                "account": uid,
                "redirect_uri": info["redirect_uri"],
                "challenge": code_challenge,
                "scope": scope,
                "resource": (resource or None) and str(resource)[:500],
                "expires_at": _later(CODE_SECONDS),
            }
        ),
    )
    return code, scope


# ---------- tokens ----------
def _verifier_ok(verifier, challenge):
    if not PKCE.fullmatch(verifier or ""):
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return hmac.compare_digest(calc, challenge or "")


def _issue(db, cfg, grant, uid, scope, resource=None):
    life = lifetimes(cfg)
    access, refresh = "lo_" + secrets.token_urlsafe(32), "lr_" + secrets.token_urlsafe(32)
    ttl = life["access_minutes"] * 60
    ends = _later(life["refresh_days"] * 86400)
    # resource: the server the tokens are for (RFC 8707), when the app named one; that server checks it
    base = store.clean({"gid": grant, "account": uid, "scope": scope, "resource": resource, "created_at": store.now()})
    # a grant revoked while its app was renewing gets nothing: the tokens hang off the grant
    if not db.rows("UPDATE $g SET expires_at = $ends RETURN id", g=R("oauth_grant", grant), ends=ends):
        raise OAuthError("invalid_grant", "this access was revoked")
    db.run(
        ["CREATE $a CONTENT $ad", "CREATE $f CONTENT $fd"],
        a=R("oauth_token", auth.sha(access)),
        ad={**base, "kind": "access", "expires_at": _later(ttl)},
        f=R("oauth_token", auth.sha(refresh)),
        fd={**base, "kind": "refresh", "expires_at": ends},
    )
    return {"access_token": access, "token_type": "Bearer", "expires_in": ttl, "refresh_token": refresh, "scope": scope}


def exchange_code(db, cfg, client_id, client_secret, code, redirect_uri, verifier):
    """The app swaps its code for tokens (once). Returns (tokens, grant): the grant is new, and replaces any this
    person gave the same app before."""
    c = _client(db, client_id, client_secret, check_secret=True)
    # deleting it is what makes a code work once, whoever else is trying it at the same moment
    key = R("oauth_code", auth.sha(code or ""))
    row = (db.rows("DELETE $r RETURN BEFORE", r=key) or [None])[0] if code else None
    if row and row.get("spent"):
        # a code that was swapped already came back: it was probably intercepted, so the tokens it gave stop working
        # (RFC 6749 §4.1.2)
        if row.get("gid") is not None:
            drop_grant(db, row["gid"])
        raise OAuthError("invalid_grant", "the code is wrong, was used already or has expired")
    if not row or row.get("expires_at", "") < store.now() or row["client"] != c["id"]:
        raise OAuthError("invalid_grant", "the code is wrong, was used already or has expired")
    if row["redirect_uri"] != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri isn't the one the code was given for")
    if not _verifier_ok(verifier, row["challenge"]):
        raise OAuthError("invalid_grant", "code_verifier doesn't match the challenge")
    if not auth.active_account(db, row["account"]):
        raise OAuthError("invalid_grant", "the account is no longer active")
    for old in db.values("SELECT VALUE record::id(id) FROM oauth_grant WHERE account = $a AND client = $c", a=row["account"], c=c["id"]):
        drop_grant(db, old)
    gid = db.next_id("oauth_grant")
    db.run(
        ["CREATE $g CONTENT $d", "UPDATE $c SET used = true", "CREATE $k CONTENT $spent"],
        k=key,
        spent={"spent": True, "gid": gid, "expires_at": _later(CODE_REMEMBERED_SECONDS)},
        g=R("oauth_grant", gid),
        d={
            "account": row["account"],
            "client": c["id"],
            "name": c["name"],
            "uri": c.get("uri"),
            "scope": row["scope"],
            "resource": row.get("resource"),
            "created_at": store.now(),
        },
        c=R("oauth_client", c["id"]),
    )
    tokens = _issue(db, cfg, gid, row["account"], row["scope"], row.get("resource"))
    return tokens, {"id": gid, "account": row["account"], "client": c["id"], "name": c["name"]}


def refresh(db, cfg, client_id, client_secret, raw):
    """A new pair for a refresh token, which then stops working. One that was swapped a while ago and comes back
    ends the grant: it was probably copied."""
    c = _client(db, client_id, client_secret, check_secret=True)
    t = db.one(
        "SELECT gid, account, kind, scope, resource, expires_at, rotated, rotated_at FROM $r", r=R("oauth_token", auth.sha(raw or ""))
    )
    g = db.one("SELECT client FROM $r", r=R("oauth_grant", t["gid"])) if t else None
    if not t or not g or t["kind"] != "refresh" or g["client"] != c["id"] or t["expires_at"] < store.now():
        raise OAuthError("invalid_grant", "the refresh token is wrong, was revoked or has expired")
    if t.get("rotated"):
        # within the grace period it's more likely the app renewing twice at once (it keeps the pair it got first);
        # later it was probably copied, so the access ends
        if (t.get("rotated_at") or "") < _later(-REUSE_GRACE_SECONDS):
            drop_grant(db, t["gid"])
            raise OAuthError("invalid_grant", "the refresh token was used already; ask for access again")
        raise OAuthError("invalid_grant", "the refresh token was used already; use the tokens it was swapped for")
    if not auth.active_account(db, t["account"]):
        raise OAuthError("invalid_grant", "the account is no longer active")
    now = store.now()
    db.run(
        ["UPDATE $old SET rotated = true, rotated_at = rotated_at OR $now", "DELETE oauth_token WHERE gid = $g AND expires_at < $now"],
        old=R("oauth_token", auth.sha(raw)),
        now=now,
        g=t["gid"],
    )
    return _issue(db, cfg, t["gid"], t["account"], t["scope"], t.get("resource"))


def token_account(db, raw):
    """The account an access token acts as ({…account, scope, grant, resource}), or None. Refresh tokens don't open
    the API. resource is the server the token was given for, when the app named one."""
    if not raw or not raw.startswith("lo_"):
        return None
    t = db.one("SELECT gid, account, kind, scope, resource, expires_at FROM $r", r=R("oauth_token", auth.sha(raw)))
    if not t or t["kind"] != "access" or t["expires_at"] < store.now():
        return None
    u = auth.active_account(db, t["account"])
    if not u:
        return None
    if not db.rows("UPDATE $r SET last_used_at = $n RETURN id", r=R("oauth_grant", t["gid"]), n=store.now()):
        return None  # its grant is gone
    return {**u, "scope": "write" if can_write(t["scope"]) else "read", "grant": t["gid"], "resource": t.get("resource")}


# ---------- grants: the apps a person gave access to ----------
def grants(db, uid):
    """The apps this person gave access to, the latest first."""
    rows = db.rows(
        "SELECT record::id(id) AS id, client, name, uri, scope, created_at, last_used_at, expires_at FROM oauth_grant WHERE account = $a",
        a=uid,
    )
    return sorted(rows, key=lambda r: (r.get("created_at") or "", r["id"]), reverse=True)


def drop_grant(db, gid):
    """End a grant: its tokens stop working now."""
    db.run(["DELETE oauth_token WHERE gid = $g", "DELETE $r"], g=gid, r=R("oauth_grant", gid))


def revoke_grant(db, uid, gid):
    """Take an app's access away (this person's grant); the grant, or None when they have no such grant."""
    g = db.one("SELECT record::id(id) AS id, client, name FROM $r WHERE account = $a", r=R("oauth_grant", gid), a=uid)
    if g:
        drop_grant(db, gid)
    return g


def revoke_token(db, client_id, client_secret, raw):
    """An app hands back a token (RFC 7009): its grant ends. Returns the grant ({id, account, client, name}) or None
    when the token is unknown, which the app isn't told."""
    c = _client(db, client_id, client_secret, check_secret=True)
    t = db.one("SELECT gid FROM $r", r=R("oauth_token", auth.sha(raw or "")))
    g = db.one("SELECT record::id(id) AS id, account, client, name FROM $r", r=R("oauth_grant", t["gid"])) if t else None
    if not g or g["client"] != c["id"]:
        return None
    drop_grant(db, g["id"])
    return g
