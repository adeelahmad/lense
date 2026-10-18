"""Cloud providers auto scaling launches machines on over their REST APIs (scaling_drivers.py, docs/scaling.md):
DigitalOcean and Hetzner Cloud. Each unit is a new machine whose first boot runs the mesh's install line
(scaling_drivers.user_data), which joins it to Lens and starts `lens worker`; terminating a unit deletes the machine.
Every machine carries Lens's tag (`scaling.tag`), so `listing` finds the ones Lens made.

They call the APIs with httpx, which Lens already has, so a provider of these kinds installs nothing.
"""

from __future__ import annotations

import httpx

from . import jobs
from .scaling_drivers import Driver, DriverError, _field, register, user_data

TIMEOUT = 30.0


def _steps():
    return _field(
        "Steps",
        "list",
        help="the steps these workers run (transcribe, analyze, embed...); none: every step",
        options=sorted(jobs.STEPS),
    )


class Rest(Driver):
    """A cloud with a JSON API and a bearer token."""

    base = ""
    needs_join = True
    transport: httpx.BaseTransport | None = None  # tests hand in an httpx.MockTransport

    def _client(self, p):
        return httpx.Client(
            base_url=self.base,
            headers={"Authorization": f"Bearer {p['secrets'].get('token') or ''}", "Accept": "application/json"},
            timeout=TIMEOUT,
            transport=self.transport,
        )

    def _error(self, r):
        """What the cloud said, in its own words."""
        return f"{self.label} said {r.status_code}"

    def call(self, p, method, path, ok404=False, **kw):
        """The JSON answer; None for a 404 when `ok404` (the thing is already gone). DriverError otherwise."""
        try:
            with self._client(p) as c:
                r = c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise DriverError(f"couldn't reach {self.label}: {type(e).__name__}") from None
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            raise DriverError(self._error(r))
        return r.json() if r.content else {}


# ---------- DigitalOcean ----------
class DigitalOcean(Rest):
    id = "digitalocean"
    label = "DigitalOcean"
    about = "Droplets that join Lens over the mesh and run Lens workers. Billed by the hour by DigitalOcean."
    base = "https://api.digitalocean.com/v2"
    fields = {
        "region": _field("Region", required=True, default="nyc3", help="where droplets start: nyc3, fra1, sgp1..."),
        "token": _field("API token", required=True, secret=True, help="a personal access token with write scope"),
        "ssh_keys": _field("SSH keys", "list", help="the ids or fingerprints of SSH keys to put on each droplet"),
        "vpc_uuid": _field("VPC", help="the VPC droplets join; none: the region's default"),
    }
    template = {
        "size": _field("Size", required=True, default="s-1vcpu-2gb", help="a droplet size slug"),
        "image": _field("Image", required=True, default="ubuntu-24-04-x64", help="an image slug or id"),
        "steps": _steps(),
    }

    def _error(self, r):
        try:
            said = r.json().get("message")
        except ValueError:
            said = None
        return f"DigitalOcean said {r.status_code}" + (f": {said}" if said else "")

    def check(self, p):
        regions = self.call(p, "GET", "/regions", params={"per_page": 200}).get("regions") or []
        names = {x.get("slug") for x in regions if x.get("available")}
        region = p["settings"].get("region")
        if region and names and region not in names:
            raise DriverError(f"DigitalOcean has no region {region} open; it has {', '.join(sorted(names))}")
        return {"regions": len(names)}

    def price(self, p, template):
        for s in self.call(p, "GET", "/sizes", params={"per_page": 200}).get("sizes") or []:
            if s.get("slug") == template.get("size"):
                return float(s["price_hourly"]) if s.get("price_hourly") is not None else None
        return None

    def launch(self, p, template, name, boot):
        body = {
            "name": name,
            "region": p["settings"]["region"],
            "size": template["size"],
            "image": template["image"],
            "user_data": user_data(boot, template),
            "tags": [boot["tag"]],
        }
        if p["settings"].get("ssh_keys"):
            body["ssh_keys"] = p["settings"]["ssh_keys"]
        if p["settings"].get("vpc_uuid"):
            body["vpc_uuid"] = p["settings"]["vpc_uuid"]
        d = self.call(p, "POST", "/droplets", json=body).get("droplet") or {}
        if d.get("id") is None:
            raise DriverError("DigitalOcean made no droplet")
        return {"id": d["id"]}

    def describe(self, p, node):
        d = (self.call(p, "GET", f"/droplets/{node['external_id']}", ok404=True) or {}).get("droplet")
        if not d or d.get("status") in ("off", "archive"):
            return {"state": "gone"}
        ip = next((n.get("ip_address") for n in (d.get("networks") or {}).get("v4") or [] if n.get("type") == "public"), None)
        return {"state": "running" if d.get("status") == "active" else "pending", "address": ip}

    def terminate(self, p, node):
        if node.get("external_id"):
            self.call(p, "DELETE", f"/droplets/{node['external_id']}", ok404=True)

    def listing(self, p, tag):
        got = self.call(p, "GET", "/droplets", params={"tag_name": tag, "per_page": 200}).get("droplets") or []
        return [{"id": str(d["id"]), "name": d.get("name")} for d in got]


