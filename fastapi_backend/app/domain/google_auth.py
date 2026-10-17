"""Signing in to Google's APIs for a source (Google Calendar, gcal.py; Gmail, gmail.py; other Google sources can use it
too), and calling them.

A source signs in one of two ways, each kept sealed like a password:

- As a **service account** (`service_account_credentials`, the .json key file Google Cloud gives): Lens signs a short
  JWT with the account's private key and swaps it at Google's token endpoint for an access token (RFC 7523). In Google
  Workspace it can act as a user of the domain (`impersonate`), once an admin gave it domain-wide delegation for the
  scopes it asks for; otherwise it reads what was shared with its own address.
- As a **Google account** someone connected (`token`): an OAuth client's refresh token, kept as the "authorized user"
  JSON ({type, client_id, client_secret, refresh_token}, as `gcloud auth application-default login --client-id-file`
  writes it for a client of your own), so the source carries everything it needs to sign in.

Access tokens last an hour; each is kept in memory until a minute before it ends, and never stored. The token endpoint
is always Google's own: a key file's `token_uri` is not followed. Every call to Google is a row in the activity ledger
(`google.token`, `google.<api>`), with what was called, never what came back.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import secrets
import time
import urllib.parse

import httpx
import jwt

from . import activity

TOKEN_URL = "https://oauth2.googleapis.com/token"
# what each scope lets Lens do, in the words Google's consent page uses for its tick box
CAN = {
    "https://www.googleapis.com/auth/gmail.readonly": "View your email messages and settings",
    "https://mail.google.com/": "Read, compose, send and permanently delete all your email from Gmail",
    "https://www.googleapis.com/auth/calendar.readonly": "See and download any calendar you can access using your Google Calendar",
    "https://www.googleapis.com/auth/drive.readonly": "See and download all your Google Drive files",
    "https://www.googleapis.com/auth/drive": "See, edit, create, and delete all of your Google Drive files",
}
TIMEOUT = 30
_TOKENS: dict[str, tuple[float, str]] = {}  # who and for what -> (when it ends, monotonic; the access token)
# what Google says when it throttles (403 with these reasons, or 429): the account works, it's only slowed down
THROTTLED = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"}


class GoogleError(RuntimeError):
    """Google refused or couldn't be reached; the message says why, for people."""


def service_key(text):
    """A service account's key file, checked and kept on one line. ValueError when it isn't one."""
    try:
        key = json.loads(text)
    except ValueError:
        raise ValueError("the service account key isn't JSON: upload the .json key file Google Cloud gave you") from None
    if not isinstance(key, dict) or key.get("type") != "service_account" or not key.get("client_email") or not key.get("private_key"):
        raise ValueError("that isn't a service account key: it needs type service_account, client_email and private_key")
    return json.dumps(key, separators=(",", ":"))


def authorized_user(text):
    """A connected Google account as it's kept: {type: authorized_user, client_id, client_secret, refresh_token}.
    ValueError when the text isn't one."""
    try:
        o = json.loads(text)
    except ValueError:
        raise ValueError("the Google account isn't JSON: it's {client_id, client_secret, refresh_token}") from None
    keys = ("client_id", "client_secret", "refresh_token")
    if (
        not isinstance(o, dict)
        or o.get("type", "authorized_user") != "authorized_user"
        or not all(isinstance(o.get(k), str) and o[k] for k in keys)
    ):
        raise ValueError("that isn't a connected Google account: it needs client_id, client_secret and refresh_token")
    return json.dumps({"type": "authorized_user", **{k: o[k].strip() for k in keys}}, separators=(",", ":"))


def check_secret(k, v):
    """A Google source's secret as it's stored (sources.create and update call this)."""
    if k == "service_account_credentials":
        return service_key(v)
    if k == "token":
        return authorized_user(v)
    return str(v)


def secret(cfg, src, k):
    """A source's sealed secret, opened ("" when it has none)."""
    from . import settings  # settings imports sources, which imports what imports this

    sealed = (src.get("sealed") or {}).get(k)
    return settings.unseal(cfg, sealed, f"source:{src['id']}:{k}") if sealed else ""


