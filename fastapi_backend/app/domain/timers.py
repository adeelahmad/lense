"""Timers (docs/automations.md, Time): moments when something runs, kept in the database so they outlast a restart.
Automations' schedules, their `at` and `relative` triggers, actions that wait and joins that wait all use them.

A timer is one row per key (`timer:<hash of key>`): when it is due (`at`, UTC), what runs then (`kind`, a function
registered with `@handler(kind)`), what that gets (`payload`), and the automation it is for. `set` makes one, or
replaces the one with that key; `cancel` and `cancel_for` call them off. A thread (`start`, started with the
routines' threads, so in the API's background work and in `lens worker`) looks every CHECK_SECONDS, and at once after
`events.wake()`, and fires what is due (`due`). Each due timer is claimed with a compare-and-set update that only one
process wins, then its handler runs, so a timer fires once even with the API and the worker both looking. A handler
that repeats (`again`: a schedule's next time) is re-armed in that same update, so a repeating timer is never lost; a
process that dies while a handler runs leaves that one firing done (at most once, never twice).

What sets them: `schedule_automation` (an automation's schedule and at triggers; they fire it with a `clock.tick`
activity), `relative` (a relative trigger's time for one activity, moved when the activity's object moves), and
`later` (an action that waits: carried out at its time, asking first when it should then). `upcoming` lists what an
automation has waiting, in words.

While Lens was off: a timer due within automations.catch_up (1 hour by default) fires late, with `late: true` in its
payload; an older one is missed (counted on its automation). A repeating one fires once, for its latest time in the
window, and then waits for its next. Fired, failed, cancelled and missed rows are dropped after KEEP_DAYS (`tidy`,
which routines.run_due calls; once an hour). Nothing looks at the table until a timer is set: while none waits, the
thread doesn't query it (it checks again once a minute, or at once when this process sets one).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from . import automation_spec, events, store

R = store.R
KEY_RX = re.compile(r"^[A-Za-z0-9_.:|/@+-]{1,300}$")
KIND_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}(\.[a-z][a-z0-9_]{0,40}){1,2}$")
STATUSES = ("waiting", "fired", "failed", "cancelled", "missed")
ENDED = ("fired", "failed", "cancelled", "missed")
CHECK_SECONDS = 5  # how often the thread looks when nothing wakes it
IDLE_SECONDS = 60  # how long "no timer waits" is believed before the table is looked at again
LATE_SECONDS = 120  # a timer fired later than this says so (late: true); within it, it is on time
BATCH = 100  # timers fired per look at most
MAX_WAITING = 1000  # waiting timers per automation at most
MAX_PAYLOAD = 64_000  # bytes of JSON a timer's payload holds at most
MAX_REPEATS = 1000  # times of a repeating timer stepped through when looking for its latest missed one
KEEP_DAYS = 7
SWEEP_SECONDS = 3600
CATCH_UP = "1h"
FIELDS = "key, at, kind, payload, automation, status, fired_at, done_at, claimed_by, note, last, runs, created_at"

log = logging.getLogger("lens.automations")
_ME = f"{socket.gethostname()}-{os.getpid()}"
_idle = {"until": 0.0}  # monotonic time until which no timer is believed to wait
_swept = {"at": 0.0}
_lock = threading.Lock()


@dataclass(frozen=True)
class Handler:
    """What runs when a timer of one kind fires: `fn(db, cfg, payload)`. `again(payload, after)` gives a repeating
    timer's next time after `after` (None: it doesn't repeat, or not any more). `ended(db, payload, status, why)` is
    told when one ends without firing (cancelled or missed), for what it stood for to say so."""

    kind: str
    fn: Callable[..., object]
    again: Callable[[dict, dt.datetime], dt.datetime | None] | None = None
    ended: Callable[..., object] | None = None


_HANDLERS: dict[str, Handler] = {}


def handler(kind, again=None, ended=None):
    """Register what runs when a timer of `kind` fires (a decorator): `@timers.handler("join.absent")` over
    `def _(db, cfg, payload)`. The payload is the one the timer was set with, plus `late` (fired later than
    LATE_SECONDS after its time) and `timer` ({key, at, automation}). Registering a kind again replaces it."""
    if not KIND_RX.match(kind or ""):
        raise ValueError(f"a timer's kind is named like schedule.next, not {kind}")

    def register(fn):
        _HANDLERS[kind] = Handler(kind, fn, again, ended)
        return fn

    return register


def handlers():
    return sorted(_HANDLERS)


# ---------- times ----------
def _utc(t):
    """An aware UTC datetime from a datetime (naive is UTC) or an ISO 8601 string; ValueError otherwise."""
    if isinstance(t, dt.datetime):
        d = t
    elif isinstance(t, str) and t.strip():
        s = t.strip()
        d = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    else:
        raise ValueError(f"{t!r} isn't a time")
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def _iso(t):
    """A time as timers keep it: to the second, in UTC (2026-10-16T07:30:00+00:00), so times sort as text."""
    return _utc(t).replace(microsecond=0).isoformat(timespec="seconds")


def _now(now=None):
    return _utc(now) if now is not None else dt.datetime.now(dt.timezone.utc)


def catch_up(cfg):
    """Seconds a timer may be late and still fire, after Lens was off (automations.catch_up)."""
    text = ((cfg or {}).get("automations") or {}).get("catch_up") or CATCH_UP
    try:
        return automation_spec.check_duration(text, what="automations.catch_up")
    except ValueError:
        return automation_spec.check_duration(CATCH_UP)


# ---------- setting and calling off ----------
def _ref(key):
    return R("timer", hashlib.sha1(key.encode()).hexdigest())


def _check_key(key):
    if not isinstance(key, str) or not KEY_RX.match(key):
        raise ValueError("a timer's key is 1 to 300 letters, digits and _ . : | / @ + -, like sched-4-morning")
    return key


def _view(r):
    return {k: r.get(k) for k in ("key", "at", "kind", "payload", "automation", "status", "fired_at", "note", "last", "runs")}


def get(db, key):
    """One timer by its key, or None."""
    r = db.one(f"SELECT {FIELDS} FROM $r", r=_ref(_check_key(key)))
    return _view(r) if r else None


def _waiting_for(db, automation):
    row = db.one("SELECT count() AS n FROM timer WHERE automation = $a AND status = 'waiting' GROUP ALL", a=automation)
    return int((row or {}).get("n") or 0)


def set(db, key, at, kind, payload=None, automation=None):  # noqa: A001 - timers.set, as the contract names it
    """Make the timer `key` due at `at` (a datetime or ISO 8601 time) to run `kind`'s handler with `payload`, or
    replace the one with that key, whatever it was doing (waiting, fired, ...). `automation` is the automation it is
    for, which may have at most MAX_WAITING timers waiting. The timer, as `get` gives it. ValueError naming the
    problem."""
    _check_key(key)
    if not KIND_RX.match(kind or ""):
        raise ValueError(f"a timer's kind is named like schedule.next, not {kind}")
    try:
        when = _iso(at)
    except (TypeError, ValueError):
        raise ValueError(f"timer {key}: at is a time like 2026-10-16T07:30:00Z") from None
    payload = dict(payload or {})
    try:
        size = len(json.dumps(payload))
    except (TypeError, ValueError):
        raise ValueError(f"timer {key}: its payload is plain JSON") from None
    if size > MAX_PAYLOAD:
        raise ValueError(f"timer {key}: its payload is at most {MAX_PAYLOAD // 1000} KB")
    if automation is not None:
        if not isinstance(automation, int) or isinstance(automation, bool):
            raise ValueError(f"timer {key}: automation is an automation's id")
        old = db.one("SELECT status FROM $r", r=_ref(key))
        if (old or {}).get("status") != "waiting" and _waiting_for(db, automation) >= MAX_WAITING:
            raise ValueError(f"automation {automation} already has {MAX_WAITING} timers waiting; let some fire or switch it off and on")
    t = store.now()
    row = {
        "key": key,
        "at": when,
        "kind": kind,
        "payload": payload,
        "automation": automation,
        "status": "waiting",
        "created_at": t,
        "runs": 0,
    }
    db.q("UPSERT $r CONTENT $d", r=_ref(key), d=store.clean(row))
    _idle["until"] = 0.0
    if _utc(when) <= _now() + dt.timedelta(seconds=CHECK_SECONDS):
        events.wake()  # due now: the thread in this process looks at once
    return _view(row)


def _end(db, r, status, why, now=None):
    """End a waiting timer without firing it (cancelled, missed): True when this call did. Its handler is told."""
    got = db.rows(
        "UPDATE $r SET status = $s, note = $n, done_at = $t WHERE status = 'waiting' AND at = $at RETURN AFTER",
        r=_ref(r["key"]),
        s=status,
        n=why,
        t=_iso(_now(now)),
        at=r["at"],
    )
    if not got:
        return False
    h = _HANDLERS.get(r.get("kind"))
    if h and h.ended:
        try:
            h.ended(db, dict(r.get("payload") or {}), status, why)
        except Exception as e:  # noqa: BLE001 - the timer is ended either way
            log.warning("timer %s: %s: %s", r["key"], type(e).__name__, e)
    return True


def cancel(db, key, why="cancelled"):
    """Call off the waiting timer `key`: True when there was one."""
    r = db.one(f"SELECT {FIELDS} FROM $r WHERE status = 'waiting'", r=_ref(_check_key(key)))
    return bool(r) and _end(db, r, "cancelled", why)


def cancel_for(db, automation, why="the automation was switched off", kinds=None, prefix=None):
    """Call off every waiting timer of an automation (when it is deleted or switched off), or only those of `kinds`
    or whose key starts with `prefix`: how many."""
    rows = db.rows(f"SELECT {FIELDS} FROM timer WHERE automation = $a AND status = 'waiting'", a=automation)
    n = 0
    for r in rows:
        if (kinds is None or r.get("kind") in kinds) and (prefix is None or str(r.get("key")).startswith(prefix)):
            n += _end(db, r, "cancelled", why)
    return n


# ---------- firing what is due ----------
def _count_missed(db, automation):
    """A missed timer counts on its automation (`missed`), when there is one."""
    if automation is None:
        return
    try:
        db.q("UPDATE $r SET missed += 1", r=R("automation", int(automation)))
    except Exception:  # noqa: BLE001 - automations aren't stored yet: the timer row still says missed
        pass


def _quiet(db):
    """Whether no timer waits (believed for IDLE_SECONDS after the table last said so)."""
    if time.monotonic() < _idle["until"]:
        return True
    if db.values("SELECT VALUE id FROM timer WHERE status = 'waiting' LIMIT 1"):
        return False
    _idle["until"] = time.monotonic() + IDLE_SECONDS
    return True


def _latest(h, payload, at, now, window):
    """For a repeating timer due at `at`: (its latest time at or before now that is within the window, or None; its
    next time after now)."""
    start = now - dt.timedelta(seconds=window)
    t = at if at >= start else h.again(payload, start - dt.timedelta(microseconds=1))
    latest, n = None, 0
    while t is not None and t <= now and n < MAX_REPEATS:
        latest, t, n = t, h.again(payload, t), n + 1
    nxt = t if t is not None and t > now else h.again(payload, now)
    return latest, nxt


def _fire(db, cfg, r, now, log_fn=None):
    """Fire one due timer, unless another process got it first: True when this one fired it."""
    h = _HANDLERS.get(r.get("kind"))
    if h is None:
        return _end(db, r, "failed", f"nothing handles timers of kind {r.get('kind')}", now) and False
    payload = dict(r.get("payload") or {})
    at = _utc(r["at"])
    window = max(catch_up(cfg), LATE_SECONDS)
    nxt = None
    if h.again is not None:
        try:
            when, nxt = _latest(h, payload, at, now, window)
        except Exception as e:  # noqa: BLE001 - a schedule that can't be read any more ends here
            return _end(db, r, "failed", f"its next time: {type(e).__name__}: {e}"[:300], now) and False
    else:
        when = at if now - at <= dt.timedelta(seconds=window) else None
    if when is None:  # missed while Lens was off
        if nxt is None:
            if _end(db, r, "missed", "Lens was off when it was due", now):
                _count_missed(db, r.get("automation"))
            return False
        got = db.rows(
            "UPDATE $r SET at = $nxt, note = $n WHERE status = 'waiting' AND at = $at RETURN AFTER",
            r=_ref(r["key"]),
            nxt=_iso(nxt),
            n=f"missed {_iso(at)}: Lens was off",
            at=r["at"],
        )
        if got:
            _count_missed(db, r.get("automation"))
        return False
    token = uuid.uuid4().hex
    stamp = _iso(now)
    got = db.rows(
        "UPDATE $r SET status = $s, at = $next, fired_at = $t, done_at = $done, claim = $c, claimed_by = $me, runs += 1 "
        "WHERE status = 'waiting' AND at = $at RETURN AFTER",
        r=_ref(r["key"]),
        s="waiting" if nxt is not None else "fired",
        next=_iso(nxt) if nxt is not None else r["at"],
        t=stamp,
        done=None if nxt is not None else stamp,
        c=token,
        me=_ME,
        at=r["at"],
    )
    if not got:
        return False
    late = now - when > dt.timedelta(seconds=LATE_SECONDS)
    call = {**payload, "late": late, "timer": {"key": r["key"], "at": _iso(when), "automation": r.get("automation")}}
    error = None
    try:
        h.fn(db, cfg, call)
    except Exception as e:  # noqa: BLE001 - recorded on the timer; the others still fire
        error = f"{type(e).__name__}: {e}"[:500]
        if log_fn:
            log_fn(f"timer {r['key']}: {error}")
    last = store.clean({"at": _iso(when), "fired_at": stamp, "late": late, "ok": error is None, "error": error})
    db.q("UPDATE $r SET last = $l WHERE claim = $c", r=_ref(r["key"]), l=last, c=token)
    if error and nxt is None:
        db.q("UPDATE $r SET status = 'failed', note = $e WHERE claim = $c AND status = 'fired'", r=_ref(r["key"]), e=error, c=token)
    return True


def due(db, cfg, now=None, log=None, limit=BATCH):
    """Fire every timer that is due, in the order they were due: how many this process fired."""
    if _quiet(db):
        return 0
    t = _now(now)
    rows = db.rows(f"SELECT {FIELDS} FROM timer WHERE status = 'waiting' AND at <= $n ORDER BY at LIMIT $l", n=_iso(t), l=int(limit))
    return sum(1 for r in rows if _fire(db, cfg, r, t, log))


def start(db, cfg_fn, stop, log=None):
    """The timer thread, until `stop` is set: a look every CHECK_SECONDS, and at once after events.wake()."""

    def loop():
        while not stop.is_set():
            seen = events.generation()
            try:
                due(db, cfg_fn(), log=log)
            except Exception as e:  # noqa: BLE001 - keep going
                if log:
                    log(f"timers: {type(e).__name__}: {e}")
            events.wait(CHECK_SECONDS, stop, since=seen)

    th = threading.Thread(target=loop, daemon=True, name="timers")
    th.start()
    return th


# ---------- keeping it small ----------
def tidy(db, cfg=None, now=None):
    """Drop timers that ended (fired, failed, cancelled, missed) more than KEEP_DAYS ago, at most once an hour per
    process (routines.run_due calls it every 30 seconds); the cut-off, or 0 when it didn't look."""
    t = time.monotonic()
    with _lock:
        if now is None and _swept["at"] and t - _swept["at"] < SWEEP_SECONDS:
            return 0
        _swept["at"] = t
    cut = _iso(_now(now) - dt.timedelta(days=KEEP_DAYS))
    db.q("DELETE timer WHERE status IN $s AND done_at < $c", s=list(ENDED), c=cut)
    return cut


