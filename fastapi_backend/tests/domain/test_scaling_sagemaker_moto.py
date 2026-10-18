"""Amazon SageMaker for auto scaling against moto, a stand-in for AWS that keeps SageMaker's models, endpoint
configurations and endpoints, and answers as SageMaker does (a ValidationException "Could not find ..." for one it
doesn't have). The Price List call is held to AWS's own model of the API by botocore's Stubber, answering with a
product as the Price List publishes it."""

from __future__ import annotations

import datetime as dt
import io
import json

import pytest

from app.domain import llm, scaling, settings
from app.domain import scaling_drivers as drivers
from app.domain.scaling_sagemaker import SageMaker
from tests.helpers import login, make_user

moto = pytest.importorskip("moto")
from botocore.stub import Stubber  # noqa: E402 - after moto, which brings botocore

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
SECRETS = {"access_key_id": "AKIAMOTO", "secret_access_key": "moto/secret+key"}
ROLE = "arn:aws:iam::123456789012:role/lens-sagemaker"
ITYPE = "ml.g4dn.12xlarge"  # moto knows SageMaker's older instance types only
# the Price List's product for hosting on that type, as AWS publishes it (attributes trimmed), with its on-demand price
PRODUCT = {
    "product": {
        "productFamily": "ML Instance",
        "sku": "YPEU36ZJTQX4MKXC",
        "attributes": {
            "servicecode": "AmazonSageMaker",
            "regionCode": "eu-west-1",
            "instanceType": f"{ITYPE}-Hosting",
            "instanceName": ITYPE,
            "component": "Hosting",
            "usagetype": f"EU-Host:{ITYPE}",
            "operation": "RunInstance",
            "gpu": "4",
            "gpuMemory": "64",
            "vCpu": "48",
        },
    },
    "terms": {
        "OnDemand": {
            "YPEU36ZJTQX4MKXC.JRTCKXETXF": {
                "priceDimensions": {"YPEU36ZJTQX4MKXC.JRTCKXETXF.6YS6EN2CT7": {"unit": "Hrs", "pricePerUnit": {"USD": "5.6730000000"}}}
            }
        }
    },
}


def _p(price=None):
    return {"id": 1, "name": "SM", "settings": {"region": "eu-west-1", "role_arn": ROLE}, "secrets": dict(SECRETS), "price": price}


def _priced(drv, p, product=PRODUCT):
    """The driver's Price List client, answering one GetProducts with `product`, checked against AWS's API model."""
    stub = Stubber(drv.client(p, "pricing", "us-east-1"))
    filters = [
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": "eu-west-1"},
        {"Type": "TERM_MATCH", "Field": "instanceName", "Value": ITYPE},
        {"Type": "TERM_MATCH", "Field": "component", "Value": "Hosting"},
    ]
    stub.add_response(
        "get_products",
        {"FormatVersion": "aws_v1", "PriceList": [json.dumps(product)]},
        {"ServiceCode": "AmazonSageMaker", "FormatVersion": "aws_v1", "Filters": filters, "MaxResults": 10},
    )
    stub.activate()
    return stub


