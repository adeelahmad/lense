"""Auto scaling's rules and terms (app/domain/scaling.py): what each rule proposes, and which changes widen what a
person approved."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import scaling

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)


def rules(*rs):
    return {"rules": [scaling.check_rule(r) for r in rs], "created_at": NOW.isoformat()}


def test_a_target_rule_keeps_its_metric_at_the_target():
    g = rules({"type": "target", "metric": "workers_busy_share", "target": 0.5})
    want, _, said, *_ = scaling.evaluate(g, {"workers_busy_share": 1.0}, 2, NOW)
    assert want == 4 and said == ["keep workers_busy_share at 0.5: workers_busy_share is 1, so 4"]
    assert scaling.evaluate(g, {"workers_busy_share": 0.25}, 4, NOW)[0] == 2
    assert scaling.evaluate(g, {"workers_busy_share": 0}, 3, NOW)[0] == 0


def test_a_step_rule_fires_once_its_condition_has_held():
    g = rules({"type": "step", "metric": "oldest_wait_minutes", "above": 10, "minutes": 5, "add": "50%"})
    want, state, *_ = scaling.evaluate(g, {"oldest_wait_minutes": 12}, 4, NOW)
    assert want is None and state["0"]["since"]
    want, state, *_ = scaling.evaluate(g, {"oldest_wait_minutes": 12}, 4, NOW + dt.timedelta(minutes=5), state)
    assert want == 6  # 50% of 4 more
    want, state, *_ = scaling.evaluate(g, {"oldest_wait_minutes": 3}, 6, NOW + dt.timedelta(minutes=6), state)
    assert want is None and "since" not in state["0"]  # the condition broke: it starts over
    down = rules({"type": "step", "metric": "jobs_waiting", "below": 1, "minutes": 0, "remove": 1})
    assert scaling.evaluate(down, {"jobs_waiting": 0}, 2, NOW)[0] == 1


def test_a_schedule_rule_sets_the_bounds_when_its_time_comes():
    g = {
        **rules({"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC", "min": 2, "desired": 2}),
        "created_at": NOW.replace(hour=7).isoformat(),
    }
    assert scaling.evaluate(g, {}, 0, NOW.replace(hour=8))[0] is None
    want, state, said, lo, hi = scaling.evaluate(g, {}, 0, NOW)  # 9:00 has passed since it was made
    assert (want, lo, hi) == (2, 2, None) and said[0].endswith("fired")
    assert scaling.evaluate(g, {}, 2, NOW + dt.timedelta(minutes=1), state)[0] is None  # once per time it names


def test_the_largest_proposal_wins():
    g = rules(
        {"type": "target", "metric": "jobs_waiting", "target": 10},
        {"type": "step", "metric": "jobs_waiting", "above": 5, "minutes": 0, "add": 3},
    )
    assert scaling.evaluate(g, {"jobs_waiting": 20}, 1, NOW)[0] == 4


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "nudge"},
        {"type": "target", "metric": "jobs_waiting"},
        {"type": "target", "metric": "jobs_waiting", "target": 0},
        {"type": "step", "metric": "jobs_waiting", "above": 1},
        {"type": "step", "metric": "jobs_waiting", "above": 1, "below": 0, "add": 1},
        {"type": "step", "metric": "jobs_waiting", "above": 1, "add": "5000%"},
        {"type": "schedule", "cron": "0 9 * * *", "timezone": "Mars/Olympus", "min": 1},
    ],
)
def test_rules_that_dont_say_enough_are_refused(rule):
    with pytest.raises(ValueError):
        scaling.check_rule(rule)


def test_what_widens_an_approval():
    t = {"kind": "node", "provider": 1, "template": {"size": "small"}, "max": 3, "budget": 20.0, "at_cap": "stop"}
    assert not scaling.widens(t, t)
    assert not scaling.widens(t, {**t, "max": 2, "budget": 10.0})
    for wider in ({"max": 4}, {"budget": 21.0}, {"budget": None}, {"provider": 2}, {"template": {"size": "large"}}, {"at_cap": "min"}):
        assert scaling.widens(t, {**t, **wider}), wider
