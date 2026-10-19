"""Auto scaling model hosts (docs/scaling.md "Model hosts"): a RunPod group launches vLLM pods with a key of their own,
sealed on the unit, joins them once vLLM answers, and deletes them; no mesh is needed. RunPod's API and the pods'
own addresses are httpx MockTransports."""

from __future__ import annotations

import datetime as dt
import io
import json

import httpx
import pytest

from app.domain import scaling, settings, store
from app.domain import scaling_drivers as drivers
from tests import fake_hf_endpoints
from tests import openapi_contract as contract
from tests.helpers import login, make_user

R = store.R
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)


class Pods:
    """RunPod's API in memory, held to RunPod's published API document (tests/data/openapi/runpod.json), and each
    pod's vLLM: healthy once `loaded` names it."""

    spec = contract.load("runpod")

    def __init__(self):
        self.calls, self.pods, self.loaded, self.next = [], {}, set(), 0

    def __call__(self, req):
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path, body, req.headers.get("authorization")))
        contract.check_request(self.spec, req.method, req.url.path, body, dict(req.url.params))
        status, answer = self.answer(req.method, req.url.path.removeprefix("/v1"), body)
        if status < 400:
            answer = contract.answer(self.spec, req.method, req.url.path, status, answer)
        return httpx.Response(status, json=answer) if answer is not None else httpx.Response(status)

    def answer(self, method, path, body):
        if path == "/pods" and method == "POST":
            if body["gpuTypeIds"] == ["NVIDIA H200"]:
                return 400, {"error": "There are no instances currently available"}
            self.next += 1
            pid = f"pod{self.next}"
            self.pods[pid] = {"id": pid, "name": body["name"], "desiredStatus": "RUNNING", "costPerHr": 0.43, "body": body}
            return 201, self.shown(pid)
        if path == "/pods":
            return 200, [self.shown(pid) for pid in self.pods]
        pid = path.rsplit("/", 1)[1]
        if pid not in self.pods:
            return 404, {"error": "pod not found"}
        if method == "DELETE":
            del self.pods[pid]
            return 204, None
        return 200, self.shown(pid)

    def shown(self, pid):
        return {k: v for k, v in self.pods[pid].items() if k != "body"}

    def health(self, req):
        pid = req.url.host.split("-", 1)[0]
        assert req.url.path == "/health" and "authorization" not in req.headers
        return httpx.Response(200 if pid in self.loaded else 502)


@pytest.fixture
def pods(monkeypatch):
    api = Pods()
    drv = drivers.get("runpod")
    monkeypatch.setattr(drv, "transport", httpx.MockTransport(api))
    monkeypatch.setattr(drv, "probe", httpx.MockTransport(api.health))
    return api


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def tick(db, cfg, now=NOW):
    return scaling.Scaler(db, lambda: cfg, "test").tick(now=now, lease=False)