def credentials(cfg, src):
    """What a source signs in with: {kind: service_account, key, subject} (its key file, acting as `impersonate`
    when set), else {kind: authorized_user, user} (the Google account connected to it)."""
    key = secret(cfg, src, "service_account_credentials")
    if key:
        subject = str((src.get("params") or {}).get("impersonate") or "").strip()
        return {"kind": "service_account", "key": json.loads(key), "subject": subject or None}
    user = secret(cfg, src, "token")
    if user:
        return {"kind": "authorized_user", "user": json.loads(user)}
    raise GoogleError("connect a Google account, or upload a service account key, first")


def who(creds):
    """The address a source signs in as, for messages: the service account's (or the user it acts as)."""
    if creds["kind"] == "service_account":
        return creds["subject"] or creds["key"]["client_email"]
    return "the connected Google account"


def _cache_key(creds, scopes):
    if creds["kind"] == "service_account":
        k = creds["key"]
        who_ = f"sa|{k['client_email']}|{k.get('private_key_id') or hashlib.sha256(k['private_key'].encode()).hexdigest()}|{creds['subject'] or ''}"
    else:
        who_ = f"user|{creds['user']['client_id']}|{creds['user']['refresh_token']}"
    return hashlib.sha256(f"{who_}|{' '.join(sorted(scopes))}".encode()).hexdigest()


def _refused(r, creds, scopes=()):
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    said = (body.get("error_description") if isinstance(body, dict) else None) or err or f"HTTP {r.status_code}"
    if creds["kind"] == "service_account" and creds["subject"]:
        k = creds["key"]
        if err == "unauthorized_client":
            return GoogleError(
                f"Google refused to let {k['client_email']} act as {creds['subject']}: a Workspace admin gives client id "
                f"{k.get('client_id') or '(in the key file)'} domain-wide delegation for {','.join(scopes) or 'its scopes'} "
                "(Admin console › Security › API controls)"
            )
        if err == "invalid_grant" and "email" in said.lower():
            return GoogleError(f"{k['client_email']} can't act as {creds['subject']}: use the address of someone in your Workspace domain")
    if creds["kind"] == "authorized_user" and err == "invalid_grant":
        return GoogleError(f"Google refused the connected account: {said}. Connect the Google account again")
    if creds["kind"] == "authorized_user" and err in ("invalid_client", "unauthorized_client"):
        return GoogleError(
            f"Google doesn't know the client the Google account was connected with: {said}. Connect the Google account again"
        )
    return GoogleError(f"Google refused to sign in: {said}")


