"""The MCP server (docs/mcp.md): agents sign in with OAuth, then search, read and cite what the person can read, over
Streamable HTTP in both eras of the protocol (the initialize handshake, and 2026-07-28's per-request envelope)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import mcp, mcp_tools
from tests.api.test_oauth import O, _bearer, _grant
from tests.helpers import login, make_user, seed

MODERN = "2026-07-28"


@pytest.fixture
def env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "both@x.io", "both password 1", roles={"pods": "viewer", "calls": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    _, tokens = _grant(client, hv, scope="read")  # what an MCP client gets by signing in
    return {"ids": ids, "h": _bearer(tokens), "tokens": tokens, "hv": hv, "hb": login(client, "both@x.io", "both password 1")}


def rpc(client: TestClient, h: dict[str, str], method: str, params: dict[str, Any] | None = None, **headers: str) -> Any:
    r = client.post("/mcp", headers={**h, **headers}, json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == 7
    return body


def tool(client: TestClient, h: dict[str, str], name: str, **args: Any) -> dict[str, Any]:
    res = rpc(client, h, "tools/call", {"name": name, "arguments": args})["result"]
    assert res["isError"] is False, res
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]  # the same, for clients that read text
    return res["structuredContent"]


def tool_error(client: TestClient, h: dict[str, str], name: str, **args: Any) -> str:
    res = rpc(client, h, "tools/call", {"name": name, "arguments": args})["result"]
    assert res["isError"] is True and "structuredContent" not in res
    return json.loads(res["content"][0]["text"])["error"]


def test_clients_are_sent_to_sign_in(client, app, env):
    # without a token: a 401 that names this resource's own metadata (RFC 9728), which names Lens as the issuer
    via = {"x-forwarded-host": "lens.example.org", "x-forwarded-proto": "https"}
    web = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    r = web.post("/mcp", headers=via, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://lens.example.org/.well-known/oauth-protected-resource/mcp", scope="read"'
    )
    meta = web.get("/.well-known/oauth-protected-resource/mcp", headers=via).json()
    assert meta["resource"] == "https://lens.example.org/mcp" and meta["authorization_servers"] == ["https://lens.example.org"]
    assert meta["scopes_supported"] == ["read"]  # all the tools need
    assert client.post("/mcp", headers={"Authorization": "Bearer lo_nope"}, json={}).status_code == 401

    # the app's read-only token is enough: everything here only reads
    init = rpc(client, env["h"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "lens" and "cite" in init["instructions"]
    assert rpc(client, env["h"], "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    tools = rpc(client, env["h"], "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [n for n, t in mcp_tools.TOOLS.items() if not t.needs]  # ask and check need a decision model
    assert all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" for t in tools)
    assert next(t for t in tools if t["name"] == "search")["inputSchema"]["required"] == ["query"]
    # API tokens and sessions work too
    assert rpc(client, env["hv"], "ping")["result"] == {}

    # taking the app's access away stops it at once
    grant = client.get("/api/v1/oauth/grants", headers=env["hv"]).json()[0]
    assert client.delete(f"/api/v1/oauth/grants/{grant['id']}", headers=env["hv"]).status_code == 200
    assert client.post("/mcp", headers=env["h"], json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401


def test_tokens_given_for_another_server(client, env):
    # an app names the server it wants a token for (RFC 8707); one for Lens or its MCP server works here, on any
    # address Lens is reached at, and keeps working when renewed
    renew = lambda app, t: client.post(  # noqa: E731
        f"{O}/token", data={"grant_type": "refresh_token", "client_id": app["client_id"], "refresh_token": t["refresh_token"]}
    )
    for resource in ("http://127.0.0.1/mcp", "http://localhost:3000/mcp", "http://localhost:3000/", "http://127.0.0.1"):
        app, t = _grant(client, env["hv"], scope="read", resource=resource)
        assert rpc(client, _bearer(t), "ping")["result"] == {}
        r = renew(app, t)
        assert rpc(client, _bearer(r.json()), "ping")["result"] == {}
    # one for another server is refused, renewed or not, and the app is told where to sign in for this one
    app, t = _grant(client, env["hv"], scope="read", resource="https://elsewhere.example/mcp")
    r = client.post("/mcp", headers=_bearer(t), json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and "oauth-protected-resource/mcp" in r.headers["www-authenticate"]
    r = renew(app, t)
    assert client.post("/mcp", headers=_bearer(r.json()), json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(r.json())).status_code == 200  # the check is the MCP server's


def test_the_transport(client, env):
    h = env["h"]
    post = lambda body, **hd: client.post("/mcp", headers={**h, **hd}, content=body if isinstance(body, str) else json.dumps(body))  # noqa: E731
    # notifications and responses: 202, nothing to say
    assert post({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert post({"jsonrpc": "2.0", "id": 3, "result": {}}).status_code == 202
    # a batch (2025-03-26) gets an answer per request
    r = post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert r.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    assert post([]).status_code == 400
    assert post([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcp.MAX_BATCH)]).status_code == 200
    r = post([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcp.MAX_BATCH + 1)])
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600
    # too large: said by Content-Length, or found while reading one sent in chunks (which has none), never read whole
    big = b" " * (mcp.MAX_BODY + 1)
    assert post(big.decode()).status_code == 413
    r = client.post("/mcp", headers=h, content=iter([b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}', big]))
    assert r.status_code == 413 and r.json()["error"]["code"] == -32600
    # JSON-RPC errors
    assert post("{nope").json()["error"]["code"] == -32700
    assert post({"id": 1, "method": "ping"}).json()["error"]["code"] == -32600
    assert post({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}).json()["error"]["code"] == -32601
    assert post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "drop_tables"}}).json()["error"]["code"] == -32602
    # nothing streams from the server, and there are no sessions
    assert client.get("/mcp", headers=h).status_code == 405 and client.delete("/mcp", headers=h).status_code == 405
    # a page on another site can't use someone's browser to call it (DNS rebinding); the web app's own pages can
    assert post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, origin="https://evil.example").status_code == 403
    assert post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, origin="http://localhost:3000").status_code == 200
    assert post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, accept="text/html").status_code == 406
    assert post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"mcp-protocol-version": "2025-06-18"}).status_code == 200


def _modern(client, h, method, params=None, *, name=None, meta=None, **headers):
    params = dict(params or {})
    params["_meta"] = meta if meta is not None else {mcp.META_VERSION: MODERN, mcp.META_CAPABILITIES: {}}
    hd = {**h, "mcp-protocol-version": MODERN, "mcp-method": method, **({"mcp-name": name} if name else {}), **headers}
    return client.post("/mcp", headers=hd, json={"jsonrpc": "2.0", "id": "r1", "method": method, "params": params})


def test_the_per_request_era(client, env):
    h = env["h"]
    r = _modern(client, h, "server/discover")
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["supportedVersions"][0] == MODERN and "2025-06-18" in res["supportedVersions"]
    assert (res["resultType"], res["ttlMs"], res["cacheScope"]) == ("complete", 0, "private")
    assert res["_meta"][mcp.META_SERVER]["name"] == "lens" and res["capabilities"]["tools"] == {"listChanged": False}
    assert _modern(client, h, "tools/list").json()["result"]["resultType"] == "complete"
    r = _modern(client, h, "tools/call", {"name": "list_namespaces", "arguments": {}}, name="list_namespaces")
    res = r.json()["result"]
    assert res["resultType"] == "complete" and [n["name"] for n in res["structuredContent"]["namespaces"]] == ["pods"]

    # the headers must agree with the body, and the envelope must be there
    bad = _modern(client, h, "tools/call", {"name": "list_namespaces", "arguments": {}}, name="search")
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, -32020)
    bad = _modern(client, h, "tools/list", **{"mcp-method": "tools/call"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, -32020)
    bad = _modern(client, h, "tools/list", meta={mcp.META_VERSION: MODERN})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, -32602)
    # a version there isn't: the versions there are
    bad = _modern(
        client, h, "tools/list", meta={mcp.META_VERSION: "2031-01-01", mcp.META_CAPABILITIES: {}}, **{"mcp-protocol-version": "2031-01-01"}
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, -32022)
    assert bad.json()["error"]["data"] == {"supported": [MODERN], "requested": "2031-01-01"}
    # no handshake and no ping in this era; a name that isn't plain ASCII comes base64-encoded
    assert _modern(client, h, "initialize").status_code == 404 and _modern(client, h, "ping").status_code == 404
    r = _modern(client, h, "tools/call", {"name": "list_namespaces", "arguments": {}}, name="=?base64?bGlzdF9uYW1lc3BhY2Vz?=")
    assert r.status_code == 200
    # notifications are acknowledged and dropped
    r = client.post("/mcp", headers={**h, "mcp-protocol-version": MODERN}, json={"jsonrpc": "2.0", "method": "notifications/cancelled"})
    assert r.status_code == 202


def test_search_and_cite(client, env):
    h, (ep1, ep2, call) = env["h"], env["ids"]
    found = tool(client, h, "search", query="capsid", limit=2)
    assert found["total"] == 4 and found["next_offset"] == 2 and len(found["results"]) == 2
    hit = found["results"][0]
    assert hit["recording_id"] == ep1 and hit["namespace"] == "pods" and hit["found"] == "said"
    assert "**capsid**" in hit["text"] and "<mark>" not in hit["text"]
    assert hit["url"] == f"http://localhost:3000/resources/{ep1}" + (f"?t={int(hit['seconds'])}" if hit["seconds"] >= 1 else "")
    assert {r["recording_id"] for r in tool(client, h, "search", query="capsid", limit=50)["results"]} == {ep1}  # not the call
    assert tool(client, h, "search", query='"exploit benchmark"', recording_id=ep2)["total"] >= 1
    # what isn't theirs is as if it weren't there
    assert tool_error(client, h, "search", query="capsid", namespace="calls") == "not found, or not something you can read"
    assert tool_error(client, h, "search", query="capsid", recording_id=call) == "not found, or not something you can read"

    c = tool(client, h, "cite", recording_id=ep1, line=1, lines=2)
    assert c["quote"].startswith("Wow, really? Dyno Therapeutics") and c["quote"].endswith("by a wide margin.")
    assert c["speakers"] == ["Bob", "Alice"] and c["title"] == "ep1" and c["line"] == 1
    assert c["url"] == f"http://localhost:3000/resources/{ep1}?t={int(c['seconds'])}"
    assert c["markdown"].startswith("> Wow, really?") and f"[{c['at']}]({c['url']})" in c["markdown"]
    at = tool(client, h, "cite", recording_id=ep1, seconds=c["seconds"] + 0.5)  # the line being said then
    assert at["line"] == 1 and at["quote"].startswith("Wow, really?")
    assert tool_error(client, h, "cite", recording_id=ep1) == "give line or seconds"
    assert tool_error(client, h, "cite", recording_id=ep1, line=99) == f"recording {ep1} has no line 99"
    assert tool_error(client, h, "cite", recording_id=call, line=0) == "not found, or not something you can read"


def test_reading_recordings(client, env):
    h, (ep1, ep2, call) = env["h"], env["ids"]
    ns = tool(client, h, "list_namespaces")["namespaces"]
    assert [(n["name"], n["role"], n["recordings"]) for n in ns] == [("pods", "viewer", 2)]
    recs = tool(client, h, "list_recordings")
    assert recs["total"] == 2 and {r["recording_id"] for r in recs["recordings"]} == {ep1, ep2}
    assert tool(client, h, "list_recordings", query="ep2", sort="title")["recordings"][0]["recording_id"] == ep2
    assert tool_error(client, h, "list_recordings", date_from="last week") == "date_from is a date like 2024-05-31"
    assert tool_error(client, h, "list_recordings", namespace="calls") == "not found, or not something you can read"
    speakers = tool(client, h, "list_speakers")["speakers"]
    assert {s["name"] for s in speakers} == {"Alice", "Bob", "Carol"}
    alice = next(s["speaker_id"] for s in speakers if s["name"] == "Alice")
    assert {r["speaker"] for r in tool(client, h, "search", query="capsid", speaker_id=alice)["results"]} == {"Alice"}

    rec = tool(client, h, "get_recording", recording_id=ep1)
    assert (rec["title"], rec["namespace"], rec["media"], rec["your_role"]) == ("ep1", "pods", "transcript", "viewer")
    assert {s["name"] for s in rec["speakers"]} == {"Alice", "Bob"} and rec["url"] == f"http://localhost:3000/resources/{ep1}"
    assert "Dyno Therapeutics" in [e["name"] for e in rec["entities"]] and rec["sections"][0]["seconds"] == 0
    assert tool_error(client, h, "get_recording", recording_id=call) == "not found, or not something you can read"

    page = tool(client, h, "get_transcript", recording_id=ep1, limit=2)
    assert page["lines_in_all"] == 6 and [x["line"] for x in page["lines"]] == [0, 1] and page["next_line"] == 2
    assert page["lines"][1]["speaker"] == "Bob" and page["lines"][1]["url"].endswith(f"/resources/{ep1}?t=6")
    rest = tool(client, h, "get_transcript", recording_id=ep1, from_line=page["next_line"])
    assert [x["line"] for x in rest["lines"]] == [2, 3, 4, 5] and "next_line" not in rest
    window = tool(client, h, "get_transcript", recording_id=ep1, start_seconds=7, end_seconds=12)
    assert [x["line"] for x in window["lines"]] == [1, 2]
    assert tool_error(client, h, "get_transcript", recording_id=ep1, limit=0) == "limit is at least 1"
    assert tool_error(client, h, "get_transcript", recording_id="ep1") == "recording_id is a whole number"
    assert tool_error(client, h, "get_transcript", recording_id=ep1, page=2).startswith("unknown argument page;")

    doc = tool(client, h, "fetch", id=str(ep2))
    assert doc["id"] == str(ep2) and doc["title"] == "ep2" and doc["url"] == f"http://localhost:3000/resources/{ep2}"
    assert doc["text"].splitlines()[1] == "[0:04] Carol: The exploit benchmark measures how often a model finishes a working exploit."
    assert doc["metadata"]["lines"] == 4 and "truncated" not in doc["metadata"]
    assert tool_error(client, h, "fetch", id=str(call)) == "not found, or not something you can read"


def test_entities_and_the_graph(client, env, monkeypatch):
    h, hb, (ep1, ep2, _) = env["h"], env["hb"], env["ids"]
    found = tool(client, h, "list_entities", query="dyno")
    assert found["total"] == 1  # the calls namespace has one too, which this person can't read
    dyno = found["entities"][0]
    assert (dyno["name"], dyno["type"], dyno["namespace"], dyno["recordings"]) == ("Dyno Therapeutics", "ORG", "pods", 2)
    assert tool(client, h, "list_entities", types=["ORG"], recording_id=ep2)["entities"][0]["entity_id"] == dyno["entity_id"]
    assert tool_error(client, h, "list_entities", types=["SPACESHIP"]).startswith("types: SPACESHIP isn't one of")
    both = tool(client, hb, "list_entities", query="dyno")["entities"]
    assert sorted(e["namespace"] for e in both) == ["calls", "pods"]
    in_calls = next(e["entity_id"] for e in both if e["namespace"] == "calls")
    assert tool_error(client, h, "get_entity", entity_id=in_calls) == "not found, or not something you can read"

    e = tool(client, h, "get_entity", entity_id=dyno["entity_id"], mentions=2)
    assert e["name"] == "Dyno Therapeutics" and e["mentions"] == 4 and e["next_mentions_offset"] == 2
    assert tool_error(client, h, "get_entity", entity_id=dyno["entity_id"], mentions_offset=10001).startswith("mentions_offset")
    assert {m["name"] for m in e["mentioned_most_by"]} == {"Alice", "Bob", "Carol"}
    line = e["lines"][0]
    assert line["recording_id"] in (ep1, ep2) and "Dyno Therapeutics" in line["text"]
    assert line["url"].startswith(f"http://localhost:3000/resources/{line['recording_id']}")

    g = tool(client, h, "explore_graph", entity_id=dyno["entity_id"])
    assert g["focus"] == f"e{dyno['entity_id']}" and {n["label"] for n in g["nodes"] if n["kind"] == "speaker"} == {"Alice", "Bob", "Carol"}
    assert tool(client, h, "explore_graph", node=f"e{dyno['entity_id']}", depth=2)["nodes"][0]["depth"] == 0
    assert tool_error(client, h, "explore_graph") == "give node, an entity_id or a speaker_id"
    assert tool_error(client, h, "explore_graph", node="x1").startswith("node is a node id")
    # the calls graph is isolated: explored on its own, by those who can read it
    assert tool_error(client, h, "explore_graph", entity_id=in_calls, namespace="calls") == "not found, or not something you can read"
    assert tool(client, hb, "explore_graph", entity_id=in_calls, namespace="calls")["focus"] == f"e{in_calls}"
    assert tool_error(client, hb, "explore_graph", entity_id=in_calls) == "not found, or not something you can read"

    carol = next(n["id"] for n in g["nodes"] if n["label"] == "Carol")
    path = tool(client, h, "find_path", from_node=f"e{dyno['entity_id']}", to_node=carol)
    assert path["found"] is True and [n["id"] for n in path["nodes"]] == [f"e{dyno['entity_id']}", carol]
    ev = path["links"][0]["evidence"][0]
    assert ev["recording_id"] == ep2 and ev["url"].startswith(f"http://localhost:3000/resources/{ep2}")

    # a tool that breaks is a JSON-RPC error that says nothing about why (the log has it)
    monkeypatch.setattr(mcp_tools.ents, "connections", lambda *a: 1 / 0)
    err = rpc(client, h, "tools/call", {"name": "get_entity", "arguments": {"entity_id": dyno["entity_id"]}})["error"]
    assert err == {"code": -32603, "message": "the tool failed; the server log has the details"}


def test_links_through_the_web_app(app, env):
    via = {"x-forwarded-host": "lens.example.org", "x-forwarded-proto": "https", **env["h"]}
    web = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    hit = tool(web, via, "search", query="exploit benchmark", limit=1)["results"][0]
    assert hit["url"].startswith(f"https://lens.example.org/resources/{hit['recording_id']}")


def test_collection_roles(client, db, env):
    """Someone with a role on one collection, and none in its namespace, reads just that collection's recordings."""
    ep1, ep2, _ = env["ids"]
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "guest@x.io", "guest password 1")
    ho, hg = login(client, "own@x.io", "owner password 1"), login(client, "guest@x.io", "guest password 1")
    url = "/api/v1/namespaces/pods/collections"
    cid = client.post(url, headers=ho, json={"name": "Talks"}).json()["id"]
    assert client.post("/api/v1/recordings/collection", headers=ho, json={"recordings": [ep1], "collection": cid}).status_code == 200
    assert client.put(f"{url}/{cid}/members", headers=ho, json={"email": "guest@x.io", "role": "viewer"}).status_code == 200
    assert [n["partial"] for n in tool(client, hg, "list_namespaces")["namespaces"]] == [True]
    assert [r["recording_id"] for r in tool(client, hg, "list_recordings")["recordings"]] == [ep1]
    assert {r["recording_id"] for r in tool(client, hg, "search", query="capsid OR exploit", limit=50)["results"]} == {ep1}
    assert tool(client, hg, "get_transcript", recording_id=ep1, limit=1)["lines"]
    rec = tool(client, hg, "get_recording", recording_id=ep1)
    assert rec["collection"] == "Talks" and rec["entities"][0]["mentions"] == 3  # of 4: ep2's isn't theirs
    assert tool_error(client, hg, "get_transcript", recording_id=ep2) == "not found, or not something you can read"
    assert tool(client, hg, "list_speakers")["speakers"] == []  # speakers are namespace-wide


def test_a_real_mcp_client(app, env):
    """The official MCP SDK's client, over Streamable HTTP, in both eras: what Claude, Cursor and the others speak."""
    import anyio
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async def session(mode: str) -> tuple[str, list[str], dict[str, Any], str]:
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1", headers=env["h"])
        async with http, Client(streamable_http_client("http://127.0.0.1/mcp", http_client=http), mode=mode) as c:
            tools = await c.list_tools()
            res = await c.call_tool("search", {"query": "capsid", "limit": 1})
            bad = await c.call_tool("get_recording", {"recording_id": env["ids"][2]})
            assert bad.is_error
            return c.session.protocol_version, [t.name for t in tools.tools], res.structured_content, res.content[0].text

    for mode, version in (("legacy", "2025-11-25"), ("auto", MODERN)):
        got, names, found, text = anyio.run(session, mode)
        assert got == version
        assert names == [n for n, t in mcp_tools.TOOLS.items() if not t.needs]
        assert found["results"][0]["recording_id"] == env["ids"][0] and json.loads(text) == found
