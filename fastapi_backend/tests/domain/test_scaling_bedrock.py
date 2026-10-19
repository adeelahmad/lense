"""Amazon Bedrock provisioned throughput for auto scaling. moto has no provisioned throughput, so botocore's Stubber
answers instead: it holds each request and answer to AWS's own model of Bedrock's API, and the requests are also held
to the patterns that model gives names and ids (a name Bedrock would refuse fails the test)."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from app.domain import llm, scaling, settings
from app.domain import scaling_drivers as drivers
from app.domain.scaling_bedrock import Bedrock
from tests.helpers import login, make_user

session = pytest.importorskip("botocore.session")
from botocore.stub import ANY, Stubber  # noqa: E402 - after the skip

NOW = dt.datetime(2026, 10, 19, 12, 0, tzinfo=dt.UTC)
SECRETS = {"access_key_id": "AKIABEDROCK", "secret_access_key": "bedrock/secret+key"}
MODEL = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"
API = session.Session().get_service_model("bedrock")


def arn(name):
    return f"arn:aws:bedrock:us-east-1:123456789012:provisioned-model/{name.replace('-', '')[:12].ljust(12, '0')}"


def follows_model(op, params):
    """Every string Lens sends matches the pattern AWS's model of the operation gives it."""
    shape = API.operation_model(op).input_shape
    for k, v in params.items():
        member = shape.members[k]
        items = v if isinstance(v, list) else [v]
        for item in items:
            fields = item.items() if isinstance(item, dict) else [(None, item)]
            for name, value in fields:
                s = member.member.members[name] if name else member
                pattern = s.metadata.get("pattern")
                if pattern and isinstance(value, str):
                    assert re.fullmatch(pattern, value), f"{op} {k}.{name or ''}: {value!r} isn't {pattern}"


def summary(name, status="InService"):
    return {
        "provisionedModelName": name,
        "provisionedModelArn": arn(name),
        "modelArn": MODEL,
        "desiredModelArn": MODEL,
        "foundationModelArn": MODEL,
        "modelUnits": 1,
        "desiredModelUnits": 1,
        "status": status,
        "creationTime": NOW,
        "lastModifiedTime": NOW,
    }


def got(name, status="InService", failure=None):
    return {**summary(name, status), **({"failureMessage": failure} if failure else {})}


def _p(price=2.5):
    return {"id": 1, "name": "Bedrock", "settings": {"region": "us-east-1"}, "secrets": dict(SECRETS), "price": price}


