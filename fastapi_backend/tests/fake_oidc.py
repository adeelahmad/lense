"""A tiny OpenID Connect provider for tests (also answers like GitHub's API): discovery, the token endpoint (checks the
client secret, redirect URI and PKCE verifier) and userinfo. Tests play the browser: they read the state and PKCE
challenge from the sign-in address Lens hands out and call `issue` for the code the provider would send back."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import urllib.parse


class Provider:
    def __init__(self, client_id="lens", client_secret="s3cret"):
        self.client_id, self.client_secret = client_id, client_secret
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.emails: list[dict] = []  # GitHub's /user/emails
        self.auto: dict | None = None  # for a browser: GET /authorize signs in as this profile straight away
        provider = self

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

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/authorize" and provider.auto:
                    code, state = provider.issue(self.path, provider.auto)
                    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                    self.send_response(302)
                    self.send_header("Location", q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": state}))
                    self.end_headers()
                    return
                if path == "/.well-known/openid-configuration":
                    return self._json(
                        {
                            "issuer": provider.base,
                            "authorization_endpoint": provider.base + "/authorize",
                            "token_endpoint": provider.base + "/token",
                            "userinfo_endpoint": provider.base + "/userinfo",
                        }
                    )
                who = provider.tokens.get((self.headers.get("Authorization") or "").removeprefix("Bearer "))
                if not who:
                    return self._json({"error": "invalid_token"}, 401)
                if path == "/user/emails":
                    return self._json(provider.emails)
                return self._json(who)

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                c = provider.codes.pop(form.get("code"), None)
                if form.get("client_id") != provider.client_id or form.get("client_secret") != provider.client_secret:
                    return self._json({"error": "invalid_client", "error_description": "bad client credentials"}, 401)
                if not c or form.get("redirect_uri") != c["redirect_uri"]:
                    return self._json({"error": "invalid_grant"}, 400)
                verifier = form.get("code_verifier") or ""
                if base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") != c["challenge"]:
                    return self._json({"error": "invalid_grant", "error_description": "PKCE check failed"}, 400)
                token = secrets.token_urlsafe(16)
                provider.tokens[token] = c["profile"]
                return self._json({"access_token": token, "token_type": "Bearer"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def issue(self, sign_in_url, profile):
        """The code the provider would send back after the person signs in at `sign_in_url` as `profile`; returns
        (code, state)."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(sign_in_url).query))
        code = secrets.token_urlsafe(12)
        self.codes[code] = {"profile": profile, "challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"]}
        return code, q["state"]

    def close(self):
        self.server.shutdown()
