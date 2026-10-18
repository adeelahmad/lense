"""Lens's own network between its machines (docs/mesh.md): an admin adds a node, which makes the mesh's certificate
authority and a join token; the machine joins without ever sending the token, and gets a certificate, its Nebula
config and credentials sealed for it; after that it renews, reports and fetches its config with its own node token;
removing it blocks its certificates everywhere. nebula-cert is faked (tests/fake_nebula_cert.py); the same flows were
run against Nebula 1.11.2 itself, two machines in separate network namespaces reaching each other through the mesh."""

from __future__ import annotations

import hashlib
import io
import pathlib
import secrets
import sys
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain import auth, jobs, mesh, settings, store
from tests.conftest import make_cfg
from tests.helpers import login, make_user

FAKE = pathlib.Path(__file__).resolve().parents[1] / "fake_nebula_cert.py"


@pytest.fixture
def nebula(tmp_path, monkeypatch):
    """Nebula's programs for Lens to find: the fake nebula-cert, and a nebula that only says its version."""
    d = tmp_path / "nebula"
    d.mkdir()
    (d / "nebula-cert").write_text(
        f"#!{sys.executable}\nimport sys\nsys.argv[0] = 'nebula-cert'\nexec(compile(open({str(FAKE)!r}).read(), {str(FAKE)!r}, 'exec'))\n"
    )
    (d / "nebula").write_text(f"#!{sys.executable}\nprint('Version: {mesh.NEBULA_VERSION}')\n")
    for p in d.iterdir():
        p.chmod(0o755)
    monkeypatch.setenv("LENS_NEBULA_DIR", str(d))
    return d


@pytest.fixture
def server_db(db, monkeypatch):
    """Tests run on the embedded database; a worker on another machine needs a SurrealDB server, as Docker installs have."""
    monkeypatch.setattr(db, "url", "ws://surrealdb:8000")
    return db


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def joined(client, cfg, token, name="studio", info=None):
    """A machine joining with `token`: what it sends, and the opened answer."""
    tid, secret = mesh.parse_token(token)
    priv, pub = mesh.keygen(cfg)
    nonce = secrets.token_urlsafe(16)
    body = {
        "token_id": tid,
        "nonce": nonce,
        "name": name,
        "public_key": pub,
        "proof": mesh.proof(secret, tid, nonce, name, pub),
        "info": info,
    }
    r = client.post("/api/v1/mesh/join", json=body)
    assert r.status_code == 200, r.text
    return r.json()["node"], mesh.open_answer(secret, nonce, tid, r.json()["sealed"]), priv


def test_adding_a_node_makes_the_ca_a_token_and_turns_the_mesh_on(client, db, cfg, nebula):
    h = admin(client, db)
    before = client.get("/api/v1/mesh", headers=h).json()
    assert before["enabled"] is False and before["ca"] is None and before["nodes"] == 0
    assert client.get("/api/v1/mesh/nodes", headers=h).json()[0]["role"] == "primary"  # this machine, mesh or not

    r = client.post("/api/v1/mesh/tokens", json={"role": "compute", "name": "gpu box", "hours": 48}, headers=h)
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["secret"].startswith(mesh.TOKEN_PREFIX) and made["join_url"].startswith("http://")
    assert made["install_line"].endswith(
        f"install.sh | LENS_JOIN={made['join_url']} LENS_JOIN_TOKEN={made['secret']} LENS_NODE_SANDBOX=1 sh"
    )  # a compute node's
    assert made["token"]["role"] == "compute" and made["token"]["uses"] == 1 and made["token"]["used"] == 0

    after = client.get("/api/v1/mesh", headers=h).json()
    assert after["enabled"] is True and after["tokens"] == 1 and after["primary_ip"] == "10.73.0.1"
    assert after["ca"]["fingerprint"] and after["ca"]["not_after"] > "2035"
    assert db.one("SELECT key FROM $r", r=mesh.CA_ROW)["key"].startswith("v1.")  # the CA's key is sealed, not plain
    listed = client.get("/api/v1/mesh/tokens", headers=h).json()
    assert [t["id"] for t in listed] == [made["token"]["id"]] and "secret" not in listed[0]
    nodes = client.get("/api/v1/mesh/nodes", headers=h).json()
    assert [(n["role"], n["ip"]) for n in nodes] == [("primary", "10.73.0.1")]
    actions = [r["action"] for r in db.rows("SELECT action FROM audit_log")]
    assert "mesh.token.create" in actions and "settings.save" in actions

    # withdrawn, it can't be used; a viewer can't make one
    assert client.delete(f"/api/v1/mesh/tokens/{made['token']['id']}", headers=h).status_code == 200
    assert client.get("/api/v1/mesh/tokens", headers=h).json() == []
    make_user(db, "v@x.io", "viewer password 1")
    assert client.post("/api/v1/mesh/tokens", json={}, headers=login(client, "v@x.io", "viewer password 1")).status_code == 403


