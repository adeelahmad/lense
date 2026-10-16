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

While Lens was off: a timer due within automations.catch_up (1 hour by default) fires late, with `late: true` in its
payload; an older one is missed (counted on its automation). A repeating one fires once, for its latest time in the
window, and then waits for its next. Fired, failed, cancelled and missed rows are dropped after KEEP_DAYS (`tidy`,
which routines.run_due calls; once an hour). Nothing looks at the table until a timer is set: while none waits, the
thread doesn't query it (it checks again once a minute, or at once when this process sets one).
"""

from __future__ import annotations

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
