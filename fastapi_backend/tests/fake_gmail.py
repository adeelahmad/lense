"""Google for Gmail tests: the OAuth token endpoint (codes, refresh tokens, and service accounts' signed assertions, checked
against their public keys) and enough of the Gmail API (labels, messages, history, profile). Tests point
google_auth.TOKEN_URL and gmail.API at it, and play the browser on Google's consent page with `consent`.

`messages` is {id: {raw, labels, at (ms)}}; `add` and `label` change the mailbox as mail arriving and a label put on
do, writing Gmail's history. History older than `kept_from` is gone (404), as Gmail forgets it after about a week.
`requests` keeps (method, path, query) of every request, so a test can check nothing was changed. A token with
gmail.compose (or all of Gmail) sends (messages.send: the message lands in SENT, kept in `sent`) and saves drafts
(drafts.create, in DRAFT).
"""

from __future__ import annotations

import base64
import email
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse

import jwt

GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
MAIL = "https://mail.google.com/"
LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "SENT", "name": "SENT", "type": "system"},
    {"id": "SPAM", "name": "SPAM", "type": "system"},
    {"id": "TRASH", "name": "TRASH", "type": "system"},
    {"id": "DRAFT", "name": "DRAFT", "type": "system"},
    {"id": "UNREAD", "name": "UNREAD", "type": "system"},
    {"id": "CATEGORY_PERSONAL", "name": "CATEGORY_PERSONAL", "type": "system"},
    {"id": "Label_7", "name": "Harbour", "type": "user"},
]


