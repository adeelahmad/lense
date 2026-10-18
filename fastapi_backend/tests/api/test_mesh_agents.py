"""The mesh through agents (docs/mesh.md, "Agents"): an admin's agent reads it, adds machines and changes them, over
MCP and in the assistant. Over MCP a change needs an admin's write-scope token and is made at once, audited; in the
assistant adding a machine and taking one off always wait for the person's yes, while renaming one, bringing one back
and withdrawing a join token happen at once. A join token's secret is never in what the read tools say, and an
approved install line goes to the person once and isn't kept."""

from __future__ import annotations

import json

import pytest

from app.domain import mesh, settings, store
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_mcp import rpc, tool, tool_error
from tests.api.test_mesh import joined, nebula, server_db  # noqa: F401 - the fake nebula-cert, a database server
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user


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


def named(client, h, t, **args):
    """tool(), for a tool that takes a `name`."""
    res = rpc(client, h, "tools/call", {"name": t, "arguments": args})["result"]
    assert res["isError"] is False, res
    return res["structuredContent"]


def audited(db):
    return db.rows("SELECT at, action, target, detail FROM audit_log ORDER BY at")


def test_mcp_clients_add_and_manage_mesh_nodes(client, db, cfg, nebula, server_db):  # noqa: F811
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    m = _bearer(_grant(client, h, scope="read write")[1])
    ro = _bearer(_grant(client, h, scope="read")[1])
    assert tool(client, m, "mesh")["enabled"] is False
    assert "read-only" in tool_error(client, ro, "add_mesh_node")
    assert mesh.tokens(db) == []

    made = named(client, m, "add_mesh_node", name="studio", hours=2)
    secret = made["secret"]
    assert made["install_line"].endswith(f"LENS_JOIN={made['join_url']} LENS_JOIN_TOKEN={secret} sh")
    assert made["join_url"].startswith("http") and made["turned_on"] is True and made["token"]["role"] == "worker"
    assert settings.effective(db, cfg)["mesh"]["enabled"] is True  # adding the first node turned the mesh on
    rows = audited(db)
    assert ("mesh.token.create", f"mesh_token:{made['token']['id']}") in [(r["action"], r["target"]) for r in rows]
    assert {"role": "worker", "uses": 1, "via": "mcp"} in [r["detail"] for r in rows]
    assert ["enabled", "mcp"] in [r["detail"] for r in rows if r["action"] == "settings.save"]
    assert secret.split(".")[-1] not in json.dumps(rows, default=str)
    assert "at least 1" in tool_error(client, m, "add_mesh_node", hours=0)

    nid, answer, _ = joined(client, cfg, secret)
    nh = {"Authorization": f"Bearer {answer['node_token']}"}
    beat = {"nebula": "running", "version": "9.9.9", "machine": {"os": "linux", "cpus": 8}, "error": "fine"}
    assert client.post("/api/v1/mesh/node/beat", json=beat, headers=nh).status_code == 200
    seen = tool(client, ro, "mesh")  # a read-only token reads it
    assert seen["enabled"] is True and seen["primary_ip"] == "10.73.0.1" and seen["ca"]["fingerprint"]
    assert seen["settings"].endswith("/settings/nodes")
    primary, node = seen["nodes"]
    assert primary["role"] == "primary" and primary["online"] is True
    assert {k: node[k] for k in ("id", "name", "role", "ip", "online", "nebula", "lens_version", "error", "removed")} == {
        "id": nid,
        "name": "studio",
        "role": "worker",
        "ip": "10.73.0.2",
        "online": True,
        "nebula": "running",
        "lens_version": "9.9.9",
        "error": "fine",
        "removed": None,
    }
    assert node["machine"]["cpus"] == 8 and node["sandbox_gateway"] is None  # a worker runs no sandboxes

    # change, take off, bring back: at once, audited
    assert "read-only" in tool_error(client, ro, "remove_mesh_node", node_id=nid)
    out = named(client, m, "change_mesh_node", node_id=nid, name="Studio Mac", namespaces=["calls"])
    assert out["node"]["name"] == "Studio Mac" and out["node"]["groups"] == ["lens-node", "lens-worker", "ns-calls"]
    assert tool(client, m, "change_mesh_node", node_id=nid, every_namespace=True)["node"]["namespaces"] == []
    assert "say what to change" in tool_error(client, m, "change_mesh_node", node_id=nid)
    assert "no namespace" in tool_error(client, m, "change_mesh_node", node_id=nid, namespaces=["nope"])
    gone = tool(client, m, "remove_mesh_node", node_id=nid)["node"]
    assert gone["revoked_at"] and gone["revoked_by"] == "root@x.io"
    assert client.get("/api/v1/mesh/node/config", headers=nh).status_code == 401
    assert tool(client, m, "mesh")["nodes"][1]["removed"] == gone["revoked_at"]
    assert tool(client, m, "restore_mesh_node", node_id=nid)["node"]["revoked_at"] is None
    assert "primary" in tool_error(client, m, "remove_mesh_node", node_id=primary["id"])
    assert "not found" in tool_error(client, m, "remove_mesh_node", node_id=999)

    # a token not used yet is listed without its secret, and withdrawn
    other = tool(client, m, "add_mesh_node", role="compute", uses=3)
    assert other["install_line"].endswith(" LENS_NODE_SANDBOX=1 sh") and other["turned_on"] is False
    listed = tool(client, m, "mesh")["tokens"]
    assert [t["id"] for t in listed] == [other["token"]["id"]] and listed[0]["uses"] == 3
    assert other["secret"].split(".")[-1] not in json.dumps(listed)
    assert tool(client, m, "withdraw_mesh_token", token_id=other["token"]["id"])["tokens"] == []
    actions = [r["action"] for r in audited(db)]
    for a in ("mesh.node.change", "mesh.node.remove", "mesh.node.restore", "mesh.token.withdraw"):
        assert a in actions

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    ed = _bearer(_grant(client, login(client, "ed@x.io", "editor password 1"), scope="read write")[1])
    assert "only admins" in tool_error(client, ed, "mesh")
    assert "only admins" in tool_error(client, ed, "add_mesh_node")
    assert "only admins" in tool_error(client, ed, "restore_mesh_node", node_id=nid)