# ---------- what an automation's clock triggers set ----------
SCHEDULE = "schedule.next"  # a schedule or at trigger: a clock.tick for its automation (re-armed for a schedule)
FIRE = "automation.fire"  # fire an automation with the activity in the payload (relative triggers, joins)
CLOCK = {"type": "Application", "id": "lens:clock", "name": "Lens clock"}
PAST_SECONDS = 300  # a relative time further in the past than this is skipped
WEEK = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
UNIT_WORDS = {"s": "second", "m": "minute", "h": "hour", "d": "day"}


def _provided(module, fn):
    """`fn` of a studio module built in its own step (automations, approvals), or None while it isn't there yet."""
    import importlib

    try:
        m = importlib.import_module(f"{__package__}.{module}")
    except ModuleNotFoundError as e:
        if e.name != f"{__package__}.{module}":
            raise
        return None
    return getattr(m, fn, None)


def _aid(automation):
    aid = automation.get("id") if isinstance(automation, dict) else automation
    if not isinstance(aid, int) or isinstance(aid, bool):
        raise ValueError("name the automation by its id")
    return aid


def _zone(name):
    from . import schedule

    try:
        return schedule.zone(name or "UTC")
    except ValueError:
        return dt.timezone.utc


def local(t, tz="UTC", now=None):
    """A time in words in a time zone: "Fri 16 Oct at 07:00" (with the year when it isn't this year's)."""
    d = _utc(t).astimezone(_zone(tz))
    year = f" {d.year}" if d.year != _now(now).astimezone(_zone(tz)).year else ""
    return f"{WEEK[d.weekday()]} {d.day} {MONTHS[d.month - 1]}{year} at {d:%H:%M}"


