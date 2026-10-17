"""Automations over the API (docs/automations.md, Automations): saved with their checks, for the person who made them
in namespaces they can edit; changed, switched, deleted and restored; the policy set only by the person; the actions
catalog; and the parts built in their own modules (schedules, webhooks, joins) called once they are there."""

from __future__ import annotations

import json

import pytest

from app.domain import activity, auth, automations, budgets, ingest, keyring, store, workflows
from tests.helpers import login, make_user, without

URL = "/api/v1/automations"


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b):
    return {"source": a, "target": b}


SAY = {
    "nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "New: {{ event.object.name }}"})],
    "edges": [edge("i", "a")],
}
NEW = [{"as": "new", "on": "event", "kinds": ["asset.added"]}]


def body(**over):
    return {"name": "Say new assets", "triggers": NEW, "graph": SAY, **over}


@pytest.fixture
def env(client, new_client, db):
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "viewer"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "pods": pods,
        "calls": calls,
        "hr": login(client, "root@x.io", "root password 1"),
        "he": login(new_client(), "ed@x.io", "editor password 1"),
        "hv": login(new_client(), "vi@x.io", "viewer password 1"),
    }


def problem(client, h, json, path=URL, method="post"):
    r = getattr(client, method)(path, headers=h, json=json)
    assert r.status_code == 400, r.text
    return r.json()["detail"]


def test_automations_are_saved_for_their_maker_in_namespaces_they_edit(client, db, env):
    hr, he, hv = env["hr"], env["he"], env["hv"]
    assert client.get(URL).status_code == 401
    # an admin's works everywhere unless they say; its Then is saved as a workflow of scope event
    r = client.post(URL, headers=hr, json=body(**{"if": {"all": [{"path": "event.space", "op": "exists"}]}}))
    assert r.status_code == 200, r.text
    root = r.json()["id"]
    a = client.get(f"{URL}/{root}", headers=hr).json()
    assert a["spaces"] is None and a["enabled"] and a["enabled_at"] and a["run_as"] and a["version"] is None
    assert a["graph"] == workflows.get(db, a["workflow"])["graph"] and a["runs_version"] == 1 and a["if"]["all"][0]["op"] == "exists"
    assert a["join"] == {"mode": "any"} and a["limits"] == {"cooldown": "0s", "max_per_hour": 60} and a["counters"]["runs"] == 0
    assert workflows.get(db, a["workflow"])["scope"] == "event" and a["triggers"][0]["from_automations"] is False
    # a member's works in the namespaces they can edit, and they see only those
    mine = client.post(URL, headers=he, json=body(name="Mine")).json()["id"]
    assert client.get(f"{URL}/{mine}", headers=he).json()["spaces"] == [env["pods"]]
    assert [x["id"] for x in client.get(URL, headers=he).json()["automations"]] == [mine]
    assert client.get(f"{URL}/{root}", headers=he).status_code == 404
    assert [x["id"] for x in client.get(URL, headers=hr).json()["automations"]] == [root, mine]
    assert "can't edit namespace" in problem(client, he, body(spaces=[env["calls"]]))
    assert "can't edit any namespace" in problem(client, hv, body())
    assert client.get(f"{URL}/{mine}", headers=hv).status_code == 404
    assert client.patch(f"{URL}/{mine}", headers=hv, json={"name": "x"}).status_code == 404