def test_a_runpod_group_serves_a_model_on_gpus_it_rents(client, db, cfg, h, pods, monkeypatch):
    def no_mesh():
        raise AssertionError("a model server joins nothing")

    monkeypatch.setattr(scaling, "mesh_module", no_mesh)
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    body = {"name": "GPUs", "driver": "runpod", "settings": {}, "secrets": {"token": "rp_key", "hf_token": "hf_gated"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "pods": 0} and p["settings"]["cloud_type"] == "SECURE"
    assert pods.calls[0][3] == "Bearer rp_key"

    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Qwen", "provider": p["id"], "min": 1, "max": 1, "budget": 50, "template": {"model": "Qwen/Qwen2.5-7B-Instruct"}},
    ).json()
    assert g["kind"] == "model" and g["needs_join"] is False and g["approved"] is True
    assert g["template"]["gpu_types"] == ["NVIDIA L4"] and g["template"]["image"].startswith("vllm/vllm-openai:")
    assert client.get("/api/v1/scaling", headers=h).json()["groups"][0]["waiting_for"] is None
    assert tick(db, cfg)["launched"] == 1

    (n,) = scaling.nodes(db, g["id"])
    (pod,) = pods.pods.values()
    sent = pod["body"]
    assert sent["name"] == n["name"] and sent["ports"] == ["8000/http"] and sent["gpuTypeIds"] == ["NVIDIA L4"]
    assert sent["dockerStartCmd"][:4] == ["--model", "Qwen/Qwen2.5-7B-Instruct", "--served-model-name", "Qwen/Qwen2.5-7B-Instruct"]
    assert sent["env"]["HF_TOKEN"] == "hf_gated" and len(sent["env"]["VLLM_API_KEY"]) > 30
    assert n["price"] == pytest.approx(0.43)  # what RunPod charges for the pod it found
    key = sent["env"]["VLLM_API_KEY"]
    assert key not in str(db.one("SELECT endpoint FROM $r", r=R("scale_node", n["id"])))  # sealed on the unit
    assert n["endpoint"] == {
        "base_url": f"https://{pod['id']}-8000.proxy.runpod.net/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "api_key": True,
    }
    stored = db.one(f"SELECT {scaling.NODE_FIELDS} FROM $r", r=R("scale_node", n["id"]))
    assert scaling.endpoint_of(cfg, stored)["api_key"] == key

    tick(db, cfg, NOW + dt.timedelta(minutes=1))
    assert scaling.nodes(db, g["id"])[0]["state"] == "launching"  # the model is still loading
    pods.loaded.add(pod["id"])
    tick(db, cfg, NOW + dt.timedelta(minutes=2))
    (n,) = scaling.nodes(db, g["id"])
    assert n["state"] == "running" and n["endpoint"]["api_key"] is True
    stored = db.one(f"SELECT {scaling.NODE_FIELDS} FROM $r", r=R("scale_node", n["id"]))
    assert scaling.endpoint_of(cfg, stored)["api_key"] == key  # the key stays across ticks

    pod["desiredStatus"] = "EXITED"  # a spot pod RunPod took back
    tick(db, cfg, NOW + dt.timedelta(minutes=3))
    assert pod["id"] not in pods.pods and scaling.nodes(db, g["id"])[0]["state"] == "terminated"


