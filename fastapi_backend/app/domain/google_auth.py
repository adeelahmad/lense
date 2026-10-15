"""Google accounts for sources: a Gmail or Google Workspace mailbox (feeds.py, gmail.py), and any other source that
reads one of Google's APIs.

A source signs in to Google one of two ways, as Google offers them:

- As a person (OAuth). Someone opens the source in the web app and signs in with Google, which asks them to let Lens
  read what the source reads: Gmail read only, or all of Gmail for IMAP (Google has no read-only scope for IMAP).
  Lens keeps the refresh token Google gives back, sealed like a password, together with the client it was given to
  (a refresh token works only with its own client). The client is the one admins set up for signing in with Google
  (Settings › Sign-in), and the round trip comes back through that client's own callback (external_login.py, flow
  mode "source"), so nothing new is registered at Google. Google lets people untick a permission on its page: then
  the source isn't connected, and they're told which one to leave ticked.
- As a Google Workspace service account with domain-wide delegation. Its JSON key (sealed) signs a short assertion
  (RS256) to act as one person of the domain, the source's `user`, which Google swaps for an access token. Nobody
  signs in; a Workspace admin lets the account's client id have the scopes (Admin console › Security › API controls
  › Domain-wide delegation).

An agent can give a source a token it already has instead: the `token` secret as JSON with refresh_token, client_id
and client_secret.

Access tokens last an hour. They're kept in memory only, until a minute before they run out; each one Google gives is
a call out in the activity ledger (google.token).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import httpx
import jwt

from . import activity, external_login, settings

TOKEN_URI = "https://oauth2.googleapis.com/token"
PROVIDER = "google"  # the sign-in provider (Settings › Sign-in) whose client people sign their sources in with
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
MAIL = "https://mail.google.com/"  # all of Gmail: the only scope Google gives IMAP
SIGN_IN = ("openid", "email")  # asked for besides a source's own scopes: which account it is
# what each scope lets Lens do, in the words Google's consent page uses for its tick box
CAN = {
    GMAIL_READ: "View your email messages and settings",
    MAIL: "Read, compose, send and permanently delete all your email from Gmail",
}
TIMEOUT = 30
EARLY = 60  # an access token is used until this many seconds before it runs out
MAX_CACHED = 256
_TOKENS: dict[str, tuple[str, float]] = {}
_LOCK = threading.Lock()


class GoogleError(RuntimeError):
    """Google said no, or couldn't be reached; the message says why, for the person."""


def _gmail(params):
    return [MAIL] if params.get("via") == "imap" else [GMAIL_READ]


# a source type that signs in to Google, and the scopes it reads with, from its settings
SCOPES = {"gmail": _gmail}


def scopes(src):
    """The scopes a source reads with."""
    how = SCOPES.get(src["type"])
    if how is None:
        raise ValueError(f"a {src['type']} source doesn't sign in to Google")
    return how({k: str(v if v is not None else "").strip() for k, v in (src.get("params") or {}).items()})


def signs_in(src):
    """How a source signs in to Google: "service_account", "oauth", or None when it doesn't yet."""
    sealed = src.get("sealed") or {}
    if sealed.get("service_account_credentials"):
        return "service_account"
    return "oauth" if sealed.get("token") else None


def check_token(text):
    """A token as a source keeps it: JSON with refresh_token, client_id and client_secret (other fields kept)."""
    try:
        t = json.loads(text)
    except ValueError:
        raise ValueError("a Google token is JSON with refresh_token, client_id and client_secret") from None
    if not isinstance(t, dict) or not all(
        isinstance(t.get(k), str) and t[k].strip() for k in ("refresh_token", "client_id", "client_secret")
    ):
        raise ValueError("a Google token needs refresh_token, client_id and client_secret")
    return json.dumps(t, separators=(",", ":"))


def _unsealed(cfg, src, k):
    sealed = (src.get("sealed") or {}).get(k)
    return settings.unseal(cfg, sealed, f"source:{src['id']}:{k}") if sealed else ""


def _user(src):
    return str((src.get("params") or {}).get("user") or "").strip()


def token(cfg, src, want=None):
    """An access token for a source, for the scopes it reads with (or `want`)."""
    want = sorted(want or scopes(src))
    sealed = src.get("sealed") or {}
    held = [src.get("id"), want, sealed.get("service_account_credentials"), sealed.get("token"), _user(src)]
    key = hashlib.sha256(json.dumps(held, default=str).encode()).hexdigest()
    with _LOCK:
        hit = _TOKENS.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
    how = signs_in(src)
    if how == "service_account":
        got = _service_account(cfg, src, want)
    elif how == "oauth":
        got = _refresh(cfg, src, want)
    else:
        raise GoogleError("this source isn't signed in to Google yet: sign in with Google, or add a service account key")
    with _LOCK:
        if len(_TOKENS) >= MAX_CACHED:
            _TOKENS.clear()
        _TOKENS[key] = (got["access_token"], time.monotonic() + max(0, int(got.get("expires_in") or 3600) - EARLY))
    return got["access_token"]


def _grant(data, explain):
    """What Google's token endpoint gives for this grant (access_token, expires_in, scope, ...); GoogleError, in
    words `explain` picks from Google's error, otherwise."""
    with activity.call("google.token", detail={"grant": data["grant_type"].rsplit(":", 1)[-1]}):
        try:
            r = httpx.post(TOKEN_URI, data=data, headers={"Accept": "application/json"}, timeout=TIMEOUT)
            body = r.json()
        except (httpx.HTTPError, ValueError):
            raise GoogleError("couldn't reach Google to sign in; try again") from None
        if r.status_code >= 400 or not isinstance(body, dict) or not body.get("access_token"):
            body = body if isinstance(body, dict) else {}
            raise GoogleError(explain(str(body.get("error") or r.status_code), str(body.get("error_description") or "")))
    return body


