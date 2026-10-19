"""Review marks on the timeline (docs/timeline.md): each person marks activities reviewed, important or hidden, for
themselves alone. Nobody else sees or sets them.

One row per person and activity: its id is `event_mark:<hash of account and activity>`, which keeps the pair unique
(a composite UNIQUE index on the two finds nothing for `account = $a` alone on the embedded engine), with an index on
`account`. It holds the three flags and when they were last set. A row whose flags are all cleared is deleted, so the
table holds only what someone marked. Marks are read for a page of activities in one query (`of`), and the
timeline's mark filters thin each page a source gives out with one query (`keeper`), never one per activity.
"""

from __future__ import annotations

import hashlib
import time

from . import store

R = store.R
FLAGS = ("reviewed", "important", "hidden")
MAX_IDS = 500  # activities one call marks at most
TIDY_BATCH = 500  # activities one tidy looks up at most
TIDY_SECONDS = 3600
TIDY_TABLES = ("event_mark", "event_reaction")
_tidied = {"at": 0.0, "after": ""}


def _ref(account, activity):
    return R("event_mark", hashlib.sha1(f"{account}|{activity}".encode()).hexdigest())


def blank():
    """The mark of an activity nobody marked."""
    return {f: False for f in FLAGS} | {"at": None}


def of(db, account, ids):
    """{activity id: {reviewed, important, hidden, at}} of the person's marks on these activities, in one query; an
    activity they haven't marked isn't in it."""
    ids = sorted({i for i in ids if isinstance(i, str)})
    if account is None or not ids:
        return {}
    rows = db.rows(
        "SELECT activity, reviewed, important, hidden, at FROM event_mark WHERE account = $a AND activity IN $ids",
        a=account,
        ids=ids,
    )
    return {r["activity"]: {f: bool(r.get(f)) for f in FLAGS} | {"at": r.get("at")} for r in rows}


def check(flags):
    """The flags to set, from {reviewed, important, hidden} (each true, false, or left out to keep it as it is);
    ValueError when none is given or one isn't true or false."""
    flags = dict(flags or {})
    extra = sorted(set(flags) - set(FLAGS))
    if extra:
        raise ValueError(f"no mark {extra[0]}; marks are {', '.join(FLAGS)}")
    out = {k: v for k, v in flags.items() if v is not None}
    for k, v in out.items():
        if not isinstance(v, bool):
            raise ValueError(f"{k} is true or false")
    if not out:
        raise ValueError(f"say which marks to set: {', '.join(FLAGS)} (true or false)")
    return out


def mark(db, account, ids, flags):
    """Set the person's marks on activities (ids they may see; the caller checks), in one transaction; the marks
    after, by id. Flags left out keep their value; a mark with every flag cleared is removed."""
    flags = check(flags)
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str)]
    if len(ids) > MAX_IDS:
        raise ValueError(f"mark at most {MAX_IDS} activities at once")
    if not ids:
        return {}
    sets = ", ".join(f"{k} = ${k}" for k in flags)
    params = {"a": account, "t": store.now(), "ids": ids, **flags}
    stmts = []
    for n, i in enumerate(ids):
        params[f"r{n}"], params[f"i{n}"] = _ref(account, i), i
        stmts.append(f"UPSERT $r{n} SET account = $a, activity = $i{n}, {sets}, at = $t")
    stmts.append(
        "DELETE event_mark WHERE account = $a AND activity IN $ids "
        "AND (reviewed ?? false) = false AND (important ?? false) = false AND (hidden ?? false) = false"
    )
    db.run(stmts, **params)
    got = of(db, account, ids)
    return {i: got.get(i) or blank() for i in ids}


def want(reviewed=None, important=None, hidden=None):
    """The mark filters asked for, {flag: bool}, leaving out those not asked for."""
    return {k: v for k, v in {"reviewed": reviewed, "important": important, "hidden": hidden}.items() if v is not None}


def keeper(db, account, wanted):
    """For events.page's `keep`: the activities of a page whose marks are as `wanted` ({flag: bool}; one not marked
    counts as false), in one query per page. None when nothing is wanted."""
    if not wanted:
        return None

    def keep(acts):
        got = of(db, account, [a["id"] for a in acts])
        return {a["id"] for a in acts if all(bool((got.get(a["id"]) or {}).get(f)) == v for f, v in wanted.items())}

    return keep


def tidy(db, cfg, now=None):
    """Drop the marks and reactions left on activities that are gone (an event log entry past events.keep_days, a
    deleted asset): up to TIDY_BATCH activities marked or reacted to over a day ago are looked up, in id order from
    where the last tidy stopped, at most once an hour per process (routines.run_due calls it). How many activities it
    found gone. Comments on them stay: they are what people wrote, and the asset's page still shows them."""
    from . import events

    t = time.monotonic()
    if now is None and _tidied["at"] and t - _tidied["at"] < TIDY_SECONDS:
        return 0
    _tidied["at"] = t
    cut, after = events.ago(86400, now), _tidied["after"]
    ids = sorted(
        {
            i
            for table in TIDY_TABLES
            for i in db.values(
                f"SELECT VALUE activity FROM {table} WHERE activity > $a AND at < $c ORDER BY activity LIMIT {TIDY_BATCH}",
                a=after,
                c=cut,
            )
            if isinstance(i, str)
        }
    )[:TIDY_BATCH]
    _tidied["after"] = ids[-1] if len(ids) == TIDY_BATCH else ""  # past the last one, else from the start next time
    if not ids:
        return 0
    there = events.get_many(db, cfg, ids, events.viewer(None, None, admin=True))
    gone = [i for i in ids if i not in there]
    for table in TIDY_TABLES:
        if gone:
            db.q(f"DELETE {table} WHERE activity IN $g", g=gone)
    return len(gone)
