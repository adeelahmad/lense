"""AWS EC2 for auto scaling (scaling.py, docs/scaling.md): Lens launches EC2 instances that join it over the mesh and
run `lens worker`, as spot instances (AWS's spare capacity, a fraction of the on-demand price, which AWS may take back
with two minutes' notice) or on demand.

A spot unit falls back to on demand when AWS has no spot capacity of that type, or none under the group's max price,
unless the group says not to. When AWS takes a spot instance back, `describe` says so and the scaler launches another;
the job its worker was running goes back in the queue (jobs.reap), so nothing is lost.

What each unit costs is an estimate: the spot price in the instance's zone when it launched, or the on-demand list
price from the AWS Price List API; an admin's price on the provider wins over both. Without any of them Lens doesn't
launch, because a unit with no price would never count against its group's budget.

Each instance is tagged `lens-unit` with its unit's name (`listing` finds the ones Lens lost by it), asks for
IMDSv2, encrypts its disk and terminates itself when it shuts down, so a machine that stops never keeps costing. Its
user-data is the mesh's install line, whose join token works once, within the hour: no long-lived secret is in it.

It talks to AWS through botocore (Apache-2.0), the `aws` extra, fetched when the first AWS provider is saved.
"""

from __future__ import annotations

import datetime as dt
import json
import shlex
import threading
import time

from . import jobs
from .scaling_drivers import Driver, DriverError, _field, register

TAG = "lens-unit"
UBUNTU_OWNER = "099720109477"  # Canonical
UBUNTU_IMAGE = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-{arch}-server-*"
ARCH = {"x86_64": "amd64", "arm64": "arm64"}
# What run_instances says when there's no spot to have: on demand may still be there
NO_SPOT = {
    "InsufficientInstanceCapacity",
    "InsufficientCapacity",
    "SpotMaxPriceTooLow",
    "MaxSpotInstanceCountExceeded",
    "Unsupported",
}
GONE_STATES = {"shutting-down", "terminated", "stopping", "stopped"}
WHY = {
    "Server.SpotInstanceTermination": "AWS took the spot instance back",
    "Server.SpotInstanceShutdown": "AWS took the spot instance back",
    "Server.InsufficientInstanceCapacity": "AWS had no capacity left for it",
    "Server.InternalError": "AWS stopped it with an internal error",
    "Client.InstanceInitiatedShutdown": "it shut itself down",
    "Client.UserInitiatedShutdown": "someone stopped it at AWS",
    "Client.VolumeLimitExceeded": "the account's disk limit was reached",
}
PRICE_SECONDS = 600  # prices and images are looked up again after this long
PRICING_REGION = "us-east-1"  # the Price List API answers for every region from here


def _now():
    return dt.datetime.now(dt.timezone.utc)