def test_a_worker_node_needs_a_database_server(folder, nebula):
    """An install on the embedded database: its own app, not the fixtures' (CI runs those against a server too),
    because a worker's token is refused by the database's URL, and a connection the pool opens meanwhile goes to that
    URL as well, so pretending the fixtures' server is embedded failed a query now and then."""
    from app.main import create_app

    cfg = make_cfg(folder, url="mem://")
    db = store.connect(cfg)
    try:
        client = TestClient(create_app(cfg, db, background=False), base_url="http://127.0.0.1")
        h = admin(client, db)
        r = client.post("/api/v1/mesh/tokens", json={"role": "worker"}, headers=h)
        assert r.status_code == 400 and "SurrealDB server" in r.text
        assert client.get("/api/v1/mesh", headers=h).json()["enabled"] is False  # nothing was turned on
    finally:
        db.close()


def test_a_machine_joins_without_sending_the_token(client, db, cfg, nebula, server_db):
    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={"role": "worker", "namespaces": ["pods"]}, headers=h).json()
    token = made["secret"]
    tid, secret = mesh.parse_token(token)
    priv, pub = mesh.keygen(cfg)
    nonce = secrets.token_urlsafe(16)

    # a request made with the wrong secret, or for another name than it was signed for, is refused and counted
    wrong = {"token_id": tid, "nonce": nonce, "name": "studio", "public_key": pub, "proof": mesh.proof("not it", tid, nonce, "studio", pub)}
    r = client.post("/api/v1/mesh/join", json=wrong)
    assert r.status_code == 403 and "wasn't made with that token" in r.text
    good = mesh.proof(secret, tid, nonce, "studio", pub)
    r = client.post("/api/v1/mesh/join", json={**wrong, "name": "other", "proof": good})
    assert r.status_code == 403
    assert client.get("/api/v1/mesh/nodes", headers=h).json()[-1]["role"] == "primary"  # nothing joined

    nid, answer, _ = joined(client, cfg, token, info={"os": "Darwin", "cpus": 8})
    assert answer["node"] == {
        "id": nid,
        "name": "studio",
        "role": "worker",
        "ip": "10.73.0.2",
        "groups": ["lens-node", "lens-worker", "ns-pods"],
        "namespaces": ["pods"],
    }
    assert answer["primary"] == {"ip": "10.73.0.1", "db_port": 8000, "api_port": 8080}
    assert answer["node_token"].startswith(mesh.NODE_PREFIX) and answer["agent_key"] and answer["renew_days"] == 10
    config = answer["config"]
    assert "BEGIN NEBULA CERTIFICATE" in config["pki"]["ca"] and "BEGIN NEBULA CERTIFICATE" in config["pki"]["cert"]
    assert "key" not in config["pki"]  # the private key never left the machine, so the answer can't hold it
    assert config["lighthouse"] == {"am_lighthouse": False, "interval": 60, "hosts": ["10.73.0.1"]}
    assert config["static_host_map"] == {"10.73.0.1": ["127.0.0.1:4242"]}  # where it joined from, on the mesh's port
    assert config["tun"]["dev"] == "lens-mesh" and config["cipher"] == "aes"
    inbound = config["firewall"]["inbound"]
    assert {"port": 7443, "proto": "tcp", "group": "lens-primary"} in inbound  # only the primary reaches its agent
    assert config["firewall"]["outbound_action"] == "drop" and config["firewall"]["inbound_action"] == "drop"
    cert = mesh.describe(cfg, config["pki"]["cert"])
    assert (
        cert["name"] == f"node-{nid}" and cert["networks"] == ["10.73.0.2/16"] and cert["groups"] == ["lens-node", "lens-worker", "ns-pods"]
    )

    nodes = client.get("/api/v1/mesh/nodes", headers=h).json()
    assert [(n["role"], n["ip"], n["name"]) for n in nodes] == [("primary", "10.73.0.1", "This machine"), ("worker", "10.73.0.2", "studio")]
    assert nodes[1]["fingerprint"] == cert["fingerprint"] and nodes[1]["status"] == {"info": {"os": "Darwin", "cpus": 8}}
    assert nodes[1]["hosts"] == ["127.0.0.1"] and nodes[1]["joined_by"] == "root@x.io"
    assert client.get("/api/v1/mesh/tokens", headers=h).json() == []  # used up
    assert [r["detail"]["name"] for r in db.rows("SELECT detail FROM audit_log WHERE action = 'mesh.join'")] == ["studio"]

    # the token is spent: the same request again, or a fresh one, is refused
    r = client.post("/api/v1/mesh/join", json={**wrong, "proof": good})
    assert r.status_code == 403 and "has been used" in r.text

    # the primary gets a certificate of its own when it asks, on the same CA
    prim = mesh.primary(db, cfg, mesh.keygen(cfg)[1])
    assert prim["ip"] == "10.73.0.1" and mesh.describe(cfg, prim["crt"])["groups"] == ["lens-primary"]
    pconf = mesh.render(db, cfg, db.one(f"SELECT {mesh.NODE_FIELDS} FROM mesh_node WHERE role = 'primary'"), prim["crt"])
    assert pconf["lighthouse"]["am_lighthouse"] is True and pconf["listen"]["port"] == 4242 and pconf["relay"]["am_relay"] is True
    assert {"port": 8000, "proto": "tcp", "group": "lens-worker"} in pconf["firewall"]["inbound"]  # the database, to workers
    assert {"port": 8080, "proto": "tcp", "group": "lens-node"} in pconf["firewall"]["inbound"]  # the API, to every node


