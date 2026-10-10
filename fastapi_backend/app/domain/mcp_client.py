"""Lens as an MCP client: the assistant calls the tools of MCP servers an admin added (extensions.py, kind `mcp`).

A server is reached over Streamable HTTP (the MCP transport for servers on the network), and answers as JSON or as an
event stream; each use is a short session of its own: `initialize`, then `tools/list` or `tools/call`. Servers that
run as a program on this computer (stdio) aren't run. Only admins add servers, as they set the model's address, so
the address is theirs to choose: a server on this machine or the local network works.
"""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request

from . import activity

VERSION = "2025-06-18"
CLIENT = {"name": "lens", "version": "1"}
MAX_REPLY = 2_000_000
MAX_TOOLS = 100
TIMEOUT = 60
_ids = itertools.count(1)


class McpError(ValueError):
    pass


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


def _post(url, headers, body, timeout=TIMEOUT):
    """One POST to the server: (status, response headers, body text). Tests replace it."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (an admin's address, http or https)
            return r.status, dict(r.headers.items()), r.read(MAX_REPLY).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read(MAX_REPLY).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise McpError(f"couldn't reach the MCP server: {getattr(e, 'reason', e)}") from None


class Session:
    def __init__(self, server):
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
            raise McpError(f"the MCP server answered {status}{hint}")
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
    with activity.call("mcp.list", detail={"url": server.get("url")}):
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


def call(run, args):
    """Call one of a server's tools: its text, and anything structured it gave back."""
    with activity.call("mcp.call", detail={"url": run["url"], "tool": run["tool"]}):
        got = Session(run).call(run["tool"], args)
    texts = [c.get("text") for c in got.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
    out = {"text": "\n".join(t for t in texts if t)}
    if got.get("structuredContent") is not None:
        out["json"] = got["structuredContent"]
    if got.get("isError"):
        out["error"] = True
    return out