def test_the_assistant_adds_and_removes_machines_once_the_person_approves(app, db, cfg, folder, new_client, llm, nebula, server_db):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "add_mesh_node", {"role": "worker", "name": "studio", "namespaces": ["pods"]})]},
        {"content": "Approve it, then run the line on the Mac."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "add my Mac as a worker"}).text)
    (appr,) = ev["approval"]
    assert appr["summary"] == (
        "Let a new worker node named studio join the mesh, serving pods: a join token that works once, for 24 hours, and turn the mesh on"
    )
    assert mesh.tokens(db) == [] and settings.effective(db, cfg)["mesh"]["enabled"] is False  # nothing yet

    out = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["token"]["namespaces"] == ["pods"] and out["turned_on"] is True
    line = out["install_line"]
    secret = line.split("LENS_JOIN_TOKEN=")[1].split()[0]
    assert secret.startswith(mesh.TOKEN_PREFIX) and "LENS_JOIN=http" in line
    assert settings.effective(db, cfg)["mesh"]["enabled"] is True
    # the line went to the person, once: not kept in the approval, its audit, or anything the model was sent
    kept = db.one("SELECT result FROM $r", r=store.R("approval", appr["id"]))["result"]
    assert kept["token"]["id"] == out["token"]["id"] and "install_line" not in kept
    assert secret.split(".")[-1] not in json.dumps(audited(db), default=str)
    assert c.get(f"/api/v1/approvals/{appr['id']}", headers=h).json()["result"] == kept
    assert "mesh.token.create" in [r["action"] for r in audited(db)]

    nid, _answer, _ = joined(c, cfg, secret)
    llm.tool_script = [
        {"content": "", "tool_calls": [call(2, "mesh", {})]},
        {"content": "", "tool_calls": [call(3, "remove_mesh_node", {"node_id": nid})]},
        {"content": "Approve it to take the Mac off."},
    ]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "take the Mac off"}).text)
    sent = json.dumps(llm.seen)
    assert '"name": "studio"' in sent.replace('\\"', '"') and secret.split(".")[-1] not in sent  # it read the mesh, never the secret
    (appr,) = ev["approval"]
    assert appr["summary"] == "Take studio (worker, 10.73.0.2) off the mesh"
    assert mesh.node(db, nid)["revoked_at"] is None
    out = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["node"]["revoked_at"]
    assert mesh.node(db, nid)["revoked_by"] == "root@x.io"
    assert ("mesh.node.remove", ["assistant"]) in [(r["action"], r["detail"]) for r in audited(db)]


def test_the_assistant_renames_restores_and_withdraws_at_once(app, db, cfg, folder, new_client, llm, nebula, server_db):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    made = mesh.add(db, cfg, cfg, "root@x.io")
    nid, _answer, _ = joined(c, cfg, made["secret"])
    mesh.remove(db, nid, by="root@x.io")
    spare, _secret = mesh.make_token(db, cfg, "root@x.io", "compute")
    llm.tool_script = [
        {"content": "", "tool_calls": [call(0, "mesh", {})]},  # Lens's own word: what follows still happens at once
        {"content": "", "tool_calls": [call(1, "restore_mesh_node", {"node_id": nid})]},
        {"content": "", "tool_calls": [call(2, "change_mesh_node", {"node_id": nid, "name": "Studio Mac", "namespaces": ["calls"]})]},
        {"content": "", "tool_calls": [call(3, "withdraw_mesh_token", {"token_id": spare["id"]})]},
        {"content": "Done."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "bring the Mac back for calls"}).text)
    assert not ev.get("approval")
    n = mesh.node(db, nid)
    assert n["revoked_at"] is None and n["name"] == "Studio Mac" and n["namespaces"] == ["calls"]
    assert mesh.tokens(db) == []
    details = {r["action"]: r["detail"] for r in audited(db)}
    assert details["mesh.node.restore"] == ["assistant"] and details["mesh.token.withdraw"] == ["assistant"]
    assert details["mesh.node.change"] == {"name": "Studio Mac", "namespaces": ["calls"], "assistant": True}


def test_a_setup_conversation_still_asks_before_a_machine_joins_or_goes(app, db, cfg, folder, new_client, llm, nebula, server_db):  # noqa: F811
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    made = mesh.add(db, cfg, cfg, "root@x.io")
    nid, _answer, _ = joined(c, cfg, made["secret"])
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "add_mesh_node", {"role": "compute"})]},
        {"content": "", "tool_calls": [call(2, "remove_mesh_node", {"node_id": nid})]},
        {"content": "Both wait for you."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "set up the nodes"}).text)
    assert [a["summary"] for a in ev["approval"]] == [
        "Let a new compute node join the mesh: a join token that works once, for 24 hours",
        "Take studio (worker, 10.73.0.2) off the mesh",
    ]
    assert mesh.tokens(db) == [] and mesh.node(db, nid)["revoked_at"] is None


def test_only_admins_get_the_mesh_tools(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    llm.tool_script = [{"content": "Hi."}]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "hi"}).text)
    offered = {t["function"]["name"] for t in llm.seen[-1].get("tools") or []}
    assert not offered & {"mesh", "add_mesh_node", "change_mesh_node", "remove_mesh_node", "restore_mesh_node", "withdraw_mesh_token"}
