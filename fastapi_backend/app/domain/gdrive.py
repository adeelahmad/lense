"""Google Drive connected as a Google account through a Google OAuth client of Lens's (Connect with Google,
google_auth.connect_start and connect_finish), for rclone, which reads Drive: what to ask Google for, and how the
account is kept so rclone can renew it.

rclone keeps a Google account as its own token JSON ({access_token, token_type, refresh_token, expiry}) and renews it
with the client it was given (`client_id`, `client_secret` in its config), so the source keeps those three: the token
and the client's secret sealed, the client's id as a parameter. A token pasted from `rclone authorize`, or one made by
signing in through rclone on the server (rclone_signin.py), renews with rclone's own client instead, so it comes
without one (sources.update drops a client left from before).
"""

from __future__ import annotations

import datetime as dt
import json

READONLY = "https://www.googleapis.com/auth/drive.readonly"
FULL = "https://www.googleapis.com/auth/drive"
ACCOUNT = "account"  # the parameter that names the connected account


def SCOPES(params):  # noqa: N802 (google_auth reads a module's SCOPES)
    """What Google is asked for: the connection's own scope (read only unless it was set to full)."""
    return (FULL,) if str(params.get("scope") or "").strip() == "drive" else (READONLY,)


def keep(client_id, client_secret, body, email):
    """The connected account as the source keeps it, from what Google's token endpoint answered: (params, secrets)."""
    lasts = int(body.get("expires_in") or 0)
    expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(lasts - 60, 0))
    token = {
        "access_token": body.get("access_token") or "",
        "token_type": body.get("token_type") or "Bearer",
        "refresh_token": body["refresh_token"],
        "expiry": expiry.isoformat(timespec="seconds"),
    }
    return {"client_id": client_id, ACCOUNT: email}, {"token": json.dumps(token), "client_secret": client_secret}
