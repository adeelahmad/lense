"""Access tokens (JWT) and signed media links."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass

import jwt

from app.config import settings

AUDIENCE = "lens-api"


@dataclass(frozen=True)
class AccessClaims:
    account: int
    sid: str


def create_access_token(account: int, sid: str) -> tuple[str, int]:
    """A short-lived token for one signed-in session. Returns the token and its lifetime in seconds."""
    now = int(time.time())
    ttl = settings.ACCESS_TOKEN_EXPIRE_SECONDS
    payload = {"sub": str(account), "sid": sid, "aud": AUDIENCE, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.ACCESS_SECRET_KEY, algorithm=settings.ALGORITHM), ttl


def decode_access_token(token: str) -> AccessClaims | None:
    try:
        p = jwt.decode(token, settings.ACCESS_SECRET_KEY, algorithms=[settings.ALGORITHM], audience=AUDIENCE)
        return AccessClaims(account=int(p["sub"]), sid=str(p["sid"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def _media_sig(path: str, exp: int, full: bool = False) -> str:
    what = f"media|{path}|{exp}" + ("|full" if full else "")
    mac = hmac.new(settings.ACCESS_SECRET_KEY.encode(), what.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def sign_path(path: str, ttl: int | None = None, full: bool = False, **params: str | int | float | None) -> str:
    """``path?...&exp=..&sig=..``: grants read access to exactly this path until it expires.

    Only hand these out to people who may read the resource. `full` marks a link handed to a member of the
    recording's namespace (``full=1``, signed with the rest): it opens pictures as they are where a namespace
    pixelates faces for visitors. Extra query parameters are kept but not signed.
    """
    exp = int(time.time()) + (ttl or settings.MEDIA_URL_EXPIRE_SECONDS)
    query = {k: v for k, v in params.items() if v not in (None, "")}
    if full:
        query["full"] = 1
    query.update(exp=exp, sig=_media_sig(path, exp, full))
    return f"{path}?{urllib.parse.urlencode(query)}"


def verify_path(path: str, exp: str | int | None, sig: str | None, full: bool = False) -> bool:
    try:
        e = int(exp or 0)
    except (TypeError, ValueError):
        return False
    return bool(sig) and e >= time.time() and hmac.compare_digest(_media_sig(path, e, full), str(sig))
