"""The tests match trees add to the workflow engine's (docs/automations.md, Match trees: Operators): one table per
test of what passes and what doesn't, the values each check refuses, patterns that could run without end, times read
in the automation's time zone against a fixed now, `changed` against the last value seen, and each in words."""

from __future__ import annotations

import re

import pytest

from app.domain import match

NOW = "2026-10-15T16:00:00Z"  # a Thursday
ENV = {"now": NOW, "timezone": "Europe/London"}  # 17:00 in London (summer time)


def leaf(path, op, value=None):
    return {"path": path, "op": op, **({"value": value} if value is not None else {})}


def passes(op, value, got, env=ENV):
    """Whether {"x": got} passes the test `op` with `value`."""
    return match.test(match.check(leaf("x", op, value)), {"x": got}, env)


CASES = [  # op, value, passes, doesn't
    ("in", ["Draft", "sent", 3], ["draft", "SENT", 3, ["x", "sent"]], ["drafts", "3", 4, None, [], ["x"], True]),
    ("in", [True, None], [True, None, [False, True]], [1, False, "true"]),
    ("not_in", ["spam", "junk"], ["inbox", None, [], ["inbox", "work"], 4], ["SPAM", ["inbox", "junk"]]),
    ("starts_with", "Re:", ["re: trip", "RE: x", ["Fwd: a", "Re: b"]], ["Fwd: re: x", None, 12, {"a": "Re:"}]),
    ("starts_with", "+44", ["+44 7700", ["+1", "+447"]], ["0044", 44]),
    ("starts_with", 44, [4412, "44 x"], [12]),
    ("ends_with", "@stmarys.org", ["office@STMARYS.org", ["a@b.c", "x@stmarys.org"]], ["office@stmarys.org.uk", None]),
    ("matches", "trip|outing", ["School TRIP on Friday", ["no", "an outing"]], ["Parents' evening", None, 7, []]),
    ("matches", r"^\d{3}-\d{4}$", ["555-0100", ["x", "555-0100"]], ["5550100", 555]),
    ("between", [1, 5], [1, 5, 2.5, [9, 3]], [0, 6, "3", None, True]),
    (
        "between",
        ["2026-10-01", "2026-10-31"],
        ["2026-10-15T09:00:00Z", "2026-10-31", ["2026-11-02", "2026-10-02"]],
        ["2026-11-01T00:00:00Z", "soon", 5],
    ),
    ("between", ["-1d", "now"], ["2026-10-15T15:59:59Z", "2026-10-14T16:00:00Z"], ["2026-10-15T16:00:01Z", "2026-10-14T15:59:00Z"]),
    ("before", "now", ["2026-10-15T15:59:59Z", "2026-10-15T16:59:59+01:00"], ["2026-10-15T16:00:00Z", "2026-10-15T17:00:01+01:00"]),
    ("before", "-2h", ["2026-10-15T13:59:00Z"], ["2026-10-15T14:00:00Z", "nonsense", None]),
    ("after", "2h", ["2026-10-15T18:00:01Z", ["2026-01-01", "2027-01-01"]], ["2026-10-15T18:00:00Z"]),
    ("after", "2026-10-15T17:00", ["2026-10-15T16:00:01Z"], ["2026-10-15T16:00:00Z"]),  # a time with no zone: London's
    ("within", "1d", ["2026-10-16T15:00:00Z", "2026-10-14T17:00:00Z", NOW], ["2026-10-16T16:00:01Z", "2026-10-14T15:59:59Z", 3]),
    ("weekday", ["sat", "Sunday", 1], ["2026-10-17T12:00:00Z", "2026-10-18", "2026-10-19T08:00:00Z"], ["2026-10-15T12:00:00Z", "Saturday"]),
    ("weekday", ["fri"], ["2026-10-16T22:30:00Z"], ["2026-10-16T23:30:00Z"]),  # Friday 23:30 UTC is Saturday in London
    (
        "time_between",
        ["22:00", "6:00"],
        ["2026-10-15T21:00:00Z", "2026-10-16T04:59:00Z", "2026-10-15T23:00:00+01:00"],
        ["2026-10-15T20:59:00Z", "2026-10-16T05:00:00Z"],
    ),
    (
        "time_between",
        ["09:00", "17:00"],
        ["2026-10-15T15:59:00Z", "2026-10-15T08:00:00Z"],
        [NOW, "2026-10-15T07:59:00Z"],
    ),  # from 9 to before 17
]


@pytest.mark.parametrize(("op", "value", "good", "bad"), CASES, ids=[f"{c[0]}-{n}" for n, c in enumerate(CASES)])
def test_each_test_passes_what_it_should(op, value, good, bad):
    for got in good:
        assert passes(op, value, got), f"{got!r} should pass {op} {value!r}"
    for got in bad:
        assert not passes(op, value, got), f"{got!r} shouldn't pass {op} {value!r}"


