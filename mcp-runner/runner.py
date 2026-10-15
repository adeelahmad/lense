"""Lens's MCP runner: MCP servers that run as a program (stdio), started in this container and reached by Lens over
Streamable HTTP (docs/assistant.md, fastapi_backend/app/domain/mcp_client.py).

This container is the sandbox: it has none of Lens's data, keys or database, runs as an unprivileged user with every
capability dropped, and shares a network only with Lens's backend (and the internet, which `npx` and `uvx` need to
fetch the servers). Each server gets an empty folder of its own as its home, and only the environment it was given.

API (each request carries `Authorization: Bearer <token>`, the token this runner writes to $LENS_MCP_TOKEN_FILE,
which Lens reads from the volume the two share):

- PUT /servers/<key> {command, args, env}: what to run under that key. The same again is a no-op; different settings
  stop the old process.
- POST /servers/<key>/mcp: one JSON-RPC message, MCP's Streamable HTTP without streaming. The process starts on first
  use (and again if it stopped) and is initialized once; a client's `initialize` gets that answer. Requests are passed
  through with ids of the runner's own; notifications get 202.
- GET /health.

Processes idle for IDLE seconds stop. Standard library only.
"""

from __future__ import annotations

import collections
import hmac
import itertools
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2025-06-18"
HOME = os.environ.get("LENS_MCP_HOME", "/data")
TOKEN_FILE = os.environ.get("LENS_MCP_TOKEN_FILE", "/key/token")
PORT = int(os.environ.get("LENS_MCP_PORT", "8090"))
START_SECONDS = 180  # a first `npx -y` downloads the server
CALL_SECONDS = 120
IDLE = 900
MAX_SERVERS = 30
MAX_BODY = 4_000_000
KEY_RX = re.compile(r"^[a-z0-9_-]{1,64}$")
BASE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "LANG": "C.UTF-8",
    "NODE_ENV": "production",
}


class Server:
    """One stdio server process, initialized once, with requests matched to their answers by id."""

    def __init__(self, key, conf):
        self.key, self.conf = key, conf
        self.proc = None
        self.init = None
        self.lock = threading.Lock()
        self.wlock = threading.Lock()
        self.waiting: dict[int, tuple[threading.Event, list]] = {}
        self.ids = itertools.count(1)
        self.err = collections.deque(maxlen=40)
        self.used = time.monotonic()

    def _start(self):
        home = os.path.join(HOME, self.key)
        os.makedirs(home, exist_ok=True)
        env = {
            **BASE_ENV,
            "HOME": home,
            "TMPDIR": home,
            **{k: str(v) for k, v in (self.conf.get("env") or {}).items()},
        }
        cmd = [self.conf["command"], *[str(a) for a in self.conf.get("args") or []]]
        if not shutil.which(cmd[0], path=env["PATH"]):
            raise RuntimeError(
                f"{cmd[0]} isn't installed in the MCP runner (it has node, npx, python3, uv and uvx)"
            )
        self.err.clear()
        self.proc = subprocess.Popen(
            cmd,
            cwd=home,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._errors, args=(self.proc,), daemon=True).start()
        got = self._request(
            "initialize",
            {
                "protocolVersion": VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lens", "version": "1"},
            },
            START_SECONDS,
        )
        if "error" in got:
            raise RuntimeError(
                f"the server didn't start: {got['error'].get('message')}"
            )
        self.init = got["result"]
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _write(self, msg):
        with self.wlock:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if (
                "method" in msg and "id" in msg
            ):  # the server asks something of the client: ping, roots, sampling
                answer = (
                    {"result": {}}
                    if msg["method"] == "ping"
                    else {"result": {"roots": []}}
                    if msg["method"] == "roots/list"
                    else {"error": {"code": -32601, "message": "not supported"}}
                )
                try:
                    self._write({"jsonrpc": "2.0", "id": msg["id"], **answer})
                except OSError:
                    pass
                continue
            hit = (
                self.waiting.pop(msg.get("id"), None)
                if isinstance(msg.get("id"), int)
                else None
            )
            if hit:
                hit[1].append(msg)
                hit[0].set()
        for ev, _ in list(self.waiting.values()):
            ev.set()

    def _errors(self, proc):
        for line in proc.stderr:
            self.err.append(line.decode("utf-8", "replace").rstrip())

    def _request(self, method, params, seconds=CALL_SECONDS):
        rid = next(self.ids)
        ev, box = threading.Event(), []
        self.waiting[rid] = (ev, box)
        self._write(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                **({"params": params} if params is not None else {}),
            }
        )
        if not ev.wait(seconds):
            self.waiting.pop(rid, None)
            raise RuntimeError(
                f"the server didn't answer {method} within {seconds} seconds"
            )
        if not box:
            tail = " / ".join(list(self.err)[-5:])
            raise RuntimeError("the server stopped" + (f": {tail}" if tail else ""))
        return box[0]

    def ready(self):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.stop()
                try:
                    self._start()
                except Exception:
                    self.stop()
                    raise
            self.used = time.monotonic()

    def handle(self, msg):
        """One client message: its answer (None for a notification)."""
        self.ready()
        if "id" not in msg:
            if msg.get("method") != "notifications/initialized":
                self._write(msg)
            return None
        if msg.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": msg["id"], "result": self.init}
        got = self._request(msg.get("method"), msg.get("params"))
        self.used = time.monotonic()
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            **{k: got[k] for k in ("result", "error") if k in got},
        }

    def stop(self):
        p, self.proc, self.init = self.proc, None, None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, 15)
                p.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, 9)
                except OSError:
                    pass


