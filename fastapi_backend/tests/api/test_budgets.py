"""Budgets: off unless set; a run over one waits for someone to pick (or is skipped, or the assistant weighs it)."""

from __future__ import annotations

import pytest

from app.domain import activity, budgets, decide, jobs, routines, store
from tests.helpers import drain, login, make_user, seed

R = store.R


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def spend(db, resource, usd=0.0, tokens=0):
    activity.record("out", "model.chat", [resource], db=db, model="m", cost_usd=usd, tokens_in=tokens)


def routine(client, h, name="Sync"):
    return client.post("/api/v1/routines", headers=h, json={"name": name, "actions": [{"type": "sync"}]}).json()["id"]


def test_setting_reading_and_removing_a_budget(client, db, h):
    rid = routine(client, h)
    res = f"routine:{rid}"
    st = client.get("/api/v1/budgets/status", headers=h, params={"resource": res}).json()
    assert st["state"] == "none" and st["next"]["runs"] == 0  # off unless set

    bad = [{"period": "month"}, {"usd": -1}, {"usd": 1, "period": "year"}, {"usd": 1, "on_over": "maybe"}, {"usd": 1, "warn_at": 2}]
    for body in bad:
        assert client.put("/api/v1/budgets", headers=h, params={"resource": res}, json=body).status_code in (400, 422), body
    assert client.put("/api/v1/budgets", headers=h, params={"resource": "chat:1"}, json={"usd": 1}).status_code == 400

    spend(db, res, usd=0.85, tokens=900)
    st = client.put("/api/v1/budgets", headers=h, params={"resource": res}, json={"usd": 1, "tokens": 10_000}).json()
    assert (st["state"], st["share"], st["spent"]["usd"], st["left"]["usd"], st["left"]["tokens"]) == ("near", 0.85, 0.85, 0.15, 9100)
    assert st["budget"]["on_over"] == "ask" and st["budget"]["period"] == "month"
    assert [b["resource"] for b in client.get("/api/v1/budgets", headers=h).json()] == [res]

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    e = login(client, "ed@x.io", "editor password 1")
    assert client.put("/api/v1/budgets", headers=e, params={"resource": res}, json={"usd": 9}).status_code == 403
    assert client.delete("/api/v1/budgets", headers=h, params={"resource": res}).status_code == 200
    assert client.delete("/api/v1/budgets", headers=h, params={"resource": res}).status_code == 404
    assert any(r["action"] == "budget.set" for r in activity.history(db, res))


def test_a_routine_over_budget_waits_for_a_pick(client, db, cfg, h):
    rid = routine(client, h)
    res = f"routine:{rid}"
    budgets.put(db, res, usd=0.5)
    spend(db, res, usd=0.6)

    # a person asking is told where it stands, and can choose to run it anyway
    r = client.post(f"/api/v1/routines/{rid}/run", headers=h, json={})
    assert r.status_code == 409 and "budget is used up" in r.json()["detail"]

    # the schedule (or anything else) asking: held, once, however many times it comes due
    routines.request_run(db, rid)
    assert routines.run_due(db, cfg, log=None) == 0
    routines.request_run(db, rid)
    routines.run_due(db, cfg, log=None)
    held = routines.runs(db, rid)
    assert [x["status"] for x in held] == ["held"] and held[0]["hold"]["resource"] == res
    assert held[0]["hold"]["missed"] == 1 and "$0.6 of $0.5 this month" in held[0]["hold"]["why"]
    assert routines.get(db, rid)["last_status"] == "held"

    # doing nothing changes nothing; a pick to run it runs it once
    assert client.post(f"/api/v1/routines/runs/{held[0]['id']}/decide", headers=h, json={"run": True}).status_code == 200
    assert routines.run_due(db, cfg, log=None) == 1
    assert [x["status"] for x in routines.runs(db, rid)] == ["done", "released"]
    assert client.post(f"/api/v1/routines/runs/{held[0]['id']}/decide", headers=h, json={"run": True}).status_code == 400

    # chosen up front by a person
    assert client.post(f"/api/v1/routines/{rid}/run", headers=h, json={"over_budget": True}).status_code == 200
    assert routines.run_due(db, cfg, log=None) == 1

    # skip instead of ask
    budgets.put(db, res, usd=0.5, on_over="skip")
    routines.request_run(db, rid)
    routines.run_due(db, cfg, log=None)
    assert routines.runs(db, rid)[0]["status"] == "skipped"


def test_the_next_run_is_estimated_from_past_ones(client, db, cfg, h):
    rid = routine(client, h)
    res = f"routine:{rid}"
    for _ in range(2):
        with activity.scope(db, res):
            spend(db, res, usd=0.3, tokens=100)
        routines.run(db, cfg, rid)
    st = budgets.status(db, res)
    assert (st["next"]["usd"], st["next"]["tokens"], st["next"]["runs"]) == (0.3, 100, 2)

    # 0.6 spent of 0.8 isn't over, but another ≈0.3 run would be
    budgets.put(db, res, usd=0.8, period="day")
    v = budgets.check(db, cfg, [res])
    assert (v["go"], v["action"], v["resource"]) == (False, "ask", res)
    assert "the next run (≈$0.3" in v["why"] and "would go over" in v["why"]
    budgets.put(db, res, usd=0.95, period="day")
    assert budgets.check(db, cfg, [res])["go"] is True

    # per run: only the estimate counts
    budgets.put(db, res, usd=0.2, period="run")
    assert budgets.check(db, cfg, [res])["go"] is False


