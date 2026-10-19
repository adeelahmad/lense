"""Asking someone's agent (docs/circles.md, When they're free): when the people in your circles are free.

Someone lets the people of a circle of theirs see when they're free (the circle's `lets` holds `free_busy`; nothing
until they pick it). Anyone in that circle, or their agent, then asks for a window and gets back only busy times: when
each event starts and ends, never what it is, where, or with whom. No model is called.

A person's events are the calendar events (a resource's `event`, calendars.details) they can read outside vaults that
they organise or are invited to by their account's email, and every event in a namespace they alone belong to (their
own calendar). A repeating event counts by its first time only.

Every ask shows in the asked person's own timeline as `agent.asked`, so nobody's agent looks behind their back.
`find_time` adds the asker's own busy times and gives the free stretches, at least so many minutes long, within the
hours of the day asked for, in a time zone.
"""

from __future__ import annotations

import datetime as dt
import re
import zoneinfo

from . import auth, circles, events, store
from .events import _s, _sample

R = store.R
MAX_DAYS = 31
MAX_PEOPLE = 20
SLACK_DAYS = 7  # how long before a window an event may start and still reach into it
ASK_FIELDS = "id AS rid, record::id(id) AS id, by, to, what, start, end, tz, at, via"
UTC = dt.timezone.utc
REFUSED = "hasn't let you see when they're free"


def _zone(tz):
    try:
        return zoneinfo.ZoneInfo(str(tz or "UTC"))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise circles.Problem(f"{tz} isn't a time zone: use one like Europe/London") from None


def _when(v, zone):
    """An ISO time, or a date for all day, as an aware datetime; a time without an offset is in `zone`. None if it
    can't be read."""
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=zone)


def window(start, end, tz="UTC"):
    """(start, end, zone) of a window to ask about, checked: Problem naming what's wrong."""
    zone = _zone(tz)
    s, e = _when(start, zone), _when(end, zone)
    if s is None or e is None:
        raise circles.Problem("give the window's start and end as ISO times, like 2026-10-20T09:00")
    if e <= s:
        raise circles.Problem("the window ends after it starts")
    if e - s > dt.timedelta(days=MAX_DAYS):
        raise circles.Problem(f"ask about at most {MAX_DAYS} days at a time")
    return s, e, zone


def allows(db, owner, asker, what="free_busy"):
    """Whether `owner` has `asker` in a circle that lets its people ask for `what`."""
    return bool(
        db.values(
            "SELECT VALUE id FROM circle WHERE owner = $o AND members CONTAINS $a AND lets CONTAINS $w LIMIT 1",
            o=int(owner),
            a=int(asker),
            w=what,
        )
    )


