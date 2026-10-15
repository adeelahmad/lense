"""API keys made for one namespace (POST /api/v1/tokens with a namespace): they open that namespace's own MCP server
and chat completions, with the person's role there, and nothing else in Lens."""

from __future__ import annotations

from tests.api import test_completions as completions
from tests.api.test_completions import ask, turn_on
from tests.api.test_mcp_namespace import call, rpc

llm, app, env = completions.llm, completions.app, completions.env  # the same fixtures: a fake model, pods and calls


def key(client, h, **body) -> dict:
    r = client.post("/api/v1/tokens", headers=h, json={"name": "family app", **body})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_key_for_one_namespace_opens_its_servers_alone(client, env, llm):
    turn_on(client, env["own"], mcp=True)
    k = key(client, env["own"], namespace="pods")
    listed = {t["name"]: t for t in client.get("/api/v1/tokens", headers=env["own"]).json()}
    assert listed["family app"]["namespace"] == "pods" and "space" not in listed["family app"]
    everyone = {t["name"]: t for t in client.get("/api/v1/admin/tokens", headers=env["root"]).json()}
    assert everyone["family app"]["namespace"] == "pods"

    # its namespace's own servers answer, seeing that namespace alone
    got = ask(client, k)
    assert got["choices"][0]["message"]["content"].startswith("The shipment") and got["lens"]["namespace"] == "pods"
    assert client.get("/api/v1/namespaces/pods/models", headers=k).status_code == 200
    assert rpc(client, k, "ping")["result"] == {}
    is_err, spaces = call(client, k, "list_namespaces")
    assert not is_err and [s["name"] for s in spaces["namespaces"]] == ["pods"]

    # nowhere else: not Lens's own MCP server, not another namespace's, not the API
    r = client.post("/mcp", headers=k, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and "pods's MCP server and chat completions alone" in r.json()["detail"]
    assert ask(client, k, ns="calls", status=401)["error"]["code"] == "invalid_api_key"
    assert client.post("/mcp/calls", headers=k, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    for path in ("/api/v1/namespaces", "/api/v1/tokens", "/api/v1/namespaces/pods/stats", "/api/v1/namespaces/pods/integrations"):
        r = client.get(path, headers=k)
        assert r.status_code == 401 and "alone" in r.json()["detail"], path
    assert client.post("/api/v1/tokens", headers=k, json={"name": "more"}).status_code == 401  # it makes no keys

    # only for a namespace you can read
    r = client.post("/api/v1/tokens", headers=env["vi"], json={"name": "x", "namespace": "calls"})
    assert r.status_code == 404
    assert client.post("/api/v1/tokens", headers=env["vi"], json={"name": "x", "namespace": "nowhere"}).status_code == 404
    # with no namespace, a key opens whatever its person can read, as before
    plain = key(client, env["own"])
    assert client.get("/api/v1/namespaces", headers=plain).status_code == 200
    assert ask(client, plain)["lens"]["namespace"] == "pods"
    audit = [(a["action"], a.get("detail")) for a in client.get("/api/v1/audit", headers=env["root"]).json()]
    assert ("token.create", {"namespace": "pods"}) in audit


def test_it_has_the_persons_role_there_and_no_more(client, env, llm):
    turn_on(client, env["own"], mcp=True)
    k = key(client, env["root"], namespace="pods", scope="write")  # an admin: an owner of pods, and nothing else
    assert "error" in rpc(client, k, "tools/call", {"name": "file_storage", "arguments": {}})  # no server tools there
    is_err, got = call(client, k, "set_namespace_integrations", chat=False)
    assert not is_err and got["namespace"] == "pods" and got["chat"] is False
    # losing the role there closes it
    vk = key(client, env["vi"], namespace="pods")
    assert ask(client, vk, status=403)["error"]["code"] == "endpoint_off"
    turn_on(client, env["own"])
    assert ask(client, vk)["lens"]["namespace"] == "pods"
    r = client.put("/api/v1/namespaces/pods/members", headers=env["root"], json={"email": "vi@x.io", "role": None})
    assert r.status_code == 200, r.text
    assert ask(client, vk, status=404)["error"]["code"] == "not_found"
