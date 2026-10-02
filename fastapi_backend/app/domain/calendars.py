"""iCalendar (.ics): reading a calendar into its events, one event as an .ics of its own, and an event as text.

Only what Lens needs, without extra dependencies: lines are unfolded, properties split into name, parameters and value,
and the events (VEVENT) picked out with the time zones they use. Each event keeps its own lines, so the .ics made for
it says everything the calendar said about it, but its DTSTAMP (when the file was generated, which many servers set to
now) is left out so the same event reads the same every time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc
VOLATILE = {"DTSTAMP"}


def _split(line, sep, limit=None):
    """`line` split at `sep` outside double quotes (parameter values may quote ; and :)."""
    out, cur, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted and (limit is None or len(out) < limit):
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def prop(line):
    """'DTSTART;TZID=Europe/Berlin:20261002T100000' -> ('DTSTART', {'TZID': 'Europe/Berlin'}, '20261002T100000')."""
    split = _split(line, ":", 1)
    head, value = split[0], split[1] if len(split) > 1 else ""
    parts = _split(head, ";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, value


def unescape(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v or "")


def lines(text):
    """Content lines, unfolded (a line that starts with a space or a tab continues the one before)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return [l for l in re.sub(r"\n[ \t]", "", text).split("\n") if l.strip()]


def parse(text):
    """{events: [{lines, props}], zones: {tzid: [lines]}, name}: a calendar's events (VEVENT), each with its own lines
    (alarms and all) and its top-level properties as [(name, params, value)], and its time zones' definitions."""
    events, zones, name = [], {}, None
    stack, cur = [], None
    for l in lines(text):
        n, params, v = prop(l)
        if n == "BEGIN":
            stack.append(v.strip().upper())
            if stack == ["VCALENDAR", "VEVENT"]:
                cur = {"lines": [l], "props": []}
            elif stack == ["VCALENDAR", "VTIMEZONE"]:
                cur = {"lines": [l], "props": []}
            elif cur is not None:
                cur["lines"].append(l)
            continue
        if n == "END":
            top = stack.pop() if stack else None
            if cur is not None:
                cur["lines"].append(l)
                if len(stack) == 1 and top == "VEVENT":
                    events.append(cur)
                    cur = None
                elif len(stack) == 1 and top == "VTIMEZONE":
                    tzid = next((v for k, _, v in cur["props"] if k == "TZID"), None)
                    if tzid:
                        zones[tzid] = cur["lines"]
                    cur = None
            continue
        if cur is not None:
            cur["lines"].append(l)
            if len(stack) == 2:
                cur["props"].append((n, params, v))
        elif stack == ["VCALENDAR"] and n == "X-WR-CALNAME":
            name = unescape(v).strip() or None
    return {"events": events, "zones": zones, "name": name}


def first(event, name):
    return next(((p, v) for k, p, v in event["props"] if k == name), (None, None))


def all_of(event, name):
    return [(p, v) for k, p, v in event["props"] if k == name]


def _zone(tzid):
    if not tzid:
        return None
    for candidate in (tzid, re.sub(r"^.*?([A-Za-z]+/[A-Za-z_+-]+(?:/[A-Za-z_+-]+)?)$", r"\1", tzid)):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def when(params, value):
    """A DATE or DATE-TIME value: a date (all day), an aware datetime (UTC, or its TZID's zone), or a naive one (a
    floating time, or a zone this machine doesn't know). None when it can't be read."""
    v = (value or "").strip()
    try:
        if re.fullmatch(r"\d{8}", v):
            return dt.datetime.strptime(v, "%Y%m%d").date()
        m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", v)
        if not m:
            return None
        t = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if m.group(3):
        return t.replace(tzinfo=UTC)
    zone = _zone((params or {}).get("TZID"))
    return t.replace(tzinfo=zone) if zone else t


def iso(value):
    """A date or a datetime as an ISO timestamp; a naive time is taken as UTC, a date as its midnight UTC."""
    if value is None:
        return None
    if not isinstance(value, dt.datetime):
        value = dt.datetime(value.year, value.month, value.day)
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat(timespec="seconds")


def key(event):
    """What identifies an event across fetches: its UID, and which occurrence it changes (RECURRENCE-ID)."""
    _, uid = first(event, "UID")
    _, rid = first(event, "RECURRENCE-ID")
    if not uid:
        _, uid = first(event, "SUMMARY")
        uid = f"{uid}|{first(event, 'DTSTART')[1]}"
    return f"{uid.strip()}|{(rid or '').strip()}"


