"""The MCP server (docs/mcp.md): `POST /mcp` speaks the Model Context Protocol; agents sign in with OAuth tokens or
API keys, and every tool sees and does exactly what the HTTP API allows the caller."""

from __future__ import annotations

import json
import shutil

import pytest

from app.domain import auth, oauth
from tests.helpers import drain, login, make_user, seed, text_pdf

POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler-utils")


def rpc(client, h, method, params=None, mid=1):
    r = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params else {})})
    assert r.status_code == 200, r.text
    return r.json()


def call(client, h, name, **args):
    """A tool's result: (what it returned, whether it's an error)."""
    res = rpc(client, h, "tools/call", {"name": name, "arguments": args})["result"]
    text = res["content"][0]["text"]
    if res.get("isError"):
        return text, True
    assert json.loads(text) == res["structuredContent"]
    return res["structuredContent"], False


@pytest.fixture
def people(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {
        "ids": ids,
        "root": login(client, "root@x.io", "root password 1"),
        "viewer": login(client, "vi@x.io", "viewer password 1"),
        "editor": login(client, "ed@x.io", "editor password 1"),
    }


def test_signing_in_and_the_protocol(client, people, db, cfg):
    hv = people["viewer"]
    # without a token: where to sign in, as MCP clients expect
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Bearer resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource/mcp"'
    meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == "http://127.0.0.1/mcp" and meta["authorization_servers"] == ["http://127.0.0.1"]
    assert client.post("/mcp", headers={"Authorization": "Bearer la_nope"}, json={}).status_code == 401
    assert client.get("/mcp", headers=hv).status_code == 405 and client.delete("/mcp", headers=hv).status_code == 405

    init = rpc(client, hv, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "lens" and "namespaces" in init["result"]["instructions"]
    assert set(init["result"]["capabilities"]) == {"tools", "resources"}
    assert rpc(client, hv, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    assert rpc(client, hv, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}
    # notifications get no answer; unknown methods, wrong shapes and bad JSON say so
    r = client.post("/mcp", headers=hv, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert (r.status_code, r.content) == (202, b"")
    assert rpc(client, hv, "prompts/list")["error"]["code"] == -32601
    assert client.post("/mcp", headers=hv, json={"id": 3, "method": "ping"}).json()["error"]["code"] == -32600
    assert client.post("/mcp", headers=hv, json=["x"]).json()[0]["error"]["code"] == -32600
    r = client.post("/mcp", headers=hv, content=b"{not json")
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32700)
    # a batch, as older clients send
    r = client.post(
        "/mcp",
        headers=hv,
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    assert [a["id"] for a in r.json()] == [1, 2]
    assert client.post("/mcp", headers=hv, json=[{"jsonrpc": "2.0", "method": "notifications/initialized"}]).status_code == 202

    # sessions, API keys and OAuth tokens all sign in; what a read-only one can't do isn't offered
    names = lambda h: {t["name"] for t in rpc(client, h, "tools/list")["result"]["tools"]}  # noqa: E731
    writes = {"import_text", "import_web_page", "process"}
    assert writes < names(hv) and len(names(hv)) == 11
    _, key = auth.create_token(db, db.one("SELECT record::id(id) AS id FROM account WHERE email = 'vi@x.io'")["id"], "agent", "read")
    hk = {"Authorization": f"Bearer {key}"}
    assert names(hk) == names(hv) - writes
    text, failed = call(client, hk, "import_text", namespace="pods", text="x")
    assert failed and "read-only" in text
    app = oauth.register_client(db, "Agent", ["https://agent.example/cb"])
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'vi@x.io'")["id"]
    info = oauth.authorization(db, uid, app["client_id"], "https://agent.example/cb", "code", "c" * 43, "S256", "read write")
    grant = _grant(db, cfg, uid, app, info)
    ho = {"Authorization": f"Bearer {grant['access_token']}"}
    assert names(ho) == names(hv)
    out, failed = call(client, ho, "list_namespaces")
    assert not failed and [n["name"] for n in out["namespaces"]] == ["pods"]
    tool = next(t for t in rpc(client, hv, "tools/list")["result"]["tools"] if t["name"] == "search")
    assert tool["inputSchema"]["required"] == ["query"] and tool["annotations"]["readOnlyHint"] is True


def _grant(db, cfg, uid, app, info):
    import base64
    import hashlib

    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    code, _ = oauth.approve(db, uid, info, challenge)
    tokens, _ = oauth.exchange_code(db, cfg, app["client_id"], None, code, info["redirect_uri"], verifier)
    return tokens


def test_tools_see_what_the_caller_may_see(client, people, db, cfg):
    hv, hr, ids = people["viewer"], people["root"], people["ids"]
    out, _ = call(client, hv, "list_namespaces")
    assert out == {"namespaces": [{"name": "pods", "role": "viewer", "recordings": 2, "speakers": 3, "partial": False, "resources": 2}]}
    assert {n["name"] for n in call(client, hr, "list_namespaces")[0]["namespaces"]} == {"pods", "calls"}

    cols, _ = call(client, hv, "list_collections", namespace="pods")
    assert [(c["name"], c["default"], c["resources"]) for c in cols["collections"]] == [("General", True, 2)]
    text, failed = call(client, hv, "list_collections", namespace="calls")  # no role there: as if it didn't exist
    assert failed and "(404)" in text

    listed, _ = call(client, hv, "list_resources")
    assert listed["total"] == 2 and {r["id"] for r in listed["resources"]} == set(ids[:2])
    assert {r["namespace"] for r in listed["resources"]} == {"pods"} and listed["resources"][0]["kind"] == "transcript"
    assert call(client, hv, "list_resources", collection=cols["collections"][0]["id"], limit=1)[0]["total"] == 2
    assert call(client, hv, "list_resources", title_contains="ep2")[0]["total"] == 1
    assert call(client, hr, "list_resources", namespace="calls")[0]["total"] == 1
    assert call(client, hv, "list_resources", namespace="calls")[1]  # an error

    found, _ = call(client, hv, "search", query="capsid")
    assert found["total"] >= 3 and "found_by_meaning" not in found
    hit = found["hits"][0]
    assert hit["resource_id"] in ids[:2] and "capsid" in hit["text"].lower() and "<mark>" not in hit["text"]
    assert hit["found"] == "said" and hit["namespace"] == "pods" and hit["seconds"] >= 0 and ":" in hit["at"]
    assert call(client, hv, "search", query="shipment")[0]["total"] == 0  # said in calls only
    assert call(client, hr, "search", query="shipment")[0]["total"] == 1
    only = call(client, hv, "search", query="exploit", resource_id=ids[1])[0]
    assert only["total"] >= 1 and {h["resource_id"] for h in only["hits"]} == {ids[1]}
    assert call(client, hv, "search", query="capsid", resource_id=ids[1])[0]["total"] == 0
    text, failed = call(client, hv, "search")
    assert failed and text == "search needs query"
    text, failed = call(client, hv, "search", query="capsid", colour="red")
    assert failed and text == "search doesn't take colour"
    assert rpc(client, hv, "tools/call", {"name": "delete_everything", "arguments": {}})["error"]["code"] == -32602

    got, _ = call(client, hv, "get_resource", id=ids[0])
    assert (got["title"], got["namespace"], got["collection"], got["kind"], got["role"]) == (
        "ep1",
        "pods",
        ["General"],
        "transcript",
        "viewer",
    )
    assert got["lines"] == 6 and set(got["speakers"]) == {"Alice", "Bob"} and got["chapters"][0]["seconds"] == 0
    text, failed = call(client, hv, "get_resource", id=ids[2])
    assert failed and "(404)" in text
    text, failed = call(client, hv, "get_resource", id="one")
    assert failed and "wrong argument" in text

    all_of_it, _ = call(client, hv, "get_transcript", id=ids[0])
    assert all_of_it["total_lines"] == 6 and all_of_it["lines"][0] == {
        "at": "0:00",
        "seconds": 0.0,
        "speaker": "Alice",
        "text": "Welcome back. Today we talk about Dyno Therapeutics and how they design a capsid with machine learning.",
    }
    part, _ = call(client, hv, "get_transcript", id=ids[0], max_lines=2)
    assert len(part["lines"]) == 2 and part["more_from_seconds"] == all_of_it["lines"][2]["seconds"]
    rest, _ = call(client, hv, "get_transcript", id=ids[0], from_seconds=part["more_from_seconds"])
    assert [line["text"] for line in rest["lines"]][-1].endswith("next story now.") and "more_from_seconds" not in rest
    assert len(rest["lines"]) + 2 in (6, 7)  # the line that's being said at that second comes again
    assert call(client, hv, "get_transcript", id=ids[2])[1]
    text, failed = call(client, hv, "get_pages", id=ids[0])
    assert failed and "isn't a document" in text


def test_resources_are_the_archives_text(client, people):
    hv, hr, ids = people["viewer"], people["root"], people["ids"]
    listed = rpc(client, hv, "resources/list")["result"]
    assert {r["uri"] for r in listed["resources"]} == {f"lens://resource/{i}" for i in ids[:2]} and "nextCursor" not in listed
    assert listed["resources"][0]["mimeType"] == "text/markdown" and "pods" in listed["resources"][0]["description"]
    assert len(rpc(client, hr, "resources/list")["result"]["resources"]) == 3
    assert rpc(client, hv, "resources/templates/list")["result"]["resourceTemplates"][0]["uriTemplate"] == "lens://resource/{id}"
    read = rpc(client, hv, "resources/read", {"uri": f"lens://resource/{ids[0]}"})["result"]["contents"][0]
    assert read["mimeType"] == "text/markdown" and read["text"].startswith("# ep1\n\n[0:00] Alice: Welcome back.")
    assert "[0:06] Bob: Wow, really?" in read["text"]
    # one they may not read, and ones that aren't there
    assert rpc(client, hv, "resources/read", {"uri": f"lens://resource/{ids[2]}"})["error"]["code"] == -32002
    assert rpc(client, hv, "resources/read", {"uri": "lens://resource/999"})["error"]["code"] == -32002
    assert rpc(client, hv, "resources/read", {"uri": "file:///etc/passwd"})["error"]["code"] == -32002
    assert rpc(client, hr, "resources/read", {"uri": f"lens://resource/{ids[2]}"})["result"]["contents"][0]["text"].startswith("# call")


def test_importing_and_processing_need_the_role_the_api_asks(client, people, db, cfg):
    hv, he, hr, ids = people["viewer"], people["editor"], people["root"], people["ids"]
    text = "Ana|N|The harbour was quiet this morning.\nBen|N|It usually is before the boats come in.\nAna|N|Then it gets loud."
    refused, failed = call(client, hv, "import_text", namespace="pods", text=text)
    assert failed and "(403)" in refused
    assert call(client, he, "import_text", namespace="calls", text=text)[1]  # an editor of pods, not of calls
    made, failed = call(client, he, "import_text", namespace="pods", text=text, title="Harbour notes")
    assert not failed and made["resource_id"] not in ids
    job, _ = call(client, he, "get_job", id=made["job"])
    assert job["status"] == "queued" and job["resource_id"] == made["resource_id"] and "analyze" in job["steps"]
    drain(db, cfg)
    assert call(client, he, "get_job", id=made["job"])[0]["status"] == "succeeded"
    assert call(client, hv, "search", query="harbour quiet")[0]["hits"][0]["resource_id"] == made["resource_id"]
    assert call(client, hv, "get_job", id=made["job"])[0]["status"] == "succeeded"  # a viewer of its namespace sees its jobs
    # the API's own audit trail: an import through MCP is an import
    assert "import" in [a["action"] for a in client.get("/api/v1/audit", headers=hr).json()]

    queued, failed = call(client, he, "process", resource_ids=[made["resource_id"]], steps=["analyze"])
    assert not failed and len(queued["jobs"]) == 1
    assert call(client, hv, "process", resource_ids=[ids[0]])[1]
    text, failed = call(client, he, "process", resource_ids=[ids[2]])
    assert failed and "(404)" in text
    text, failed = call(client, he, "process", resource_ids=[made["resource_id"]], steps=["fly"])
    assert failed and "(400)" in text
    # capturing web pages follows the API's rules too: no private addresses, and editors only
    text, failed = call(client, he, "import_web_page", url="http://127.0.0.1:9/secret", namespace="pods")
    assert failed and "(400)" in text
    assert call(client, hv, "import_web_page", url="https://example.org/", namespace="pods")[1]


@POPPLER
def test_a_documents_pages(client, people, db, cfg):
    he, hv = people["editor"], people["viewer"]
    data = text_pdf([["The lighthouse stands at the harbour mouth."], ["Its lamp was lit in 1887."]])
    r = client.post("/api/v1/uploads", headers=he, json={"namespace": "pods", "filename": "lighthouse.pdf", "size": len(data)})
    assert r.status_code == 201, r.text
    up = client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**he, "Content-Type": "application/octet-stream"}, content=data)
    rid = up.json()["recording"]
    drain(db, cfg)
    pages, failed = call(client, hv, "get_pages", id=rid)
    assert not failed and pages["total_pages"] == 2
    assert [(p["page"], p["text"]) for p in pages["pages"]] == [
        (1, "The lighthouse stands at the harbour mouth."),
        (2, "Its lamp was lit in 1887."),
    ]
    assert [p["page"] for p in call(client, hv, "get_pages", id=rid, from_page=2)[0]["pages"]] == [2]
    assert call(client, hv, "get_resource", id=rid)[0]["pages"] == 2
    hit = call(client, hv, "search", query="lamp")[0]["hits"][0]
    assert (hit["at"], hit["page"], hit["found"]) == ("p. 2", 2, "page") and "seconds" not in hit
    read = rpc(client, hv, "resources/read", {"uri": f"lens://resource/{rid}"})["result"]["contents"][0]["text"]
    assert "## Page 1\n\nThe lighthouse stands" in read and "## Page 2\n\nIts lamp" in read
    assert call(client, hv, "list_resources", kind="document")[0]["resources"][0]["pages"] == 2
