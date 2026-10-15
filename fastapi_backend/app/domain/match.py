"""Match trees (docs/automations.md, Match trees): the one condition language for a trigger's `where`, an
automation's `if`, and the condition, filter and switch nodes of a workflow.

    M := {"all": [M, ...]} | {"any": [M, ...]} | {"not": M} | {"path": "a.b.0", "op": OP, "value": V}

A leaf tests the part of a value at a dotted path (list items by number, as flow.dig does; no path tests the value
itself) with one of OPS: the workflow engine's tests (flow.OPS: exists, empty, equals, not_equals, contains, gt, lt),
which work as a condition node's do, and the ones added here: `in` and `not_in` (one of a list), `starts_with`,
`ends_with` and `matches` (text), `between` (numbers or times), `before`, `after` and `within` (times, against now),
`weekday` and `time_between` (a time's day and time of day), and `changed` (against the value this test saw last
time). A list passes one of the added tests when one of its items does, as `contains` does; `not_in` passes when none
does. `all` holds when every tree in it does (an empty one always does), `any` when one does, `not` when its tree
doesn't. Values are literals: templates belong to actions, which keeps matching cheap and safe. A tree nests at most
MAX_DEPTH deep and holds at most MAX_LEAVES tests.

`env` carries what tests about time and state read, so that tests and dry runs can fix them: `now` (ISO 8601; the
clock when it isn't given), `timezone` (the automation's, for weekday, time_between and times written without a zone;
UTC when it isn't given) and `state` (a State: what `changed` compares with; without one nothing has changed).
`changed` keeps the last value it saw in `automation_state` (kind `last`), per automation, trigger and key, and `tidy`
drops what nobody has looked at for STATE_DAYS.
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import logging
import re
import time
from re import _parser as _sre  # the parser re itself uses, to check patterns before they run

import regex

from . import flow, store

R = store.R
log = logging.getLogger(__name__)

MAX_DEPTH = 6
MAX_LEAVES = 50
MAX_LIST = 500  # values in an in or not_in list
MAX_TEXT = 1000  # characters in a starts_with or ends_with value
MAX_PATTERN = 200  # characters in a matches pattern
MAX_INPUT = 20_000  # characters of a value a pattern reads
PATTERN_SECONDS = 0.05  # how long a pattern may take on one value; past that it doesn't match
STATE_DAYS = 30  # a value changed compares with is kept this long after it was last seen
MAX_OPEN = 1000  # rows an automation keeps in automation_state; past that the oldest go
SWEEP_SECONDS = 3600
GROUPS = ("all", "any", "not")
LEAF = {"path", "op", "value"}
ADDED = (
    "in", "not_in", "starts_with", "ends_with", "matches", "between", "before", "after", "within", "weekday", "time_between",
    "changed",
)  # fmt: skip
OPS = flow.OPS + ADDED
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CLOCK_RX = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
WORDS = {  # each test in words, and its opposite (for a test under not)
    "exists": ("is set", "is not set"),
    "empty": ("is empty", "is not empty"),
    "equals": ("is", "is not"),
    "not_equals": ("is not", "is"),
    "contains": ("contains", "doesn't contain"),
    "gt": ("is more than", "is not more than"),
    "lt": ("is less than", "is not less than"),
    "in": ("is", "is not"),
    "not_in": ("is not", "is"),
    "starts_with": ("starts with", "doesn't start with"),
    "ends_with": ("ends with", "doesn't end with"),
    "matches": ("matches", "doesn't match"),
    "between": ("is between", "is not between"),
    "before": ("is before", "is not before"),
    "after": ("is after", "is not after"),
    "within": ("is within", "is not within"),
    "weekday": ("falls on a", "doesn't fall on a"),
    "time_between": ("is in the day between", "is not in the day between"),
    "changed": ("changed", "didn't change"),
}
_swept = {"at": 0.0}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------- checking ----------
def check(tree, what="the condition"):
    """The tree, cleaned: ValueError naming the first problem, and where it is (`what`, then the path into the tree,
    such as "the condition, all.1: ...")."""
    return _check(tree, what, 0, [0], "")


def _check(m, what, depth, count, at):
    where = f"{what}, {at}" if at else what
    if depth >= MAX_DEPTH:
        raise ValueError(f"{where}: conditions nest at most {MAX_DEPTH} deep")
    if not isinstance(m, dict) or not m:
        raise ValueError(f"{where}: a condition is {{all: [...]}}, {{any: [...]}}, {{not: ...}} or {{path, op, value}}")
    groups = [k for k in GROUPS if k in m]
    if groups:
        if len(m) != 1:
            raise ValueError(f"{where}: {groups[0]} stands alone, with nothing next to it")
        k = groups[0]
        if k == "not":
            return {"not": _check(m["not"], what, depth + 1, count, f"{at}.not" if at else "not")}
        items = m[k]
        if not isinstance(items, list):
            raise ValueError(f"{where}: {k} is a list of conditions")
        if k == "any" and not items:
            raise ValueError(f"{where}: any needs at least one condition")
        return {k: [_check(x, what, depth + 1, count, f"{at}.{k}.{n}" if at else f"{k}.{n}") for n, x in enumerate(items)]}
    extra = sorted(set(m) - LEAF)
    if extra:
        raise ValueError(f"{where}: a test is {{path, op, value}}, not {extra[0]}")
    count[0] += 1
    if count[0] > MAX_LEAVES:
        raise ValueError(f"{what} has at most {MAX_LEAVES} tests")
    try:
        return check_leaf(m)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


def check_leaf(m):
    """One test ({path, op, value}), cleaned: ValueError saying what is wrong with it."""
    op = m.get("op")
    if op not in OPS:
        raise ValueError(f"the test is one of {', '.join(OPS)}")
    if m.get("path") and not flow.PATH_RX.match(str(m["path"])):
        raise ValueError("give a path like summary.importance, or none")
    out = {"op": op}
    if m.get("path"):
        out["path"] = str(m["path"])
    if op in flow.OPS:
        problem = flow.test_problem(m)
        if problem:
            raise ValueError(problem)
        if "value" in m:
            out["value"] = m["value"]
        return out
    value = _VALUES[op](m.get("value"))
    if value is not None:
        out["value"] = value
    return out


def _literal(x):
    return x is None or isinstance(x, (str, int, float, bool))


def _list_value(v):
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_LIST or not all(_literal(x) for x in v):
        raise ValueError(f"give a list of 1 to {MAX_LIST} values (text, numbers, true or false)")
    return list(v)


def _text_value(v):
    if _num(v):
        v = str(v)
    if not isinstance(v, str) or not v or len(v) > MAX_TEXT:
        raise ValueError(f"give the text to look for, up to {MAX_TEXT} characters")
    return v


def _pattern_value(v):
    if not isinstance(v, str) or not v or len(v) > MAX_PATTERN:
        raise ValueError(f"give a pattern of up to {MAX_PATTERN} characters, like trip|outing")
    try:
        parsed = _sre.parse(v, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"the pattern doesn't read: {e.msg}") from None
    if _refers(parsed):
        raise ValueError("the pattern refers back to a group (like \\1); write it without")
    if _risky(parsed):
        raise ValueError(
            "the pattern repeats a part that repeats itself, like (a+)+, or repeats alternatives that start alike, "
            "like (a|aa)+; that can take very long, so write it more simply"
        )
    try:
        _rx(v)
    except regex.error as e:
        raise ValueError(f"the pattern doesn't read: {e}") from None
    return v


_REPEATS = {_sre.MAX_REPEAT, _sre.MIN_REPEAT, _sre.POSSESSIVE_REPEAT}


def _subs(op, av):
    """The parsed pieces inside one parsed piece."""
    if op in _REPEATS:
        return [av[2]]
    if op is _sre.SUBPATTERN:
        return [av[-1]]
    if op is _sre.BRANCH:
        return list(av[1])
    if op in (_sre.ASSERT, _sre.ASSERT_NOT):
        return [av[1]]
    if op is _sre.ATOMIC_GROUP:
        return [av]
    if op is _sre.GROUPREF_EXISTS:
        return [x for x in av[1:] if x is not None]
    return []


def _refers(items):
    return any(op is _sre.GROUPREF or any(_refers(s) for s in _subs(op, av)) for op, av in items)


def _first(alt):
    """The character an alternative starts with, when it is one plain character; None otherwise."""
    if not alt:
        return None
    op, av = alt[0]
    if op is _sre.SUBPATTERN:
        return _first(list(av[-1]))
    return chr(av).casefold() if op is _sre.LITERAL else None


def _risky(items, inside=False):
    """Whether a parsed pattern can backtrack without end: a part repeated more than once inside a part that is, or
    alternatives inside such a part that may start alike (`(a|aa)+`)."""
    for op, av in items:
        if op in _REPEATS:
            many = av[1] > 1
            if many and inside:
                return True
            if _risky(av[2], inside or many):
                return True
            continue
        if op is _sre.BRANCH and inside:
            starts = [_first(list(alt)) for alt in av[1]]
            if None in starts or len(set(starts)) != len(starts):
                return True
        if any(_risky(s, inside) for s in _subs(op, av)):
            return True
    return False


@functools.lru_cache(maxsize=512)
def _rx(pattern):
    return regex.compile(pattern, regex.IGNORECASE | regex.V0)


def _when_value(v):
    from . import automation_spec

    if v == "now":
        return v
    if isinstance(v, str) and v.strip():
        try:
            automation_spec.check_duration(v, back=True)
            return v.strip()
        except ValueError:
            pass
        if _time(v, dt.timezone.utc) is not None:
            return v.strip()
    raise ValueError("compare with a time like 2026-10-16T07:30, with now, or with a time from now like 2h (-2h: 2 hours ago)")


def _between_value(v):
    if not isinstance(v, list) or len(v) != 2:
        raise ValueError("give the two ends, like [1, 5] or [2026-10-01, 2026-10-31]")
    lo, hi = v
    if _num(lo) and _num(hi):
        if lo > hi:
            raise ValueError("the first end is the lower one")
        return [lo, hi]
    if _num(lo) or _num(hi):
        raise ValueError("give two numbers or two times")
    lo, hi = _when_value(lo), _when_value(hi)
    a, b = _time(lo, dt.timezone.utc), _time(hi, dt.timezone.utc)
    if a is not None and b is not None and a > b:
        raise ValueError("the first end is the earlier one")
    return [lo, hi]


def _within_value(v):
    from . import automation_spec

    if not isinstance(v, str) or automation_spec.check_duration(v, what="within") <= 0:
        raise ValueError("within is a time from now like 2h or 7d")
    return v.strip()


def _day(d):
    if _num(d) and int(d) == d and 1 <= d <= 7:
        return DAYS[int(d) - 1]
    s = str(d).strip().casefold()[:3] if isinstance(d, str) else ""
    if s in DAYS:
        return s
    raise ValueError("give the days, like [sat, sun] or [monday, friday]")


def _weekday_value(v):
    if not isinstance(v, list) or not 1 <= len(v) <= 7:
        raise ValueError("give the days, like [sat, sun] or [monday, friday]")
    return sorted({_day(d) for d in v}, key=DAYS.index)


def _clock(s):
    m = CLOCK_RX.match(str(s).strip()) if isinstance(s, str) else None
    if not m:
        raise ValueError("give two times of day, like [22:00, 06:00]")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _time_between_value(v):
    if not isinstance(v, list) or len(v) != 2:
        raise ValueError("give two times of day, like [22:00, 06:00]")
    a, b = _clock(v[0]), _clock(v[1])
    if a == b:
        raise ValueError("give two different times of day")
    return [a, b]


def _changed_value(v):
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not flow.PATH_RX.match(v):
        raise ValueError("changed compares with the last value seen; its value, when given, is a path to keep them apart by, like actor.id")
    return v


_VALUES = {
    "in": _list_value,
    "not_in": _list_value,
    "starts_with": _text_value,
    "ends_with": _text_value,
    "matches": _pattern_value,
    "between": _between_value,
    "before": _when_value,
    "after": _when_value,
    "within": _within_value,
    "weekday": _weekday_value,
    "time_between": _time_between_value,
    "changed": _changed_value,
}


# ---------- testing ----------
def test(tree, value, env=None):
    """Whether `value` passes the tree (checked with `check` first)."""
    if "all" in tree:
        return all(test(m, value, env) for m in tree["all"])
    if "any" in tree:
        return any(test(m, value, env) for m in tree["any"])
    if "not" in tree:
        return not test(tree["not"], value, env)
    return test_leaf(tree, value, env)


def test_leaf(m, value, env=None):
    """Whether `value` passes one (checked) test."""
    op = m["op"]
    if op in flow.OPS:
        return flow.test(m, value)
    v, want = flow.dig(value, m.get("path")), m.get("value")
    if op == "changed":
        st = state_of(env)
        if st is None:
            return False
        key = m.get("path") or ""
        if want:
            key += "|" + _canon(flow.dig(value, want))
        return st.changed(key, v)
    if op in ("in", "not_in"):
        return _some(v, lambda x: _one_of(x, want)) == (op == "in")
    if v is None:
        return False
    if op in ("starts_with", "ends_with"):
        w = want.casefold()
        if op == "starts_with":
            return _some(v, lambda x: (t := _text(x)) is not None and t.casefold().startswith(w))
        return _some(v, lambda x: (t := _text(x)) is not None and t.casefold().endswith(w))
    if op == "matches":
        return _some(v, lambda x: (t := _text(x)) is not None and _search(want, t))
    if op == "between" and _num(want[0]):
        return _some(v, lambda x: _num(x) and want[0] <= x <= want[1])
    tz = _zone(env)

    def at(x):
        return _time(x, tz)

    if op == "between":
        lo, hi = when(want[0], env), when(want[1], env)
        return _some(v, lambda x: (t := at(x)) is not None and lo <= t <= hi)
    if op == "before":
        b = when(want, env)
        return _some(v, lambda x: (t := at(x)) is not None and t < b)
    if op == "after":
        b = when(want, env)
        return _some(v, lambda x: (t := at(x)) is not None and t > b)
    if op == "within":
        from . import automation_spec

        n, now = automation_spec.check_duration(want), now_of(env)
        return _some(v, lambda x: (t := at(x)) is not None and abs((t - now).total_seconds()) <= n)
    if op == "weekday":
        days = {DAYS.index(d) for d in want}
        return _some(v, lambda x: (t := at(x)) is not None and t.astimezone(tz).weekday() in days)
    if op == "time_between":
        a, b = (int(c[:2]) * 60 + int(c[3:]) for c in want)

        def inside(t):
            mins = t.astimezone(tz).hour * 60 + t.astimezone(tz).minute
            return a <= mins < b if a < b else mins >= a or mins < b

        return _some(v, lambda x: (t := at(x)) is not None and inside(t))
    raise ValueError(f"no test {op}")


def _some(v, one):
    """Whether a value passes: one of its items, for a list."""
    return any(one(x) for x in v) if isinstance(v, list) else one(v)


def _one_of(x, want):
    for w in want:
        if isinstance(x, str) and isinstance(w, str):
            if x.casefold() == w.casefold():
                return True
        elif isinstance(x, bool) == isinstance(w, bool) and x == w:
            return True
    return False


def _text(x):
    if isinstance(x, str):
        return x
    return str(x) if _num(x) else None


def _search(pattern, text):
    try:
        return _rx(pattern).search(text[:MAX_INPUT], timeout=PATTERN_SECONDS) is not None
    except TimeoutError:
        log.warning("the pattern %r took over %ss on a value and counts as not matching", pattern, PATTERN_SECONDS)
        return False


def _zone(env):
    from . import schedule

    try:
        return schedule.zone((env or {}).get("timezone") or "UTC")
    except ValueError:
        return dt.timezone.utc


def _time(x, tz):
    """An aware time from an ISO 8601 time or date (one without a zone is in `tz`), or a datetime; None for anything
    else."""
    if isinstance(x, dt.datetime):
        d = x
    elif isinstance(x, str) and 8 <= len(x.strip()) <= 40 and x.strip()[:4].isdigit():
        s = x.strip()
        try:
            d = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s[-1] in "zZ" else s)
        except ValueError:
            return None
    else:
        return None
    return d if d.tzinfo else d.replace(tzinfo=tz)


def now_of(env):
    """The time a test calls now: env's `now`, else the clock."""
    n = _time((env or {}).get("now"), dt.timezone.utc)
    return n or dt.datetime.now(dt.timezone.utc)


