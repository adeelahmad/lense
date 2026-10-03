"""Signing in with an outside account: Google, GitHub, Microsoft or any OpenID Connect provider (Authentik, Keycloak,
Okta, ...). Admins add providers in Settings › Sign-in; each keeps its client id and a sealed client secret.

The browser asks for a provider's sign-in page (`start`), which keeps an `external_flow` row (ten minutes, used once:
the PKCE verifier, the address to come back to, and a hash of a cookie set on this browser, so a sign-in someone else
started can't be finished in your browser). The provider sends the browser back to the callback with a code, which
`finish` swaps for the person's profile at the provider's token and userinfo endpoints (server to server, over TLS).

An outside account (`external_identity:<hash of provider|subject>`) belongs to one Lens account. Signing in with one
that isn't connected yet connects it to the account with the same email, when the provider vouches for the email:
Google and OpenID Connect providers say so (email_verified), GitHub lists verified addresses, and Microsoft only for a
single organization's tenant (with "common" anyone can make an account that claims any email). Otherwise people
connect it from their profile first. Providers can also let new people in (sign-up), optionally only from some email
domains; new accounts have no password and no roles.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import secrets
import time
import urllib.parse

import httpx

from . import auth, settings, store

R = store.R
FLOW_MINUTES = 10
KINDS = ("google", "github", "microsoft", "oidc")
LABELS = {"google": "Google", "github": "GitHub", "microsoft": "Microsoft", "oidc": "OpenID Connect"}
# Where each kind signs in; "oidc" finds its own from the issuer's discovery document.
ENDPOINTS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
    },
    "microsoft": {
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/oidc/userinfo",
        "scope": "openid email profile",
    },
}
SHARED_TENANTS = ("common", "organizations", "consumers")
KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
TIMEOUT = 15
_DISCOVERY: dict[str, tuple[float, dict]] = {}


class ExternalLoginError(ValueError):
    """Signing in with the outside account didn't work; the message says why, for the person."""


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _context(key):
    return f"login_provider:{key}.client_secret"


# ---------- providers (Settings › Sign-in) ----------
def _public(row, people=None):
    out = {"key": row["key"], "kind": row["kind"], "label": row.get("label") or LABELS[row["kind"]]}
    if people is not None:
        out.update(
            {
                "client_id": row.get("client_id") or "",
                "secret_set": bool(row.get("client_secret")),
                "issuer": row.get("issuer") or "",
                "tenant": row.get("tenant") or "",
                "signup": bool(row.get("signup")),
                "domains": list(row.get("domains") or []),
                "enabled": bool(row.get("enabled")),
                "people": people,
            }
        )
    return out


def _rows(db):
    return db.rows("SELECT *, record::id(id) AS key FROM login_provider ORDER BY created_at")


def providers(db, admin=False):
    """Admins see every provider and its settings (never the secret); everyone else the enabled ones' names."""
    out = []
    for r in _rows(db):
        if admin:
            out.append(_public(r, len(db.values("SELECT VALUE id FROM external_identity WHERE provider = $p", p=r["key"]))))
        elif r.get("enabled"):
            out.append(_public(r))
    return out


def provider(db, key):
    row = db.one("SELECT *, record::id(id) AS key FROM $r", r=R("login_provider", key or "")) if key and KEY.fullmatch(key) else None
    if not row:
        raise KeyError(key)
    return row


def _domains(value):
    out = []
    for d in value or []:
        d = str(d).strip().lower().lstrip("@")
        if d:
            if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d):
                raise ValueError(f"{d} isn't an email domain (like example.com)")
            out.append(d)
    return sorted(set(out))


def _issuer(value):
    u = urllib.parse.urlsplit((value or "").strip())
    local = (u.hostname or "") in ("localhost", "127.0.0.1") or (u.hostname or "").endswith(".localhost")
    if not u.hostname or not (u.scheme == "https" or (u.scheme == "http" and local)) or u.query or u.fragment:
        raise ValueError("the issuer is the provider's https:// address (its discovery document is at /.well-known/openid-configuration)")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), "", ""))


