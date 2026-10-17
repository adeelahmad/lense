"""Dates said in assets, read as days on the calendar (analysis.dates, off by default; docs/processing.md#dates).

Analysis reads each line for the dates it gives and keeps them in `date_said`, one row per date: the words said, the
first and last day they mean (`day`, `until`, as YYYY-MM-DD), how long that is (`period`: day, weekend, week, month or
year) and whether it was worked out from the asset's own date (`relative`). "next Tuesday" in a recording made on
Wednesday 14 October 2026 is 2026-10-20, "last week" is 5 to 11 October, "March 2024" all of March. Agents and the
API ask by day or range (`GET /api/v1/dates`, the MCP tool `list_dates`, the assistant's `find_dates`), each date with
its line and a link to the moment.

The rules read English: day and month names, numbers ("3 March 2024", "March 3rd", "2026-10-20", "20/10/2026"), and
what's said relative to the day ("yesterday", "in three days", "two weeks ago", "this Friday", "next month"). A date
two ways ("10/11/2026") or a weekday on its own ("on Friday": the one before or the one after?) is left out rather
than guessed. "this Friday" is the coming one, "next Friday" the Friday of the week after, "last Friday" the one
before the asset's day. A year alone counts after a word that makes it one ("in 2024", "since 1998"), so "2000
people" isn't a date. Without an asset date (`recorded_at`), only dates that name their year are kept.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from . import store

R = store.R
PERIODS = ("day", "weekend", "week", "month", "year")
MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for m in names
}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}  # fmt: skip
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = "|".join(WEEKDAYS)
_N = r"\d{1,3}|" + "|".join(NUMBERS)
_UNIT = r"day|week|month|year"
_ORD = r"(?:st|nd|rd|th)?"
_YEAR = r"(?:19|20)\d{2}"
PATTERNS = [
    # absolute: a day of a month, perhaps with its year
    ("dmy", rf"(?P<d>\d{{1,2}}){_ORD}(?:\s+of)?\s+(?P<m>{_MON})\.?(?:,?\s+(?P<y>{_YEAR}))?"),
    ("mdy", rf"(?P<m>{_MON})\.?\s+(?:the\s+)?(?P<d>\d{{1,2}}){_ORD}(?:,?\s+(?P<y>{_YEAR}))?"),
    ("my", rf"(?P<m>{_MON})\.?,?\s+(?:of\s+)?(?P<y>{_YEAR})"),
    ("iso", rf"(?P<y>{_YEAR})-(?P<m>\d{{2}})-(?P<d>\d{{2}})"),
    ("num", rf"(?P<a>\d{{1,2}})[/.](?P<b>\d{{1,2}})[/.](?P<y>{_YEAR})"),
    ("year", rf"(?:in|since|until|till|by|from|during|before|after|of)\s+(?P<y>{_YEAR})"),
    # relative to the asset's day
    (
        "near",
        r"(?P<w>the\s+day\s+after\s+tomorrow|the\s+day\s+before\s+yesterday|today|tonight|yesterday|tomorrow|this\s+(?:morning|afternoon|evening))",
    ),  # fmt: skip
    ("wd", rf"(?P<q>this|next|last|coming|past)\s+(?P<wd>{_DAY})"),
    ("unit", r"(?P<q>this|next|last|past|coming)\s+(?P<u>weekend|week|month|year)"),
    ("ago", rf"(?P<n>{_N})\s+(?P<u>{_UNIT})s?\s+ago"),
    ("in", rf"(?:in\s+(?P<n>{_N})\s+(?P<u>{_UNIT})s?(?!\s+ago)|(?P<n2>{_N})\s+(?P<u2>{_UNIT})s?\s+from\s+now)"),
]
# one pass over a line: each pattern as one named group, its own groups unnamed (_SUB reads them)
FIND = re.compile("|".join(f"(?P<{k}>(?<![\\w-])(?:{re.sub(r'[(][?]P<[a-z0-9]+>', '(?:', p)})(?![\\w-]))" for k, p in PATTERNS), re.I)
_SUB = {k: re.compile(p, re.I) for k, p in PATTERNS}


def base_day(recorded_at):
    """The day an asset was made, as recorded_at gives it (an ISO date and time), or None."""
    try:
        return dt.datetime.fromisoformat(str(recorded_at)[:19]).date() if recorded_at else None
    except ValueError:
        return None


def day_bound(value, last=False):
    """YYYY-MM-DD, YYYY-MM or YYYY as a day (YYYY-MM-DD): the first day it holds, or with `last` the last; None for
    nothing. ValueError for anything else."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return dt.date.fromisoformat(v).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}", v):
            y, m = int(v[:4]), int(v[5:])
            return (_month_end(y, m) if last else dt.date(y, m, 1)).isoformat()
        if re.fullmatch(r"\d{4}", v):
            return f"{v}-12-31" if last else f"{v}-01-01"
    except ValueError:
        pass
    raise ValueError(f"{v} isn't a day: YYYY-MM-DD, YYYY-MM or YYYY")


