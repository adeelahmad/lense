"""Calendar proposals from dates said (docs/processing.md#dates, "Propose an event"): a date an asset's line gives
("let's meet next Tuesday at 3") becomes an event to add to a Google Calendar connection, prefilled from the line, that
waits in Approvals for the person's yes. Nothing is sent until they approve: the proposal is an approval of the
assistant's `add_event` (connection_tools.py), carried out by ai_tools.approve as one the assistant asked for would be,
and declining it does nothing.

What is prefilled, all of it shown in the approval and each overridable by the caller:

- the time: the day the date means (dates.py), at a time of day said in the same line ("at 3", "3pm", "15:30",
  "noon"), else all day; a weekend or a week is all of its days. A time without am or pm from 1 to 6 is in the
  afternoon, from 7 to 11 in the morning. A month or a year is too long to be one event, so it needs a start.
- the title: the line itself, shortened, so the person recognises what was said;
- who is invited: the addresses in the line and, for an email, its sender and recipients (not the calendar's own
  account or the person's), never emailed an invitation unless `invite` is true;
- the description: where it was said, with a link to that moment.

Only admins propose (adding an event through a connection is an admin's, as the assistant's tool is), only when a
calendar connection an admin let add events exists, and only for an asset they can read. Proposing the same date again
while one waits gives the one waiting. No model is called and nothing leaves Lens until the approval is approved.
"""

from __future__ import annotations

import datetime as dt
import re

from . import connection_tools, dates, events_content, store

R = store.R
TITLE_MAX = 120
ATTENDEES_MAX = 20
# a number that is a day of a month ("from 3 March"), or a count ("by 5 people", "at 2 weeks"), is no time
_NOT_TIME = rf"(?!\s*(?:st|nd|rd|th)\b|\s+(?:of\s+)?(?:{dates._MON})\b|\s+(?:%|percent|people|days?|weeks?|months?|years?|times?|minutes?|hours?)\b)"
TIME_RX = re.compile(
    r"(?<![\w:/.-])(?:(?P<noon>noon|midday)|(?:(?P<at>at|@|from|by|around)\s+)?(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?"
    r"\s*(?P<ap>a\.?m\.?|p\.?m\.?|o'?clock)?)(?![\w:/-])" + _NOT_TIME,
    re.I,
)
EMAIL_RX = re.compile(r"[^@\s<>,;\"'()]+@[^@\s<>,;\"'()]+\.[A-Za-z]{2,}")


def calendars(db):
    """The calendar connections that may add events (an admin let them, `write` yes)."""
    return [s for s in connection_tools.listing(db, "calendar") if "add_event" in s["tools"]]


def offered(db, admin):
    """Whether this person can propose an event: an admin, with a calendar connection that may add events."""
    return bool(admin) and bool(calendars(db))


def time_of_day(line, near=None):
    """The time of day a line says, as HH:MM, or None: the first one after the date's words (`near`), else the first
    in the line. A bare number counts only after "at" (or with am, pm or o'clock), so "3 March" is no time."""
    text = line or ""
    start = text.casefold().find(near.casefold()) if near else -1
    after = start + len(near) if start >= 0 else 0
    for chunk in (text[after:], text) if after else (text,):
        for m in TIME_RX.finditer(chunk):
            if m.group("noon"):
                return "12:00"
            h, mins, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").casefold().replace(".", "")
            if not (m.group("at") or ap or m.group("m")):
                continue  # a bare number: a day, a count, not a time
            if ap in ("am", "pm"):
                if not 1 <= h <= 12:
                    continue
                h = (h % 12) + (12 if ap == "pm" else 0)
            elif m.group("m") and h > 12:
                pass  # 15:30 is in the afternoon already
            elif 1 <= h <= 6:
                h += 12  # at 3: the afternoon
            if h > 23:
                continue
            return f"{h:02d}:{mins:02d}"
    return None


