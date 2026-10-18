"""Auto scaling model hosts (docs/scaling.md "Model hosts"): a RunPod group launches vLLM pods with a key of their own,
sealed on the unit, joins them once vLLM answers, and deletes them; no mesh is needed. RunPod's API and the pods'
own addresses are httpx MockTransports."""

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


class Pods:
    """RunPod's API in memory, and each pod's vLLM: healthy once `loaded` names it."""

    def __init__(self):
        self.calls, self.pods, self.loaded, self.next = [], {}, set(), 0

    def __call__(self, req):
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path, body, req.headers.get("authorization")))
        path = req.url.path.removeprefix("/v1")
        if path == "/pods" and req.method == "POST":
            if body["gpuTypeIds"] == ["NVIDIA H200"]:
                return httpx.Response(400, json={"error": "There are no instances currently available"})
            self.next += 1
            pid = f"pod{self.next}"
            self.pods[pid] = {"id": pid, "name": body["name"], "desiredStatus": "RUNNING", "costPerHr": 0.43, "body": body}
            return httpx.Response(201, json=self.pods[pid])
        if path == "/pods":
            return httpx.Response(200, json=list(self.pods.values()))
        pid = path.rsplit("/", 1)[1]
        if pid not in self.pods:
            return httpx.Response(404, json={"error": "pod not found"})
        if req.method == "DELETE":
            del self.pods[pid]
            return httpx.Response(204)
        return httpx.Response(200, json=self.pods[pid])

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
