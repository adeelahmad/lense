"""Connecting a storage source by signing in at the service in the browser, rather than pasting a token from rclone.

Google Drive signs in through the Google app an admin set up in Settings › Sign-in (external_login.py, kind google):
its client id and secret, and its callback address, so nothing more is registered at Google. The app may stay off for
signing in to Lens; connecting a source only needs it to exist. The round trip is an external_flow row like a
sign-in's (PKCE, ten minutes, used once, tied by a cookie to the browser that started it), in mode "source". An agent
can start one through the API for its person to open in their own browser: that flow isn't tied to a browser, so the
link alone finishes it, once.

The callback swaps the code for tokens at Google and keeps them sealed as the source's `token`, in rclone's own shape
(access_token, token_type, refresh_token, expiry). The source's `oauth` says which app signed it in and as whom; rclone
is handed that app's client id and secret (sources._config), because Google refreshes a token only for the app that
issued it. A token pasted from rclone, or a service account key, replaces it (sources.update drops `oauth`).

Code that talks to Google itself rather than through rclone gets an access token from fresh_token, which refreshes it
when it is about to run out.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import secrets
import urllib.parse

import httpx

from . import auth, external_login, settings, sources, store

R = store.R
GOOGLE_SCOPE = "https://www.googleapis.com/auth/"
DRIVE_SCOPES = ("drive", "drive.readonly", "drive.file", "drive.metadata.readonly")
TIMEOUT = 15


def _drive_scope(params):
    asked = [s.strip() for s in str(params.get("scope") or "drive.readonly").split(",") if s.strip()]
    if not asked or any(s not in DRIVE_SCOPES for s in asked):
        raise ValueError(f"the Drive scope is one of {', '.join(DRIVE_SCOPES)}")
    return " ".join(GOOGLE_SCOPE + s for s in asked)


# the source types that sign in here: the kind of sign-in provider whose app they use, and the scopes they ask for
TYPES = {"drive": {"kind": "google", "scope": _drive_scope}}


def app(db, typ):
    """The sign-in provider (with its sealed secret) that a source type signs in through, or None when it isn't set up.
    Google's provider key is its kind: an install has one Google app."""
    t = TYPES.get(typ)
    if not t:
        return None
    try:
        row = external_login.provider(db, t["kind"])
    except KeyError:
        return None
    return row if row.get("client_id") and row.get("client_secret") else None


def signin(db, typ):
    """How a source type signs in in the browser, for the connection form; None when it only takes a pasted token or
    keys. `ready` says whether its app is set up; `callback_path` is the address to register with the service."""
    t = TYPES.get(typ)
    if not t:
        return None
    return {
        "kind": t["kind"],
        "label": external_login.LABELS[t["kind"]],
        "ready": app(db, typ) is not None,
        "callback_path": external_login.callback_path(t["kind"]),
    }


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def start(db, cfg, origin, typ=None, params=None, name=None, sid=None, account=None, by=None, bound=True, next_path="/connected"):
    """The service's sign-in page for connecting a new source of type `typ` (with `params`, named `name`), or for
    signing source `sid` in again; and, for a flow tied to this browser (`bound`), the secret to keep in its cookie
    (else None). ValueError when the type can't sign in here or its app isn't set up; KeyError for no such source."""
    if sid is not None:
        src = sources.get(db, sid)
        typ, params = src["type"], {**(src.get("params") or {}), **(params or {})}
    if typ not in TYPES:
        raise ValueError(f"{sources.BACKENDS[typ]['label'] if typ in sources.BACKENDS else typ} doesn't sign in here")
    params = {**sources.BACKENDS[typ]["fields"], **(params or {})}
    sources._check_params(typ, params, None)
    scope = TYPES[typ]["scope"](params)
    row = app(db, typ)
    if not row:
        label = external_login.LABELS[TYPES[typ]["kind"]]
        raise ValueError(f"set up your {label} app first (Settings › Sign-in, or the connection form): its client id and secret")
    ep = external_login.ENDPOINTS[row["kind"]]
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    browser = secrets.token_urlsafe(24) if bound else None
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect_uri = origin.rstrip("/") + external_login.callback_path(row["key"])
    db.q("DELETE external_flow WHERE expires_at < $n", n=store.now())
    db.q(
        "CREATE $r CONTENT $d",
        r=R("external_flow", auth.sha(state)),
        d=store.clean(
            {
                "provider": row["key"],
                "verifier": verifier,
                "browser": auth.sha(browser) if browser else None,
                "unbound": None if bound else True,
                "redirect_uri": redirect_uri,
                "origin": origin.rstrip("/"),
                "mode": "source",
                "account": account,
                "next": external_login.safe_next(next_path),
                "source": store.clean({"id": sid, "type": typ, "params": params, "name": (name or "").strip()[:80] or None, "by": by}),
                "expires_at": _later(external_login.FLOW_MINUTES * 60),
            }
        ),
    )
    q = {
        "response_type": "code",
        "client_id": row["client_id"],
        "redirect_uri": redirect_uri,
        "scope": f"openid email {scope}",  # who signed in, to show beside the connection
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",  # a refresh token, so the connection keeps working
        "prompt": "consent",  # Google only sends one when asked for consent
    }
    return ep["authorize"] + "?" + urllib.parse.urlencode(q), browser


