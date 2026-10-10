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


FAKE_STDIO = r"""
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": "echo", "description": "Say it back", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]}
    elif m["method"] == "tools/call":
        if m["params"]["arguments"].get("die"):
            sys.exit(3)
        r = {"content": [{"type": "text", "text": "echo " + json.dumps(m["params"]["arguments"]) + " " + sys.argv[1]}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
"""


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """The MCP runner (mcp-runner/runner.py) on a free port, and Lens told where it is."""
    import importlib.util
    import pathlib
    import sys
    import threading

    path = pathlib.Path(__file__).resolve().parents[3] / "mcp-runner" / "runner.py"
    spec = importlib.util.spec_from_file_location("lens_mcp_runner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOME = str(tmp_path / "home")
    srv = mod.serve(0, token="secret-token")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    (tmp_path / "token").write_text("secret-token")
    monkeypatch.setenv("LENS_MCP_RUNNER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("LENS_MCP_TOKEN_FILE", str(tmp_path / "token"))
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_STDIO)
    yield {"mod": mod, "command": sys.executable, "args": ["-I", str(script), "hi"]}
    for s in mod.SERVERS.values():
        s.stop()
    mod.SERVERS.clear()
    srv.shutdown()


def test_a_server_that_runs_as_a_program_runs_in_the_runner(app, db, cfg, folder, new_client, runner, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    manifest = {"name": "echoer", "kind": "mcp", "command": runner["command"], "args": runner["args"], "env": {"X": "1"}}
    eid = ca.post("/api/v1/extensions", headers=ha, json={"manifest": manifest}).json()["id"]
    got = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert [t["name"] for t in got["spec"]["tools"]] == ["echo"] and got["spec"]["command"] == runner["command"]
    run = lambda args: ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "echoer__echo", "args": args})  # noqa: E731
    assert run({"a": 1}).json()["output"] == {"text": 'echo {"a": 1} hi'}
    # one process, initialized once, kept between uses
    (server,) = runner["mod"].SERVERS.values()
    pid = server.proc.pid
    assert run({"b": 2}).json()["output"]["text"].startswith('echo {"b": 2}') and server.proc.pid == pid
    # it stops: the answer says so, and the next use starts it again
    assert "stopped" in run({"die": True}).json()["detail"]
    assert run({"c": 3}).json()["output"]["text"].startswith('echo {"c": 3}') and server.proc.pid != pid

    # the runner wants its token
    import urllib.request

    req = urllib.request.Request(f"{runner['mod'].os.environ['LENS_MCP_RUNNER_URL']}/servers/x", data=b"{}", method="PUT")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401
    # without the runner, such a server can't be added
    monkeypatch.delenv("LENS_MCP_RUNNER_URL")
    no = ca.post("/api/v1/extensions", headers=ha, json={"manifest": {**manifest, "name": "other"}})
    assert no.status_code == 400 and "COMPOSE_PROFILES=mcp" in no.json()["detail"]
