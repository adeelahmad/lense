"""Google Calendar as a source (a file sensor, as feeds.py's iCal feeds are), through the Calendar API: each calendar is
a folder and each event a file `<calendar id>/<event id>.ics`, so browsing, choosing events, watching and importing
work as they do for any source.

It signs in as a service account or a connected Google account (google_auth.py), with read-only access
(calendar.readonly): nothing is changed in the calendar. `calendars` (calendar ids, comma separated) limits it to
those; otherwise it reads the account's own calendar list, which a service account only has when it acts as a
Workspace user (`impersonate`): share a calendar with the service account's address and give its id instead.
Watching the whole account (no path) takes every calendar in it but Google's own (holidays, birthdays, week
numbers). Events that ended more than `days_back` days ago are left out (empty: all of them).

An event is written as an iCalendar event of its own (its title, when and where, organizer and attendees with their
answers, how it repeats and the occurrences cancelled, status, video call, its page in Google Calendar, and when it
was created and changed), so it is read as text exactly as an iCal feed's events are (calendars.py). A changed
occurrence of a repeating event is an event of its own, as Google keeps it. Working-location entries ("Home",
"Office") aren't events and are left out.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import time
import urllib.parse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import calendars, google_auth

API = "https://www.googleapis.com/calendar/v3"
SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)
TYPE = {
    "label": "Google Calendar",
    "fields": {"calendars": "", "impersonate": "", "days_back": "365", "account": ""},  # account: the one connected
    "secrets": ["token", "service_account_credentials"],
    "google": True,  # signs in through google_auth.py
}
SYSTEM = "group.v.calendar.google.com"  # Google's own calendars (holidays, birthdays, week numbers) end so
SKIP_TYPES = {"workingLocation"}
PAGE = 2500  # the most events.list gives at once
MAX_PAGES = 40
LIST_SECONDS = 60  # a calendar listed for a scan is reused this long (the scan then reads each of its events)
EPOCH = "1970-01-01T00:00:00+00:00"
_LISTED: dict = {}  # (source id, calendar id) -> (monotonic time, {event id.ics: (entry, body)})
_CALENDARS: dict = {}  # source id -> (monotonic time, its calendars)
PARTSTAT = {"needsAction": "NEEDS-ACTION", "accepted": "ACCEPTED", "declined": "DECLINED", "tentative": "TENTATIVE"}


def _params(src):
    return {k: str(v).strip() for k, v in (src.get("params") or {}).items() if v is not None}


def _call(cfg, src, path, params=None, what=None):
    creds = google_auth.credentials(cfg, src)
    return google_auth.get(cfg, creds, SCOPES, API + path, params, api="calendar", what=what)


def _q(cal_id):
    return urllib.parse.quote(cal_id, safe="@")


# ---------- calendars ----------
def _chosen(src):
    return [c.strip() for c in re.split(r"[,\s]+", _params(src).get("calendars", "")) if c.strip()]


def calendars_of(cfg, src):
    """[{id, name, zone}] of the calendars this source reads: the chosen ones, else the account's calendar list (those
    it can read events of), reused for LIST_SECONDS."""
    hit = _CALENDARS.get(src["id"])
    if hit and time.monotonic() - hit[0] < LIST_SECONDS and hit[1] == _chosen(src):
        return hit[2]
    out = _calendars(cfg, src)
    _CALENDARS[src["id"]] = (time.monotonic(), _chosen(src), out)
    return out


def _calendars(cfg, src):
    chosen = _chosen(src)
    if chosen:
        out = []
        for cid in chosen:
            try:
                c = _call(cfg, src, f"/calendars/{_q(cid)}", what="calendars.get")
            except FileNotFoundError:
                raise ValueError(
                    f"there's no calendar {cid}, or it isn't shared with {google_auth.who(google_auth.credentials(cfg, src))}"
                ) from None
            out.append({"id": c.get("id") or cid, "name": c.get("summary") or cid, "zone": c.get("timeZone")})
        return out
    out, token = [], None
    for _ in range(MAX_PAGES):
        q = {"minAccessRole": "reader", "maxResults": 250, **({"pageToken": token} if token else {})}
        page = _call(cfg, src, "/users/me/calendarList", q, what="calendarList.list")
        for c in page.get("items") or []:
            if c.get("id") and not c.get("deleted"):
                out.append(
                    {
                        "id": c["id"],
                        "name": c.get("summaryOverride") or c.get("summary") or c["id"],
                        "zone": c.get("timeZone"),
                        "primary": bool(c.get("primary")),
                    }
                )
        token = page.get("nextPageToken")
        if not token:
            break
    return sorted(out, key=lambda c: (not c.get("primary"), c["name"].lower()))


# ---------- an event as iCalendar ----------
def _plain(v):
    """A value written as it is (an address, a link, a rule): on one line, without control characters."""
    return re.sub(r"[\x00-\x1f\x7f]", "", str(v)).strip()


def _escape(v):
    return (
        re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", str(v))
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\n", "\\n")
    )


def _param(v):
    v = re.sub(r'[\x00-\x1f"]', "", str(v))
    return f'"{v}"' if re.search(r"[;:,]", v) else v


def _fold(line):
    """A content line folded at 75 characters, continuations starting with a space (RFC 5545 §3.1)."""
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def _zone(name):
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _instant(v):
    try:
        t = dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", v.replace("Z", "+00:00")))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _when(name, t, zone):
    """'DTSTART;TZID=Europe/Berlin:20261002T100000', 'DTSTART;VALUE=DATE:20261017' or 'DTSTART:20261002T080000Z'
    for Google's {dateTime, timeZone} or {date}; None when there's neither."""
    t = t or {}
    if t.get("date"):
        return f"{name};VALUE=DATE:{t['date'].replace('-', '')}"
    at = _instant(t.get("dateTime"))
    if at is None:
        return None
    z = _zone(t.get("timeZone")) or _zone(zone)
    if z is not None and str(z) not in ("UTC", "Etc/UTC"):
        return f"{name};TZID={z}:{at.astimezone(z):%Y%m%dT%H%M%S}"
    return f"{name}:{at.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}"


def _stamp(v):
    at = _instant(v)
    return at.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") if at else None


def _text(desc):
    """A description as text: Google Calendar's web app writes some as HTML (line breaks, links, lists)."""
    if not re.search(r"<(br|/?p|/?div|/?ul|/?ol|li|a|b|i|u|span)\b", desc or "", re.I):
        return desc or ""
    t = re.sub(
        r"<a\s[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>",
        lambda m: m.group(2) if html.unescape(m.group(1)) in (m.group(2), "") else f"{m.group(2)} ({m.group(1)})",
        desc,
        flags=re.I | re.S,
    )
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t, flags=re.I)
    t = re.sub(r"<li\b[^>]*>", "- ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _person(name, p):
    params = []
    if p.get("displayName"):
        params.append(f"CN={_param(p['displayName'])}")
    if name == "ATTENDEE":
        if p.get("resource"):
            params.append("CUTYPE=RESOURCE")
        params.append(f"ROLE={'OPT-PARTICIPANT' if p.get('optional') else 'REQ-PARTICIPANT'}")
        if PARTSTAT.get(p.get("responseStatus")):
            params.append(f"PARTSTAT={PARTSTAT[p['responseStatus']]}")
    return f"{name}{''.join(';' + x for x in params)}:mailto:{_plain(p['email'])}"


def _meeting(ev):
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for e in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if e.get("entryPointType") == "video" and e.get("uri"):
            return e["uri"]
    return None


def event_ics(ev, cal, cancelled=()):
    """One of Google's events as an .ics of its own. `cancelled` are the RECURRENCE-ID values of the occurrences of a
    repeating event that were cancelled, written as its EXDATEs."""
    zone = cal.get("zone")
    lines = ["BEGIN:VEVENT", f"UID:{_plain(ev.get('iCalUID') or ev['id'])}"]
    rid = _when("RECURRENCE-ID", ev.get("originalStartTime"), zone)
    lines += [x for x in (rid, _when("DTSTART", ev.get("start"), zone), _when("DTEND", ev.get("end"), zone)) if x]
    for name, value in (("SUMMARY", ev.get("summary")), ("LOCATION", ev.get("location")), ("DESCRIPTION", _text(ev.get("description")))):
        if value:
            lines.append(f"{name}:{_escape(value)}")
    if (ev.get("organizer") or {}).get("email"):
        lines.append(_person("ORGANIZER", ev["organizer"]))
    lines += [_person("ATTENDEE", a) for a in ev.get("attendees") or [] if a.get("email")]
    for rule in ev.get("recurrence") or []:  # RRULE, EXRULE, RDATE and EXDATE lines, as iCalendar writes them
        if re.match(r"^(RRULE|EXRULE|RDATE|EXDATE)[:;]", rule):
            lines.append(_plain(rule))
    lines += [x.replace("RECURRENCE-ID", "EXDATE", 1) for x in sorted(cancelled)]
    if ev.get("status"):
        lines.append(f"STATUS:{_plain(ev['status']).upper()}")
    if ev.get("transparency") == "transparent":
        lines.append("TRANSP:TRANSPARENT")
    if ev.get("visibility") in ("private", "confidential"):
        lines.append(f"CLASS:{ev['visibility'].upper()}")
    if _meeting(ev):
        lines.append(f"CONFERENCE;VALUE=URI;FEATURE=VIDEO:{_plain(_meeting(ev))}")
    if ev.get("htmlLink"):
        lines.append(f"URL:{_plain(ev['htmlLink'])}")
    for name, value in (("CREATED", ev.get("created")), ("LAST-MODIFIED", ev.get("updated"))):
        if _stamp(value):
            lines.append(f"{name}:{_stamp(value)}")
    lines.append("END:VEVENT")
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lens//Google Calendar source//EN", f"X-WR-CALNAME:{_escape(cal['name'])}"]
    return "\r\n".join([*head, *(_fold(l) for l in lines), "END:VCALENDAR", ""])


# ---------- events as files ----------
def _since(src):
    days = _params(src).get("days_back", "")
    if not days:
        return None
    try:
        n = int(days)
    except ValueError:
        raise ValueError("days back is a number of days (empty for all events)") from None
    if n <= 0:
        return None
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _items(cfg, src, cal):
    """Every event of a calendar Google gives (repeating ones once, with their changed occurrences), and its time zone."""
    out, token, zone, since = [], None, cal.get("zone"), _since(src)
    for _ in range(MAX_PAGES):
        q = {
            "maxResults": PAGE,
            "singleEvents": "false",
            "showDeleted": "false",
            **({"timeMin": since} if since else {}),
            **({"pageToken": token} if token else {}),
        }
        try:
            page = _call(cfg, src, f"/calendars/{_q(cal['id'])}/events", q, what="events.list")
        except FileNotFoundError:
            raise ValueError(f"there's no calendar {cal['id']}, or it isn't shared with this account") from None
        zone = zone or page.get("timeZone")
        out += page.get("items") or []
        token = page.get("nextPageToken")
        if not token:
            break
    return out, zone


def _listing(cfg, src, cal):
    """{event id.ics: (entry, body)} of a calendar, reused for LIST_SECONDS (a scan lists, then reads each event)."""
    hit = _LISTED.get((src["id"], cal["id"]))
    if hit and time.monotonic() - hit[0] < LIST_SECONDS:
        return hit[1]
    items, zone = _items(cfg, src, cal)
    cal = {**cal, "zone": zone}
    cancelled: dict[str, set] = {}
    for ev in items:
        if ev.get("status") == "cancelled" and ev.get("recurringEventId"):
            rid = _when("RECURRENCE-ID", ev.get("originalStartTime"), zone)
            if rid:
                cancelled.setdefault(ev["recurringEventId"], set()).add(rid)
    out = {}
    for ev in items:
        if ev.get("status") == "cancelled" or ev.get("eventType") in SKIP_TYPES or not re.fullmatch(r"[A-Za-z0-9_-]+", ev.get("id") or ""):
            continue
        body = event_ics(ev, cal, cancelled.get(ev["id"], ())).encode("utf-8")
        parsed = calendars.parse(body.decode("utf-8"))["events"][0]
        start = calendars.start(parsed)
        title = calendars.summary(parsed) or "(no title)"
        day = calendars.iso(start)[:10] + " " if start is not None else ""
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", f"{day}{title}")[:150].strip()
        rel = f"{ev['id']}.ics"
        out[rel] = (
            {
                "path": f"{cal['id']}/{rel}",
                "rel": rel,
                "name": f"{name}.ics",
                "dir": False,
                "size": len(body),
                "modified": calendars.iso(calendars.changed(parsed)) or EPOCH,
                "title": title,
                "when": calendars.iso(start),
            },
            body,
        )
    _LISTED[(src["id"], cal["id"])] = (time.monotonic(), out)
    return out


def events(cfg, src, cal, base=""):
    """A calendar's events as files, their `rel` under `base` (the calendar's own folder when the whole account is
    listed)."""
    return [{**e, "rel": base + e["rel"]} for e, _ in _listing(cfg, src, cal).values()]


def _calendar(cfg, src, cal_id):
    cal = next((c for c in calendars_of(cfg, src) if c["id"] == cal_id), None)
    if cal is None and not _chosen(src):  # one the account can read but doesn't list (shared, not added)
        cal = {"id": cal_id, "name": cal_id, "zone": None}
    if cal is None:
        raise ValueError(f"there's no calendar {cal_id} in this connection")
    return cal


# ---------- what feeds.py calls ----------
def browse(cfg, src, path):
    if not path:
        return [
            {"path": c["id"], "rel": c["id"], "name": c["name"], "dir": True, "size": None, "modified": None}
            for c in calendars_of(cfg, src)
        ]
    if "/" in path:
        raise ValueError("a calendar has no folders")
    return sorted(events(cfg, src, _calendar(cfg, src, path)), key=lambda e: e["when"] or "", reverse=True)


def list_files(cfg, src, path, cursor=None):
    """A calendar's events, or every calendar's (no path) but Google's own."""
    if path:
        return browse(cfg, src, path)
    out = []
    for c in calendars_of(cfg, src):
        if c["id"].endswith(SYSTEM) and not _chosen(src):
            continue
        out += events(cfg, src, c, f"{c['id']}/")
    return out


def fetch(cfg, src, path):
    cal_id, _, name = path.rpartition("/")
    if not cal_id or not name.endswith(".ics"):
        raise FileNotFoundError("that isn't an event (calendar id/event id.ics)")
    hit = _LISTED.get((src["id"], cal_id))
    listed = hit[1] if hit and time.monotonic() - hit[0] < LIST_SECONDS else _listing(cfg, src, _calendar(cfg, src, cal_id))
    if name not in listed:
        raise FileNotFoundError("that event isn't in the calendar any more")
    return listed[name][1]


def immutable(src):
    return False  # an event can change


def forget(src):
    """Drop what was listed for this source, so the next call asks Google again."""
    _CALENDARS.pop(src["id"], None)
    for k in [k for k in _LISTED if k[0] == src["id"]]:
        _LISTED.pop(k, None)


def test(cfg, src):
    forget(src)
    google_auth.credentials(cfg, src)  # says what's missing before anything is called
    _since(src)
    found = calendars_of(cfg, src)
    if not found:
        creds = google_auth.credentials(cfg, src)
        if creds["kind"] == "service_account" and not creds["subject"]:
            raise RuntimeError(
                f"signed in, but {creds['key']['client_email']} has no calendars of its own: share a calendar with that "
                "address and enter its ID in Calendars, or act as a Workspace user"
            )
        raise RuntimeError("signed in, but the account has no calendars")
    events(cfg, src, found[0])  # reads events, as watching will
