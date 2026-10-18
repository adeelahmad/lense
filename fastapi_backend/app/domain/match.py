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
def check(tree, what="the condition", lookback=False):
    """The tree, cleaned: ValueError naming the first problem, and where it is (`what`, then the path into the tree,
    such as "the condition, all.1: ..."). Only an automation's If may look back over the stream (`seen`, with
    `lookback`), so that testing a trigger's where on each new activity stays cheap."""
    return _check(tree, what, 0, [0], "", lookback)


def _check(m, what, depth, count, at, lookback=False):
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
            return {"not": _check(m["not"], what, depth + 1, count, f"{at}.not" if at else "not", lookback)}
        items = m[k]
        if not isinstance(items, list):
            raise ValueError(f"{where}: {k} is a list of conditions")
        if k == "any" and not items:
            raise ValueError(f"{where}: any needs at least one condition")
        return {k: [_check(x, what, depth + 1, count, f"{at}.{k}.{n}" if at else f"{k}.{n}", lookback) for n, x in enumerate(items)]}
    if "seen" in m:
        if len(m) != 1:
            raise ValueError(f"{where}: seen stands alone, with nothing next to it")
        if not lookback:
            raise ValueError(f"{where}: only an automation's If looks back over what happened (seen)")
        count[0] += 1
        if count[0] > MAX_LEAVES:
            raise ValueError(f"{what} has at most {MAX_LEAVES} tests")
        try:
            return {"seen": check_seen(m["seen"])}
        except ValueError as e:
            raise ValueError(f"{where}: {e}") from None
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
    if "seen" in tree:
        return seen(tree["seen"], value, env)
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


# ---------- looking back over the stream ----------
SEEN_KEYS = {"kinds", "within", "where", "same", "count"}
MAX_SEEN = 200  # activities a lookback reads at most
MAX_SEEN_COUNT = 100
MAX_SEEN_KINDS = 10


def check_seen(s):
    """A lookback ({kinds, within, where, same, count}), cleaned: ValueError saying what is wrong with it."""
    from . import automation_spec, events

    if not isinstance(s, dict):
        raise ValueError("seen is {kinds, within, where, same, count}")
    extra = sorted(set(s) - SEEN_KEYS)
    if extra:
        raise ValueError(f"seen has no {extra[0]}; it takes {', '.join(sorted(SEEN_KEYS))}")
    ks = s.get("kinds")
    if not isinstance(ks, list) or not 1 <= len(ks) <= MAX_SEEN_KINDS or not all(isinstance(k, str) for k in ks):
        raise ValueError(f"seen's kinds lists 1 to {MAX_SEEN_KINDS} kinds of activity, like email.received")
    events.expand(ks)
    within = s.get("within")
    if within is None:
        raise ValueError("seen says how far back it looks (within), like 2d")
    automation_spec.check_duration(within, what="seen's within")
    if automation_spec.check_duration(within) == 0:
        raise ValueError("seen's within is more than 0")
    out: dict = {"kinds": list(dict.fromkeys(ks)), "within": within.strip()}
    if s.get("where") is not None:
        out["where"] = check(s["where"], "seen's where")
        if any(m["op"] == "changed" for m in leaves(out["where"])):
            raise ValueError("seen's where can't test changed; test it in the If itself")
    if s.get("same") is not None:
        if not isinstance(s["same"], str) or not flow.PATH_RX.match(s["same"]):
            raise ValueError("seen's same is a path in the activities, like object.from")
        out["same"] = s["same"]
    c = s.get("count") if s.get("count") is not None else {"at_least": 1}
    if not isinstance(c, dict) or not c or set(c) - {"at_least", "at_most"}:
        raise ValueError("seen's count is {at_least: n}, {at_most: n} or both")
    for k, v in c.items():
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_SEEN_COUNT:
            raise ValueError(f"seen's count {k} is a whole number from 0 to {MAX_SEEN_COUNT}")
    if "at_most" in c and c.get("at_least", 0) > c["at_most"]:
        raise ValueError("seen's count at_least is at most its at_most")
    if not c.get("at_least") and "at_most" not in c:
        raise ValueError("seen's count needs at_least 1 or more, or an at_most")
    out["count"] = dict(c)
    return out