def when(v, env=None):
    """The moment a before, after or between value names: now, a time from now (2h, -30m) or a time (in env's time
    zone when it gives none)."""
    from . import automation_spec

    if v == "now":
        return now_of(env)
    try:
        return now_of(env) + dt.timedelta(seconds=automation_spec.check_duration(v, back=True))
    except ValueError:
        return _time(v, _zone(env))


def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


# ---------- what changed compares with ----------
class State:
    """What `changed` compares with: the last value each test saw, kept per automation and trigger (alias) in
    `automation_state` (kind `last`). Make one State per activity tested: within it each key is compared once, so an
    automation's If and a node that test the same thing agree. With `dry` it reads and keeps nothing new; with no
    database it keeps the values in `values` (a dict), for tests."""

    def __init__(self, db=None, automation=None, alias=None, dry=False, values=None):
        self.db, self.automation, self.alias, self.dry = db, automation, alias, dry
        self.values = {} if values is None else values
        self._said: dict[str, bool] = {}

    def changed(self, key, value):
        """Whether `value` differs from the last one seen under `key` (the first one seen is no change), keeping it."""
        if key in self._said:
            return self._said[key]
        text = _canon(value)
        if self.db is None:
            before = self.values.get(key)
            if not self.dry:
                self.values[key] = text
        else:
            before = self._swap(key, text)
        out = before is not None and before != text
        self._said[key] = out
        return out

    def _swap(self, key, text):
        ref = state_ref(self.automation, "last", self.alias, key)
        row = self.db.one("SELECT last FROM $r", r=ref)
        if not self.dry:
            if row is None:
                make_room(self.db, self.automation)
            t = _stamp()
            self.db.q(
                "UPSERT $r SET automation = $a, kind = 'last', alias = $al, key = $k, last = $v, at = $t, expires_at = $x",
                r=ref,
                a=self.automation,
                al=self.alias,
                k=str(key)[:300],
                v=text,
                t=t,
                x=_later(STATE_DAYS * 86400),
            )
        return (row or {}).get("last")


