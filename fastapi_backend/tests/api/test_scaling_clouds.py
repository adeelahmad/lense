"""Auto scaling on DigitalOcean, Hetzner Cloud and Lambda (docs/scaling.md "Providers"): the drivers against their REST APIs
(an httpx MockTransport for each cloud), and a group on another machine waiting for the mesh before it
launches anything, and counting only the jobs a worker node takes."""

from __future__ import annotations

import datetime as dt
import http.server
import json
import re
import shlex
import ssl
import threading

import httpx
import pytest

from app.domain import scaling, settings, store
from app.domain import scaling_drivers as drivers
from app.domain.mesh import WORKER_STEPS
from tests import openapi_contract as contract
from tests.helpers import login, make_user

R = store.R
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)


class Cloud:
    """One cloud's API in memory, held to the cloud's own published API document (tests/data/openapi/): every request
    must be one it takes, and every answer is built from its schemas, so it carries every field the real one does."""

    spec = None

    def __init__(self):
        self.calls, self.machines, self.next = [], {}, 100

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body, request.headers.get("authorization")))
        contract.check_request(self.spec, request.method, request.url.path, body, dict(request.url.params))
        status, answer = self.answer(request.method, request.url.path, dict(request.url.params), body)
        if status < 400:
            answer = contract.answer(self.spec, request.method, request.url.path, status, answer)
        return httpx.Response(status, json=answer) if answer is not None else httpx.Response(status)


class DO(Cloud):
    spec = contract.load("digitalocean")

    def answer(self, method, path, params, body):
        if path == "/v2/regions":
            return 200, {"regions": [{"slug": "nyc3", "available": True}, {"slug": "fra1", "available": True}]}
        if path == "/v2/sizes":
            return 200, {"sizes": [{"slug": "s-1vcpu-2gb", "price_hourly": 0.01786}]}
        if path == "/v2/droplets" and method == "POST":
            self.next += 1
            self.machines[self.next] = {"id": self.next, "name": body["name"], "status": "new", "tags": body["tags"]}
            self.machines[self.next]["body"] = body
            return 202, {"droplet": self._shown(self.machines[self.next])}
        if path == "/v2/droplets":
            tag = params.get("tag_name")
            return 200, {"droplets": [self._shown(m) for m in self.machines.values() if tag in m["tags"]]}
        did = int(path.rsplit("/", 1)[1])
        if did not in self.machines:
            return 404, {"id": "not_found", "message": "The resource you requested could not be found."}
        if method == "DELETE":
            del self.machines[did]
            return 204, None
        return 200, {"droplet": self._shown(self.machines[did])}

    @staticmethod
    def _shown(m):
        return {k: v for k, v in m.items() if k != "body"}


class Hz(Cloud):
    spec = contract.load("hetzner-cloud")

    def answer(self, method, path, params, body):
        if path == "/v1/locations":
            return 200, {"locations": [{"name": "fsn1"}, {"name": "hel1"}]}
        if path == "/v1/server_types":
            prices = [{"location": "fsn1", "price_hourly": {"net": "0.0060", "gross": "0.0071"}}]
            return 200, {"server_types": [{"name": params.get("name"), "prices": prices}]}
        if path == "/v1/servers" and method == "POST":
            self.next += 1
            self.machines[self.next] = {"id": self.next, "name": body["name"], "status": "initializing", "labels": body["labels"]}
            self.machines[self.next]["public_net"] = {"ipv4": {"ip": "203.0.113.7"}}
            return 201, {"server": self.machines[self.next]}
        if path == "/v1/servers":
            return 200, {"servers": list(self.machines.values())}
        sid = int(path.rsplit("/", 1)[1])
        if sid not in self.machines:
            return 404, {"error": {"code": "not_found", "message": "server not found"}}
        if method == "DELETE":
            del self.machines[sid]
            return 200, {"action": {}}
        return 200, {"server": self.machines[sid]}


