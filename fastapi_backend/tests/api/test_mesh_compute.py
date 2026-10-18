"""Compute nodes (docs/mesh.md, Compute nodes): a machine that joined as a compute node runs its own OpenShell gateway,
hands this install the gateway's client certificate once, and reports whether the gateway answers. Lens keeps the
certificate sealed and then reaches that gateway over the mesh with mutual TLS, checking it against the name its
certificate was made for, so the assistant's code can run there. The gateway is tests/fake_openshell.py, over TLS."""

from __future__ import annotations

import datetime as dt
import json

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from openshell._proto import openshell_pb2_grpc

from app.domain import mesh, sandbox, store
from tests import fake_openshell
from tests.api.test_mesh import admin, nebula, server_db  # noqa: F401 - fixtures
from tests.api.test_mesh_node import agent, due, running


def _pem(obj):
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM).decode()
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def _cert(subject, key, issuer=None, issuer_key=None, ca=False, sans=()):
    now = dt.datetime.now(dt.UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer or subject)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if sans:
        b = b.add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
    return b.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture(scope="module")
def pki():
    """What openshell-init makes: a CA, the gateway's certificate for openshell-gateway, and the client's."""
    ca_key, srv_key, cli_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca = _cert("openshell-ca", ca_key, ca=True)
    srv = _cert("openshell-gateway", srv_key, "openshell-ca", ca_key, sans=[x509.DNSName("openshell-gateway")])
    cli = _cert("openshell-client", cli_key, "openshell-ca", ca_key)
    return {"ca": _pem(ca), "server": (_pem(srv_key), _pem(srv)), "cert": _pem(cli), "key": _pem(cli_key)}


@pytest.fixture
def gateway(pki, tmp_path):
    """The compute node's gateway, which lets in only the client certificate its CA signed."""
    gw = fake_openshell.Gateway(tmp_path / "boxes")
    server = grpc.server(fake_openshell.concurrent.futures.ThreadPoolExecutor(max_workers=4))
    openshell_pb2_grpc.add_OpenShellServicer_to_server(gw, server)
    creds = grpc.ssl_server_credentials(
        [(pki["server"][0].encode(), pki["server"][1].encode())], root_certificates=pki["ca"].encode(), require_client_auth=True
    )
    port = server.add_secure_port("127.0.0.1:0", creds)
    server.start()
    yield gw, port
    server.stop(None)