def found(s, value, env=None):
    """The activities a (checked) lookback finds, newest first, leaving out the one being tested: of its kinds,
    published in the last `within` before now, passing its `where`, with the same value at its `same` path as the
    activity being tested (the run context's `event`, or the value itself), in the automation's namespaces, as the
    automation's person may see them. `env` gives `db`, `cfg`, `spaces` (the automation's namespaces) and `account`
    (its person), or a `viewer` (events.viewer). Without a database it finds nothing."""
    from . import automation_spec, events

    env = env or {}
    db = env.get("db")
    if db is None:
        return []
    current = value.get("event") if isinstance(value, dict) and isinstance(value.get("event"), dict) else value
    current = current if isinstance(current, dict) else {}
    now = now_of(env)
    since = now - dt.timedelta(seconds=automation_spec.check_duration(s["within"]))
    flt: dict = {"kinds": s["kinds"], "since": since.isoformat(), "until": now.isoformat()}
    if s.get("same"):
        want = flow.dig(current, s["same"])
        if want is None:
            return []
        flt["where"] = {"path": s["same"], "op": "equals", "value": want}
    spaces = env.get("spaces")
    if spaces is not None:
        flt["spaces"] = list(spaces)
    who = env.get("viewer") or events.viewer(env.get("account"), spaces or [], admin=False)
    c = s["count"]
    # with a where of its own (tested here, with env's clock), it reads as far as it may; else just enough to count
    need = MAX_SEEN if s.get("where") else min(MAX_SEEN, (c["at_most"] + 1 if "at_most" in c else c["at_least"]) + 1)
    items = events.page(db, env.get("cfg") or {}, who, flt=flt, limit=need)["items"]
    return [a for a in items if a.get("id") != current.get("id") and (not s.get("where") or test(s["where"], a, env))]


def seen(s, value, env=None):
    """Whether a (checked) lookback finds as many activities as its count asks (at least 1 when it doesn't say)."""
    n, c = len(found(s, value, env)), s["count"]
    return n >= c.get("at_least", 0) and ("at_most" not in c or n <= c["at_most"])


def _kind_label(k):
    from . import events

    try:
        return f"“{events.kind_info(k)['label']}”"
    except KeyError:
        return k


def _seen_words(s, negated):
    c = s["count"]
    lo, hi = c.get("at_least", 0), c.get("at_most")
    if hi is None:
        n = ("none" if negated else "at least one") if lo == 1 else (f"fewer than {lo}" if negated else f"at least {lo}")
    elif hi == 0:
        n = "at least one" if negated else "none"
    elif negated:
        n = f"more than {hi}" if not lo else f"not {lo} to {hi}"
    else:
        n = f"at most {hi}" if not lo else f"{lo} to {hi}"
    words = f"{n} of {_either([_kind_label(k) for k in s['kinds']])} in the last {duration_words(s['within'])}"
    if s.get("same"):
        words += f", with the same {_label(s['same'], s['kinds'][0])}"
    if s.get("where"):
        words += f", where {_describe(s['where'], s['kinds'][0], False)}"
    return words


# ---------- joins ----------
MAX_BATCH = 500  # activities a batch collects at most
SLACK = 86400  # a join row waiting on a timer is kept this long past its time, for a timer that fires late
CUT, CUT_MANY = 2000, 300  # characters of text kept in a copy of one activity, and of each in a list


def _small(v, cut=CUT, depth=0):
    """A small copy of an activity to keep while a join waits: text cut, lists and nesting kept short."""
    if isinstance(v, str):
        return v if len(v) <= cut else v[: cut - 1] + "…"
    if isinstance(v, list):
        return [_small(x, cut, depth + 1) for x in v[:20]] if depth < 6 else []
    if isinstance(v, dict):
        return {k: _small(x, cut, depth + 1) for k, x in v.items()} if depth < 6 else {}
    return v


