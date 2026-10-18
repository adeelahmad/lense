"""MCP servers as extensions: an admin adds one (by a manifest, or a config pasted from another app), Lens lists its
tools, and the assistant calls them (a read-only one at once, the others after a yes, each switched on or off); its
tokens are sealed and never shown; a plugin's MCP servers come along when an admin installs it."""

from __future__ import annotations

import json

import pytest

from app.domain import extensions, marketplace, mcp_client, mcp_servers
from app.domain.store import R
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
import json, os, sys
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
        said = "echo " + json.dumps(m["params"]["arguments"]) + " " + sys.argv[1] + os.environ.get("API_KEY", "")
        r = {"content": [{"type": "text", "text": said}]}
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


def test_a_programs_keys_are_sealed_and_go_to_that_program_only(app, db, cfg, folder, new_client, runner):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    manifest = {"name": "keyed", "kind": "mcp", "command": runner["command"], "args": runner["args"], "env": {"API_KEY": "-k3y", "X": "1"}}
    eid = ca.post("/api/v1/extensions", headers=ha, json={"manifest": manifest}).json()["id"]
    env = extensions.get(db, eid)["spec"]["env"]
    assert mcp_servers.is_sealed(env["API_KEY"]) and env["X"] == "1"
    got = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert got["spec"]["env"] == {"API_KEY": "••••", "X": "1"}
    tried = ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "keyed__echo", "args": {}})
    assert tried.json()["output"] == {"text": "echo {} hi-k3y"}  # the program got it, opened
    # another package is another program: the key isn't handed on
    other = {**manifest, "args": [*runner["args"][:-1], "other"], "env": {"API_KEY": "••••", "X": "1"}}
    no = ca.post(f"/api/v1/extensions/{eid}/versions", headers=ha, json={"manifest": other})
    assert no.status_code == 400 and "goes somewhere else" in no.json()["detail"]


def test_its_secrets_are_sealed_and_never_shown(app, db, cfg, folder, new_client, server):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    eid = ca.post("/api/v1/extensions", headers=ha, json={"text": SERVER}).json()["id"]
    kept = extensions.get(db, eid)["spec"]["headers"]["Authorization"]
    assert mcp_servers.is_sealed(kept) and "t0ken" not in kept
    got = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert got["spec"]["headers"] == {"Authorization": "••••"} and "t0ken" not in got["manifest"]
    assert "Authorization: ••••" in got["manifest"]
    assert "t0ken" not in json.dumps(ca.get("/api/v1/extensions", headers=ha).json())
    # opened only to reach the server
    tried = ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "tracker__search", "args": {"q": "x"}})
    assert tried.json()["output"] == {"text": 'search: {"q": "x"}'}

    # saving the manifest as shown keeps the token, and its tools are listed again with it
    server.clear()
    saved = ca.post(f"/api/v1/extensions/{eid}/versions", headers=ha, json={"text": got["manifest"], "notes": "again"})
    assert saved.status_code == 200, saved.text
    assert any(b["method"] == "tools/list" for _, _, b in server)
    assert extensions.get(db, eid)["spec"]["headers"]["Authorization"] != kept  # sealed again: the same token
    again = ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "tracker__search", "args": {"q": "y"}})
    assert again.status_code == 200
    # but not to another host, nor into another extension
    moved = got["manifest"].replace("mcp.example.com", "evil.example.net")
    no = ca.post(f"/api/v1/extensions/{eid}/versions", headers=ha, json={"text": moved})
    assert no.status_code == 400 and "goes somewhere else" in no.json()["detail"]
    new = ca.post("/api/v1/extensions", headers=ha, json={"text": got["manifest"].replace("name: tracker", "name: copy")})
    assert new.status_code == 400 and "enter the Authorization value of copy" in new.json()["detail"]
    pasted = SERVER.replace("name: tracker", "name: copy").replace("Bearer t0ken", kept)
    no = ca.post("/api/v1/extensions", headers=ha, json={"text": pasted})
    assert no.status_code == 400 and "sealed for another extension" in no.json()["detail"]
    # a header that isn't a credential stays as it is
    headers = {"Authorization": "Bearer t0ken", "X-Team": "blue"}
    other = {"name": "plain", "kind": "mcp", "url": "https://mcp.example.com/mcp", "headers": headers}
    pid = ca.post("/api/v1/extensions", headers=ha, json={"manifest": other}).json()["id"]
    assert extensions.get(db, pid)["spec"]["headers"]["X-Team"] == "blue"
    assert ca.get(f"/api/v1/extensions/{pid}", headers=ha).json()["spec"]["headers"] == {"Authorization": "••••", "X-Team": "blue"}