@pytest.fixture
def sm(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(k, raising=False)
    with moto.mock_aws():
        drv = SageMaker()
        monkeypatch.setitem(drivers.DRIVERS, "sagemaker", drv)
        yield drv


def test_a_unit_is_a_model_a_configuration_and_an_endpoint_and_goes_as_all_three(sm):
    p = _p()
    assert sm.check(p) == {"region": "eu-west-1", "endpoints": 0}
    _priced(sm, p)
    t = {"model": "Qwen/Qwen3-32B", "instance_type": ITYPE, "max_model_len": 16384, "env": ["SM_VLLM_QUANTIZATION=awq"]}
    got = sm.launch(p, t, "lens-models-7", {"tag": "lens"})
    url = "https://runtime.sagemaker.eu-west-1.amazonaws.com/endpoints/lens-models-7"
    assert got == {
        "id": "lens-models-7",
        "endpoint": {"base_url": url, "model": "Qwen/Qwen3-32B", "api_key": "AKIAMOTO:moto/secret+key"},
        "price": 5.673,
    }
    assert llm.SAGEMAKER.match(url)  # Lens's model calls go there signed

    c = sm.sm(p)
    model = c.describe_model(ModelName="lens-models-7")
    assert model["ExecutionRoleArn"] == ROLE
    image = model["PrimaryContainer"]["Image"]
    assert image.startswith("763104351884.dkr.ecr.eu-west-1.amazonaws.com/vllm:") and image.endswith("-sagemaker")
    assert model["PrimaryContainer"]["Environment"] == {
        "SM_VLLM_MODEL": "Qwen/Qwen3-32B",
        "SM_VLLM_SERVED_MODEL_NAME": "Qwen/Qwen3-32B",
        "SM_VLLM_TENSOR_PARALLEL_SIZE": "4",  # the Price List's GPUs for the type
        "SM_VLLM_MAX_MODEL_LEN": "16384",
        "SM_VLLM_QUANTIZATION": "awq",
        "LENS_SCALING_UNIT": "lens-models-7",
    }
    (variant,) = c.describe_endpoint_config(EndpointConfigName="lens-models-7")["ProductionVariants"]
    assert variant["InstanceType"] == ITYPE and variant["InitialInstanceCount"] == 1 and variant["ModelName"] == "lens-models-7"
    arn = c.describe_endpoint(EndpointName="lens-models-7")["EndpointArn"]
    assert c.list_tags(ResourceArn=arn)["Tags"] == [{"Key": "lens-unit", "Value": "lens-models-7"}, {"Key": "lens", "Value": "lens"}]

    assert sm.describe(p, {"external_id": "lens-models-7"}) == {"state": "running"}
    assert sm.listing(p, "lens") == [{"id": "lens-models-7", "name": "lens-models-7"}] and sm.listing(p, "other") == []

    sm.terminate(p, {"external_id": "lens-models-7"})
    assert sm.describe(p, {"external_id": "lens-models-7"}) == {"state": "gone"} and sm.listing(p, "lens") == []
    assert c.list_models()["Models"] == [] and c.list_endpoint_configs()["EndpointConfigs"] == []
    sm.terminate(p, {"external_id": "lens-models-7"})  # already gone: no error


def test_an_endpoint_that_fails_says_sagemakers_reason(sm):
    p = _p(price=1.2)
    sm.launch(p, {"model": "meta-llama/Llama-3.1-8B-Instruct", "instance_type": "ml.g4dn.xlarge"}, "lens-m-2", {"tag": "lens"})
    backend = moto.sagemaker.models.sagemaker_backends["123456789012"]["eu-west-1"]
    e = backend.endpoints["lens-m-2"]
    e.endpoint_status, e.failure_reason = "Failed", "The model at Hugging Face is gated: give the provider a token"
    assert sm.describe(p, {"external_id": "lens-m-2"}) == {
        "state": "gone",
        "why": "SageMaker: The model at Hugging Face is gated: give the provider a token",
    }
    e.endpoint_status = "Creating"
    assert sm.describe(p, {"external_id": "lens-m-2"}) == {"state": "pending"}


def test_without_a_price_or_with_a_bad_setting_nothing_is_made(sm):
    p = _p()
    _priced(sm, p, product={"product": PRODUCT["product"], "terms": {}})
    with pytest.raises(drivers.DriverError, match="give the provider a price per hour"):
        sm.launch(p, {"model": "m", "instance_type": ITYPE}, "lens-m-3", {"tag": "lens"})
    with pytest.raises(drivers.DriverError, match="NAME=value"):
        sm.launch(_p(price=1.0), {"model": "m", "instance_type": ITYPE, "env": ["oops"]}, "lens-m-3", {"tag": "lens"})
    assert sm.sm(p).list_models()["Models"] == []


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


class Answer(io.BytesIO):
    headers = {"Content-Type": "application/json"}


def test_a_sagemaker_group_serves_lenss_model_calls_signed_until_it_scales_in(client, db, cfg, h, sm, monkeypatch):
    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    scaler = scaling.Scaler(db, lambda: settings.effective(db, base), "test")
    body = {"name": "SM", "driver": "sagemaker", "settings": {"region": "eu-west-1", "role_arn": ROLE}, "secrets": SECRETS, "price": 0.74}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "region": "eu-west-1", "endpoints": 0}
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={
            "name": "Models",
            "provider": p["id"],
            "min": 1,
            "max": 1,
            "budget": 30,
            "template": {"model": "Qwen/Qwen3-8B", "instance_type": "ml.g4dn.xlarge"},
        },
    ).json()
    assert g["kind"] == "model", g
    scaler.tick(now=NOW, lease=False)
    (n,) = scaling.nodes(db, g["id"])
    assert n["price"] == pytest.approx(0.74) and n["endpoint"]["api_key"] is True  # sealed, shown only as set

    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    assert scaling.nodes(db, g["id"])[0]["state"] == "running"
    cfg = settings.effective(db, base)
    assert cfg["llm"]["base_url"] == f"https://runtime.sagemaker.eu-west-1.amazonaws.com/endpoints/{n['external_id']}"
    assert cfg["llm"]["model"] == "Qwen/Qwen3-8B" and cfg["llm"]["api_key"] == "AKIAMOTO:moto/secret+key"

    sent = []
    answer = {"choices": [{"message": {"content": "From SageMaker."}, "finish_reason": "stop"}]}
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req) or Answer(json.dumps(answer).encode()))
    assert llm.chat(cfg, [{"role": "user", "content": "hi"}]) == "From SageMaker."
    assert sent[0].full_url.endswith("/invocations") and sent[0].get_header("Authorization").startswith(
        "AWS4-HMAC-SHA256 Credential=AKIAMOTO/"
    )

    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    assert sm.listing(scaling.opened(db, cfg, p["id"]), "lens") == []
    assert settings.effective(db, base)["llm"]["base_url"] == "http://old.home:8080/v1"
