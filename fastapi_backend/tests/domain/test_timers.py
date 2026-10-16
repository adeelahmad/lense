"""Timers: set, replaced and called off; fired once when due, across two processes; repeating ones re-armed; late
and missed ones after Lens was off; the per-automation cap; the tidy; and the thread."""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from app.domain import store, timers

UTC = dt.UTC
T0 = dt.datetime(2026, 10, 16, 7, 0, tzinfo=UTC)


def at(minutes=0, seconds=0, days=0):
    return T0 + dt.timedelta(minutes=minutes, seconds=seconds, days=days)


@pytest.fixture
def fired(monkeypatch):
    """A handler kind test.ping that records what it was given, and test.hourly that repeats every hour."""
    got = []
    ended = []
    monkeypatch.setattr(timers, "_HANDLERS", dict(timers._HANDLERS))
    timers._idle["until"] = 0.0

    def hourly(payload, after):
        return after.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)

    timers.handler("test.ping", ended=lambda db, p, status, why: ended.append((p, status, why)))(lambda db, cfg, p: got.append(p))
    timers.handler("test.hourly", again=hourly)(lambda db, cfg, p: got.append(p))
    timers.handler("test.boom")(lambda db, cfg, p: 1 / 0)
    return got, ended


def test_a_timer_fires_once_when_due(db, cfg, fired):
    got, _ = fired
    t = timers.set(db, "ping-1", at(5), "test.ping", {"n": 1}, automation=4)
    assert t["at"] == "2026-10-16T07:05:00+00:00" and t["status"] == "waiting"
    assert timers.due(db, cfg, now=at(4, 59)) == 0 and got == []
    assert timers.due(db, cfg, now=at(5, 3)) == 1
    assert got == [{"n": 1, "late": False, "timer": {"key": "ping-1", "at": "2026-10-16T07:05:00+00:00", "automation": 4}}]
    assert timers.due(db, cfg, now=at(6)) == 0 and len(got) == 1
    row = timers.get(db, "ping-1")
    assert row["status"] == "fired" and row["runs"] == 1 and row["last"]["ok"] is True