def test_upgrading_seals_tokens_saved_before(app, db, cfg, folder, new_client, server):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    eid = ca.post("/api/v1/extensions", headers=ha, json={"text": SERVER}).json()["id"]
    plain = {**extensions.get(db, eid)["spec"], "headers": {"Authorization": "Bearer t0ken"}}
    db.q("UPDATE $r SET spec = $s", r=R("extension_version", f"{eid}-1"), s=plain)  # as an older Lens kept it
    mcp_servers.migrate_seal(db)
    kept = extensions.get(db, eid)["spec"]["headers"]["Authorization"]
    assert mcp_servers.is_sealed(kept)
    mcp_servers.migrate_seal(db)  # twice: nothing changes
    assert extensions.get(db, eid)["spec"]["headers"]["Authorization"] == kept
    tried = ca.post(f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "tracker__search", "args": {"q": "x"}})
    assert tried.json()["output"] == {"text": 'search: {"q": "x"}'}


def test_each_tool_is_switched_and_says_whether_it_asks(app, db, cfg, folder, new_client, server, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    eid = ca.post("/api/v1/extensions", headers=ha, json={"text": SERVER}).json()["id"]
    ca.patch(f"/api/v1/extensions/{eid}", headers=ha, json={"visibility": "everyone"})

    def effects():
        return {t["name"]: t["spec"]["effect"] for t in extensions.tool_items(extensions.get(db, eid))}

    def switch(body):
        return ca.patch(f"/api/v1/extensions/{eid}/tools", headers=ha, json=body)

    assert effects() == {"tracker__search": "read", "tracker__create-issue": "change"}  # as the server says
    assert switch({"tool": "search", "ask": True}).json()["version"] == 2
    assert switch({"tool": "create-issue", "on": False}).json()["version"] == 3
    assert effects() == {"tracker__search": "change"}
    got = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert got["spec"]["off"] == ["create-issue"] and got["spec"]["ask"] == {"search": True}
    assert got["history"][0]["notes"] == "create-issue off"
    # they stay when its tools are listed again on saving its manifest
    ca.post(f"/api/v1/extensions/{eid}/versions", headers=ha, json={"text": got["manifest"]})
    assert effects() == {"tracker__search": "change"}
    # back as the server says: not written down
    switch({"tool": "search", "ask": False})
    switch({"tool": "create-issue", "on": True, "ask": False})
    assert effects() == {"tracker__search": "read", "tracker__create-issue": "read"}
    assert extensions.get(db, eid)["spec"]["ask"] == {"create-issue": False}
    assert switch({"tool": "nope", "on": False}).status_code == 400
    assert c.patch(f"/api/v1/extensions/{eid}/tools", headers=h, json={"tool": "search", "on": False}).status_code == 403

    # in a conversation: a tool switched off isn't offered, and one set to ask first waits for a yes
    switch({"tool": "create-issue", "on": False})
    switch({"tool": "search", "ask": True})
    llm.seen.clear()
    llm.tool_script = [{"content": "", "tool_calls": [call(1, "tracker__search", {"q": "login"})]}, {"content": "Asked."}]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Any login bugs?"}).text)
    names = [t["function"]["name"] for t in [b for b in llm.seen if b.get("tools")][0]["tools"]]
    assert "tracker__search" in names and "tracker__create-issue" not in names
    assert ev["step"][0]["summary"] == 'Asked for approval: Run tracker__search(q="login")'


CLAUDE = {
    "mcpServers": {
        "tracker": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer ${TRACKER_TOKEN}"}},
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "${GH}"}},
        "old": {"type": "sse", "url": "https://old.example.com/sse"},
    }
}