class Lam(Cloud):
    spec = contract.load("lambda-cloud")

    def __init__(self, keys=("ada-laptop",)):
        super().__init__()
        self.keys, self.rulesets = list(keys), {}

    def _type(self, name, cents, regions):
        item = {"instance_type": {"name": name, "price_cents_per_hour": cents}, "regions_with_capacity_available": regions}
        return contract._sample(self.spec, {"$ref": "#/components/schemas/InstanceTypesItem"}, item)

    def answer(self, method, path, params, body):
        path = path.removeprefix("/api/v1")
        if path == "/instance-types":
            here = [{"name": "us-east-1", "description": "Virginia, USA"}]
            return 200, {
                "data": {"gpu_1x_a10": self._type("gpu_1x_a10", 75, here), "gpu_8x_h100_sxm5": self._type("gpu_8x_h100_sxm5", 2392, [])}
            }
        if path == "/firewall-rulesets" and method == "GET":
            return 200, {"data": list(self.rulesets.values())}
        if path == "/firewall-rulesets":
            rid = f"c4d291f47f9d436fa39f58493ce3b5{len(self.rulesets):02d}"
            region = {"name": body["region"], "description": "Virginia, USA"}
            made = {"id": rid, "name": body["name"], "region": region, "rules": body["rules"], "created": "2026-10-19T05:00:00Z"}
            self.rulesets[rid] = made | {"instance_ids": []}
            return 200, {"data": self.rulesets[rid]}
        if path == "/ssh-keys":
            return 200, {"data": [{"id": str(i), "name": k, "public_key": "ssh-ed25519 AAAA"} for i, k in enumerate(self.keys)]}
        if path == "/instance-operations/launch":
            self.next += 1
            iid = f"0920582c7ff041399e34823a0be{self.next}"
            self.machines[iid] = {"id": iid, "name": body["name"], "status": "booting", "tags": body["tags"], "body": body}
            return 200, {"data": {"instance_ids": [iid]}}
        if path == "/instance-operations/terminate":
            gone = [i for i in body["instance_ids"] if i in self.machines]
            if not gone:
                return 404, {"error": {"code": "global/object-does-not-exist", "message": "Specified instance does not exist."}}
            for i in gone:
                self.machines[i]["status"] = "terminating"
            return 200, {"data": {"terminated_instances": [self._shown(self.machines[i]) for i in gone]}}
        if path == "/instances":
            ids = sorted(self.machines)
            start = int(params.get("page_token") or 0)
            size = int(params.get("page_size") or 100)
            more = start + size < len(ids)
            page = [self._shown(self.machines[i]) for i in ids[start : start + size]]
            return 200, {"data": page, "page_token": str(start + size) if more else None}
        iid = path.rsplit("/", 1)[1]
        if iid not in self.machines:
            return 404, {"error": {"code": "global/object-does-not-exist", "message": "Specified instance does not exist."}}
        return 200, {"data": self._shown(self.machines[iid])}

    def _shown(self, m):
        return contract._sample(self.spec, {"$ref": "#/components/schemas/Instance"}, {k: v for k, v in m.items() if k != "body"})


@pytest.fixture
def lam_models(monkeypatch):
    cloud = Lam()
    cloud.healthy = set()
    drv = drivers.get("lambda-models")
    monkeypatch.setattr(drv, "transport", httpx.MockTransport(cloud))
    monkeypatch.setattr(drv, "probe", httpx.MockTransport(lambda req: httpx.Response(200 if req.url.host in cloud.healthy else 502)))
    return cloud


@pytest.fixture
def do(monkeypatch):
    cloud = DO()
    monkeypatch.setattr(drivers.get("digitalocean"), "transport", httpx.MockTransport(cloud))
    return cloud


@pytest.fixture
def hz(monkeypatch):
    cloud = Hz()
    monkeypatch.setattr(drivers.get("hetzner"), "transport", httpx.MockTransport(cloud))
    return cloud


@pytest.fixture
def lam(monkeypatch):
    cloud = Lam()
    monkeypatch.setattr(drivers.get("lambda"), "transport", httpx.MockTransport(cloud))
    return cloud


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