def _merge(spans):
    out: list[list[dt.datetime]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def busy(db, person, start, end, zone=UTC):
    """`person`'s busy times within [start, end), merged, as (start, end) pairs: their own calendar events."""
    acc = auth.get_account(db, int(person))
    if not acc:
        return []
    spaces = [s for s in auth.roles(db, acc) if not circles._vault(db, s)]
    if not spaces:
        return []
    alone = {
        r["space"]
        for r in db.rows("SELECT space, count() AS n FROM membership WHERE space IN $s GROUP BY space", s=spaces)
        if r.get("n") == 1 and db.one("SELECT id FROM membership WHERE space = $s AND account = $a LIMIT 1", s=r["space"], a=acc["id"])
    }
    rows = db.rows(
        "SELECT space, event FROM recording WHERE space IN $s AND event.startTime != NONE "
        "AND event.startTime < $hi AND event.startTime >= $lo",
        s=spaces,
        lo=(start - dt.timedelta(days=SLACK_DAYS)).date().isoformat(),
        hi=(end + dt.timedelta(days=1)).date().isoformat(),
    )
    email = str(acc.get("email") or "").lower()
    spans = []
    for r in rows:
        ev = r.get("event") or {}
        mine = (
            r["space"] in alone
            or str(ev.get("organizer") or "").lower() == email
            or email in [str(a).lower() for a in ev.get("attendees") or []]
        )
        s = _when(ev.get("startTime"), zone)
        e = _when(ev.get("endTime"), zone) or (s + dt.timedelta(hours=1) if s else None)
        if not mine or s is None or e is None or e <= start or s >= end:
            continue
        spans.append((max(s, start), min(e, end)))
    return _merge(spans)


def _iso(t, zone):
    return t.astimezone(zone).isoformat(timespec="minutes")


def _people(db, me, people):
    """[(account id or None, the name to show, what was asked for)] for the people asked about, `me` included as is."""
    if len(people) > MAX_PEOPLE:
        raise circles.Problem(f"ask about at most {MAX_PEOPLE} people at a time")
    out = []
    for p in people:
        if isinstance(p, str) and not p.strip().isdigit():
            acct = auth.find_account(db, p)
        else:
            acct = auth.get_account(db, int(p)) if str(p).strip().isdigit() else None
        live = acct and not acct.get("disabled") and not acct.get("removed_at")
        out.append((acct["id"] if live else None, (acct.get("name") or acct["email"]) if live else str(p), p))
    return out


def free_busy(db, me, people, start, end, tz="UTC", via="web"):
    """When each of `people` (account ids or emails) is busy in a window, for those who let `me` see it (and `me`
    themselves): {start, end, tz, people: [{id, name, busy: [{start, end}]} or {person, refused}]}. Each person asked
    gets agent.asked in their own timeline."""
    s, e, zone = window(start, end, tz)
    out, asked = [], []
    for pid, name, ref in _people(db, me, people):
        if pid is not None and (pid == me["id"] or allows(db, pid, me["id"])):
            out.append(
                {"id": pid, "name": name, "busy": [{"start": _iso(a, zone), "end": _iso(b, zone)} for a, b in busy(db, pid, s, e, zone)]}
            )
            if pid != me["id"]:
                asked.append(pid)
        else:
            out.append({"person": str(ref), "refused": f"{ref} {REFUSED}"})
    if asked:
        at = store.now()
        db.run(
            [f"CREATE $r{i} CONTENT $d{i}" for i in range(len(asked))],
            **{f"r{i}": R("agent_ask", db.next_id("agent_ask")) for i in range(len(asked))},
            **{
                f"d{i}": {
                    "by": me["id"],
                    "to": pid,
                    "what": "free_busy",
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "tz": str(zone),
                    "at": at,
                    "via": via,
                }
                for i, pid in enumerate(asked)
            },
        )
        events.wake()
    return {"start": _iso(s, zone), "end": _iso(e, zone), "tz": str(zone), "people": out}


def find_time(db, me, people, start, end, minutes=30, tz="UTC", day_start=9, day_end=18, limit=10, via="web"):
    """free_busy for `people` and `me`, and the stretches of at least `minutes` when all of them who let `me` see are
    free, within `day_start`..`day_end` o'clock each day in `tz`: {..free_busy, free: [{start, end}]}."""
    if not (0 <= int(day_start) < int(day_end) <= 24):
        raise circles.Problem("the day starts before it ends, in hours from 0 to 24")
    if not (5 <= int(minutes) <= 24 * 60):
        raise circles.Problem("a meeting is 5 minutes to a day long")
    who = list(dict.fromkeys([me["id"], *people]))
    got = free_busy(db, me, who, start, end, tz, via)
    s, e, zone = window(start, end, tz)
    taken = _merge([(_when(b["start"], zone), _when(b["end"], zone)) for p in got["people"] for b in p.get("busy", [])])
    free, need = [], dt.timedelta(minutes=int(minutes))
    day = s.astimezone(zone).date()
    while len(free) < int(limit) and dt.datetime.combine(day, dt.time(), zone) < e:
        lo = max(s, dt.datetime.combine(day, dt.time(), zone) + dt.timedelta(hours=int(day_start)))
        hi = min(e, dt.datetime.combine(day, dt.time(), zone) + dt.timedelta(hours=int(day_end)))
        t = lo
        for a, b in taken:
            if b <= t:
                continue
            if a >= hi:
                break
            if a - t >= need:
                free.append({"start": _iso(t, zone), "end": _iso(a, zone)})
            t = max(t, b)
        if hi - t >= need:
            free.append({"start": _iso(t, zone), "end": _iso(hi, zone)})
        day += dt.timedelta(days=1)
    return {**got, "free": free[: int(limit)]}


# ---------- as activities ----------
def _asked(db, rows):
    rows = [r for r in rows if r and r.get("at")]
    if not rows:
        return []
    names = circles._people(db, [r["by"] for r in rows])
    out = []
    for r in rows:
        actor = circles._actor(names, r["by"])
        zone = _zone(r.get("tz"))
        s, e = _when(r.get("start"), zone), _when(r.get("end"), zone)
        span = f"{s.astimezone(zone):%a %d %b} – {e.astimezone(zone):%a %d %b}" if s and e else "a time"
        who = actor["name"] if r.get("via") in (None, "web") else f"{actor['name']}'s agent"
        out.append(
            events.build(
                f"ask-{r['id']}",
                "agent.asked",
                actor=actor,
                obj=store.clean(
                    {
                        "type": "Question",
                        "name": "When are you free?",
                        "what": r.get("what"),
                        "startTime": r.get("start"),
                        "endTime": r.get("end"),
                    }
                ),
                summary=f"{who} asked when you're free, {span}",
                space=None,
                source="agent_asks",
                pos=[r["at"], r["id"]],
                published=r["at"],
                audience="owner",
                owner=r["to"],
            )
        )
    return out


class AsksSource(circles._ShareSource):
    """agent_ask rows as agent.asked, read by (at, id): the person asked alone sees each. They belong to no namespace."""

    name = "agent_asks"
    table = "agent_ask"
    fields = ASK_FIELDS
    rx = re.compile(r"^ask-(\d+)$")
    mine = "to"

    def _make(self, db, rows):
        return _asked(db, rows)

    def _spaces(self, spaces, p):
        return "true" if spaces is None else "false"


events.register_source(AsksSource())
events.kind(
    "agent.asked",
    label="Asked of your agent",
    source="agent_asks",
    as_type="Announce",
    object_type="Question",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "type": _s("Object type"),
                "name": _s("Question"),
                "what": _s("What was asked", enum=list(circles.LETS)),
                "startTime": _s("From", format="date-time"),
                "endTime": _s("To", format="date-time"),
            },
        }
    },
    sample={
        **_sample(
            "ask-3",
            "agent.asked",
            "Announce",
            "agent_asks",
            ["2026-10-18T07:10:00+00:00", 3],
            {"type": "Person", "id": "lens:account/2", "name": "Sam"},
            {
                "type": "Question",
                "name": "When are you free?",
                "what": "free_busy",
                "startTime": "2026-10-20T09:00:00+01:00",
                "endTime": "2026-10-24T18:00:00+01:00",
            },
            "Sam's agent asked when you're free, Mon 20 Oct – Fri 24 Oct",
            (),
            [],
        ),
        "space": None,
    },
)