def _published(a):
    return _time((a or {}).get("published"), dt.timezone.utc) or dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _cas(db, ref, automation, change):
    """Read a state row, work out its next state (`change(row or None)` gives (new row or None to delete it, result))
    and write it only if nobody wrote it meanwhile (its `v`), else read again. Returns the result."""
    for _ in range(8):
        row = db.one("SELECT * FROM $r", r=ref)
        new, out = change(row)
        if new is row:  # nothing to change
            return out
        if new is not None:
            new = {k: x for k, x in new.items() if k != "id"}
        if row is None:
            if new is None:
                return out
            make_room(db, automation)
            try:
                db.run(["CREATE $r CONTENT $d"], r=ref, d={**new, "automation": automation, "v": 1, "at": _stamp()})
                return out
            except Exception as e:  # noqa: BLE001 - only a row someone else made first means read again
                if "already exists" not in str(e):
                    raise
                continue
        v = row.get("v") or 0
        if new is None:
            done = db.values("DELETE $r WHERE v = $v RETURN BEFORE", r=ref, v=v)
        else:
            done = db.values(
                "UPDATE $r CONTENT $d WHERE v = $v", r=ref, d={**new, "automation": automation, "v": v + 1, "at": _stamp()}, v=v
            )
        if done:
            return out
    raise RuntimeError("a join's state kept changing while it was written; the activity is left out")


def _timers():
    """The timers module (docs/automations.md, Timers), imported when used: it imports this module's checks."""
    from . import timers

    return timers


def _arm(db, ref, aid, key, due, kind, payload):
    """Set a waiting join's timer; when it can't be set (the automation has too many waiting), drop the row so the
    next activity starts afresh, and say why in the log."""
    try:
        _timers().set(db, key, _iso(due), kind, payload, automation=aid)
    except ValueError as e:
        _take(db, {"automation": aid, "state": ref.id})
        log.warning("automation %s: its join's timer wasn't set: %s", aid, e)


def _aliases(automation):
    return [t["as"] for t in automation.get("triggers") or [] if isinstance(t, dict) and t.get("as")]


def _named(j):
    """The triggers a join waits on; activities of the others fire on their own, as with mode any."""
    if j["mode"] == "count":
        return {j["of"]}
    if j["mode"] == "absent":
        return {j["after"], j["missing"]}
    if j["mode"] == "batch" and j.get("of"):
        return {j["of"]}
    return None  # all of them


def join_step(db, cfg, automation, alias, activity):
    """What one activity of trigger `alias` completes in the automation's join: a list of completions, each
    {"as", "events": {alias: activity | [activities]}, "event": the last}, for automations.fire. The caller claims
    each activity once per automation and trigger first. Mode any, and triggers the join doesn't name, complete at
    once; all, count and absent keep what they wait on in automation_state; absent and batch set timers
    (join.absent, join.batch) that complete later."""
    from . import automation_spec

    aliases = _aliases(automation)
    if not aliases:
        raise ValueError("a join needs the automation's triggers")
    j = automation_spec.check_join(automation.get("join"), aliases)
    one = [{"as": alias, "events": {alias: activity}, "event": activity}]
    named = _named(j)
    if j["mode"] == "any" or (named is not None and alias not in named):
        return one
    aid, within = automation["id"], automation_spec.check_duration(j["within"])
    key = ""
    if j.get("key"):
        k = flow.dig(activity, j["key"])
        if k is None:
            return []  # nothing to pair it with
        key = _canon(k)
    t = _published(activity)
    if j["mode"] == "all":
        return _all(db, aid, aliases, alias, activity, key, t, within)
    if j["mode"] == "count":
        return _count(db, aid, j, alias, activity, key, t, within)
    if j["mode"] == "absent":
        return _absent(db, aid, j, alias, activity, key, t, within)
    return _batch(db, aid, alias, activity, t, within)


def _close(entries, t, within):
    return {k: e for k, e in entries.items() if abs((_published(e) - t).total_seconds()) <= within}


def _all(db, aid, aliases, alias, activity, key, t, within):
    ref = state_ref(aid, "join", "all", key)

    def change(row):
        got = _close(dict((row or {}).get("events") or {}), t, within)
        got[alias] = _small(activity)
        if all(a in got for a in aliases):
            return None, [{"as": alias, "events": {a: got[a] for a in aliases}, "event": activity}]
        last = max(_published(e) for e in got.values())
        return {
            "kind": "join",
            "alias": "all",
            "key": key[:300],
            "events": got,
            "expires_at": _iso(last + dt.timedelta(seconds=within)),
        }, []

    return _cas(db, ref, aid, change)