def state_of(env):
    """The State in env (a dict is used as a State's values), or None."""
    st = (env or {}).get("state")
    if isinstance(st, dict):
        return State(values=st)
    return st if isinstance(st, State) else None


def state_ref(automation, kind, alias, key):
    """The automation_state row of one automation's kind of state (last, join, decide) for a trigger and key."""
    return R("automation_state", hashlib.sha1(f"{automation}|{kind}|{alias}|{key}".encode()).hexdigest())


def _stamp():
    """When a state row was last written, to the microsecond: the oldest go first when an automation keeps too many."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def make_room(db, automation):
    """Before an automation opens one more row: past MAX_OPEN rows its oldest go, and are counted (`dropped`)."""
    n = (db.one("SELECT count() AS n FROM automation_state WHERE automation = $a GROUP ALL", a=automation) or {}).get("n") or 0
    if n < MAX_OPEN:
        return 0
    over = n - MAX_OPEN + 1
    old = db.rows("SELECT id, at FROM automation_state WHERE automation = $a ORDER BY at LIMIT $n", a=automation, n=over)
    db.q("DELETE automation_state WHERE id IN $ids", ids=[R("automation_state", r["id"]) for r in old])
    db.q(
        "UPSERT $r SET automation = $a, n += $n, at = $t",
        r=R("automation_state_dropped", str(automation)),
        a=automation,
        n=over,
        t=store.now(),
    )
    return over


def dropped(db, automation):
    """How many of an automation's rows went to make room."""
    return int((db.one("SELECT n FROM $r", r=R("automation_state_dropped", str(automation))) or {}).get("n") or 0)


