"""Google's token endpoint and Calendar API, enough for tests: a service account signs in with a JWT (checked against
its public key: issuer, audience, scope, and the user it acts as) and a connected account with its refresh token; the
access tokens they get read the calendars and events the test sets up, in pages."""

from __future__ import annotations

import http.server
import json
import secrets
import threading
import urllib.parse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def service_account_key(email="lens-reader@harbour-project.iam.gserviceaccount.com"):
    """A service account key file as Google Cloud gives it, with a fresh RSA key; and that key's public half."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    key = {
        "type": "service_account",
        "project_id": "harbour-project",
        "private_key_id": "k1",
        "private_key": pem,
        "client_email": email,
        "client_id": "1234567890",
        "token_uri": "https://evil.example/token",  # never followed: Lens always signs in at Google's own
    }
    return key, private.public_key()


class Google:
    def __init__(self):
        self.service_accounts: dict[str, object] = {}  # client_email -> public key
        self.delegated: dict[str, set] = {}  # client_email -> users it may act as
        self.users: dict[str, dict] = {}  # refresh token -> {client_id, client_secret, account}
        self.tokens: dict[str, str] = {}  # access token -> account it reads as
        self.calendars: dict[str, dict] = {}  # calendar id -> {summary, timeZone, events: [...], readers: {account}}
        self.lists: dict[str, list] = {}  # account -> calendar ids in its calendar list
        self.calls: list[tuple[str, dict]] = []
        self.page = 2500
        self.expire_next = False  # answer the next API call 401, as when a token ends early
        self.throttle = False
        google = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, status=200):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _error(self, status, reason, message):
                return self._json(
                    {"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}}, status
                )

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                google.calls.append(("token", form))
                if form.get("grant_type") == "urn:ietf:params:oauth:grant-type:jwt-bearer":
                    try:
                        head = jwt.get_unverified_header(form["assertion"])
                        claims = jwt.decode(form["assertion"], options={"verify_signature": False})
                        public = google.service_accounts[claims["iss"]]
                        claims = jwt.decode(form["assertion"], public, algorithms=["RS256"], audience=google.base + "/token")
                    except (KeyError, jwt.PyJWTError) as e:
                        return self._json({"error": "invalid_grant", "error_description": f"Invalid JWT: {e}"}, 400)
                    assert head.get("kid") == "k1"
                    who = claims.get("sub") or claims["iss"]
                    if claims.get("sub") and claims["sub"] not in google.delegated.get(claims["iss"], set()):
                        return self._json(
                            {
                                "error": "unauthorized_client",
                                "error_description": "Client is unauthorized to retrieve access tokens using this method, or client not authorized for any of the scopes requested.",
                            },
                            401,
                        )
                    if claims.get("scope") != "https://www.googleapis.com/auth/calendar.readonly":
                        return self._json({"error": "invalid_scope"}, 400)
                elif form.get("grant_type") == "refresh_token":
                    u = google.users.get(form.get("refresh_token"))
                    if not u:
                        return self._json({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, 400)
                    if (form.get("client_id"), form.get("client_secret")) != (u["client_id"], u["client_secret"]):
                        return self._json({"error": "invalid_client", "error_description": "The OAuth client was not found."}, 401)
                    who = u["account"]
                else:
                    return self._json({"error": "unsupported_grant_type"}, 400)
                token = secrets.token_urlsafe(16)
                google.tokens[token] = who
                return self._json({"access_token": token, "expires_in": 3599, "token_type": "Bearer"})

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                path = urllib.parse.unquote(u.path)
                google.calls.append((path, q))
                who = google.tokens.get((self.headers.get("Authorization") or "").removeprefix("Bearer "))
                if google.expire_next:
                    google.expire_next = False
                    who = None
                if not who:
                    return self._error(401, "authError", "Invalid Credentials")
                if google.throttle:
                    return self._error(403, "rateLimitExceeded", "Rate Limit Exceeded")
                parts = path.removeprefix("/calendar/v3/").split("/")
                if parts[:3] == ["users", "me", "calendarList"]:
                    ids = google.lists.get(who, [])
                    items = [
                        {
                            "id": i,
                            "summary": google.calendars[i]["summary"],
                            "timeZone": google.calendars[i]["timeZone"],
                            "accessRole": "owner",
                            **({"primary": True} if i == who else {}),
                        }
                        for i in ids
                    ]
                    return self._json(self._paged(items, q, 250))
                if parts[0] == "calendars" and len(parts) >= 2:
                    cal = google.calendars.get(parts[1])
                    if not cal or who not in cal["readers"]:
                        return self._error(404, "notFound", "Not Found")
                    if len(parts) == 2:
                        return self._json({"id": parts[1], "summary": cal["summary"], "timeZone": cal["timeZone"]})
                    if parts[2:] == ["events"]:
                        assert q.get("singleEvents") == "false" and q.get("showDeleted") == "false"
                        items = [
                            e
                            for e in cal["events"]
                            if not q.get("timeMin")
                            or (e.get("end", {}).get("dateTime") or e.get("end", {}).get("date") or "9") >= q["timeMin"][:10]
                            or e.get("recurrence")
                        ]
                        return self._json({"summary": cal["summary"], "timeZone": cal["timeZone"], **self._paged(items, q, google.page)})
                return self._error(404, "notFound", "Not Found")

            def _paged(self, items, q, size):
                start = int(q.get("pageToken") or 0)
                size = min(size, int(q.get("maxResults") or size))
                out = {"items": items[start : start + size]}
                if start + size < len(items):
                    out["nextPageToken"] = str(start + size)
                return out

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def api_calls(self, what=None):
        return [c for c in self.calls if c[0] != "token" and (what is None or c[0].endswith(what))]

    def close(self):
        self.server.shutdown()
