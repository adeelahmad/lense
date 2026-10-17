"""The clock as a trigger: schedule triggers fire at their next time and re-arm (DST as schedule.py has it), at
triggers fire once, relative timers move with their event and skip past times, and upcoming says what's coming."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import events, schedule, store, timers

UTC = dt.UTC


def t(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


@pytest.fixture
def fired(monkeypatch):
    """automations.fire, as M6 provides it: records each (automation, alias, activity)."""
    got = []
    timers._idle["until"] = 0.0
    real = timers._provided

    def provided(module, fn):
        if (module, fn) == ("automations", "fire"):
            return lambda db, cfg, aid, alias, activity, **kw: got.append((aid, alias, activity))
        return real(module, fn)

    monkeypatch.setattr(timers, "_provided", provided)
    return got


def auto(*triggers, aid=4, enabled=True, spaces=(3,)):
    return {"id": aid, "enabled": enabled, "spaces": list(spaces), "triggers": list(triggers)}


MORNING = {"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"}
SOON = {"as": "soon", "on": "relative", "kinds": ["calendar_event.added"], "field": "object.startTime", "offset": "-30m"}


def event(start, kind="calendar_event.added", aid="create-recording-9"):
    a = events.kind_info(kind)["sample"]
    return {**a, "id": aid, "object": {**a["object"], "id": "lens:recording/9", "startTime": start}}


def test_a_schedule_trigger_fires_at_its_next_time_and_re_arms(db, cfg, fired):
    assert timers.schedule_automation(db, auto(MORNING), now=t("2026-10-15T12:00:00")) == ["sched-4-morning"]
    assert timers.get(db, "sched-4-morning")["at"] == "2026-10-16T06:00:00+00:00"  # 07:00 in London is 06:00 UTC
    assert timers.due(db, cfg, now=t("2026-10-16T05:59:00")) == 0
    assert timers.due(db, cfg, now=t("2026-10-16T06:00:03")) == 1
    [(aid, alias, act)] = fired
    assert (aid, alias) == (4, "morning") and events.check_activity(act)
    assert act["kind"] == "clock.tick" and act["actor"] == timers.CLOCK and act["space"] == 3
    assert act["object"]["at"] == "2026-10-16T06:00:00.000Z" and act["object"]["local"] == "Fri 16 Oct at 07:00"
    assert act["object"]["description"] == "every weekday at 07:00" and act["object"]["late"] is False
    assert act["published"] == act["object"]["at"] and act["id"] == "tick-4-morning-1792130400"
    assert timers.get(db, "sched-4-morning")["at"] == "2026-10-19T06:00:00+00:00"  # Friday, then Monday
    assert timers.due(db, cfg, now=t("2026-10-17T06:00:00")) == 0


def test_schedules_follow_dst_as_schedule_py_does(db, cfg, fired):
    gap = {"as": "early", "on": "schedule", "cron": "30 2 * * *", "timezone": "Europe/Stockholm"}
    repeat = {"as": "night", "on": "schedule", "cron": "30 1 * * *", "timezone": "America/New_York"}
    timers.schedule_automation(db, auto(gap, aid=5), now=t("2027-03-27T12:00:00"))
    timers.schedule_automation(db, auto(repeat, aid=6), now=t("2026-10-31T12:00:00"))
    # 02:30 doesn't happen in Stockholm that night: it runs when the clock gets past the gap (03:00 = 01:00 UTC)
    assert timers.get(db, "sched-5-early")["at"] == "2027-03-28T01:00:00+00:00"
    # 01:30 happens twice in New York on 1 November: it runs once, at the first
    assert timers.get(db, "sched-6-night")["at"] == "2026-11-01T05:30:00+00:00"
    assert timers.due(db, cfg, now=t("2026-11-01T05:30:01")) == 1
    assert timers.due(db, cfg, now=t("2026-11-01T06:30:01")) == 0
    assert timers.get(db, "sched-6-night")["at"] == _iso(schedule.next_after("30 1 * * *", "America/New_York", t("2026-11-01T05:31:00")))


def _iso(d):
    return d.isoformat(timespec="seconds")


def test_saving_again_keeps_changes_and_calls_off(db, cfg, fired):
    at = {"as": "once", "on": "at", "at": "2026-10-20T09:00", "timezone": "Europe/London"}
    a = auto(MORNING, at, SOON)
    noon = t("2026-10-15T12:00:00")
    assert timers.schedule_automation(db, a, now=noon) == ["at-4-once", "sched-4-morning"]
    assert timers.get(db, "at-4-once")["at"] == "2026-10-20T08:00:00+00:00"
    timers.relative(db, 4, "soon", event("2026-10-20T18:00:00+01:00"), "object.startTime", "-30m", now=noon)
    # Lens off past 07:00 Friday: saving again then leaves the timer due, so it can still catch up
    timers.schedule_automation(db, a, now=t("2026-10-16T06:30:00"))
    assert timers.get(db, "sched-4-morning")["at"] == "2026-10-16T06:00:00+00:00"
    assert timers.due(db, cfg, now=t("2026-10-16T06:30:00")) == 1 and fired[0][2]["object"]["late"] is True
    # a new schedule, the at trigger dropped, the relative offset changed: the timers follow
    later = {**MORNING, "cron": "0 8 * * *"}
    timers.schedule_automation(db, auto(later, {**SOON, "offset": "-1h"}), now=t("2026-10-16T06:45:00"))
    assert timers.get(db, "sched-4-morning")["at"] == "2026-10-16T07:00:00+00:00"  # 08:00 London
    assert timers.get(db, "at-4-once")["status"] == "cancelled"
    assert timers.get(db, "rel-4-soon-lens:recording/9")["at"] == "2026-10-20T16:00:00+00:00"
    # the relative trigger removed: its timers go; switched off: every timer goes
    timers.schedule_automation(db, auto(later), now=t("2026-10-16T07:00:00"))
    assert timers.get(db, "rel-4-soon-lens:recording/9")["status"] == "cancelled"
    assert timers.schedule_automation(db, auto(later, enabled=False)) == []
    assert timers.get(db, "sched-4-morning")["status"] == "cancelled" and timers.upcoming(db, 4) == []


def test_an_at_trigger_fires_once_then_reads_done(db, cfg, fired):
    at = {"as": "once", "on": "at", "at": "2026-10-20T09:00", "timezone": "Europe/London"}
    a = auto(at)
    timers.schedule_automation(db, a, now=t("2026-10-15T12:00:00"))
    assert timers.clock_state(db, a, now=t("2026-10-15T12:00:00"))["once"] == {
        "status": "waiting",
        "at": "2026-10-20T08:00:00+00:00",
        "description": "once",
    }
    assert timers.due(db, cfg, now=t("2026-10-20T08:00:01")) == 1 and fired[0][2]["object"]["description"] == "once"
    assert timers.get(db, "at-4-once")["status"] == "fired"
    assert timers.schedule_automation(db, a, now=t("2026-10-20T09:00:00")) == []  # saved again afterwards: not re-armed
    assert timers.get(db, "at-4-once")["status"] == "fired"
    assert timers.clock_state(db, a, now=t("2026-10-20T09:00:00"))["once"]["status"] == "done"
    assert timers.clock_state(db, {**a, "enabled": False})["once"]["status"] == "off"
    # one already past when it's saved is never set, and reads done
    past = auto({**at, "as": "gone", "at": "2026-10-01T09:00:00+00:00"}, aid=9)
    assert timers.schedule_automation(db, past, now=t("2026-10-15T12:00:00")) == []
    assert timers.clock_state(db, past, now=t("2026-10-15T12:00:00"))["gone"]["status"] == "done"


def test_a_relative_timer_moves_when_its_event_moves(db, cfg, fired):
    noon = t("2026-10-15T12:00:00")
    got = timers.relative(db, 4, "soon", event("2026-10-20T18:00:00+01:00"), "object.startTime", "-30m", now=noon)
    assert got == {"key": "rel-4-soon-lens:recording/9", "at": "2026-10-20T16:30:00+00:00", "set": True, "note": None}
    moved = event("2026-10-21T18:00:00+01:00", kind="calendar_event.changed", aid="log-13")
    timers.relative(db, 4, "soon", moved, "object.startTime", "-30m", now=noon)
    assert db.one("SELECT count() AS n FROM timer GROUP ALL")["n"] == 1  # one timer per event, at its new time
    assert timers.due(db, cfg, now=t("2026-10-20T16:31:00")) == 0
    assert timers.due(db, cfg, now=t("2026-10-21T16:30:02")) == 1
    [(aid, alias, act)] = fired
    assert (aid, alias, act["id"]) == (4, "soon", "log-13")  # fired with the activity that set it
    # a date is its midnight in UTC; a time a few minutes past is due now; further past is skipped, with a note
    assert timers.relative(db, 4, "soon", event("2026-11-02"), "object.startTime", "0s", now=noon)["at"] == "2026-11-02T00:00:00+00:00"
    assert timers.relative(db, 4, "soon", event("2026-10-15T12:28:00+00:00"), "object.startTime", "-30m", now=noon)["set"]
    skipped = timers.relative(db, 4, "soon", event("2026-10-15T12:20:00+00:00"), "object.startTime", "-30m", now=noon)
    assert skipped["set"] is False and "had already passed" in skipped["note"]
    assert timers.get(db, "rel-4-soon-lens:recording/9")["status"] == "cancelled"  # the timer it had is called off
    none = timers.relative(db, 4, "soon", event(None), "object.startTime", "-30m", now=noon)
    assert none["set"] is False and "has no time at object.startTime" in none["note"]
    with pytest.raises(ValueError, match="offset"):
        timers.relative(db, 4, "soon", event("2026-10-20T18:00:00Z"), "object.startTime", "soon", now=noon)


def test_upcoming_says_what_is_coming(db, cfg, fired):
    noon = t("2026-10-15T12:00:00")
    timers.schedule_automation(db, auto(MORNING, {"as": "once", "on": "at", "at": "2027-01-04T09:00:00Z"}), now=noon)
    timers.relative(db, 4, "soon", event("2026-10-20T18:00:00+01:00"), "object.startTime", "-30m", now=noon)
    got = timers.upcoming(db, 4, now=noon)
    assert [(u["key"], u["trigger"], u["untrusted"]) for u in got] == [
        ("sched-4-morning", "morning", False),
        ("rel-4-soon-lens:recording/9", "soon", True),
        ("at-4-once", "once", False),
    ]
    assert [u["description"] for u in got] == [
        "Every weekday at 07:00: next on Fri 16 Oct at 07:00 (Europe/London)",
        "30 minutes before “Parents' evening” starts: on Tue 20 Oct at 16:30 (UTC)",
        "Once: next on Mon 4 Jan 2027 at 09:00 (UTC)",
    ]
    assert len(timers.upcoming(db, 4, n=1, now=noon)) == 1 and timers.upcoming(db, 5) == []


def test_before_automations_are_stored_a_schedule_still_re_arms(db, cfg, monkeypatch):
    real = timers._provided
    monkeypatch.setattr(timers, "_provided", lambda module, fn: None if module == "automations" else real(module, fn))
    timers._idle["until"] = 0.0
    timers.schedule_automation(db, auto(MORNING), now=t("2026-10-15T12:00:00"))
    assert timers.due(db, cfg, now=t("2026-10-16T06:00:00")) == 1
    row = timers.get(db, "sched-4-morning")
    assert row["status"] == "waiting" and "aren't stored yet" in row["last"]["error"]


def test_the_clock_is_a_kind_that_is_never_stored(db):
    k = events.kind_info("clock.tick")
    assert k["source"] == "clock" and k["noisy"] and k["type"] == "Arrive"
    assert events.field_at("clock.tick", "object.at")["title"] == "Time"
    assert "clock" not in {s.name for s in events.sources()}  # nothing reads it from the database
    assert store.R("timer", "x")  # the table is in the schema
    assert db.rows("INFO FOR TABLE timer")


def test_schedules_in_words():
    assert schedule.describe("0 7 * * 1-5") == "every weekday at 07:00"
    assert schedule.describe("30 8 * * sat,sun") == "every Saturday and Sunday at 08:30"
    assert schedule.describe("0 9 * * mon") == "every Monday at 09:00"
    assert schedule.describe("0 9 * * 7") == "every Sunday at 09:00"
    assert schedule.describe("0 9 20 * *") == "at 09:00 on the 20th of every month"
    assert schedule.describe("0 9 22 * *") == "at 09:00 on the 22nd of every month"
    assert schedule.describe("0 9 11 * *") == "at 09:00 on the 11th of every month"
    assert schedule.describe("0 9 * * 1,3") == "0 9 * * 1,3"