@pytest.fixture
def key_dir(tmp_path, pki, monkeypatch):
    """The sandbox-key volume as openshell-key fills it on the node."""
    d = tmp_path / "sandbox-key"
    d.mkdir()
    for k, f in (("ca", "ca.crt"), ("cert", "tls.crt"), ("key", "tls.key")):
        (d / f).write_text(pki[k])
    monkeypatch.setenv("LENS_SANDBOX_TLS_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def no_nodes():
    sandbox.register_nodes(None, None)
    yield
    sandbox.register_nodes(None, None)


class Answers:
    """The gateway's port as `lens node` checks it: answering, or not."""

    def __init__(self):
        self.up, self.asked = True, []

    def __call__(self, addr, timeout=None):
        self.asked.append(addr)
        if not self.up:
            raise ConnectionRefusedError(111, "Connection refused")
        return self

    def close(self):
        pass


def on_mesh(client, cfg, h, folders, role="compute"):
    made = client.post("/api/v1/mesh/tokens", json={"role": role}, headers=h).json()
    a = agent(client, folders, made["secret"], name="gpu box")
    a.gateway.connect = Answers()
    a.tick()
    (folders[1] / "host.pub").write_text(mesh.keygen(cfg)[1])
    running(folders[1])
    a.tick()
    return made, a


@pytest.fixture
def folders(tmp_path):
    return tmp_path / "node", tmp_path / "mesh"


def test_a_compute_nodes_install_line_starts_its_gateway(client, db, cfg, nebula, server_db):  # noqa: F811
    h = admin(client, db)
    compute = client.post("/api/v1/mesh/tokens", json={"role": "compute"}, headers=h).json()
    worker = client.post("/api/v1/mesh/tokens", json={"role": "worker"}, headers=h).json()
    assert compute["install_line"].endswith(" LENS_NODE_SANDBOX=1 sh")
    assert "LENS_NODE_SANDBOX" not in worker["install_line"]


def test_a_compute_node_hands_over_its_gateway_and_the_assistant_may_run_code_there(client, db, cfg, nebula, key_dir, pki, folders):  # noqa: F811
    h = admin(client, db)
    assert not sandbox.nodes_on()
    _, a = on_mesh(client, cfg, h, folders)
    nid = a.node["node"]["id"]
    row = db.one("SELECT sandbox_tls FROM $r", r=store.R("mesh_node", nid))
    assert row["sandbox_tls"] and "PRIVATE KEY" not in json.dumps(row)  # kept sealed
    assert a.node["sandbox_sent"] and a.gateway.connect.asked == [("openshell-gateway", 17670)]
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert me["status"]["sandbox"] == {"gateway": "running"} and me["status"].get("error") is None
    assert any("gave the install the sandbox gateway's certificate" in line for line in me["status"]["log"])

    # the sandbox now has the node to run on, ready
    assert sandbox.nodes_on()
    listed = {n["id"]: n for n in sandbox.node_list(db)["nodes"]}
    assert listed[nid]["ready"] is True and listed[nid]["name"] == "gpu box"

    # sent once; again only when the certificate changes
    puts = []
    real = a.http.put
    a.http.put = lambda *x, **k: puts.append(x) or real(*x, **k)
    due(a)
    a.tick()
    assert not puts

    # the gateway stops answering: the node says so, and the sandbox won't pick it
    a.gateway.connect.up = False
    due(a)
    a.tick()
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert "sandbox" not in me["status"] and "isn't answering at openshell-gateway:17670" in me["status"]["error"]
    assert "its sandbox gateway isn't running" in {n["id"]: n for n in sandbox.node_list(db)["nodes"]}[nid]["why_not"]

    # removing the node takes its sandboxes away from the assistant; an undo gives them back
    client.delete(f"/api/v1/mesh/nodes/{nid}", headers=h)
    assert not sandbox.nodes_on()
    client.post(f"/api/v1/mesh/nodes/{nid}/restore", headers=h)
    assert sandbox.nodes_on()


def test_without_its_gateway_a_compute_node_says_how_to_start_it(client, db, cfg, nebula, folders, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LENS_SANDBOX_TLS_DIR", str(tmp_path / "none"))
    h = admin(client, db)
    _, a = on_mesh(client, cfg, h, folders)
    me = client.get("/api/v1/mesh/nodes", headers=h).json()[1]
    assert "LENS_NODE_SANDBOX=1" in me["status"]["error"] and not sandbox.nodes_on()


def test_only_a_compute_node_gives_a_gateway_and_only_a_certificate(client, db, cfg, nebula, server_db, key_dir, pki, folders):  # noqa: F811
    h = admin(client, db)
    _, w = on_mesh(client, cfg, h, folders, role="worker")
    r = client.put("/api/v1/mesh/node/sandbox", json={"ca": pki["ca"], "cert": pki["cert"], "key": pki["key"]}, headers=w._headers())
    assert r.status_code == 403
    _, c = on_mesh(client, cfg, h, (folders[0].parent / "c", folders[1].parent / "c-mesh"))
    bad = client.put("/api/v1/mesh/node/sandbox", json={"ca": pki["ca"], "cert": "hello", "key": pki["key"]}, headers=c._headers())
    assert bad.status_code == 400 and "cert isn't PEM" in bad.json()["detail"]
    nobody = client.put("/api/v1/mesh/node/sandbox", json={"ca": pki["ca"], "cert": pki["cert"], "key": pki["key"]})
    assert nobody.status_code == 401


def test_lens_reaches_the_nodes_gateway_with_its_certificate_checked_by_name(db, cfg, pki, gateway, monkeypatch):
    gw, port = gateway
    monkeypatch.setattr(mesh, "AGENT_PORT", port)
    nid = 7
    db.q("CREATE $r CONTENT {role: 'compute', name: 'gpu box', ip: '127.0.0.1'}", r=store.R("mesh_node", nid))
    row = {"id": nid, "role": "compute"}
    with pytest.raises(PermissionError):
        mesh.sandbox_gateway(db, cfg, {**row, "role": "worker"}, pki)
    mesh.sandbox_gateway(db, cfg, row, pki)
    d = mesh.gateway_driver(db, cfg, {"id": nid, "ip": "127.0.0.1"})
    try:
        d.health()  # TLS checked against openshell-gateway, not 127.0.0.1, and Lens let in with the client certificate
        d.create("lens-chat-1", {"image": "python:3.12-slim", "network": "off", "hosts": [], "cpu": 1, "memory": "512Mi"})
        ran = d.exec("lens-chat-1", ["python3", "-c", "print(6 * 7)"], None, 30, 10_000)
        assert ran.stdout.strip() == b"42"
    finally:
        d.close()

    # a client the gateway's CA didn't sign isn't let in
    other_key = ec.generate_private_key(ec.SECP256R1())
    stranger = {**pki, "cert": _pem(_cert("someone", other_key)), "key": _pem(other_key)}
    d = sandbox.OpenShell(cfg, endpoint=f"127.0.0.1:{port}", pem=stranger, server_name=mesh.SANDBOX_NAME, timeout=5)
    try:
        with pytest.raises(grpc.RpcError):
            d.health()
    finally:
        d.close()