def _service_account(cfg, src, want):
    key = json.loads(_unsealed(cfg, src, "service_account_credentials"))
    who = _user(src)
    if not who:
        raise GoogleError("a service account reads one person's mail: enter their address (the user it acts as)")
    now = int(time.time())
    claims = {"iss": key["client_email"], "sub": who, "scope": " ".join(want), "aud": TOKEN_URI, "iat": now, "exp": now + 3600}
    try:
        assertion = jwt.encode(
            claims, key["private_key"], algorithm="RS256", headers={"kid": key["private_key_id"]} if key.get("private_key_id") else None
        )
    except (ValueError, TypeError, jwt.PyJWTError):
        raise GoogleError("the service account key can't sign: upload the .json key file Google Cloud gave you again") from None

    def explain(error, said):
        if error == "unauthorized_client":
            return (
                f"Google refused the service account: a Workspace admin has to let its client id ({key.get('client_id') or '?'}) "
                f"use {', '.join(want)} (Admin console › Security › API controls › Domain-wide delegation)"
            )
        if error == "invalid_grant" and "email" in said.lower():
            return f"the service account can't act as {who}: use the address of someone in your Workspace domain"
        return f"Google refused the service account: {said or error}"

    return _grant({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}, explain)


def _refresh(cfg, src, want):
    t = json.loads(_unsealed(cfg, src, "token"))

    def explain(error, said):
        if error == "invalid_grant":
            return "Google no longer accepts this source's sign-in (it was revoked, or expired): sign in with Google again"
        if error in ("invalid_client", "unauthorized_client"):
            return "Google doesn't know the client this source signed in with any more: sign in with Google again"
        return f"Google refused: {said or error}"

    got = _grant(
        {
            "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"],
            "client_id": t["client_id"],
            "client_secret": t["client_secret"],
        },
        explain,
    )
    granted = (got.get("scope") or t.get("scope") or "").split()
    missing = [s for s in want if s not in granted] if granted else []
    if missing:  # signed in for reading by the API, then switched to IMAP (or the other way): ask for the new scope
        raise GoogleError(f"this source's sign-in doesn't allow “{CAN.get(missing[0], missing[0])}”: sign in with Google again")
    return got


# ---------- signing a source in (the web app) ----------
def client(db):
    """The Google client people sign their sources in with: the sign-in provider's row. GoogleError when there's none."""
    try:
        row = external_login.provider(db, PROVIDER)
    except KeyError:
        raise GoogleError(
            "set up Google in Settings › Sign-in first (its client id and secret): the same client signs sources in to Google"
        ) from None
    if row.get("kind") != "google" or not row.get("client_id") or not row.get("client_secret"):
        raise GoogleError("the Google sign-in in Settings › Sign-in has no client id or secret")
    return row


def start(db, cfg, src, origin, account):
    """Google's page to send the browser to, to sign a source in, and the browser secret to keep in a cookie."""
    client(db)
    asked = [*SIGN_IN, *scopes(src)]
    extra = {"access_type": "offline", "prompt": "consent select_account"}  # consent: Google gives a refresh token every time
    if "@" in _user(src):
        extra["login_hint"] = _user(src)
    return external_login.start(
        db,
        cfg,
        PROVIDER,
        origin,
        mode="source",
        account=account,
        next_path=f"/sensors/{src['id']}",
        source=src["id"],
        scope=" ".join(asked),
        extra=extra,
    )


def finish(db, cfg, flow, code):
    """Sign the flow's source in with what Google sent back: keeps the refresh token on the source, its address as the
    source's user, and drops a service account key it had. Returns (source id, address)."""
    from . import sources  # sources imports feeds, which reads through this

    row = client(db)
    src = sources.get(db, flow["source"])
    secret = settings.unseal(cfg, row["client_secret"], external_login._context(PROVIDER))

    def explain(error, said):
        return f"Google refused: {said or error}; check the client id and secret in Settings › Sign-in"

    got = _grant(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow["redirect_uri"],
            "client_id": row["client_id"],
            "client_secret": secret,
            "code_verifier": flow["verifier"],
        },
        explain,
    )
    granted = set((got.get("scope") or "").split())
    missing = [s for s in (flow.get("scope") or "").split() if s not in SIGN_IN and s not in granted]
    if missing:
        raise GoogleError(f"Google wasn't allowed to “{CAN.get(missing[0], missing[0])}”: sign in again and leave that box ticked")
    if not got.get("refresh_token"):
        raise GoogleError(
            "Google didn't give Lens a lasting sign-in; remove Lens's access in your Google account's security page and try again"
        )
    try:
        who = jwt.decode(got.get("id_token") or "", options={"verify_signature": False})  # straight from Google, over TLS
    except jwt.PyJWTError:
        who = {}
    email = str(who.get("email") or "").strip().lower()
    if not email:
        raise GoogleError("Google didn't say which account signed in; try again")
    keep = {
        "refresh_token": got["refresh_token"],
        "client_id": row["client_id"],
        "client_secret": secret,
        "email": email,
        "scope": got.get("scope"),
    }
    secrets = {"token": json.dumps(keep, separators=(",", ":"))}
    if (src.get("sealed") or {}).get("service_account_credentials"):
        secrets["service_account_credentials"] = ""  # signed in as a person now: the key would win otherwise
    sources.update(db, cfg, src["id"], params={"user": email}, secret_values=secrets)
    return src["id"], email
