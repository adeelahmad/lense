"""The activity ledger: every changing request and every call out, with its cost, counted for each resource it touched."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import activity, routines, store
from tests import fake_llm
from tests.helpers import drain, login, make_user, seed


@pytest.fixture
def model(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    cfg["telemetry"]["prices"] = {"fake": {"input": 1.0, "output": 2.0}}  # USD per million tokens
    cfg["ai"]["tools"] = False  # chats get the plain, streamed answer
    fake_llm.Handler.usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    yield fake_llm.Handler
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def app(cfg, db, model):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def uid(db, email):
    return db.one("SELECT record::id(id) AS id FROM account WHERE email = $e", e=email)["id"]


def rows(db, resource, kind=None):
    return [r for r in activity.history(db, resource, 500) if kind is None or r["kind"] == kind]


def test_a_routines_calls_count_for_it_its_run_and_the_jobs_it_queued(client, db, cfg, folder, h):
    seed(db, cfg, folder)
    rid = client.post(
        "/api/v1/routines",
        headers=h,
        json={"name": "Summaries", "actions": [{"type": "pipeline", "steps": ["summarize"], "recordings": "all"}]},
    ).json()["id"]
    run_id = routines.run(db, cfg, rid, by="test")
    assert drain(db, cfg) == 3

    calls = rows(db, f"routine:{rid}", "out")
    assert len(calls) == 3 and {c["action"] for c in calls} == {"model.chat"}
    for c in calls:
        assert (c["model"], c["tokens_in"], c["tokens_out"], c["ok"]) == ("fake", 1000, 500, True)
        assert c["cost_usd"] == pytest.approx(0.002)
        job = next(r for r in c["resources"] if r.startswith("job:"))
        assert {f"routine:{rid}", "pipeline:standard"} - set(c["resources"]) <= {"pipeline:standard"}
        assert any(r.startswith("recording:") for r in c["resources"]) and any(r.startswith("space:") for r in c["resources"])
        # each job's own total, on the job and as its run row
        jid = int(job.split(":")[1])
        assert db.one("SELECT cost_usd, tokens FROM $j", j=store.R("job", jid)) == {"cost_usd": pytest.approx(0.002), "tokens": 1500}
        assert [r["action"] for r in rows(db, job, "run")] == ["job.succeeded"]

    # the routine's history: the request that made it, its run, and the audit entry
    hist = rows(db, f"routine:{rid}")
    assert {"in", "out", "run", "change"} <= {r["kind"] for r in hist}
    assert any(r["kind"] == "change" and r["action"] == "routine.create" for r in hist)
    assert db.one("SELECT cost_usd FROM $r", r=store.R("routine_run", run_id))["cost_usd"] is None  # queued, not run, in the run
    assert [r["action"] for r in rows(db, f"routine_run:{run_id}", "run")] == ["routine.done"]

    t = client.get("/api/v1/activity/totals", headers=h, params={"resource": f"routine:{rid}", "period": "day"}).json()
    assert (t["tokens_in"], t["tokens_out"]) == (3000, 1500) and t["cost_usd"] == pytest.approx(0.006)
    assert t["by_kind"]["out"]["calls"] == 3 and t["by_kind"]["run"]["calls"] >= 4  # three jobs and the routine run
    top = client.get("/api/v1/activity/top", headers=h, params={"table": "routine"}).json()["resources"]
    assert top[0]["resource"] == f"routine:{rid}" and top[0]["cost_usd"] == pytest.approx(0.006) and top[0]["tokens"] == 4500
    everything = client.get("/api/v1/activity/totals", headers=h).json()
    assert everything["cost_usd"] == pytest.approx(0.006)


def test_changing_requests_are_logged_with_their_resources_and_caller(client, db, h):
    rid = client.post("/api/v1/routines", headers=h, json={"name": "Nightly", "actions": [{"type": "sync"}]}).json()["id"]
    assert client.patch(f"/api/v1/routines/{rid}", headers=h, json={"name": "Nightly sync"}).status_code == 200
    assert client.get(f"/api/v1/routines/{rid}", headers=h).status_code == 200  # reads aren't logged
    assert client.patch("/api/v1/routines/999", headers=h, json={"name": "x"}).status_code == 404
    me = uid(db, "root@x.io")

    got = rows(db, f"routine:{rid}", "in")
    assert [r["action"] for r in got] == ["PATCH /api/v1/routines/{rid}", "POST /api/v1/routines"]  # made, then changed
    assert got[0]["actor"] == me and f"account:{me}" in got[0]["resources"] and got[0]["detail"] == {"status": 200}
    failed = rows(db, "routine:999", "in")
    assert (failed[0]["ok"], failed[0]["error"]) == (False, "404")
    mine = [r["action"] for r in rows(db, f"account:{me}", "in")]
    assert "POST /api/v1/routines" in mine
    assert not any("/auth/" in a for a in mine)  # signing in is in the audit log, never with its body

    # reads too, once asked
    assert client.put("/api/v1/settings/activity", headers=h, json={"reads": True}).status_code == 200
    client.get(f"/api/v1/routines/{rid}", headers=h)
    assert "GET /api/v1/routines/{rid}" in [r["action"] for r in rows(db, f"routine:{rid}", "in")]

    # off: nothing more is written
    assert client.put("/api/v1/settings/activity", headers=h, json={"enabled": False, "reads": False}).status_code == 200
    ins, changes = len(rows(db, f"routine:{rid}", "in")), len(rows(db, f"routine:{rid}", "change"))
    client.patch(f"/api/v1/routines/{rid}", headers=h, json={"name": "again"})
    assert len(rows(db, f"routine:{rid}", "in")) == ins and len(rows(db, f"routine:{rid}", "change")) == changes + 1  # audit only


def test_chats_and_people_see_only_their_own(client, db, cfg, folder, h, model):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    e = login(client, "ed@x.io", "editor password 1")
    cid = client.post("/api/v1/chats", headers=e, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    assert client.post(f"/api/v1/chats/{cid}/messages", headers=e, json={"content": "What was it about?"}).status_code == 200
    me = uid(db, "ed@x.io")

    got = client.get("/api/v1/activity", headers=e, params={"resource": f"chat:{cid}", "kind": "out"}).json()
    assert [r["action"] for r in got] == ["model.chat"] and f"account:{me}" in got[0]["resources"]
    assert got[0]["cost_usd"] == pytest.approx(0.002), [(r["tokens_in"], r["model"]) for r in got]
    assert client.get("/api/v1/activity", headers=e, params={"resource": f"account:{me}"}).status_code == 200
    pods = store.ns_id(db, "pods")
    assert client.get("/api/v1/activity", headers=e, params={"resource": f"space:{pods}"}).status_code == 200
    for other in ("routine:1", "account:1", f"space:{store.ns_id(db, 'calls')}"):
        assert client.get("/api/v1/activity", headers=e, params={"resource": other}).status_code in (403, 404), other
    assert client.get("/api/v1/activity", headers=e, params={"resource": "not a resource"}).status_code == 422
    assert client.get("/api/v1/activity/totals", headers=e).status_code == 403
    assert client.get("/api/v1/activity/top", headers=e).status_code == 403
    # an admin sees anyone's
    assert client.get("/api/v1/activity", headers=h, params={"resource": f"chat:{cid}"}).status_code == 200


def test_old_rows_go_after_keep_days(db, cfg):
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=400)).isoformat(timespec="seconds")
    db.q("CREATE activity CONTENT $d", d={"at": old, "kind": "out", "action": "model.chat", "resources": ["routine:1"]})
    activity.record("out", "model.chat", ["routine:1"], cfg, db)
    activity.tidy(db, cfg, now=dt.datetime.now(dt.UTC))
    assert [r["at"] > old for r in rows(db, "routine:1")] == [True]


def test_scopes_nest_and_writing_never_breaks_the_work(db, cfg):
    with activity.scope(db, "routine:1", cfg=cfg):
        with activity.scope(None, "job:2", None, "not a ref"):
            assert activity.current() == ["routine:1", "job:2"]
            activity.record("out", "x")
        assert activity.current() == ["routine:1"]
    assert rows(db, "job:2")[0]["resources"] == ["routine:1", "job:2"]

    class Broken:
        def values(self, *a, **k):
            raise RuntimeError("down")

    assert activity.record("out", "x", db=Broken()) is None


def test_costs_for_lists_say_when_they_are_estimates(client, db, cfg, h):
    with activity.scope(db, "routine:1", cfg=cfg):
        activity.record("out", "model.chat", model="fake", tokens_in=10, cost_usd=0.5)
    with activity.scope(db, "routine:2", cfg=cfg):
        activity.record("out", "model.chat", model="local", tokens_in=10)  # not costed: not an estimate
        activity.record("out", "model.chat", model="fake", cost_usd=0.25)
        activity.record("out", "model.chat", model="fake", unpriced=True)  # priced, but no token counts came back
        activity.record("out", "notify.webhook")  # costs nothing: not unpriced
    got = client.get(
        "/api/v1/activity/costs", headers=h, params=[("resource", "routine:1"), ("resource", "routine:2"), ("resource", "routine:3")]
    )
    c = got.json()["costs"]
    assert (c["routine:1"]["cost_usd"], c["routine:1"]["estimate"]) == (0.5, False)
    assert (c["routine:2"]["cost_usd"], c["routine:2"]["unpriced"], c["routine:2"]["estimate"]) == (0.25, 1, True)
    assert c["routine:3"] == {"cost_usd": 0, "tokens": 0, "calls": 0, "unpriced": 0, "estimate": False}
    t = client.get("/api/v1/activity/totals", headers=h, params={"resource": "routine:2"}).json()
    assert (t["unpriced"], t["estimate"]) == (1, True)
    make_user(db, "v@x.io", "viewer password 1")
    v = login(client, "v@x.io", "viewer password 1")
    assert client.get("/api/v1/activity/costs", headers=v, params={"resource": "routine:1"}).json()["costs"] == {}


def test_models_are_costed_by_tokens_by_time_or_not_at_all(client, cfg, h):
    cfg["telemetry"]["prices"] = {
        "cloud": {"input": 3.0, "output": 15.0},
        "gpu-box": {"unit": "time", "per_hour": 0.6},
        "off": {"unit": "off"},
    }
    price = activity.model_cost(cfg)
    assert price("cloud", 1_000_000, 100_000, 50) == (pytest.approx(4.5), False)
    assert price("cloud", None, None, 50) == (None, True)  # priced, but the server didn't count
    assert price("gpu-box", 10, 10, 60_000) == (pytest.approx(0.01), False)  # a minute at 0.60/hour
    assert price("off", 10, 10, 60_000) == (None, False)
    assert price("unlisted-local", 10, 10, 60_000) == (None, False)  # local models aren't costed by default

    put = lambda v: client.put("/api/v1/settings/telemetry", headers=h, json={"prices": v})  # noqa: E731
    assert put({"m": {"unit": "time", "per_hour": 1.5}, "x": {"unit": "off"}, "t": {"input": 1}}).status_code == 200
    got = client.get("/api/v1/settings", headers=h).json()["telemetry"]["values"]["prices"]
    assert {k: got[k] for k in "mxt"} == {"m": {"unit": "time", "per_hour": 1.5}, "x": {"unit": "off"}, "t": {"input": 1.0, "output": 0.0}}
    for bad in ({"m": {"unit": "time", "input": 1}}, {"m": {"unit": "watts"}}, {"m": {"unit": "off", "per_hour": 1}}):
        assert put(bad).status_code == 400, bad
