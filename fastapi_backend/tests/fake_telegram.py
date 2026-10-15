"""A tiny stand-in for Telegram's Bot API: `updates` are handed out by getUpdates (and forgotten once a later call's
offset passes them), what's sent with sendMessage lands in `sent`, and `files` are what getFile and /file/ serve."""

import http.server
import json
import threading

TOKEN = "123456789:" + "A" * 35
BOT = {"id": 999, "is_bot": True, "first_name": "Lens", "username": "lens_test_bot"}


class Handler(http.server.BaseHTTPRequestHandler):
    updates: list = []
    sent: list = []
    files: dict = {}
    offsets: list = []
    token = TOKEN

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        prefix = f"/file/bot{Handler.token}/"
        if self.path.startswith(prefix) and self.path[len(prefix) :] in Handler.files:
            return self._send(200, Handler.files[self.path[len(prefix) :]])
        self._send(404, b"not found")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        bot, _, method = self.path.lstrip("/").partition("/")
        if bot != f"bot{Handler.token}":
            return self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        if method == "getMe":
            return self._send(200, {"ok": True, "result": BOT})
        if method == "getUpdates":
            off = body.get("offset") or 0
            Handler.offsets.append(off)
            Handler.updates = [u for u in Handler.updates if u["update_id"] >= off]
            return self._send(200, {"ok": True, "result": Handler.updates})
        if method == "sendMessage":
            Handler.sent.append(body)
            return self._send(200, {"ok": True, "result": {"message_id": len(Handler.sent)}})
        if method == "getFile":
            fid = body.get("file_id")
            if fid not in Handler.files:
                return self._send(400, {"ok": False, "description": "Bad Request: file not found"})
            return self._send(200, {"ok": True, "result": {"file_id": fid, "file_size": len(Handler.files[fid]), "file_path": fid}})
        self._send(404, {"ok": False, "description": "Not Found"})


_N = [0]


def update(text="", chat_id=42, chat_type="private", user="alice", user_id=7, title=None, **extra):
    """An update with a message, as Telegram sends it."""
    _N[0] += 1
    chat = {"id": chat_id, "type": chat_type}
    if title:
        chat["title"] = title
    msg = {
        "message_id": _N[0],
        "from": {"id": user_id, "is_bot": False, "first_name": user.title(), "username": user},
        "chat": chat,
        "date": 1760000000,
        **extra,
    }
    if text:
        msg["text"] = text
    return {"update_id": 1000 + _N[0], "message": msg}


def start():
    Handler.updates, Handler.sent, Handler.files, Handler.offsets, Handler.token = [], [], {}, [], TOKEN
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
