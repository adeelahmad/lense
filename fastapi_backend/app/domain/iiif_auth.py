"""IIIF Authorization Flow API 2.0: the access service (a sign-in page other IIIF viewers open in a new tab), the
token service (a page in a hidden iframe that posts an access token to the viewer), the probe service (tells the
viewer whether it can play a resource, and hands out a short-lived signed link when it can) and logout.

The access cookie is SameSite=None; Secure over HTTPS, as third-party iframes need; the spec requires HTTPS anyway.
Because browsers increasingly block third-party cookies for media elements too, a successful probe answers with a
302 status and a signed link, so playback doesn't depend on the cookie.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import secrets

from . import auth, iiif, store

R = store.R
COOKIE = "la_iiif"
ORIGIN_RX = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


def _later(minutes):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def grant_cookie(db, cfg, account, origin="", raw=None):
    """A new access cookie for the viewer at `origin`, or, given a valid cookie (`raw`), that viewer added to it. The
    token service answers only the viewers a person signed in or pressed Continue for (cookie_account's `origin`)."""
    origins = [origin] if ORIGIN_RX.match(origin or "") else []
    if raw:
        r = R("iiif_cookie", auth.sha(raw))
        had = (db.one("SELECT origins FROM $r", r=r) or {}).get("origins") or []
        db.q("UPDATE $r SET origins = $o", r=r, o=list(dict.fromkeys([*had, *origins])))
        return raw
    raw = secrets.token_urlsafe(32)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("iiif_cookie", auth.sha(raw)),
        d={
            "account": account,
            "origins": origins,
            "expires_at": _later(cfg["server"]["session_hours"] * 60),
            "created_at": store.now(),
        },
    )
    return raw


def cookie_account(db, raw, origin=None):
    """The cookie's account, or None and the IIIF error profile. With `origin`, only for a viewer the person confirmed."""
    if not raw:
        return None, "missingAspect"
    row = db.one("SELECT account, expires_at, origins FROM $r", r=R("iiif_cookie", auth.sha(raw)))
    if not row:
        return None, "invalidAspect"
    if row["expires_at"] < store.now():
        return None, "expiredAspect"
    if origin is not None and origin not in (row.get("origins") or []):
        return None, "missingAspect"  # the viewer opens the access page, which asks the person to confirm this site
    u = auth.get_account(db, row["account"])
    return (auth.public(u), None) if u and not u.get("disabled") else (None, "invalidAspect")


def issue_token(db, cfg, account, origin):
    raw, minutes = secrets.token_urlsafe(32), int(cfg["iiif"].get("token_minutes") or 60)
    db.q("CREATE $r CONTENT $d", r=R("iiif_token", auth.sha(raw)), d={"account": account, "origin": origin, "expires_at": _later(minutes)})
    return raw, minutes * 60


def token_account(db, raw):
    if not raw:
        return None
    row = db.one("SELECT account, expires_at FROM $r", r=R("iiif_token", auth.sha(raw)))
    if not row or row["expires_at"] < store.now():
        return None
    u = auth.get_account(db, row["account"])
    return auth.public(u) if u and not u.get("disabled") else None


def forget(db, raw_cookie):
    acct, _ = cookie_account(db, raw_cookie)
    if raw_cookie:
        db.q("DELETE $r", r=R("iiif_cookie", auth.sha(raw_cookie)))
    if acct:
        db.q("DELETE iiif_token WHERE account = $a", a=acct["id"])


def probe_result(status, location=None, heading=None, note=None):
    out = {"@context": iiif.AUTH2, "type": "AuthProbeResult2", "status": status}
    if location:
        out["location"] = location
    if heading:
        out["heading"] = iiif.lm(heading, "en")
        out["note"] = iiif.lm(note or heading, "en")
    return out


def token_page(message, origin, nonce):
    """The token service response: a page whose script posts the message to exactly the viewer's origin."""
    msg = json.dumps(message).replace("</", "<\\/")
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>token</title></head><body>'
        f'<script nonce="{nonce}">window.parent.postMessage({msg}, {json.dumps(origin)});</script></body></html>'
    )


def access_page(site, nonce, account=None, csrf="", origin="", error="", done=False, passwords=True):
    style = (
        "body{font:15px/1.5 system-ui,sans-serif;max-width:24rem;margin:3rem auto;padding:0 1rem;color:#1d2733}"
        "label{display:block;margin:.6rem 0}input{width:100%;padding:.45rem;border:1px solid #bbb;border-radius:6px}"
        "button{margin-top:.8rem;padding:.5rem 1rem;border:0;border-radius:6px;background:#1d2733;color:#fff}.err{color:#a4262c}"
    )
    esc = html.escape
    if done:
        body = (
            f"<h1>You're signed in</h1><p>You can go back to the viewer; this tab should close by itself.</p>"
            f'<script nonce="{nonce}">window.close();</script>'
        )
    elif account:
        body = (
            f"<h1>Sign in to {esc(site)}</h1><p>You're signed in as {esc(account.get('name') or account['email'])}. "
            f"Continue to let the viewer at {esc(origin or 'another site')} play recordings you have access to.</p>"
            f'<form method="post"><input type="hidden" name="continue" value="1"><input type="hidden" name="csrf" value="{esc(csrf)}">'
            f'<input type="hidden" name="origin" value="{esc(origin)}"><button type="submit">Continue</button></form>'
        )
    elif not passwords:  # auth.passwords off: this page has no passkey sign-in yet, so there is nothing to submit
        body = (
            f"<h1>Sign in to {esc(site)}</h1><p>The viewer at {esc(origin or 'another site')} wants to play recordings that need an account.</p>"
            + (f'<p class="err">{esc(error)}</p>' if error else "")
            + f"<p>{esc(site)} signs people in with a passkey only, and this page can't use passkeys yet, so this viewer "
            "can only play what is open to everyone.</p>"
        )
    else:
        body = (
            f"<h1>Sign in to {esc(site)}</h1><p>The viewer at {esc(origin or 'another site')} wants to play recordings that need an account.</p>"
            + (f'<p class="err">{esc(error)}</p>' if error else "")
            + f'<form method="post"><input type="hidden" name="origin" value="{esc(origin)}">'
            + (f'<input type="hidden" name="csrf" value="{esc(csrf)}">' if csrf else "")
            + '<label>Email <input name="email" type="email" autocomplete="username" required></label>'
            '<label>Password <input name="password" type="password" autocomplete="current-password" required></label>'
            '<button type="submit">Sign in</button></form>'
        )
    return f'<!doctype html><html><head><meta charset="utf-8"><title>Sign in to {esc(site)}</title><style>{style}</style></head><body>{body}</body></html>'
