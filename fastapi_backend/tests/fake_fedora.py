"""A tiny stand-in for Fedora 6's REST API: LDP resources in memory, PUT (RDF or a binary), HEAD, GET, DELETE and
tombstones, with Basic auth, and a parent that must exist before a child is made (as Fedora wants)."""

import base64
import http.server
import threading


class Handler(http.server.BaseHTTPRequestHandler):
    store: dict = {}  # path -> {"type", "body"}
    tombstones: set = set()
    seen: list = []  # (method, path)
    auth = "Basic " + base64.b64encode(b"fedoraAdmin:secret").decode()
    refuse = None  # a path to answer 409 for

    def log_message(self, *a):
        pass

    def _ok_auth(self):
        if self.headers.get("Authorization") != Handler.auth:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def _reply(self, status, body=b"", ctype=None):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        if self._ok_auth():
            Handler.seen.append(("HEAD", self.path))
            self._reply(200 if self.path in Handler.store else 404)

    def do_GET(self):
        if self._ok_auth():
            r = Handler.store.get(self.path)
            self._reply(200, r["body"], r["type"]) if r else self._reply(404)

    def do_PUT(self):
        if not self._ok_auth():
            return
        Handler.seen.append(("PUT", self.path))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        parent = self.path.rsplit("/", 1)[0]
        if self.path == Handler.refuse:
            return self._reply(409, b"Constraint violation")
        if self.path in Handler.tombstones:
            return self._reply(410)
        if parent != "/rest" and parent not in Handler.store:
            return self._reply(404, b"parent missing")
        if self.headers.get("Content-Type") == "text/turtle" and "handling=lenient" not in (self.headers.get("Prefer") or ""):
            return self._reply(412)
        new = self.path not in Handler.store
        Handler.store[self.path] = {
            "type": self.headers.get("Content-Type"),
            "body": body,
            "disposition": self.headers.get("Content-Disposition"),
        }
        self._reply(201 if new else 204)

    def do_DELETE(self):
        if not self._ok_auth():
            return
        Handler.seen.append(("DELETE", self.path))
        if self.path.endswith("/fcr:tombstone"):
            Handler.tombstones.discard(self.path[: -len("/fcr:tombstone")])
            return self._reply(204)
        if self.path not in Handler.store:
            return self._reply(404)
        for p in [p for p in Handler.store if p == self.path or p.startswith(self.path + "/")]:
            del Handler.store[p]
        Handler.tombstones.add(self.path)
        self._reply(204)


def start(cfg):
    Handler.store, Handler.tombstones, Handler.seen, Handler.refuse = {}, set(), [], None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg["fedora"] = {
        **cfg["fedora"],
        "enabled": True,
        "url": f"http://127.0.0.1:{srv.server_address[1]}/rest",
        "user": "fedoraAdmin",
        "password": "secret",
    }
    return srv
