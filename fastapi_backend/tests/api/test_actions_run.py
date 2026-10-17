"""Automation actions that run Lens's own work (docs/automations.md, Actions): run_workflow and run_pipeline queue a
job on an asset by the automation, with its person's permission to process it, at once on the asset the activity is
about and asking first otherwise; run_routine asks the scheduler for a routine's run, an admin's to ask for."""

from __future__ import annotations

import pytest

from app.domain import actions, budgets, pipelines, routines, store, workflows
from tests import fake_llm
from tests.api._assist import start_llm
from tests.api.test_budgets import spend
from tests.helpers import make_user, seed

GRAPH = {
    "nodes": [{"id": "i", "type": "input", "config": {}}, {"id": "o", "type": "output", "config": {"key": "who"}}],
    "edges": [{"source": "i", "target": "o"}],
}


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    srv = start_llm(cfg)
    yield create_app(cfg, db, background=False)
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def env(app, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    return {
        "a": a,
        "call": call,
        "me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}),
        "viewer": make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"}),
        "root": make_user(db, "root@x.io", "root password 1", admin=True),
        "pods": store.ns_id(db, "pods"),
        "workflow": workflows.create(db, "People", GRAPH),
        "pipeline": pipelines.create(db, "Just analyze", ["analyze"]),
    }


def run(db, cfg, env, action, params, dry=False, **kw):
    intent = {
        "key": "51-n5",
        "action": action,
        "params": params,
        "effect": "lens",
        "risk": "routine",
        "untrusted": False,
        "spaces": [env["pods"]],
        "run_as": env["me"],
        "node": "n5",
        "automation": 4,
        "run": 51,
        **kw,
    }
    return actions.perform(db, cfg, intent, None, dry)


def ctx(env, rid):
    return {
        "event": {"object": {"id": f"lens:recording/{rid}"}, "lens": {"spaces": [env["pods"]]}},
        "automation": {"spaces": [env["pods"]]},
    }


def test_on_the_asset_the_activity_is_about_they_run_at_once_else_they_ask(db, cfg, env):
    wf, pl, rt = (actions.get(n) for n in ("run_workflow", "run_pipeline", "run_routine"))
    p = actions.check_params(wf, {"workflow": 1})
    assert p["asset"] == "{{ event.object.id }}" and actions.effect_of(wf, p) == "lens"
    mine = ctx(env, env["a"])
    assert actions.resolve_risk(wf, {"workflow": 1, "asset": f"lens:recording/{env['a']}"}, mine) == "routine"
    assert actions.resolve_risk(pl, {"asset": env["a"]}, mine) == "routine"  # an id names the same asset
    assert actions.resolve_risk(wf, {"workflow": 1, "asset": env["call"]}, mine) == "ask"
    assert actions.resolve_risk(rt, {"routine": 1}, mine, {"n5": "auto"}) == "ask"
    assert actions.describe("run_workflow", p) == "Run workflow 1 on the asset it's about"
    assert actions.describe("run_pipeline", {"pipeline": 3, "asset": 9}) == "Run pipeline 3 on asset 9"


def test_run_workflow_queues_it_on_the_asset_as_the_automation(db, cfg, env):
    rid, wid = env["a"], env["workflow"]
    got = run(db, cfg, env, "run_workflow", {"workflow": wid, "asset": f"lens:recording/{rid}"}, dry=True)
    assert got["job"] is None and got["would"].startswith("run the workflow People on ") and got["version"] == 1
    assert not db.values("SELECT VALUE id FROM job WHERE created_by = 'automation: Automation 4'")
    got = run(db, cfg, env, "run_workflow", {"workflow": wid, "asset": f"lens:recording/{rid}"})
    job = db.one("SELECT recording, steps, created_by FROM $r", r=store.R("job", got["job"]))
    assert got == {"workflow": wid, "version": 1, "asset": rid, "job": got["job"]}
    assert job["recording"] == rid and job["steps"][-1] == {"type": "workflow", "workflow": wid, "version": 1}
    assert job["created_by"] == "automation: Automation 4"
    for params, said in (
        ({"workflow": 999, "asset": rid}, "there's no workflow 999"),
        ({"workflow": wid, "asset": "lens:email/3"}, "isn't an asset"),
        ({"workflow": wid, "asset": 99999}, "there's no asset 99999"),
        ({"workflow": wid, "asset": env["call"]}, "isn't in this automation's namespaces"),
    ):
        with pytest.raises(ValueError, match=said):
            run(db, cfg, env, "run_workflow", params, dry=True)
    with pytest.raises(ValueError, match="vi can't process assets in that namespace"):
        run(db, cfg, env, "run_workflow", {"workflow": wid, "asset": rid}, run_as=env["viewer"])


def test_run_pipeline_processes_the_asset_again(db, cfg, env):
    rid, pid = env["a"], env["pipeline"]
    assert run(db, cfg, env, "run_pipeline", {"pipeline": pid, "asset": rid}, dry=True)["would"].startswith("run the pipeline Just analyze")
    assert run(db, cfg, env, "run_pipeline", {"asset": rid}, dry=True)["would"].startswith("run its namespace's pipeline")
    got = run(db, cfg, env, "run_pipeline", {"pipeline": pid, "asset": str(rid)})
    assert got["job"] and got["asset"] == rid and got["pipeline"] == pid
    assert db.one("SELECT created_by FROM $r", r=store.R("job", got["job"]))["created_by"] == "automation: Automation 4"
    with pytest.raises(ValueError, match="there's no pipeline 999"):
        run(db, cfg, env, "run_pipeline", {"pipeline": 999, "asset": rid})


def test_run_routine_asks_the_scheduler_for_an_admins_routine_in_the_automations_namespaces(db, cfg, env):
    rid = routines.create(db, "Small batches", [{"type": "pipeline", "steps": ["analyze"], "limit": 1}], namespaces=[env["pods"]])
    with pytest.raises(ValueError, match="routines are run by admins, and me isn't one"):
        run(db, cfg, env, "run_routine", {"routine": rid})
    got = run(db, cfg, env, "run_routine", {"routine": rid, "propose_only": True}, dry=True, run_as=env["root"])
    assert got == {
        "routine": rid,
        "propose_only": True,
        "requested": False,
        "would": "run the routine Small batches, proposing its changes",
    }
    assert not routines.get(db, rid).get("run_now")
    assert run(db, cfg, env, "run_routine", {"routine": rid}, run_as=env["root"])["requested"]
    assert routines.get(db, rid)["run_now"]["by"] == "automation: Automation 4"
    everywhere = routines.create(db, "All", [{"type": "pipeline", "steps": ["analyze"], "limit": 1}])
    with pytest.raises(ValueError, match="works in namespaces outside this automation's"):
        run(db, cfg, env, "run_routine", {"routine": everywhere}, run_as=env["root"])
    with pytest.raises(ValueError, match="there's no routine 999"):
        run(db, cfg, env, "run_routine", {"routine": 999}, run_as=env["root"])
    # over one of its budgets, it doesn't ask for the run
    other = routines.create(db, "Other", [{"type": "pipeline", "steps": ["analyze"], "limit": 1}], namespaces=[env["pods"]])
    budgets.put(db, f"routine:{other}", usd=0.5)
    spend(db, f"routine:{other}", usd=0.6)
    with pytest.raises(ValueError, match="budget is used up"):
        run(db, cfg, env, "run_routine", {"routine": other}, run_as=env["root"])
    assert not routines.get(db, other).get("run_now")