SERVERS: dict[str, Server] = {}
LOCK = threading.Lock()


def check_conf(conf):
    if (
        not isinstance(conf, dict)
        or not isinstance(conf.get("command"), str)
        or not conf["command"].strip()
    ):
        raise ValueError("a server is {command, args, env}")
    args, env = conf.get("args") or [], conf.get("env") or {}
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args are a list of words")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env is names and text values")
    return {"command": conf["command"].strip(), "args": args, "env": env}


def reap():
    while True:
        time.sleep(60)
        with LOCK:
            for s in SERVERS.values():
                if s.proc and time.monotonic() - s.used > IDLE:
                    with s.lock:
                        s.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = "lens-mcp-runner"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        given = self.headers.get("Authorization", "")
        return hmac.compare_digest(
            given.encode(), f"Bearer {self.server.token}".encode()
        )

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"null")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "servers": len(SERVERS)})
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        m = re.fullmatch(r"/servers/([^/]+)", self.path)
        if not self._allowed():
            return self._send(401, {"error": "unauthorized"})
        if not m or not KEY_RX.match(m[1]):
            return self._send(404, {"error": "not found"})
        try:
            conf = check_conf(self._body())
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        with LOCK:
            s = SERVERS.get(m[1])
            if s and s.conf == conf:
                return self._send(200, {"ok": True})
            if not s and len(SERVERS) >= MAX_SERVERS:
                idle = sorted(
                    (x for x in SERVERS.values() if not x.proc), key=lambda x: x.used
                )
                if not idle:
                    return self._send(
                        409,
                        {"error": f"the runner keeps at most {MAX_SERVERS} servers"},
                    )
                del SERVERS[idle[0].key]
            if s:
                with s.lock:
                    s.stop()
            SERVERS[m[1]] = Server(m[1], conf)
        self._send(200, {"ok": True})

    def do_POST(self):
        m = re.fullmatch(r"/servers/([^/]+)/mcp", self.path)
        if not self._allowed():
            return self._send(401, {"error": "unauthorized"})
        s = SERVERS.get(m[1]) if m else None
        if not s:
            return self._send(404, {"error": "no such server: Lens sets it up first"})
        try:
            msg = self._body()
        except ValueError:
            return self._send(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "not JSON"},
                },
            )
        if not isinstance(msg, dict):
            return self._send(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "one message at a time"},
                },
            )
        try:
            out = s.handle(msg)
        except (
            RuntimeError,
            OSError,
            ValueError,
            KeyError,
        ) as e:  # it wouldn't start or answer: say why, as MCP does
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)[:2000]},
                },
            )
        if out is None:
            return self._send(202)
        self._send(200, out, {"Mcp-Session-Id": s.key})


def serve(port=PORT, token=None):
    """The runner on `port` (0: any); returns the server (its .token, .server_address)."""
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    srv.token = token or secrets.token_urlsafe(32)
    return srv


def main():
    srv = serve()
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    tmp = TOKEN_FILE + ".new"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w") as f:
        f.write(srv.token)
    os.replace(tmp, TOKEN_FILE)
    threading.Thread(target=reap, daemon=True).start()
    print(f"MCP runner on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
