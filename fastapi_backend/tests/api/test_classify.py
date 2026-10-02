"""The classify step (docs/processing.md#classify): a decision model says which of the namespace's tags, which content
type and which collection fit a new resource; what it's sure of is applied, what's likely waits for an editor."""

from __future__ import annotations

import pytest

from app.domain import classify, decide, hierarchy, ingest, jobs, library
from tests import fake_decide
from tests.helpers import login, make_user

BUDGET = """Ana|N|The budget for the harbour office was approved by the finance board.
Ben|N|Finance wants the invoices and the budget report before the end of the quarter."""


@pytest.fixture
def env(client, db, cfg):
    srv, url = fake_decide.start()
    decide.recovered()
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hr = login(client, "root@x.io", "root password 1")
    old = ingest.import_text(db, cfg, "pods", "Ana|N|Old notes about ships.", title="Old")
    library.set_tags(db, old, ["finance", "shipping", "weather"])
    sid = db.one("SELECT space FROM $r", r=hierarchy.R("recording", old))["space"]
    money = hierarchy.create(db, sid, "Finance", description="budgets, invoices and the finance board")
    hierarchy.create(db, sid, "Gardening", description="plants and soil")
    rid = ingest.import_text(db, cfg, "pods", BUDGET, title="Budget meeting")
    yield {"hr": hr, "he": login(client, "ed@x.io", "editor password 1"), "hv": login(client, "vi@x.io", "viewer password 1"),
           "rid": rid, "old": old, "money": money, "url": url, "jev": fake_decide.Handler}  # fmt: skip
    srv.shutdown()
    decide.recovered()


def _on(client, env, **more):
    r = client.put(
        "/api/v1/settings/decisions", headers=env["hr"], json={"enabled": True, "base_url": env["url"], "model": "fake-jev", **more}
    )
    assert r.status_code == 200, r.text


def _run(db, cfg_fn, rid):
    jid = jobs.enqueue(db, rid, ["classify"])
    jobs.Worker(db, cfg_fn, steps=["classify"]).run_once()
    return jobs.get(db, jid)


def test_the_step_is_skipped_without_a_decision_model(client, db, app, env):
    job = _run(db, app.state.settings.current, env["rid"])
    assert job["step_runs"][0]["outcome"] == "skipped" and "Settings → Decisions" in job["step_runs"][0]["note"]
    assert "classify" in jobs.PIPELINE and jobs.AFTER_IMPORT.index("classify") < jobs.AFTER_IMPORT.index("summarize")


def test_sure_answers_are_applied_and_likely_ones_wait(client, db, app, env):
    rid, jev = env["rid"], env["jev"]
    _on(client, env)
    cfg = app.state.settings.current()
    jev.script = {"“finance”": {"noul": 0.97}, "“shipping”": {"noul": 0.6}, "“weather”": {"noul": 0.1},
                  "collection": {"choice": f"c{env['money']}", "probabilities": {f"c{env['money']}": 0.95}, "confidence": 0.95}}  # fmt: skip
    applied, waiting = classify.classify_recording(db, cfg, rid, lambda *_: None)
    assert [(s["kind"], s["value"]) for s in applied] == [("tag", "finance")]
    done = db.rows("SELECT email, target, detail FROM audit_log WHERE action = 'recording.classify.tag'")  # nobody did it: it says so
    assert [(a["email"], a["target"], a["detail"]["value"], a["detail"]["p"]) for a in done] == [
        ("decision model", f"recording:{rid}", "finance", 0.97)
    ]
    # moving changes who can read it, so it's only suggested however sure the model is
    assert [(s["kind"], s["value"], s["p"]) for s in waiting] == [("collection", env["money"], 0.95), ("tag", "shipping", 0.6)]
    asked = jev.seen[-1]
    assert asked["state"]["title"] == "Budget meeting" and "approved by the finance board" in asked["state"]["text"]
    assert len([q for q in asked["questions"].values() if q["type"] == "noul"]) == 3  # only tags the namespace uses
    rec = client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()
    assert rec["tags"] == ["finance"] and [s["id"] for s in rec["suggestions"]] == [f"collection:{env['money']}", "tag:shipping"]
    assert "suggestions_dismissed" not in rec
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["suggestions"] == []  # editors' business
    # accepting and dismissing
    assert client.post(f"/api/v1/recordings/{rid}/suggestions/tag:shipping", headers=env["hv"]).status_code == 403
    r = client.post(f"/api/v1/recordings/{rid}/suggestions/tag:shipping", headers=env["he"])
    assert r.status_code == 200 and r.json()["label"] == "shipping"
    assert client.post(f"/api/v1/recordings/{rid}/suggestions/tag:shipping", headers=env["he"]).status_code == 404
    r = client.post(f"/api/v1/recordings/{rid}/suggestions/collection:{env['money']}", headers=env["he"])
    assert r.status_code == 200, r.text
    rec = client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()
    assert rec["tags"] == ["finance", "shipping"] and rec["collection"] == env["money"] and rec["suggestions"] == []
    audit = db.rows("SELECT target, detail FROM audit_log WHERE action = 'recording.collection'")
    assert [(a["target"], a["detail"]["suggested"]) for a in audit] == [(f"collection:{env['money']}", 0.95)]