def _title(line):
    text = " ".join(str(line or "").split())
    if len(text) > TITLE_MAX:
        text = text[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return (text[:1].upper() + text[1:]) or None


def _said_at(db, rid, segment, index):
    rows = db.rows(
        "SELECT recording, segment, idx, t0, text, day, until, period FROM date_said WHERE recording = $r AND segment = $s",
        r=int(rid),
        s=int(segment),
    )
    rows.sort(key=lambda x: (x.get("day") or "", x.get("text") or ""))
    line = db.one("SELECT record::id(id) AS id, text, speaker FROM $s", s=R("segment", int(segment))) or {}
    if not rows:
        raise LookupError("no date was read in that line: dates are read while Settings › Analysis › Read dates is on")
    said = [r for r in rows if r["text"].casefold() in (line.get("text") or "").casefold()] or rows
    if not 0 <= int(index) < len(said):
        raise LookupError(f"that line gives {len(said)} date(s): index is 0 to {len(said) - 1}")
    return said[int(index)], line


def _guests(rec, line, skip):
    found = EMAIL_RX.findall(line or "")
    mail = events_content._mail(rec) if rec.get("mail") or rec.get("email") else {}
    for k in ("from", "to", "cc"):
        found += [a for a in mail.get(k) or [] if isinstance(a, str)]
    out, seen = [], {s.casefold() for s in skip if s}
    for a in found:
        addr = (re.search(r"<([^>]+)>", a) or [None, a])[1].strip().casefold()
        if addr and addr not in seen and EMAIL_RX.fullmatch(addr):
            seen.add(addr)
            out.append(addr)
    return out[:ATTENDEES_MAX]


def draft(db, cfg, rid, segment, index=0, user=None, web=""):
    """What would be proposed for one date said (the `index`-th in the line `segment` of asset `rid`): the add_event
    fields, prefilled, and where it was said."""
    said, line = _said_at(db, rid, segment, index)
    rec = db.one("SELECT record::id(id) AS id, title, space, mail, email FROM $r", r=R("recording", int(rid))) or {}
    period, day, until = said.get("period"), said["day"], said.get("until") or said["day"]
    at = time_of_day(line.get("text"), said["text"]) if period == "day" else None
    if period in ("month", "year"):
        start = end = None  # too long to be one event: the caller gives start
    elif at:
        start, end = f"{day}T{at}", None
    else:
        start = day
        end = (dt.date.fromisoformat(until) + dt.timedelta(days=1)).isoformat() if until != day else None
    moment = f"{web}/resources/{rid}" + (f"?t={int(said['t0']) // 1000}" if (said.get("t0") or 0) >= 1000 else "")
    where = f"“{rec.get('title') or 'an asset'}”" + (f" at {store.tc(said['t0'])}" if said.get("t0") else "")
    skip = [(user or {}).get("email")] + [c.get("account") for c in calendars(db)]
    return store.clean(
        {
            "recording_id": int(rid),
            "segment": int(segment),
            "index": int(index),
            "said": said["text"],
            "day": day,
            "until": until,
            "period": period,
            "time": at,
            "line": line.get("text"),
            "title": _title(line.get("text")) or f"{said['text'][:1].upper()}{said['text'][1:]}",
            "start": start,
            "end": end,
            "attendees": _guests(rec, line.get("text"), skip) or None,
            "description": f"Said in {where}: “{' '.join(str(line.get('text') or '').split())[:2000]}”\n{moment}",
            "space": rec.get("space"),
        }
    )


def _waiting(db, account, rid, segment, index):
    for a in db.rows(
        "SELECT record::id(id) AS id, args, summary, preview, status FROM approval "
        "WHERE account = $a AND tool = 'add_event' AND status = 'pending' AND args.said.recording = $r",
        a=account,
        r=int(rid),
    ):
        s = (a.get("args") or {}).get("said") or {}
        if s.get("segment") == int(segment) and s.get("index") == int(index):
            return a
    return None


def propose(db, cfg, user, rid, segment, index=0, web="", **given):
    """Propose one date said as an event: a pending approval of add_event, prefilled from the line, with `given`
    (title, start, end, where, description, attendees, invite, connection, calendar) in place of what was guessed.
    Gives {approval, status, summary, preview, event: the fields}, the one already waiting when it was proposed
    before."""
    old = _waiting(db, user["id"], rid, segment, index)
    if old:
        return {"approval": old["id"], "status": "waiting", "summary": old.get("summary"), "preview": old.get("preview"), "again": True}
    d = draft(db, cfg, rid, segment, index, user, web)
    pick = {k: v for k, v in given.items() if v is not None}
    start = pick.get("start") or d.get("start")
    if not start:
        raise ValueError(f"“{d['said']}” is a whole {d.get('period')}: give the event's start")
    src, cal, args = connection_tools.new_event(
        db,
        cfg,
        pick.get("connection"),
        pick.get("calendar"),
        pick.get("title") or d["title"],
        start,
        pick.get("end") if "start" in pick or "end" in pick else d.get("end"),
        pick.get("where"),
        pick.get("description") or d["description"],
        pick.get("attendees") if "attendees" in pick else d.get("attendees"),
        bool(pick.get("invite")),
    )
    args["said"] = {"recording": int(rid), "segment": int(segment), "index": int(index), "day": d["day"]}
    ev = args["event"]
    when = ev["start"].get("date") or ev["start"]["dateTime"][:16].replace("T", " ")
    summary = f"Add “{ev['summary'][:80]}” on {when} to {cal.get('name') or cal['id']}"
    if args.get("invite"):
        n = len(ev["attendees"])
        summary += f", inviting {n} {'person' if n == 1 else 'people'}"
    preview = connection_tools.event_preview(cal, args)
    aid = db.next_id("approval")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("approval", aid),
        d=store.clean(
            {
                "account": user["id"],
                "tool": "add_event",
                "args": args,
                "summary": summary,
                "preview": preview,
                "status": "pending",
                "space": d.get("space"),
                "created_at": store.now(),
            }
        ),
    )
    from . import auth

    auth.audit(db, user, "calendar.propose", f"recording:{rid}", {"approval": aid, "segment": int(segment), "source": src["id"]})
    return {"approval": aid, "status": "waiting", "summary": summary, "preview": preview, "event": ev}