def _n(x):
    x = (x or "").lower()
    return int(x) if x.isdigit() else NUMBERS.get(x)


def _month_end(y, m):
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _add(day, unit, n):
    if unit == "day":
        return day + dt.timedelta(days=n)
    if unit == "week":
        return day + dt.timedelta(weeks=n)
    if unit == "month":
        m = day.month - 1 + n
        y, m = day.year + m // 12, m % 12 + 1
        return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))
    return dt.date(day.year + n, day.month, min(day.day, calendar.monthrange(day.year + n, day.month)[1]))


def _span(day, unit):
    """(first, last day, period) of the `unit` that holds `day`."""
    if unit == "week":
        start = day - dt.timedelta(days=day.weekday())
        return start, start + dt.timedelta(days=6), "week"
    if unit == "weekend":
        start = day - dt.timedelta(days=day.weekday()) + dt.timedelta(days=5)
        return start, start + dt.timedelta(days=1), "weekend"
    if unit == "month":
        return day.replace(day=1), _month_end(day.year, day.month), "month"
    if unit == "year":
        return dt.date(day.year, 1, 1), dt.date(day.year, 12, 31), "year"
    return day, day, "day"


def _verb_like(month):
    """A month's short name ("mar", "dec") or "may" in small letters is more likely a word than a month."""
    low = month.lower()
    return not month[:1].isupper() and (low == "may" or (len(low) <= 4 and low not in ("june", "july")))


def resolve(kind, m, base):
    """(first, last, period, relative) for one match of PATTERNS[kind], or None when it isn't a day on the calendar."""
    g = {k: v for k, v in m.groupdict().items() if v}
    if kind in ("dmy", "mdy", "my", "iso", "num"):
        if kind == "num":
            a, b = int(g["a"]), int(g["b"])
            if a <= 12 and b <= 12 and a != b:
                return None  # 10/11: October or November?
            d, mo = (a, b) if a > 12 or a == b else (b, a)
        else:
            if kind != "iso" and _verb_like(g["m"]):
                return None
            mo = int(g["m"]) if kind == "iso" else MONTHS[g["m"].lower()]
            d = int(g.get("d") or 0)
        y = int(g["y"]) if g.get("y") else (base.year if base else None)
        if y is None:
            return None
        try:
            if not d:
                return dt.date(y, mo, 1), _month_end(y, mo), "month", False
            day = dt.date(y, mo, d)
        except ValueError:
            return None
        return day, day, "day", not g.get("y")
    if kind == "year":
        return dt.date(int(g["y"]), 1, 1), dt.date(int(g["y"]), 12, 31), "year", False
    if base is None:
        return None
    if kind == "near":
        w = " ".join(g["w"].lower().split())
        shift = {"yesterday": -1, "tomorrow": 1, "the day after tomorrow": 2, "the day before yesterday": -2}.get(w, 0)
        day = base + dt.timedelta(days=shift)
        return day, day, "day", True
    if kind == "wd":
        q, want = g["q"].lower(), WEEKDAYS.index(g["wd"].lower())
        if q in ("this", "coming"):
            day = base + dt.timedelta(days=(want - base.weekday()) % 7)
        elif q == "next":
            day = base - dt.timedelta(days=base.weekday()) + dt.timedelta(weeks=1, days=want)
        else:  # last, past: the one before the asset's day
            day = base - dt.timedelta(days=(base.weekday() - want) % 7 or 7)
        return day, day, "day", True
    if kind == "unit":
        q, unit = g["q"].lower(), g["u"].lower()
        step = 0 if q == "this" else (1 if q in ("next", "coming") else -1)
        day = _add(base, "week" if unit == "weekend" else unit, step)
        return (*_span(day, unit), True)
    n, unit = _n(g.get("n") or g.get("n2")), (g.get("u") or g.get("u2")).lower()
    if not n:
        return None
    day = _add(base, unit, -n if kind == "ago" else n)
    return (*_span(day, unit), True)


