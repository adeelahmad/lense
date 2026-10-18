"""Auto scaling on DigitalOcean and Hetzner Cloud (docs/scaling.md "Providers"): the drivers against their REST APIs
(an httpx MockTransport for each cloud), and a group on another machine waiting for the mesh before it
launches anything, and counting only the jobs a worker node takes."""

from __future__ import annotations

import datetime as dt
import json

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


def test_the_cloud_drivers_are_listed_and_need_the_mesh(client, h):
    ds = {d["id"]: d for d in client.get("/api/v1/scaling", headers=h).json()["drivers"]}
    assert ds["digitalocean"]["needs_join"] is True and ds["hetzner"]["priced"] is True
    assert ds["local"]["needs_join"] is False
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