def test_runpod_says_why_it_made_no_pod(client, db, cfg, h, pods):
    body = {"name": "GPUs", "driver": "runpod", "settings": {"data_centers": ["EU-RO-1"]}, "secrets": {"token": "rp"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    drv = drivers.get("runpod")
    opened = scaling.opened(db, cfg, p["id"])
    boot = {"env": {}, "line": None, "tag": "lens"}
    with pytest.raises(drivers.DriverError, match="RunPod said 400: There are no instances currently available"):
        drv.launch(opened, {"model": "m", "gpu_types": ["NVIDIA H200"]}, "lens-q-1", boot)
    got = drv.launch(opened, {"model": "m", "gpu_types": ["NVIDIA A40"], "gpu_count": 2, "max_model_len": 8192}, "lens-q-2", boot)
    sent = pods.pods[got["id"]]["body"]
    assert sent["dataCenterIds"] == ["EU-RO-1"] and "HF_TOKEN" not in sent["env"]
    assert sent["dockerStartCmd"][-4:] == ["--tensor-parallel-size", "2", "--max-model-len", "8192"]
    assert drv.listing(opened, "lens") == [{"id": got["id"], "name": "lens-q-2"}]
    drv.terminate(opened, {"external_id": got["id"]})
    assert drv.describe(opened, {"external_id": got["id"]}) == {"state": "gone"}
    drv.terminate(opened, {"external_id": got["id"]})  # already gone: no error


def test_a_running_model_unit_serves_lenss_model_calls_until_none_runs(client, db, cfg, h, pods):
    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big", "api_key": "old-key"}, "root@x.io")
    llm = lambda: settings.effective(db, base)["llm"]  # noqa: E731
    scaler = scaling.Scaler(db, lambda: settings.effective(db, base), "test")
    body = {"name": "GPUs", "driver": "runpod", "settings": {}, "secrets": {"token": "rp"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Qwen", "provider": p["id"], "min": 1, "max": 1, "budget": 50, "template": {"model": "Qwen/Qwen3-8B"}},
    ).json()
    assert g["template"]["use_as_provider"] is True

    scaler.tick(now=NOW, lease=False)
    (pod,) = pods.pods.values()
    assert llm()["base_url"] == "http://old.home:8080/v1"  # nothing serves until the model is loaded
    pods.loaded.add(pod["id"])
    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    now_llm = llm()
    assert now_llm["base_url"] == f"https://{pod['id']}-8000.proxy.runpod.net/v1" and now_llm["model"] == "Qwen/Qwen3-8B"
    assert now_llm["api_key"] == pod["body"]["env"]["VLLM_API_KEY"]
    assert "old-key" not in str(db.one("SELECT previous FROM $r", r=scaling.ROW))  # the old key waits sealed

    pod["desiredStatus"] = "EXITED"
    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    back = llm()
    assert (back["base_url"], back["model"], back["api_key"]) == ("http://old.home:8080/v1", "big", "old-key")  # as it was
    assert not (db.one("SELECT serving FROM $r", r=scaling.ROW) or {}).get("serving")


def test_model_calls_go_to_a_groups_running_units_in_turn(client, db, cfg, h, pods, monkeypatch):
    from app.domain import llm as llm_

    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    eff = lambda: settings.effective(db, base)  # noqa: E731
    scaler = scaling.Scaler(db, eff, "test")
    p = client.post("/api/v1/scaling/providers", headers=h, json={"name": "G", "driver": "runpod", "secrets": {"token": "rp"}}).json()
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Q", "provider": p["id"], "min": 3, "max": 3, "budget": 50, "template": {"model": "m"}},
    ).json()
    scaler.tick(now=NOW, lease=False)
    assert len({pod["body"]["env"]["VLLM_API_KEY"] for pod in pods.pods.values()}) == 1  # one key for the group's units
    assert "unit_key" not in client.get(f"/api/v1/scaling/groups/{g['id']}", headers=h).json()
    pods.loaded.update(list(pods.pods)[:2])  # the third is still loading its model
    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    urls = {pid: f"https://{pid}-8000.proxy.runpod.net/v1" for pid in pods.pods}
    served, other, loading = urls.values()
    assert eff()["llm"]["base_url"] == served and eff()["llm"]["spread"] == {"base_url": served, "urls": [other]}

    went, refusing = [], set()

    def urlopen(req, timeout=None):
        went.append(req.full_url.removesuffix("/chat/completions"))
        assert req.headers["Authorization"] == "Bearer " + pods.pods["pod1"]["body"]["env"]["VLLM_API_KEY"]
        if any(req.full_url.startswith(u) for u in refusing):
            raise llm_.urllib.error.URLError("connection refused")  # a unit going away hands its call on
        r = io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
        r.headers = {"Content-Type": "application/json"}
        return r

    monkeypatch.setattr(llm_.urllib.request, "urlopen", urlopen)
    for _ in range(4):
        assert llm_.chat(eff(), [{"role": "user", "content": "hi"}]) == "ok"
    assert sorted(went) == sorted([served, other] * 2)
    went.clear()
    refusing.add(other)
    for _ in range(2):
        llm_.chat(eff(), [{"role": "user", "content": "hi"}])
    assert went.count(served) == 2 and loading not in went

    pods.loaded.add("pod3")
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    assert eff()["llm"]["spread"] == {"base_url": served, "urls": sorted([other, loading])}
    del pods.pods["pod2"], pods.pods["pod3"]  # gone, and not yet replaced
    scaler.tick(now=NOW + dt.timedelta(minutes=3), lease=False)
    assert eff()["llm"]["base_url"] == served and eff()["llm"]["spread"] is None

    settings.save(db, base, "llm", {"spread": {"base_url": served, "urls": [other]}}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://mine.home/v1", "model": "mine"}, "root@x.io")
    assert llm_._spread(eff()) == [eff()]  # a spread left from another provider sends nothing elsewhere
    scaler.tick(now=NOW + dt.timedelta(minutes=4), lease=False)
    assert eff()["llm"]["spread"] is None


def test_llm_spread_is_checked(db, cfg):
    for bad in ({"base_url": "http://a/v1", "urls": []}, {"base_url": "http://a/v1", "urls": ["ftp://b"]}, {"urls": ["http://b"]}):
        with pytest.raises(ValueError, match="llm.spread"):
            settings.save(db, cfg, "llm", {"spread": bad}, "root@x.io")


def test_a_provider_a_person_picks_while_a_unit_serves_stays(client, db, cfg, h, pods):
    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    llm = lambda: settings.effective(db, base)["llm"]  # noqa: E731
    scaler = scaling.Scaler(db, lambda: settings.effective(db, base), "test")
    p = client.post("/api/v1/scaling/providers", headers=h, json={"name": "G", "driver": "runpod", "secrets": {"token": "rp"}}).json()
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={"name": "Q", "provider": p["id"], "min": 1, "max": 1, "budget": 50, "template": {"model": "m"}},
    ).json()
    scaler.tick(now=NOW, lease=False)
    pods.loaded.update(pods.pods)
    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    assert llm()["model"] == "m"
    settings.save(db, base, "llm", {"base_url": "http://mine.home/v1", "model": "mine"}, "root@x.io")
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    assert llm()["base_url"] == "http://mine.home/v1"  # the person's pick stays while the unit runs
    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    for pod in pods.pods.values():
        pod["desiredStatus"] = "EXITED"
    scaler.tick(now=NOW + dt.timedelta(minutes=3), lease=False)
    assert llm()["base_url"] == "http://mine.home/v1" and not (db.one("SELECT serving FROM $r", r=scaling.ROW) or {}).get("serving")


@pytest.fixture
def hf(monkeypatch):
    api = fake_hf_endpoints.Endpoints()
    monkeypatch.setattr(drivers.get("huggingface"), "transport", httpx.MockTransport(api))
    return api


def test_a_hugging_face_group_serves_a_model_on_an_inference_endpoint(client, db, cfg, h, hf):
    base = cfg
    settings.save(db, base, "scaling", {"enabled": True}, "root@x.io")
    settings.save(db, base, "llm", {"base_url": "http://old.home:8080/v1", "model": "big"}, "root@x.io")
    scaler = scaling.Scaler(db, lambda: settings.effective(db, base), "test")
    body = {"name": "HF", "driver": "huggingface", "secrets": {"token": "hf_manage"}}
    p = client.post("/api/v1/scaling/providers", headers=h, json=body).json()
    assert p["check"] == {"ok": True, "account": "ada", "endpoints": 0}
    assert hf.calls[0][3] == "Bearer hf_manage"

    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={
            "name": "A rather long group name",
            "provider": p["id"],
            "min": 1,
            "max": 1,
            "budget": 50,
            "template": {"model": "Qwen/Qwen3-8B", "hardware": "aws-us-east-1-nvidia-l4-x4", "max_model_len": 8192},
        },
    ).json()
    assert g["kind"] == "model" and g["template"]["use_as_provider"] is True
    scaler.tick(now=NOW, lease=False)

    (e,) = hf.endpoints.values()
    (n,) = scaling.nodes(db, g["id"])
    assert len(e["name"]) <= 32 and e["name"].endswith(f"-{n['id']}") and n["external_id"] == f"ada/{e['name']}"
    assert e["type"] == "public" and e["provider"] == {"vendor": "aws", "region": "us-east-1"}
    assert e["compute"]["instanceType"] == "nvidia-l4" and e["compute"]["instanceSize"] == "x4"
    assert e["compute"]["scaling"] == {"minReplica": 1, "maxReplica": 1}
    assert e["model"]["repository"] == "Qwen/Qwen3-8B" and list(e["model"]["image"]) == ["vLLM"]
    assert e["model"]["args"] == ["--served-model-name", "Qwen/Qwen3-8B", "--tensor-parallel-size", "4", "--max-model-len", "8192"]
    key = e["model"]["secrets"]["VLLM_API_KEY"]
    assert "hf_manage" not in json.dumps(e) and len(key) > 30  # model calls never carry the account's token
    assert n["price"] == pytest.approx(3.8) and n["state"] == "launching"

    scaler.tick(now=NOW + dt.timedelta(minutes=1), lease=False)
    assert scaling.nodes(db, g["id"])[0]["state"] == "launching"  # still pending at Hugging Face
    hf.set_state(e["name"], "running", url="https://abc.us-east-1.aws.endpoints.huggingface.cloud")
    scaler.tick(now=NOW + dt.timedelta(minutes=2), lease=False)
    (n,) = scaling.nodes(db, g["id"])
    assert n["state"] == "running"
    llm = settings.effective(db, base)["llm"]
    assert llm["base_url"] == "https://abc.us-east-1.aws.endpoints.huggingface.cloud/v1" and llm["api_key"] == key

    client.patch(f"/api/v1/scaling/groups/{g['id']}", headers=h, json={"min": 0, "desired": 0})
    scaler.tick(now=NOW + dt.timedelta(minutes=3), lease=False)
    assert hf.endpoints == {} and settings.effective(db, base)["llm"]["base_url"] == "http://old.home:8080/v1"


def test_endpoints_that_never_come_up_pause_the_group(client, db, cfg, h, hf):
    settings.save(db, cfg, "scaling", {"enabled": True}, "root@x.io")
    cfg = settings.effective(db, cfg)
    p = client.post(
        "/api/v1/scaling/providers",
        headers=h,
        json={"name": "HF", "driver": "huggingface", "settings": {"namespace": "ada"}, "secrets": {"token": "t"}},
    ).json()
    with pytest.raises(drivers.DriverError, match="no hardware 'aws-mars-1-h100'"):
        drivers.get("huggingface").price(scaling.opened(db, cfg, p["id"]), {"hardware": "aws-mars-1-h100"})
    g = client.post(
        "/api/v1/scaling/groups",
        headers=h,
        json={
            "name": "Gated",
            "provider": p["id"],
            "min": 1,
            "max": 1,
            "budget": 50,
            "cooldown_seconds": 0,
            "template": {"model": "meta/gated"},
        },
    ).json()
    made = set()
    for i in range(8):
        tick(db, cfg, NOW + dt.timedelta(minutes=i))
        for name in list(hf.endpoints):
            made.add(name)
            hf.set_state(name, "failed", error="403: the model is gated")
        if client.get("/api/v1/scaling", headers=h).json()["groups"][0]["paused"]:
            break
    view = client.get("/api/v1/scaling", headers=h).json()["groups"][0]
    assert view["paused"] and "3 units in a row failed to come up" in view["paused_why"]
    assert len(made) == 3 and hf.endpoints == {}  # each one deleted at Hugging Face
    (last, *_) = scaling.nodes(db, g["id"])
    assert last["state"] == "failed" and "Hugging Face: 403: the model is gated" in last["why"]
    assert not any(c[1].startswith("https://huggingface.co") for c in hf.calls)  # the account was given: no whoami


def test_lens_reads_only_fields_hugging_face_answers_with():
    spec = fake_hf_endpoints.SPEC
    contract.check_fields(spec, "EndpointWithStatus", "name", "model.repository", "status.state", "status.url")
    contract.check_fields(spec, "EndpointWithStatus", "status.message", "status.errorMessage")
    contract.check_fields(spec, "EndpointWithStatusList", "items")
    for f in ("id", "accelerator", "instanceType", "instanceSize", "numAccelerators", "pricePerHour"):
        contract.check_fields(spec, "Vendors", f"vendors.regions.computes.{f}")
    contract.check_fields(spec, "Vendors", "vendors.name", "vendors.regions.name")
    with pytest.raises(AssertionError, match="compute"):  # the fake would refuse what Hugging Face refuses
        contract.check_request(
            spec, "POST", "/v2/endpoint/ada", {"name": "x", "type": "public", "provider": {"vendor": "aws", "region": "r"}}
        )


def test_lens_reads_only_fields_runpod_answers_with():
    for f in ("id", "name", "desiredStatus", "costPerHr", "adjustedCostPerHr"):
        contract.check_fields(Pods.spec, "Pod", f)