def test_the_added_tests_read_the_clock_and_zone_from_env():
    assert passes("time_between", ["09:00", "17:00"], NOW, {"now": NOW})  # 16:00 in UTC, the zone when none is given
    assert not passes("time_between", ["09:00", "17:00"], NOW, {"timezone": "Asia/Tokyo"})  # 01:00 the next day there
    assert passes("weekday", ["fri"], NOW, {"timezone": "Asia/Tokyo"})
    assert passes("within", "1h", "2026-10-15T16:30:00Z", {"now": "2026-10-15T16:00:00Z"})
    assert not passes("within", "1h", "2026-10-15T16:30:00Z", {"now": "2026-10-15T18:00:00Z"})
    assert passes("before", "now", "2000-01-01", None)  # no env: the clock
    assert passes("after", "2026-10-15", "2026-10-15T00:30:00Z", {"timezone": "Europe/London"})  # midnight there is 23:00 UTC
    assert passes("weekday", ["thu"], NOW, {"timezone": "Nowhere/Else"})  # a zone that isn't one: UTC


def test_checks_refuse_values_a_test_cant_use():
    bad = [
        (leaf("x", "in", "draft"), "give a list of 1 to 500 values"),
        (leaf("x", "in", []), "give a list"),
        (leaf("x", "not_in", [{"a": 1}]), "give a list"),
        (leaf("x", "in", list(range(501))), "give a list of 1 to 500"),
        (leaf("x", "starts_with", ""), "give the text to look for"),
        (leaf("x", "ends_with", ["a"]), "give the text to look for"),
        (leaf("x", "matches", "x" * 201), "up to 200 characters"),
        (leaf("x", "matches", "(trip"), "the pattern doesn't read: missing )"),
        (leaf("x", "matches", "(a+)+$"), "repeats a part that repeats itself"),
        (leaf("x", "matches", r"(\w+\s?)*$"), "repeats a part that repeats itself"),
        (leaf("x", "matches", "(a|aa)+"), "alternatives that start alike"),
        (leaf("x", "matches", "(?:x|xy){2,}"), "alternatives that start alike"),
        (leaf("x", "matches", r"(a)\1"), "refers back to a group"),
        (leaf("x", "between", [5, 1]), "the first end is the lower one"),
        (leaf("x", "between", [1, "2026-10-01"]), "two numbers or two times"),
        (leaf("x", "between", ["2026-10-31", "2026-10-01"]), "the first end is the earlier one"),
        (leaf("x", "between", [1]), "give the two ends"),
        (leaf("x", "before", "tomorrow"), "compare with a time like"),
        (leaf("x", "after", 5), "compare with a time like"),
        (leaf("x", "within", "-2h"), "within can't point back"),
        (leaf("x", "within", "0s"), "within is a time from now"),
        (leaf("x", "within", "soon"), "within is a whole number"),
        (leaf("x", "weekday", ["someday"]), "give the days"),
        (leaf("x", "weekday", "sat"), "give the days"),
        (leaf("x", "weekday", [8]), "give the days"),
        (leaf("x", "time_between", ["22:00"]), "two times of day"),
        (leaf("x", "time_between", ["25:00", "06:00"]), "two times of day"),
        (leaf("x", "time_between", ["06:00", "6:00"]), "two different times"),
        (leaf("x", "changed", "not a path"), "a path to keep them apart by"),
        (leaf("x", "near", 1), "the test is one of exists, empty, equals"),
    ]
    for tree, said in bad:
        with pytest.raises(ValueError, match=re.escape(said)):
            match.check(tree)
    with pytest.raises(ValueError, match="trigger mail's where, all.0: give a list"):
        match.check({"all": [leaf("x", "in", "a")]}, "trigger mail's where")
    # cleaned: days as three letters in week order, times of day with two digits, durations trimmed
    assert match.check(leaf("x", "weekday", ["Sunday", 6, "mon"]))["value"] == ["mon", "sat", "sun"]
    assert match.check(leaf("x", "time_between", ["6:00", "22:30"]))["value"] == ["06:00", "22:30"]
    assert match.check(leaf("x", "within", " 2h "))["value"] == "2h"
    assert match.check(leaf("x", "changed")) == {"path": "x", "op": "changed"}
    assert match.check(leaf("x", "starts_with", 44))["value"] == "44"
    for fine in ["trip|outing", "(trip|outing)+", "(cat|car)s?", r"\d+-\d+", r"(\w|-)+", "a{2,3}b+", "(ab)+c"]:
        assert match.check(leaf("x", "matches", fine))["value"] == fine


def test_a_pattern_reads_a_long_value_only_so_far_and_gives_up_when_slow(monkeypatch):
    long = "a" * 25_000 + " trip"
    assert not passes("matches", "trip", long)  # past MAX_INPUT characters
    assert passes("matches", "trip", "a" * 19_000 + " trip")
    monkeypatch.setattr(match, "PATTERN_SECONDS", 0.01)
    slow = {"path": "x", "op": "matches", "value": "(a|aa)+$"}  # never passes a check; here only to time it out
    assert match.test_leaf(slow, {"x": "a" * 60 + "b"}) is False