def _at_of(t):
    """An at trigger's moment, in UTC: its time, in its time zone when it names none."""
    d = dt.datetime.fromisoformat(str(t["at"]).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=_zone(t.get("timezone")))).astimezone(dt.timezone.utc)


def _what(p):
    """A schedule or at trigger's payload, in words: "every weekday at 07:00", "once"."""
    from . import schedule

    return schedule.describe(p["cron"]) if p.get("cron") else "once"


def tick(automation, alias, at, timezone="UTC", cron=None, spaces=None, late=False):
    """The activity a clock trigger fires its automation with (kind clock.tick, never stored): the moment it is for
    (`at`), in its time zone, with the schedule (`cron`) when it repeats."""
    aid = _aid(automation)
    when = _utc(at)
    desc = _what({"cron": cron})
    spaces = sorted({int(s) for s in spaces or []})
    obj = store.clean(
        {
            "type": "Event",
            "name": desc[:1].upper() + desc[1:],
            "cron": cron,
            "at": events.stamp(when),
            "timezone": timezone or "UTC",
            "local": local(when, timezone, when),
            "description": desc,
            "late": bool(late),
        }
    )
    return events.build(
        f"tick-{aid}-{alias}-{int(when.timestamp())}",
        "clock.tick",
        actor=dict(CLOCK),
        obj=obj,
        summary=f"Lens clock: {obj['local']} ({desc}, {obj['timezone']})",
        space=spaces[0] if len(spaces) == 1 else None,
        spaces=spaces if len(spaces) > 1 else None,
        source="clock",
        pos=[events.stamp(when), f"tick-{aid}-{alias}"],
        published=when,
    )


