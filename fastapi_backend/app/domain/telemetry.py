"""What's done with the archive (docs/analytics.md): each view, play, search, download and comment is kept with the
account that did it, the resource and its collection, and the time, for as long as analytics.retention_days says (90
days unless changed), and counted per day, resource and action for the charts. No addresses and no browsers are kept;
someone who isn't signed in counts as a visitor, with no account at all.

A namespace's owners see its numbers, an admin of a collection their collection's, admins everything; each person
sees their own activity. The daily counts hold no accounts, so they outlive the 90 days.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter

from . import hierarchy, store

R = store.R
log = logging.getLogger("lens")
ACTIONS = ("view", "play", "search", "download", "comment")
VIEW_AGAIN_MINUTES = 30  # opening the same resource again within this isn't another view (pages reload their data)
TOP = 20


def retention_days(cfg):
    return int({**store.DEFAULTS["analytics"], **(cfg.get("analytics") or {})}["retention_days"])


def _ago(**delta):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**delta)).isoformat(timespec="seconds")


def record(db, cfg, action, account=None, recording=None, space=None, collection=None):
    """Keep one action. Never raises: what someone was doing matters more than counting it."""
    try:
        if action not in ACTIONS:
            return False
        if recording is not None and space is None:
            rec = db.one("SELECT space, collection FROM $r", r=R("recording", recording))
            if not rec:
                return False
            space, collection = rec["space"], rec.get("collection")
        if action == "view" and account is not None and recording is not None:
            again = db.values(
                "SELECT VALUE id FROM activity WHERE account = $a AND recording = $r AND action = 'view' AND at > $t LIMIT 1",
                a=account,
                r=recording,
                t=_ago(minutes=VIEW_AGAIN_MINUTES),
            )
            if again:
                return False
        now = store.now()
        row = store.clean(
            {"at": now, "action": action, "account": account, "recording": recording, "space": space, "collection": collection}
        )
        db.q("CREATE activity CONTENT $d", d=row)
        day = now[:10]
        fields = store.clean({"day": day, "action": action, "recording": recording, "space": space, "collection": collection})
        db.q(
            f"UPSERT $r SET {', '.join(f'{k} = ${k}' for k in fields)}, n = (n ?? 0) + 1, anonymous = (anonymous ?? 0) + $anon",
            r=R("activity_day", f"{day}|{space or 0}|{collection or 0}|{recording or 0}|{action}"),
            anon=0 if account is not None else 1,
            **fields,
        )
        return True
    except Exception:  # noqa: BLE001 - see above
        log.exception("couldn't record %s", action)
        return False


def _counts(rows, key):
    out = {}
    for r in rows:
        k = r.get(key)
        if k is not None:
            out.setdefault(k, Counter())[r["action"]] += r["n"]
    return out


def _line(counts):
    return {a: int(counts.get(a, 0)) for a in ACTIONS}


def summary(db, day_from, day_to, sid=None, collections=None):
    """The numbers for a range of days (both included): for a namespace (`sid`), for some of its collections, or for
    the whole archive. {totals, people, anonymous, days, collections | namespaces, resources}"""
    where, p = ["day >= $f", "day <= $t"], {"f": day_from, "t": day_to}
    if sid is not None:
        where.append("space = $s")
        p["s"] = sid
    if collections is not None:
        where.append("collection IN $c")
        p["c"] = sorted(collections)
    rows = db.rows(f"SELECT day, space, collection, recording, action, n, anonymous FROM activity_day WHERE {' AND '.join(where)}", **p)
    by_day = _counts(rows, "day")
    first, last = dt.date.fromisoformat(day_from), dt.date.fromisoformat(day_to)
    days = [
        {"day": d.isoformat(), **_line(by_day.get(d.isoformat(), {}))}
        for d in (first + dt.timedelta(n) for n in range((last - first).days + 1))
    ]
    totals = Counter()
    for r in rows:
        totals[r["action"]] += r["n"]
    raw = ["at >= $f", "at < $t", "account != NONE"] + where[2:]
    people = db.values(
        f"SELECT VALUE account FROM activity WHERE {' AND '.join(raw)}",
        **{**p, "f": day_from, "t": (last + dt.timedelta(1)).isoformat()},
    )
    out = {
        "from": day_from,
        "to": day_to,
        "totals": _line(totals),
        "people": len(set(people)),
        "anonymous": sum(r.get("anonymous") or 0 for r in rows),
        "days": days,
    }
    by_rec = _counts(rows, "recording")
    top = sorted(by_rec, key=lambda r: (-sum(by_rec[r].values()), r))[:TOP]
    titles = (
        {
            x["id"]: x
            for x in db.rows(
                "SELECT record::id(id) AS id, title, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in top]
            )
        }
        if top
        else {}
    )
    names = store.space_names(db)
    out["resources"] = [
        {
            "id": r,
            "title": (titles.get(r) or {}).get("title"),
            "namespace": names.get((titles.get(r) or {}).get("space")),
            **_line(by_rec[r]),
        }
        for r in top
        if r in titles
    ]
    if sid is None:
        by_space = _counts(rows, "space")
        out["namespaces"] = sorted(({"name": names.get(s) or str(s), **_line(c)} for s, c in by_space.items()), key=lambda x: x["name"])
    else:
        by_col = _counts(rows, "collection")
        cols = {c["id"]: c for c in hierarchy.of_space(db, sid)}
        out["collections"] = sorted(
            (
                {"id": c, "name": cols[c]["name"], "path": [x["name"] for x in hierarchy.path(db, c)], **_line(n)}
                for c, n in by_col.items()
                if c in cols
            ),
            key=lambda x: [p.casefold() for p in x["path"]],
        )
    return out


def mine(db, account, limit=50, before=None):
    """This person's own activity, the latest first: [{at, action, recording, space, collection}]."""
    rows = db.rows(
        "SELECT at, action, recording, space, collection FROM activity WHERE account = $a"
        + (" AND at < $b" if before else "")
        + f" ORDER BY at DESC LIMIT {int(limit)}",
        a=account,
        b=before,
    )
    return rows


def _count(db, table, where="", **p):
    rows = db.rows(f"SELECT count() AS n FROM {table}{where} GROUP ALL", **p)
    return int(rows[0]["n"]) if rows else 0


def status(db, cfg):
    """What's kept: {events, oldest, days, retention_days}."""
    oldest = db.values("SELECT VALUE at FROM activity ORDER BY at LIMIT 1")
    return {
        "events": _count(db, "activity"),
        "oldest": oldest[0] if oldest else None,
        "days": _count(db, "activity_day"),
        "retention_days": retention_days(cfg),
    }


def purge(db, cfg, everything=False):
    """Delete what's older than analytics.retention_days (the daily counts stay: they hold no accounts), or with
    `everything` all of it, counts too. How many actions were deleted."""
    if everything:
        n = _count(db, "activity")
        db.q("DELETE activity")
        db.q("DELETE activity_day")
        return n
    cutoff = _ago(days=retention_days(cfg))
    n = _count(db, "activity", " WHERE at < $t", t=cutoff)
    db.q("DELETE activity WHERE at < $t", t=cutoff)
    return n