def test_changed_compares_with_the_last_value_seen():
    tree = match.check(leaf("object.state", "changed"))
    kept: dict = {}

    def seen(state):
        return match.test(tree, {"object": {"state": state}}, {"state": kept})

    assert seen("closed") is False  # the first value seen is where it starts
    assert seen("closed") is False
    assert seen("open") is True
    assert seen("open") is False
    assert seen(None) is True
    assert match.test(tree, {"object": {"state": "x"}}, None) is False  # no state: nothing has changed
    # each sensor its own: value names the path that keeps them apart
    per = match.check(leaf("object.state", "changed", "actor.id"))
    st: dict = {}

    def reading(sensor, state):
        return match.test(per, {"actor": {"id": sensor}, "object": {"state": state}}, {"state": st})

    assert [reading("s1", "on"), reading("s2", "off"), reading("s1", "on"), reading("s2", "on"), reading("s1", "off")] == [
        False,
        False,
        False,
        True,
        True,
    ]


def test_one_state_answers_once_per_key_and_dry_keeps_nothing(db):
    tree = match.check(leaf("object.state", "changed"))
    a = {"object": {"state": "closed"}}
    b = {"object": {"state": "open"}}
    assert match.test(tree, a, {"state": match.State(db, 4, "door")}) is False
    st = match.State(db, 4, "door")
    assert match.test(tree, b, {"state": st}) is True
    assert match.test(tree, b, {"state": st}) is True  # the If and a node testing the same activity agree
    assert match.test(tree, b, {"state": match.State(db, 4, "door")}) is False
    assert match.test(tree, a, {"state": match.State(db, 4, "door", dry=True)}) is True  # a dry run reads...
    assert match.test(tree, a, {"state": match.State(db, 4, "door", dry=True)}) is True  # ...and keeps nothing
    assert match.test(tree, a, {"state": match.State(db, 4, "other")}) is False  # each trigger its own
    assert match.test(tree, a, {"state": match.State(db, 5, "door")}) is False  # each automation its own
    row = db.one("SELECT * FROM automation_state WHERE automation = 4 AND alias = 'door'")
    assert row["kind"] == "last" and row["key"] == "object.state" and row["last"] == '"open"' and row["expires_at"] > row["at"]


def test_state_keeps_to_its_limit_and_is_tidied(db, monkeypatch):
    monkeypatch.setattr(match, "MAX_OPEN", 3)
    tree = match.check(leaf("v", "changed", "k"))
    for k in range(5):
        match.test(tree, {"k": k, "v": 1}, {"state": match.State(db, 7, "t")})
    assert db.one("SELECT count() AS n FROM automation_state WHERE automation = 7 GROUP ALL")["n"] == 3
    assert match.dropped(db, 7) == 2
    keys = sorted(r["key"] for r in db.rows("SELECT key FROM automation_state WHERE automation = 7"))
    assert keys == ["v|2", "v|3", "v|4"]  # the oldest went
    db.q("UPDATE automation_state SET expires_at = '2000-01-01T00:00:00+00:00' WHERE key = 'v|2'")
    assert match.tidy(db, now="2026-10-15T00:00:00+00:00")
    assert len(db.rows("SELECT id FROM automation_state WHERE automation = 7")) == 2
    assert match.tidy(db) == 0  # once an hour, unless now is given


def test_the_added_tests_read_in_words():
    tree = {
        "all": [
            leaf("object.from", "ends_with", "@stmarys.org"),
            leaf("object.subject", "matches", "trip|outing"),
            leaf("object.label", "in", ["school", "family"]),
            {"not": leaf("object.label", "in", ["spam"])},
            leaf("object.size", "between", [1, 5]),
            leaf("object.start", "within", "1d"),
            leaf("object.start", "after", "now"),
            leaf("published", "before", "-2h"),
            leaf("published", "between", ["-1d", "2026-10-31"]),
            leaf("now", "weekday", ["sat", "sun"]),
            leaf("now", "time_between", ["22:00", "06:00"]),
            leaf("object.code", "matches", r"^\d+$"),
            leaf("object.state", "changed"),
            {"not": leaf("object.state", "changed", "actor.id")},
            leaf("object.subject", "starts_with", "Re:"),
        ]
    }
    assert match.describe(tree) == (
        "object.from ends with “@stmarys.org” and object.subject matches “trip” or “outing” and object.label is "
        "“school” or “family” and object.label is not “spam” and object.size is between 1 and 5 and object.start is "
        "within 1 day of now and object.start is after now and published is before 2 hours ago and published is "
        "between 1 day ago and 2026-10-31 and now falls on a Saturday or Sunday and now is in the day between 22:00 "
        "and 06:00 and object.code matches the pattern “^\\d+$” and object.state changed and object.state didn't "
        "change for each actor.id and object.subject starts with “Re:”"
    )
    assert match.describe(match.check(leaf("x", "not_in", ["a", "b", "c"]))) == "x is not “a”, “b” or “c”"
    assert match.duration_words("1m") == "1 minute" and match.duration_words("-90s") == "90 seconds"