def save_provider(db, cfg, key, data):
    """Add a provider (key None) or change one. Returns its key. The secret is kept sealed; leaving it out keeps it."""
    old = provider(db, key) if key else None
    kind = old["kind"] if old else data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"the kind is one of {', '.join(KINDS)}")
    if not old:
        key = kind if kind != "oidc" else re.sub(r"[^a-z0-9]+", "-", (data.get("label") or "oidc").lower()).strip("-")[:40] or "oidc"
        if not KEY.fullmatch(key):
            key = "oidc"
        base, n = key, 2
        while db.one("SELECT id FROM $r", r=R("login_provider", key)):
            if kind != "oidc":
                raise ValueError(f"{LABELS[kind]} is already set up; change it instead")
            key, n = f"{base}-{n}", n + 1
    row = {k: v for k, v in (old or {}).items() if k not in ("id", "key")}
    row.update({"kind": kind, "created_at": row.get("created_at") or store.now()})
    for f in ("label", "client_id", "tenant"):
        if f in data and data[f] is not None:
            row[f] = str(data[f]).strip()[:200]
    if "issuer" in data and data["issuer"] is not None:
        row["issuer"] = _issuer(data["issuer"]) if kind == "oidc" else ""
    if "signup" in data and data["signup"] is not None:
        row["signup"] = bool(data["signup"])
    if "domains" in data and data["domains"] is not None:
        row["domains"] = _domains(data["domains"])
    if "enabled" in data and data["enabled"] is not None:
        row["enabled"] = bool(data["enabled"])
    elif not old:
        row["enabled"] = True
    if data.get("client_secret"):
        row["client_secret"] = settings.seal(cfg, str(data["client_secret"]).strip(), _context(key))
    if not row.get("client_id"):
        raise ValueError("enter the client id from the provider")
    if not row.get("client_secret"):
        raise ValueError("enter the client secret from the provider")
    if kind == "oidc" and not row.get("issuer"):
        raise ValueError("enter the issuer address of the OpenID Connect provider")
    if kind == "microsoft" and not row.get("tenant"):
        row["tenant"] = "common"
    if kind != "oidc" and not row.get("label"):
        row["label"] = LABELS[kind]
    db.q("UPSERT $r CONTENT $d", r=R("login_provider", key), d=store.clean(row))
    return key


def delete_provider(db, key):
    """Remove a provider and the outside accounts connected through it (people sign in another way)."""
    provider(db, key)
    db.q("DELETE external_identity WHERE provider = $p", p=key)
    db.q("DELETE external_flow WHERE provider = $p", p=key)
    db.q("DELETE $r", r=R("login_provider", key))


def callback_path(key):
    return f"/api/v1/auth/external/{key}/callback"


# ---------- signing in ----------
def _endpoints(row):
    kind = row["kind"]
    if kind == "oidc":
        doc = _discover(row["issuer"])
        return {
            "authorize": doc["authorization_endpoint"],
            "token": doc["token_endpoint"],
            "userinfo": doc.get("userinfo_endpoint"),
            "scope": "openid email profile",
        }
    ep = dict(ENDPOINTS[kind])
    if kind == "microsoft":
        tenant = urllib.parse.quote(row.get("tenant") or "common", safe="")
        ep = {k: v.replace("{tenant}", tenant) for k, v in ep.items()}
    return ep


def _discover(issuer):
    hit = _DISCOVERY.get(issuer)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        r = httpx.get(issuer + "/.well-known/openid-configuration", timeout=TIMEOUT, follow_redirects=False)
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError):
        raise ExternalLoginError(f"can't reach the sign-in provider at {issuer}; check its issuer address") from None
    if not isinstance(doc, dict) or not doc.get("authorization_endpoint") or not doc.get("token_endpoint"):
        raise ExternalLoginError("the provider's discovery document has no authorization or token endpoint")
    _DISCOVERY[issuer] = (time.time(), doc)
    return doc


