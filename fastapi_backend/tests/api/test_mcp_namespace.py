"""A namespace's own MCP server (/mcp/<namespace>, docs/mcp.md): off until an owner turns it on, then the same tools
seeing that namespace alone, whatever else the person who signed the agent in can read."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import mcp_tools
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed


@pytest.fixture
def env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "viewer"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "root@x.io", "admin password 1", admin=True)
    return {
        "ids": ids,
        "own": login(client, "own@x.io", "owner password 1"),
        "vi": login(client, "vi@x.io", "viewer password 1"),
        "root": login(client, "root@x.io", "admin password 1"),
    }


def rpc(client: TestClient, h: dict[str, str], method: str, params: dict[str, Any] | None = None, path: str = "/mcp/pods") -> Any:
    r = client.post(path, headers=h, json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


def call(client: TestClient, h: dict[str, str], name: str, server: str = "/mcp/pods", **args: Any) -> tuple[bool, Any]:
    res = rpc(client, h, "tools/call", {"name": name, "arguments": args}, server)["result"]
    return res["isError"], json.loads(res["content"][0]["text"])


def turn_on(client: TestClient, h: dict[str, str], ns: str = "pods", on: bool = True) -> dict[str, Any]:
    r = client.patch(f"/api/v1/namespaces/{ns}/integrations", headers=h, json={"mcp": on})
    assert r.status_code == 200, r.text
    return r.json()


def test_off_until_an_owner_turns_it_on(client, env):
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    got = client.get("/api/v1/namespaces/pods/integrations", headers=env["vi"]).json()
    assert got["mcp"] is False and got["mcp_url"] == "http://localhost:3000/mcp/pods"  # off by default
    r = client.post("/mcp/pods", headers=env["own"], json=ping)
    assert r.status_code == 403 and "Connect apps" in r.json()["detail"]
    # only those who manage the namespace turn it on; someone who can't see it doesn't learn it's there
    assert client.patch("/api/v1/namespaces/pods/integrations", headers=env["vi"], json={"mcp": True}).status_code == 403
    assert client.patch("/api/v1/namespaces/calls/integrations", headers=env["vi"], json={"mcp": True}).status_code == 404
    assert turn_on(client, env["own"])["mcp"] is True
    assert rpc(client, env["vi"], "ping")["result"] == {}
    assert client.post("/mcp/calls", headers=env["vi"], json=ping).status_code == 404  # no role there
    assert client.post("/mcp/nowhere", headers=env["own"], json=ping).status_code == 404
    assert client.post("/mcp/calls", headers=env["own"], json=ping).status_code == 403  # theirs to read, but off
    assert client.get("/mcp/pods", headers=env["vi"]).status_code == 405  # no stream, as on /mcp
    # off again: it stops answering, and Lens's own MCP server is as it was
    turn_on(client, env["own"], on=False)
    assert client.post("/mcp/pods", headers=env["vi"], json=ping).status_code == 403
    assert client.post("/mcp", headers=env["vi"], json=ping).status_code == 200


def test_it_sees_one_namespace(client, env):
    ep1, ep2, call_id = env["ids"]
    turn_on(client, env["own"])
    h = env["own"]  # reads pods and calls; through pods's server, only pods
    init = rpc(client, h, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
    assert "one namespace, pods" in init["instructions"]
    names = [t["name"] for t in rpc(client, h, "tools/list")["result"]["tools"]]
    assert names == [n for n, t in mcp_tools.TOOLS.items() if not (t.admin or t.personal)] and "file_storage" not in names
    assert "read_chat" not in names  # conversations are the person's own, not the namespace's
    sparql = next(t for t in rpc(client, h, "tools/list")["result"]["tools"] if t["name"] == "sparql")
    assert sparql["inputSchema"]["required"] == ["query"]  # the namespace is this server's
    assert "pods alone" in sparql["inputSchema"]["properties"]["namespace"]["description"]

    err, out = call(client, h, "list_namespaces")
    assert not err and [n["name"] for n in out["namespaces"]] == ["pods"]
    err, out = call(client, h, "search", query="Dyno Therapeutics", limit=50)
    assert not err and {r["recording_id"] for r in out["results"]} <= {ep1, ep2} and out["results"]
    err, out = call(client, h, "list_recordings")
    assert {r["recording_id"] for r in out["recordings"]} == {ep1, ep2}
    for name, args in (
        ("get_recording", {"recording_id": call_id}),
        ("search", {"query": "capsid", "namespace": "calls"}),
        ("cite", {"recording_id": call_id, "line": 0}),
        ("sparql", {"namespace": "calls", "query": "ASK { ?s ?p ?o }"}),
    ):
        assert call(client, h, name, **args) == (True, {"error": "not found, or not something you can read"}), name
    err, out = call(client, h, "sparql", query="ASK { ?s ?p ?o }")  # pods, without naming it
    assert not err and out["boolean"] is True
    err, out = call(client, h, "list_files", path="")
    assert not err and [e["name"] for e in out["entries"]] == ["pods"]
    # Lens's own server still sees both
    every = client.post(
        "/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_namespaces"}}
    ).json()["result"]["structuredContent"]["namespaces"]
    assert {n["name"] for n in every} == {"pods", "calls"}


def test_an_admin_is_an_owner_there(client, env):
    turn_on(client, env["root"])
    err, out = call(client, env["root"], "list_namespaces")
    assert [(n["name"], n["role"]) for n in out["namespaces"]] == [("pods", "owner")]
    # the tools about the server itself aren't on a namespace's server, even for admins
    r = rpc(client, env["root"], "tools/call", {"name": "file_storage", "arguments": {}})
    assert r["error"]["code"] == -32602 and "unknown tool" in r["error"]["message"]


def test_signing_in_for_one_namespace(client, app, env):
    turn_on(client, env["own"])
    turn_on(client, env["root"], ns="calls")
    via = {"x-forwarded-host": "lens.example.org", "x-forwarded-proto": "https"}
    web = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    r = web.post("/mcp/pods", headers=via, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://lens.example.org/.well-known/oauth-protected-resource/mcp/pods", scope="read"'
    )
    meta = web.get("/.well-known/oauth-protected-resource/mcp/pods", headers=via).json()
    assert meta["resource"] == "https://lens.example.org/mcp/pods" and meta["scopes_supported"] == ["read"]

    # a token given for pods's server opens it, and nothing else: not calls's, not Lens's, not the API
    _, t = _grant(client, env["own"], scope="read", resource="http://127.0.0.1/mcp/pods")
    assert rpc(client, _bearer(t), "ping")["result"] == {}
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert client.post("/mcp/calls", headers=_bearer(t), json=ping).status_code == 401
    assert client.post("/mcp", headers=_bearer(t), json=ping).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(t)).status_code == 401
    # one for Lens's MCP server (or Lens) works on every namespace's own server too, seeing only that one there
    _, t = _grant(client, env["own"], scope="read", resource="http://127.0.0.1/mcp")
    assert rpc(client, _bearer(t), "ping", path="/mcp/calls")["result"] == {}
    err, out = call(client, _bearer(t), "list_namespaces", server="/mcp/calls")
    assert [n["name"] for n in out["namespaces"]] == ["calls"]


def test_agents_turn_it_on(client, env):
    # through Lens's own MCP server, with a write token: the owner's agent turns pods's server on and gets its address
    _, t = _grant(client, env["own"], scope="read write")
    h = _bearer(t)
    err, out = call(client, h, "namespace_integrations", server="/mcp", namespace="pods")
    assert not err and out["mcp"] is False and out["mcp_url"] == "http://localhost:3000/mcp/pods"
    err, out = call(client, h, "set_namespace_integrations", server="/mcp", namespace="pods", mcp=True)
    assert not err and out["mcp"] is True
    assert client.get("/api/v1/namespaces/pods/integrations", headers=env["vi"]).json()["mcp"] is True
    assert rpc(client, env["vi"], "ping")["result"] == {}
    # a viewer's can't; nor can a read-only token
    _, t = _grant(client, env["vi"], scope="read write")
    err, out = call(client, _bearer(t), "set_namespace_integrations", server="/mcp", namespace="pods", mcp=False)
    assert err and "manage" in out["error"]
    _, t = _grant(client, env["own"], scope="read")
    err, out = call(client, _bearer(t), "set_namespace_integrations", server="/mcp", namespace="pods", mcp=False)
    assert err and "read-only" in out["error"]