class Ec2(Driver):
    id = "aws"
    kind = "node"
    label = "AWS EC2"
    about = (
        "Launches EC2 instances that join Lens over the mesh and run its workers: spot instances for a fraction of the "
        "price, falling back to on demand when AWS has no spot capacity, or on demand only."
    )
    priced = True
    extra = "aws"
    modules = ("botocore",)
    license = "Apache-2.0"
    fields = {
        "region": _field("Region", required=True, default="us-east-1", help="the AWS region units launch in, such as eu-west-1"),
        "access_key_id": _field(
            "Access key ID", required=True, secret=True, help="an IAM user's key; docs/scaling.md has the policy it needs"
        ),
        "secret_access_key": _field("Secret access key", required=True, secret=True),
        "subnet_id": _field("Subnet", help="the subnet units launch in; none: the region's default VPC"),
        "security_group_ids": _field("Security groups", "list", help="none: the VPC's default group (units only need to reach out)"),
        "key_name": _field("SSH key pair", help="a key pair's name, to sign in to units yourself; none: no SSH key"),
    }
    template = {
        "instance_type": _field("Instance type", required=True, default="t4g.medium", help="such as t4g.medium (Arm) or m7i.large"),
        "market": _field(
            "Market", default="spot", options=["spot", "on-demand"], help="spot: AWS's spare capacity, which it may take back"
        ),
        "max_price": _field("Spot max price (USD per hour)", "number", help="none: up to the on-demand price"),
        "fallback": _field("On demand when there's no spot", "boolean", default=True),
        "image": _field("AMI", help="none: the latest Ubuntu 24.04 for the instance type's architecture"),
        "disk_gb": _field("Disk (GB)", "integer", default=30),
        "steps": _field(
            "Steps",
            "list",
            help="the steps these workers run (transcribe, analyze, embed...); none: every step",
            options=sorted(jobs.STEPS),
        ),
    }

    def __init__(self):
        self.lock = threading.Lock()
        self.clients: dict = {}
        self.cache: dict = {}

    # ---------- talking to AWS ----------
    def client(self, p, service="ec2", region=None):
        """A botocore client for the provider's account (one per account, region and service, reused)."""
        s = p.get("secrets") or {}
        region = region or (p.get("settings") or {}).get("region") or "us-east-1"
        key = (service, region, s.get("access_key_id"), hash(s.get("secret_access_key")))
        with self.lock:
            c = self.clients.get(key)
        if c is not None:
            return c
        import botocore.session
        from botocore.config import Config

        c = botocore.session.Session().create_client(
            service,
            region_name=region,
            aws_access_key_id=s.get("access_key_id"),
            aws_secret_access_key=s.get("secret_access_key"),
            config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3, "mode": "standard"}, user_agent_extra="lens"),
        )
        with self.lock:
            self.clients[key] = c
        return c

    def _call(self, client, op, **params):
        """One AWS call; what AWS said, as a DriverError, when it fails."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(client, op)(**params)
        except ClientError as e:
            err = e.response.get("Error") or {}
            raise AwsError(err.get("Code") or "Error", err.get("Message") or str(e), op) from None
        except BotoCoreError as e:
            raise DriverError(f"AWS {op}: {e}") from None

    def _cached(self, key, fn):
        now = time.monotonic()
        with self.lock:
            hit = self.cache.get(key)
        if hit and now - hit[0] < PRICE_SECONDS:
            return hit[1]
        value = fn()
        with self.lock:
            self.cache[key] = (now, value)
        return value

    def _region(self, p):
        return (p.get("settings") or {}).get("region") or "us-east-1"

    # ---------- what a unit is ----------
    def arch(self, p, itype):
        """The instance type's architecture (x86_64 or arm64); DriverError when AWS has no such type here."""

        def look():
            out = self._call(self.client(p), "describe_instance_types", InstanceTypes=[itype])
            types = out.get("InstanceTypes") or []
            if not types:
                raise DriverError(f"AWS has no instance type {itype} in {self._region(p)}")
            archs = (types[0].get("ProcessorInfo") or {}).get("SupportedArchitectures") or []
            return "arm64" if "arm64" in archs else "x86_64"

        try:
            return self._cached(("arch", self._region(p), itype), look)
        except AwsError as e:
            if e.code == "InvalidInstanceType":
                raise DriverError(f"AWS has no instance type {itype} in {self._region(p)}") from None
            raise

    def image(self, p, arch):
        """The newest Ubuntu 24.04 server image for this architecture in the provider's region: (id, root device)."""

        def look():
            out = self._call(
                self.client(p),
                "describe_images",
                Owners=[UBUNTU_OWNER],
                Filters=[
                    {"Name": "name", "Values": [UBUNTU_IMAGE.format(arch=ARCH[arch])]},
                    {"Name": "state", "Values": ["available"]},
                    {"Name": "architecture", "Values": [arch]},
                ],
            )
            images = sorted(out.get("Images") or [], key=lambda i: i.get("CreationDate") or "", reverse=True)
            if not images:
                raise DriverError(f"there's no Ubuntu 24.04 image for {arch} in {self._region(p)}: give the group an AMI")
            return images[0]["ImageId"], images[0].get("RootDeviceName") or "/dev/sda1"

        return self._cached(("image", self._region(p), arch), look)

    def root_device(self, p, image_id):
        out = self._call(self.client(p), "describe_images", ImageIds=[image_id])
        images = out.get("Images") or []
        if not images:
            raise DriverError(f"there's no AMI {image_id} in {self._region(p)}")
        return images[0].get("RootDeviceName") or "/dev/sda1"

    # ---------- prices ----------
    def spot_prices(self, p, itype, zone=None):
        """The spot price now in each zone of the region (or in one zone): {zone: USD per hour}."""

        def look():
            params = {"InstanceTypes": [itype], "ProductDescriptions": ["Linux/UNIX"], "StartTime": _now()}
            if zone:
                params["AvailabilityZone"] = zone
            out = self._call(self.client(p), "describe_spot_price_history", **params)
            prices: dict = {}
            for h in out.get("SpotPriceHistory") or []:
                z = h.get("AvailabilityZone")
                if z and z not in prices:  # the newest comes first
                    try:
                        prices[z] = float(h["SpotPrice"])
                    except (KeyError, TypeError, ValueError):
                        continue
            return prices

        return self._cached(("spot", self._region(p), itype, zone), look)

    def on_demand_price(self, p, itype):
        """The on-demand list price of a Linux instance of this type in the provider's region, from the AWS Price
        List API; None when it doesn't say."""

        def look():
            pricing = self.client(p, "pricing", PRICING_REGION)
            match = {
                "instanceType": itype,
                "regionCode": self._region(p),
                "operatingSystem": "Linux",
                "tenancy": "Shared",
                "preInstalledSw": "NA",
                "capacitystatus": "Used",
                "licenseModel": "No License required",
            }
            out = self._call(
                pricing,
                "get_products",
                ServiceCode="AmazonEC2",
                FormatVersion="aws_v1",
                Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in match.items()],
                MaxResults=10,
            )
            for item in out.get("PriceList") or []:
                try:
                    doc = json.loads(item) if isinstance(item, str) else item
                except ValueError:
                    continue
                for term in ((doc.get("terms") or {}).get("OnDemand") or {}).values():
                    for dim in (term.get("priceDimensions") or {}).values():
                        try:
                            usd = float((dim.get("pricePerUnit") or {}).get("USD"))
                        except (TypeError, ValueError):
                            continue
                        if usd > 0:
                            return usd
            return None

        try:
            return self._cached(("demand", self._region(p), itype), look)
        except DriverError:
            return None  # no pricing:GetProducts permission, say: the admin's price, or none

    def _spot_estimate(self, p, template, zone=None):
        try:
            prices = self.spot_prices(p, template["instance_type"], zone)
        except DriverError:
            return None
        if not prices:
            return None
        price = max(prices.values())  # before launch the zone isn't known: the dearest, so budgets err high
        cap = template.get("max_price")
        return min(price, float(cap)) if cap else price

    def price(self, p, template):
        """USD per hour of one unit, an estimate: the spot price (the dearest zone's, under the max price) or the
        on-demand list price."""
        if template.get("market", "spot") == "spot":
            spot = self._spot_estimate(p, template)
            if spot is not None:
                return spot
        return self.on_demand_price(p, template["instance_type"])

    # ---------- the calls the scaler makes ----------
    def check(self, p):
        """Prove the keys work in the region with a read-only call."""
        out = self._call(self.client(p), "describe_availability_zones")
        zones = sorted(z["ZoneName"] for z in out.get("AvailabilityZones") or [] if z.get("State") == "available")
        return {"region": self._region(p), "zones": zones}

    def user_data(self, name, template, boot):
        """The script an instance runs on its first boot: the mesh's install line, with the unit's name and the
        steps its worker runs."""
        env = {**(boot.get("env") or {}), "LENS_SCALING_UNIT": name}
        if template.get("steps"):
            env["LENS_WORKER_STEPS"] = ",".join(template["steps"])
        lines = ["#!/bin/sh", f"# Lens scaling unit {name}: joins Lens over the mesh and runs a worker", "set -eu"]
        lines += [f"export {k}={shlex.quote(str(v))}" for k, v in sorted(env.items())]
        lines.append(boot["line"])
        return "\n".join(lines) + "\n"

    def launch(self, p, template, name, boot):
        if not boot.get("line"):
            raise DriverError("an EC2 unit joins Lens over the mesh: turn the mesh on in Settings › Network first")
        st = p.get("settings") or {}
        itype = template["instance_type"]
        spot = template.get("market", "spot") == "spot"
        own_price = p.get("price")
        estimate = None if own_price is not None else self.price(p, template)
        if own_price is None and estimate is None:
            raise DriverError(
                f"AWS didn't say what {itype} costs in {self._region(p)}: allow pricing:GetProducts for these keys, "
                "or give the provider a price per hour"
            )
        arch = self.arch(p, itype)
        if template.get("image"):
            image_id, root = template["image"], self.root_device(p, template["image"])
        else:
            image_id, root = self.image(p, arch)
        tags = [{"Key": "Name", "Value": name}, {"Key": TAG, "Value": name}]
        params = {
            "ImageId": image_id,
            "InstanceType": itype,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": self.user_data(name, template, boot),
            "InstanceInitiatedShutdownBehavior": "terminate",
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "BlockDeviceMappings": [
                {
                    "DeviceName": root,
                    "Ebs": {
                        "VolumeSize": int(template.get("disk_gb") or 30),
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}, {"ResourceType": "volume", "Tags": tags}],
        }
        if st.get("subnet_id"):
            params["SubnetId"] = st["subnet_id"]
        if st.get("security_group_ids"):
            params["SecurityGroupIds"] = list(st["security_group_ids"])
        if st.get("key_name"):
            params["KeyName"] = st["key_name"]
        market = "on-demand"
        if spot:
            options: dict = {"SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"}
            if template.get("max_price"):
                options["MaxPrice"] = f"{float(template['max_price']):.4f}"
            try:
                out = self._call(
                    self.client(p), "run_instances", **params, InstanceMarketOptions={"MarketType": "spot", "SpotOptions": options}
                )
                market = "spot"
            except AwsError as e:
                if e.code not in NO_SPOT or template.get("fallback") is False:
                    raise
                out = None
                fell_back = e.message
            if out is None:
                if own_price is None:
                    estimate = self.on_demand_price(p, itype)
                    if estimate is None:
                        raise DriverError(f"there was no spot {itype} ({fell_back}), and AWS didn't say what on demand costs")
                out = self._call(self.client(p), "run_instances", **params)
        else:
            out = self._call(self.client(p), "run_instances", **params)
        inst = (out.get("Instances") or [{}])[0]
        if not inst.get("InstanceId"):
            raise DriverError("AWS launched nothing")
        price = own_price
        if price is None:
            price = estimate
            zone = (inst.get("Placement") or {}).get("AvailabilityZone")
            if market == "spot" and zone:
                exact = self._spot_estimate(p, template, zone)
                price = exact if exact is not None else price
        got = {"id": inst["InstanceId"], "address": inst.get("PrivateIpAddress"), "market": market}
        if price is not None:
            got["price"] = round(float(price), 6)
        return got

    def describe(self, p, node):
        iid = node.get("external_id")
        if not iid:
            return {"state": "gone"}
        try:
            out = self._call(self.client(p), "describe_instances", InstanceIds=[iid])
        except AwsError as e:
            if e.code == "InvalidInstanceID.NotFound":
                return {"state": "gone", "why": "AWS has no such instance any more"}
            raise
        insts = [i for r in out.get("Reservations") or [] for i in r.get("Instances") or []]
        if not insts:
            return {"state": "gone", "why": "AWS has no such instance any more"}
        inst = insts[0]
        state = (inst.get("State") or {}).get("Name")
        if state in GONE_STATES:
            code = (inst.get("StateReason") or {}).get("Code")
            if state in ("stopping", "stopped"):
                self.terminate(p, node)  # it takes no jobs, and its disk would keep costing; the scaler won't call again
            return {"state": "gone", "why": WHY.get(code) or f"it's {state} at AWS", "reason": code}
        out_ = {"state": "running" if state == "running" else "pending"}
        if inst.get("PrivateIpAddress"):
            out_["address"] = inst["PrivateIpAddress"]
        if inst.get("InstanceLifecycle") == "spot":
            out_["market"] = "spot"
        return out_

    def terminate(self, p, node):
        iid = node.get("external_id")
        if not iid:
            return
        try:
            self._call(self.client(p), "terminate_instances", InstanceIds=[iid])
        except AwsError as e:
            if e.code != "InvalidInstanceID.NotFound":
                raise

    def listing(self, p, tag):
        client = self.client(p)
        found, token = [], None
        while True:
            params = {
                "Filters": [
                    {"Name": f"tag:{TAG}", "Values": [f"{tag}-*"]},
                    {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
                ],
                "MaxResults": 100,
            }
            if token:
                params["NextToken"] = token
            out = self._call(client, "describe_instances", **params)
            for r in out.get("Reservations") or []:
                for i in r.get("Instances") or []:
                    name = next((t.get("Value") for t in i.get("Tags") or [] if t.get("Key") == TAG), None)
                    found.append({"id": i["InstanceId"], "name": name})
            token = out.get("NextToken")
            if not token:
                return found


class AwsError(DriverError):
    """What AWS said when a call failed: its error code and message."""

    def __init__(self, code, message, op):
        self.code, self.message, self.op = code, message, op
        super().__init__(f"AWS {op}: {message} ({code})")


register(Ec2())