def test_a_pasted_config_reads_as_servers_to_add(app, db, cfg, folder, new_client, server):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]

    def read(text):
        return ca.post("/api/v1/extensions/mcp/read", headers=ha, json={"text": text})

    got = read(json.dumps(CLAUDE)).json()
    assert [(x["name"], x["fill"]) for x in got["servers"]] == [("tracker", ["TRACKER_TOKEN"]), ("github", ["GH"])]
    github = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "${GH}"}}
    assert got["servers"][1]["spec"] == github
    assert [x["name"] for x in got["skipped"]] == ["old"] and "SSE" in got["skipped"][0]["why"]
    # VS Code's, a list, and one server on its own, its name guessed
    vscode = {"servers": {"fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]}}}
    assert [x["name"] for x in read(json.dumps(vscode)).json()["servers"]] == ["fetch"]
    one = read('{"command": "npx", "args": ["@playwright/mcp@latest", "--headless"]}').json()["servers"]
    assert [x["name"] for x in one] == ["playwright"]
    two = read(json.dumps([{"url": "https://mcp.linear.app/mcp"}, {"url": "https://mcp.linear.app/other"}])).json()["servers"]
    assert [x["name"] for x in two] == ["linear", "linear_2"]
    for bad in ('{"mcpServers": {"x": ', "hello there", "[]"):
        assert read(bad).status_code == 400

    # one is tried, then added with its token filled in
    first = {**got["servers"][0]["spec"], "headers": {"Authorization": "Bearer t0ken"}}
    tried = ca.post("/api/v1/extensions/mcp/test", headers=ha, json={"spec": first}).json()
    assert [t["name"] for t in tried["tools"]] == ["search", "create-issue"]
    eid = ca.post("/api/v1/extensions", headers=ha, json={"manifest": {"name": "tracker", "kind": "mcp", **first}}).json()["id"]
    # tried again as saved, its token as ••••
    shown = {**first, "headers": {"Authorization": "••••"}}
    again = ca.post("/api/v1/extensions/mcp/test", headers=ha, json={"spec": shown, "extension": eid})
    assert again.status_code == 200 and len(again.json()["tools"]) == 2
    no = ca.post("/api/v1/extensions/mcp/test", headers=ha, json={"spec": shown})
    assert no.status_code == 400 and "there is none" in no.json()["detail"]
    c, h = s.cl["editor"]
    no = c.post("/api/v1/extensions/mcp/test", headers=h, json={"spec": first})
    assert no.status_code == 400 and "only admins" in no.json()["detail"]


@pytest.mark.parametrize(
    "server, name",
    [
        ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}, "github"),
        ({"command": "uvx", "args": ["mcp-server-fetch"]}, "fetch"),
        ({"command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"]}, "github"),
        ({"command": "npx", "args": ["@playwright/mcp@latest"]}, "playwright"),
        ({"url": "https://api.githubcopilot.com/mcp/"}, "githubcopilot"),
        ({"url": "http://localhost:8931/mcp"}, "localhost"),
    ],
)
def test_a_server_without_a_name_gets_one(server, name):
    assert mcp_servers.guess_name(server) == name


BROWSER_TOOLS = [
    {"name": "browser_navigate", "description": "Go to a page", "inputSchema": {"type": "object"}},
    {"name": "browser_snapshot", "description": "Read the page", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "browser_click", "description": "Click", "inputSchema": {"type": "object"}},
    {"name": "browser_run_code_unsafe", "description": "Run code", "inputSchema": {"type": "object"}},
]


