"""A tiny stand-in for Slack: its Web API (the methods Lens calls; what's posted lands in `posted`), files to fetch with
the bot token (`files`), slash-command response addresses (what's sent lands in `responses`), and a Socket Mode
connection (Socket) that hands out envelopes and keeps what Lens sends back, its acknowledgements."""

import http.server
import json
import queue
import threading

BOT = "xoxb-test"
APP = "xapp-test"


class Handler(http.server.BaseHTTPRequestHandler):
    posted: list = []
    responses: list = []
    files: dict = {}
    people = {"U1": "alice", "U2": "bob", "U3": "carol"}
    rooms = {"C1": "general", "D1": None}

    def log_message(self, *a):
        pass

    def _send(self, obj, status=200, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        name = self.path.rsplit("/", 1)[-1]
        if self.headers.get("Authorization") != f"Bearer {BOT}" or name not in Handler.files:
            return self._send(b"no", 404, "text/plain")
        self._send(Handler.files[name], ctype="application/octet-stream")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        auth = self.headers.get("Authorization")
        if self.path.startswith("/commands/"):
            Handler.responses.append(body)
            return self._send(b"ok", ctype="text/plain")
        if method == "apps.connections.open":
            if auth != f"Bearer {APP}":
                return self._send({"ok": False, "error": "invalid_auth"})
            return self._send({"ok": True, "url": "wss://fake.slack/link"})
        if auth != f"Bearer {BOT}":
            return self._send({"ok": False, "error": "invalid_auth"})
        if method == "auth.test":
            return self._send({"ok": True, "user_id": "UBOT", "user": "lens", "team": "Acme", "team_id": "T1"})
        if method == "users.info":
            return self._send({"ok": True, "user": {"name": Handler.people.get(body["user"], body["user"]), "profile": {}}})
        if method == "conversations.info":
            name = Handler.rooms.get(body["channel"], body["channel"])
            return self._send({"ok": True, "channel": {"name": name, "is_im": name is None}})
        if method == "chat.postMessage":
            Handler.posted.append(body)
            return self._send({"ok": True})
        self._send({"ok": False, "error": "unknown_method"})


def start():
    Handler.posted, Handler.responses, Handler.files = [], [], {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Socket:
    """The Socket Mode connection: recv() hands out what's put in `envelopes` (else times out), send() keeps what Lens
    sends."""

    def __init__(self):
        self.envelopes: queue.Queue = queue.Queue()
        self.sent: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def recv(self, timeout=None):
        try:
            return json.dumps(self.envelopes.get(timeout=timeout))
        except queue.Empty:
            raise TimeoutError from None

    def send(self, data):
        self.sent.append(json.loads(data))


_n = [0]


def event(text="", user="U1", channel="C1", channel_type="channel", **kw):
    _n[0] += 1
    ev = {"type": "message", "text": text, "user": user, "channel": channel, "channel_type": channel_type, "ts": f"{_n[0]}.0", **kw}
    return {"envelope_id": f"env-{_n[0]}", "type": "events_api", "payload": {"team_id": "T1", "event": ev}}


def command(text, user="U2", user_name="bob", channel="C1", channel_name="general", response_url="http://hooks/commands/1"):
    _n[0] += 1
    p = {"command": "/lens", "text": text, "user_id": user, "user_name": user_name, "channel_id": channel, "channel_name": channel_name}
    return {"envelope_id": f"env-{_n[0]}", "type": "slash_commands", "payload": {**p, "response_url": response_url}}
