"""A Vaultwarden server for tests (vaultwarden_secrets.py), on loopback, encrypting its items the way Bitwarden does:
``POST /identity/connect/token`` signs an API key in (client_credentials) and hands over the account's user key
wrapped by the master key, and ``GET /api/sync`` answers the folders and items, each field encrypted with the user key
(or an item's own key, for one that has one). The library's own crypto (python-vaultwarden) encrypts here, so the test
proves Lens decrypts what a real vault sends. Every request is kept in `seen`."""

from __future__ import annotations

import http.server
import json
import secrets
import threading
import urllib.parse
import uuid

from vaultwarden.models.bitwarden import Kdf, KdfType
from vaultwarden.utils.crypto import encrypt_sym, make_master_key

EMAIL = "lens@vault.example"
PASSWORD = "correct horse battery staple"
CLIENT_ID = "user.3f4c8f3e-1111-4f5e-9d2a-0a1b2c3d4e5f"
CLIENT_SECRET = "ApiKeySecretForTests0123456789"
ITERATIONS = 5000  # Bitwarden uses 600000; the test only needs the same number on both sides


class Vault:
    """The vault's contents: folders by name, and items (type 1 login, 2 note, 3 card) in folders."""

    def __init__(self):
        self.user_key = secrets.token_bytes(64)
        self.master_key = make_master_key(PASSWORD, EMAIL, Kdf(KdfType.Pbkdf2, ITERATIONS))
        self.folders = {"Lens": str(uuid.uuid4()), "Personal": str(uuid.uuid4())}
        self.items = []
        self.seen = []
        self.refuse = None  # an HTTP status the token endpoint answers with instead

    def enc(self, text, key=None):
        return encrypt_sym(text, key or self.user_key) if text is not None else None

    def add(self, name, folder="Lens", type=1, own_key=False, fields=None, notes=None, **login):
        """A login (username, password, totp, uri), note or card in a folder; own_key wraps it with a key of its own."""
        key = secrets.token_bytes(64) if own_key else None

        def e(t):
            return self.enc(t, key)

        item = {
            "object": "cipherDetails",
            "id": str(uuid.uuid4()),
            "organizationId": None,
            "folderId": self.folders[folder] if folder else None,
            "type": type,
            "name": e(name),
            "notes": e(notes),
            "favorite": False,
            "reprompt": 0,
            "revisionDate": "2026-10-17T09:00:00Z",
            "deletedDate": None,
            "key": self.enc(key) if key else None,
            "fields": [{"name": e(n), "value": e(v), "type": 1, "linkedId": None} for n, v in (fields or {}).items()],
            "collectionIds": [],
            "passwordHistory": None,
            "attachments": None,
        }
        if type == 1:
            item["login"] = {
                "username": e(login.get("username")),
                "password": e(login.get("password")),
                "totp": e(login.get("totp")),
                "uris": [{"uri": e(login["uri"]), "match": None}] if login.get("uri") else None,
                "passwordRevisionDate": None,
            }
        elif type == 2:
            item["secureNote"] = {"type": 0}
        elif type == 3:
            item["card"] = {"cardholderName": e(login.get("holder")), "number": e(login.get("number")), "code": e(login.get("code"))}
        self.items.append(item)
        return item

    def token(self):
        return {
            "access_token": "token-" + secrets.token_hex(8),
            "expires_in": 3600,
            "token_type": "Bearer",
            "refresh_token": "refresh-" + secrets.token_hex(8),
            "scope": "api",
            "Kdf": 0,
            "KdfIterations": ITERATIONS,
            "KdfMemory": None,
            "KdfParallelism": None,
            "Key": self.enc(self.user_key, self.master_key),
            "PrivateKey": self.enc(b"not a real key: no organizations here"),
            "ForcePasswordReset": False,
            "ResetMasterPassword": False,
            "unofficialServer": True,
        }

    def sync(self):
        return {
            "object": "sync",
            "profile": {
                "object": "profile",
                "id": str(uuid.uuid4()),
                "name": "Lens",
                "email": EMAIL,
                "emailVerified": True,
                "premium": False,
                "masterPasswordHint": None,
                "culture": "en-US",
                "twoFactorEnabled": False,
                "key": self.enc(self.user_key, self.master_key),
                "privateKey": self.enc(b"not a real key"),
                "securityStamp": "stamp",
                "organizations": [],
                "providers": [],
                "providerOrganizations": [],
                "forcePasswordReset": False,
                "avatarColor": None,
                "_status": 0,
            },
            "folders": [
                {"object": "folder", "id": fid, "name": self.enc(name), "revisionDate": "2026-10-17T09:00:00Z"}
                for name, fid in self.folders.items()
            ],
            "collections": [],
            "ciphers": self.items,
            "domains": None,
            "policies": [],
            "sends": [],
        }


class Handler(http.server.BaseHTTPRequestHandler):
    vault: Vault

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        form = dict(urllib.parse.parse_qsl(raw))
        self.vault.seen.append({"method": "POST", "path": self.path, "form": {k: v for k, v in form.items() if k != "client_secret"}})
        if self.path != "/identity/connect/token":
            return self._send(404, {"error": "not found"})
        if self.vault.refuse:
            return self._send(self.vault.refuse, {"error": "refused"})
        if form.get("grant_type") == "refresh_token":
            return self._send(200, self.vault.token())
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return self._send(400, {"error": "invalid_client", "ErrorModel": {"Message": "client_id or client_secret is incorrect"}})
        return self._send(200, self.vault.token())

    def do_GET(self):  # noqa: N802
        self.vault.seen.append({"method": "GET", "path": self.path, "auth": bool(self.headers.get("Authorization"))})
        if not (self.headers.get("Authorization") or "").startswith("Bearer token-"):
            return self._send(401, {"error": "no"})
        if self.path.startswith("/api/sync"):
            return self._send(200, self.vault.sync())
        return self._send(404, {"error": "not found"})


def server():
    """A Vaultwarden server on loopback: (vault, its address). Stop it with vault.srv.shutdown()."""
    vault = Vault()
    handler = type("VaultHandler", (Handler,), {"vault": vault})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    vault.srv = srv
    return vault, f"http://127.0.0.1:{srv.server_address[1]}"