def test_too_many_wrong_joins_from_one_address_are_turned_away(client, db, cfg, nebula, server_db):
    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={}, headers=h).json()
    tid, _ = mesh.parse_token(made["secret"])
    _, pub = mesh.keygen(cfg)
    bad = {"token_id": tid, "nonce": secrets.token_urlsafe(16), "name": "x", "public_key": pub, "proof": "0" * 64}
    codes = [client.post("/api/v1/mesh/join", json=bad).status_code for _ in range(9)]
    assert codes[:8] == [403] * 8 and codes[8] == 429
    auth._FAILS.clear()
    assert client.post("/api/v1/mesh/join", json={**bad, "token_id": 999}).status_code == 403  # not this install's


def test_a_node_reports_renews_and_follows_its_config(client, db, cfg, nebula, server_db):
    h = admin(client, db)
    made = client.post("/api/v1/mesh/tokens", json={"uses": 2}, headers=h).json()
    nid, answer, _ = joined(client, cfg, made["secret"])
    nh = {"Authorization": f"Bearer {answer['node_token']}"}
    assert client.get("/api/v1/mesh/node/config").status_code == 401
    assert client.get("/api/v1/mesh/node/config", headers={"Authorization": "Bearer lensnode1.1.nope"}).status_code == 401

    got = client.get("/api/v1/mesh/node/config", headers=nh).json()
    assert got["version"] == answer["version"] and got["config"]["pki"]["blocklist"] == []

    r = client.post(
        "/api/v1/mesh/node/beat", json={"nebula": "running", "peers": [{"ip": "10.73.0.1"}], "log": ["x" * 400], "junk": 1}, headers=nh
    )
    assert r.status_code == 422  # unknown fields are refused
    r = client.post("/api/v1/mesh/node/beat", json={"nebula": "running", "peers": [{"ip": "10.73.0.1"}], "log": ["x" * 400]}, headers=nh)
    assert r.status_code == 200
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert me["last_seen"] and me["status"]["nebula"] == "running" and len(me["status"]["log"][0]) == 300

    # a second machine joins with the same token (uses: 2): its arrival changes nothing in the first one's config yet
    nid2, answer2, _ = joined(client, cfg, made["secret"], name="pi")
    assert answer2["node"]["ip"] == "10.73.0.3"
    assert client.get("/api/v1/mesh/node/config", headers=nh).json()["version"] == answer["version"]

    # the admin gives the first node a namespace: its next certificate carries it
    r = client.patch(f"/api/v1/mesh/nodes/{nid}", json={"name": "Studio Mac", "namespaces": ["calls"]}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Studio Mac" and r.json()["groups"] == ["lens-node", "lens-worker", "ns-calls"]
    assert client.patch(f"/api/v1/mesh/nodes/{nid}", json={"namespaces": ["nope"]}, headers=h).status_code == 400
    _, pub2 = mesh.keygen(cfg)
    renewed = client.post("/api/v1/mesh/node/renew", json={"public_key": pub2}, headers=nh).json()
    cert = mesh.describe(cfg, renewed["crt"])
    assert cert["groups"] == ["lens-node", "lens-worker", "ns-calls"] and cert["networks"] == ["10.73.0.2/16"]
    assert renewed["version"] != answer["version"] and renewed["config"]["pki"]["cert"] == renewed["crt"]
    assert client.post("/api/v1/mesh/node/renew", json={"public_key": "not a key"}, headers=nh).status_code == 400
    old_fp = me["fingerprint"]
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert me["fingerprint"] == cert["fingerprint"] != old_fp

    # removed: its node token stops working, and both its certificates are blocked in everyone's config
    r = client.delete(f"/api/v1/mesh/nodes/{nid}", headers=h)
    assert r.status_code == 200 and r.json()["revoked_at"] and r.json()["revoked_by"] == "root@x.io"
    assert client.get("/api/v1/mesh/node/config", headers=nh).status_code == 401
    other = client.get("/api/v1/mesh/node/config", headers={"Authorization": f"Bearer {answer2['node_token']}"}).json()
    assert sorted(other["config"]["pki"]["blocklist"]) == sorted([old_fp, cert["fingerprint"]]) and other["version"] != answer2["version"]
    status = client.get("/api/v1/mesh", headers=h).json()
    assert status["nodes"] == 1 and status["removed"] == 1
    # the primary can't be removed; a removed node comes back with undo
    primary_id = client.get("/api/v1/mesh/nodes", headers=h).json()[0]["id"]
    assert client.delete(f"/api/v1/mesh/nodes/{primary_id}", headers=h).status_code == 400
    r = client.post(f"/api/v1/mesh/nodes/{nid}/restore", headers=h)
    assert r.status_code == 200 and r.json()["revoked_at"] is None
    assert client.get("/api/v1/mesh/node/config", headers=nh).json()["config"]["pki"]["blocklist"] == []
    assert client.get("/api/v1/mesh/nodes/", headers=h).status_code in (200, 307, 404)
    assert client.get("/api/v1/mesh/node/config", headers=nh).status_code == 200
    # a node's address is never given out again
    nid3, answer3, _ = joined(client, cfg, client.post("/api/v1/mesh/tokens", json={}, headers=h).json()["secret"], name="third")
    assert answer3["node"]["ip"] == "10.73.0.4"


def test_the_nodes_list_shows_each_machines_workers(client, db, cfg, nebula, server_db, monkeypatch):
    h = admin(client, db)
    nid, answer, _ = joined(client, cfg, client.post("/api/v1/mesh/tokens", json={}, headers=h).json()["secret"])
    jobs.Worker(db, lambda: cfg, name="here-1").register()
    monkeypatch.setattr(jobs, "MESH_NODE", nid)
    jobs.Worker(db, lambda: cfg, name="studio-1").register()
    nodes = client.get("/api/v1/mesh/nodes", headers=h).json()
    assert [w["name"] for w in nodes[0]["workers"]] == ["here-1"]
    assert [w["name"] for w in nodes[1]["workers"]] == ["studio-1"] and nodes[1]["workers"][0]["node"] == nid


def test_mesh_settings_are_checked(client, db, cfg, nebula, server_db):
    h = admin(client, db)
    r = client.put("/api/v1/settings/mesh", json={"network": "8.8.0.0/16"}, headers=h)
    assert r.status_code == 400 and "private IPv4" in r.text
    assert client.put("/api/v1/settings/mesh", json={"hosts": ["lens.example.com:4242", "bad host!"]}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/mesh", json={"cipher": "rot13"}, headers=h).status_code == 400
    assert (
        client.put(
            "/api/v1/settings/mesh", json={"network": "10.9.0.0/24", "hosts": ["lens.example.com:4242", "203.0.113.5"]}, headers=h
        ).status_code
        == 200
    )
    made = client.post("/api/v1/mesh/tokens", json={}, headers=h).json()
    nid, answer, _ = joined(client, cfg, made["secret"])
    assert answer["node"]["ip"] == "10.9.0.2" and answer["primary"]["ip"] == "10.9.0.1"
    assert answer["config"]["static_host_map"] == {"10.9.0.1": ["lens.example.com:4242", "203.0.113.5:4242"]}
    # with machines on it, the network can't move
    r = client.put("/api/v1/settings/mesh", json={"network": "10.10.0.0/24"}, headers=h)
    assert r.status_code == 400 and "can't change" in r.text
    assert client.put("/api/v1/settings/mesh", json={"cert_days": 3, "relay": False}, headers=h).status_code == 200
    got = client.get("/api/v1/mesh/node/config", headers={"Authorization": f"Bearer {answer['node_token']}"}).json()
    assert got["renew_days"] == 1 and got["config"]["relay"]["use_relays"] is False
    view = client.get("/api/v1/settings", headers=h).json()["mesh"]["values"]
    assert view["enabled"] is True and view["network"] == "10.9.0.0/24"


def test_nebula_is_fetched_once_with_its_checksum_checked(cfg, monkeypatch, tmp_path):
    monkeypatch.delenv("LENS_NEBULA_DIR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert mesh.binaries(cfg) is None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in ("nebula", "nebula-cert"):
            data = f"#!/bin/sh\necho {name}\n".encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    tarball = buf.getvalue()
    asked = []

    def transport(request):
        asked.append(str(request.url))
        return httpx.Response(200, content=tarball)

    class Fake:  # httpx as mesh.py sees it, answering from `transport`
        HTTPError = httpx.HTTPError

        @staticmethod
        def stream(method, url, **kw):
            return httpx.Client(transport=httpx.MockTransport(transport)).stream(method, url, **kw)

    monkeypatch.setattr(mesh, "httpx", Fake)
    monkeypatch.setattr(mesh, "_platform", lambda: "nebula-linux-amd64.tar.gz")
    with pytest.raises(mesh.MeshError, match="checksum"):
        mesh.fetch(cfg)
    assert mesh.binaries(cfg) is None and not list((pathlib.Path(cfg["data_dir"]) / "bin").rglob("*.part"))
    monkeypatch.setitem(mesh.SHA256, "nebula-linux-amd64.tar.gz", hashlib.sha256(tarball).hexdigest())
    got = mesh.fetch(cfg)
    assert asked[-1] == mesh.RELEASES + "nebula-linux-amd64.tar.gz" and asked[-1].startswith(
        "https://github.com/slackhq/nebula/releases/download/v"
    )
    assert got == mesh.binaries(cfg) and pathlib.Path(got["nebula-cert"]).read_text().endswith("echo nebula-cert\n")
    assert mesh.tools(cfg) == got and len(asked) == 2  # found now: not fetched again


def test_a_join_answer_is_only_for_the_token_it_was_sealed_with():
    sealed = mesh.seal_answer("secret-a", "nonce-1", 7, {"node_token": "x"})
    assert mesh.open_answer("secret-a", "nonce-1", 7, sealed) == {"node_token": "x"}
    with pytest.raises(ValueError, match="right Lens"):
        mesh.open_answer("secret-b", "nonce-1", 7, sealed)
    with pytest.raises(ValueError):
        mesh.open_answer("secret-a", "nonce-1", 8, sealed)
    assert mesh.parse_token("lensjoin1.12.abcdefghijklmnopqrstuvwxyz") == (12, "abcdefghijklmnopqrstuvwxyz")
    with pytest.raises(ValueError):
        mesh.parse_token("lensnode1.12.abcdefghijklmnopqrstuvwxyz")
    assert mesh.check_setting("network", "10.73.0.1/16") == "10.73.0.0/16"
    assert mesh.check_setting("hosts", ["lens.example.com", "[2001:db8::1]:4243"]) == ["lens.example.com", "[2001:db8::1]:4243"]
    assert mesh.host_port("[2001:db8::1]", 4242) == "[2001:db8::1]:4242" and mesh.host_port("lens.home:5", 4242) == "lens.home:5"
    assert settings.EDITABLE["mesh"] is None  # every mesh setting is editable in the web app