def _count(db, aid, j, alias, activity, key, t, within):
    ref = state_ref(aid, "join", "count", key)

    def change(row):
        got = [e for e in (row or {}).get("events") or [] if abs((_published(e) - t).total_seconds()) <= within]
        got.append(_small(activity, CUT_MANY))
        if len(got) >= j["at_least"]:  # crossed: one run, and it starts counting again
            return None, [{"as": alias, "events": {alias: got[:-1] + [activity]}, "event": activity}]
        last = max(_published(e) for e in got)
        return {
            "kind": "join",
            "alias": "count",
            "key": key[:300],
            "events": got,
            "expires_at": _iso(last + dt.timedelta(seconds=within)),
        }, []

    return _cas(db, ref, aid, change)


def _timer_key(kind, aid, key=""):
    return f"{kind}-{aid}" + (f"-{hashlib.sha1(key.encode()).hexdigest()[:20]}" if key else "")


def _absent(db, aid, j, alias, activity, key, t, within):
    ref = state_ref(aid, "join", "absent", key)
    if alias == j["after"]:
        due = t + dt.timedelta(seconds=within)

        def wait(row):
            if row is not None:  # already waiting: it completes with the first
                return row, False
            ev = {j["after"]: _small(activity)}
            return {
                "kind": "join",
                "alias": "absent",
                "key": key[:300],
                "events": ev,
                "due": _iso(due),
                "expires_at": _iso(due + dt.timedelta(seconds=SLACK)),
            }, True

        if _cas(db, ref, aid, wait):
            _arm(db, ref, aid, _timer_key("absent", aid, key), due, "join.absent", {"automation": aid, "state": ref.id, "as": j["after"]})
        return []

    def came(row):  # the missing one came: nothing is absent, if it came after what it answers
        first = ((row or {}).get("events") or {}).get(j["after"])
        if first is None or t < _published(first):
            return row, False
        return None, True

    if _cas(db, ref, aid, came):
        _timers().cancel(db, _timer_key("absent", aid, key), "the missing trigger came")
    return []


def _batch(db, aid, alias, activity, t, within):
    ref = state_ref(aid, "join", "batch", "")
    due = t + dt.timedelta(seconds=within)

    def add(row):
        if row is None:
            ev = {alias: [_small(activity, CUT_MANY)]}
            return {
                "kind": "join",
                "alias": "batch",
                "key": "",
                "events": ev,
                "n": 1,
                "last": _small(activity),
                "due": _iso(due),
                "expires_at": _iso(due + dt.timedelta(seconds=SLACK)),
            }, True
        row = dict(row)
        ev = {k: list(v) for k, v in (row.get("events") or {}).items()}
        if (row.get("n") or 0) >= MAX_BATCH:
            row["over"] = (row.get("over") or 0) + 1
            return row, False
        ev.setdefault(alias, []).append(_small(activity, CUT_MANY))
        row.update(events=ev, n=(row.get("n") or 0) + 1, last=_small(activity))
        return row, False

    if _cas(db, ref, aid, add):
        _arm(db, ref, aid, _timer_key("batch", aid), due, "join.batch", {"automation": aid, "state": ref.id, "as": alias})
    return []


def _take(db, payload):
    """A waiting join's row, taken (deleted) by the one process whose timer fired; None when it went meanwhile."""
    ref = R("automation_state", str(payload.get("state") or ""))
    aid = payload.get("automation")
    return _cas(db, ref, aid, lambda row: (None, row))


def _fire(db, cfg, aid, completion):
    from . import automations

    return automations.fire(db, cfg, aid, completion["as"], completion["event"], events=completion["events"])


def absent_fired(db, cfg, payload):
    """Timer join.absent: the missing trigger didn't come in time, so the automation runs with the first event."""
    row = _take(db, payload)
    if not row:
        return None
    first = (row.get("events") or {}).get(payload["as"])
    return _fire(db, cfg, payload["automation"], {"as": payload["as"], "events": {payload["as"]: first}, "event": first})


def batch_fired(db, cfg, payload):
    """Timer join.batch: the batch's time is up, so the automation runs once with what it collected."""
    row = _take(db, payload)
    if not row:
        return None
    if row.get("over"):
        log.warning("automation %s: its batch kept %s activities and left out %s", payload["automation"], MAX_BATCH, row["over"])
    return _fire(db, cfg, payload["automation"], {"as": payload["as"], "events": row.get("events") or {}, "event": row.get("last")})


def join_ended(db, payload, status, why):
    """A join's timer ended without firing (its automation was switched off, or Lens was off past the catch-up
    window): what it waited on goes too, so the next activity starts afresh."""
    _take(db, payload)