class Mesh:
    """The mesh with its install line, as mesh.py will have it."""

    def __init__(self):
        self.tokens = []

    def conf(self, cfg):
        return {"enabled": True}

    def make_token(self, db, cfg, by=None, role="worker", name=None, namespaces=None, hours=24, uses=1):
        self.tokens.append({"by": by, "role": role, "name": name, "hours": hours, "uses": uses})
        return {"id": len(self.tokens)}, f"lensjoin1.{len(self.tokens)}.secret"

    def join_line(self, cfg, token):
        return f"curl -fsSL https://lens.example/install.sh | LENS_JOIN=http://lens.home:3000 LENS_JOIN_TOKEN={token} sh"


def tick(db, cfg, now=NOW):
    return scaling.Scaler(db, lambda: cfg, "test").tick(now=now, lease=False)


def test_a_digitalocean_group_waits_for_the_install_line_then_launches_droplets(client, db, cfg, h, do, monkeypatch):
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    body = {"name": "DO", "driver": "digitalocean", "settings": {"region": "fra1"}, "secrets": {"token": "dop_v1_abc"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "regions": 2} and p["secrets"] == {"token": True}
    assert do.calls[0][4] == "Bearer dop_v1_abc"  # opened only for the call
    assert "dop_v1_abc" not in str(db.one("SELECT sealed FROM $r", r=R("scale_provider", p["id"])))
    bad = {**body, "settings": {"region": "mars1"}}
    assert "no region mars1" in client.post("/api/v1/scaling/providers", headers=h, json=bad).json()["check"]["error"]

    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Workers", "provider": p["id"], "min": 1, "max": 2, "budget": 30, "template": {"steps": ["transcribe"]}},
    ).json()
    assert g["approved"] is True and g["needs_join"] is True
    assert g["template"] == {"size": "s-1vcpu-2gb", "image": "ubuntu-24-04-x64", "steps": ["transcribe"]}
    assert tick(db, cfg)["launched"] == 0 and not do.machines  # no way for a droplet to join yet: none is started
    (view,) = client.get("/api/v1/scaling", headers=h).json()["groups"]
    assert "Settings › Network" in view["waiting_for"]  # the mesh is off

    mesh = Mesh()
    monkeypatch.setattr(scaling, "mesh_module", lambda: mesh)
    assert client.get("/api/v1/scaling", headers=h).json()["groups"][0]["waiting_for"] is None
    assert tick(db, cfg)["launched"] == 1
    ((did, m),) = do.machines.items()
    (n,) = scaling.nodes(db, g["id"])
    assert n["external_id"] == str(did) and n["price"] == pytest.approx(0.01786)  # DigitalOcean's list price
    sent = m["body"]
    assert sent["region"] == "fra1" and sent["size"] == "s-1vcpu-2gb" and sent["tags"] == ["lens"] and sent["name"] == n["name"]
    assert sent["user_data"].startswith("#!/bin/sh\n")
    assert "LENS_JOIN_TOKEN=lensjoin1.1.secret" in sent["user_data"] and "export LENS_WORKER_STEPS=transcribe" in sent["user_data"]
    assert f"export LENS_SCALING_UNIT={n['name']}" in sent["user_data"]
    assert mesh.tokens == [{"by": "scaling", "role": "worker", "name": n["name"], "hours": 1, "uses": 1}]  # one use, one hour

    drv = drivers.get("digitalocean")
    opened = scaling.opened(db, cfg, p["id"])
    assert drv.describe(opened, n)["state"] == "pending"
    do.machines[did]["status"] = "active"
    do.machines[did]["networks"] = {"v4": [{"type": "private", "ip_address": "10.0.0.2"}, {"type": "public", "ip_address": "198.51.100.4"}]}
    assert drv.describe(opened, n) == {"state": "running", "address": "198.51.100.4"}
    assert drv.listing(opened, "lens") == [{"id": str(did), "name": n["name"]}]
    drv.terminate(opened, n)
    assert not do.machines and drv.describe(opened, n) == {"state": "gone"}
    drv.terminate(opened, n)  # already gone: no error