def start(db, cfg, key, origin, mode="login", account=None, next_path="/"):
    """The provider's sign-in page to send the browser to, and the browser secret to keep in a cookie."""
    row = provider(db, key)
    if not row.get("enabled"):
        raise ExternalLoginError(f"signing in with {row.get('label') or LABELS[row['kind']]} is turned off")
    ep = _endpoints(row)
    state, verifier, browser = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect_uri = origin.rstrip("/") + callback_path(key)
    db.q("DELETE external_flow WHERE expires_at < $n", n=store.now())
    db.q(
        "CREATE $r CONTENT $d",
        r=R("external_flow", auth.sha(state)),
        d=store.clean(
            {
                "provider": key,
                "verifier": verifier,
                "browser": auth.sha(browser),
                "redirect_uri": redirect_uri,
                "origin": origin.rstrip("/"),
                "mode": mode,
                "account": account,
                "next": _safe_next(next_path),
                "expires_at": _later(FLOW_MINUTES * 60),
            }
        ),
    )
    q = {
        "response_type": "code",
        "client_id": row["client_id"],
        "redirect_uri": redirect_uri,
        "scope": ep["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if row["kind"] != "github":
        q["nonce"] = secrets.token_urlsafe(16)
    if row["kind"] == "google":
        q["prompt"] = "select_account"
    sep = "&" if "?" in ep["authorize"] else "?"
    return ep["authorize"] + sep + urllib.parse.urlencode(q), browser


def _safe_next(path):
    path = path or "/"
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def claim(db, state, browser):
    """The flow a callback answers, used up. ExternalLoginError when it's gone, expired, or started in another browser."""
    rows = db.rows("DELETE $r RETURN BEFORE", r=R("external_flow", auth.sha(state))) if state else []
    row = rows[0] if rows else None
    if not row or (row.get("expires_at") or "") < store.now():
        raise ExternalLoginError("that sign-in took too long or was already used; try again")
    if not browser or auth.sha(browser) != row.get("browser"):
        raise ExternalLoginError("that sign-in was started in another browser; start it again here")
    return row


def _profile(row, cfg, flow, code):
    """{sub, email, trusted, name} from the provider, for the code it sent back."""
    ep = _endpoints(row)
    secret = settings.unseal(cfg, row["client_secret"], _context(row["key"]))
    try:
        r = httpx.post(
            ep["token"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow["redirect_uri"],
                "client_id": row["client_id"],
                "client_secret": secret,
                "code_verifier": flow["verifier"],
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        tok = r.json()
    except (httpx.HTTPError, ValueError):
        raise ExternalLoginError("the sign-in provider didn't answer; try again") from None
    access = tok.get("access_token") if isinstance(tok, dict) else None
    if r.status_code >= 400 or not access:
        why = (tok.get("error_description") or tok.get("error")) if isinstance(tok, dict) else None
        raise ExternalLoginError(f"the sign-in provider refused: {why or r.status_code}; check the client id and secret")
    h = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
    try:
        info = httpx.get(ep["userinfo"], headers=h, timeout=TIMEOUT).raise_for_status().json() if ep.get("userinfo") else {}
        if row["kind"] == "github":
            emails = httpx.get(ep["emails"], headers=h, timeout=TIMEOUT).json()
            best = next((e for e in emails if isinstance(e, dict) and e.get("primary") and e.get("verified")), None)
            best = best or next((e for e in emails if isinstance(e, dict) and e.get("verified")), None)
            return {
                "sub": str(info.get("id") or ""),
                "email": (best or {}).get("email") or "",
                "trusted": bool(best),
                "name": info.get("name") or info.get("login"),
            }
    except (httpx.HTTPError, ValueError, TypeError):
        raise ExternalLoginError("couldn't read your profile from the sign-in provider; try again") from None
    trusted = info.get("email_verified") in (True, "true")
    if row["kind"] == "microsoft":
        trusted = (row.get("tenant") or "common").lower() not in SHARED_TENANTS
    return {
        "sub": str(info.get("sub") or ""),
        "email": info.get("email") or "",
        "trusted": trusted,
        "name": info.get("name"),
    }


def _ident(key, sub):
    return R("external_identity", auth.sha(f"{key}|{sub}"))


def _connect(db, uid, row, prof):
    db.q(
        "UPSERT $r MERGE $d",
        r=_ident(row["key"], prof["sub"]),
        d=store.clean(
            {
                "account": uid,
                "provider": row["key"],
                "subject": prof["sub"],
                "email": (prof["email"] or "").lower() or None,
                "created_at": store.now(),
                "last_used_at": store.now(),
            }
        ),
    )


def finish(db, cfg, key, flow, code):
    """Sign in (or connect, for flow mode "connect") with what the provider sent back. Returns the account."""
    row = provider(db, key)
    if flow.get("provider") != key:
        raise ExternalLoginError("that sign-in was for another provider; try again")
    if not row.get("enabled"):
        raise ExternalLoginError(f"signing in with {row.get('label')} is turned off")
    prof = _profile(row, cfg, flow, code)
    if not prof["sub"]:
        raise ExternalLoginError("the sign-in provider didn't say who you are")
    label = row.get("label") or LABELS[row["kind"]]
    known = db.one("SELECT account FROM $r", r=_ident(key, prof["sub"]))
    if flow.get("mode") == "connect":
        uid = flow.get("account")
        if known and known["account"] != uid:
            raise ExternalLoginError(f"that {label} account is already connected to another Lens account")
        u = auth.active_account(db, uid)
        if not u:
            raise ExternalLoginError("your account is disabled")
        _connect(db, uid, row, prof)
        return u
    email = (prof["email"] or "").strip().lower()
    if known:
        u = auth.active_account(db, known["account"])
        if not u:
            raise ExternalLoginError("this account is disabled; ask an admin")
    elif not email or not prof["trusted"]:
        raise ExternalLoginError(
            f"{label} didn't confirm your email address, so Lens can't tell which account is yours; sign in another way "
            f"and connect {label} in Profile and sign-in"
        )
    else:
        found = auth.find_account(db, email)
        if found:
            if found.get("disabled"):
                raise ExternalLoginError("this account is disabled; ask an admin")
            u = auth.active_account(db, found["id"])
        elif row.get("signup") and (not row.get("domains") or email.rsplit("@", 1)[-1] in row["domains"]):
            uid = auth.create_account(db, email, None, prof.get("name"))
            u = auth.active_account(db, uid)
        else:
            raise ExternalLoginError(f"no Lens account uses {email}; ask an admin to add you, then sign in with {label}")
    _connect(db, u["id"], row, prof)
    db.q("UPDATE $r SET last_login_at = $t", r=R("account", u["id"]), t=store.now())
    return u


# ---------- your outside accounts ----------
def _iid(rec):
    return auth.sha(str(rec))[:16]


def identities(db, uid):
    labels = {r["key"]: (r.get("label") or LABELS[r["kind"]], r["kind"]) for r in _rows(db)}
    rows = db.rows(
        "SELECT id, provider, email, created_at, last_used_at FROM external_identity WHERE account = $a ORDER BY created_at", a=uid
    )
    out = []
    for r in rows:
        label, kind = labels.get(r["provider"], (r["provider"], "oidc"))
        out.append(
            {
                "id": _iid(r.pop("id")),
                "provider": r["provider"],
                "label": label,
                "kind": kind,
                **{k: r.get(k) for k in ("email", "created_at", "last_used_at")},
            }
        )
    return out


def count(db, uid, but=None):
    """Outside accounts the account can sign in with (through a provider that's turned on), leaving out `but`."""
    on = {r["key"] for r in _rows(db) if r.get("enabled")}
    rows = db.rows("SELECT id, provider FROM external_identity WHERE account = $a", a=uid)
    return sum(1 for r in rows if r["provider"] in on and (but is None or _iid(r["id"]) != but))


def disconnect(db, uid, iid, other_ways):
    """Disconnect one of the account's outside accounts; refused when it's the last way in (`other_ways`: whether a
    passkey or a working password is left)."""
    for rec in db.values("SELECT VALUE id FROM external_identity WHERE account = $a", a=uid):
        if _iid(rec) == iid:
            if not other_ways and not count(db, uid, but=iid):
                raise ValueError("this is your only way to sign in: add a passkey first")
            db.q("DELETE $r", r=rec)
            return True
    return False
