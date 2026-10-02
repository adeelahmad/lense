"""When a routine runs: five-field cron expressions (minute hour day-of-month month day-of-week) in a time zone.

`*`, lists (`1,15`), ranges (`1-5`), steps (`*/15`, `9-17/2`), month and day names (`jan`, `mon-fri`) and the
shorthands `@hourly`, `@daily`, `@weekly`, `@monthly` and `@yearly` work. As in cron, when both day fields are
restricted a day matches either of them. Sunday is 0 (or 7). Times DST skips run when the clock
gets past the gap; times it repeats run once (both times when the schedule runs every hour).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SHORTHANDS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
NAMES = {
    3: {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))},
    4: {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))},
}
BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
LABELS = ("minute", "hour", "day of month", "month", "day of week")
HORIZON_DAYS = 366 * 5  # far enough for Feb 29


def _value(text, field):
    t = text.lower()
    if t in NAMES.get(field, {}):
        return NAMES[field][t]
    if not t.isdigit():
        raise ValueError(f"cron {LABELS[field]}: {text!r} isn't a number")
    return int(t)


def _field(text, field):
    lo, hi = BOUNDS[field]
    out = set()
    for part in text.split(","):
        rng, _, step = part.partition("/")
        if step and (not step.isdigit() or int(step) < 1):
            raise ValueError(f"cron {LABELS[field]}: step {step!r} must be 1 or more")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (_value(x, field) for x in rng.split("-", 1))
        else:
            a = _value(rng, field)
            b = hi if step else a
        if field == 4 and "-" in rng and b == 0 and a > 0:
            b = 7  # mon-sun
        if not (lo <= a <= hi and lo <= b <= hi) or a > b:
            raise ValueError(f"cron {LABELS[field]}: {part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, int(step or 1)))
    if field == 4 and 7 in out:
        out = (out - {7}) | {0}
    return out


def parse(expr):
    """(minutes, hours, days, months, weekdays, day_restricted, weekday_restricted); ValueError naming the problem."""
    expr = SHORTHANDS.get((expr or "").strip().lower(), (expr or "").strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("a schedule is five fields: minute hour day-of-month month day-of-week (e.g. 0 3 * * *)")
    sets = [_field(p, i) for i, p in enumerate(parts)]
    return (*sets, parts[2] != "*", parts[4] != "*")


def zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {name!r}") from None


def check(expr, tz="UTC"):
    parse(expr)
    zone(tz)
    if next_after(expr, tz) is None:
        raise ValueError("that schedule never runs")


def _day_ok(d, days, weekdays, dr, wr):
    dom, dow = d.day in days, (d.weekday() + 1) % 7 in weekdays
    if dr and wr:
        return dom or dow
    return dom and dow


def _exists(t):
    """Whether a wall-clock time happens in its zone (DST skips some)."""
    back = t.astimezone(dt.timezone.utc).astimezone(t.tzinfo)
    return back.replace(tzinfo=None) == t.replace(tzinfo=None)


def _instants(wall, z, every_hour):
    """The UTC instants a wall-clock time stands for. A time DST skips runs when the clock gets past the gap (as cron
    does); a time DST repeats runs once, at its first occurrence, unless the schedule runs every hour (then both)."""
    t = wall.replace(tzinfo=z)
    if not _exists(t):
        while not _exists(t):
            t = (t.replace(tzinfo=None) + dt.timedelta(minutes=1)).replace(tzinfo=z)
        return [t.astimezone(dt.timezone.utc)]
    first, second = t.replace(fold=0).astimezone(dt.timezone.utc), t.replace(fold=1).astimezone(dt.timezone.utc)
    return [first, second] if every_hour and second != first else [first]


def next_after(expr, tz="UTC", after=None):
    """The first time after `after` (default now) the schedule fires, as an aware UTC datetime; None if never."""
    minutes, hours, days, months, weekdays, dr, wr = parse(expr)
    z = zone(tz)
    after = (after or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    every_hour = len(hours) == 24
    day = after.astimezone(z).date() - dt.timedelta(days=1)  # a repeated hour can fall on the day before, locally
    for _ in range(HORIZON_DAYS):
        if day.month in months and _day_ok(day, days, weekdays, dr, wr):
            found = [
                u
                for h in hours
                for m in minutes
                for u in _instants(dt.datetime(day.year, day.month, day.day, h, m), z, every_hour)
                if u > after
            ]
            if found:
                return min(found)
        day += dt.timedelta(days=1)
    return None


def describe(expr):
    """A short reading of common schedules; the expression itself otherwise."""
    e = SHORTHANDS.get((expr or "").strip().lower(), (expr or "").strip())
    p = e.split()
    if len(p) == 5 and p[0].isdigit() and p[1].isdigit() and p[2:] == ["*", "*", "*"]:
        return f"every day at {int(p[1]):02d}:{int(p[0]):02d}"
    if len(p) == 5 and p[0].isdigit() and p[1:] == ["*", "*", "*", "*"]:
        return f"every hour at :{int(p[0]):02d}"
    if len(p) == 5 and p[0].startswith("*/") and p[1:] == ["*", "*", "*", "*"]:
        return f"every {p[0][2:]} minutes"
    return e