def test_the_browser_preset_keeps_one_session(app, db, cfg, folder, new_client, monkeypatch):
    """The browser, added with a click from its preset: its calls share a session (the page stays open), opened again
    when the server lets it go; running code in its server process starts off, and going to a page asks only once the
    conversation has read outside text."""
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    assert c.get("/api/v1/extensions/mcp/presets", headers=h).status_code == 403
    got = {p["name"]: p for p in ca.get("/api/v1/extensions/mcp/presets", headers=ha).json()["presets"]}
    b, gh = got["browser"], got["github"]
    assert (b["needs"], b["service"]["profile"], b["spec"]["url"]) == ("browser", "browser", "http://browser:8931/mcp")
    assert b["spec"]["keep"] is True and b["spec"]["off"] == ["browser_run_code_unsafe"]
    assert (gh["fill"], gh["service"], gh["spec"]["headers"]) == (["GITHUB_TOKEN"], None, {"Authorization": "Bearer ${GITHUB_TOKEN}"})

    sessions, gone, calls = [], set(), []

    def post(url, headers, body, timeout=60):
        if "id" not in body:
            return 202, {}, ""
        if body["method"] == "initialize":
            sessions.append(f"s{len(sessions) + 1}")
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "pw"}}
            reply = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            return 200, {"Content-Type": "application/json", "Mcp-Session-Id": sessions[-1]}, reply
        sid = headers.get("Mcp-Session-Id")
        if sid in gone:
            return 404, {}, "Session not found"
        if body["method"] == "tools/list":
            result = {"tools": BROWSER_TOOLS}
        else:
            calls.append((sid, body["params"]["name"]))
            result = {"content": [{"type": "text", "text": f"{sid} {body['params']['name']}"}]}
        return 200, {"Content-Type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(mcp_client, "_post", post)
    monkeypatch.setattr(mcp_client, "_kept", {})
    manifest = {"name": b["name"], "kind": "mcp", **b["spec"]}
    eid = ca.post("/api/v1/extensions", headers=ha, json={"manifest": manifest}).json()["id"]
    tools = {t["name"]: t for t in extensions.tool_items(extensions.get(db, eid))}
    assert sorted(tools) == ["browser__browser_click", "browser__browser_navigate", "browser__browser_snapshot"]
    effects = [tools[f"browser__browser_{n}"]["spec"]["effect"] for n in ("navigate", "snapshot", "click")]
    assert effects == ["read", "read", "change"]

    def use(name):
        return mcp_client.call(mcp_servers.open_run(db, tools[f"browser__{name}"]["spec"]["run"]), {})["text"]

    first = use("browser_navigate").split()[0]
    assert use("browser_snapshot").split()[0] == first  # the same session: the page it opened
    gone.add(first)  # the server restarted: a new session, and the call made once more
    assert use("browser_snapshot").split()[0] != first
    assert [n for _, n in calls].count("browser_snapshot") == 2
    # a server that doesn't keep its session gets a new one each call, as before
    monkeypatch.setattr(mcp_client, "_kept", {})
    plain = {**tools["browser__browser_snapshot"]["spec"]["run"], "keep": None}
    assert len({mcp_client.call(plain, {})["text"].split()[0] for _ in range(2)}) == 2 and not mcp_client._kept


def test_agents_list_and_add_servers(app, db, cfg, folder, new_client, server, monkeypatch):
    """Over MCP, an admin's agent lists the servers and the ready-made ones, and adds one: its own, or GitHub with its
    token typed in, checked as the web app's form is (it must answer, its token sealed and never shown)."""
    from app.domain import services
    from tests.api.test_mcp import rpc

    s = Assist(app, db, cfg, folder, new_client)

    def mcp(who, tool, **args):
        c, h = s.cl[who]
        res = rpc(c, h, "tools/call", {"name": tool, "arguments": args})["result"]
        return json.loads(res["content"][0]["text"]) if res["isError"] else res["structuredContent"]

    assert "only admins" in mcp("editor", "mcp_servers")["error"]
    assert "only admins" in mcp("editor", "add_mcp_server", preset="github", values={"GITHUB_TOKEN": "t0ken"})["error"]
    got = mcp("admin", "mcp_servers")
    assert got["servers"] == [] and got["add"].endswith("/extensions/new/mcp")
    browser, github = got["ready_made"]
    assert (browser["preset"], browser["added"], browser["service"]["profile"]) == ("browser", False, "browser")
    assert (github["preset"], github["values"], "service" in github) == ("github", ["GITHUB_TOKEN"], False)

    # its own server, with a value typed in for its ${TOKEN}
    spec = {"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}, "off": ["create-issue"]}
    assert "type in TOKEN in values" in mcp("admin", "add_mcp_server", name="tracker", server=spec)["error"]
    added = mcp("admin", "add_mcp_server", name="tracker", server=spec, values={"TOKEN": "t0ken"}, description="Issues")
    assert (added["tools"], added["off"], added["headers"]) == (["search", "create-issue"], ["create-issue"], ["Authorization"])
    assert added["enabled"] is True and added["page"].endswith(f"/extensions/{added['id']}")
    assert mcp_servers.is_sealed(extensions.get(db, added["id"])["spec"]["headers"]["Authorization"])
    assert "t0ken" not in json.dumps(mcp("admin", "mcp_servers"))
    again = mcp("admin", "add_mcp_server", name="tracker", server=spec, values={"TOKEN": "t0ken"})
    assert "already called" in again["error"]
    assert "answered 401" in mcp("admin", "add_mcp_server", name="wrong", server=spec, values={"TOKEN": "nope"})["error"]

    # GitHub, ready-made: its name, address and description come with it
    gh = mcp("admin", "add_mcp_server", preset="github", values={"GITHUB_TOKEN": "t0ken"})
    assert (gh["name"], gh["url"]) == ("github", "https://api.githubcopilot.com/mcp/")
    assert [p["added"] for p in mcp("admin", "mcp_servers")["ready_made"]] == [False, True]
    # the browser waits for its container
    assert "isn't running yet" in mcp("admin", "add_mcp_server", preset="browser")["error"]
    monkeypatch.setattr(services, "running", lambda profile: True)
    assert "isn't running yet" not in mcp("admin", "add_mcp_server", preset="browser")["error"]  # on to the server
    assert "give a preset" in mcp("admin", "add_mcp_server", name="x")["error"]