def test_the_assistant_weighs_it_when_asked_to(client, db, cfg, h, monkeypatch):
    rid = routine(client, h)
    res = f"routine:{rid}"
    budgets.put(db, res, tokens=10, on_over="assistant")
    spend(db, res, tokens=20)
    picks = []

    def choose(cfg_, question, options, state):
        picks.append(state)
        return {"choice": "run", "confidence": confidence, "ranked": [], "by": "jev"}

    monkeypatch.setattr(decide, "choose", choose)
    confidence = 0.95
    v = budgets.check(db, cfg, [res])
    assert v["go"] is True and "assistant let it run" in v["why"]
    assert picks[0]["spent"]["tokens"] == 20 and picks[0]["budget"]["tokens"] == 10
    confidence = 0.5  # not sure: ask
    assert budgets.check(db, cfg, [res])["action"] == "ask"

    def undecided(*a):
        raise decide.Undecided("nothing set up")

    monkeypatch.setattr(decide, "choose", undecided)
    assert budgets.check(db, cfg, [res])["action"] == "ask"


def test_jobs_over_a_budget_are_held_until_released(client, db, cfg, folder, h):
    rids = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    budgets.put(db, f"space:{pods}", usd=0.1, period="week")
    spend(db, f"space:{pods}", usd=0.2)
    for rid in rids:
        jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    got = {j["recording"]: j for j in jobs.list_jobs(db)}
    held = [j for j in got.values() if j["status"] == "held"]
    assert len(held) == 2 and all(j["hold"]["resource"] == f"space:{pods}" for j in held)
    assert [j["status"] for j in got.values() if j["space"] != pods] == ["succeeded"]
    assert jobs.enqueue(db, held[0]["recording"], ["analyze"]) == held[0]["id"]  # still the one job

    r = client.get("/api/v1/jobs", headers=h).json()
    assert {j["status"] for j in r["jobs"]} >= {"held"}
    assert client.post(f"/api/v1/jobs/{held[0]['id']}/release", headers=h, json={"run": True}).status_code == 200
    assert client.post(f"/api/v1/jobs/{held[1]['id']}/release", headers=h, json={"run": False}).status_code == 200
    drain(db, cfg)
    assert jobs.get(db, held[0]["id"])["status"] == "succeeded"  # let through once, not held again
    assert jobs.get(db, held[1]["id"])["status"] == "cancelled"
    assert client.post(f"/api/v1/jobs/{held[0]['id']}/release", headers=h, json={"run": True}).status_code == 400


def test_the_periodic_check_warns_once_per_period(db, cfg):
    budgets.put(db, "workflow:1", usd=1)
    assert budgets.sweep(db, cfg) == []
    spend(db, "workflow:1", usd=0.9)
    assert [a["state"] for a in budgets.sweep(db, cfg)] == ["near"]
    assert budgets.sweep(db, cfg) == []
    spend(db, "workflow:1", usd=0.2)
    assert [a["state"] for a in budgets.sweep(db, cfg)] == ["over"]
    assert budgets.sweep(db, cfg) == []
    assert [r["action"] for r in activity.history(db, "workflow:1", kind="run")] == ["budget.over", "budget.near"]


def test_the_meter_follows_the_namespace_and_its_budget(client, db, h):
    sid = store.ns_id(db, "pods")
    other = store.ns_id(db, "news")
    rid = routine(client, h, "Digest")
    activity.record("out", "model.chat", [f"space:{sid}", f"routine:{rid}"], db=db, model="gpt", cost_usd=0.3, tokens_in=100, tokens_out=20)
    activity.record("out", "model.chat", [f"space:{sid}"], db=db, model="local", tokens_in=50)
    activity.record("out", "model.chat", [f"space:{other}"], db=db, model="gpt", cost_usd=2.0, tokens_in=10)

    m = client.get("/api/v1/budgets/meter", headers=h, params={"ns": "pods"}).json()
    assert (m["resource"], m["period"], m["spent"]["usd"], m["spent"]["tokens"], m["calls"]) == (f"space:{sid}", "month", 0.3, 170, 2)
    assert m["budget"] is None and m["by_day"] is None

    budgets.put(db, f"space:{sid}", usd=0.5, period="week")
    m = client.get("/api/v1/budgets/meter", headers=h, params={"ns": "pods", "detail": True}).json()
    assert m["period"] == "week" and m["budget"]["state"] == "ok" and m["budget"]["left"]["usd"] == 0.2
    assert [x["model"] for x in m["by_model"]] == ["gpt", "local"] and len(m["by_day"]) == 1
    assert m["top"] == [{"resource": f"routine:{rid}", "cost_usd": 0.3, "tokens": 120, "calls": 1, "name": "Digest"}]
    assert client.get("/api/v1/budgets/meter", headers=h, params={"ns": "pods", "period": "day"}).json()["period"] == "day"

    # all of Lens: admins, against the one budget for everything
    m = client.get("/api/v1/budgets/meter", headers=h, params={"detail": True}).json()
    assert m["resource"] is None and m["spent"]["usd"] == 2.3 and m["budget"] is None
    assert [x["resource"] for x in m["by_namespace"]] == [f"space:{other}", f"space:{sid}"]
    assert m["by_namespace"][0]["name"] == "news"

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    e = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/budgets/meter", headers=e, params={"ns": "pods"}).json()["spent"]["usd"] == 0.3
    assert client.get("/api/v1/budgets/meter", headers=e).status_code == 403
    assert client.get("/api/v1/budgets/meter", headers=e, params={"ns": "news"}).status_code == 404