def test_what_an_automation_is_made_of_is_checked(client, db, env, monkeypatch):
    without(monkeypatch, "timers", "match")  # schedules and joins come with their own modules
    hr = env["hr"]
    assert "at least one trigger" in problem(client, hr, body(triggers=[]))
    assert "kinds" in problem(client, hr, body(triggers=[{"as": "x", "on": "event"}]))
    assert "no kind email.nope" in problem(client, hr, body(triggers=[{"as": "x", "on": "event", "kinds": ["email.nope"]}]))
    assert "schedules are coming soon" in problem(client, hr, body(triggers=[{"as": "m", "on": "schedule", "cron": "0 7 * * *"}]))
    two = [*NEW, {"as": "now", "on": "manual"}]
    assert "coming soon" in problem(client, hr, body(triggers=two, join={"mode": "all", "within": "1h"}))
    assert "paths start with" in problem(client, hr, body(**{"if": {"all": [{"path": "nope.x", "op": "exists"}]}}))
    assert "choose an action" in problem(
        client, hr, body(graph={"nodes": [node("i", "input"), node("a", "action", action="nope")], "edges": [edge("i", "a")]})
    )
    assert "say what it does" in problem(client, hr, {"name": "x", "triggers": NEW})
    assert "max_per_hour" in problem(client, hr, body(limits={"max_per_hour": 0}))
    assert "cooldown" in problem(client, hr, body(limits={"cooldown": "-5m"}))
    assert "isn't in the workflow" in problem(client, hr, body(policy={"zz": "ask"}))
    # auto is only the person's own pick, on its own route
    assert client.post(URL, headers=hr, json=body(policy={"a": "auto"})).status_code == 422
    # a saved workflow of scope event will do, another scope won't
    wid = workflows.create(db, "Say it", SAY, scope="event")
    other = workflows.create(db, "Keep", {"nodes": [node("i", "input"), node("o", "output", key="k")], "edges": [edge("i", "o")]})
    assert "scope event" in problem(client, hr, {"name": "x", "triggers": NEW, "workflow": other})
    assert "not both" in problem(client, hr, body(workflow=wid))
    r = client.post(URL, headers=hr, json={"name": "Saved", "triggers": [{"as": "now", "on": "manual"}], "workflow": wid, "version": 1})
    assert r.status_code == 200 and client.get(f"{URL}/{r.json()['id']}", headers=hr).json()["version"] == 1


def test_automations_change_switch_delete_and_come_back(client, db, env):
    hr = env["hr"]
    aid = client.post(URL, headers=hr, json=body(enabled=False, **{"if": {"all": [{"path": "event.space", "op": "exists"}]}})).json()["id"]
    a = client.get(f"{URL}/{aid}", headers=hr).json()
    assert not a["enabled"] and a["enabled_at"] is None
    # a new graph is a new version of its workflow, which it follows; the name follows too
    more = {
        "nodes": [*SAY["nodes"], node("b", "action", action="log_line", params={"text": "{{ steps.a.line }}"})],
        "edges": [*SAY["edges"], edge("a", "b")],
    }
    r = client.patch(f"{URL}/{aid}", headers=hr, json={"graph": more, "name": "Say twice", "if": None})
    assert r.status_code == 200, r.text
    a = client.get(f"{URL}/{aid}", headers=hr).json()
    assert a["runs_version"] == 2 and a["version"] is None and len(a["graph"]["nodes"]) == 3 and a["if"] is None
    assert workflows.get(db, a["workflow"])["name"] == "Say twice"
    assert "a trigger on event has no cron" in problem(
        client, hr, {"triggers": [{"as": "x", "on": "event", "kinds": ["asset.added"], "cron": "0 * * * *"}]}, f"{URL}/{aid}", "patch"
    )
    # switched on, it reacts from now on; off keeps everything
    assert client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": True}).status_code == 200
    on = client.get(f"{URL}/{aid}", headers=hr).json()
    assert on["enabled"] and on["enabled_at"]
    client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": False})
    assert client.get(f"{URL}/{aid}", headers=hr).json()["enabled_at"] == on["enabled_at"]
    client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": True})
    # deleted: switched off and hidden, and restore undoes it
    assert client.delete(f"{URL}/{aid}", headers=hr).status_code == 200
    assert client.get(f"{URL}/{aid}", headers=hr).status_code == 404 and client.get(URL, headers=hr).json()["automations"] == []
    assert [x["id"] for x in client.get(URL, params={"removed": True}, headers=hr).json()["automations"]] == [aid]
    assert client.post(f"{URL}/{aid}/restore", headers=hr).status_code == 200
    back = client.get(f"{URL}/{aid}", headers=hr).json()
    assert not back["enabled"] and back["removed_at"] is None
    assert client.post(f"{URL}/9999/restore", headers=hr).status_code == 404


