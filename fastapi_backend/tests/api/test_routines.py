"""Routines: schedules, running what is due once, pipeline and workflow actions on recordings, and the graph-organising
workflow proposing, making and undoing merges and links."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import analyze, entities, ingest, organize, routines, schedule, store, workflows
from tests import fake_llm
from tests.helpers import login, make_user, quiet, seed

R = store.R
UTC = dt.UTC
EXTRA = (
    "Alice|N|We met the Northwind Labs team and the North Wind Labs lawyers.\n"
    "Bob|N|Amazon Web Services hosts it; AWS bills monthly.\nAlice|N|Northwind Labs again, with Dyno Therapeutics."
)


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(db, cfg, folder, client):
    seed(db, cfg, folder)
    p = folder / "extra.txt"
    p.write_text(EXTRA)
    ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"admin": login(client, "root@x.io", "root password 1")}


def eid(db, name, ns="pods"):
    return db.one("SELECT record::id(id) AS id FROM entity WHERE ekey = $k", k=f"{store.ns_id(db, ns)}:{analyze.ent_key(name)}")["id"]


def t(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_schedules():
    assert schedule.next_after("0 3 * * *", "UTC", t("2026-10-02T12:00:00")) == t("2026-10-03T03:00:00")
    assert schedule.next_after("@hourly", "UTC", t("2026-10-02T12:00:00")) == t("2026-10-02T13:00:00")
    assert schedule.next_after("*/15 * * * *", "UTC", t("2026-10-02T12:07:30")) == t("2026-10-02T12:15:00")
    assert schedule.next_after("30 9 * * mon-fri", "UTC", t("2026-10-02T10:00:00")) == t("2026-10-05T09:30:00")  # Fri → Mon
    assert schedule.next_after("0 0 29 feb *", "UTC", t("2026-10-02T00:00:00")) == t("2028-02-29T00:00:00")
    # 03:00 in Stockholm is 01:00 UTC in summer and 02:00 in winter
    assert schedule.next_after("0 3 * * *", "Europe/Stockholm", t("2026-10-02T12:00:00")) == t("2026-10-03T01:00:00")
    assert schedule.next_after("0 3 * * *", "Europe/Stockholm", t("2026-12-02T12:00:00")) == t("2026-12-03T02:00:00")
    # 02:30 doesn't happen on the spring DST night: it runs when the clock gets past the gap (03:00 = 01:00 UTC)
    assert schedule.next_after("30 2 * * *", "Europe/Stockholm", t("2027-03-27T12:00:00")) == t("2027-03-28T01:00:00")
    # the hour New York repeats in November: every-15-minutes keeps going through both, never into the past
    assert schedule.next_after("*/15 * * * *", "America/New_York", t("2026-11-01T06:10:00")) == t("2026-11-01T06:15:00")
    assert schedule.next_after("*/15 * * * *", "America/New_York", t("2026-11-01T05:50:00")) == t("2026-11-01T06:00:00")
    # a daily time in the repeated hour runs once
    assert schedule.next_after("30 1 * * *", "America/New_York", t("2026-11-01T05:31:00")) == t("2026-11-02T06:30:00")
    assert schedule.next_after("0 9 * * mon-sun", "UTC", t("2026-10-04T10:00:00")) == t("2026-10-05T09:00:00")
    assert schedule.describe("0 3 * * *") == "every day at 03:00"
    for bad, msg in (("0 3 * *", "five fields"), ("61 * * * *", "outside"), ("* * * * fun", "isn't a number"), ("0 0 31 feb *", "never")):
        with pytest.raises(ValueError, match=msg):
            schedule.check(bad)
    with pytest.raises(ValueError, match="time zone"):
        schedule.check("@daily", "Mars/Olympus")


def test_routines_are_checked_and_run_when_due(client, db, cfg, env):
    h = env["admin"]
    pods = store.ns_id(db, "pods")
    ed = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/routines", headers=ed).status_code == 403

    def problem(**body):
        r = client.post("/api/v1/routines", headers=h, json={"name": "x", **body})
        assert r.status_code == 400, r.text
        return r.json()["detail"]

    assert "at least one thing" in problem(actions=[])
    assert "type is one of" in problem(actions=[{"type": "nap"}])
    assert "five fields" in problem(actions=[{"type": "sync"}], schedule="daily")
    assert "no namespace" in problem(actions=[{"type": "sync"}], namespaces=[999])
    assert "choose a workflow" in problem(actions=[{"type": "workflow", "workflow": 999}])
    assert "no setting colour" in problem(actions=[{"type": "pipeline", "colour": 1}])
    assert "recordings is one of" in problem(actions=[{"type": "pipeline", "recordings": "some"}])
    prev = client.get("/api/v1/routines/schedule", params={"schedule": "0 3 * * *", "timezone": "UTC"}, headers=h).json()
    assert prev["text"] == "every day at 03:00" and len(prev["next"]) == 5

    r = client.post(
        "/api/v1/routines",
        headers=h,
        json={
            "name": "Re-analyse pods",
            "schedule": "*/5 * * * *",
            "namespaces": [pods],
            "actions": [{"type": "pipeline", "steps": ["analyze"], "recordings": "all"}],
        },
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    got = client.get(f"/api/v1/routines/{rid}", headers=h).json()
    assert got["schedule_text"] == "every 5 minutes" and got["namespace_names"] == ["pods"] and got["next_run_at"]

    # not due yet; then due: it runs once, even when two schedulers look at the same moment
    assert routines.run_due(db, cfg, log=None) == 0
    later = t(got["next_run_at"][:19]) + dt.timedelta(seconds=1)
    assert routines.run_due(db, cfg, log=None, now=later) == 1
    assert routines.run_due(db, cfg, log=None, now=later) == 0
    after = client.get(f"/api/v1/routines/{rid}", headers=h).json()
    assert after["last_status"] == "done" and after["next_run_at"] > got["next_run_at"] and not after["running"]
    run = client.get(f"/api/v1/routines/{rid}/runs", headers=h).json()[0]
    assert run["trigger"] == "schedule" and run["results"][0]["result"]["queued"] == 3  # the three pods recordings
    queued = db.rows("SELECT steps, created_by FROM job WHERE space = $s AND status = 'queued'", s=pods)
    assert len(queued) == 3 and all(j["created_by"] == f"routine:{rid}" and j["steps"][0]["type"] == "analyze" for j in queued)

    # by hand, even when off; "new" takes only recordings made since the last run
    assert client.patch(f"/api/v1/routines/{rid}", headers=h, json={"enabled": False, "actions": [{"type": "pipeline"}]}).status_code == 200
    assert routines.run_due(db, cfg, log=None, now=later + dt.timedelta(hours=1)) == 0
    assert client.post(f"/api/v1/routines/{rid}/run", headers=h, json={}).status_code == 200
    assert routines.run_due(db, cfg, log=None) == 1
    run = client.get(f"/api/v1/routines/{rid}/runs", headers=h).json()[0]
    assert run["trigger"] == "manual" and run["by"] == "root@x.io" and run["results"][0]["result"]["recordings"] == 0
    # no schedule: by hand only
    assert client.patch(f"/api/v1/routines/{rid}", headers=h, json={"schedule": None, "enabled": True}).status_code == 200
    got = client.get(f"/api/v1/routines/{rid}", headers=h).json()
    assert got["schedule"] is None and got["next_run_at"] is None and got["schedule_text"] == "only when run by hand"
    assert client.delete(f"/api/v1/routines/{rid}", headers=h).status_code == 200
    assert client.get(f"/api/v1/routines/{rid}", headers=h).status_code == 404


def test_workflow_actions_on_recordings(client, db, cfg, env):
    h = env["admin"]
    wid = workflows.create(
        db,
        "People",
        {
            "nodes": [{"id": "i", "type": "input", "config": {}}, {"id": "o", "type": "output", "config": {"key": "who"}}],
            "edges": [{"source": "i", "target": "o"}],
        },
    )
    r = client.post(
        "/api/v1/routines",
        headers=h,
        json={"name": "People everywhere", "actions": [{"type": "workflow", "workflow": wid, "recordings": "all"}]},
    )
    rid = r.json()["id"]
    run_id = routines.run(db, cfg, rid, by="test")
    res = routines.get_run(db, run_id)["results"][0]
    assert res["status"] == "done" and res["result"]["queued"] == 4  # every namespace: three in pods, one in calls
    steps = db.values("SELECT VALUE steps FROM job WHERE created_by = $b", b=f"routine:{rid}")
    assert all(s[-1] == {"type": "workflow", "workflow": wid, "version": 1} for s in steps)
    bad = client.post(
        "/api/v1/routines", headers=h, json={"name": "x", "actions": [{"type": "workflow", "workflow": wid, "propose_only": True}]}
    )
    assert "graph" in bad.json()["detail"]


def test_graph_workflows_organise_entities(client, new_client, db, cfg, env, folder):
    h = env["admin"]
    routines.seed(db)
    routines.seed(db)  # once only
    cat = client.get("/api/v1/routines", headers=h).json()
    assert [r["name"] for r in cat["routines"]] == ["Organise the graph every night"] and not cat["routines"][0]["enabled"]
    wf = client.get("/api/v1/workflows", headers=h).json()
    graph_wf = [w for w in wf["workflows"] if w["scope"] == "graph"][0]
    assert graph_wf["name"] == organize.DEFAULT_NAME
    assert {n["type"]: n["scopes"] for n in wf["node_types"]}["apply_changes"] == ["graph"]

    # graph nodes only in graph workflows; a graph workflow isn't for pipelines or for one recording
    r = client.post(
        "/api/v1/workflows",
        headers=h,
        json={
            "name": "x",
            "graph": {
                "nodes": [{"id": "i", "type": "input", "config": {}}, {"id": "a", "type": "apply_changes", "config": {}}],
                "edges": [{"source": "i", "target": "a"}],
            },
        },
    )
    assert r.status_code == 400 and "node types are" in r.json()["detail"]
    r = client.post("/api/v1/pipelines", headers=h, json={"name": "p", "steps": [{"type": "workflow", "workflow": graph_wf["id"]}]})
    assert r.status_code == 400 and "organises the graph" in r.json()["detail"]
    rec = db.values("SELECT VALUE record::id(id) FROM recording LIMIT 1")[0]
    assert client.post(f"/api/v1/workflows/{graph_wf['id']}/run", headers=h, json={"recording": rec}).status_code == 400
    bad = {
        "name": "y",
        "scope": "graph",
        "graph": {
            "nodes": [{"id": "i", "type": "input", "config": {}}, {"id": "a", "type": "apply_changes", "config": {"apply_above": 2}}],
            "edges": [{"source": "i", "target": "a"}],
        },
    }
    assert "between 0 and 1" in client.post("/api/v1/workflows", headers=h, json=bad).json()["detail"]

    # calls shares its graph too, and names a lab much like one in pods: a link across namespaces
    db.q("UPDATE space SET graph = 'shared'")
    p = folder / "lab.txt"
    p.write_text("Carol|N|The Northwind Lab people called back.\nDan|N|Fine.")
    ingest.import_transcript(db, cfg, "calls", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)

    rid = cat["routines"][0]["id"]
    assert client.post(f"/api/v1/routines/{rid}/run", headers=h, json={}).status_code == 200
    assert routines.run_due(db, cfg, log=None) == 1
    run = client.get(f"/api/v1/routines/{rid}/runs", headers=h).json()[0]
    assert run["status"] == "done", run
    res = run["results"][0]["result"]
    assert res["applied"] == 1 and res["proposed"] >= 1 and res["skipped"] == 0, res
    full = client.get(f"/api/v1/routine-runs/{run['id']}", headers=h).json()
    assert any("judged" in line for line in full["log"])

    changes = client.get("/api/v1/graph-changes", params={"run": run["id"]}, headers=h).json()
    applied = [c for c in changes if c["status"] == "applied"]
    proposed = [c for c in changes if c["status"] == "proposed"]
    assert len(applied) == 1 and applied[0]["verdict"]["confidence"] == 0.97
    first = applied[0]
    gone = first["b"]["id"] if first.get("keep") == first["a"]["id"] else first["a"]["id"]
    if first["kind"] == "merge":
        assert not db.one("SELECT id FROM $r", r=R("entity", gone))  # merged away
    kinds = {c["kind"] for c in changes}
    assert kinds == {"merge", "link"}, changes

    # a second run proposes nothing it already proposed
    routines.request_run(db, rid, "test")
    routines.run_due(db, cfg, log=None)
    again = client.get(f"/api/v1/routines/{rid}/runs", headers=h).json()[0]["results"][0]["result"]
    assert again["proposed"] == 0, again

    # an editor of pods sees and decides the changes in pods; ones touching calls aren't theirs
    ce = new_client()
    he = login(ce, "ed@x.io", "editor password 1")
    mine = ce.get("/api/v1/graph-changes", params={"status": "proposed"}, headers=he).json()
    assert mine and all(c["spaces"] == [store.ns_id(db, "pods")] for c in mine)
    link = [c for c in proposed if c["kind"] == "link"]
    if link:
        assert ce.post(f"/api/v1/graph-changes/{link[0]['id']}/accept", headers=he, json={}).status_code == 404
    pick = mine[0]
    assert ce.post(f"/api/v1/graph-changes/{pick['id']}/accept", headers=he, json={"keep": 1}).status_code == 400
    assert ce.post(f"/api/v1/graph-changes/{pick['id']}/accept", headers=he, json={}).status_code == 200
    assert ce.post(f"/api/v1/graph-changes/{pick['id']}/accept", headers=he, json={}).status_code == 400  # applied already
    assert ce.post(f"/api/v1/graph-changes/{pick['id']}/undo", headers=he).status_code == 200
    a, b = pick["a"]["id"], pick["b"]["id"]
    assert db.one("SELECT id FROM $r", r=R("entity", a)) and db.one("SELECT id FROM $r", r=R("entity", b))
    if len(mine) > 1:
        assert ce.post(f"/api/v1/graph-changes/{mine[1]['id']}/dismiss", headers=he).status_code == 200
        lo, hi = sorted((mine[1]["a"]["id"], mine[1]["b"]["id"]))
        assert db.one("SELECT id FROM $r", r=R("entity_distinct", f"{lo}-{hi}"))

    # the whole first run, taken back
    assert client.post(f"/api/v1/routine-runs/{run['id']}/undo", headers=h).json() == {"undone": 1, "failed": 0}
    assert client.get(f"/api/v1/routine-runs/{run['id']}", headers=h).json()["changes"]["applied"] == 0  # counted live
    assert db.one("SELECT id FROM $r", r=R("entity", gone))
    assert client.get("/api/v1/graph-changes", params={"run": run["id"], "status": "applied"}, headers=h).json() == []


def test_propose_only(db, cfg, env):
    routines.seed(db)
    rid = routines.list_routines(db)[0]["id"]
    before = len(db.values("SELECT VALUE id FROM entity"))
    routines.request_run(db, rid, "test", propose_only=True)
    assert routines.run_due(db, cfg, log=None) == 1
    res = routines.get_run(db, routines.get(db, rid)["last_run"])["results"][0]["result"]
    assert res["applied"] == 0 and res["proposed"] >= 1
    assert len(db.values("SELECT VALUE id FROM entity")) == before
    north = [eid(db, "Northwind Labs"), eid(db, "North Wind Labs")]
    assert all(entities._entity(db, x) for x in north)


def test_new_recordings_advance_only_as_far_as_queued(db, cfg, env):
    pods = store.ns_id(db, "pods")
    rid = routines.create(db, "Small batches", [{"type": "pipeline", "steps": ["analyze"], "limit": 1}], namespaces=[pods])
    db.q("UPDATE $r SET seen_recording = 0", r=R("routine", rid))  # as if made before the recordings came
    taken = []
    for _ in range(4):
        db.q("UPDATE job SET status = 'done'")
        run_id = routines.run(db, cfg, rid)
        taken.append(routines.get_run(db, run_id)["results"][0]["result"]["queued"])
    assert taken == [1, 1, 1, 0]  # three pods recordings, one a run, none skipped

    # an action that fails doesn't move the mark on
    db.q("UPDATE $r SET seen_recording = 0, actions = $a", r=R("routine", rid), a=[{"type": "workflow", "workflow": 999}])
    routines.run(db, cfg, rid)
    assert routines.get(db, rid)["seen_recording"] == 0


def test_dead_runs_are_swept(db, cfg, env):
    rid = routines.create(db, "x", [{"type": "sync"}])
    now = dt.datetime.now(UTC)
    old = (now - dt.timedelta(hours=5)).isoformat(timespec="seconds")
    db.q("UPDATE $r SET running_since = $t, heartbeat_at = $t", r=R("routine", rid), t=old)
    db.q("CREATE routine_run:900 CONTENT $d", d={"routine": rid, "status": "running", "started_at": old})
    assert routines.sweep(db, now) == 1
    assert routines.get_run(db, 900)["status"] == "error" and not routines.get(db, rid)["running"]


def test_one_failed_batch_or_undone_merge_doesnt_stop_the_rest(db, cfg, env, llm):
    routines.seed(db)
    rid = routines.list_routines(db)[0]["id"]
    calls = {"n": 0}
    real = organize.llm.json_out

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise organize.llm.LLMError("500 the server fell over")
        return real(*a, **k)

    wid = routines.get(db, rid)["actions"][0]["workflow"]
    g = workflows.get(db, wid)["graph"]
    for n in g["nodes"]:
        if n["type"] == "llm_judge":
            n["config"]["batch"] = 1
    workflows.save_version(db, wid, g)
    organize.llm.json_out = flaky
    try:
        run_id = routines.run(db, cfg, rid)
    finally:
        organize.llm.json_out = real
    got = routines.get_run(db, run_id)
    assert got["status"] == "done" and any("the model failed" in line for line in got["log"])
    applied = db.rows("SELECT record::id(id) AS id, merge FROM graph_change WHERE run = $r AND status = 'applied'", r=run_id)
    assert applied
    for a in applied:
        if a.get("merge"):
            entities.undo_merge(db, a["merge"])  # undone from the entity page first
    assert organize.undo_run(db, run_id) == (len(applied), 0)


def test_scheduling_threads_scan_folders_and_run_routines(db, cfg, monkeypatch):
    """What the API's background work and `lens worker` start: both rounds, until told to stop."""
    import threading

    from app.domain import sources

    calls = {"watched folders": threading.Event(), "routines": threading.Event()}
    monkeypatch.setattr(routines, "MIN_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(routines, "CHECK_SECONDS", 0.01)
    monkeypatch.setattr(sources, "poll_due", lambda db, cfg, log=None: calls["watched folders"].set())
    monkeypatch.setattr(routines, "run_due", lambda db, cfg, log=None: calls["routines"].set())
    stop = threading.Event()
    threads = routines.start(db, lambda: {**cfg, "sources": {**cfg["sources"], "check_seconds": 0}}, stop)
    try:
        assert all(e.wait(5) for e in calls.values())
    finally:
        stop.set()
        for t in threads:
            t.join(5)
    assert not any(t.is_alive() for t in threads)
