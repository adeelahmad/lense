"""AWS EC2 for auto scaling against moto, a stand-in for AWS that keeps state the way EC2 does: instances launch,
carry their tags and spot market, stop and terminate, so the driver and the scaler run as they would against a real
account (test_scaling_aws.py holds each call to EC2's API model; this follows instances through their life)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import scaling, settings
from app.domain import scaling_drivers as drivers
from app.domain.scaling_aws import TAG, Ec2
from tests.helpers import login, make_user

moto = pytest.importorskip("moto")

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
LINE = "curl -fsSL https://lens.example/install.sh | LENS_JOIN=https://lens.example LENS_JOIN_TOKEN=lensjoin1.7.x sh"
SECRETS = {"access_key_id": "AKIAMOTO", "secret_access_key": "moto"}


@pytest.fixture
def aws(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(k, raising=False)
    with moto.mock_aws():
        drv = Ec2()
        monkeypatch.setitem(drivers.DRIVERS, "aws", drv)
        yield drv


def _p(drv=None):
    return {"id": 1, "name": "AWS", "settings": {"region": "eu-west-1"}, "secrets": SECRETS, "price": None}


def _ami(drv):
    """An image moto has, as the group's AMI (moto has no Ubuntu 24.04 to find by name)."""
    return drv.client(_p()).describe_images(Owners=["amazon"])["Images"][0]["ImageId"]


def test_a_spot_instance_launches_tagged_hardened_and_is_found_by_its_tag(aws):
    p = _p()
    assert aws.check(p) == {"region": "eu-west-1", "zones": ["eu-west-1a", "eu-west-1b", "eu-west-1c"]}
    t = {"instance_type": "t4g.medium", "market": "spot", "fallback": True, "image": _ami(aws), "disk_gb": 40}
    got = aws.launch(p, t, "lens-workers-1", {"line": LINE, "env": {}, "tag": "lens"})
    assert got["market"] == "spot" and got["price"] > 0 and got["address"]

    ec2 = aws.client(p)
    (inst,) = ec2.describe_instances(InstanceIds=[got["id"]])["Reservations"][0]["Instances"]
    assert inst["InstanceType"] == "t4g.medium" and inst["InstanceLifecycle"] == "spot"
    tags = {x["Key"]: x["Value"] for x in inst["Tags"]}
    assert tags == {"Name": "lens-workers-1", TAG: "lens-workers-1", "lens": "lens"}
    assert inst["MetadataOptions"]["HttpTokens"] == "required"  # IMDSv2 only
    (vol,) = ec2.describe_volumes(Filters=[{"Name": "attachment.instance-id", "Values": [got["id"]]}])["Volumes"]
    assert vol["Encrypted"] and vol["Size"] == 40 and vol["VolumeType"] == "gp3"

    assert aws.describe(p, {"external_id": got["id"]}) == {"state": "running", "address": got["address"], "market": "spot"}
    assert aws.listing(p, "lens") == [{"id": got["id"], "name": "lens-workers-1"}]
    assert aws.listing(p, "other") == []

    aws.terminate(p, {"external_id": got["id"]})
    assert aws.describe(p, {"external_id": got["id"]})["state"] == "gone" and aws.listing(p, "lens") == []
    aws.terminate(p, {"external_id": got["id"]})  # already gone: no error
    assert aws.describe(p, {"external_id": "i-0123456789abcdef0"})["state"] == "gone"


def test_an_instance_someone_stopped_is_terminated_so_its_disk_stops_costing(aws):
    p = _p()
    t = {"instance_type": "t3.small", "market": "on-demand", "image": _ami(aws)}
    p["price"] = 0.02  # the provider's own price: no Price List API needed
    got = aws.launch(p, t, "lens-workers-2", {"line": LINE, "env": {}, "tag": "lens"})
    assert got["market"] == "on-demand" and got["price"] == 0.02
    ec2 = aws.client(p)
    ec2.stop_instances(InstanceIds=[got["id"]])
    assert aws.describe(p, {"external_id": got["id"]}) == {
        "state": "gone",
        "why": "someone stopped it at AWS",
        "reason": "Client.UserInitiatedShutdown",
    }
    state = ec2.describe_instances(InstanceIds=[got["id"]])["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert state == "terminated"


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_the_scaler_runs_a_group_on_moto_from_launch_to_scale_in(client, db, cfg, h, aws, monkeypatch):
    monkeypatch.setattr(scaling, "can_join", lambda cfg: True)  # the mesh is on and has its install line
    monkeypatch.setattr(
        scaling,
        "bootstrap",
        lambda db, cfg, g, name, **_: {"env": {"LENS_SCALING_UNIT": name}, "line": LINE, "token_id": None, "tag": "lens"},
    )
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    body = {"name": "AWS", "driver": "aws", "settings": {"region": "eu-west-1"}, "secrets": SECRETS}
    r = client.post("/api/v1/scaling/providers", headers=h, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["check"]["zones"] == ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
    template = {"instance_type": "t4g.medium", "market": "spot", "image": _ami(aws), "steps": ["transcribe"]}
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Workers", "provider": r.json()["id"], "min": 2, "max": 2, "budget": 20, "template": template, "cooldown_seconds": 0},
    )
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    tick = lambda now: scaling.Scaler(db, lambda: cfg, "test").tick(now=now, lease=False)  # noqa: E731
    assert tick(NOW)["launched"] == 2
    units = scaling.nodes(db, gid)
    ec2 = aws.client(_p())
    running = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running"]}])["Reservations"]
    assert sorted(i["InstanceId"] for r_ in running for i in r_["Instances"]) == sorted(u["external_id"] for u in units)

    for u in units:  # their workers join
        db.q("UPSERT $w MERGE {name: $n, heartbeat_at: $t}", w=scaling.R("worker", u["name"]), n=u["name"], t=NOW.isoformat())
    tick(NOW + dt.timedelta(seconds=30))
    assert {u["state"] for u in scaling.nodes(db, gid)} == {"running"}

    client.patch(f"/api/v1/scaling/groups/{gid}", headers=h, json={"min": 0, "desired": 0})
    tick(NOW + dt.timedelta(minutes=1))
    tick(NOW + dt.timedelta(minutes=2))  # drained, then terminated
    left = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}])["Reservations"]
    assert left == [] and {u["state"] for u in scaling.nodes(db, gid)} == {"terminated"}