def tidy(db, cfg=None, now=None):
    """Drop automation_state rows past their expires_at, at most once an hour per process (routines.run_due calls it
    every 30 seconds); the cut-off, or 0 when it didn't look."""
    t = time.monotonic()
    if now is None and _swept["at"] and t - _swept["at"] < SWEEP_SECONDS:
        return 0
    _swept["at"] = t
    cut = now or store.now()
    db.q("DELETE automation_state WHERE expires_at < $c", c=cut)
    return cut


# ---------- reading trees ----------
def leaves(tree):
    """The tests in a tree, in order."""
    if "all" in tree or "any" in tree:
        return [x for m in tree.get("all", tree.get("any")) for x in leaves(m)]
    if "not" in tree:
        return leaves(tree["not"])
    return [tree]


def paths(tree):
    """The paths a tree tests, each once, in order (for checking them against a kind's fields)."""
    return list(dict.fromkeys(m.get("path") or "" for m in leaves(tree)))


def _label(path, kind):
    """A path in words: with a kind, the title its schema gives the field there ("the title"), else the path."""
    if kind and path:
        from . import events

        title = (events.field_at(kind, path) or {}).get("title")
        if title:
            return f"the {title[0].lower()}{title[1:]}"
    return path or "the value"


def _value(v):
    if isinstance(v, str):
        return f"“{v}”"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "nothing"
    return str(v)


