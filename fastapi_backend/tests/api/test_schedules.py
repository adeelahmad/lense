"""Schedules (docs/schedules.md): everything that runs on a clock in one list, like a crontab (routines, automations'
schedule and at triggers, the scheduled backup), each changed, switched and run from there through its own checks; and
the same for agents over MCP."""

from __future__ import annotations

import datetime as dt

from app.domain import routines, store, timers
from tests.api.test_automation_tools import tool, tool_error
from tests.helpers import login, make_user

SAY = {
    "nodes": [
        {"id": "i", "type": "input"},
        {"id": "a", "type": "action", "config": {"action": "log_line", "params": {"text": "It is {{ event.object.local }}"}}},
    ],
    "edges": [{"source": "i", "target": "a"}],
}


def _env(client, new_client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    db.q("UPDATE routine SET enabled = false")  # the routines Lens starts with aren't what these are about
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    hv = login(new_client(), "vi@x.io", "viewer password 1")
    pods = store.ns_id(db, "pods")
    r = client.post("/api/v1/routines", headers=h, json={"name": "Sync nightly", "schedule": "0 3 * * *", "actions": [{"type": "sync"}]})
    assert r.status_code == 200, r.text
    soon = (dt.datetime.now(dt.UTC) + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    triggers = [
        {"as": "morning", "on": "schedule", "cron": "0 7 * * *", "timezone": "Europe/London"},
        {"as": "once", "on": "at", "at": soon, "timezone": "UTC"},
        {"as": "new", "on": "event", "kinds": ["asset.added"]},
    ]
    a = client.post("/api/v1/automations", headers=he, json={"name": "Mornings", "triggers": triggers, "graph": SAY, "spaces": [pods]})
    assert a.status_code == 200, a.text
    return {"h": h, "he": he, "hv": hv, "routine": r.json()["id"], "auto": a.json()["id"], "soon": soon}


def _ids(client, h):
    r = client.get("/api/v1/schedules", headers=h)
    assert r.status_code == 200, r.text
    return {s["id"]: s for s in r.json()["schedules"]}


def test_one_list_holds_every_routine_automation_schedule_and_the_backup(client, new_client, db):
    env = _env(client, new_client, db)
    rid, aid = env["routine"], env["auto"]
    got = _ids(client, env["h"])
    # admins: every routine (those switched off and run by hand too), each clock trigger of every automation, the backup
    assert {f"routine:{rid}", f"automation:{aid}:morning", f"automation:{aid}:once", "backups"} <= set(got)
    assert f"automation:{aid}:new" not in got  # what happens isn't a clock
    assert len([s for s in got if s.startswith("routine:")]) == len(routines.list_routines(db))
    r = got[f"routine:{rid}"]
    assert (r["kind"], r["text"], r["cron"], r["enabled"], r["state"], r["href"]) == (
        "routine",
        "every day at 03:00",
        "0 3 * * *",
        True,
        "on",
        f"/routines/{rid}",
    )
    m = got[f"automation:{aid}:morning"]
    assert m["text"] == "every day at 07:00" and m["timezone"] == "Europe/London" and m["state"] == "waiting"
    assert m["next_run_at"] == timers.get(db, f"sched-{aid}-morning")["at"] and m["namespace_names"] == ["pods"]
    once = got[f"automation:{aid}:once"]
    assert once["on"] == "at" and once["text"].startswith("once, on ") and once["next_run_at"]
    b = got["backups"]
    assert (b["enabled"], b["text"], b["next_run_at"], b["href"]) == (False, "not scheduled", None, "/settings/backups")
    # soonest first; what has no next time after
    order = [s["next_run_at"] for s in client.get("/api/v1/schedules", headers=env["h"]).json()["schedules"]]
    times = [t for t in order if t]
    assert times == sorted(times) and order[: len(times)] == times
    # anyone else: the clock triggers of the automations they manage, and nothing of the admins'
    mine = _ids(client, env["he"])
    assert set(mine) == {f"automation:{aid}:morning", f"automation:{aid}:once"}
    assert client.get(f"/api/v1/schedules/routine:{rid}", headers=env["he"]).status_code == 404
    assert client.get("/api/v1/schedules/backups", headers=env["he"]).status_code == 404
    assert _ids(client, env["hv"]) == {}  # a viewer manages no automation
    assert client.get(f"/api/v1/schedules/automation:{aid}:morning", headers=env["hv"]).status_code == 404
    assert client.get("/api/v1/schedules/nonsense", headers=env["h"]).status_code == 404


def test_schedules_are_changed_switched_and_run_from_the_list(client, new_client, db):
    env = _env(client, new_client, db)
    rid, aid, h, he = env["routine"], env["auto"], env["h"], env["he"]

    def patch(sid, hh, **body):
        return client.patch(f"/api/v1/schedules/{sid}", headers=hh, json=body)

    # a routine: a new time, then only by hand, then off
    r = patch(f"routine:{rid}", h, cron="30 2 * * 1-5", timezone="Europe/Stockholm")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "every weekday at 02:30" and r.json()["timezone"] == "Europe/Stockholm" and r.json()["next_run_at"]
    assert routines.get(db, rid)["schedule"] == "30 2 * * 1-5"
    assert "five fields" in patch(f"routine:{rid}", h, cron="daily").json()["detail"]
    got = patch(f"routine:{rid}", h, cron=None).json()
    assert (got["on"], got["text"], got["next_run_at"]) == ("manual", "only when run by hand", None)
    assert patch(f"routine:{rid}", h, enabled=False).json()["state"] == "off"
    assert patch(f"routine:{rid}", he, enabled=True).status_code == 404  # routines are the admins'
    assert patch(f"routine:{rid}", h).status_code == 400
    # run now, even when off: it starts when the scheduler next looks
    r = client.post(f"/api/v1/schedules/routine:{rid}/run", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "asked" and routines.get(db, rid)["run_now"]

    # an automation's schedule: moved, through the automation's own checks; its timer follows
    m = f"automation:{aid}:morning"
    got = patch(m, he, cron="30 6 * * 1-5").json()
    assert got["text"] == "every weekday at 06:30" and got["timezone"] == "Europe/London"
    assert timers.get(db, f"sched-{aid}-morning")["payload"]["cron"] == "30 6 * * 1-5"
    assert got["next_run_at"] == timers.get(db, f"sched-{aid}-morning")["at"]
    assert "never runs" in patch(m, he, cron="0 0 31 feb *").json()["detail"]
    assert "repeats" in patch(m, he, at="2030-01-01T07:00").json()["detail"]
    later = (dt.datetime.now(dt.UTC) + dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
    once = patch(f"automation:{aid}:once", he, at=later).json()
    assert once["at"].startswith(later) and once["next_run_at"].startswith(later)
    assert patch(m, env["hv"], enabled=False).status_code == 404  # a viewer doesn't manage it
    # switched off: the whole automation, and its timers are called off
    off = patch(m, he, enabled=False).json()
    assert (off["enabled"], off["state"], off["next_run_at"]) == (False, "off", None)
    assert timers.get(db, f"sched-{aid}-morning")["status"] == "cancelled"
    # run now: here, as the schedule would when its time comes
    r = client.post(f"/api/v1/schedules/{m}/run", headers=he)
    assert r.status_code == 200, r.text
    ran = r.json()
    assert ran["status"] == "done" and ran["run"]
    run = client.get(f"/api/v1/automations/runs/{ran['run']}", headers=he).json()
    assert run["trigger"] == "morning" and run["intents"][0]["result"]["line"].startswith("It is ")
    assert _ids(client, he)[m]["last_status"] == "done"
    assert patch(m, he, enabled=True).json()["state"] == "waiting"

    # the backup: given a schedule it's on; switched off it has none; on again needs one
    got = patch("backups", h, cron="0 4 * * *", timezone="Europe/London").json()
    assert (got["enabled"], got["text"], got["timezone"]) == (True, "every day at 04:00", "Europe/London") and got["next_run_at"]
    assert patch("backups", h, enabled=False).json()["enabled"] is False
    assert "give the backup a schedule" in patch("backups", h, enabled=True).json()["detail"]
    assert "backups.schedule" in patch("backups", h, cron="nope").json()["detail"]
    assert patch("backups", he, cron="0 4 * * *").status_code == 404
    # taken now through Settings → Backups' own route (an in-memory database has nothing to copy)
    r = client.post("/api/v1/schedules/backups/run", headers=h)
    assert r.status_code in (200, 400) and ("in-memory" in r.text or r.json()["backup"])


def test_agents_list_change_and_run_schedules_over_mcp(client, new_client, db):
    env = _env(client, new_client, db)
    aid, he = env["auto"], env["he"]
    m = f"automation:{aid}:morning"
    got = tool(client, he, "schedules")["schedules"]
    assert [s["id"] for s in got] == [f"automation:{aid}:once", m] or [s["id"] for s in got] == [m, f"automation:{aid}:once"]
    assert tool(client, env["h"], "schedules", kind="backups")["schedules"][0]["id"] == "backups"
    changed = tool(client, he, "set_schedule", schedule_id=m, cron="0 8 * * 1-5", timezone="UTC")
    assert changed["text"] == "every weekday at 08:00" and changed["timezone"] == "UTC"
    assert "say what changes" in tool_error(client, he, "set_schedule", schedule_id=m)
    assert "switch it off instead" in tool_error(client, he, "set_schedule", schedule_id=m, by_hand=True)
    assert "not found" in tool_error(client, he, "set_schedule", schedule_id=f"routine:{env['routine']}", enabled=False)
    by_hand = tool(client, env["h"], "set_schedule", schedule_id=f"routine:{env['routine']}", by_hand=True)
    assert by_hand["on"] == "manual"
    ran = tool(client, he, "run_schedule", schedule_id=m)
    assert ran["status"] == "done" and ran["run"]
    assert "not found" in tool_error(client, env["hv"], "run_schedule", schedule_id=m)