def find(text, base=None):
    """The dates a line gives: [{text, day, until, period, relative}], in the order said."""
    out = []
    for m in FIND.finditer(text or ""):
        kind = m.lastgroup
        sub = _SUB[kind].fullmatch(m.group(kind))
        got = resolve(kind, sub, base) if sub else None
        if got:
            first, last, period, relative = got
            said = sub.group("y") if kind == "year" else m.group(kind)  # "in 2024": the year is the date
            out.append({"text": said, "day": first.isoformat(), "until": last.isoformat(), "period": period, "relative": relative})
    return out


def index_recording(db, cfg, rid, segs, space, recorded_at):
    """Keep the dates a recording's lines give (when Settings → Analysis reads dates); how many."""
    if not (cfg.get("analysis") or {}).get("dates"):
        db.q("DELETE date_said WHERE recording = $r", r=rid)
        return 0
    base = base_day(recorded_at)
    rows = [
        {"recording": rid, "space": space, "segment": s["id"], "idx": s["idx"], "t0": s.get("t0"), **d}
        for s in segs
        for d in find(s.get("text"), base)
    ]
    db.run(["DELETE date_said WHERE recording = $r"] + (["INSERT INTO date_said $rows"] if rows else []), r=rid, rows=rows)
    return len(rows)


def said(db, spaces, within=None, day_from=None, day_to=None, recording=None, q=None, limit=50, offset=0):
    """Dates said in the assets of these namespaces (and the collections in `within`) that fall in [day_from, day_to]
    (either may be left open), the earliest first: {total, items: [...]} with each date's line."""
    from . import library, render

    where, p = ["space IN $all"], {"all": sorted(set(spaces) | set(within or {}))}
    if day_from:
        where.append("until >= $a")
        p["a"] = day_from
    if day_to:
        where.append("day <= $b")
        p["b"] = day_to
    if recording is not None:
        where.append("recording = $rec")
        p["rec"] = int(recording)
    rows = db.rows(
        f"SELECT recording, segment, idx, t0, text, day, until, period, relative FROM date_said WHERE {' AND '.join(where)}", **p
    )
    if not rows:
        return {"total": 0, "items": []}
    cond, sp = library.in_scope(spaces, within)
    recs = {
        r["id"]: r
        for r in db.rows(
            f"SELECT record::id(id) AS id, title, recorded_at, space FROM recording WHERE id IN $ids AND {cond}",
            ids=[R("recording", i) for i in {x["recording"] for x in rows}],
            **sp,
        )
    }
    rows = [x for x in rows if x["recording"] in recs]
    if not rows:
        return {"total": 0, "items": []}
    seg_ids = [R("segment", i) for i in {x["segment"] for x in rows}]
    lines = {s["id"]: s for s in db.rows("SELECT record::id(id) AS id, text, speaker FROM segment WHERE id IN $ids", ids=seg_ids)}
    if q:
        needle = q.lower()
        rows = [x for x in rows if needle in (lines.get(x["segment"], {}).get("text") or "").lower() or needle in x["text"].lower()]
    rows.sort(key=lambda x: (x["day"], x["until"], recs[x["recording"]].get("recorded_at") or "", x["segment"]))
    page = rows[offset : offset + limit]
    names, spaces_n = render.speaker_names(db, [lines.get(x["segment"], {}).get("speaker") for x in page]), store.space_names(db)
    items = []
    for x in page:
        rec, line = recs[x["recording"]], lines.get(x["segment"], {})
        items.append(
            store.clean(
                {
                    "day": x["day"],
                    "until": x["until"],
                    "period": x["period"],
                    "said": x["text"],
                    "relative": bool(x.get("relative")),
                    "recording_id": x["recording"],
                    "title": rec.get("title"),
                    "namespace": spaces_n.get(rec.get("space")),
                    "recorded_at": rec.get("recorded_at"),
                    "segment": x["segment"],
                    "idx": x["idx"],
                    "t0": x.get("t0"),
                    "time": store.tc(x.get("t0")),
                    "speaker": names.get(line.get("speaker")),
                    "text": line.get("text"),
                }
            )
        )
    return {"total": len(rows), "items": items}