def _either(items, word="or"):
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}"


UNIT_WORDS = {"s": "second", "m": "minute", "h": "hour", "d": "day"}


def duration_words(text):
    """A duration in words: 2h is "2 hours", -30m "30 minutes" (the sign is the caller's)."""
    s = str(text).strip().lstrip("-")
    n, unit = int(s[:-1]), UNIT_WORDS[s[-1]]
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _when_words(v):
    if v == "now":
        return "now"
    if isinstance(v, str) and re.match(r"^-?\d+[smhd]$", v):
        return f"{duration_words(v)} ago" if v.startswith("-") else f"{duration_words(v)} from now"
    return str(v)


def _pattern_words(p):
    words = p.split("|")
    if all(re.match(r"^[\w @.'-]+$", w) for w in words):
        return _either([f"“{w}”" for w in words])
    return f"the pattern “{p}”"


def _value_words(op, v, kind):
    if op in ("in", "not_in"):
        return _either([_value(x) for x in v])
    if op == "matches":
        return _pattern_words(v)
    if op == "between":
        return f"{_value(v[0]) if _num(v[0]) else _when_words(v[0])} and {_value(v[1]) if _num(v[1]) else _when_words(v[1])}"
    if op in ("before", "after"):
        return _when_words(v)
    if op == "within":
        return f"{duration_words(v)} of now"
    if op == "weekday":
        return _either([DAY_NAMES[DAYS.index(d)] for d in v])
    if op == "time_between":
        return f"{v[0]} and {v[1]}"
    if op == "changed":
        return f"for each {_label(v, kind)}" if v else ""
    return _value(v)


def describe(tree, kind=None):
    """The tree in plain words, as the builder and the agent show it: "the sender contains “@stmarys.org” and the
    subject matches “trip” or “outing”". `kind` is the kind of activity the tree tests (events.py), for naming its
    fields."""
    return _describe(tree, kind, True)


def _describe(m, kind, top):
    for k, word in (("all", " and "), ("any", " or ")):
        if k in m:
            if not m[k]:
                return "always"
            parts = [_describe(x, kind, False) for x in m[k]]
            text = word.join(parts)
            return text if top or len(parts) == 1 else f"({text})"
    if "not" in m:
        inner = m["not"]
        return _leaf(inner, kind, True) if "op" in inner else f"not ({_describe(inner, kind, True)})"
    return _leaf(m, kind, False)


def _leaf(m, kind, negated):
    said = WORDS.get(m["op"], (m["op"], f"not {m['op']}"))[1 if negated else 0]
    words = f"{_label(m.get('path'), kind)} {said}"
    if m["op"] in ("exists", "empty") or (m["op"] == "changed" and not m.get("value")):
        return words
    return f"{words} {_value_words(m['op'], m.get('value'), kind)}"
