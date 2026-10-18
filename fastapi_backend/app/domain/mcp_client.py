"""Lens as an MCP client: the assistant calls the tools of MCP servers an admin added (extensions.py, kind `mcp`).

A server is reached over Streamable HTTP (the MCP transport for servers on the network), and answers as JSON or as an
event stream; each use is a short session of its own: `initialize`, then `tools/list` or `tools/call`. Only admins
add servers, as they set the model's address, so the address is theirs to choose: a server on this machine or the
local network works.

A server that runs as a program (stdio: a command, usually `npx` or `uvx`) runs in the MCP runner, a container of its
own with none of Lens's data or keys (mcp-runner/runner.py, the optional `mcp` service), which Lens tells what to run
and then reaches over HTTP like any other. Without the runner, such servers aren't run.

A server whose spec says `keep` remembers things between calls (the browser: the page it has open), so its tools are
called in one session, kept from one call to the next for KEEP seconds, and opened again when the server no longer
knows it. That session is the server's, shared by everyone whose assistant uses it, as its address and token are.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import activity

VERSION = "2025-06-18"
CLIENT = {"name": "lens", "version": "1"}
MAX_REPLY = 2_000_000
MAX_TOOLS = 100
TIMEOUT = 60
KEEP = 600  # seconds a kept session waits for its next call
_ids = itertools.count(1)
_kept: dict[str, tuple[Session, float]] = {}
_lock = threading.Lock()


class McpError(ValueError):
    pass


class Refused(McpError):
    """The server turned the request away before running anything (it doesn't know the session, or the token)."""


def _events(raw):
    """The JSON messages of an event stream's `data:` lines."""
    data = []
    for line in raw.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            try:
                yield json.loads("\n".join(data))
            except ValueError:
                pass
            data = []


def _post(url, headers, body, timeout=TIMEOUT, method="POST"):
    """One request to the server (or the runner): (status, response headers, body text). Tests replace it."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (an admin's address, http or https)
            return r.status, dict(r.headers.items()), r.read(MAX_REPLY).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read(MAX_REPLY).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise McpError(f"couldn't reach the MCP server: {getattr(e, 'reason', e)}") from None


def runner():
    """The MCP runner's address and token, when it runs (LENS_MCP_RUNNER_URL, and the token it wrote to
    LENS_MCP_TOKEN_FILE); None otherwise."""
    url = os.environ.get("LENS_MCP_RUNNER_URL", "").rstrip("/")
    try:
        with open(os.environ.get("LENS_MCP_TOKEN_FILE") or "/mcp-key/token") as f:
            token = f.read().strip()
    except OSError:
        return None
    return (url, token) if url and token else None


NO_RUNNER = (
    "MCP servers that run as a program need the MCP runner, a sandbox of their own: turn it on in Extensions › Add "
    "MCP server › Command (or add COMPOSE_PROFILES=mcp to Lens's .env and run the install command again; "
    "docs/assistant.md)"
)


def reach(server):
    """Where to reach a server: its own address, or (one that runs as a program) the runner's, after telling the
    runner what to run. {url, headers}."""
    if not server.get("command"):
        return server
    r = runner()
    if not r:
        raise McpError(NO_RUNNER)
    conf = {"command": server["command"], "args": server.get("args") or [], "env": server.get("env") or {}}
    key = "s" + hashlib.sha256(json.dumps(conf, sort_keys=True).encode()).hexdigest()[:24]
    headers = {"Authorization": f"Bearer {r[1]}", "Content-Type": "application/json"}
    status, _, text = _post(f"{r[0]}/servers/{key}", headers, conf, method="PUT")
    if status >= 400:
        raise McpError(f"the MCP runner said {status}: {text[:300]}")
    return {"url": f"{r[0]}/servers/{key}/mcp", "headers": {"Authorization": headers["Authorization"]}}


class Session:
    def __init__(self, server):
        server = reach(server)
        if not str(server.get("url") or "").startswith(("http://", "https://")):
            raise McpError("an MCP server's address starts with http:// or https://")
        self.url = server["url"]
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Lens",
            **(server.get("headers") or {}),
        }
        self._send("initialize", {"protocolVersion": VERSION, "capabilities": {}, "clientInfo": CLIENT})
        self._send("notifications/initialized", None, note=True)

    def _send(self, method, params, note=False):
        body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not note:
            body["id"] = next(_ids)
        status, headers, text = _post(self.url, self.headers, body)
        low = {k.lower(): v for k, v in headers.items()}
        if low.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = low["mcp-session-id"]
        if method == "initialize":
            self.headers["MCP-Protocol-Version"] = VERSION
        if note:
            return None
        if status >= 400:
            hint = ": does it need a token in its headers?" if status in (401, 403) else ""
            raise (Refused if status in (400, 401, 403, 404) else McpError)(f"the MCP server answered {status}{hint}")
        msgs = list(_events(text)) if "text/event-stream" in low.get("content-type", "") else [json.loads(text or "null")]
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == body["id"]:
                if m.get("error"):
                    raise McpError(f"the MCP server said: {(m['error'] or {}).get('message') or m['error']}")
                return m.get("result") or {}
        raise McpError("the MCP server didn't answer")

    def tools(self):
        out, cursor = [], None
        for _ in range(20):
            got = self._send("tools/list", {"cursor": cursor} if cursor else {})
            out += [t for t in got.get("tools") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = got.get("nextCursor")
            if not cursor or len(out) >= MAX_TOOLS:
                break
        return out[:MAX_TOOLS]

    def call(self, name, args):
        return self._send("tools/call", {"name": name, "arguments": args})


def discover(server):
    """A server's tools, as an MCP extension keeps them: {name, description, schema, read_only}."""
    with activity.call("mcp.list", detail={"url": server.get("url") or server.get("command")}):
        tools = Session(server).tools()
    out = []
    for t in tools:
        schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object"}
        out.append(
            {
                "name": t["name"][:64],
                "description": str(t.get("description") or t.get("title") or t["name"])[:1024],
                "schema": schema,
                "read_only": bool((t.get("annotations") or {}).get("readOnlyHint")),
            }
        )
    return out


def _call(run, args):
    """tools/call in a new session, or, for a server that keeps its session, in the one kept for it (a new one when
    that one is too old or the server turns it away)."""
    if not run.get("keep"):
        return Session(run).call(run["tool"], args)
    where = {k: run.get(k) for k in ("url", "headers", "command", "args", "env")}
    key = hashlib.sha256(json.dumps(where, sort_keys=True, default=str).encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        for k, (_, used) in list(_kept.items()):
            if now - used > KEEP:
                del _kept[k]
        kept = _kept.get(key)
    session = kept[0] if kept else Session(run)
    try:
        got = session.call(run["tool"], args)
    except Refused:
        if not kept:
            raise
        session = Session(run)  # the server restarted or let it go: nothing ran, so once more in a new one
        got = session.call(run["tool"], args)
    with _lock:
        _kept[key] = (session, time.monotonic())
    return got


def call(run, args):
    """Call one of a server's tools: its text, and anything structured it gave back."""
    with activity.call("mcp.call", detail={"url": run.get("url") or run.get("command"), "tool": run["tool"]}):
        got = _call(run, args)
    texts = [c.get("text") for c in got.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
    out = {"text": "\n".join(t for t in texts if t)}
    if got.get("structuredContent") is not None:
        out["json"] = got["structuredContent"]
    if got.get("isError"):
        out["error"] = True
    return out
