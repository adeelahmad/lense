"""`lens node` (docs/mesh.md, Nodes): the agent on a machine that joins another install's mesh. It turns the folder it
shares with the mesh container on, waits for the container's public key, joins with the token from the install line
(which never crosses the wire), keeps its credentials to itself, writes its config for the container, and then, over
the mesh, follows the primary's config, renews its certificate and reports every minute. The mesh container is
played here by writing its public key; the calls go to this install's API."""

from __future__ import annotations

import json
import stat

import pytest

from app.domain import mesh, mesh_node
from tests.api.test_mesh import admin, nebula, server_db  # noqa: F401 - fixtures


@pytest.fixture
def folders(tmp_path):
    return tmp_path / "node", tmp_path / "mesh"


def agent(client, folders, token, name="pi"):
    a = mesh_node.Agent(folders[0], folders[1], "http://testserver/", token, name, http=client)
    return a


def running(shared, error=None):
    """The mesh container saying Nebula runs (its status.json)."""
    (shared / "status.json").write_text(json.dumps({"state": "running", "nebula": True, "error": error, "log": ["up"]}))


def due(a):
    a.last_beat = 0.0  # as a minute later


def test_a_machine_joins_and_follows_the_primary(client, db, cfg, nebula, server_db, folders):  # noqa: F811
    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={"role": "worker", "namespaces": []}, headers=h).json()
    node_dir, shared = folders
    a = agent(client, folders, made["secret"], name="Studio Mac (M2)!")

    # first the container is asked for a key pair: lens.json on, nothing to run yet
    a.tick()
    lens = json.loads((shared / "lens.json").read_text())
    assert lens == {"on": True, "ip": None, "public_key": None, "config": None, "version": None}
    assert a.error == "waiting for the mesh container's key" and not (node_dir / "node.json").exists()

    # the container publishes its public key: the agent joins with it
    _, pub = mesh.keygen(cfg)
    (shared / "host.pub").write_text(pub)
    a.tick()
    kept = json.loads((node_dir / "node.json").read_text())
    assert stat.S_IMODE((node_dir / "node.json").stat().st_mode) == 0o600
    assert kept["node"]["name"] == "Studio Mac -M2--" and kept["node"]["ip"] == "10.73.0.2" and kept["node"]["role"] == "worker"
    assert kept["node_token"].startswith(mesh.NODE_PREFIX) and kept["agent_key"] and kept["public_key"] == pub
    lens = json.loads((shared / "lens.json").read_text())
    assert lens["ip"] == "10.73.0.2" and lens["public_key"] == pub and lens["version"] == kept["version"]
    assert lens["config"]["pki"]["cert"] and "lighthouse" in lens["config"]
    assert made["secret"] not in (shared / "lens.json").read_text()  # the container gets the config, not the credentials

    # it calls the primary over the mesh only, so not before Nebula runs; then at once, with its node token
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert a.error == "waiting for Nebula to start" and not me["last_seen"]
    running(shared, error="a peer is slow")
    a.tick()
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert me["last_seen"] and me["status"]["machine"]["cpus"] and me["status"]["version"] and a.error is None
    assert me["status"]["nebula"] == "running" and me["status"]["error"] == "a peer is slow" and me["status"]["log"] == ["up"]

    # a removed node is refused, and keeps asking: an undo brings it back
    client.delete(f"/api/v1/mesh/nodes/{me['id']}", headers=h)
    due(a)
    a.tick()
    assert a.removed and json.loads((shared / "lens.json").read_text())["on"] is True
    a.report()
    assert mesh_node.Agent(node_dir, shared).view()["removed"] is True
    client.post(f"/api/v1/mesh/nodes/{me['id']}/restore", headers=h)
    due(a)
    a.tick()
    assert not a.removed

    # another machine is removed: the blocklist grows, so this one's config changes and lens.json follows
    other = client.post("/api/v1/mesh/tokens", json={"role": "compute"}, headers=h).json()
    b = agent(client, (node_dir.parent / "b", node_dir.parent / "b-mesh"), other["secret"], name="box")
    b.tick()
    (b.shared / "host.pub").write_text(mesh.keygen(cfg)[1])
    running(b.shared)
    b.tick()
    assert b.node["node"]["role"] == "compute" and b.node["node"]["ip"] == "10.73.0.3"
    before = json.loads((shared / "lens.json").read_text())["version"]
    client.delete(f"/api/v1/mesh/nodes/{b.node['node']['id']}", headers=h)
    due(a)
    a.tick()
    lens = json.loads((shared / "lens.json").read_text())
    assert lens["version"] != before and lens["config"]["pki"]["blocklist"]

    # the container made a new key pair (its volume was lost): the agent has it signed, and lens.json names it
    _, pub2 = mesh.keygen(cfg)
    (shared / "host.pub").write_text(pub2)
    due(a)
    a.tick()
    lens = json.loads((shared / "lens.json").read_text())
    assert lens["public_key"] == pub2 and lens["config"]["pki"]["cert"] != kept["config"]["pki"]["cert"]
    assert mesh.describe(cfg, lens["config"]["pki"]["cert"])["networks"] == ["10.73.0.2/16"]

    # and one that's near its end is renewed with the same key
    db.q("UPDATE $r SET not_after = '2000-01-01T00:00:00Z'", r=mesh.R("mesh_node", me["id"]))
    due(a)
    a.tick()
    assert a.node["not_after"] > "2001" and json.loads((shared / "lens.json").read_text())["public_key"] == pub2

    # started again (a restart of the machine), it carries on as the same node without its token
    again = mesh_node.Agent(node_dir, shared, None, None, "whatever", http=client)
    again.tick()
    assert again.node["node"]["id"] == me["id"] and not again.refused and again.error is None
    view = again.view()
    assert view["joined"] and view["node"]["ip"] == "10.73.0.2" and "node_token" not in json.dumps(view)


