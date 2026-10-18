"""Auto scaling on Proxmox VE (docs/scaling.md "Providers"): clones of a VM template, booted and joined through the
guest agent, against an httpx MockTransport standing in for the Proxmox API."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.domain import scaling
from app.domain import scaling_drivers as drivers
from tests.helpers import login, make_user


class PVE:
    """A Proxmox node in memory: VMs by id, tasks that finish at once, a guest agent that answers once booted."""

    def __init__(self):
        self.calls, self.vms, self.next = [], {9000: {"name": "debian-lens", "template": 1, "status": "stopped"}}, 105
        self.agent_after = 0  # pings that fail before the agent answers

    def __call__(self, req):
        path = req.url.path.removeprefix("/api2/json")
        form = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(req.content.decode()).items()} if req.content else {}
        self.calls.append((req.method, path, form, req.headers.get("authorization")))
        ok = lambda data: httpx.Response(200, json={"data": data})  # noqa: E731
        if path == "/version":
            return ok({"version": "8.2.4"})
        if path == "/nodes":
            return ok([{"node": "pve"}])
        if path == "/cluster/nextid":
            return ok(str(self.next))
        if path == "/cluster/resources":
            return ok([{"node": "pve", "vmid": i, **v} for i, v in self.vms.items()])
        if path.startswith("/nodes/pve/tasks/"):
            return ok({"status": "stopped", "exitstatus": "OK"})
        parts = path.split("/")  # ['', 'nodes', 'pve', 'qemu', id, ...]
        vmid, rest = int(parts[4]), "/".join(parts[5:])
        if rest == "clone":
            self.vms[int(form["newid"])] = {"name": form["name"], "status": "stopped", "tags": ""}
            self.next += 1
            return ok("UPID:pve:clone")
        vm = self.vms.get(vmid)
        if vm is None:
            return httpx.Response(
                500, json={"data": None}, extensions={"reason_phrase": f"Configuration file '{vmid}.conf' does not exist".encode()}
            )
        if req.method == "DELETE":
            del self.vms[vmid]
            return ok("UPID:pve:destroy")
        if rest == "config":
            vm["tags"] = form["tags"]
            return ok(None)
        if rest == "status/start":
            vm["status"] = "running"
            return ok("UPID:pve:start")
        if rest == "status/stop":
            vm["status"] = "stopped"
            return ok("UPID:pve:stop")
        if rest == "status/current":
            return ok({"status": vm["status"]})
        if rest == "agent/ping":
            if self.agent_after > 0:
                self.agent_after -= 1
                return httpx.Response(500, json={"data": None}, extensions={"reason_phrase": b"QEMU guest agent is not running"})
            return ok({})
        if rest == "agent/exec":
            vm["ran"] = form["command"]
            return ok({"pid": 1})
        return httpx.Response(501)


@pytest.fixture
def pve(monkeypatch):
    api = PVE()
    drv = drivers.get("proxmox")
    monkeypatch.setattr(drv, "transport", httpx.MockTransport(api))
    monkeypatch.setattr(drv, "background", False)
    monkeypatch.setattr(drv, "pause", lambda s: None)
    return api


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


SETTINGS = {"url": "https://pve.home:8006", "node": "pve", "token_id": "lens@pve!scaling"}


def test_proxmox_clones_boots_through_the_guest_agent_and_deletes(client, db, cfg, h, pve):
    body = {"name": "Home lab", "driver": "proxmox", "settings": SETTINGS, "secrets": {"token_secret": "s3cret"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "version": "8.2.4"} and p["priced"] is False
    assert pve.calls[0][3] == "PVEAPIToken=lens@pve!scaling=s3cret"
    bad = {**body, "settings": {**SETTINGS, "node": "pve2"}}
    assert "no node pve2" in client.post("/api/v1/scaling/providers", headers=h, json=bad).json()["check"]["error"]

    drv = drivers.get("proxmox")
    opened = scaling.opened(db, cfg, p["id"])
    pve.agent_after = 2  # the clone takes a while to boot
    boot = {"env": {"LENS_SCALING_UNIT": "lens-lab-1"}, "line": "echo join", "tag": "lens"}
    got = drv.launch(opened, {"vm_template": 9000, "steps": ["ocr"]}, "lens-lab-1", boot)
    assert got == {"id": "pve/105"}
    clone = next(c for c in pve.calls if c[1].endswith("/clone"))
    assert clone[1] == "/nodes/pve/qemu/9000/clone" and clone[2] == {"newid": "105", "name": "lens-lab-1", "full": "0"}
    vm = pve.vms[105]
    assert vm["status"] == "running" and vm["tags"] == "lens;lens-booted"
    assert vm["ran"][:2] == ["sh", "-c"] and "export LENS_WORKER_STEPS=ocr" in vm["ran"][2] and vm["ran"][2].endswith("echo join\n")

    node = {"name": "lens-lab-1", "external_id": got["id"]}
    assert drv.describe(opened, node) == {"state": "running"}
    assert drv.listing(opened, "lens") == [{"id": "pve/105", "name": "lens-lab-1"}]  # the template isn't Lens's
    drv.terminate(opened, node)
    assert 105 not in pve.vms and drv.describe(opened, node) == {"state": "gone"}
    drv.terminate(opened, node)  # already gone: no error


def test_a_clone_whose_agent_never_answers_says_so(client, db, cfg, h, pve, monkeypatch):
    body = {"name": "Lab", "driver": "proxmox", "settings": SETTINGS, "secrets": {"token_secret": "s"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    drv = drivers.get("proxmox")
    monkeypatch.setattr("app.domain.scaling_proxmox.AGENT_SECONDS", 0)
    pve.agent_after = 10**6
    opened = scaling.opened(db, cfg, p["id"])
    got = drv.launch(opened, {"vm_template": 9000}, "lens-lab-2", {"env": {}, "line": "echo join", "tag": "lens"})
    with pytest.raises(drivers.DriverError, match="guest agent didn't answer"):
        drv.describe(opened, {"name": "lens-lab-2", "external_id": got["id"]})
    assert "ran" not in pve.vms[105]
    drv.terminate(opened, {"name": "lens-lab-2", "external_id": got["id"]})
    assert 105 not in pve.vms


def test_a_clone_still_booting_is_pending(pve):
    drv = drivers.get("proxmox")
    drv.booting["lens-lab-3"] = None  # its boot is still running in the background
    try:
        assert drv.describe({"settings": SETTINGS, "secrets": {}}, {"name": "lens-lab-3", "external_id": "pve/200"}) == {"state": "pending"}
    finally:
        drv.booting.pop("lens-lab-3", None)
