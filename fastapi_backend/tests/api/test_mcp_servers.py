"""MCP servers as extensions: an admin adds one, Lens lists its tools, and the assistant calls them (a read-only one at
once, the others after a yes); a plugin's MCP servers come along when an admin installs it."""

from __future__ import annotations

import json

import pytest

from app.domain import extensions, marketplace, mcp_client
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_marketplaces import tarball

SERVER = """
name: tracker
kind: mcp
description: The issue tracker
url: https://mcp.example.com/mcp
headers: {Authorization: Bearer t0ken}
"""

TOOLS = [
    {
        "name": "search",
        "description": "Search issues",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "create-issue",
        "description": "File an issue",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
    },
]


@pytest.fixture
def server(monkeypatch):
    """A Streamable HTTP MCP server, as far as these tests go: every message it got, with its headers."""
    got = []

    def post(url, headers, body, timeout=60):
        got.append((url, dict(headers), body))
        if headers.get("Authorization") != "Bearer t0ken":
            return 401, {}, ""
        m = body["method"]
        if "id" not in body:
            return 202, {}, ""
        if m == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "t"}}
            return (
                200,
                {"Content-Type": "application/json", "Mcp-Session-Id": "s1"},
                json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}),
            )
        assert headers.get("Mcp-Session-Id") == "s1"
        if m == "tools/list":
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": TOOLS}}
            return 200, {"Content-Type": "text/event-stream"}, f"event: message\ndata: {json.dumps(msg)}\n\n"
        if m == "tools/call":
            a = body["params"]
            text = f"{a['name']}: {json.dumps(a['arguments'])}"
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}}),
            )
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "no"}}),
        )

    monkeypatch.setattr(mcp_client, "_post", post)
    return got


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_an_admin_adds_a_server_and_the_assistant_uses_it(app, db, cfg, folder, new_client, server, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    no = c.post("/api/v1/extensions", headers=h, json={"text": SERVER})
    assert no.status_code == 400 and "only admins" in no.json()["detail"]
    bad = ca.post("/api/v1/extensions", headers=ha, json={"text": SERVER.replace("t0ken", "wrong")})
    assert bad.status_code == 400 and "401" in bad.json()["detail"]

    eid = ca.post("/api/v1/extensions", headers=ha, json={"text": SERVER}).json()["id"]
    got = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert [(t["name"], t["read_only"]) for t in got["spec"]["tools"]] == [("search", True), ("create-issue", False)]
    assert "tools" not in got["manifest"] and "url: https://mcp.example.com/mcp" in got["manifest"]  # listed again on save
    # tried from the extension, like any tool
    tried = ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "tracker__search", "args": {"q": "x"}}).json()
    assert tried["output"] == {"text": 'search: {"q": "x"}'}
    ca.patch(f"/api/v1/extensions/{eid}", headers=ha, json={"visibility": "everyone"})

    llm.seen.clear()
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "tracker__search", {"q": "login bug"})]},
        {"content": "", "tool_calls": [call(2, "tracker__create-issue", {"title": "Fix login"})]},
        {"content": "Found it and asked to file one."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Any login bugs? File one."}).text)
    first = [b for b in llm.seen if b.get("tools")][0]
    search = next(t for t in first["tools"] if t["function"]["name"] == "tracker__search")["function"]
    assert search["parameters"]["properties"] == {"q": {"type": "string"}} and search["parameters"]["required"] == ["q"]
    assert ev["step"][0]["summary"] == 'Ran tracker__search(q="login bug")'
    calls = [b["params"] for _, _, b in server if b["method"] == "tools/call"]
    assert calls[-1] == {"name": "search", "arguments": {"q": "login bug"}}
    # one that changes something waits for a yes
    assert len([b for _, _, b in server if b["method"] == "tools/call"]) == 2  # the test call and the search
    assert ev["step"][1]["summary"] == 'Asked for approval: Run tracker__create-issue(title="Fix login")'


def test_a_plugins_mcp_servers_come_with_an_admin_install(app, db, cfg, folder, new_client, server, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    repo = {
        ".claude-plugin/plugin.json": json.dumps({"name": "tracker-kit", "description": "Issues"}),
        ".mcp.json": json.dumps(
            {
                "mcpServers": {
                    "tracker": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer t0ken"}},
                    "local": {"command": "npx", "args": ["x"]},
                }
            }
        ),
    }
    pages = {"https://codeload.github.com/acme/kit/tar.gz/HEAD": tarball(repo)}
    monkeypatch.setattr(marketplace, "_fetch", lambda cfg, url, out, limit: out.write(pages[url]))
    marketplace._cache.clear()

    pre = ca.post("/api/v1/extensions/import", headers=ha, json={"url": "acme/kit", "preview": True}).json()
    assert [(i["kind"], i["name"]) for i in pre["manifest"]["items"]] == [("mcp", "tracker")]
    assert [x["name"] for x in pre["skipped"]] == ["local"]
    eid = ca.post("/api/v1/extensions/import", headers=ha, json={"url": "acme/kit"}).json()["id"]
    g = extensions.get(db, eid)
    assert [t["name"] for t in extensions.tool_items(g)] == ["tracker__search", "tracker__create-issue"]
    # someone else: the servers are left out, and with nothing else in it, nothing to install
    no = c.post("/api/v1/extensions/import", headers=h, json={"url": "acme/kit", "preview": True})
    assert no.status_code == 400 and "only admins add MCP servers" in no.json()["detail"]
