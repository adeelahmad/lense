"""Mutes on the timeline (docs/timeline.md, Muting): each person mutes a source or person (an actor) or a kind of
activity ("hide syslog"), for good or until a time (snoozed for a day), for themselves alone. Muting hides; it never
deletes: a muted actor's activities stay stored, still trigger automations and still show to everyone else, and come
back when the mute is lifted or runs out. The timeline leaves them out as it leaves out what the person hid, so
`hidden=true` shows them and `hidden=false` (the page) does not.

One row per person and target: `event_mute:<hash of account and target>` (the id keeps the pair unique, as with
event_mark), with an index on `account`, holding the actor id or the kind, the actor's name as the page showed it
(for the Muted chips), `until` (None for good) and when it was set. A mute that has run out is dropped the next time
the person's mutes are read.
"""

from __future__ import annotations

import hashlib

from . import event_marks, events, store

R = store.R
MAX_MUTES = 200  # per person
NAME_MAX = 200


def _ref(account, target):
    return R("event_mute", hashlib.sha1(f"{account}|{target}".encode()).hexdigest())


def target(actor=None, kind=None):
    """("actor", id) or ("kind", name) from what was given; ValueError for neither, both, or a kind that isn't."""
    actor, kind = (str(actor or "").strip() or None), (str(kind or "").strip() or None)
    if bool(actor) == bool(kind):
        raise ValueError("mute an actor (its id, like lens:source/12) or a kind (like sensor.reading), one of the two")
    if actor:
        if len(actor) > 240 or ":" not in actor:
            raise ValueError("actor is an actor's id, like lens:source/12 or lens:account/1")
        return "actor", actor
    events.expand([kind])  # ValueError for an unknown kind
    return "kind", kind


def _until(until):
    """An ISO 8601 time in the future as the stream stamps it, or None for good; ValueError otherwise."""
    if until in (None, ""):
        return None
    try:
        t = events.stamp(until)
    except (ValueError, TypeError):
        raise ValueError("until is a time (ISO 8601), like 2026-10-19T08:00:00Z, or left out to mute for good") from None
    if t <= events.stamp():
        raise ValueError("until is a time still to come")
    return t


def of(db, account):
    """The person's mutes now, [{actor, kind, name, until, at}], the latest first; those that ran out are dropped."""
    if account is None:
        return []
    rows = db.rows("SELECT record::id(id) AS id, actor, kind, name, until, at FROM event_mute WHERE account = $a", a=account)
    now, out, gone = events.stamp(), [], []
    for r in rows:
        if r.get("until") and r["until"] <= now:
            gone.append(R("event_mute", r["id"]))
        else:
            out.append({k: r.get(k) for k in ("actor", "kind", "name", "until", "at")})
    if gone:
        db.q("DELETE $g", g=gone)
    return sorted(out, key=lambda m: m.get("at") or "", reverse=True)


def mute(db, account, actor=None, kind=None, until=None, name=None):
    """Mute the actor or kind for the person, for good or `until`; the person's mutes after. A mute set again takes
    the new `until`. ValueError for a bad target, a bad time, or more than MAX_MUTES."""
    what, value = target(actor, kind)
    when = _until(until)
    if what == "kind":
        name = events.kind_info(value)["label"]
    name = str(name or "").strip()[:NAME_MAX] or None
    have = of(db, account)
    if len(have) >= MAX_MUTES and not any(m.get(what) == value for m in have):
        raise ValueError(f"at most {MAX_MUTES} mutes; lift one first")
    db.q(
        "UPSERT $r SET account = $a, actor = $actor, kind = $kind, name = $n, until = $u, at = $t",
        r=_ref(account, f"{what}:{value}"),
        a=account,
        actor=value if what == "actor" else None,
        kind=value if what == "kind" else None,
        n=name,
        u=when,
        t=store.now(),
    )
    return of(db, account)


def unmute(db, account, actor=None, kind=None):
    """Lift the person's mute on the actor or kind; the person's mutes after (the same when there was none)."""
    what, value = target(actor, kind)
    db.q("DELETE $r", r=_ref(account, f"{what}:{value}"))
    return of(db, account)


def muted_by(mutes):
    """(actor ids, kinds with those under them) from a person's mutes, for `is_muted`."""
    actors = {m["actor"] for m in mutes if m.get("actor")}
    kinds = set()
    for m in mutes:
        if m.get("kind"):
            kinds |= events.descendants(m["kind"])
    return actors, kinds


def keeper(db, account, wanted):
    """For events.page's `keep`: the activities of a page whose marks are as `wanted` ({flag: bool}; one not marked
    counts as false), with a muted actor's or kind's activities counting as hidden, in one query per page. None when
    nothing is wanted and the person has no mutes. Without a `hidden` filter, muted activities are left out."""
    wanted = dict(wanted or {})
    actors, kinds = muted_by(of(db, account)) if account is not None else (set(), set())
    if not wanted and not actors and not kinds:
        return None

    def muted(a):
        return (a.get("actor") or {}).get("id") in actors or a.get("kind") in kinds

    def keep(acts):
        got = event_marks.of(db, account, [a["id"] for a in acts]) if wanted else {}
        out = set()
        for a in acts:
            m = got.get(a["id"]) or {}
            flags = {f: bool(m.get(f)) for f in event_marks.FLAGS}
            flags["hidden"] = flags["hidden"] or muted(a)
            if "hidden" not in wanted and flags["hidden"] and muted(a):
                continue
            if all(flags[f] == v for f, v in wanted.items()):
                out.add(a["id"])
        return out

    return keep