def test_dismissed_suggestions_stay_dismissed_and_routing_can_be_switched_on(client, db, app, env):
    rid, jev = env["rid"], env["jev"]
    _on(client, env)
    jev.script = {"“finance”": {"noul": 0.7}, "“shipping”": {"noul": 0.1}, "“weather”": {"noul": 0.1},
                  "collection": {"choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9}}  # fmt: skip
    job = _run(db, app.state.settings.current, rid)
    assert job["step_runs"][0]["outcome"] == "done", job
    assert [s["id"] for s in classify.suggestions(db, rid)] == ["tag:finance"]
    assert client.delete(f"/api/v1/recordings/{rid}/suggestions/tag:finance", headers=env["he"]).status_code == 200
    assert client.delete(f"/api/v1/recordings/{rid}/suggestions/tag:finance", headers=env["he"]).status_code == 404
    _run(db, app.state.settings.current, rid)
    assert classify.suggestions(db, rid) == [] and library.set_tags(db, rid, [])[1] == []
    # a tag the model applied and a person took off again isn't put back; one with a slash in it can be settled
    library.set_tags(db, env["old"], ["finance", "shipping", "weather", "projects/alpha"])
    jev.script |= {"“weather”": {"noul": 0.99}, "“projects/alpha”": {"noul": 0.6}}
    _run(db, app.state.settings.current, rid)
    assert library.set_tags(db, rid, [])[0] == ["weather"]
    _run(db, app.state.settings.current, rid)
    assert db.one("SELECT tags FROM $r", r=hierarchy.R("recording", rid))["tags"] == []
    assert [s["id"] for s in classify.suggestions(db, rid)] == ["tag:projects/alpha"]
    assert client.post(f"/api/v1/recordings/{rid}/suggestions/tag:projects%2Falpha", headers=env["he"]).status_code == 200
    assert db.one("SELECT tags FROM $r", r=hierarchy.R("recording", rid))["tags"] == ["projects/alpha"]
    # an admin lets it move resources by itself
    _on(client, env, route=True)
    jev.script["collection"] = {"choice": f"c{env['money']}", "probabilities": {f"c{env['money']}": 0.93}, "confidence": 0.93}
    job = _run(db, app.state.settings.current, rid)
    assert "collection Finance: applied (93% sure)" in str(job)
    assert db.one("SELECT collection FROM $r", r=hierarchy.R("recording", rid))["collection"] == env["money"]
    # placed by now: the collection isn't asked about again
    _run(db, app.state.settings.current, rid)
    assert "collection" not in jev.seen[-1]["questions"]
    # a model that can't be reached skips the step; the resource is fine
    jev.fail = (529, {"detail": "overloaded"})
    decide.recovered()
    job = _run(db, app.state.settings.current, rid)
    assert job["step_runs"][0]["outcome"] == "skipped" and "couldn't ask the decision model" in job["step_runs"][0]["note"]
    assert client.put("/api/v1/settings/decisions", headers=env["hr"], json={"route": "yes"}).status_code == 400
