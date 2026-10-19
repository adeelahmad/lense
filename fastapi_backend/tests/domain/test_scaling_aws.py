"""AWS EC2 for auto scaling (scaling_aws.py, docs/scaling.md): spot or on-demand instances that join over the mesh,
with prices as estimates, a fall back to on demand when there's no spot, and AWS taking a spot instance back. AWS is
botocore's Stubber: every call is checked against EC2's own API model, and answered from here."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest
from botocore.session import Session
from botocore.stub import Stubber

from app.domain import scaling, settings, store
from app.domain import scaling_drivers as drivers
from app.domain.scaling_aws import TAG, Ec2
from tests.helpers import login, make_user

R = store.R
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
LINE = "curl -fsSL https://lens.example/install.sh | LENS_JOIN=https://lens.example LENS_JOIN_TOKEN=lensjoin1.7.x sh"
P = {
    "id": 1,
    "name": "AWS",
    "settings": {"region": "eu-west-1"},
    "secrets": {"access_key_id": "AKIA", "secret_access_key": "s"},
    "price": None,
}
SPOT = {"instance_type": "t4g.medium", "market": "spot", "fallback": True, "disk_gb": 30}


def _client(service, region):
    return Session().create_client(service, region_name=region, aws_access_key_id="AKIA", aws_secret_access_key="s")


class Aws:
    """An Ec2 driver whose clients are stubbed; `calls` is every call it made, with its parameters."""

    def __init__(self, monkeypatch):
        self.drv = Ec2()
        self.ec2, self.pricing = _client("ec2", "eu-west-1"), _client("pricing", "us-east-1")
        self.stubs = {"ec2": Stubber(self.ec2), "pricing": Stubber(self.pricing)}
        self.calls = []
        clients = {"ec2": self.ec2, "pricing": self.pricing}
        monkeypatch.setattr(self.drv, "client", lambda p, service="ec2", region=None: clients[service])
        real = self.drv._call

        def call(client, op, **params):
            self.calls.append((op, params))
            return real(client, op, **params)

        monkeypatch.setattr(self.drv, "_call", call)
        for s in self.stubs.values():
            s.activate()

    def add(self, op, response=None, service="ec2"):
        self.stubs[service].add_response(op, response or {})

    def error(self, op, code, message="no", service="ec2"):
        self.stubs[service].add_client_error(op, service_error_code=code, service_message=message, http_status_code=400)

    def ops(self):
        return [op for op, _ in self.calls]

    def params(self, op):
        return [p for o, p in self.calls if o == op]

    def done(self):
        for s in self.stubs.values():
            s.assert_no_pending_responses()

    # the answers a launch reads
    def spot_prices(self, **zones):
        self.add(
            "describe_spot_price_history",
            {"SpotPriceHistory": [{"AvailabilityZone": z.replace("_", "-"), "SpotPrice": str(v)} for z, v in zones.items()]},
        )

    def arch(self, arch="arm64"):
        self.add(
            "describe_instance_types",
            {"InstanceTypes": [{"InstanceType": "t4g.medium", "ProcessorInfo": {"SupportedArchitectures": [arch]}}]},
        )

    def ubuntu(self):
        self.add(
            "describe_images",
            {
                "Images": [
                    {"ImageId": "ami-old", "CreationDate": "2026-01-01T00:00:00.000Z", "RootDeviceName": "/dev/sda1"},
                    {"ImageId": "ami-new", "CreationDate": "2026-09-01T00:00:00.000Z", "RootDeviceName": "/dev/sda1"},
                ]
            },
        )

    def launched(self, iid="i-1", zone="eu-west-1b", spot=True):
        inst = {"InstanceId": iid, "PrivateIpAddress": "172.31.0.5", "Placement": {"AvailabilityZone": zone}}
        if spot:
            inst["InstanceLifecycle"] = "spot"
        self.add("run_instances", {"Instances": [inst]})

    def on_demand(self, usd="0.0368"):
        doc = {"terms": {"OnDemand": {"x": {"priceDimensions": {"y": {"pricePerUnit": {"USD": usd}}}}}}}
        self.add("get_products", {"PriceList": [json.dumps(doc)]}, service="pricing")


@pytest.fixture
def aws(monkeypatch):
    return Aws(monkeypatch)


BOOT = {"env": {"LENS_SCALING_UNIT": "lens-workers-1"}, "line": LINE, "token_id": 7, "tag": "lens"}


def test_a_spot_unit_launches_hardened_and_priced_in_its_zone(aws):
    aws.spot_prices(eu_west_1a=0.012, eu_west_1b=0.014)  # before launch: the dearest zone
    aws.arch("arm64")
    aws.ubuntu()
    aws.launched(zone="eu-west-1a")
    aws.spot_prices(eu_west_1a=0.012)  # after: the zone it landed in
    got = aws.drv.launch(P, {**SPOT, "steps": ["transcribe"]}, "lens-workers-1", BOOT)
    aws.done()
    assert got == {"id": "i-1", "address": "172.31.0.5", "market": "spot", "price": 0.012}

    (images,) = aws.params("describe_images")
    assert images["Owners"] == ["099720109477"] and "ubuntu-noble-24.04-arm64-server-*" in images["Filters"][0]["Values"][0]
    (run,) = aws.params("run_instances")
    assert run["ImageId"] == "ami-new" and run["InstanceType"] == "t4g.medium"
    assert run["InstanceMarketOptions"] == {
        "MarketType": "spot",
        "SpotOptions": {"SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"},
    }
    assert run["MetadataOptions"]["HttpTokens"] == "required"  # IMDSv2 only
    assert run["InstanceInitiatedShutdownBehavior"] == "terminate"  # one that stops never keeps costing
    assert run["BlockDeviceMappings"][0]["Ebs"] == {"VolumeSize": 30, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}
    tags = {t["Key"]: t["Value"] for t in run["TagSpecifications"][0]["Tags"]}
    assert tags == {"Name": "lens-workers-1", TAG: "lens-workers-1", "lens": "lens"}
    # the user-data is the mesh's install line, named for the unit: its one-time token is the only credential in it
    data = run["UserData"]
    data = data if data.startswith("#!") else base64.b64decode(data).decode()
    assert data.startswith("#!/bin/sh\n") and data.rstrip().endswith(LINE)
    assert "export LENS_SCALING_UNIT=lens-workers-1" in data and "export LENS_WORKER_STEPS=transcribe" in data


def test_without_spot_capacity_it_falls_back_to_on_demand_unless_told_not_to(aws):
    aws.spot_prices(eu_west_1a=0.012)
    aws.arch()
    aws.ubuntu()
    aws.error("run_instances", "InsufficientInstanceCapacity", "no spot t4g.medium in eu-west-1")
    aws.on_demand("0.0368")
    aws.launched(spot=False)
    got = aws.drv.launch(P, SPOT, "lens-workers-1", BOOT)
    aws.done()
    assert got["market"] == "on-demand" and got["price"] == 0.0368
    first, second = aws.params("run_instances")
    assert "InstanceMarketOptions" in first and "InstanceMarketOptions" not in second

    aws.error("run_instances", "SpotMaxPriceTooLow", "your max price is under the spot price")
    with pytest.raises(drivers.DriverError, match="SpotMaxPriceTooLow"):
        aws.drv.launch(P, {**SPOT, "fallback": False, "max_price": 0.005}, "lens-workers-2", BOOT)  # arch and image are cached
    aws.done()
    assert aws.params("run_instances")[-1]["InstanceMarketOptions"]["SpotOptions"]["MaxPrice"] == "0.0050"


def test_a_unit_without_a_price_or_the_mesh_launches_nothing(aws):
    with pytest.raises(drivers.DriverError, match="mesh"):
        aws.drv.launch(P, SPOT, "lens-workers-1", {**BOOT, "line": None})
    assert aws.calls == []  # no mesh: not even a call to AWS

    aws.error("get_products", "AccessDeniedException", "not allowed", service="pricing")
    with pytest.raises(drivers.DriverError, match="pricing:GetProducts"):
        aws.drv.launch(P, {**SPOT, "market": "on-demand"}, "lens-workers-1", BOOT)
    aws.done()
    assert "run_instances" not in aws.ops()  # a unit with no price would never count against the budget

    # the admin's own price on the provider wins, and no price is looked up
    aws.arch("x86_64")
    aws.ubuntu()
    aws.launched(spot=False)
    got = aws.drv.launch({**P, "price": 0.1}, {**SPOT, "market": "on-demand", "instance_type": "m7i.large"}, "lens-workers-1", BOOT)
    aws.done()
    assert got["price"] == 0.1 and "get_products" not in aws.ops()[-3:]
    assert "ubuntu-noble-24.04-amd64-server-*" in aws.params("describe_images")[-1]["Filters"][0]["Values"][0]


def test_describe_says_when_aws_took_a_spot_instance_back(aws):
    node = {"external_id": "i-1", "name": "lens-workers-1"}
    reservation = lambda state, code=None: {  # noqa: E731
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-1",
                        "State": {"Name": state},
                        "PrivateIpAddress": "172.31.0.5",
                        "InstanceLifecycle": "spot",
                        **({"StateReason": {"Code": code, "Message": code}} if code else {}),
                    }
                ]
            }
        ]
    }
    aws.add("describe_instances", reservation("pending"))
    aws.add("describe_instances", reservation("running"))
    aws.add("describe_instances", reservation("terminated", "Server.SpotInstanceTermination"))
    aws.add("describe_instances", reservation("stopped", "Client.UserInitiatedShutdown"))
    aws.add("terminate_instances", {})  # a stopped one takes no jobs and its disk keeps costing: terminated
    aws.error("describe_instances", "InvalidInstanceID.NotFound")
    assert aws.drv.describe(P, node)["state"] == "pending"
    assert aws.drv.describe(P, node) == {"state": "running", "address": "172.31.0.5", "market": "spot"}
    assert aws.drv.describe(P, node) == {
        "state": "gone",
        "why": "AWS took the spot instance back",
        "reason": "Server.SpotInstanceTermination",
    }
    assert aws.drv.describe(P, node)["why"] == "someone stopped it at AWS"
    assert aws.drv.describe(P, node)["state"] == "gone"
    aws.done()
    assert aws.params("terminate_instances") == [{"InstanceIds": ["i-1"]}]


def test_terminate_check_and_listing(aws):
    aws.add("terminate_instances", {"TerminatingInstances": [{"InstanceId": "i-1"}]})
    aws.error("terminate_instances", "InvalidInstanceID.NotFound")  # already gone: no error
    aws.drv.terminate(P, {"external_id": "i-1"})
    aws.drv.terminate(P, {"external_id": "i-1"})
    aws.drv.terminate(P, {"external_id": None})  # never launched: nothing to call

    aws.add(
        "describe_availability_zones",
        {"AvailabilityZones": [{"ZoneName": "eu-west-1b", "State": "available"}, {"ZoneName": "eu-west-1a", "State": "available"}]},
    )
    assert aws.drv.check(P) == {"region": "eu-west-1", "zones": ["eu-west-1a", "eu-west-1b"]}

    tagged = lambda iid, name: {"InstanceId": iid, "Tags": [{"Key": TAG, "Value": name}]}  # noqa: E731
    aws.add("describe_instances", {"Reservations": [{"Instances": [tagged("i-1", "lens-a-1")]}], "NextToken": "n"})
    aws.add("describe_instances", {"Reservations": [{"Instances": [tagged("i-2", "lens-a-2")]}]})
    assert aws.drv.listing(P, "lens") == [{"id": "i-1", "name": "lens-a-1"}, {"id": "i-2", "name": "lens-a-2"}]
    assert aws.params("describe_instances")[0]["Filters"][0] == {"Name": f"tag:{TAG}", "Values": ["lens-*"]}
    assert aws.params("describe_instances")[1]["NextToken"] == "n"
    aws.done()


def test_the_driver_is_offered_and_its_settings_are_checked():
    drv = drivers.get("aws")
    spec = drv.spec()
    assert (spec["kind"], spec["priced"], spec["needs_join"], spec["extra"], spec["license"]) == ("node", True, True, "aws", "Apache-2.0")
    assert set(drv.secrets) == {"access_key_id", "secret_access_key"}
    assert drivers.check_template(drv, {}) == {"instance_type": "t4g.medium", "market": "spot", "fallback": True, "disk_gb": 30}
    with pytest.raises(ValueError, match="market"):
        drivers.check_template(drv, {"market": "reserved"})


# ---------- through the scaler ----------
@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_the_scaler_replaces_a_spot_unit_aws_took_back(client, db, cfg, h, aws, monkeypatch):
    monkeypatch.setitem(drivers.DRIVERS, "aws", aws.drv)
    monkeypatch.setattr(scaling, "can_join", lambda cfg: True)  # the mesh is on and has its install line
    monkeypatch.setattr(
        scaling,
        "bootstrap",
        lambda db, cfg, g, name, **_: {"env": {"LENS_SCALING_UNIT": name}, "line": LINE, "token_id": None, "tag": "lens"},
    )
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    aws.add("describe_availability_zones", {"AvailabilityZones": []})  # the check when it's saved
    body = {
        "name": "AWS",
        "driver": "aws",
        "settings": {"region": "eu-west-1"},
        "secrets": {"access_key_id": "AKIA", "secret_access_key": "s"},
    }
    r = client.post("/api/v1/scaling/providers", headers=h, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["secrets"] == {"access_key_id": True, "secret_access_key": True}
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={
            "name": "Workers",
            "provider": r.json()["id"],
            "min": 1,
            "max": 2,
            "budget": 20,
            "template": SPOT,
            "cooldown_seconds": 0,
            "warmup_seconds": 0,
        },
    )
    assert g.status_code == 201, g.text
    gid = g.json()["id"]

    aws.spot_prices(eu_west_1a=0.012)  # the scaler's estimate before launching (then cached)
    aws.arch()
    aws.ubuntu()
    aws.launched("i-1", "eu-west-1a")
    aws.spot_prices(eu_west_1a=0.012)  # the zone it landed in
    tick = lambda now: scaling.Scaler(db, lambda: cfg, "test").tick(now=now, lease=False)  # noqa: E731
    assert tick(NOW)["launched"] == 1
    (unit,) = scaling.nodes(db, gid)
    assert (unit["external_id"], unit["price"], unit["state"]) == ("i-1", 0.012, "launching")

    # its worker joins, then AWS takes the spot instance back: the scaler says why and launches another
    db.q("UPSERT $w MERGE {name: $n, heartbeat_at: $t}", w=R("worker", unit["name"]), n=unit["name"], t=NOW.isoformat())
    aws.add("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": "running"}}]}]})
    tick(NOW + dt.timedelta(seconds=30))
    assert scaling.nodes(db, gid)[0]["state"] == "running"
    gone = {"InstanceId": "i-1", "State": {"Name": "terminated"}, "StateReason": {"Code": "Server.SpotInstanceTermination", "Message": "x"}}
    aws.add("describe_instances", {"Reservations": [{"Instances": [gone]}]})
    aws.launched("i-2", "eu-west-1a")  # prices, the image and the architecture are cached
    did = tick(NOW + dt.timedelta(minutes=5))
    aws.done()
    assert (did["terminated"], did["launched"]) == (1, 1)
    old, new = sorted(scaling.nodes(db, gid), key=lambda n: n["id"])
    assert old["state"] == "terminated" and old["why"] == "AWS took the spot instance back"
    assert new["external_id"] == "i-2" and new["state"] == "launching"
