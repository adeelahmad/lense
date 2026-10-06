"""Opt-in telemetry: off by default, sent only to the configured OTLP endpoint, and nothing personal in it."""

from __future__ import annotations

import threading

import pytest

from app.domain import jobs, telemetry
from tests import fake_llm, fake_otlp
from tests.helpers import drain, login, make_user, seed


@pytest.fixture
def collector():
    srv, url = fake_otlp.start()
    yield url
    telemetry.shutdown()  # telemetry is per process: the next test starts with it off
    srv.shutdown()


@pytest.fixture
def model(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    yield fake_llm.Handler
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def app(cfg, db, model):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_off_by_default_sends_nothing(client, db, cfg, folder, collector):
    rid = seed(db, cfg, folder)[0]
    h = admin(client, db)
    t = client.get("/api/v1/settings", headers=h).json()["telemetry"]
    assert t["values"]["enabled"] is False and t["values"]["endpoint"] is None
    assert t["values"]["headers"] == {"secret": True, "set": False} and t["locked"] == []
    # an endpoint alone doesn't turn it on
    assert client.put("/api/v1/settings/telemetry", json={"endpoint": collector}, headers=h).status_code == 200
    assert client.get(f"/api/v1/recordings/{rid}", headers=h).status_code == 200
    jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    assert client.post("/api/v1/settings/llm/test", headers=h).json()["ok"]
    assert not telemetry.active()
    telemetry.flush()
    assert fake_otlp.Handler.bodies == []
    s = client.get("/api/v1/settings/telemetry/status", headers=h).json()
    assert (s["enabled"], s["traces"], s["metrics"], s["last_traces"]) == (False, False, False, None)


def test_settings_are_checked(client, db, collector):
    h = admin(client, db)

    def put(body):
        return client.put("/api/v1/settings/telemetry", json=body, headers=h)

    for bad in (
        {"endpoint": "ftp://collector:4318"},
        {"endpoint": "http://collector:4318/v1/traces"},
        {"enabled": "yes"},
        {"sample_ratio": 1.5},
        {"export_seconds": 1},
        {"service_name": "lens server"},
        {"prices": {"gpt": {"input": -1, "output": 1}}},
        {"prices": {"gpt": {"input": 1, "cached": 1}}},
        {"prices": ["gpt"]},
        {"headers": "no-equals-sign"},
        {"endpoint": "http://user:secret@collector:4318"},
        {"endpoint": "http://token@collector:4318"},
        {"endpoint": "http://169.254.169.254:4318"},
        {"endpoint": "http://[fe80::1]:4318"},
        {"endpoint": "http://collector:99999"},
        {"colour": "blue"},
    ):
        assert put(bad).status_code == 400, bad
    assert put({"endpoint": "http://collector:4318/", "prices": {" gpt-4o ": {"input": 2.5, "output": 10}}}).status_code == 200
    v = client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]
    assert v["endpoint"] == "http://collector:4318" and v["prices"] == {"gpt-4o": {"input": 2.5, "output": 10.0}}
    assert put({"headers": "Authorization=Bearer%20abc,X-Tenant=lens"}).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["headers"] == {"secret": True, "set": True}
    assert put({"prices": {}}).status_code == 200

    # the saved headers stay with their collector: a new path keeps them, a new host drops them
    assert put({"endpoint": "http://collector:4318/otlp"}).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["headers"]["set"] is True
    assert put({"endpoint": "http://elsewhere:4318", "headers": {"secret": True}}).status_code == 200  # the mask: unchanged
    assert client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["headers"]["set"] is False
    # unless new ones come with the move
    assert put({"endpoint": "http://third:4318", "headers": "X-Key=1"}).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["headers"]["set"] is True