TIMER_HANDLERS = {"join.absent": absent_fired, "join.batch": batch_fired}


def register_timers():
    """Give the timers module the join handlers (timers.py calls this as it finishes loading)."""
    timers = _timers()
    for kind, fn in TIMER_HANDLERS.items():
        timers.handler(kind, ended=join_ended)(fn)


def describe_join(join, kind=None):
    """A join in plain words: "all of the triggers within 10 minutes, for the same sender"."""
    from . import automation_spec

    j = automation_spec.check_join(join)
    if j["mode"] == "any":
        return "each trigger on its own"
    within = duration_words(j["within"])
    same = f", for the same {_label(j['key'], kind)}" if j.get("key") else ""
    if j["mode"] == "all":
        return f"all of the triggers within {within}{same}"
    if j["mode"] == "count":
        return f"{j['at_least']} times {j['of']} within {within}{same}"
    if j["mode"] == "absent":
        return f"{j['after']}, then no {j['missing']} within {within}{same}"
    who = f"{j['of']} brings" if j.get("of") else "the triggers bring"
    return f"what {who} in {within}, together in one run"


def describe_trigger(t):
    """A trigger in plain words, as lists show it: "“Email received”, where the sender contains “@stmarys.org”",
    "every weekday at 07:00 (Europe/London)", "a call to its webhook"."""
    on = t.get("on")
    tz = t.get("timezone") or "UTC"
    kinds = [k for k in t.get("kinds") or [] if isinstance(k, str)]
    kind = kinds[0] if len(kinds) == 1 else None
    if on in ("event", "relative"):
        words = _either([_kind_label(k) for k in kinds]) if kinds else "any activity"
        if on == "relative":
            from . import timers

            words = f"{timers._offset_words(t.get('offset') or '0s')} {_label(t.get('field'), kind)} of {words}"
        if t.get("where"):
            words += f", where {_describe(t['where'], kind, False)}"
        return words
    if on == "schedule":
        from . import schedule

        return f"{schedule.describe(t.get('cron'))} ({tz})"
    if on == "at":
        return f"once, at {t.get('at')} ({tz})"
    if on == "webhook":
        return "a call to its webhook"
    if on == "manual":
        return "when you run it"
    return str(on)


def describe_triggers(triggers, join=None):
    """What sets an automation off, in one line: its triggers, then how its join takes them when there are several."""
    words = "; ".join(describe_trigger(t) for t in triggers or [])
    if len(triggers or []) > 1 and join and join.get("mode", "any") != "any":
        words += f"; {describe_join(join)}"
    return words


# ---------- which automations an activity could start ----------
def _actors(m):
    """The actor ids a where tree only lets through (actor.id equals or in), or None when it lets any through."""
    if "all" in m:
        found = [x for x in (_actors(c) for c in m["all"]) if x is not None]
        return set.intersection(*found) if found else None
    if "any" in m:
        parts = [_actors(c) for c in m["any"]]
        return set().union(*parts) if parts and None not in parts else None
    if m.get("path") == "actor.id" and m.get("op") == "equals" and isinstance(m.get("value"), str):
        return {m["value"]}
    if m.get("path") == "actor.id" and m.get("op") == "in" and all(isinstance(x, str) for x in m.get("value") or [None]):
        return set(m["value"])
    return None


def compile_triggers(automations):
    """An index of the stream triggers of the automations switched on, for the dispatcher: `kinds`, every kind they
    listen for (to follow); `by_kind`, for each kind (descendants included) the triggers listening, as
    {automation (its id), as, trigger, row (the automation)}; and `hint`, for each source whose every listening trigger names the actors it takes
    (where actor.id equals or is in), {source name: {"actors": [...]}} for events.follow, so a busy sensor table is
    read only for the sensors someone listens to. A webhook trigger listens for webhook.received."""
    from . import events

    by_kind: dict[str, list] = {}
    actors: dict[str, set | None] = {}  # per kind: the actors every trigger on it names, None when one takes any
    for a in automations or []:
        if not a.get("enabled", True):
            continue
        for t in a.get("triggers") or []:
            if t.get("on") in ("event", "relative"):
                names = t.get("kinds") or []
            elif t.get("on") == "webhook" and "webhook.received" in events._KINDS:
                names = ["webhook.received"]
            else:
                continue
            try:
                kinds = events.expand(names)
            except ValueError:  # a kind that went away: the trigger listens for nothing
                continue
            who = _actors(t["where"]) if t.get("where") else None
            for k in sorted(kinds):
                by_kind.setdefault(k, []).append({"automation": a.get("id"), "as": t.get("as"), "trigger": t, "row": a})
                actors[k] = None if who is None or (k in actors and actors[k] is None) else (actors.get(k) or set()) | who
    hint = {}
    for src in events.sources():
        mine = events.produces(src) & set(by_kind)
        if mine and all(actors[k] is not None for k in mine):
            hint[src.name] = {"actors": sorted(set().union(*(actors[k] for k in mine)))}
    return {"kinds": sorted(by_kind), "by_kind": by_kind, "hint": hint}