@pytest.fixture
def bedrock(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(k, raising=False)
    drv = Bedrock()
    monkeypatch.setitem(drivers.DRIVERS, "bedrock", drv)
    stub = Stubber(drv.bedrock(_p()))
    stub.activate()
    yield drv, stub
    stub.assert_no_pending_responses()


def expect(stub, op, answer, params):
    follows_model(API.operation_model("".join(w.title() for w in op.split("_"))).name, params)
    stub.add_response(op, answer, params)


def test_a_unit_is_a_provisioned_throughput_held_until_it_goes(bedrock):
    drv, stub = bedrock
    p = _p()
    expect(stub, "list_provisioned_model_throughputs", {"provisionedModelSummaries": []}, {"maxResults": 100})
    assert drv.check(p) == {"region": "us-east-1", "provisioned": 0}

    name = "lens-models-7"
    made = {
        "clientRequestToken": name,
        "modelUnits": 2,
        "provisionedModelName": name,
        "modelId": MODEL,
        "tags": [{"key": "lens-unit", "value": name}, {"key": "lens", "value": "lens"}],
    }
    expect(stub, "create_provisioned_model_throughput", {"provisionedModelArn": arn(name)}, made)
    out = drv.launch(p, {"model": MODEL, "model_units": 2}, name, {"tag": "lens"})
    assert out == {
        "id": arn(name),
        "endpoint": {
            "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
            "model": arn(name),
            "api_key": "AKIABEDROCK:bedrock/secret+key",
        },
        "price": 2.5,
    }

    expect(stub, "get_provisioned_model_throughput", got(name, "Creating"), {"provisionedModelId": arn(name)})
    assert drv.describe(p, {"external_id": arn(name)}) == {"state": "pending"}
    expect(stub, "get_provisioned_model_throughput", got(name), {"provisionedModelId": arn(name)})
    assert drv.describe(p, {"external_id": arn(name)}) == {"state": "running"}
    page = {"provisionedModelSummaries": [summary(name), summary("lensx-other-1")], "nextToken": "next1"}
    expect(stub, "list_provisioned_model_throughputs", page, {"nameContains": "lens-", "maxResults": 100})
    expect(
        stub,
        "list_provisioned_model_throughputs",
        {"provisionedModelSummaries": [summary("lens-models-8")]},
        {"nameContains": "lens-", "maxResults": 100, "nextToken": "next1"},
    )
    assert drv.listing(p, "lens") == [{"id": arn(name), "name": name}, {"id": arn("lens-models-8"), "name": "lens-models-8"}]

    expect(stub, "delete_provisioned_model_throughput", {}, {"provisionedModelId": arn(name)})
    drv.terminate(p, {"external_id": arn(name)})
    stub.add_client_error("delete_provisioned_model_throughput", "ResourceNotFoundException", "Provisioned model not found", 404)
    drv.terminate(p, {"external_id": arn(name)})  # already gone: no error
    stub.add_client_error("get_provisioned_model_throughput", "ResourceNotFoundException", "Provisioned model not found", 404)
    assert drv.describe(p, {"external_id": arn(name)}) == {"state": "gone"}


def test_one_that_fails_says_bedrocks_reason_and_none_launch_without_a_price(bedrock):
    drv, stub = bedrock
    expect(
        stub,
        "get_provisioned_model_throughput",
        got("lens-m-2", "Failed", "Insufficient capacity for amazon.nova-lite-v1:0"),
        {"provisionedModelId": arn("lens-m-2")},
    )
    assert drv.describe(_p(), {"external_id": arn("lens-m-2")}) == {
        "state": "gone",
        "why": "Bedrock: Insufficient capacity for amazon.nova-lite-v1:0",
    }
    with pytest.raises(drivers.DriverError, match="price per unit-hour"):
        drv.launch(_p(price=None), {"model": MODEL}, "lens-m-3", {"tag": "lens"})
    stub.add_client_error(
        "create_provisioned_model_throughput", "ValidationException", "The model doesn't support provisioned throughput.", 400
    )
    with pytest.raises(drivers.DriverError, match="doesn't support provisioned throughput"):
        drv.launch(_p(), {"model": "amazon.titan-text-lite-v1"}, "lens-m-3", {"tag": "lens"})


def test_a_long_name_keeps_its_number_and_bedrocks_pattern(bedrock):
    drv, stub = bedrock
    name = "lens-" + "a-very-long-group-name-" * 3 + "12"
    short = name[: 63 - 3].rstrip("-") + "-12"
    stub.add_response(
        "create_provisioned_model_throughput",
        {"provisionedModelArn": arn(short)},
        {**dict.fromkeys(["clientRequestToken", "modelUnits", "modelId", "tags"], ANY), "provisionedModelName": short},
    )
    drv.launch(_p(), {"model": MODEL}, name, {"tag": "lens"})
    assert len(short) <= 63
    follows_model("CreateProvisionedModelThroughput", {"provisionedModelName": short, "clientRequestToken": short})


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_a_bedrock_group_serves_lenss_model_calls_until_it_scales_in(client, db, cfg, h, bedrock):
    drv, stub = bedrock
    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    scaler = scaling.Scaler(db, lambda: settings.effective(db, base), "test")
    stub.add_response("list_provisioned_model_throughputs", {"provisionedModelSummaries": []}, {"maxResults": 100})
    body = {"name": "Bedrock", "driver": "bedrock", "settings": {"region": "us-east-1"}, "secrets": SECRETS, "price": 23.5}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "region": "us-east-1", "provisioned": 0}
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Models", "provider": p["id"], "min": 1, "max": 1, "budget": 100, "template": {"model": MODEL}},
    ).json()
    assert g["kind"] == "model" and g["template"]["model_units"] == 1, g

    stub.add_response(
        "create_provisioned_model_throughput",
        {"provisionedModelArn": arn("lens-models-1")},
        {"clientRequestToken": ANY, "modelUnits": 1, "provisionedModelName": ANY, "modelId": MODEL, "tags": ANY},
    )
    scaler.tick(now=NOW, lease=False)
    (n,) = scaling.nodes(db, g["id"])
    assert n["price"] == pytest.approx(23.5) and n["endpoint"]["api_key"] is True  # sealed, shown only as set

    stub.add_response("get_provisioned_model_throughput", got(n["name"]), {"provisionedModelId": n["external_id"]})
    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    assert scaling.nodes(db, g["id"])[0]["state"] == "running"
    served = settings.effective(db, base)
    assert served["llm"]["base_url"] == "https://bedrock-runtime.us-east-1.amazonaws.com"
    assert served["llm"]["model"] == n["external_id"] and served["llm"]["api_key"] == "AKIABEDROCK:bedrock/secret+key"

    llm._CLIENTS.clear()
    runtime = Stubber(llm.bedrock_client(served, "us-east-1"))
    runtime.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "From Bedrock."}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 3, "outputTokens": 3, "totalTokens": 6},
            "metrics": {"latencyMs": 100},
        },
        {"modelId": n["external_id"], "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"temperature": 0.2}},
    )
    with runtime:
        assert llm.chat(served, [{"role": "user", "content": "hi"}]) == "From Bedrock."
    llm._CLIENTS.clear()

    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    stub.add_response("delete_provisioned_model_throughput", {}, {"provisionedModelId": n["external_id"]})
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    assert settings.effective(db, base)["llm"]["base_url"] == "http://old.home:8080/v1"