def _next_time(p, after):
    from . import schedule

    return schedule.next_after(p["cron"], p.get("timezone") or "UTC", after) if p.get("cron") else None


@handler(SCHEDULE, again=_next_time)
def _tick(db, cfg, p):
    act = tick(p["automation"], p["alias"], p["timer"]["at"], p.get("timezone"), p.get("cron"), p.get("spaces"), p.get("late"))
    fire = _provided("automations", "fire")
    if fire is None:
        raise RuntimeError("automations aren't stored yet, so there is nothing to fire")
    fire(db, cfg, int(p["automation"]), p["alias"], act)


@handler(FIRE)
def _fire_automation(db, cfg, p):
    fire = _provided("automations", "fire")
    if fire is None:
        raise RuntimeError("automations aren't stored yet, so there is nothing to fire")
    fire(db, cfg, int(p["automation"]), p["alias"], p.get("activity"))


def _waiting(db, aid):
    return db.rows(f"SELECT {FIELDS} FROM timer WHERE automation = $a AND status = 'waiting' ORDER BY at", a=aid)


def schedule_automation(db, automation, now=None):
    """Set an automation's clock timers from its triggers ({id, enabled, spaces, triggers}; M6 calls it when one is
    saved, switched on or switched off): one per schedule trigger, at its next time, and one per at trigger still to
    come. A timer a trigger still wants is left as it is (so one Lens missed can still catch up); one no trigger wants
    any more is called off; waiting relative timers follow a changed field or offset. Switched off, every timer it has
    is called off. The keys of its clock timers waiting."""
    aid = _aid(automation)
    if not automation.get("enabled", True):
        cancel_for(db, aid)
        return []
    t0 = _now(now)
    spaces = sorted({int(s) for s in automation.get("spaces") or []})
    triggers = [automation_spec.check_trigger(t, n) for n, t in enumerate(automation.get("triggers") or [], 1)]
    have = {r["key"]: r for r in _waiting(db, aid)}
    keep: dict[str, dict] = {}
    rel = {}
    for t in triggers:
        if t["on"] == "relative":
            rel[t["as"]] = t
            continue
        if t["on"] not in ("schedule", "at"):
            continue
        key = f"{'sched' if t['on'] == 'schedule' else 'at'}-{aid}-{t['as']}"
        payload = {"automation": aid, "alias": t["as"], "timezone": t["timezone"], "spaces": spaces}
        if t["on"] == "schedule":
            from . import schedule

            payload["cron"] = t["cron"]
            when = schedule.next_after(t["cron"], t["timezone"], t0)
        else:
            payload["at"] = t["at"]
            when = _at_of(t)
        old = have.get(key)
        if old and old.get("kind") == SCHEDULE and old.get("payload") == payload and (t["on"] == "schedule" or old["at"] == _iso(when)):
            keep[key] = _view(old)
        elif when is not None and (t["on"] == "schedule" or when > t0):
            keep[key] = set(db, key, when, SCHEDULE, payload, aid)
    for key, r in have.items():
        p = r.get("payload") or {}
        if r.get("kind") == SCHEDULE and key not in keep:
            _end(db, r, "cancelled", "its trigger changed")
        elif r.get("kind") == FIRE and key.startswith(f"rel-{aid}-") and p.get("alias") is not None:
            t = rel.get(p["alias"])
            if t is None:
                _end(db, r, "cancelled", "its trigger changed")
            elif (t["field"], t["offset"]) != (p.get("field"), p.get("offset")) and isinstance(p.get("activity"), dict):
                relative(db, aid, p["alias"], p["activity"], t["field"], t["offset"], now=t0)
    return sorted(keep)