def test_only_the_person_lets_a_node_run_on_its_own(client, db, env):
    hr = env["hr"]
    aid = client.post(URL, headers=hr, json=body()).json()["id"]
    key = client.post("/api/v1/tokens", headers=hr, json={"name": "agent", "scope": "write"}).json()["token"]
    ha = {"Authorization": f"Bearer {key}"}
    r = client.post(f"{URL}/{aid}/policy", headers=ha, json={"node": "a", "value": "auto"})
    assert r.status_code == 403 and "signed in" in r.json()["detail"]
    assert client.post(f"{URL}/{aid}/policy", headers=ha, json={"node": "a", "value": "ask"}).status_code == 200
    assert client.post(f"{URL}/{aid}/policy", headers=hr, json={"node": "zz", "value": "auto"}).status_code == 400
    assert client.post(f"{URL}/{aid}/policy", headers=hr, json={"node": "a", "value": "auto"}).json() == {"a": "auto"}
    # a save that keeps it on auto is fine; nobody else sets it there
    assert client.patch(f"{URL}/{aid}", headers=ha, json={"policy": {"a": "auto"}, "name": "Still"}).status_code == 200
    assert client.get(f"{URL}/{aid}", headers=hr).json()["policy"] == {"a": "auto"}
    automations.set_policy(db, aid, "a", "ask")
    assert "only you can let node a run" in problem(client, ha, {"policy": {"a": "auto"}}, f"{URL}/{aid}", "patch")
    # a node that goes away takes its pick with it
    client.post(f"{URL}/{aid}/policy", headers=hr, json={"node": "a", "value": "auto"})
    other = {"nodes": [node("i", "input"), node("b", "action", action="log_line", params={"text": "x"})], "edges": [edge("i", "b")]}
    client.patch(f"{URL}/{aid}", headers=hr, json={"graph": other})
    assert client.get(f"{URL}/{aid}", headers=hr).json()["policy"] == {}


def test_credentials_for_a_web_call_are_sealed_when_it_is_saved(client, db, cfg, env):
    he = env["he"]
    headers = {"Authorization": "Bearer s3cret", "X-Token": "{{ event.id }}", "Accept": "text/plain"}
    call = node("w", "action", action="http_request", params={"url": "https://hooks.example/in", "method": "POST", "headers": headers})
    graph = {"nodes": [node("i", "input"), call], "edges": [edge("i", "w")]}
    aid = client.post(URL, headers=he, json=body(graph=graph)).json()["id"]
    a = client.get(f"{URL}/{aid}", headers=he).json()
    kept = a["graph"]["nodes"][1]["config"]["params"]["headers"]
    # bound to this automation and the address, with the namespace's key; a template is filled in when it runs
    assert keyring.is_sealed(kept["Authorization"]) and kept["X-Token"] == "{{ event.id }}" and kept["Accept"] == "text/plain"
    assert "s3cret" not in json.dumps(a) and kept["Authorization"].split(".")[1] == str(env["pods"])
    got = keyring.unseal(db, cfg, kept["Authorization"], f"automation:{aid}.header:authorization@https://hooks.example:443")
    assert got == b"Bearer s3cret"
    # saved again as it was read, it is sealed once
    r = client.patch(f"{URL}/{aid}", headers=he, json={"graph": a["graph"]})
    assert r.status_code == 200, r.text
    assert client.get(f"{URL}/{aid}", headers=he).json()["graph"]["nodes"][1]["config"]["params"]["headers"] == kept
    # a credential is sealed for one address, so the address can't be a template
    call["config"]["params"]["url"] = "https://{{ event.id }}.example/in"
    assert "sealed for one address" in problem(client, he, body(graph=graph))


def test_the_actions_catalog(client, env):
    got = client.get(f"{URL}/actions", headers=env["he"]).json()
    log_line = next(a for a in got if a["name"] == "log_line")
    assert log_line["effect"] == "read" and log_line["required"] == ["text"] and log_line["params"]["type"] == "object"


