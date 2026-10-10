"""The Prometheus scrape endpoint: off by default, answered only with its token, and nothing personal in it."""

from __future__ import annotations

from app.domain import activity, budgets, jobs, prometheus
from tests.helpers import login, make_user, seed

TOKEN = "0123456789abcdef0123456789abcdef"


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_off_by_default(client, db):
    h = admin(client, db)
    t = client.get("/api/v1/settings", headers=h).json()["telemetry"]
    assert t["values"]["prometheus"] is False
    assert t["values"]["prometheus_token"] == {"secret": True, "set": False}
    assert client.get("/metrics", headers=bearer()).status_code == 404


def test_needs_its_token(client, db):
    h = admin(client, db)
    assert client.put("/api/v1/settings/telemetry", json={"prometheus": True}, headers=h).status_code == 200
    # on, but no token set: nobody may scrape
    assert client.get("/metrics", headers=bearer()).status_code == 401
    assert client.put("/api/v1/settings/telemetry", json={"prometheus_token": "short"}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/telemetry", json={"prometheus_token": TOKEN}, headers=h).status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert client.get("/metrics", headers=bearer("x" * 32)).status_code == 401
    assert client.get("/metrics", headers=h).status_code == 401  # a session isn't the scrape token
    r = client.get("/metrics", headers=bearer())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'lens_build_info{version="' in r.text
    # the token stays sealed
    assert client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["prometheus_token"] == {"secret": True, "set": True}
    assert client.put("/api/v1/settings/telemetry", json={"prometheus": False}, headers=h).status_code == 200
    assert client.get("/metrics", headers=bearer()).status_code == 404


def test_what_it_measures(db, cfg, folder):
    rid = seed(db, cfg, folder)[0]
    jobs.enqueue(db, rid, ["analyze"])
    activity.record("out", "llm.chat", ["routine:3"], cfg=cfg, db=db, model="gpt-x", tokens_in=1000, tokens_out=200, cost_usd=0.25, ms=1500)
    activity.record("out", "llm.chat", ["routine:3"], cfg=cfg, db=db, model="gpt-x", ok=False, error="Timeout", ms=500)
    budgets.put(db, "routine:3", usd=0.2, period="day")
    (folder / "data").mkdir(exist_ok=True)
    text = prometheus.render(db)
    lines = set(text.splitlines())
    assert 'lens_jobs{status="queued"} 1' in lines
    assert 'lens_resources{status="analyzed"} 3' in lines
    assert 'lens_imports{window="24h"} 3' in lines
    assert 'lens_activity_calls{window="1h",kind="out",model="gpt-x"} 2' in lines
    assert 'lens_activity_failed{window="1h",kind="out",model="gpt-x"} 1' in lines
    assert 'lens_activity_tokens{window="24h",kind="out",model="gpt-x",direction="in"} 1000' in lines
    assert 'lens_activity_cost_usd{window="24h",kind="out",model="gpt-x"} 0.25' in lines
    assert 'lens_budget_limit_usd{resource="routine:3",period="day"} 0.2' in lines
    assert 'lens_budget_over{resource="routine:3",period="day"} 1' in lines
    assert "lens_disk_free_bytes " in text and "lens_scrape_errors 0" in lines
    # every family says what it is, once
    assert text.count("# TYPE lens_jobs gauge") == 1
    # nothing personal: no namespace names, file names or text
    samples = "\n".join(x for x in text.splitlines() if not x.startswith("#"))
    for word in ("pods", '"calls"', "ep1", "root@x.io"):
        assert word not in samples
