"""Proxmox VE as an auto scaling provider (scaling_drivers.py, docs/scaling.md): each unit is a clone of a VM template
on your own Proxmox server, which joins Lens over the mesh and runs `lens worker`; letting the unit go stops and
deletes the clone. It costs nothing unless you give the provider a price.

The template needs the QEMU guest agent (`qemu-guest-agent`, with the VM's Guest Agent option on): once the clone is
up, Lens runs the first-boot script (scaling_drivers.user_data) in it through the agent, so nothing has to be put on
the Proxmox server's storage. Lens calls the Proxmox API with an API token (`user@realm!name` and its secret), over
httpx, so a provider of this kind installs nothing.
"""

from __future__ import annotations

import ssl
import threading
import time
from urllib.parse import quote

import httpx

from . import jobs
from .scaling_drivers import Driver, DriverError, _field, register, user_data

TIMEOUT = 30.0
CLONE_SECONDS = 600  # a full clone of a big disk takes a while; a linked clone, seconds
AGENT_SECONDS = 600  # how long the clone has to boot far enough for its guest agent to answer
BOOTED = "lens-booted"  # the tag a clone gets once its first-boot script ran


class Proxmox(Driver):
    id = "proxmox"
    label = "Proxmox VE"
    about = "Clones of a VM template on your Proxmox server, which join Lens over the mesh and run Lens workers."
    priced = False
    needs_join = True
    fields = {
        "url": _field("Address", required=True, help="the Proxmox API, like https://pve.home:8006"),
        "node": _field("Node", required=True, help="the Proxmox node clones run on, like pve"),
        "token_id": _field("API token id", required=True, help="user@realm!name, like lens@pve!scaling"),
        "token_secret": _field("API token secret", required=True, secret=True),
        "verify_tls": _field("Check the certificate", "boolean", default=True, help="off only for a test server"),
        "ca_cert": _field("CA certificate", help="the PEM of the CA that signed the server's certificate, when it isn't public"),
    }
    template = {
        "vm_template": _field("VM template", "integer", required=True, help="the id of the template to clone"),
        "full_clone": _field("Full clone", "boolean", default=False, help="a full copy of the disk; off: a linked clone"),
        "storage": _field("Storage", help="where a full clone's disk goes; none: the template's"),
        "pool": _field("Pool", help="the resource pool clones join"),
        "steps": _field(
            "Steps",
            "list",
            help="the steps these workers run (transcribe, analyze, embed...); none: every step",
            options=sorted(jobs.STEPS),
        ),
    }
    transport: httpx.BaseTransport | None = None  # tests hand in an httpx.MockTransport
    background = True  # tests run the boot inline
    pause = staticmethod(time.sleep)

    def __init__(self):
        self.booting: dict[str, str | None] = {}  # clone name -> None while it boots, or why its boot failed
        self.lock = threading.Lock()

    # ---------- the API ----------
    def _verify(self, p):
        s = p["settings"]
        if not s.get("verify_tls", True):
            return False
        if s.get("ca_cert"):
            try:
                return ssl.create_default_context(cadata=s["ca_cert"])
            except ssl.SSLError as e:
                raise DriverError(f"the CA certificate can't be read: {e.reason}") from None
        return True

    def _client(self, p):
        s = p["settings"]
        return httpx.Client(
            base_url=s["url"].rstrip("/") + "/api2/json",
            headers={"Authorization": f"PVEAPIToken={s['token_id']}={p['secrets'].get('token_secret') or ''}"},
            timeout=TIMEOUT,
            verify=self._verify(p) if self.transport is None else True,
            transport=self.transport,
        )

    def call(self, p, method, path, gone_ok=False, **kw):
        """The answer's `data`; None when `gone_ok` and the VM isn't there. DriverError otherwise."""
        try:
            with self._client(p) as c:
                r = c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise DriverError(f"couldn't reach Proxmox: {type(e).__name__}") from None
        said = (r.reason_phrase or "").strip()
        if gone_ok and (r.status_code == 404 or (r.status_code == 500 and "does not exist" in (said + r.text))):
            return None
        if r.status_code >= 400:
            raise DriverError(f"Proxmox said {r.status_code}" + (f": {said}" if said else ""))
        return (r.json() if r.content else {}).get("data")

    def _vm(self, node):
        where, _, vmid = (node.get("external_id") or "").partition("/")
        return where, vmid

    def _wait(self, p, where, upid, seconds):
        """Until the task ends; DriverError if it failed or took too long."""
        end = time.monotonic() + seconds
        while True:
            st = self.call(p, "GET", f"/nodes/{where}/tasks/{quote(upid, safe='')}/status") or {}
            if st.get("status") == "stopped":
                if st.get("exitstatus") != "OK":
                    raise DriverError(f"Proxmox's task failed: {st.get('exitstatus')}")
                return
            if time.monotonic() > end:
                raise DriverError(f"Proxmox's task didn't finish within {seconds} seconds")
            self.pause(2)

    # ---------- the driver ----------
    def check(self, p):
        version = (self.call(p, "GET", "/version") or {}).get("version")
        nodes = {n.get("node") for n in self.call(p, "GET", "/nodes") or []}
        if p["settings"]["node"] not in nodes:
            raise DriverError(f"Proxmox has no node {p['settings']['node']}; it has {', '.join(sorted(nodes)) or 'none'}")
        return {"version": version}

    def launch(self, p, template, name, boot):
        where = p["settings"]["node"]
        vmid = int(self.call(p, "GET", "/cluster/nextid"))
        body = {"newid": vmid, "name": name, "full": 1 if template.get("full_clone") else 0}
        if template.get("full_clone") and template.get("storage"):
            body["storage"] = template["storage"]
        if template.get("pool"):
            body["pool"] = template["pool"]
        upid = self.call(p, "POST", f"/nodes/{where}/qemu/{template['vm_template']}/clone", data=body)
        with self.lock:
            self.booting[name] = None
        script = user_data(boot, template)
        if self.background:
            threading.Thread(target=self._boot, args=(p, where, vmid, upid, name, boot["tag"], script), daemon=True).start()
        else:
            self._boot(p, where, vmid, upid, name, boot["tag"], script)
        return {"id": f"{where}/{vmid}"}

    def _boot(self, p, where, vmid, upid, name, tag, script):
        """Wait for the clone, tag it, start it, and once its guest agent answers, run the first-boot script in it. The
        script lives only here, never on the Proxmox server: a clone whose boot was cut short (Lens restarted) never
        joins, and the scaler deletes it."""
        vm = f"/nodes/{where}/qemu/{vmid}"
        try:
            if upid:
                self._wait(p, where, upid, CLONE_SECONDS)
            self.call(p, "PUT", f"{vm}/config", data={"tags": tag})
            self._wait(p, where, self.call(p, "POST", f"{vm}/status/start"), 120)
            end = time.monotonic() + AGENT_SECONDS
            while True:
                try:
                    self.call(p, "POST", f"{vm}/agent/ping")
                    break
                except DriverError:
                    if time.monotonic() > end:
                        raise DriverError(f"the clone's guest agent didn't answer within {AGENT_SECONDS} seconds") from None
                    self.pause(5)
            self.call(p, "POST", f"{vm}/agent/exec", data={"command": ["sh", "-c", script]})
            self.call(p, "PUT", f"{vm}/config", data={"tags": f"{tag};{BOOTED}"})
            with self.lock:
                self.booting.pop(name, None)
        except Exception as e:  # noqa: BLE001 - shown on the unit when the scaler next looks
            with self.lock:
                self.booting[name] = str(e)[:300] if isinstance(e, DriverError) else f"{type(e).__name__}: {e}"[:300]

    def describe(self, p, node):
        with self.lock:
            boot = self.booting.get(node.get("name"), ...)
        if boot is None:
            return {"state": "pending"}  # still cloning or booting here
        if boot is not ...:
            raise DriverError(boot)  # the boot failed: shown on the unit, which fails when it doesn't join
        where, vmid = self._vm(node)
        st = self.call(p, "GET", f"/nodes/{where}/qemu/{vmid}/status/current", gone_ok=True)
        if st is None or st.get("status") != "running":
            return {"state": "gone"}  # stopped, or a clone whose boot this process never finished
        return {"state": "running"}

    def terminate(self, p, node):
        with self.lock:
            self.booting.pop(node.get("name"), None)
        where, vmid = self._vm(node)
        if not vmid:
            return
        vm = f"/nodes/{where}/qemu/{vmid}"
        st = self.call(p, "GET", f"{vm}/status/current", gone_ok=True)
        if st is None:
            return
        if st.get("status") == "running":
            self._wait(p, where, self.call(p, "POST", f"{vm}/status/stop"), 120)
        upid = self.call(p, "DELETE", vm, gone_ok=True, params={"purge": 1, "destroy-unreferenced-disks": 1})
        if upid:
            self._wait(p, where, upid, 300)

    def listing(self, p, tag):
        vms = self.call(p, "GET", "/cluster/resources", params={"type": "vm"}) or []
        return [
            {"id": f"{v.get('node')}/{v.get('vmid')}", "name": v.get("name")}
            for v in vms
            if tag in (v.get("tags") or "").split(";") and not v.get("template")
        ]


register(Proxmox())