class Google:
    def __init__(self, client_id="lens-google", client_secret="g-secret", address="mara@harbour.example"):
        self.client_id, self.client_secret, self.address = client_id, client_secret, address
        self.labels = [dict(lab) for lab in LABELS]
        self.messages: dict[str, dict] = {}
        self.history: list[dict] = []
        self.history_id = 1000
        self.kept_from = 0
        self.requests: list[tuple[str, str, dict]] = []
        self.access: dict[str, dict] = {}  # access token -> {email, scope}
        self.refresh: dict[str, dict] = {}  # refresh token -> {email, scope, client_id}
        self.codes: dict[str, dict] = {}
        self.service_accounts: dict[str, dict] = {}  # client_email -> {public_key, scopes allowed, domain}
        self.refused_refresh = False
        self.sent: list[dict] = []  # what was sent or drafted: {id, raw, threadId, draft}
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

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                data = self.rfile.read(int(self.headers["Content-Length"])).decode()
                if path != "/token":
                    google.requests.append(("POST", path, {}))
                    who = google.access.get((self.headers.get("Authorization") or "").removeprefix("Bearer "))
                    if not who:
                        return self._json({"error": {"code": 401, "message": "Invalid Credentials"}}, 401)
                    if not ({COMPOSE, MAIL} & set(who["scope"].split())):
                        return self._json({"error": {"code": 403, "message": "Request had insufficient authentication scopes."}}, 403)
                    status, body = google.write(path.split("/gmail/v1/users/me", 1)[-1], json.loads(data))
                    return self._json(body, status)
                form = dict(urllib.parse.parse_qsl(data))
                google.requests.append(("POST", path, {"grant_type": form.get("grant_type")}))
                status, body = google.token(form)
                self._json(body, status)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(url.query)
                google.requests.append(("GET", url.path, q))
                who = google.access.get((self.headers.get("Authorization") or "").removeprefix("Bearer "))
                if not who or not ({GMAIL_READ, MAIL} & set(who["scope"].split())):
                    return self._json({"error": {"code": 401, "message": "Invalid Credentials"}}, 401)
                status, body = google.gmail(url.path.split("/gmail/v1/users/me", 1)[-1], q)
                self._json(body, status)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token_uri = self.base + "/token"
        self.api = self.base + "/gmail/v1/users/me"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # ---------- the mailbox ----------
    def _record(self, **what):
        self.history_id += 7  # history ids grow, with gaps
        self.history.append({"id": str(self.history_id), **what})
        return self.history_id

    def add(self, raw, labels=("INBOX",), at=None):
        mid = format(0x18F0000000 + len(self.messages) * 4099, "x")
        self.messages[mid] = {"raw": raw, "labels": list(labels), "at": at or int(time.time() * 1000)}
        self._record(messagesAdded=[{"message": {"id": mid, "threadId": mid, "labelIds": list(labels)}}])
        return mid

    def label(self, mid, label):
        self.messages[mid]["labels"].append(label)
        self._record(labelsAdded=[{"message": {"id": mid, "threadId": mid, "labelIds": self.messages[mid]["labels"]}, "labelIds": [label]}])

    def gmail(self, path, q):
        one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
        if path == "/labels":
            return 200, {"labels": self.labels}
        if path == "/profile":
            return 200, {"emailAddress": self.address, "messagesTotal": len(self.messages), "historyId": str(self.history_id)}
        if path == "/messages":
            label, search = one("labelIds"), one("q") or ""
            ids = [
                mid
                for mid, m in sorted(self.messages.items(), key=lambda kv: -kv[1]["at"])
                if not {"SPAM", "TRASH"} & set(m["labels"])
                and (label in m["labels"] if label else True)
                and not ("-in:drafts" in search and "DRAFT" in m["labels"])
                and not any(m["at"] <= int(w[6:]) * 1000 for w in search.split() if w.startswith("after:"))
            ]
            start, size = int(one("pageToken", "0")), int(one("maxResults", "100"))
            page = ids[start : start + size]
            body = {"messages": [{"id": i, "threadId": i} for i in page], "resultSizeEstimate": len(ids)}
            if start + size < len(ids):
                body["nextPageToken"] = str(start + size)
            return 200, body
        if path.startswith("/messages/"):
            m = self.messages.get(path.rsplit("/", 1)[1])
            if not m:
                return 404, {"error": {"code": 404, "message": "Requested entity was not found."}}
            mid = path.rsplit("/", 1)[1]
            base = {"id": mid, "threadId": mid, "labelIds": m["labels"], "internalDate": str(m["at"]), "sizeEstimate": len(m["raw"])}
            if one("format") == "raw":
                return 200, {**base, "raw": base64.urlsafe_b64encode(m["raw"]).decode().rstrip("=")}
            msg = email.message_from_bytes(m["raw"])
            want = {h.lower() for h in q.get("metadataHeaders") or []}
            headers = [{"name": k, "value": str(v)} for k, v in msg.items() if k.lower() in want]
            return 200, {**base, "payload": {"headers": headers}}
        if path == "/history":
            start = int(one("startHistoryId"))
            if start < self.kept_from:
                return 404, {"error": {"code": 404, "message": "Requested entity was not found."}}
            kinds, label = set(q.get("historyTypes") or []), one("labelId")
            out = []
            for rec in self.history:
                if int(rec["id"]) <= start:
                    continue
                keep = {}
                for kind, field in (("messageAdded", "messagesAdded"), ("labelAdded", "labelsAdded")):
                    items = [a for a in rec.get(field) or [] if kind in kinds and (not label or label in a["message"]["labelIds"])]
                    if items:
                        keep[field] = items
                if keep:
                    out.append({"id": rec["id"], **keep})
            return 200, {"history": out, "historyId": str(self.history_id)}
        return 404, {"error": {"code": 404, "message": "not here"}}

    def write(self, path, body):
        """messages.send and drafts.create: the raw message, base64url, kept and put in the mailbox."""
        draft = path == "/drafts"
        if path not in ("/messages/send", "/drafts"):
            return 404, {"error": {"code": 404, "message": "not here"}}
        m = body.get("message") if draft else body
        raw = base64.urlsafe_b64decode(m["raw"] + "=" * (-len(m["raw"]) % 4))
        mid = self.add(raw, ["DRAFT"] if draft else ["SENT"])
        thread = m.get("threadId") or mid
        self.sent.append({"id": mid, "raw": raw, "threadId": m.get("threadId"), "draft": draft})
        made = {"id": mid, "threadId": thread, "labelIds": ["DRAFT"] if draft else ["SENT"]}
        return 200, {"id": "r-" + mid, "message": made} if draft else made

    # ---------- signing in ----------
    def consent(self, sign_in_url, email_address=None, untick=()):
        """The code Google sends back once someone allows Lens at `sign_in_url` (leaving `untick` scopes unticked):
        (code, state)."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(sign_in_url).query))
        code = secrets.token_urlsafe(12)
        scope = " ".join(s for s in q["scope"].split() if s not in untick).replace(
            "email", "https://www.googleapis.com/auth/userinfo.email"
        )
        self.codes[code] = {**q, "email": email_address or self.address, "granted": scope}
        return code, q["state"]

    def _access(self, email_address, scope):
        token = "ya29." + secrets.token_urlsafe(16)
        self.access[token] = {"email": email_address, "scope": scope}
        return {"access_token": token, "expires_in": 3599, "scope": scope, "token_type": "Bearer"}

    def token(self, form):
        grant = form.get("grant_type")
        if grant == "urn:ietf:params:oauth:grant-type:jwt-bearer":
            return self._assertion(form.get("assertion") or "")
        if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return 401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}
        if grant == "authorization_code":
            c = self.codes.pop(form.get("code"), None)
            if not c or form.get("redirect_uri") != c["redirect_uri"]:
                return 400, {"error": "invalid_grant", "error_description": "Bad Request"}
            verifier = form.get("code_verifier") or ""
            if base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") != c["code_challenge"]:
                return 400, {"error": "invalid_grant", "error_description": "Missing code verifier."}
            refresh = "1//" + secrets.token_urlsafe(16)
            self.refresh[refresh] = {"email": c["email"], "scope": c["granted"], "client_id": self.client_id}
            id_token = jwt.encode(
                {"iss": "https://accounts.google.com", "email": c["email"], "email_verified": True},
                "not checked by Lens; long enough for PyJWT",
                "HS256",
            )
            return 200, {**self._access(c["email"], c["granted"]), "refresh_token": refresh, "id_token": id_token}
        if grant == "refresh_token":
            r = self.refresh.get(form.get("refresh_token"))
            if not r or self.refused_refresh:
                return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            return 200, self._access(r["email"], r["scope"])
        return 400, {"error": "unsupported_grant_type"}

    def _assertion(self, assertion):
        try:
            iss = jwt.decode(assertion, options={"verify_signature": False})["iss"]
            sa = self.service_accounts[iss]
            claims = jwt.decode(assertion, sa["public_key"], algorithms=["RS256"], audience=self.token_uri)
        except (KeyError, jwt.PyJWTError) as e:
            return 400, {"error": "invalid_grant", "error_description": f"Invalid JWT Signature. ({e})"}
        if not claims.get("sub", "").endswith("@" + sa["domain"]):
            return 400, {"error": "invalid_grant", "error_description": "Invalid email or User ID"}
        if not set(claims["scope"].split()) <= set(sa["scopes"]):
            return 401, {
                "error": "unauthorized_client",
                "error_description": "Client is unauthorized to retrieve access tokens using this method, or client not authorized for any of the scopes requested.",
            }
        return 200, self._access(claims["sub"], claims["scope"])