def _field(a, path):
    v = a
    for k in str(path).split("."):
        if isinstance(v, dict):
            v = v.get(k)
        elif isinstance(v, list) and k.isdigit() and int(k) < len(v):
            v = v[int(k)]
        else:
            return None
    return v


def _time_of(v):
    """A time an activity holds (ISO 8601, a date meaning its midnight in UTC), in UTC; None when it isn't one."""
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v.strip()):
        v = v.strip() + "T00:00:00+00:00"
    try:
        return _utc(v)
    except (TypeError, ValueError):
        return None


def _offset_words(offset):
    n = automation_spec.check_duration(offset, back=True)
    if n == 0:
        return "when"
    unit = next(u for u in ("d", "h", "m", "s") if abs(n) % automation_spec.UNITS[u] == 0)
    k = abs(n) // automation_spec.UNITS[unit]
    return f"{k} {UNIT_WORDS[unit]}{'' if k == 1 else 's'} {'before' if n < 0 else 'after'}"


def relative(db, automation, alias, activity, field, offset="0s", now=None):
    """Set the timer of a relative trigger for one activity (M6 calls it when a matching activity comes): due at the
    time in its `field` plus `offset` (-30m: half an hour before), to fire the automation with that activity. One
    timer per automation, trigger and object (`rel-<automation>-<alias>-<object id>`), so an event that moves
    (calendar_event.changed, about the same object) moves its timer rather than adding one. A time more than
    PAST_SECONDS in the past is skipped (and a timer it had is called off). {key, at, set, note}."""
    aid = _aid(automation)
    if not isinstance(activity, dict) or not isinstance(activity.get("id"), str):
        raise ValueError("a relative trigger counts from an activity")
    ref = (activity.get("object") or {}).get("id") or activity["id"]
    key = f"rel-{aid}-{alias}-{ref}"
    _check_key(key)
    seconds = automation_spec.check_duration(offset or "0s", back=True, what=f"trigger {alias}'s offset")
    base = _time_of(_field(activity, field))
    if base is None:
        note = f"trigger {alias}: {activity['id']} has no time at {field}, so nothing was set"
        log.info("automation %s: %s", aid, note)
        return {"key": key, "at": None, "set": False, "note": note}
    when = base + dt.timedelta(seconds=seconds)
    t0 = _now(now)
    if when < t0 - dt.timedelta(seconds=PAST_SECONDS):
        cancel(db, key, why="its time had passed")
        note = f"trigger {alias}: {_iso(when)} for {activity['id']} had already passed, so it was skipped"
        log.info("automation %s: %s", aid, note)
        return {"key": key, "at": _iso(when), "set": False, "note": note}
    payload = {"automation": aid, "alias": alias, "activity": activity, "field": field, "offset": offset or "0s"}
    set(db, key, when, FIRE, payload, aid)
    return {"key": key, "at": _iso(when), "set": True, "note": None}