def test_traces_and_metrics_go_to_the_endpoint(client, db, cfg, folder, collector, model):
    cfg["ai"]["tools"] = False  # the plain, streamed answer
    rid = seed(db, cfg, folder)[0]
    h = admin(client, db)
    r = client.put(
        "/api/v1/settings/telemetry",
        headers=h,
        json={
            "enabled": True,
            "endpoint": collector,
            "headers": "Authorization=Bearer%20t0k",
            "prices": {"fake": {"input": 1.0, "output": 2.0}},
        },
    )
    assert r.status_code == 200, r.text
    # the next request picks the change up
    assert client.get(f"/api/v1/recordings/{rid}", params={"q": "private words"}, headers=h).status_code == 200
    assert telemetry.active()
    assert client.get("/api/v1/recordings/999999", headers=h).status_code == 404
    assert client.post("/api/v1/settings/llm/test", headers=h).json()["ok"]
    jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    cid = client.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["calls"]}}).json()["id"]
    assert "The shipment" in client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When does it ship?"}).text
    telemetry.flush()

    spans = {s["name"]: s for s in fake_otlp.Handler.spans}
    got = [s["attrs"] for s in fake_otlp.Handler.spans if s["name"] == "GET /api/v1/recordings/{rid}"]
    assert [(a["http.route"], a["http.response.status_code"]) for a in got] == [("/api/v1/recordings/{rid}", c) for c in (200, 404)]
    got = spans["GET /api/v1/recordings/{rid}"]
    assert got["resource"]["service.name"] == "lens" and got["resource"]["lens.process.role"] == "api"
    assert "host.name" not in got["resource"]
    job, step = spans["job"], spans["step analyze"]
    assert (job["attrs"]["lens.recording.id"], job["attrs"]["lens.job.outcome"]) == (str(rid), "succeeded")
    assert "lens.pipeline.id" not in job["attrs"]  # steps asked for by hand, not a pipeline
    assert step["attrs"]["lens.step.outcome"] == "done"
    calls = [s for s in fake_otlp.Handler.spans if s["name"] == "chat fake"]
    assert len(calls) == 2  # the test and the streamed answer
    for c in calls:
        a = c["attrs"]
        assert (a["gen_ai.request.model"], a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == ("fake", 1000, 500)
        assert a["lens.llm.cost_usd"] == pytest.approx(0.002) and a["server.address"] == "127.0.0.1"
    assert {h_.get("authorization") for h_ in fake_otlp.Handler.headers} == {"Bearer t0k"}

    names = {p["name"] for p in fake_otlp.Handler.points}
    assert {"http.server.request.duration", "lens.jobs", "lens.job.step.duration", "gen_ai.client.token.usage"} <= names
    assert {"gen_ai.client.operation.duration", "lens.llm.cost"} <= names
    cost = sum(p["value"] for p in fake_otlp.Handler.points if p["name"] == "lens.llm.cost")
    assert cost == pytest.approx(0.004)
    tokens = {"input": 0.0, "output": 0.0}
    for p in fake_otlp.Handler.points:
        if p["name"] == "gen_ai.client.token.usage":
            tokens[p["attrs"]["gen_ai.token.type"]] += p["value"]
    assert tokens == {"input": 2000, "output": 1000}

    # nothing personal: no query strings, prompts, answers, titles or transcript text
    raw = b"".join(fake_otlp.Handler.bodies)
    for text in (b"private words", b"When does it ship", b"The shipment", b"ep1", b"capsid", b"root@x.io"):
        assert text not in raw, text

    s = client.get("/api/v1/settings/telemetry/status", headers=h).json()
    assert (s["enabled"], s["endpoint"], s["traces"], s["metrics"]) == (True, collector, True, True)
    assert s["last_traces"]["ok"] and s["last_metrics"]["ok"]

    # off again: the next request stops it, and nothing more is sent
    assert client.put("/api/v1/settings/telemetry", json={"enabled": False}, headers=h).status_code == 200
    client.get("/api/v1/jobs", headers=h)
    assert not telemetry.active()
    for t in threading.enumerate():  # the old providers send what they held, in the background
        if t.name == "telemetry-stop":
            t.join(15)
    fake_otlp.reset()
    client.get(f"/api/v1/recordings/{rid}", headers=h)
    assert fake_otlp.Handler.bodies == []


def test_send_a_test_span(client, db, collector):
    h = admin(client, db)
    r = client.post("/api/v1/settings/telemetry/test", headers=h).json()
    assert (r["ok"], r["error"]) == (False, "set an endpoint first")
    client.put("/api/v1/settings/telemetry", json={"endpoint": collector}, headers=h)
    r = client.post("/api/v1/settings/telemetry/test", headers=h).json()
    assert r["ok"] and r["ms"] >= 0, r
    assert [s["name"] for s in fake_otlp.Handler.spans] == ["lens.telemetry.test"]
    assert not telemetry.active()  # testing doesn't turn it on
    fake_otlp.Handler.refuse = True
    r = client.post("/api/v1/settings/telemetry/test", headers=h).json()
    assert not r["ok"] and r["error"]


def test_environment_turns_it_on_or_keeps_it_off(client, db, collector, monkeypatch):
    h = admin(client, db)
    monkeypatch.setenv("LENS_TELEMETRY", "off")
    client.put("/api/v1/settings/telemetry", json={"enabled": True, "endpoint": collector}, headers=h)
    t = client.get("/api/v1/settings", headers=h).json()["telemetry"]
    assert t["values"]["enabled"] is False and t["locked"] == ["enabled"]
    assert not telemetry.active()
    assert client.get("/api/v1/setup", headers=h).json()["telemetry"]["locked"] == ["enabled"]

    monkeypatch.setenv("LENS_TELEMETRY", "on")
    monkeypatch.setenv("LENS_TELEMETRY_ENDPOINT", collector + "/")
    client.put("/api/v1/settings/telemetry", json={"enabled": False}, headers=h)  # a save rebuilds the settings
    client.get("/api/v1/jobs", headers=h)
    assert telemetry.active()
    assert client.get("/api/v1/settings/telemetry/status", headers=h).json()["endpoint"] == collector + "/"


def test_setup_wizard_opt_in(client, db, collector):
    h = admin(client, db)
    v = client.get("/api/v1/setup", headers=h).json()["telemetry"]
    assert v == {"enabled": False, "endpoint": None, "locked": []}
    r = client.put("/api/v1/setup/telemetry", json={"enabled": True}, headers=h)
    assert r.status_code == 400 and "endpoint" in r.json()["detail"]
    assert client.put("/api/v1/setup/telemetry", json={"enabled": True, "endpoint": "nope"}, headers=h).status_code == 400
    r = client.put("/api/v1/setup/telemetry", json={"enabled": True, "endpoint": collector}, headers=h)
    assert r.json()["saved"] == ["enabled", "endpoint"]
    assert client.get("/api/v1/setup", headers=h).json()["telemetry"] == {"enabled": True, "endpoint": collector, "locked": []}
    # off keeps the address for later
    client.put("/api/v1/setup/telemetry", json={"enabled": False}, headers=h)
    assert client.get("/api/v1/setup", headers=h).json()["telemetry"] == {"enabled": False, "endpoint": collector, "locked": []}
    audit = client.get("/api/v1/audit", headers=h).json()
    assert any(a["action"] == "settings.save" and a["target"] == "telemetry" for a in audit)


def test_cost_and_headers():
    prices = {"m": {"input": 3.0, "output": 15.0}}
    assert telemetry.cost("m", 2_000_000, 100_000, prices) == pytest.approx(7.5)
    assert telemetry.cost("other", 10, 10, prices) is None
    cfg = {"llm": {"model": "m"}, "ai": {"price_in": 1.0, "price_out": 2.0}, "telemetry": {"prices": {"x": {"input": 0, "output": 1}}}}
    assert telemetry.prices(cfg) == {"m": {"input": 1.0, "output": 2.0}, "x": {"input": 0, "output": 1}}
    cfg["telemetry"]["prices"]["m"] = {"input": 5.0, "output": 5.0}
    assert telemetry.prices(cfg)["m"] == {"input": 5.0, "output": 5.0}
    assert telemetry.parse_headers("a=1, Authorization=Bearer%20x") == {"a": "1", "Authorization": "Bearer x"}
    assert telemetry.parse_headers("") == {}
    with pytest.raises(ValueError):
        telemetry.parse_headers("bad header=1")


def test_a_new_workers_first_job_is_traced(client, db, cfg, folder, collector):
    """A worker reads the saved settings when it starts, so telemetry is on before its first job's span."""
    from app.domain import settings

    rid = seed(db, cfg, folder)[0]
    settings.save(db, cfg, "telemetry", {"enabled": True, "endpoint": collector})
    jobs.enqueue(db, rid, ["analyze"])
    telemetry.shutdown()  # as in a fresh worker process
    worker = jobs.Worker(db, settings.Settings(db, cfg).current, name="w1", steps=["analyze"])  # lens worker --steps
    assert worker.drain() == 1
    telemetry.flush()
    assert "job" in {s["name"] for s in fake_otlp.Handler.spans}