def access_token(cfg, creds, scopes, fresh=False):
    """An access token for these scopes, from the cache unless it's about to end (or `fresh`)."""
    key = _cache_key(creds, scopes)
    hit = _TOKENS.get(key)
    if hit and not fresh and hit[0] > time.monotonic():
        return hit[1]
    if creds["kind"] == "service_account":
        k, now = creds["key"], int(time.time())
        claims = {"iss": k["client_email"], "scope": " ".join(scopes), "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
        if creds["subject"]:
            claims["sub"] = creds["subject"]
        try:
            assertion = jwt.encode(
                claims, k["private_key"], algorithm="RS256", headers={"kid": k.get("private_key_id")} if k.get("private_key_id") else None
            )
        except (ValueError, TypeError, jwt.PyJWTError) as e:
            raise GoogleError(f"the service account's private key can't sign: {e}") from None
        form = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
    else:
        u = creds["user"]
        form = {
            "grant_type": "refresh_token",
            "client_id": u["client_id"],
            "client_secret": u["client_secret"],
            "refresh_token": u["refresh_token"],
        }
    with activity.call("google.token", cfg, detail={"as": creds["kind"]}):
        try:
            r = httpx.post(TOKEN_URL, data=form, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            raise GoogleError(f"couldn't reach Google to sign in: {e}") from None
        if r.status_code >= 400:
            raise _refused(r, creds, scopes)
        try:
            body = r.json()
            token, lasts = body["access_token"], int(body.get("expires_in") or 3600)
        except (ValueError, KeyError, TypeError):
            raise GoogleError("Google's sign-in answered without an access token") from None
        granted = str(body.get("scope") or "").split()
        missing = [s for s in scopes if granted and s not in granted]
        if creds["kind"] == "authorized_user" and missing:  # signed in for one way of reading, then switched to another
            raise GoogleError(
                f"the connected Google account doesn't allow “{CAN.get(missing[0], missing[0])}”: connect the Google account again"
            )
    _TOKENS[key] = (time.monotonic() + max(lasts - 60, 30), token)
    return token


def _reason(r):
    try:
        e = r.json().get("error") or {}
        return (e.get("errors") or [{}])[0].get("reason") or e.get("status") or "", e.get("message") or ""
    except (ValueError, AttributeError, IndexError, TypeError):
        return "", ""


def _answer(r, creds):
    """A reply from one of Google's APIs as JSON, or the error it means."""
    if r.status_code < 400:
        try:
            return r.json()
        except ValueError:
            raise GoogleError("Google answered with something that isn't JSON") from None
    reason, message = _reason(r)
    if r.status_code == 429 or reason in THROTTLED:
        from . import sources  # sources imports feeds, which imports what imports this

        raise sources.RateLimited(f"Google is rate limiting {who(creds)}: {message or reason or r.status_code}")
    if r.status_code == 404:
        raise FileNotFoundError(message or "Google has no such item, or it isn't shared with this account")
    if r.status_code == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
        raise GoogleError(f"the API isn't turned on for this Google Cloud project: {message}")
    raise GoogleError(f"Google answered {r.status_code}: {message or reason or 'refused'}")


def get(cfg, creds, scopes, url, params=None, api="api", what=None):
    """GET one of Google's APIs as the source signs in, as JSON. Signs in again once if Google says the token ended.
    sources.RateLimited when Google is throttling the account, FileNotFoundError when it has no such item, GoogleError
    when it refused."""
    for attempt in (0, 1):
        token = access_token(cfg, creds, scopes, fresh=attempt == 1)
        with activity.call(f"google.{api}", cfg, detail={"call": what} if what else None) as row:
            try:
                r = httpx.get(
                    url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=TIMEOUT
                )
            except httpx.HTTPError as e:
                raise GoogleError(f"couldn't reach Google: {e}") from None
            if r.status_code == 401 and attempt == 0:  # the token ended early (or was revoked): sign in again, once
                row.end(GoogleError("signed in again"))
                continue
            return _answer(r, creds)
    raise GoogleError("Google didn't accept a fresh sign-in")  # not reached: the second attempt returns or raises


# ---------- connecting a Google account to a source (OAuth consent in the browser) ----------
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
IDENTITY = ("openid", "email")  # asked for too, to show which account is connected
FLOW_MINUTES = 10
CALLBACK = "/api/v1/auth/external/google/callback"  # the Google sign-in provider's own: one redirect URI for both


def _module(src):
    from . import feeds, gdrive, gmail  # they import this

    mod = feeds.OWN.get(src["type"]) or {"gmail": gmail, "drive": gdrive}.get(src["type"])
    if mod is None or not getattr(mod, "SCOPES", None):
        raise ValueError(f"a {src['type']} source doesn't sign in with a Google account")
    return mod


def scopes_for(src):
    """The scopes a source's type reads with: its module's SCOPES, or SCOPES(params) when they depend on its settings."""
    s = _module(src).SCOPES
    return tuple(s(src.get("params") or {}) if callable(s) else s)


def client(db, cfg, client_id=None, client_secret=None):
    """The OAuth client a Google account is connected through: {client_id, client_secret, origin}. The one given, else
    the Google sign-in provider's (Settings › Sign-in), else the one a Google source was connected through last.
    ValueError when there's none."""
    if client_id or client_secret:
        if not (client_id and client_secret):
            raise ValueError("enter both the client ID and the client secret of the Google OAuth client")
        return {"client_id": str(client_id).strip(), "client_secret": str(client_secret).strip(), "origin": "given"}
    from . import external_login, settings, sources, store

    row = db.one("SELECT client_id, client_secret FROM $r", r=store.R("login_provider", "google"))
    if row and row.get("client_id") and row.get("client_secret"):
        secret_ = settings.unseal(cfg, row["client_secret"], external_login._context("google"))
        return {"client_id": row["client_id"], "client_secret": secret_, "origin": "sign-in"}
    google_types = [k for k, v in sources.BACKENDS.items() if v.get("google")]
    rows = db.rows(
        "SELECT record::id(id) AS id, created_at FROM storage_source WHERE type IN $t AND sealed.token != NONE ORDER BY created_at DESC",
        t=google_types,
    )
    for sid in [r["id"] for r in rows]:
        user = json.loads(secret(cfg, sources.get(db, sid), "token") or "{}")
        if user.get("client_id") and user.get("client_secret"):
            return {"client_id": user["client_id"], "client_secret": user["client_secret"], "origin": "connection"}
    raise ValueError(
        "Lens needs a Google OAuth client to connect an account: add Google in Settings › Sign-in, or enter a client "
        "ID and secret (Google Cloud › APIs & Services › Credentials, a web application client)"
    )


def connect_start(db, cfg, src, origin, given=None, account=None):
    """Google's consent page for connecting an account to source `src`, and the browser secret to keep in a cookie
    (the sign-in cookie: the flow is an external_flow, used once, ten minutes, bound to this browser)."""
    from . import auth, settings, store

    scopes = scopes_for(src)
    c = client(db, cfg, *(given or (None, None)))
    state, verifier, browser = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect_uri = origin.rstrip("/") + CALLBACK
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=FLOW_MINUTES)).isoformat(timespec="seconds")
    db.q("DELETE external_flow WHERE expires_at < $n", n=store.now())
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("external_flow", auth.sha(state)),
        d={
            "provider": "google",
            "mode": "source",
            "source": src["id"],
            "scopes": list(scopes),
            "client_id": c["client_id"],
            "client_secret": settings.seal(cfg, c["client_secret"], f"external_flow:{auth.sha(state)}"),
            "verifier": verifier,
            "browser": auth.sha(browser),
            "redirect_uri": redirect_uri,
            "origin": origin.rstrip("/"),
            "account": account,  # the admin who started it: still one when Google sends them back
            "expires_at": expires,
        },
    )
    q = {
        "response_type": "code",
        "client_id": c["client_id"],
        "redirect_uri": redirect_uri,
        "scope": " ".join([*IDENTITY, *scopes]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",  # a refresh token, so the source reads on its own
        "prompt": "consent",  # Google gives a refresh token only when it asks
        "include_granted_scopes": "false",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(q), browser


def connect_finish(db, cfg, flow, code, state):
    """Swap the code Google sent back for the account's refresh token and keep it on the source (its `token`; a
    service account key it had is dropped). Returns the source's id and the account's address."""
    from . import auth, settings, sources

    src = sources.get(db, flow["source"])  # KeyError: removed meanwhile
    client_secret = settings.unseal(cfg, flow["client_secret"], f"external_flow:{auth.sha(state)}")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "client_secret": client_secret,
        "code_verifier": flow["verifier"],
    }
    with activity.call("google.token", cfg, detail={"as": "consent"}):
        try:
            r = httpx.post(TOKEN_URL, data=form, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            raise GoogleError(f"couldn't reach Google: {e}") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or not isinstance(body, dict):
            said = (body.get("error_description") or body.get("error")) if isinstance(body, dict) else None
            raise GoogleError(f"Google refused: {said or r.status_code}; check the client ID and secret")
    if not body.get("refresh_token"):
        raise GoogleError("Google gave no refresh token; remove Lens from your Google account's third-party access and connect again")
    granted = set(str(body.get("scope") or "").split())
    missing = [s for s in flow.get("scopes") or [] if granted and s not in granted]
    if missing:
        raise GoogleError("Google wasn't given access to what this connection reads: tick it on Google's consent screen")
    email = ""
    if body.get("id_token"):
        try:  # straight from Google's token endpoint over TLS: no signature to check (OpenID Connect §3.1.3.7)
            email = str(jwt.decode(body["id_token"], options={"verify_signature": False}).get("email") or "")
        except jwt.PyJWTError:
            email = ""
    mod = _module(src)
    if hasattr(mod, "keep"):  # kept another way (Drive: as rclone keeps it)
        params, kept = mod.keep(flow["client_id"], client_secret, body, email)
    else:
        user = {
            "type": "authorized_user",
            "client_id": flow["client_id"],
            "client_secret": client_secret,
            "refresh_token": body["refresh_token"],
        }
        field = getattr(mod, "ACCOUNT", "account")  # the parameter that names the connected account
        params, kept = ({field: email} if email and field in sources.BACKENDS[src["type"]]["fields"] else None), {"token": json.dumps(user)}
    sources.update(db, cfg, src["id"], params=params, secret_values={**kept, "service_account_credentials": None})
    return src["id"], email