def file_id(event):
    return hashlib.sha1(key(event).encode("utf-8")).hexdigest()[:16]


def summary(event):
    return unescape(first(event, "SUMMARY")[1] or "").strip()


def start(event):
    p, v = first(event, "DTSTART")
    return when(p, v)


def changed(event):
    """When the event last changed, as the calendar says (LAST-MODIFIED, else CREATED); None if it doesn't say."""
    for name in ("LAST-MODIFIED", "CREATED"):
        p, v = first(event, name)
        t = when(p, v)
        if t is not None:
            return t
    return None


def event_ics(cal, event):
    """One event as an .ics of its own: the time zones it uses, and its lines without DTSTAMP."""
    used = {p.get("TZID") for _, p, _ in event["props"] if p.get("TZID")}
    zones = [l for tz in sorted(used) for l in cal["zones"].get(tz, [])]
    body = [l for l in event["lines"] if prop(l)[0] not in VOLATILE]
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lens//calendar source//EN", *zones, *body, "END:VCALENDAR", ""])


def _person(params, value):
    mail = re.sub(r"^mailto:", "", (value or "").strip(), flags=re.I)
    name = (params or {}).get("CN", "").strip()
    return f"{name} ({mail})" if name and mail and name != mail else (name or mail)  # not <mail>: Markdown drops it


def _zone_name(t):
    if not t.tzinfo:
        return ""
    return " UTC" if t.utcoffset() == dt.timedelta(0) and str(t.tzinfo) in ("UTC", "UTC+00:00") else f" ({t.tzinfo})"


def _day(t):
    return f"{t:%A} {t.day} {t:%B %Y}"


def _shown(t):
    return _day(t) if not isinstance(t, dt.datetime) else f"{_day(t)}, {t:%H:%M}{_zone_name(t)}"


def _span(event):
    """'Friday 2 October 2026, 10:00 to 11:00 (Europe/Berlin)', 'Friday 2 October 2026 (all day)'..."""
    s = start(event)
    p, v = first(event, "DTEND")
    end = when(p, v)
    if s is None:
        return ""
    if not isinstance(s, dt.datetime):  # all day; DTEND is the day after the last
        last = end - dt.timedelta(days=1) if end is not None and not isinstance(end, dt.datetime) else None
        return f"{_day(s)} to {_day(last)} (all day)" if last and last > s else f"{_day(s)} (all day)"
    if end is None or end == s:
        return _shown(s)
    if isinstance(end, dt.datetime) and end.tzinfo == s.tzinfo and end.date() == s.date():
        return f"{_day(s)}, {s:%H:%M} to {end:%H:%M}{_zone_name(s)}"
    return f"{_shown(s)} to {_shown(end)}"


def event_text(event):
    """An event as Markdown: its summary as the heading, when and where, who, how it repeats, then its description.
    The details are 'Label — value' lines, so the transcript reader doesn't take them for speakers."""
    title = summary(event) or "(no title)"
    rows = [("When", _span(event))]
    rows.append(("Where", unescape(first(event, "LOCATION")[1] or "").strip()))
    p, v = first(event, "ORGANIZER")
    rows.append(("Organizer", _person(p, v) if v else ""))
    rows.append(("Attendees", ", ".join(_person(p, v) for p, v in all_of(event, "ATTENDEE"))))
    rows.append(("Repeats", (first(event, "RRULE")[1] or "").strip()))
    status = (first(event, "STATUS")[1] or "").strip().lower()
    rows.append(("Status", status if status and status != "confirmed" else ""))
    rows.append(("Link", (first(event, "URL")[1] or "").strip()))
    head = [f"{k} — {v}" for k, v in rows if v]
    desc = unescape(first(event, "DESCRIPTION")[1] or "").strip()
    return "\n".join([f"# {title}", "", *head, *([""] + [desc] if desc else [])]) + "\n"


def text(raw):
    """A whole calendar as Markdown, its events in order of when they start (for a .ics file read as a transcript)."""
    cal = parse(raw)
    events = sorted(cal["events"], key=lambda e: iso(start(e)) or "")
    if not events:
        raise ValueError("this calendar has no events")
    return "\n\n".join(event_text(e) for e in events)