def test_a_webhook_trigger_gets_its_address_when_saved_and_loses_it_when_dropped(client, db, env):
    hr = env["hr"]
    ts = [*NEW, {"as": "ping", "on": "webhook"}]
    r = client.post(URL, headers=hr, json=body(triggers=ts, spaces=[env["pods"]]))
    assert r.status_code == 200, r.text
    aid, (hook,) = r.json()["id"], r.json()["hooks"]
    assert hook["as"] == "ping" and "/api/v1/automations/hooks/lah_" in hook["url"] and hook["secret"]
    path = hook["url"][hook["url"].index("/api/v1/") :]
    kept = client.get(f"{URL}/{aid}", headers=hr).json()["triggers"][1]["hook"]
    assert isinstance(kept, int)
    # a call to it is an activity in the automation's namespace
    r = client.post(path, json={"door": "open"})
    assert r.status_code == 202, r.text
    # saved again, the trigger keeps its address; dropped, the address stops working at once
    assert client.patch(f"{URL}/{aid}", headers=hr, json={"triggers": ts}).json()["hooks"] == []
    assert client.post(path, json={"door": "open"}).status_code == 202
    assert client.patch(f"{URL}/{aid}", headers=hr, json={"triggers": NEW}).status_code == 200
    assert client.post(path, json={"door": "open"}).status_code == 401


def test_a_schedule_sets_its_timer_and_switching_off_calls_it_off(client, db, env):
    from app.domain import timers

    hr = env["hr"]
    r = client.post(URL, headers=hr, json=body(triggers=[{"as": "m", "on": "schedule", "cron": "0 7 * * *"}]))
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert timers.get(db, f"sched-{aid}-m")["status"] == "waiting"
    client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": False})
    assert timers.get(db, f"sched-{aid}-m")["status"] == "cancelled"
    client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": True})
    assert timers.get(db, f"sched-{aid}-m")["status"] == "waiting"
    assert client.delete(f"{URL}/{aid}", headers=hr).status_code == 200
    assert timers.get(db, f"sched-{aid}-m")["status"] == "cancelled"


def test_schedules_and_webhooks_are_armed_once_their_modules_are_there(client, db, env, monkeypatch):
    calls = []
    fake = {
        ("timers", "schedule_automation"): lambda db, a: calls.append(("arm", a["id"])),
        ("timers", "relative"): lambda *a: None,
        ("timers", "cancel_for"): lambda db, aid: calls.append(("cancel", aid)),
        ("automation_hooks", "token_for"): lambda db, cfg, aid, alias: {
            "hook": 70 + aid,
            "url": f"https://lens.example/hooks/{alias}-{aid}",
            "secret": "whsec_x",
        },
        ("match", "join_step"): lambda *a: [],
    }
    monkeypatch.setattr(automations, "provided", lambda m, fn: fake.get((m, fn)))
    hr = env["hr"]
    ts = [{"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"}, {"as": "hook", "on": "webhook"}]
    r = client.post(URL, headers=hr, json=body(triggers=ts, join={"mode": "any"}))
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["hooks"] == [{"as": "hook", "url": f"https://lens.example/hooks/hook-{aid}", "secret": "whsec_x"}]
    assert calls == [("arm", aid)]
    a = client.get(f"{URL}/{aid}", headers=hr).json()
    assert a["triggers"][1]["hook"] == 70 + aid
    # the URL is shown once: saving again keeps the hook, and a hook is never chosen
    r = client.patch(f"{URL}/{aid}", headers=hr, json={"triggers": [*ts[:1], {"as": "hook", "on": "webhook", "hook": 5}]})
    assert r.json()["hooks"] == [] and client.get(f"{URL}/{aid}", headers=hr).json()["triggers"][1]["hook"] == 70 + aid
    client.post(f"{URL}/{aid}/enable", headers=hr, json={"on": False})
    assert calls[-1] == ("cancel", aid)
    two = [*NEW, {"as": "now", "on": "manual"}]
    assert client.post(URL, headers=hr, json=body(triggers=two, join={"mode": "all", "within": "1h"})).status_code == 200