def test_hetzner_prices_in_euros_and_its_errors_read_as_hetzners(client, db, cfg, h, hz):
    body = {"name": "Hetzner", "driver": "hetzner", "settings": {"usd_per_eur": 1.2}, "secrets": {"token": "hz"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "locations": 2} and p["settings"]["location"] == "fsn1"
    drv = drivers.get("hetzner")
    opened = scaling.opened(db, cfg, p["id"])
    assert drv.price(opened, {"server_type": "cx22"}) == pytest.approx(0.0071 * 1.2)

    boot = {"env": {"LENS_SCALING_UNIT": "lens-w-1"}, "line": "echo join", "tag": "lens"}
    got = drv.launch(opened, {"server_type": "cx22", "image": "ubuntu-24.04"}, "lens-w-1", boot)
    assert got["address"] == "203.0.113.7"
    sent = hz.calls[-1][3]
    assert sent["labels"] == {"lens": "lens"} and sent["location"] == "fsn1" and sent["user_data"].endswith("echo join\n")
    node = {"external_id": str(got["id"])}
    assert drv.describe(opened, node)["state"] == "pending"
    hz.machines[got["id"]]["status"] = "running"
    assert drv.describe(opened, node)["state"] == "running"
    hz.machines[got["id"]]["status"] = "off"
    assert drv.describe(opened, node)["state"] == "gone"  # switched off: the scaler lets it go
    assert drv.listing(opened, "lens") == [{"id": str(got["id"]), "name": "lens-w-1"}]
    drv.terminate(opened, node)
    with pytest.raises(drivers.DriverError, match="Hetzner said 404: server not found"):
        drv.call(opened, "GET", f"/servers/{got['id']}")


def test_a_lambda_group_launches_gpu_instances_that_join_over_the_mesh(client, db, cfg, h, lam, monkeypatch):
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    monkeypatch.setattr(scaling, "mesh_module", lambda: Mesh())
    body = {"name": "Lambda", "driver": "lambda", "settings": {}, "secrets": {"token": "secret_lens_abc"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "instance_types": 2, "with_capacity_here": 1} and p["settings"]["region"] == "us-east-1"
    assert lam.calls[0][4] == "Bearer secret_lens_abc"
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "GPUs", "provider": p["id"], "min": 1, "max": 1, "budget": 30, "template": {"steps": ["transcribe"]}},
    ).json()
    assert g["template"]["instance_type"] == "gpu_1x_a10" and g["needs_join"] is True
    assert tick(db, cfg)["launched"] == 1
    ((iid, m),) = lam.machines.items()
    (n,) = scaling.nodes(db, g["id"])
    assert n["external_id"] == iid and n["price"] == pytest.approx(0.75)  # Lambda's cents an hour
    sent = m["body"]
    assert sent["region_name"] == "us-east-1" and sent["instance_type_name"] == "gpu_1x_a10" and sent["name"] == n["name"]
    assert sent["ssh_key_names"] == ["ada-laptop"]  # the account's first key: Lambda puts one on every instance
    assert sent["tags"] == [{"key": "lens", "value": "lens"}, {"key": "lens-unit", "value": n["name"]}]
    assert "LENS_JOIN_TOKEN=lensjoin1.1.secret" in sent["user_data"] and "export LENS_WORKER_STEPS=transcribe" in sent["user_data"]

    drv, opened = drivers.get("lambda"), scaling.opened(db, cfg, p["id"])
    assert drv.describe(opened, n)["state"] == "pending"
    lam.machines[iid].update(status="active", ip="198.51.100.9")
    assert drv.describe(opened, n) == {"state": "running", "address": "198.51.100.9"}
    assert drv.listing(opened, "lens") == [{"id": iid, "name": n["name"]}] and drv.listing(opened, "other") == []
    lam.machines[iid]["status"] = "preempted"
    assert drv.describe(opened, n) == {"state": "gone", "why": "Lambda took the instance back"}
    assert drv.listing(opened, "lens") == []
    lam.machines[iid]["status"] = "active"
    drv.terminate(opened, n)
    assert drv.describe(opened, n) == {"state": "gone"} and drv.listing(opened, "lens") == []
    drv.terminate(opened, {"external_id": "nope"})  # already gone: no error
    with pytest.raises(drivers.DriverError, match=r"Lambda said 404: Specified instance does not exist. \(global/object-does-not-exist\)"):
        drv.call(opened, "GET", "/instances/nope")


