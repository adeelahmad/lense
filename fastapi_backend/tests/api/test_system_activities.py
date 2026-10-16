"""Lens's own happenings as activities (docs/automations.md, Kinds): a routine run that finished as `routine.ran`, a
budget crossing its warning or its cap as `budget.warned`, a backup taken by hand or on the schedule as
`backup.finished`, and a newer Lens release as `lens.release`. Each is for admins unless it is about one namespace,
and each is logged once however often the thing that makes it runs."""

from __future__ import annotations

import datetime as dt
import time

import pytest

from app.domain import activity, backups, budgets, events, events_streams, migrations, routines, store, updates

ADMIN = events.viewer(1, None, admin=True)


@pytest.fixture(autouse=True)
def fresh():
    events._seen.clear()
    events._claimed.clear()
    yield


def _start(db, cfg, consumer, kinds):
    """A follower's first look marks where it starts: what happens in that second isn't seen, so wait for the next."""
    assert events.follow(db, cfg, consumer, kinds=kinds)[0] == []
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def _logged(db, kind):
    return sorted(db.values("SELECT VALUE record::id(id) FROM event_log WHERE kind = $k", k=kind))


def test_a_finished_routine_run_is_routine_ran(db, cfg):
    pods = store.ns_id(db, "pods")
    one = routines.create(db, "Tidy pods", [{"type": "sync"}], namespaces=[pods])
    every = routines.create(db, "Tidy all", [{"type": "sync"}])
    _start(db, cfg, "test.runs", ["routine.ran"])
    first = routines.run(db, cfg, one)
    second = routines.run(db, cfg, every)
    held = db.next_id("routine_run")
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("routine_run", held),
        d={"routine": one, "trigger": "schedule", "status": "held", "started_at": store.now(), "results": []},
    )

    got, commit = events.follow(db, cfg, "test.runs", kinds=["routine.ran"])
    commit()
    assert [a["id"] for a in got] == [f"ran-routine_run-{first}", f"ran-routine_run-{second}"]  # held: not finished
    for a in got:
        events.check_activity(a)
    a = got[0]
    assert a["kind"] == "routine.ran" and a["type"] == "Update" and a["space"] == pods and "audience" not in a["lens"]
    assert a["actor"] == {"type": "Application", "id": f"lens:routine/{one}", "name": "Tidy pods"}
    assert a["summary"] == "Tidy pods ran"
    o = a["object"]
    assert o["id"] == f"lens:routine_run/{first}" and o["url"] == f"/routines/{one}" and o["name"] == f"Tidy pods, run {first}"
    assert (o["status"], o["trigger"], o["actions"], o["failed"]) == ("done", "manual", 1, 0)
    assert o["startTime"].endswith("Z") and o["endTime"] == a["published"]
    assert got[1]["space"] is None and got[1]["lens"]["audience"] == "admins"  # every namespace: admins

    # a held run picked: skipped is a run that ended; released runs again as a new run, so it isn't one
    routines.decide_held(db, held, run=False)
    got, commit = events.follow(db, cfg, "test.runs", kinds=["routine.ran"])
    commit()
    assert [(a["id"], a["summary"]) for a in got] == [(f"ran-routine_run-{held}", "Tidy pods was skipped (over budget)")]
    db.q("UPDATE $r SET status = 'released'", r=store.R("routine_run", held))
    assert events.get(db, cfg, f"ran-routine_run-{held}", ADMIN) is None

    page = events.page(db, cfg, ADMIN, flt={"kinds": ["routine.ran"]})["items"]
    assert [x["id"] for x in page] == [f"ran-routine_run-{second}", f"ran-routine_run-{first}"]
    reader = events.viewer(2, [pods])
    assert [x["id"] for x in events.page(db, cfg, reader, flt={"kinds": ["routine.ran"]})["items"]] == [f"ran-routine_run-{first}"]
    assert events.get(db, cfg, f"ran-routine_run-{second}", reader) is None


