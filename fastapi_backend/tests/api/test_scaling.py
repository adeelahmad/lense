"""Auto scaling (docs/scaling.md): providers with sealed credentials, groups that launch nothing until a person
approves their terms in the web app, rules, budgets, and the scaler launching, joining, draining and terminating units
through a fake provider."""

from __future__ import annotations

import datetime as dt
import sys

import pytest

from app.domain import activity, budgets, scaling, settings, store
from app.domain import scaling_drivers as drivers
from tests.helpers import login, make_user

R = store.R
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)


class Fake(drivers.Driver):
    """A cloud in memory: machines by name, a key that has to be "good", 50 cents an hour."""

    id = "fake"
    kind = "node"
    label = "Fake cloud"
    about = "Machines in a dict."
    priced = True
    fields = {
        "region": drivers._field("Region", required=True),
        "api_key": drivers._field("API key", required=True, secret=True),
    }
    template = {"size": drivers._field("Size", default="small", options=["small", "large"])}

    def __init__(self):
        self.machines, self.boots, self.seen_keys, self.fail = {}, {}, [], None

    def check(self, p):
        self.seen_keys.append(p["secrets"].get("api_key"))
        if p["secrets"].get("api_key") != "good":
            raise drivers.DriverError("the key was refused")
        return {"regions": 3}

    def price(self, p, template):
        return 0.5

    def launch(self, p, template, name, boot):
        if self.fail:
            raise drivers.DriverError(self.fail)
        self.machines[name] = "pending"
        self.boots[name] = boot
        return {"id": name, "address": "10.0.0.9"}

    def describe(self, p, node):
        return {"state": self.machines.get(node["external_id"], "gone")}

    def terminate(self, p, node):
        self.machines.pop(node["external_id"], None)


@pytest.fixture
def fake():
    drv = Fake()
    drivers.register(drv)
    yield drv
    drivers.DRIVERS.pop("fake", None)


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


