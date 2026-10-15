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

  Someone signs a source in from the web app with **Sign in with Google** (`start` and `finish`, below): Google asks
  them to let Lens read what the source reads, and Lens keeps the refresh token it gives. The client is the one admins
  set up for signing in with Google (Settings › Sign-in), and the round trip comes back through that client's own
  callback (external_login.py, flow mode "source"), so nothing new is registered at Google. Google lets people untick
  a permission on its page: then the source isn't connected, and they're told which one to leave ticked.

Access tokens last an hour; each is kept in memory until a minute before it ends, and never stored. The token endpoint
is always Google's own: a key file's `token_uri` is not followed. Every call to Google is a row in the activity ledger
(`google.token`, `google.<api>`), with what was called, never what came back.
"""

from __future__ import annotations

import hashlib
import json
import time

import httpx
import jwt

from . import activity

TOKEN_URL = "https://oauth2.googleapis.com/token"
PROVIDER = "google"  # the sign-in provider (Settings › Sign-in) whose client people sign their sources in with
SIGN_IN = ("openid", "email")  # asked for besides a source's own scopes: which account it is
# what each scope lets Lens do, in the words Google's consent page uses for its tick box
CAN = {
    "https://www.googleapis.com/auth/gmail.readonly": "View your email messages and settings",
    "https://mail.google.com/": "Read, compose, send and permanently delete all your email from Gmail",
    "https://www.googleapis.com/auth/calendar.readonly": "See and download any calendar you can access using your Google Calendar",
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
    raise GoogleError("sign in with Google, or upload a service account key, first")


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
        return GoogleError(f"Google no longer accepts this source's sign-in ({said}): sign in with Google again")
    if creds["kind"] == "authorized_user" and err in ("invalid_client", "unauthorized_client"):
        return GoogleError(f"Google doesn't know the client this source signed in with ({said}): sign in with Google again")
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
            raise GoogleError(f"this source's Google sign-in doesn't allow “{CAN.get(missing[0], missing[0])}”: sign in with Google again")
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


# ---------- signing a source in (the web app) ----------
def scopes(src):
    """The scopes a Google source reads with. ValueError for a source that doesn't sign in to Google."""
    from . import gcal, gmail  # they read through this module

    how = {"gmail": gmail.scopes, "gcal": lambda _: gcal.SCOPES}.get(src["type"])
    if how is None:
        raise ValueError(f"a {src['type']} source doesn't sign in to Google")
    return list(how(src))


def client(db):
    """The Google client people sign their sources in with: the sign-in provider's row. GoogleError when there's none."""
    from . import external_login

    try:
        row = external_login.provider(db, PROVIDER)
    except KeyError:
        raise GoogleError(
            "set up Google in Settings › Sign-in first (its client id and secret): the same client signs sources in to Google"
        ) from None
    if row.get("kind") != "google" or not row.get("client_id") or not row.get("client_secret"):
        raise GoogleError("the Google sign-in in Settings › Sign-in has no client id or secret")
    return row


def _hint(src):
    return str((src.get("params") or {}).get("user") or "").strip()


def start(db, cfg, src, origin, account):
    """Google's page to send the browser to, to sign a source in, and the browser secret to keep in a cookie."""
    from . import external_login

    asked = [*SIGN_IN, *scopes(src)]
    client(db)
    extra = {"access_type": "offline", "prompt": "consent select_account"}  # consent: Google gives a refresh token every time
    if "@" in _hint(src):
        extra["login_hint"] = _hint(src)
    return external_login.start(
        db,
        cfg,
        PROVIDER,
        origin,
        mode="source",
        account=account,
        next_path=f"/sources?source={src['id']}",
        source=src["id"],
        scope=" ".join(asked),
        extra=extra,
    )


def finish(db, cfg, flow, code):
    """Sign the flow's source in with what Google sent back: keeps the refresh token on the source (and, for a source
    that has one, its address as the source's user), and drops a service account key it had. Returns (source id,
    address)."""
    from . import external_login, feeds, settings, sources  # sources imports feeds, which reads through this

    row = client(db)
    src = sources.get(db, flow["source"])
    secret_ = settings.unseal(cfg, row["client_secret"], external_login._context(PROVIDER))
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow["redirect_uri"],
        "client_id": row["client_id"],
        "client_secret": secret_,
        "code_verifier": flow["verifier"],
    }
    with activity.call("google.token", cfg, detail={"as": "sign-in"}):
        try:
            r = httpx.post(TOKEN_URL, data=form, headers={"Accept": "application/json"}, timeout=TIMEOUT)
            got = r.json()
        except (httpx.HTTPError, ValueError):
            raise GoogleError("couldn't reach Google to sign in; try again") from None
        if r.status_code >= 400 or not isinstance(got, dict):
            said = (got.get("error_description") or got.get("error")) if isinstance(got, dict) else r.status_code
            raise GoogleError(f"Google refused: {said}; check the client id and secret in Settings › Sign-in")
    granted = set(str(got.get("scope") or "").split())
    missing = [s for s in str(flow.get("scope") or "").split() if s not in SIGN_IN and s not in granted]
    if missing:
        raise GoogleError(f"Google wasn't allowed to “{CAN.get(missing[0], missing[0])}”: sign in again and leave that box ticked")
    if not got.get("refresh_token"):
        raise GoogleError(
            "Google didn't give Lens a lasting sign-in; remove Lens's access in your Google account's security page and try again"
        )
    try:
        said = jwt.decode(got.get("id_token") or "", options={"verify_signature": False})  # straight from Google, over TLS
    except jwt.PyJWTError:
        said = {}
    address = str(said.get("email") or "").strip().lower()
    if not address:
        raise GoogleError("Google didn't say which account signed in; try again")
    keep = {"type": "authorized_user", "client_id": row["client_id"], "client_secret": secret_, "refresh_token": got["refresh_token"]}
    values = {"token": json.dumps(keep, separators=(",", ":"))}
    if (src.get("sealed") or {}).get("service_account_credentials"):
        values["service_account_credentials"] = ""  # signed in as a person now: the key would sign in first otherwise
    has_user = "user" in (feeds.TYPES.get(src["type"]) or {}).get("fields", {})
    sources.update(db, cfg, src["id"], params={"user": address} if has_user else None, secret_values=values)
    return src["id"], address
