"""A tiny stand-in for Matterbridge's API: `waiting` is handed out (and forgotten) on GET /api/messages, what's posted
to /api/message lands in `posted`."""

import http.server
import json
import threading


class Handler(http.server.BaseHTTPRequestHandler):
    waiting = []
    posted = []
    token = "mb-token"

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self):
        if Handler.token and self.headers.get("Authorization") != f"Bearer {Handler.token}":
            self._send(401, b"Unauthorized")
            return False
        return True

    def do_GET(self):
        if not self._ok():
            return
        if self.path == "/api/health":
            return self._send(200, b"OK")
        if self.path == "/api/messages":
            out, Handler.waiting = Handler.waiting, []
            return self._send(200, out)
        self._send(404, b"not found")

    def do_POST(self):
        if not self._ok():
            return
        Handler.posted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self._send(200, {})


def said(text, username="alice", channel="general", gateway="team", account="slack.work"):
    """A message as Matterbridge relays it from a room."""
    return {
        "text": text,
        "channel": channel,
        "username": username,
        "userid": f"U-{username}",
        "account": account,
        "event": "",
        "protocol": account.split(".")[0],
        "gateway": gateway,
        "id": "",
        "timestamp": "2026-10-03T00:00:00Z",
    }


def start():
    Handler.waiting, Handler.posted, Handler.token = [], [], "mb-token"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