def test_a_budget_crossing_is_budget_warned_once(db, cfg):
    pods = store.ns_id(db, "pods")
    res = f"space:{pods}"
    budgets.put(db, res, usd=1)
    budgets.put(db, "all", usd=100)
    activity.record("out", "model.chat", [res], db=db, model="m", cost_usd=0.9)
    assert [a["state"] for a in budgets.sweep(db, cfg)] == ["near"]
    assert budgets.sweep(db, cfg) == []
    [n] = _logged(db, "budget.warned")
    a = events.check_activity(events.get(db, cfg, f"log-{n}", ADMIN))
    assert a["kind"] == "budget.warned" and a["type"] == "Announce" and a["space"] == pods and "audience" not in a["lens"]
    assert a["actor"] == events.LENS and a["summary"] == "The pods namespace has used 90% of its budget"
    o = a["object"]
    assert (o["resource"], o["state"], o["share"], o["usd"], o["cap_usd"], o["period"]) == (res, "near", 0.9, 0.9, 1, "month")
    assert o["name"] == "Budget for the pods namespace" and o["url"] == "/costs"

    activity.record("out", "model.chat", [res], db=db, model="m", cost_usd=0.2)
    assert [a["state"] for a in budgets.sweep(db, cfg)] == ["over"]
    over = events.get(db, cfg, f"log-{_logged(db, 'budget.warned')[-1]}", ADMIN)
    assert over["summary"] == "The pods namespace is over its budget" and over["object"]["state"] == "over"
    # emitted again (another process sweeping): the same activity
    b, st = budgets.get(db, res), budgets.status(db, res)
    alert = {"state": "over", "since": st["since"], "share": st["share"]}
    assert events_streams.budget_warned(db, b, alert, st) == over["id"] and len(_logged(db, "budget.warned")) == 2


def test_a_backup_taken_is_backup_finished(db, cfg, monkeypatch):
    folder = backups.folder(db)

    def fake(db_, label="manual"):
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{label}-20261015-020000.surql.gz"
        p.write_bytes(b"backup")
        return p

    monkeypatch.setattr(migrations, "backup", fake)
    got = backups.take(db)
    [n] = _logged(db, "backup.finished")
    a = events.check_activity(events.get(db, cfg, f"log-{n}", ADMIN))
    assert a["kind"] == "backup.finished" and a["type"] == "Create" and a["space"] is None and a["lens"]["audience"] == "admins"
    assert a["summary"] == f"A backup finished: {got['name']}"
    assert {k: a["object"][k] for k in ("id", "label", "ok", "kind", "bytes")} == {
        "id": f"lens:backup/{got['name']}",
        "label": "manual",
        "ok": True,
        "kind": "export",
        "bytes": 6,
    }
    assert events.get(db, cfg, f"log-{n}", events.viewer(2, [store.ns_id(db, "pods")])) is None

    # on the schedule: when it's due, once, and a failure says why
    cfg["backups"] = {**cfg["backups"], "schedule": "0 2 * * *", "timezone": "UTC"}
    t0 = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.UTC)
    assert backups.run_due(db, cfg, now=t0) is False  # works out the next time
    assert backups.run_due(db, cfg, now=t0 + dt.timedelta(days=1)) is True
    scheduled = events.get(db, cfg, f"log-{_logged(db, 'backup.finished')[-1]}", ADMIN)
    assert scheduled["summary"] == "A scheduled backup finished: scheduled-20261015-020000.surql.gz"
    assert scheduled["object"]["label"] == "scheduled" and "copied" not in scheduled["object"]  # no storage connection

    def broken(db_, label="manual"):
        raise OSError("disk full")

    monkeypatch.setattr(migrations, "backup", broken)
    assert backups.run_due(db, cfg, now=t0 + dt.timedelta(days=2)) is True
    failed = events.get(db, cfg, f"log-{_logged(db, 'backup.finished')[-1]}", ADMIN)
    assert failed["summary"] == "A scheduled backup failed: OSError: disk full"
    assert failed["object"]["ok"] is False and failed["object"]["error"] == "OSError: disk full"
    assert len(_logged(db, "backup.finished")) == 3


def test_a_new_release_is_lens_release_once(db, cfg, monkeypatch):
    from app import email

    async def quiet(*a):
        return None

    monkeypatch.setattr(email, "send_release_email", quiet)
    rel = {
        "tag": "v99.0.0",
        "name": "v99.0.0",
        "url": "https://github.com/adeelahmad/lense/releases/tag/v99.0.0",
        "published_at": "2026-10-15T09:00:00Z",
    }
    updates._put(db, latest=rel)
    assert updates.announce(db, cfg) is True
    assert updates.announce(db, cfg) is False
    [n] = _logged(db, "lens.release")
    a = events.check_activity(events.get(db, cfg, f"log-{n}", ADMIN))
    assert a["kind"] == "lens.release" and a["lens"]["audience"] == "admins" and a["actor"] == events.LENS
    assert a["summary"] == f"Lens v99.0.0 is out (this server runs {updates.current()})"
    assert a["object"]["url"] == rel["url"] and a["object"]["tag"] == "v99.0.0" and a["object"]["current"] == updates.current()
    assert events_streams.lens_release(db, rel, updates.current()) == a["id"] and len(_logged(db, "lens.release")) == 1