def test_two_processes_fire_a_timer_once(db, cfg, fired):
    got, _ = fired
    timers.set(db, "ping-2", at(), "test.ping", {})
    rows = db.rows(f"SELECT {timers.FIELDS} FROM timer WHERE status = 'waiting'")
    # the worker and the API both read it as due; only one claim wins
    assert [timers._fire(db, cfg, r, at(0, 2)) for r in rows + rows] == [True, False]
    done = []
    timers.set(db, "ping-3", at(), "test.ping", {})
    rows = db.rows(f"SELECT {timers.FIELDS} FROM timer WHERE status = 'waiting'")
    threads = [threading.Thread(target=lambda: done.append(timers._fire(db, cfg, rows[0], at(0, 2)))) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(done) == [False, False, False, True] and len(got) == 2


def test_set_replaces_cancel_calls_off(db, cfg, fired):
    got, ended = fired
    timers.set(db, "ping-4", at(5), "test.ping", {"v": 1}, automation=4)
    timers.set(db, "ping-4", at(10), "test.ping", {"v": 2}, automation=4)  # moved: one timer, the new time
    assert db.one("SELECT count() AS n FROM timer GROUP ALL")["n"] == 1
    assert timers.due(db, cfg, now=at(6)) == 0
    assert timers.due(db, cfg, now=at(10)) == 1 and got[0]["v"] == 2
    timers.set(db, "ping-4", at(20), "test.ping", {"v": 3}, automation=4)  # set again after it fired: waits again
    assert timers.get(db, "ping-4")["status"] == "waiting"
    assert timers.cancel(db, "ping-4") is True and timers.cancel(db, "ping-4") is False
    assert timers.get(db, "ping-4")["status"] == "cancelled" and ended[-1][1:] == ("cancelled", "cancelled")
    assert timers.due(db, cfg, now=at(30)) == 0 and len(got) == 1
    for n in range(3):
        timers.set(db, f"ping-5-{n}", at(5), "test.ping", {}, automation=5)
    timers.set(db, "ping-6", at(5), "test.ping", {}, automation=6)
    assert timers.cancel_for(db, 5, prefix="ping-5-0") == 1
    assert timers.cancel_for(db, 5) == 2 and timers.cancel_for(db, 5) == 0
    assert ended[-1][1:] == ("cancelled", "the automation was switched off")
    assert timers.get(db, "ping-6")["status"] == "waiting"


def test_what_isnt_a_timer_is_refused(db, fired):
    for args, msg in (
        (("bad key!", at(), "test.ping"), "key"),
        (("k", "soon", "test.ping"), "at is a time"),
        (("k", at(), "Ping"), "kind"),
        (("k", at(), "test.ping", {"x": object()}), "plain JSON"),
        (("k", at(), "test.ping", {"x": "y" * 70_000}), "at most 64 KB"),
    ):
        with pytest.raises(ValueError, match=msg):
            timers.set(db, *args)


def test_unknown_kinds_and_failures_are_noted(db, cfg, fired):
    timers.set(db, "who-1", at(), "test.nobody", {})
    timers.set(db, "boom-1", at(), "test.boom", {})
    logged = []
    assert timers.due(db, cfg, now=at(0, 1), log=logged.append) == 1  # boom fired (and failed); nobody isn't handled
    assert timers.get(db, "who-1")["status"] == "failed" and "nothing handles" in timers.get(db, "who-1")["note"]
    boom = timers.get(db, "boom-1")
    assert boom["status"] == "failed" and "ZeroDivisionError" in boom["note"] and boom["last"]["ok"] is False
    assert logged and "ZeroDivisionError" in logged[0]


def test_a_repeating_timer_is_re_armed_as_it_is_claimed(db, cfg, fired):
    got, _ = fired
    timers.set(db, "hourly", at(), "test.hourly", {"h": True}, automation=4)
    assert timers.due(db, cfg, now=at(0, 4)) == 1
    row = timers.get(db, "hourly")
    assert row["status"] == "waiting" and row["at"] == "2026-10-16T08:00:00+00:00" and row["last"]["at"] == "2026-10-16T07:00:00+00:00"
    assert timers.due(db, cfg, now=at(59)) == 0
    assert timers.due(db, cfg, now=at(60, 1)) == 1 and [p["timer"]["at"][11:16] for p in got] == ["07:00", "08:00"]


def test_late_and_missed_after_lens_was_off(db, cfg, fired):
    got, ended = fired
    db.q("CREATE $r CONTENT {name: 'School emails'}", r=store.R("automation", 4))
    timers.set(db, "late", at(), "test.ping", {}, automation=4)
    timers.set(db, "old", at(-90), "test.ping", {}, automation=4)
    assert timers.due(db, cfg, now=at(45)) == 1  # 45 minutes late: within the hour of catch-up
    assert got[0]["late"] is True and got[0]["timer"]["key"] == "late"
    old = timers.get(db, "old")
    assert old["status"] == "missed" and ended[-1][1] == "missed"
    assert db.one("SELECT missed FROM $r", r=store.R("automation", 4))["missed"] == 1
    # catch-up 0s: only timers within LATE_SECONDS of their time fire
    cfg = {**cfg, "automations": {"catch_up": "0s"}}
    timers.set(db, "soon", at(100), "test.ping", {})
    timers.set(db, "later", at(100), "test.ping", {})
    assert timers.due(db, cfg, now=at(101)) == 2
    timers.set(db, "much-later", at(110), "test.ping", {})
    assert timers.due(db, cfg, now=at(120)) == 0 and timers.get(db, "much-later")["status"] == "missed"


def test_a_repeating_timer_fires_its_latest_missed_time_once(db, cfg, fired):
    got, _ = fired
    timers.set(db, "hourly", at(), "test.hourly", {}, automation=4)
    # off from 07:00 to 10:30: 07:00, 08:00, 09:00 and 10:00 were missed; 10:00 is within the hour, so it fires once
    assert timers.due(db, cfg, now=at(210)) == 1
    assert [p["timer"]["at"][11:16] for p in got] == ["10:00"] and got[0]["late"] is True
    assert timers.get(db, "hourly")["at"] == "2026-10-16T11:00:00+00:00"
    # with ten minutes of catch-up, 11:00 is missed at 11:30 and the timer waits for 12:00
    cfg = {**cfg, "automations": {"catch_up": "10m"}}
    assert timers.due(db, cfg, now=at(270)) == 0 and len(got) == 1
    row = timers.get(db, "hourly")
    assert row["status"] == "waiting" and row["at"] == "2026-10-16T12:00:00+00:00" and "missed" in row["note"]


def test_an_automation_has_at_most_so_many_timers_waiting(db, cfg, fired, monkeypatch):
    monkeypatch.setattr(timers, "MAX_WAITING", 2)
    timers.set(db, "a-1", at(5), "test.ping", {}, automation=7)
    timers.set(db, "a-2", at(5), "test.ping", {}, automation=7)
    timers.set(db, "a-2", at(6), "test.ping", {}, automation=7)  # replacing a waiting one is fine
    with pytest.raises(ValueError, match="already has 2 timers waiting"):
        timers.set(db, "a-3", at(5), "test.ping", {}, automation=7)
    timers.set(db, "b-1", at(5), "test.ping", {}, automation=8)  # another automation's count is its own
    assert timers.due(db, cfg, now=at(5)) == 2
    timers.set(db, "a-3", at(9), "test.ping", {}, automation=7)


def test_tidy_drops_timers_that_ended_a_week_ago(db, cfg, fired):
    timers.set(db, "done", at(), "test.ping", {})
    timers.set(db, "off", at(), "test.ping", {})
    timers.set(db, "waits", at(days=30), "test.ping", {})
    timers.due(db, cfg, now=at(1))
    timers.cancel(db, "off")
    assert timers.tidy(db, cfg, now=at(days=6)) and timers.get(db, "done")
    timers.tidy(db, cfg, now=dt.datetime.now(UTC) + dt.timedelta(days=8))
    assert timers.get(db, "done") is None and timers.get(db, "off") is None and timers.get(db, "waits")
    timers._swept["at"] = 0.0
    assert timers.tidy(db, cfg) and timers.tidy(db, cfg) == 0  # once an hour


def test_no_query_while_no_timer_waits(db, cfg, fired, monkeypatch):
    got, _ = fired
    assert timers.due(db, cfg, now=at()) == 0 and timers._idle["until"] > 0
    calls = []
    real = db.rows
    monkeypatch.setattr(db, "rows", lambda *a, **k: calls.append(a) or real(*a, **k))
    row = {"key": "x", "at": "2026-10-16T07:00:00+00:00", "kind": "test.ping", "status": "waiting"}
    db.q("CREATE $r CONTENT $d", r=timers._ref("x"), d=row)
    assert timers.due(db, cfg, now=at(1)) == 0 and calls == []  # another process's timer: seen within a minute
    timers.set(db, "mine", at(), "test.ping", {})  # one set here is seen at once
    assert timers.due(db, cfg, now=at(1)) == 2 and len(got) == 2


def test_the_thread_fires_what_is_due_and_stops(db, cfg, fired):
    got, _ = fired
    stop = threading.Event()
    th = timers.start(db, lambda: cfg, stop)
    try:
        timers.set(db, "now", dt.datetime.now(UTC), "test.ping", {"n": 9})  # due now: wakes the thread
        for _ in range(100):
            if got:
                break
            stop.wait(0.05)
    finally:
        stop.set()
        th.join(5)
    assert got and got[0]["n"] == 9 and not th.is_alive()


def test_catch_up_is_a_setting(app, client, db):
    from tests.helpers import login, make_user

    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    assert timers.catch_up(app.state.settings.current()) == 3600
    assert client.put("/api/v1/settings/automations", json={"catch_up": "15m"}, headers=h).status_code == 200
    assert timers.catch_up(app.state.settings.current()) == 900
    assert client.get("/api/v1/settings", headers=h).json()["automations"]["values"]["catch_up"] == "15m"
    for bad in ({"catch_up": "8d"}, {"catch_up": 60}, {"catch_up": "-1h"}, {"catch_up": "soon"}, {"grace": "1h"}):
        assert client.put("/api/v1/settings/automations", json=bad, headers=h).status_code == 400, bad