def test_runs_are_started_listed_and_read_by_who_manages_them(client, db, cfg, env):
    he, hv, hr = env["he"], env["hv"], env["hr"]
    aid = client.post(URL, headers=he, json=body()).json()["id"]
    rec = ingest.import_text(db, cfg, "pods", "Sam: Hello.", title="Courier")
    one = f"create-recording-{rec}"
    # run now on an activity, and with none: the newest one its triggers listen for
    r = client.post(f"{URL}/{aid}/run", headers=he, json={"activity": one})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["status"] == "done" and first["run"]
    again = client.post(f"{URL}/{aid}/run", headers=he).json()
    assert again["status"] == "done" and again["run"] != first["run"]
    page = client.get(f"{URL}/{aid}/runs", headers=he, params={"limit": 1}).json()
    assert [x["id"] for x in page["runs"]] == [again["run"]] and page["next"] == again["run"]
    rest = client.get(f"{URL}/{aid}/runs", headers=he, params={"before": page["next"]}).json()
    assert [x["id"] for x in rest["runs"]] == [first["run"]] and rest["next"] is None
    run = client.get(f"{URL}/runs/{first['run']}", headers=he).json()
    assert run["event"] == one and run["by"] == "ed@x.io" and run["counts"]["done"] == 1
    assert run["intents"][0]["result"]["line"] == "New: Courier" and run["trace"]["a"]["status"] == "done" and run["log"]
    assert {x["id"] for x in client.get(f"{URL}/runs", headers=he, params={"activity": one}).json()} == {first["run"], again["run"]}
    # lists show what waits and what it cost this month
    [listed] = client.get(URL, headers=he).json()["automations"]
    assert listed["waiting"] == 0 and listed["cost_month"] == {"cost_usd": 0.0, "estimate": False} and listed["last_run"] == again["run"]
    # an admin sees them; someone who doesn't manage it sees nothing
    assert client.get(f"{URL}/runs/{first['run']}", headers=hr).status_code == 200
    assert client.get(f"{URL}/runs/{first['run']}", headers=hv).status_code == 404
    assert client.get(f"{URL}/runs", headers=hv, params={"activity": one}).json() == []
    assert client.post(f"{URL}/{aid}/run", headers=hv).status_code == 404
    assert client.get(f"{URL}/{aid}/upcoming", headers=he).json() == {"times": []}
    # nor on an activity outside its namespaces, or one its person can't see
    calls = ingest.import_text(db, cfg, "calls", "Sam: Hi.", title="Call")
    r = client.post(f"{URL}/{aid}/run", headers=hr, json={"activity": f"create-recording-{calls}"})
    assert r.status_code == 400 and "isn't in a namespace it works in" in r.json()["detail"]
    other = ingest.import_text(db, cfg, "other", "Sam: Hi.", title="Other")
    root = client.post(URL, headers=hr, json=body(name="Root")).json()["id"]
    db.q("UPDATE $r SET run_as = $a", r=store.R("automation", root), a=auth.find_account(db, "ed@x.io")["id"])
    r = client.post(f"{URL}/{root}/run", headers=hr, json={"activity": f"create-recording-{other}"})
    assert r.status_code == 400 and "can't see that activity" in r.json()["detail"]
    assert client.post(f"{URL}/{aid}/run", headers=he, json={"activity": "create-recording-999"}).status_code == 404