@pytest.fixture
def key(client, h):
    """An admin's API key: an agent, which may do everything but approve."""
    tok = client.post("/api/v1/tokens", headers=h, json={"name": "agent", "scope": "write"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def on(db, cfg, **more):
    settings.save(db, cfg, "scaling", {"enabled": True, **more}, "root@x.io")
    return settings.effective(db, cfg)


def provider(client, h, **over):
    body = {"name": "Fake", "driver": "fake", "settings": {"region": "eu"}, "secrets": {"api_key": "good"}, **over}
    r = client.post("/api/v1/scaling/providers", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def group(client, h, pid, **over):
    body = {"name": "Workers", "provider": pid, "min": 0, "max": 3, "budget": 20, **over}
    return client.post("/api/v1/scaling/groups", headers=h, json=body)


def join(db, name, current=None, at=NOW):
    """The unit's `lens worker` has started and beats."""
    db.q("UPSERT $w MERGE {name: $n, heartbeat_at: $t, current: $c}", w=R("worker", name), n=name, t=at.isoformat(), c=current)


def tick(db, cfg, now=NOW):
    return scaling.Scaler(db, lambda: cfg, "test").tick(now=now, lease=False)


def unit_states(db, gid):
    return sorted((n["name"], n["state"]) for n in scaling.nodes(db, gid))


def test_scaling_is_off_and_lists_its_drivers(client, h, fake):
    v = client.get("/api/v1/scaling", headers=h).json()
    assert v["enabled"] is False and v["groups"] == [] and v["settings"]["tick_seconds"] == 30
    ids = {d["id"]: d for d in v["drivers"]}
    assert ids["local"]["priced"] is False and ids["fake"]["fields"]["api_key"]["secret"] is True
    assert set(v["metrics"]) >= set(scaling.METRICS)
    assert client.get("/api/v1/scaling/metrics", headers=h).json()["about"]["jobs_waiting"]


def test_a_providers_credentials_are_sealed_and_checked(client, db, cfg, h, fake):
    p = provider(client, h)
    assert p["secrets"] == {"api_key": True} and p["check"] == {"ok": True, "regions": 3}
    raw = db.one("SELECT sealed FROM $r", r=R("scale_provider", p["id"]))
    assert "good" not in str(raw) and fake.seen_keys == ["good"]  # kept sealed, opened only for the driver's call
    assert "good" not in client.get("/api/v1/scaling/providers", headers=h).text

    bad = provider(client, h, name="Bad", secrets={"api_key": "nope"})
    assert bad["check"] == {"ok": False, "error": "the key was refused"}
    for body in (
        {"name": "x", "driver": "fake", "settings": {"region": "eu"}},  # no key
        {"name": "x", "driver": "fake", "settings": {}, "secrets": {"api_key": "good"}},  # no region
        {"name": "x", "driver": "fake", "settings": {"region": "eu", "zone": 1}, "secrets": {"api_key": "good"}},
        {"name": "x", "driver": "nimbus", "settings": {}},
    ):
        assert client.post("/api/v1/scaling/providers", headers=h, json=body).status_code in (400, 404, 422), body

    r = client.patch(f"/api/v1/scaling/providers/{bad['id']}", headers=h, json={"secrets": {"api_key": "good"}})
    assert r.json()["check"]["ok"] is True
    assert client.post(f"/api/v1/scaling/providers/{p['id']}/check", headers=h).json()["ok"] is True
    assert client.delete(f"/api/v1/scaling/providers/{bad['id']}", headers=h).status_code == 200
    assert [a["action"] for a in activity.history(db, f"scale_provider:{p['id']}")][-1] == "scaling.provider.add"


def test_only_a_person_in_the_web_app_approves_a_groups_terms(client, db, cfg, h, key, fake):
    pid = provider(client, h)["id"]
    assert group(client, key, pid, budget=None).status_code == 400  # a priced provider needs a budget

    # an agent proposes: saved, but it launches nothing until a person approves
    g = group(client, key, pid).json()
    assert (g["approved"], g["approval"]) == (False, None) and g["budget"]["usd"] == 20
    assert g["waiting_for"] == "scaling is off: turn it on in Settings › Scaling"
    gid = g["id"]
    assert client.post(f"/api/v1/scaling/groups/{gid}/approve", headers=key).status_code == 403
    cfg = on(db, cfg)
    assert tick(db, cfg)["launched"] == 0 and scaling.nodes(db, gid) == []
    assert "approval" in client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()["waiting_for"]

    # the person, signed in, approves the terms as they stand
    g = client.post(f"/api/v1/scaling/groups/{gid}/approve", headers=h).json()
    assert g["approved"] is True and g["approval"]["by"] == "root@x.io" and g["terms"]["budget"] == 20 and g["waiting_for"] is None

    # within the terms an agent acts: fewer units, a lower budget, the desired count
    g = client.patch(f"/api/v1/scaling/groups/{gid}", headers=key, json={"max": 2, "budget": 10}).json()
    assert g["approved"] is True
    assert client.post(f"/api/v1/scaling/groups/{gid}/desired", headers=key, json={"desired": 2}).json()["desired"] == 2
    assert client.post(f"/api/v1/scaling/groups/{gid}/desired", headers=key, json={"desired": 5}).status_code == 400
    # widening them takes the approval away, until the person gives it again
    g = client.patch(f"/api/v1/scaling/groups/{gid}", headers=key, json={"max": 4}).json()
    assert g["approved"] is False and g["approval"] is None
    g = client.patch(f"/api/v1/scaling/groups/{gid}", headers=key, json={"max": 2, "budget": 50}).json()
    assert g["approved"] is False
    # a change the person makes in the web app carries their approval
    g = client.patch(f"/api/v1/scaling/groups/{gid}", headers=h, json={"max": 4}).json()
    assert g["approved"] is True and g["terms"]["max"] == 4 and g["terms"]["budget"] == 50
    # and a group the person makes there is approved as it's made
    assert group(client, h, pid, name="Mine").json()["approved"] is True
    acts = [a["action"] for a in activity.history(db, f"scale_group:{gid}", 50)]
    assert {"scaling.group.create", "scaling.group.approve", "scaling.group.change", "scaling.group.desired"} <= set(acts)


def test_the_scaler_launches_joins_drains_and_terminates(client, db, cfg, h, fake):
    cfg = on(db, cfg)
    pid = provider(client, h)["id"]
    gid = group(client, h, pid, min=1, max=3, cooldown_seconds=0, warmup_seconds=0).json()["id"]

    assert tick(db, cfg)["launched"] == 1  # min 1
    ((name, state),) = unit_states(db, gid)
    assert state == "launching" and fake.machines == {name: "pending"} and name.startswith("lens-workers-")
    assert fake.boots[name]["env"] == {"LENS_SCALING_UNIT": name}  # no long-lived secret in what the provider gets

    join(db, name)
    tick(db, cfg, NOW + dt.timedelta(seconds=30))
    assert unit_states(db, gid) == [(name, "running")]
    assert client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()["units"] == {"launching": 0, "running": 1, "draining": 0}

    # asked for more: two more launch
    client.post(f"/api/v1/scaling/groups/{gid}/desired", headers=h, json={"desired": 3})
    assert tick(db, cfg, NOW + dt.timedelta(minutes=1))["launched"] == 2
    for n in scaling.nodes(db, gid, alive=True):
        join(db, n["name"], at=NOW + dt.timedelta(minutes=2))
    tick(db, cfg, NOW + dt.timedelta(minutes=2))
    assert [s for _, s in unit_states(db, gid)] == ["running"] * 3

    # back to one: a busy worker drains (its job is handed back) and goes once it's idle; idle ones go first
    busy = scaling.nodes(db, gid, alive=True)[0]["name"]
    join(db, busy, current=7, at=NOW + dt.timedelta(minutes=3))
    client.post(f"/api/v1/scaling/groups/{gid}/desired", headers=h, json={"desired": 1})
    assert tick(db, cfg, NOW + dt.timedelta(minutes=3))["terminated"] == 2
    left = dict(unit_states(db, gid))
    assert left[busy] == "running" and sorted(left.values()) == ["draining", "draining", "running"]
    for n, s in left.items():
        if s == "draining":
            join(db, n, current=None, at=NOW + dt.timedelta(minutes=4))  # finished, idle
    tick(db, cfg, NOW + dt.timedelta(minutes=4))
    assert sorted(dict(unit_states(db, gid)).values()) == ["running", "terminated", "terminated"]
    assert list(fake.machines) == [busy]

    # the ledger has each step, and the hours each unit ran, as an estimate
    acts = activity.history(db, f"scale_group:{gid}", 100)
    names = [a["action"] for a in acts]
    assert {"scaling.launch", "scaling.join", "scaling.unit.drain", "scaling.terminate", "scaling.unit"} <= set(names)
    cost = [a for a in acts if a["action"] == "scaling.unit"]
    assert all(a["detail"]["estimate"] for a in cost) and sum(a["cost_usd"] for a in cost) > 0
    assert budgets.status(db, f"scale_group:{gid}")["spent"]["usd"] > 0

    # turning scaling off lets every unit go
    settings.save(db, cfg, "scaling", {"enabled": False}, "root@x.io")
    cfg = settings.effective(db, cfg)
    tick(db, cfg, NOW + dt.timedelta(minutes=5))
    join(db, busy, current=None, at=NOW + dt.timedelta(minutes=6))
    tick(db, cfg, NOW + dt.timedelta(minutes=6))
    assert scaling.nodes(db, gid, alive=True) == [] and fake.machines == {}


def test_a_unit_that_never_joins_fails_and_failures_pause_the_group(client, db, cfg, h, fake):
    cfg = on(db, cfg, join_minutes=5)
    pid = provider(client, h)["id"]
    gid = group(client, h, pid, min=1, max=1, cooldown_seconds=0, warmup_seconds=0).json()["id"]
    tick(db, cfg)
    first = scaling.nodes(db, gid)[0]["name"]
    tick(db, cfg, NOW + dt.timedelta(minutes=6))  # it never joined: terminated as failed, and replaced
    assert dict(unit_states(db, gid))[first] == "failed" and first not in fake.machines
    assert sorted(dict(unit_states(db, gid)).values()) == ["failed", "launching"]
    fake.fail = "out of capacity"
    fake.machines.clear()  # the provider lost the new one too, and launches no more
    for i in range(3):
        tick(db, cfg, NOW + dt.timedelta(minutes=10 + i))
    g = client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()
    assert g["paused"] is True and "out of capacity" in g["paused_why"] and g["waiting_for"].startswith("paused")
    assert tick(db, cfg, NOW + dt.timedelta(minutes=20))["launched"] == 0
    g = client.post(f"/api/v1/scaling/groups/{gid}/pause", headers=h, json={"paused": False}).json()
    assert g["paused"] is False and g["failures"] == 0


def test_at_its_budgets_cap_a_group_stops(client, db, cfg, h, fake):
    cfg = on(db, cfg)
    pid = provider(client, h)["id"]
    gid = group(client, h, pid, min=2, max=2, budget=1, cooldown_seconds=0, warmup_seconds=0).json()["id"]
    tick(db, cfg)
    for n in scaling.nodes(db, gid, alive=True):
        join(db, n["name"], at=NOW + dt.timedelta(hours=1))
    tick(db, cfg, NOW + dt.timedelta(hours=1))  # two units, an hour at 50 cents: the dollar is spent
    tick(db, cfg, NOW + dt.timedelta(hours=1, minutes=1))
    g = client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()
    assert g["capped"] and g["budget"]["state"] == "over" and "budget" in g["waiting_for"]
    assert [s for _, s in unit_states(db, gid)] == ["draining", "draining"]  # at_cap stop: every unit goes
    assert tick(db, cfg, NOW + dt.timedelta(hours=1, minutes=2))["launched"] == 0


def test_rules_scale_on_waiting_jobs_and_on_a_schedule(client, db, cfg, h, fake):
    cfg = on(db, cfg)
    pid = provider(client, h)["id"]
    rules = [
        {"type": "step", "metric": "jobs_waiting", "above": 4, "minutes": 2, "add": 2},
        {"type": "schedule", "cron": "0 18 * * *", "timezone": "UTC", "max": 1, "desired": 0},
    ]
    g = group(client, h, pid, max=5, rules=rules, cooldown_seconds=0, warmup_seconds=0).json()
    assert [r["says"] for r in g["rules"]] == [
        "add 2 when jobs_waiting above 4 for 2 min",
        g["rules"][1]["says"],
    ] and "max 1" in g["rules"][1]["says"]
    gid = g["id"]
    for _ in range(6):
        db.q("CREATE job CONTENT {status: 'queued', next_step: 'transcribe', created_at: $t}", t=NOW.isoformat())
    assert tick(db, cfg)["launched"] == 0  # the condition has to hold for 2 minutes
    assert tick(db, cfg, NOW + dt.timedelta(minutes=2))["launched"] == 2
    said = client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()["last_eval"]["said"]
    assert said == ["add 2 when jobs_waiting above 4 for 2 min: jobs_waiting is 6, so 2"]
    # at 18:00 the schedule brings max down to 1
    tick(db, cfg, NOW.replace(hour=18, minute=1))
    g = client.get(f"/api/v1/scaling/groups/{gid}", headers=h).json()
    assert (g["max"], g["desired"]) == (1, 1)

    for bad in (
        {"type": "step", "metric": "jobs_waiting", "above": 4, "add": 1, "remove": 1},
        {"type": "target", "metric": "mood", "target": 1},
        {"type": "schedule", "cron": "whenever", "desired": 1},
        {"type": "schedule", "cron": "0 9 * * *"},
    ):
        assert client.patch(f"/api/v1/scaling/groups/{gid}", headers=h, json={"rules": [bad]}).status_code in (400, 422), bad


def test_protected_and_terminated_units(client, db, cfg, h, key, fake):
    cfg = on(db, cfg)
    pid = provider(client, h)["id"]
    gid = group(client, h, pid, min=2, max=2, cooldown_seconds=0, warmup_seconds=0).json()["id"]
    tick(db, cfg)
    a, b = scaling.nodes(db, gid, alive=True)
    assert client.post(f"/api/v1/scaling/units/{a['id']}/protect", headers=key, json={"protected": True}).json()["protected"]
    out = client.post(f"/api/v1/scaling/units/{b['id']}/terminate", headers=key, json={"why": "testing"}).json()
    assert out["state"] == "terminated" and out["why"] == "testing" and b["name"] not in fake.machines
    assert client.post(f"/api/v1/scaling/units/{b['id']}/terminate", headers=key, json={}).status_code == 400
    assert client.delete(f"/api/v1/scaling/providers/{pid}", headers=h).status_code == 400  # a group uses it
    assert client.delete(f"/api/v1/scaling/groups/{gid}", headers=h).status_code == 200
    assert fake.machines == {} and scaling.nodes(db, gid, alive=True) == []


def test_the_settings_section(client, db, cfg, h):
    assert settings.effective(db, cfg)["scaling"]["enabled"] is False
    for bad in ({"tick_seconds": 1}, {"join_minutes": 500}, {"tag": "Not A Tag"}):
        with pytest.raises(ValueError):
            settings.save(db, cfg, "scaling", bad, "root@x.io")
    settings.save(db, cfg, "scaling", {"tick_seconds": 60, "tag": "acme"}, "root@x.io")
    s = settings.effective(db, cfg)["scaling"]
    assert (s["tick_seconds"], s["tag"]) == (60, "acme")


def test_only_admins_see_scaling(client, db, h):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    e = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/scaling", headers=e).status_code == 403
    assert client.post("/api/v1/scaling/groups", headers=e, json={"name": "x", "provider": 1}).status_code == 403


def test_this_machine_runs_more_workers_at_no_cost(client, db, cfg, h, monkeypatch):
    here = drivers.get("local")
    monkeypatch.setattr(here, "command", lambda name, template: [sys.executable, "-c", "import time; time.sleep(60)"])
    assert tick(db, cfg) is None  # off, with nothing running: the scaler doesn't even take its lease
    cfg = on(db, cfg)
    pid = client.post("/api/v1/scaling/providers", headers=h, json={"name": "Here", "driver": "local"}).json()["id"]
    g = group(client, h, pid, budget=None, min=1, max=2, template={"steps": ["transcribe"]}, cooldown_seconds=0, warmup_seconds=0)
    g = g.json()
    assert g["approved"] is True and g["budget"] is None  # it costs nothing, so it needs no budget
    assert tick(db, cfg)["launched"] == 1
    (n,) = scaling.nodes(db, g["id"])
    assert n["price"] == 0.0 and n["external_id"].startswith(here.host() + ":")
    assert here.describe(None, n)["state"] == "running"  # a process on this machine
    join(db, n["name"])
    tick(db, cfg, NOW + dt.timedelta(seconds=30))
    out = client.post(f"/api/v1/scaling/units/{n['id']}/terminate", headers=h, json={}).json()
    assert out["state"] == "draining"
    tick(db, cfg, NOW + dt.timedelta(minutes=1))  # idle: it goes, and its process stops
    assert scaling.nodes(db, g["id"])[0]["state"] in ("terminated", "launching")
    assert here.describe(None, n)["state"] == "gone"
    for u in scaling.nodes(db, g["id"], alive=True):  # min 1 started another; stop it too
        here.terminate(None, u)