# ---------- actions that wait ----------
PERFORM = "intent.perform"  # carry out an intent whose time came (an action node with a delay or a when)
_STRICT = {e: n for n, e in enumerate(automation_spec.EFFECTS)}  # read, self, lens, out: each reaches further


def later(db, intent, depth=1):
    """Keep an intent that waits for its time (an action node with a `delay` or a `when`; M6 hands it here): a timer
    at its `when`, `intent-<key>`, that carries it out then, asking first when it should (`risk_now`). `depth` is
    the run's (for the loop guard on what it causes). What to keep with the intent: {timer, at}."""
    i = automation_spec.check_intent(intent)
    if not i["when"]:
        raise ValueError(f"intent {i['key']} doesn't say when it runs (when)")
    key = f"intent-{i['key']}"
    payload = {"intent": {**i, "status": "scheduled"}, "depth": max(1, int(depth or 1))}
    row = set(db, key, i["when"], PERFORM, payload, i["automation"])
    return {"timer": key, "at": row["at"]}


def _automation_now(db, aid):
    """The automation as it is stored now ({enabled, policy, spaces}); None when it isn't (removed, or automations
    aren't stored yet)."""
    if aid is None:
        return None
    try:
        return db.one("SELECT enabled, policy, spaces FROM $r", r=R("automation", int(aid)))
    except Exception:  # noqa: BLE001 - no automation table yet
        return None