def test_automations_are_tried_saved_or_as_a_draft(client, db, env, monkeypatch):
    without(monkeypatch, "timers")
    he = env["he"]
    aid = client.post(URL, headers=he, json=body()).json()["id"]
    # on its first trigger's kind's sample, keeping nothing
    t = client.post(f"{URL}/{aid}/test", headers=he).json()
    assert t["status"] == "done" and t["intents"][0]["status"] == "done" and t["intents"][0]["result"]["line"].startswith("New: ")
    assert db.rows("SELECT id FROM automation_run") == [] and client.get(f"{URL}/{aid}", headers=he).json()["counters"]["runs"] == 0
    # a draft, with an If that's false on this sample
    draft = body(**{"if": {"all": [{"path": "event.object.name", "op": "equals", "value": "nothing like it"}]}})
    t = client.post(f"{URL}/test", headers=he, json={"draft": draft, "kind": "asset.added"})
    assert t.status_code == 200, t.text
    t = t.json()
    assert t["status"] == "skipped" and t["intents"] == []
    t = client.post(f"{URL}/test", headers=he, json={"draft": body()}).json()
    assert t["status"] == "done" and t["intents"][0]["action"] == "log_line"
    assert problem(client, he, {"draft": body(), "kind": "no.such"}, f"{URL}/test").startswith("no kind no.such")
    assert "coming soon" in problem(
        client, he, {"draft": body(triggers=[{"as": "t", "on": "schedule", "cron": "0 7 * * *"}])}, f"{URL}/test"
    )
    assert client.post(f"{URL}/test", headers=he, json={"draft": body(), "activity": "create-recording-999"}).status_code == 404
    assert db.rows("SELECT id FROM automation") and len(db.rows("SELECT id FROM automation")) == 1


def test_a_run_held_over_budget_waits_for_the_persons_pick(client, db, cfg, env):
    he = env["he"]
    aid = client.post(URL, headers=he, json=body()).json()["id"]
    activity.record("out", "model.chat", [f"automation:{aid}"], db=db, model="m", cost_usd=0.6)
    budgets.put(db, f"automation:{aid}", usd=0.5)
    assert "nothing it listens for has happened yet" in problem(client, he, None, f"{URL}/{aid}/run")
    rec = ingest.import_text(db, cfg, "pods", "Sam: Hello.", title="Courier")
    held = client.post(f"{URL}/{aid}/run", headers=he, json={"activity": f"create-recording-{rec}"}).json()
    assert held["status"] == "held" and "$0.6 of $0.5 this month" in held["why"]
    assert client.post(f"{URL}/runs/{held['run']}/decide", headers=env["hv"], json={"run": True}).status_code == 404
    got = client.post(f"{URL}/runs/{held['run']}/decide", headers=he, json={"run": True}).json()
    assert got["run"] != held["run"] and got["status"] == "done"
    assert client.get(f"{URL}/runs/{held['run']}", headers=he).json()["status"] == "released"
    r = client.post(f"{URL}/runs/{held['run']}/decide", headers=he, json={"run": False})
    assert r.status_code == 400 and "isn't waiting on a budget" in r.json()["detail"]


def test_an_automation_is_drafted_from_a_sentence(client, db, env, monkeypatch):
    from app.domain import automation_draft

    reply = {"name": "Say new", "triggers": NEW, "actions": [{"action": "log_line", "params": {"text": "New: {{ event.object.name }}"}}]}
    monkeypatch.setattr(automation_draft.llm, "configured", lambda cfg: True)
    monkeypatch.setattr(automation_draft.llm, "json_out", lambda cfg, system, user, schema, model=None: reply)
    r = client.post(f"{URL}/draft", headers=env["he"], json={"text": "when something new comes in, log its name"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["draft"]["enabled"] is False and got["draft"]["triggers"] == [{**NEW[0], "from_automations": False}]
    assert got["dropped"] == [] and got["unmapped"] == []
    # it tries and saves as it is; drafting saved nothing
    assert db.rows("SELECT id FROM automation") == []
    assert client.post(f"{URL}/test", headers=env["he"], json={"draft": got["draft"]}).json()["status"] == "done"
    assert client.post(URL, headers=env["he"], json=got["draft"]).status_code == 200
    assert "can't edit any namespace" in problem(client, env["hv"], {"text": "log it"}, f"{URL}/draft")
    assert client.post(f"{URL}/draft", headers=env["he"], json={"text": "x" * 2001}).status_code == 422