def test_a_spent_or_wrong_token_is_not_tried_again(client, db, cfg, nebula, folders):  # noqa: F811
    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={"role": "compute"}, headers=h).json()
    first = agent(client, (folders[0] / "1", folders[1] / "1"), made["secret"])
    (folders[1] / "1").mkdir(parents=True)
    (folders[1] / "1" / "host.pub").write_text(mesh.keygen(cfg)[1])
    first.tick()
    assert first.node

    second = agent(client, folders, made["secret"])
    folders[1].mkdir(parents=True, exist_ok=True)
    (folders[1] / "host.pub").write_text(mesh.keygen(cfg)[1])
    second.tick()
    assert "used" in second.refused and not (folders[0] / "node.json").exists()
    second.report()
    assert "used" in mesh_node.Agent(*folders).view()["refused"]
    calls = []
    second.http = type("Spy", (), {"post": lambda *a, **k: calls.append(a)})()
    second.tick()
    assert calls == []  # not tried again: the install line is run again with a new token

    bad = agent(client, (folders[0] / "3", folders[1]), "lensjoin1.nope")
    bad.tick()
    assert "isn't a Lens join token" in bad.refused


def test_a_primary_out_of_reach_is_tried_again_later(client, db, cfg, nebula, folders, monkeypatch):  # noqa: F811
    import httpx

    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={"role": "compute"}, headers=h).json()

    class Down:
        def post(self, *a, **k):
            raise httpx.ConnectError("no route to host")

    a = mesh_node.Agent(*folders, "http://testserver", made["secret"], "pi", http=Down())
    folders[1].mkdir(parents=True)
    (folders[1] / "host.pub").write_text(mesh.keygen(cfg)[1])
    a.tick()
    assert "no route to host" in a.error and a.next_try > 0 and not a.refused
    a.http, a.next_try = client, 0.0
    a.tick()
    assert a.node and a.error == "waiting for Nebula to start"


def test_a_machines_name_is_one_the_mesh_takes():
    assert mesh_node.machine_name("pi") == "pi"
    assert mesh_node.machine_name("--weird/host.local") == "weird-host.local"
    assert mesh_node.machine_name("x" * 80) == "x" * 63
    assert mesh_node.machine_name("///") == "node"
    assert all(mesh.NAME_RX.fullmatch(mesh_node.machine_name(n)) for n in ("pi", "a b", "é-mac", "1"))


def test_the_primarys_mesh_address_is_a_host_only_for_nodes_calls(client, db, cfg, nebula):  # noqa: F811
    at = {"host": "10.73.0.1:8080"}
    assert client.get("/api/v1/mesh/node/config", headers=at).status_code == 400  # the mesh is off: not a host here
    h = admin(client, db)
    client.post("/api/v1/mesh/tokens", json={"role": "compute"}, headers=h)
    assert client.get("/api/v1/mesh/node/config", headers=at).status_code == 401  # let in, and still needs a node token
    assert client.get("/api/v1/auth/status", headers=at).status_code == 400  # nothing else is served at that address
    assert client.get("/api/v1/mesh", headers={**at, **h}).status_code == 400


def test_the_install_line_names_this_install_and_the_token(cfg):
    token = "lensjoin1.7." + "a" * 43
    line = mesh.join_line(cfg, token, "http://192.168.1.20:3000/")
    assert line == (
        "curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh"
        f" | LENS_JOIN=http://192.168.1.20:3000 LENS_JOIN_TOKEN={token} sh"
    )
    for bad in ("http://x; rm -rf ~", "file:///etc", "http://a b"):
        with pytest.raises(ValueError):
            mesh.join_line(cfg, token, bad)
    with pytest.raises(ValueError):
        mesh.join_line(cfg, "lensjoin1.7.$(id)", "http://lens.home")
