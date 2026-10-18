"""Joins (docs/automations.md, Joins): how several triggers make one run. all waits for every trigger within a window
(on the same key), count for n of one trigger, absent for one trigger not followed by another in time (with a fake
timer), batch collects for a while; the state they keep is capped, expires, survives two writers at once, and each
join reads in words."""

from __future__ import annotations

import types

import pytest

from app.domain import automations, match

T0 = "2026-10-15T16:00:00Z"


def at(minutes):
    """A published time, `minutes` after T0."""
    h, m = divmod(16 * 60 + minutes, 60)
    return f"2026-10-15T{h:02d}:{m:02d}:00Z"


def act(aid, minutes, sender="office@stmarys.org", **obj):
    return {"id": aid, "kind": "x.y", "published": at(minutes), "object": {"from": [sender], **obj}, "summary": aid}


def auto(join, *aliases, aid=4):
    return {"id": aid, "join": join, "triggers": [{"as": a} for a in aliases or ("mail", "cal")]}


@pytest.fixture
def clock(monkeypatch):
    """Fake timers and a fake automations.fire, recording what they were asked."""
    timers = types.SimpleNamespace(set_=[], cancelled=[])
    timers.set = lambda db, key, when, kind, payload, automation=None: timers.set_.append((key, when, kind, payload))
    timers.cancel = lambda db, key, why="": timers.cancelled.append(key) or True
    fired = []
    monkeypatch.setattr(match, "_timers", lambda: timers)
    monkeypatch.setattr(
        automations,
        "fire",
        lambda db, cfg, aid, alias, activity, events=None, dry=False: fired.append((aid, alias, activity, events)) or 51,
    )
    timers.fired = fired
    return timers


def step(db, automation, alias, a):
    return match.join_step(db, {}, automation, alias, a)


def test_any_and_triggers_a_join_doesnt_name_fire_at_once(db):
    a = act("m1", 0)
    assert step(db, auto({"mode": "any"}), "mail", a) == [{"as": "mail", "events": {"mail": a}, "event": a}]
    count = auto({"mode": "count", "of": "mail", "at_least": 2, "within": "5m"})
    assert step(db, count, "cal", a) == [{"as": "cal", "events": {"cal": a}, "event": a}]
    with pytest.raises(ValueError, match="the join's of is the name of one of the triggers"):
        step(db, auto({"mode": "count", "of": "nope", "at_least": 2, "within": "5m"}), "mail", a)
    with pytest.raises(ValueError, match="a join needs the automation's triggers"):
        step(db, {"id": 4, "join": {"mode": "any"}}, "mail", a)


def test_all_waits_for_every_trigger_within_the_window_on_the_same_key(db):
    j = auto({"mode": "all", "within": "1d", "key": "object.from.0"})
    m1, c_other, c1 = act("m1", 0), act("c0", 10, sender="club@example.com"), act("c1", 30)
    assert step(db, j, "mail", m1) == []
    assert step(db, j, "cal", c_other) == []  # another sender: its own key
    done = step(db, j, "cal", c1)
    assert done == [{"as": "cal", "events": {"mail": match._small(m1), "cal": c1}, "event": c1}]
    assert step(db, j, "mail", act("m2", 40)) == []  # it starts again
    # too far apart: the older one is dropped
    late = auto({"mode": "all", "within": "10m"}, aid=5)
    assert step(db, late, "mail", act("m3", 0)) == []
    assert step(db, late, "cal", act("c3", 20)) == []
    assert step(db, late, "mail", act("m4", 25))[0]["events"]["cal"]["id"] == "c3"
    assert step(db, j, "mail", {"id": "nokey", "published": T0}) == []  # nothing at the key: nothing to pair with


def test_count_fires_once_per_crossing_then_counts_again(db):
    j = auto({"mode": "count", "of": "login", "at_least": 3, "within": "5m", "key": "object.from.0"}, "login")
    assert step(db, j, "login", act("l1", 0)) == []
    assert step(db, j, "login", act("l2", 1)) == []
    assert step(db, j, "login", act("x1", 1, sender="other")) == []
    done = step(db, j, "login", act("l3", 2))
    assert len(done) == 1 and [a["id"] for a in done[0]["events"]["login"]] == ["l1", "l2", "l3"]
    assert done[0]["event"]["id"] == "l3"
    assert step(db, j, "login", act("l4", 3)) == []  # reset: counting starts over
    assert step(db, j, "login", act("l5", 4)) == []
    assert step(db, j, "login", act("l6", 20)) == []  # l4 and l5 are past the window
    assert step(db, j, "login", act("l7", 21)) == []
    assert [a["id"] for a in step(db, j, "login", act("l8", 22))[0]["events"]["login"]] == ["l6", "l7", "l8"]