def risk_now(db, intent, automation=None):
    """routine or ask, worked out again for an intent whose time came: from its effect (the stricter of the one it was
    made with and what its params give now), the automation's policy now (an out action runs on its own only while
    its node is set to), its namespaces now (a lens action stays routine only on the event's own object, in
    namespaces the automation still has), and the action's own check, which may only be stricter. `automation` is the
    stored automation ({policy, spaces}), or None when there is none to read: then an out action asks."""
    from . import actions

    spec = actions.get(intent["action"])
    effect = max(intent["effect"], actions.effect_of(spec, intent["params"]), key=lambda e: _STRICT.get(e, 3))
    policy = (automation or {}).get("policy") or {}
    spaces = (automation or {}).get("spaces")
    if effect in ("read", "self"):
        risk = "routine"
    elif effect == "lens":
        still = spaces is None or {int(s) for s in intent["spaces"]} <= {int(s) for s in spaces}
        risk = "routine" if intent["risk"] == "routine" and still else "ask"
    else:
        risk = "routine" if policy.get(intent["node"]) == "auto" else "ask"
    if risk == "routine" and spec.risk is not None:
        ctx = {"automation": {"id": intent["automation"], "spaces": intent["spaces"] if spaces is None else spaces}}
        try:
            risk = "routine" if spec.risk(intent["params"], {**ctx, "node": intent["node"]}) == "routine" else "ask"
        except Exception:  # noqa: BLE001 - when the action can't tell, it asks
            risk = "ask"
    return risk


def _intent_status(db, i, status, result=None):
    """Say on the run what became of an intent (M6's automations.update_intent owns the run's intents)."""
    update = _provided("automations", "update_intent")
    if update is None or i.get("run") is None:
        log.info("automation %s run %s: intent %s %s", i.get("automation"), i.get("run"), i.get("key"), status)
        return
    update(db, i["run"], i["key"], status, result)


def _intent_ended(db, payload, status, why):
    i = payload.get("intent")
    if isinstance(i, dict):
        _intent_status(db, i, "expired", {"why": why})


@handler(PERFORM, ended=_intent_ended)
def _perform(db, cfg, p):
    from . import actions, activity  # here: actions loads its action modules, which load routines, which load this

    i = automation_spec.check_intent(p["intent"])
    auto = _automation_now(db, i["automation"])
    if i["automation"] is not None and _provided("automations", "fire") is not None and (not auto or auto.get("enabled") is False):
        _intent_status(db, i, "expired", {"why": "the automation was switched off or removed before its time"})
        return
    try:
        risk = risk_now(db, i, auto)
    except KeyError:
        _intent_status(db, i, "failed", {"error": f"there's no action {i['action']} any more"})
        raise ValueError(f"there's no action {i['action']} any more") from None
    if risk == "ask":
        ask = _provided("approvals", "ask")
        approval = ask(db, cfg, {**i, "risk": "ask", "status": "waiting"}) if ask is not None else None
        _intent_status(db, i, "waiting", store.clean({"approval": approval, "why": "waiting for your pick"}))
        return
    refs = []
    if i["automation"] is not None:
        refs.append(f"automation:{i['automation']}")
    if i["run"] is not None:
        refs.append(f"automation_run:{i['run']}")
    try:
        with contextlib.ExitStack() as stack:
            if i["automation"] is not None and i["run"] is not None:
                stack.enter_context(events.caused_by({"automation": i["automation"], "run": i["run"], "depth": int(p.get("depth") or 1)}))
            stack.enter_context(activity.scope(db, *refs, cfg=cfg))
            out = actions.perform(db, cfg, {**i, "risk": "routine", "status": "done"}, None)
    except Exception as e:
        _intent_status(db, i, "failed", {"error": f"{type(e).__name__}: {e}"[:500]})
        raise
    _intent_status(db, i, "done", out)