def candidates(index, activity):
    """The triggers in an index (compile_triggers) an activity could start: [{automation, as, trigger, row}]."""
    return list(index["by_kind"].get((activity or {}).get("kind"), []))


# ---------- the decide node ----------
DECIDE_CONFIG = {"question", "options", "input", "min_confidence"}
MAX_OPTIONS = 10
DECIDE_DAYS = 7  # an answer is kept this long, so a retry or a dry run of the same activity doesn't pay again
UNSURE = "unsure"


def check_decide(nid, cfg):
    """A decide node's settings ({question, options: {name: what it means}, input, min_confidence}), cleaned:
    ValueError naming the node and the problem."""
    from . import templates

    q = cfg.get("question")
    if not isinstance(q, str) or not q.strip() or len(q) > 1000:
        raise ValueError(f"decide {nid}: ask a question, up to 1000 characters")
    opts = cfg.get("options")
    if not isinstance(opts, dict) or not 2 <= len(opts) <= MAX_OPTIONS:
        raise ValueError(f"decide {nid}: give 2 to {MAX_OPTIONS} options, each a name and what it means")
    for name, meaning in opts.items():
        if not isinstance(name, str) or not flow.NAME_RX.match(name) or name in (UNSURE, "default"):
            raise ValueError(f"decide {nid}: name each option in lowercase letters, digits and _ (not unsure)")
        if not isinstance(meaning, str) or not meaning.strip() or len(meaning) > 300:
            raise ValueError(f"decide {nid}: say what option {name} means, up to 300 characters")
    out = {"question": q.strip(), "options": {k: v.strip() for k, v in opts.items()}}
    if cfg.get("input") is not None:
        body = cfg["input"]
        if not isinstance(body, str) or not body.strip() or len(body) > 20000:
            raise ValueError(f"decide {nid}: input is a template of up to 20000 characters, or none")
        try:
            templates.check(body)
        except templates.TemplateProblem as e:
            raise ValueError(f"decide {nid}: {e}") from None
        out["input"] = body
    mc = cfg.get("min_confidence")
    if mc is not None:
        if not _num(mc) or not 0 <= mc <= 1:
            raise ValueError(f"decide {nid}: min_confidence is from 0 to 1")
        out["min_confidence"] = float(mc)
    return out


def decide_ports(cfg):
    """A decide node's output ports: one per option, then unsure."""
    return [*(cfg.get("options") or {}), UNSURE]


def _untrusted_text(a):
    """An activity's summary and the outside text at its lens.untrusted paths, as one text."""
    lines = [str(a.get("summary") or "")]
    for p in (a.get("lens") or {}).get("untrusted") or []:
        v = flow.dig(a, p)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x is not None)
        if v not in (None, ""):
            lines.append(f"{p}: {v}")
    return "\n".join(x for x in lines if x)