def test_lambda_needs_an_ssh_key_and_pages_through_instances(client, db, cfg, h, lam):
    lam.keys = []
    body = {"name": "Lambda", "driver": "lambda", "settings": {"ssh_key": "ops"}, "secrets": {"token": "t"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert "add one to your Lambda account" in p["check"]["error"]
    drv, opened = drivers.get("lambda"), scaling.opened(db, cfg, p["id"])
    boot = {"env": {}, "line": "echo join", "tag": "lens"}
    names = [f"lens-g-{i}" for i in range(102)]
    for name in names:  # a key named in settings needs no lookup
        drv.launch(opened, {"instance_type": "gpu_1x_a10"}, name, boot)
    assert {m["body"]["ssh_key_names"][0] for m in lam.machines.values()} == {"ops"}
    assert sorted(x["name"] for x in drv.listing(opened, "lens")) == sorted(names)  # two pages of 100
    assert [c[2] for c in lam.calls if c[1] == "/api/v1/instances"] == [{"page_size": "100"}, {"page_size": "100", "page_token": "100"}]
    opened["settings"].pop("ssh_key")
    with pytest.raises(drivers.DriverError, match="add one to your Lambda account"):
        drv.launch(opened, {"instance_type": "gpu_1x_a10"}, "lens-g-x", boot)
    assert drv.price(opened, {"instance_type": "gpu_8x_h100_sxm5"}) == pytest.approx(23.92)
    assert drv.price(opened, {"instance_type": "gpu_0x_none"}) is None


def _written(script, path):
    """What the first-boot script writes to `path` (printf '%s' '<text>' > path)."""
    m = re.search(r"^printf '%s' ('[^']*') > " + re.escape(path) + "$", script, re.M)
    assert m, f"nothing written to {path}"
    return shlex.split(m[1])[0]


def _https(tmp_path, cert, key, answer):
    """A real HTTPS server on this machine with the unit's certificate, answering chat calls."""
    (tmp_path / "cert.pem").write_text(cert)
    (tmp_path / "key.pem").write_text(key)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    heard = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            heard.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, heard


def test_a_lambda_model_group_serves_https_with_a_certificate_lens_signed(client, db, cfg, h, lam_models, tmp_path, monkeypatch):
    from cryptography import x509

    from app.domain import llm

    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    eff = lambda: settings.effective(db, base)  # noqa: E731
    scaler = scaling.Scaler(db, eff, "test")
    body = {"name": "Lambda GPUs", "driver": "lambda-models", "settings": {}, "secrets": {"token": "t", "hf_token": "hf_x"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"]["ok"] is True and p["kind"] == "model"
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Qwen", "provider": p["id"], "min": 2, "max": 2, "budget": 40, "template": {"model": "Qwen/Qwen3-8B"}},
    ).json()
    assert g["template"]["instance_type"] == "gpu_1x_a10" and g["needs_join"] is False
    scaler.tick(now=NOW, lease=False)
    (rules,) = lam_models.rulesets.values()  # one ruleset for the region, shared by every unit
    assert rules["name"] == "lens-models" and rules["region"]["name"] == "us-east-1"
    assert rules["rules"] == [
        {"protocol": "tcp", "port_range": [8000, 8000], "source_network": "0.0.0.0/0", "description": rules["rules"][0]["description"]}
    ]
    (a, m1), (b, m2) = sorted(lam_models.machines.items())
    for m in (m1, m2):
        assert m["body"]["firewall_rulesets"] == [{"id": rules["id"]}]
    script = m1["body"]["user_data"]
    cert, key = _written(script, "/opt/lens/cert.pem"), _written(script, "/opt/lens/key.pem")
    run = shlex.split(next(line for line in script.splitlines() if line.startswith("docker run")))
    assert "--gpus" in run and run[run.index("--model") + 1] == "Qwen/Qwen3-8B" and "--ssl-certfile" in run
    vllm_key = next(e for e in run if e.startswith("VLLM_API_KEY=")).split("=", 1)[1]
    assert "HF_TOKEN=hf_x" in run and vllm_key in m2["body"]["user_data"]  # one key for the group's units
    row = db.one("SELECT unit_ca FROM $r", r=R("scale_group", g["id"]))["unit_ca"]
    assert "PRIVATE KEY" not in str(row["key"]) and "PRIVATE KEY" not in script.replace(key, "")  # the CA's key stays sealed
    ca = x509.load_pem_x509_certificate(row["cert"].encode())
    x509.load_pem_x509_certificate(cert.encode()).verify_directly_issued_by(ca)
    assert _written(m2["body"]["user_data"], "/opt/lens/cert.pem") != cert  # each unit its own certificate

    monkeypatch.setattr(drivers.get("lambda"), "transport", httpx.MockTransport(lam_models))  # the same account
    workers = {"name": "Lambda workers", "driver": "lambda", "settings": {}, "secrets": {"token": "t"}}
    client.post("/api/v1/scaling/providers", headers=h, json=workers)
    scaler.tick(now=NOW + dt.timedelta(seconds=30), lease=False)
    assert all(not x["strays"] for x in client.get("/api/v1/scaling/providers", headers=h).json())  # each knows the other's
    lam_models.machines[a].update(status="active", ip="198.51.100.9")
    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    assert eff()["llm"]["base_url"] == "http://old.home:8080/v1"  # up, but vLLM hasn't loaded the model yet
    lam_models.healthy.add("198.51.100.9")
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    now_llm = eff()["llm"]
    assert now_llm["base_url"] == "https://198.51.100.9:8000/v1" and now_llm["model"] == "Qwen/Qwen3-8B"
    assert now_llm["api_key"] == vllm_key and now_llm["tls_ca"] == row["cert"]
    assert llm.shared(eff()) == (None, None)  # embeddings and speech don't borrow it

    server, heard = _https(tmp_path, cert, key, {"choices": [{"message": {"content": "hi from the GPU"}}]})
    try:
        here = f"https://127.0.0.1:{server.server_address[1]}/v1"
        talk = {**eff(), "llm": {**eff()["llm"], "base_url": here}}
        assert llm.chat(talk, [{"role": "user", "content": "hi"}]) == "hi from the GPU"
        assert heard == [("/v1/chat/completions", f"Bearer {vllm_key}")]
        other = scaling.unit_tls(db, eff(), g["id"] + 1000, "lens-x")["ca"]  # another group's CA signed nothing here
        for ca_ in (None, other):
            with pytest.raises(llm.LLMError, match="CERTIFICATE_VERIFY_FAILED"):
                llm.chat({**talk, "llm": {**talk["llm"], "tls_ca": ca_}}, [{"role": "user", "content": "hi"}])
    finally:
        server.shutdown()

    with pytest.raises(ValueError, match="llm.tls_ca is a CA certificate"):
        settings.save(db, base, "llm", {"tls_ca": "not a certificate"}, "root@x.io")
    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    for m in lam_models.machines.values():
        m["status"] = "terminated"
    scaler.tick(now=NOW + dt.timedelta(minutes=3), lease=False)
    back = eff()["llm"]
    assert (back["base_url"], back["tls_ca"]) == ("http://old.home:8080/v1", None)  # as it was


def test_the_cloud_drivers_are_listed_and_need_the_mesh(client, h):
    ds = {d["id"]: d for d in client.get("/api/v1/scaling", headers=h).json()["drivers"]}
    assert ds["digitalocean"]["needs_join"] is True and ds["hetzner"]["priced"] is True
    assert ds["local"]["needs_join"] is False and ds["lambda"]["needs_join"] is True and ds["lambda"]["priced"] is True
    assert ds["hetzner"]["fields"]["token"]["secret"] is True and ds["digitalocean"]["extra"] is None  # nothing to install


def test_a_group_elsewhere_counts_only_the_jobs_a_node_takes(client, db, cfg, h, do, monkeypatch):
    """A machine elsewhere is a worker node, which runs only mesh.WORKER_STEPS: a group never launches one for jobs
    it would never take (docs/mesh.md, Worker nodes)."""
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    fake = Mesh()
    fake.WORKER_STEPS = WORKER_STEPS
    monkeypatch.setattr(scaling, "mesh_module", lambda: fake)
    body = {"name": "DO", "driver": "digitalocean", "settings": {"region": "fra1"}, "secrets": {"token": "dop_v1_abc"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    rule = {"type": "step", "metric": "jobs_waiting", "above": 0, "minutes": 0, "add": 1}
    made = {"provider": p["id"], "min": 0, "max": 2, "budget": 30, "rules": [rule], "cooldown_seconds": 0, "warmup_seconds": 0}
    g = client.post("/api/v1/scaling/groups", headers=h, json={"name": "Any", **made}).json()
    assert g["unit_steps"] == ["transcribe", "diarize"] and g["waiting_for"] is None
    odd = client.post("/api/v1/scaling/groups", headers=h, json={"name": "Odd", **made, "template": {"steps": ["summarize"]}}).json()
    assert odd["unit_steps"] == [] and "transcribe and diarize" in odd["waiting_for"]

    db.q("CREATE job CONTENT {status: 'queued', next_step: 'summarize', created_at: $t}", t=NOW.isoformat())
    assert tick(db, cfg)["launched"] == 0 and not do.machines  # a summarize job: no node would take it
    db.q("CREATE job CONTENT {status: 'queued', next_step: 'transcribe', created_at: $t}", t=NOW.isoformat())
    assert tick(db, cfg)["launched"] == 1  # the Any group, for the transcription; Odd still launches nothing
    assert [len(scaling.nodes(db, x["id"])) for x in (g, odd)] == [1, 0]


def test_lens_reads_only_fields_the_clouds_answer_with():
    do, hz = DO.spec, Hz.spec
    contract.check_answer_fields(do, "GET", "/v2/regions", 200, "regions.slug", "regions.available")
    contract.check_answer_fields(do, "GET", "/v2/sizes", 200, "sizes.slug", "sizes.price_hourly")
    contract.check_answer_fields(do, "POST", "/v2/droplets", 202, "droplet.id")
    contract.check_answer_fields(do, "GET", "/v2/droplets/{droplet_id}", 200, "droplet.status", "droplet.networks.v4.ip_address")
    contract.check_answer_fields(do, "GET", "/v2/droplets/{droplet_id}", 200, "droplet.networks.v4.type")
    contract.check_answer_fields(do, "GET", "/v2/droplets", 200, "droplets.id", "droplets.name")
    contract.check_answer_fields(hz, "GET", "/locations", 200, "locations.name")
    contract.check_answer_fields(hz, "GET", "/server_types", 200, "server_types.prices.location", "server_types.prices.price_hourly.gross")
    contract.check_answer_fields(hz, "POST", "/servers", 201, "server.id", "server.public_net.ipv4.ip")
    contract.check_answer_fields(hz, "GET", "/servers/{id}", 200, "server.status", "server.public_net.ipv4.ip")
    contract.check_answer_fields(hz, "GET", "/servers", 200, "servers.id", "servers.name")
    lm = Lam.spec
    contract.check_answer_fields(lm, "GET", "/api/v1/instance-types", 200, "data")
    contract.check_fields(lm, "InstanceTypesItem", "instance_type.price_cents_per_hour", "regions_with_capacity_available.name")
    contract.check_answer_fields(lm, "GET", "/api/v1/ssh-keys", 200, "data.name")
    contract.check_answer_fields(lm, "POST", "/api/v1/instance-operations/launch", 200, "data.instance_ids")
    contract.check_answer_fields(lm, "GET", "/api/v1/instances/{id}", 200, "data.status", "data.ip")
    contract.check_answer_fields(
        lm, "GET", "/api/v1/instances", 200, "data.id", "data.name", "data.status", "data.tags.key", "data.tags.value"
    )
    contract.check_answer_fields(lm, "GET", "/api/v1/instances", 200, "page_token")
    contract.check_answer_fields(lm, "GET", "/api/v1/firewall-rulesets", 200, "data.id", "data.name", "data.region.name")
    contract.check_answer_fields(lm, "POST", "/api/v1/firewall-rulesets", 200, "data.id")