# ---------- what is coming ----------
def _describe(r, now):
    p = r.get("payload") or {}
    alias = p.get("alias")
    if r["kind"] == SCHEDULE:
        tz = p.get("timezone") or "UTC"
        return f"{_what(p)[:1].upper()}{_what(p)[1:]}: next on {local(r['at'], tz, now)} ({tz})", False
    if r["kind"] == FIRE and isinstance(p.get("activity"), dict) and p.get("field"):
        a = p["activity"]
        try:
            title = (events.field_at(a.get("kind"), p["field"]) or {}).get("title")
        except (KeyError, ValueError):
            title = None
        name = (a.get("object") or {}).get("name") or a.get("summary") or a["id"]
        what = f"{_offset_words(p.get('offset') or '0s')} “{str(name)[:120]}” {(title or p['field']).lower()}"
        return f"{what[:1].upper()}{what[1:]}: on {local(r['at'], 'UTC', now)} (UTC)", True
    if r["kind"] == PERFORM and isinstance(p.get("intent"), dict):
        from . import actions

        i = p["intent"]
        try:
            what = actions.describe(i["action"], i.get("params") or {})
        except KeyError:
            what = i["action"]
        return f"{what}: on {local(r['at'], 'UTC', now)} (UTC)", bool(i.get("untrusted"))
    alias_text = f" of trigger {alias}" if alias else ""
    return f"{r['kind']}{alias_text}: on {local(r['at'], 'UTC', now)} (UTC)", False


def upcoming(db, automation, n=10, now=None):
    """An automation's waiting timers, soonest first (M6 serves them at GET /api/v1/automations/{id}/upcoming):
    [{key, at, kind, trigger, node, description, untrusted}]. `untrusted` says the description quotes outside text
    (an event's title)."""
    t0 = _now(now)
    rows = db.rows(
        f"SELECT {FIELDS} FROM timer WHERE automation = $a AND status = 'waiting' ORDER BY at LIMIT $n",
        a=_aid(automation),
        n=max(1, min(int(n), 100)),
    )
    out = []
    for r in rows:
        p = r.get("payload") or {}
        text, outside = _describe(r, t0)
        out.append(
            {
                "key": r["key"],
                "at": r["at"],
                "kind": r["kind"],
                "trigger": p.get("alias"),
                "node": (p.get("intent") or {}).get("node") if isinstance(p.get("intent"), dict) else None,
                "description": text,
                "untrusted": outside,
            }
        )
    return out


def clock_state(db, automation, now=None):
    """How each schedule and at trigger of an automation ({id, enabled, triggers}) stands, for its page: {alias:
    {status, at, description}}, status waiting (at: when next), done (an at trigger whose time came), failed or
    missed (its last time), or off (switched off, or not set)."""
    aid = _aid(automation)
    t0 = _now(now)
    out = {}
    for n, t in enumerate(automation.get("triggers") or [], 1):
        t = automation_spec.check_trigger(t, n)
        if t["on"] not in ("schedule", "at"):
            continue
        r = get(db, f"{'sched' if t['on'] == 'schedule' else 'at'}-{aid}-{t['as']}")
        status = (r or {}).get("status")
        if not automation.get("enabled", True):
            state = "off"
        elif status == "waiting":
            state = "waiting"
        elif status in ("missed", "failed"):
            state = status
        elif t["on"] == "at" and _at_of(t) <= t0:
            state = "done"
        else:
            state = "off"
        at = r["at"] if state == "waiting" and r else (_iso(_at_of(t)) if t["on"] == "at" else None)
        out[t["as"]] = {"status": state, "at": at, "description": _what({"cron": t.get("cron")})}
    return out


CLOCK_KIND = {
    "label": "Time came",
    "source": "clock",
    "as_type": "Arrive",
    "object_type": "Event",
    "fields": {
        "actor": {"type": "object", "properties": {"name": {"type": "string", "title": "Clock"}}},
        "object": {
            "type": "object",
            "properties": {
                "at": {"type": "string", "title": "Time", "format": "date-time"},
                "cron": {"type": "string", "title": "Schedule"},
                "timezone": {"type": "string", "title": "Time zone"},
                "local": {"type": "string", "title": "Time there"},
                "description": {"type": "string", "title": "When"},
                "late": {"type": "boolean", "title": "Late (Lens was off)"},
            },
        },
    },
    "noisy": True,
}
events.kind("clock.tick", **CLOCK_KIND)  # first without its sample, which is built with the kind
events.kind("clock.tick", **CLOCK_KIND, sample=tick(4, "morning", "2026-10-16T06:00:00Z", "Europe/London", "0 7 * * 1-5", [3]))