def run_decide(run, frame, node, value):
    """Run a decide node: ask decide.choose (Jev, Laya, or the language model) which option fits what came in, inside
    the run's activity.scope. It reads the node's input template, rendered as prompts are (variables.prompt: each
    value from outside Lens marked, and `input`, what reached the node, counted as outside), or else the event's
    summary and outside text, wrapped with untrusted.wrap. Passes the decision ({choice, confidence, ranked, by, sure}) on along the chosen option's port, or
    along unsure when no option is sure enough (min_confidence, else decisions.act_above), nothing could answer, or
    the budget's hard limit is reached. Answers are kept per activity and question, so a retry or a dry run doesn't
    pay twice."""
    from . import activity, decide, untrusted, variables

    c, name = node["config"], node.get("label") or f"decide {node['id']}"
    ctx = run.ctx if isinstance(run.ctx, dict) else {}
    event = ctx.get("event") if isinstance(ctx.get("event"), dict) else None
    if c.get("input"):
        seen = {**ctx, "input": None if value is run.ctx else value}
        try:
            state = variables.prompt(c["input"], seen, [*variables.outside_paths(ctx), "input"], run.cfg, run.db)["text"]
        except variables.Problem as e:
            raise ValueError(f"{name}: input: {e}") from None
    else:
        text = _untrusted_text(event) if event else flow.preview(value, 20000)
        source = f"activity {event['id']}" if event and event.get("id") else "the run's input"
        state = untrusted.wrap(text, source, run.cfg)
    aid = (ctx.get("automation") or {}).get("id")
    rid = (ctx.get("run") or {}).get("id")
    asked = _canon({"q": c["question"], "o": c["options"], "i": c.get("input")})
    key = f"{event['id']}|{hashlib.sha1(asked.encode()).hexdigest()}" if event and event.get("id") else None
    ref = state_ref(aid, "decide", "", key) if key else None
    kept = run.db.one("SELECT decision FROM $r", r=ref) if ref is not None and run.db is not None else None
    if kept and isinstance(kept.get("decision"), dict):
        got = dict(kept["decision"], cached=True)
    else:
        refs = [r for r in (f"automation:{aid}" if aid else None, f"automation_run:{rid}" if rid else None) if r]
        try:
            with activity.scope(run.db, *refs, cfg=run.cfg):
                got = decide.choose(run.cfg, c["question"], c["options"], state)
        except (decide.Undecided, activity.OverBudget) as e:
            why = "over budget" if isinstance(e, activity.OverBudget) else str(e)
            run.say(f"{name}: unsure ({why})")
            return {UNSURE: {"choice": None, "confidence": 0.0, "ranked": [], "by": None, "sure": False, "why": why}}
        if ref is not None and run.db is not None:
            if not run.db.one("SELECT id FROM $r", r=ref):
                make_room(run.db, aid)
            run.db.q(
                "UPSERT $r SET automation = $a, kind = 'decide', alias = '', key = $k, decision = $d, at = $t, expires_at = $x",
                r=ref,
                a=aid,
                k=key[:300],
                d=got,
                t=_stamp(),
                x=_later(DECIDE_DAYS * 86400),
            )
    least = c.get("min_confidence")
    if least is None:
        least = float((run.cfg.get("decisions") or {}).get("act_above") or 0.8)
    sure = got.get("choice") in c["options"] and float(got.get("confidence") or 0) >= least
    out = {**got, "sure": sure}
    port = got["choice"] if sure else UNSURE
    run.say(f"{name}: {port} ({float(got.get('confidence') or 0):.2f}{', kept from before' if got.get('cached') else ''})")
    return {port: out}


# ---------- reading trees ----------
def leaves(tree):
    """The tests in a tree, in order (not the lookbacks: `lookbacks`)."""
    if "all" in tree or "any" in tree:
        return [x for m in tree.get("all", tree.get("any")) for x in leaves(m)]
    if "not" in tree:
        return leaves(tree["not"])
    return [] if "seen" in tree else [tree]


def lookbacks(tree):
    """The seen lookbacks in a tree, in order."""
    if "all" in tree or "any" in tree:
        return [x for m in tree.get("all", tree.get("any")) for x in lookbacks(m)]
    if "not" in tree:
        return lookbacks(tree["not"])
    return [tree["seen"]] if "seen" in tree else []


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
        if "seen" in inner:
            return _seen_words(inner["seen"], True)
        return _leaf(inner, kind, True) if "op" in inner else f"not ({_describe(inner, kind, True)})"
    if "seen" in m:
        return _seen_words(m["seen"], False)
    return _leaf(m, kind, False)


def _leaf(m, kind, negated):
    said = WORDS.get(m["op"], (m["op"], f"not {m['op']}"))[1 if negated else 0]
    words = f"{_label(m.get('path'), kind)} {said}"
    if m["op"] in ("exists", "empty") or (m["op"] == "changed" and not m.get("value")):
        return words
    return f"{words} {_value_words(m['op'], m.get('value'), kind)}"