def test_absent_waits_with_a_timer_and_the_missing_one_cancels_it(db, cfg, clock):
    j = auto({"mode": "absent", "after": "mail", "missing": "reply", "within": "2d", "key": "object.thread"}, "mail", "reply")
    first = act("m1", 0, thread="t1")
    assert step(db, j, "mail", first) == []
    assert step(db, j, "mail", act("m2", 5, thread="t1")) == []  # already waiting: it keeps the first
    assert len(clock.set_) == 1
    key, when, kind, payload = clock.set_[0]
    assert key.startswith("absent-4-") and when == "2026-10-17T16:00:00+00:00" and kind == "join.absent"
    # a reply on another thread changes nothing; one on this thread, after the mail, cancels the timer
    assert step(db, j, "reply", act("r0", 10, thread="t2")) == [] and clock.cancelled == []
    assert step(db, j, "reply", act("r1", 60, thread="t1")) == []
    assert clock.cancelled == [key]
    assert match.absent_fired(db, cfg, payload) is None and clock.fired == []  # nothing left to complete
    # no reply in time: the timer runs the automation with the first mail
    assert step(db, j, "mail", act("m3", 70, thread="t3")) == []
    assert step(db, j, "reply", act("r_before", 65, thread="t3")) == []  # a reply from before the mail doesn't count
    payload = clock.set_[-1][3]
    assert match.absent_fired(db, cfg, payload) == 51
    assert clock.fired == [(4, "mail", match._small(act("m3", 70, thread="t3")), {"mail": match._small(act("m3", 70, thread="t3"))})]
    assert match.absent_fired(db, cfg, payload) is None  # fired once


def test_batch_collects_then_runs_once(db, cfg, clock, monkeypatch):
    monkeypatch.setattr(match, "MAX_BATCH", 3)
    j = auto({"mode": "batch", "within": "1h", "of": "alert"}, "alert", "other")
    assert step(db, j, "other", act("o1", 0))[0]["event"]["id"] == "o1"  # not batched: of names alert
    for n in range(5):
        assert step(db, j, "alert", act(f"a{n}", n)) == []
    assert len(clock.set_) == 1 and clock.set_[0][0] == "batch-4" and clock.set_[0][2] == "join.batch"
    assert clock.set_[0][1] == "2026-10-15T17:00:00+00:00"
    assert match.batch_fired(db, cfg, clock.set_[0][3]) == 51
    aid, alias, last, events = clock.fired[0]
    assert aid == 4 and alias == "alert" and [a["id"] for a in events["alert"]] == ["a0", "a1", "a2"] and last["id"] == "a2"
    assert step(db, j, "alert", act("a9", 9)) == [] and len(clock.set_) == 2  # a new batch


def test_join_state_is_capped_and_expires(db, monkeypatch):
    monkeypatch.setattr(match, "MAX_OPEN", 2)
    j = auto({"mode": "count", "of": "mail", "at_least": 5, "within": "1h", "key": "object.from.0"})
    for n in range(4):
        step(db, j, "mail", act(f"m{n}", n, sender=f"s{n}"))
    assert len(db.rows("SELECT id FROM automation_state WHERE automation = 4")) == 2
    assert match.dropped(db, 4) == 2
    row = db.one("SELECT * FROM automation_state WHERE automation = 4 ORDER BY at DESC LIMIT 1")
    assert row["kind"] == "join" and row["expires_at"] == "2026-10-15T17:03:00+00:00"
    match.tidy(db, now="2026-10-15T17:02:30+00:00")
    assert len(db.rows("SELECT id FROM automation_state WHERE automation = 4")) == 1
    match.tidy(db, now="2026-10-15T18:00:00+00:00")
    assert db.rows("SELECT id FROM automation_state WHERE automation = 4") == []


def test_two_writers_at_once_lose_nothing(db, monkeypatch):
    j = auto({"mode": "count", "of": "mail", "at_least": 3, "within": "1h"})
    step(db, j, "mail", act("m1", 0))
    real, once = match._small, {"done": False}

    def meanwhile(v, cut=match.CUT, depth=0):
        if not once["done"] and isinstance(v, dict) and v.get("id") == "m3":
            once["done"] = True
            step(db, j, "mail", act("m2", 1))  # another process adds one between this one's read and write
        return real(v, cut, depth)

    monkeypatch.setattr(match, "_small", meanwhile)
    done = step(db, j, "mail", act("m3", 2))
    assert [a["id"] for a in done[0]["events"]["mail"]] == ["m1", "m2", "m3"]


def test_joins_read_in_words():
    assert match.describe_join({"mode": "any"}) == "each trigger on its own"
    assert match.describe_join(None) == "each trigger on its own"
    assert (
        match.describe_join({"mode": "all", "within": "10m", "key": "object.from"})
        == "all of the triggers within 10 minutes, for the same object.from"
    )
    assert match.describe_join({"mode": "count", "of": "login", "at_least": 3, "within": "5m"}) == "3 times login within 5 minutes"
    assert (
        match.describe_join({"mode": "absent", "after": "mail", "missing": "reply", "within": "2d"}) == "mail, then no reply within 2 days"
    )
    assert match.describe_join({"mode": "batch", "within": "1h"}) == "what the triggers bring in 1 hour, together in one run"
    assert match.describe_join({"mode": "batch", "within": "1h", "of": "alert"}) == "what alert brings in 1 hour, together in one run"


def test_join_handlers_are_registered_with_timers():
    from app.domain import timers

    for kind, fn in match.TIMER_HANDLERS.items():
        assert timers._HANDLERS[kind].fn is fn and timers._HANDLERS[kind].ended is match.join_ended