def _token(cfg, row, form):
    """What the provider's token endpoint answers to `form` (with the app's client id and secret)."""
    try:
        r = httpx.post(
            external_login.ENDPOINTS[row["kind"]]["token"],
            data={**form, "client_id": row["client_id"], "client_secret": external_login.client_secret(cfg, row)},
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        tok = r.json()
    except (httpx.HTTPError, ValueError):
        raise external_login.ExternalLoginError(f"{external_login.LABELS[row['kind']]} didn't answer; try again") from None
    if r.status_code >= 400 or not isinstance(tok, dict) or not tok.get("access_token"):
        why = (tok.get("error_description") or tok.get("error")) if isinstance(tok, dict) else None
        raise external_login.ExternalLoginError(
            f"{external_login.LABELS[row['kind']]} refused: {why or r.status_code}; check the app's client id and secret"
        )
    return tok


def _expiry(tok):
    seconds = tok.get("expires_in")
    seconds = int(seconds) if isinstance(seconds, (int, float, str)) and str(seconds).isdigit() else 3600
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def _email(row, access):
    """The signed-in account's address, to show beside the connection; None when the provider won't say."""
    try:
        info = httpx.get(
            external_login.ENDPOINTS[row["kind"]]["userinfo"],
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            timeout=TIMEOUT,
        ).json()
    except (httpx.HTTPError, ValueError):
        return None
    email = info.get("email") if isinstance(info, dict) else None
    return str(email)[:200] if email else None


def finish(db, cfg, flow, code):
    """Swap the code the provider sent back for tokens and connect the source the flow is for: made now when it's new,
    else signed in again. Returns the source's id. ExternalLoginError (a ValueError) says why it didn't work."""
    want = flow.get("source") or {}
    row = app(db, want.get("type"))
    if not row or row["key"] != flow.get("provider"):
        raise external_login.ExternalLoginError("the app this sign-in went through was removed; set it up again and sign in again")
    tok = _token(
        cfg,
        row,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]},
    )
    if not tok.get("refresh_token"):
        raise external_login.ExternalLoginError(
            f"{external_login.LABELS[row['kind']]} sent no refresh token, so the connection would stop within the hour; sign in again"
        )
    email = _email(row, tok["access_token"])
    token = json.dumps(
        {
            "access_token": tok["access_token"],
            "token_type": tok.get("token_type") or "Bearer",
            "refresh_token": tok["refresh_token"],
            "expiry": _expiry(tok),
        },
        separators=(",", ":"),
    )
    typ = want["type"]
    others = {k: None for k in sources.BACKENDS[typ]["secrets"] if k != "token"}  # signed in: no service account
    if want.get("id") is not None:
        sid = want["id"]
        try:
            sources.update(db, cfg, sid, params=want.get("params"), secret_values={"token": token, **others})
        except KeyError:
            raise external_login.ExternalLoginError("that connection was deleted while you were signing in") from None
    else:
        label = sources.BACKENDS[typ]["label"]
        name = want.get("name") or (f"{label} · {email}" if email else label)
        sid = sources.create(db, cfg, name, typ, want.get("params"), {"token": token}, want.get("by"))
    db.q(
        "UPDATE $r SET oauth = $o",
        r=R("storage_source", sid),
        o=store.clean({"provider": row["key"], "email": email, "at": store.now()}),
    )
    sources.test(db, cfg, sid)
    return sid


def _when(text):
    """An rclone token's expiry (Go writes RFC 3339 with up to nine digits of seconds), or None."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)", str(text or ""))
    if not m:
        return None
    return dt.datetime.fromisoformat(m[1] + (m[2] or "")[:7] + ("+00:00" if m[3] == "Z" else m[3]))


def fresh_token(db, cfg, src, margin=60):
    """A working access token for a source signed in here, refreshed at the provider when it runs out within `margin`
    seconds (the new one is saved). For code that calls the service itself; rclone refreshes its own. ValueError when
    the source wasn't signed in here, or the provider refuses (sign in again)."""
    oauth = src.get("oauth") or {}
    row = app(db, src["type"])
    if not oauth or not row or row["key"] != oauth.get("provider") or "token" not in (src.get("sealed") or {}):
        raise ValueError(f"{src.get('name') or 'this connection'} wasn't signed in through an app set up here; sign in again")
    context = f"source:{src['id']}:token"
    tok = json.loads(settings.unseal(cfg, src["sealed"]["token"], context))
    when = _when(tok.get("expiry"))
    if when and when > dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=margin):
        return tok["access_token"]
    new = _token(cfg, row, {"grant_type": "refresh_token", "refresh_token": tok.get("refresh_token") or ""})
    tok.update(access_token=new["access_token"], token_type=new.get("token_type") or tok.get("token_type") or "Bearer", expiry=_expiry(new))
    if new.get("refresh_token"):
        tok["refresh_token"] = new["refresh_token"]
    sealed = settings.seal(cfg, json.dumps(tok, separators=(",", ":")), context)
    db.q("UPDATE $r SET sealed.token = $t", r=R("storage_source", src["id"]), t=sealed)
    src["sealed"]["token"] = sealed
    return tok["access_token"]
