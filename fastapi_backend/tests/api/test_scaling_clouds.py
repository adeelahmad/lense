"""Auto scaling on DigitalOcean and Hetzner Cloud (docs/scaling.md "Providers"): the drivers against their REST APIs
(an httpx MockTransport for each cloud), and a group on another machine waiting for the mesh's install line before it
launches anything."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from app.domain import scaling, settings, store
from app.domain import scaling_drivers as drivers
from tests.helpers import login, make_user

R = store.R
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)


class Cloud:
    """One cloud's API in memory: what it was asked, and its machines."""

    def __init__(self):
        self.calls, self.machines, self.next = [], {}, 100

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body, request.headers.get("authorization")))
        return self.answer(request.method, request.url.path, dict(request.url.params), body)


class DO(Cloud):
    def answer(self, method, path, params, body):
        if path == "/v2/regions":
            return httpx.Response(200, json={"regions": [{"slug": "nyc3", "available": True}, {"slug": "fra1", "available": True}]})
        if path == "/v2/sizes":
            return httpx.Response(200, json={"sizes": [{"slug": "s-1vcpu-2gb", "price_hourly": 0.01786}]})
        if path == "/v2/droplets" and method == "POST":
            self.next += 1
            self.machines[self.next] = {"id": self.next, "name": body["name"], "status": "new", "tags": body["tags"], "body": body}
            return httpx.Response(202, json={"droplet": self.machines[self.next]})
        if path == "/v2/droplets":
            tag = params.get("tag_name")
            return httpx.Response(200, json={"droplets": [m for m in self.machines.values() if tag in m["tags"]]})
        did = int(path.rsplit("/", 1)[1])
        if did not in self.machines:
            return httpx.Response(404, json={"id": "not_found", "message": "The resource you requested could not be found."})
        if method == "DELETE":
            del self.machines[did]
            return httpx.Response(204)
        return httpx.Response(200, json={"droplet": self.machines[did]})


class Hz(Cloud):
    def answer(self, method, path, params, body):
        if path == "/v1/locations":
            return httpx.Response(200, json={"locations": [{"name": "fsn1"}, {"name": "hel1"}]})
        if path == "/v1/server_types":
            prices = [{"location": "fsn1", "price_hourly": {"net": "0.0060", "gross": "0.0071"}}]
            return httpx.Response(200, json={"server_types": [{"name": params.get("name"), "prices": prices}]})
        if path == "/v1/servers" and method == "POST":
            self.next += 1
            self.machines[self.next] = {"id": self.next, "name": body["name"], "status": "initializing", "labels": body["labels"]}
            self.machines[self.next]["public_net"] = {"ipv4": {"ip": "203.0.113.7"}}
            return httpx.Response(201, json={"server": self.machines[self.next]})
        if path == "/v1/servers":
            return httpx.Response(200, json={"servers": list(self.machines.values())})
        sid = int(path.rsplit("/", 1)[1])
        if sid not in self.machines:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "server not found"}})
        if method == "DELETE":
            del self.machines[sid]
            return httpx.Response(200, json={"action": {}})
        return httpx.Response(200, json={"server": self.machines[sid]})


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
    """The mesh with its install line, and nodes that run workers, as mesh.py will have it."""

    WORKER_NODES = True

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
    assert "turn it on" in view["waiting_for"]  # the mesh is off

    from app.domain import mesh as real_mesh

    monkeypatch.setattr(real_mesh, "conf", lambda cfg: {"enabled": True})
    (view,) = client.get("/api/v1/scaling", headers=h).json()["groups"]
    assert "nodes that run Lens workers" in view["waiting_for"]  # the mesh joins machines, but they don't work yet
    assert tick(db, cfg)["launched"] == 0 and not do.machines

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