# ---------- Hetzner Cloud ----------
class Hetzner(Rest):
    id = "hetzner"
    label = "Hetzner Cloud"
    about = "Cloud servers that join Lens over the mesh and run Lens workers. Billed by the hour by Hetzner, in euros."
    base = "https://api.hetzner.cloud/v1"
    fields = {
        "location": _field("Location", required=True, default="fsn1", help="where servers start: fsn1, nbg1, hel1, ash..."),
        "token": _field("API token", required=True, secret=True, help="a project's API token with read and write"),
        "ssh_keys": _field("SSH keys", "list", help="the names or ids of the project's SSH keys to put on each server"),
        "usd_per_eur": _field(
            "Dollars per euro",
            "number",
            default=1.1,
            help="turns Hetzner's euro prices into the dollars budgets count; an estimate, so set your rate",
        ),
    }
    template = {
        "server_type": _field("Server type", required=True, default="cx22", help="cx22, cpx31, cax21..."),
        "image": _field("Image", required=True, default="ubuntu-24.04"),
        "steps": _steps(),
    }

    def _error(self, r):
        try:
            said = (r.json().get("error") or {}).get("message")
        except (ValueError, AttributeError):
            said = None
        return f"Hetzner said {r.status_code}" + (f": {said}" if said else "")

    def check(self, p):
        names = {x.get("name") for x in self.call(p, "GET", "/locations").get("locations") or []}
        loc = p["settings"].get("location")
        if loc and names and loc not in names:
            raise DriverError(f"Hetzner has no location {loc}; it has {', '.join(sorted(names))}")
        return {"locations": len(names)}

    def price(self, p, template):
        types = self.call(p, "GET", "/server_types", params={"name": template.get("server_type")}).get("server_types") or []
        loc = p["settings"].get("location")
        for t in types:
            for pr in t.get("prices") or []:
                if pr.get("location") == loc:
                    eur = (pr.get("price_hourly") or {}).get("gross")
                    return round(float(eur) * float(p["settings"].get("usd_per_eur") or 1.1), 6) if eur is not None else None
        return None

    def launch(self, p, template, name, boot):
        body = {
            "name": name,
            "server_type": template["server_type"],
            "image": template["image"],
            "location": p["settings"]["location"],
            "user_data": user_data(boot, template),
            "labels": {"lens": boot["tag"]},
            "start_after_create": True,
        }
        if p["settings"].get("ssh_keys"):
            body["ssh_keys"] = p["settings"]["ssh_keys"]
        s = self.call(p, "POST", "/servers", json=body).get("server") or {}
        if s.get("id") is None:
            raise DriverError("Hetzner made no server")
        return {"id": s["id"], "address": ((s.get("public_net") or {}).get("ipv4") or {}).get("ip")}

    def describe(self, p, node):
        s = (self.call(p, "GET", f"/servers/{node['external_id']}", ok404=True) or {}).get("server")
        if not s or s.get("status") in ("off", "stopping", "deleting"):
            return {"state": "gone"}
        ip = ((s.get("public_net") or {}).get("ipv4") or {}).get("ip")
        return {"state": "running" if s.get("status") == "running" else "pending", "address": ip}

    def terminate(self, p, node):
        if node.get("external_id"):
            self.call(p, "DELETE", f"/servers/{node['external_id']}", ok404=True)

    def listing(self, p, tag):
        got = self.call(p, "GET", "/servers", params={"label_selector": f"lens=={tag}", "per_page": 50}).get("servers") or []
        return [{"id": str(s["id"]), "name": s.get("name")} for s in got]


register(DigitalOcean())
register(Hetzner())
