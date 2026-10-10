"""Usage analytics (docs/analytics.md): who uses Lens and how often, worked out on this machine, and optionally a
handful of counted events sent to a Google Analytics 4 property the admin owns.

**The numbers** (`metrics`) come from what Lens already keeps: the activity ledger's rows for API calls a person made
(activity.py) and sign-ins in the audit log. Nothing new is collected for them and nothing leaves the machine: daily,
weekly and monthly active people, stickiness, churn and retention by sign-up week, and which features get used.

**Google Analytics** is off by default (analytics.enabled), and needs a GA4 measurement id and a Measurement Protocol
API secret, set in Settings → Usage analytics, the setup wizard, or .env (LENS_ANALYTICS, LENS_GA_MEASUREMENT_ID,
LENS_GA_API_SECRET). Turned on, one process sends once a day the events in EVENTS that analytics.events names, for this
install as a whole: the install's random id (made here, never a person, host or address), the Lens version and
platform, and counts. Never a name, email, title, file name, namespace, prompt or text. Churn across installs is then
GA's retention report over lens_daily_active; lens_inactive marks an install nobody has used for
analytics.inactive_days, which is how a self-hosted install that quietly stopped shows up.
"""

from __future__ import annotations

import datetime as dt
import logging
import platform
import re
import uuid

import httpx

from . import store

log = logging.getLogger("lens")
R = store.R
ROW = R("app_service", "analytics")
HOSTS = {"global": "www.google-analytics.com", "eu": "region1.google-analytics.com"}  # tests point it at a fake
BATCH = 25  # GA4 takes up to 25 events a request
MEASUREMENT_RX = re.compile(r"^G-[A-Z0-9]{4,20}$")

# The events Lens can send, and what each carries. Names are prefixed so none clashes with GA's reserved ones.
EVENTS = {
    "lens_install": "Analytics first ran on this install. version, platform.",
    "lens_setup_done": "The setup wizard was finished (or skipped). skipped (0 or 1).",
    "lens_daily_active": "At least one person used Lens on the day before. active_users, actions.",
    "lens_feature_used": "A feature was used on the day before, one event per feature. feature, actions, users.",
    "lens_upgrade": "Lens runs a new version. from_version, to_version.",
    "lens_inactive": "Nobody has used Lens for analytics.inactive_days (the uninstall-like signal). days.",
    "lens_return": "Someone used Lens again after lens_inactive. days_away.",
}
FEATURE_RX = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SCOPES = ("namespaces", "spaces")  # /namespaces/{ns}/recordings counts as recordings


# ---------- what people did, from the activity ledger and sign-ins ----------
def feature(action):
    """The feature an activity row's action names: `POST /api/v1/namespaces/{ns}/recordings` is recordings. Actions are
    route templates (activity.py), so this is code's wording, never what a person typed."""
    _, _, path = (action or "").partition(" ")
    parts = [p for p in path.split("/") if p][2:]  # past api/v1
    i = 0
    while i < len(parts) and parts[i] in SCOPES and i + 1 < len(parts) and parts[i + 1].startswith("{"):
        i += 2
    name = parts[i] if i < len(parts) else (parts[0] if parts else "")
    return name if FEATURE_RX.match(name) else "other"


def _day(at):
    return str(at or "")[:10]


def _days(db, since):
    """{(account, day): actions} for every person since `since` (a date): API calls that changed something, and
    sign-ins (one each)."""
    out = {}
    for r in db.rows(
        "SELECT actor, string::slice(at, 0, 10) AS day, count() AS n FROM activity "
        "WHERE kind = 'in' AND actor != NONE AND at >= $s GROUP BY actor, day",
        s=since.isoformat(),
    ):
        out[(str(r["actor"]), r["day"])] = out.get((str(r["actor"]), r["day"]), 0) + int(r["n"])
    for r in db.rows(
        "SELECT account, string::slice(at, 0, 10) AS day, count() AS n FROM audit_log "
        "WHERE action = 'login' AND account != NONE AND at >= $s GROUP BY account, day",
        s=since.isoformat(),
    ):
        out[(str(r["account"]), r["day"])] = out.get((str(r["account"]), r["day"]), 0) + int(r["n"])
    return out


def _features(db, since, until=None):
    """[{feature, actions, users}] since `since` (and before `until`), most used first."""
    rows = db.rows(
        "SELECT action, actor, count() AS n FROM activity WHERE kind = 'in' AND actor != NONE AND at >= $s"
        + (" AND at < $u" if until else "")
        + " GROUP BY action, actor",
        s=since.isoformat(),
        u=until.isoformat() if until else None,
    )
    acc = {}
    for r in rows:
        f = acc.setdefault(feature(r["action"]), {"actions": 0, "users": set()})
        f["actions"] += int(r["n"])
        f["users"].add(str(r["actor"]))
    return sorted(
        ({"feature": k, "actions": v["actions"], "users": len(v["users"])} for k, v in acc.items()),
        key=lambda f: (-f["actions"], f["feature"]),
    )


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def metrics(db, days=30, now=None, weeks=8):
    """Active people, churn, retention and feature use over the last `days` days, worked out from the activity ledger
    and sign-ins (docs/analytics.md#the-numbers). Churn: of the people active in the `days` before this period, the
    share not active in it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    start = today - dt.timedelta(days=days - 1)  # the period is the last `days` days, today included
    prev = start - dt.timedelta(days=days)
    cohort_start = today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=weeks - 1)
    since = min(prev, cohort_start)
    seen = _days(db, since)
    by_day, people = {}, {}
    for (who, day), n in seen.items():
        d = by_day.setdefault(day, {"users": set(), "actions": 0})
        d["users"].add(who)
        d["actions"] += n
        people.setdefault(who, set()).add(day)

    def active(a, b):
        """The people active from day a to day b, both included."""
        lo, hi = a.isoformat(), b.isoformat()
        return {w for w, ds in people.items() if any(lo <= d <= hi for d in ds)}

    daily = []
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        got = by_day.get(d) or {"users": (), "actions": 0}
        daily.append({"day": d, "active_users": len(got["users"]), "actions": got["actions"]})
    current, before = active(start, today), active(prev, start - dt.timedelta(days=1))
    mau = len(active(today - dt.timedelta(days=29), today))
    avg_dau = sum(x["active_users"] for x in daily) / days
    accounts = db.rows("SELECT record::id(id) AS id, created_at, disabled FROM account")
    churned = before - current

    cohorts = []
    for w in range(weeks):
        wk = cohort_start + dt.timedelta(weeks=w)
        members = {str(a["id"]) for a in accounts if wk.isoformat() <= _day(a.get("created_at")) < (wk + dt.timedelta(weeks=1)).isoformat()}
        if not members:
            continue
        shares = []
        for k in range(weeks - w):
            a = wk + dt.timedelta(weeks=k)
            back = active(a, min(a + dt.timedelta(days=6), today)) & members
            shares.append(_rate(len(back), len(members)))
        cohorts.append({"week": wk.isoformat(), "users": len(members), "retention": shares})

    last = max((d for ds in people.values() for d in ds), default=None)
    return {
        "days": days,
        "since": start.isoformat(),
        "until": today.isoformat(),
        "daily": daily,
        "dau": len(by_day.get(today.isoformat(), {}).get("users", ())),
        "wau": len(active(today - dt.timedelta(days=6), today)),
        "mau": mau,
        "stickiness": _rate(avg_dau, mau),
        "accounts": sum(1 for a in accounts if not a.get("disabled")),
        "active_users": len(current),
        "new_users": sum(1 for a in accounts if _day(a.get("created_at")) >= start.isoformat()),
        "churn": {
            "previous_active": len(before),
            "retained": len(before & current),
            "churned": len(churned),
            "rate": _rate(len(churned), len(before)),
            "returning": len(current - before),
        },
        "retention": cohorts,
        "features": _features(db, dt.datetime.combine(start, dt.time(), dt.timezone.utc)),
        "last_active_day": last,
        "days_since_active": (today - dt.date.fromisoformat(last)).days if last else None,
    }


# ---------- Google Analytics ----------
def _section(cfg):
    return (cfg or {}).get("analytics") or {}


def configured(cfg):
    """On, with a measurement id and an API secret to send with."""
    a = _section(cfg)
    return bool(a.get("enabled") and a.get("measurement_id") and isinstance(a.get("api_secret"), str) and a["api_secret"])


def _state(db):
    return db.one("SELECT * OMIT id FROM $r", r=ROW) or {}


def _put(db, **fields):
    db.q("UPSERT $r MERGE $d", r=ROW, d=fields)


def client_id(db):
    """This install's random id for Google Analytics: made once, here; not derived from anything about the machine."""
    st = _state(db)
    if st.get("client_id"):
        return st["client_id"]
    cid = str(uuid.uuid4())
    _put(db, client_id=cid)
    return _state(db).get("client_id") or cid


def _url(cfg, debug=False):
    a = _section(cfg)
    base = HOSTS.get(a.get("region") or "global", HOSTS["global"])
    base = base if "://" in base else f"https://{base}"
    return f"{base}/{'debug/' if debug else ''}mp/collect"


def _platform():
    return f"{platform.system().lower() or 'unknown'}-{platform.machine().lower() or 'unknown'}"[:40]


def payload(cfg, cid, events, day=None):
    """The Measurement Protocol body: the install's id, its version and platform as user properties, and the events,
    each counted as engaged so GA's active-user and retention reports include it."""
    from . import __version__

    session = int(dt.datetime.combine(day or dt.date.today(), dt.time(), dt.timezone.utc).timestamp())
    return {
        "client_id": cid,
        "non_personalized_ads": True,
        "user_properties": {"lens_version": {"value": __version__}, "lens_platform": {"value": _platform()}},
        "events": [{"name": name, "params": {**params, "session_id": session, "engagement_time_msec": 1}} for name, params in events],
    }


def send(db, cfg, events, day=None, debug=False):
    """Send events ([(name, params)]) now; (ok, error, validation messages). Only events analytics.events names."""
    a = _section(cfg)
    wanted = set(a.get("events") or EVENTS)
    events = [(n, p) for n, p in events if n in wanted or (debug and n == "lens_test")]
    if not events:
        return True, None, []
    cid, msgs = client_id(db), []
    params = {"measurement_id": a.get("measurement_id"), "api_secret": a.get("api_secret")}
    try:
        for i in range(0, len(events), BATCH):
            r = httpx.post(_url(cfg, debug), params=params, json=payload(cfg, cid, events[i : i + BATCH], day), timeout=10)
            if r.status_code >= 300:
                raise ValueError(f"Google Analytics answered {r.status_code}")
            if debug:
                msgs += [str(m.get("description") or m)[:300] for m in (r.json().get("validationMessages") or [])]
    except (httpx.HTTPError, ValueError) as e:
        error = f"couldn't send to Google Analytics: {type(e).__name__}: {e}"[:300].replace(a.get("api_secret") or "\0", "…")
        if not debug:
            _put(db, last_error=error, last_error_at=store.now())
        return False, error, msgs
    if not debug:
        _put(db, last_sent_at=store.now(), last_sent=[n for n, _ in events], last_error=None)
    return not msgs, None, msgs


def _claim(db, day):
    """True for the one process that gets to send `day`'s events."""
    if _state(db).get("day") == day:
        return False
    client_id(db)
    got = db.rows("UPDATE $r SET day = $d WHERE day != $d RETURN AFTER", r=ROW, d=day)
    return bool(got)


def tick(db, cfg, now=None):
    """Once a day, while analytics is on and set up: send what happened since the last time (docs/analytics.md)."""
    if not configured(cfg):
        return []
    from . import __version__

    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    if not _claim(db, today.isoformat()):
        return []
    st, events = _state(db), []
    if not st.get("installed"):
        events.append(("lens_install", {"version": __version__, "platform": _platform()}))
    wiz = db.one("SELECT done_at, skipped FROM $r", r=R("setup", "wizard")) or {}
    if wiz.get("done_at") and not st.get("setup_done"):
        events.append(("lens_setup_done", {"skipped": 1 if wiz.get("skipped") else 0}))
    if st.get("version") and st["version"] != __version__:
        events.append(("lens_upgrade", {"from_version": st["version"], "to_version": __version__}))
    yesterday = today - dt.timedelta(days=1)
    seen = _days(db, yesterday)
    users = {w for (w, d) in seen if d == yesterday.isoformat()}
    inactive_days = int(_section(cfg).get("inactive_days") or 30)
    marks = {"installed": True, "setup_done": bool(wiz.get("done_at")), "version": __version__}
    if users:
        events.append(
            (
                "lens_daily_active",
                {"active_users": len(users), "actions": sum(n for (_, d), n in seen.items() if d == yesterday.isoformat())},
            )
        )
        start = dt.datetime.combine(yesterday, dt.time(), dt.timezone.utc)
        for f in _features(db, start, start + dt.timedelta(days=1))[:20]:
            events.append(("lens_feature_used", f))
        if st.get("inactive_since"):
            away = (yesterday - dt.date.fromisoformat(st["inactive_since"])).days
            events.append(("lens_return", {"days_away": max(away, inactive_days)}))
            marks["inactive_since"] = None
    elif not st.get("inactive_since"):
        last = (db.one("SELECT at FROM activity WHERE kind = 'in' AND actor != NONE ORDER BY at DESC LIMIT 1") or {}).get("at")
        last = last or (db.one("SELECT at FROM audit_log WHERE action = 'login' ORDER BY at DESC LIMIT 1") or {}).get("at")
        since = dt.date.fromisoformat(_day(last)) if last else None
        if since and (today - since).days >= inactive_days:
            events.append(("lens_inactive", {"days": (today - since).days}))
            marks["inactive_since"] = since.isoformat()
    ok, error, _ = send(db, cfg, events, day=yesterday)
    if ok:
        _put(db, **marks)
    else:
        log.info("analytics: %s", error)
        db.q("UPDATE $r SET day = NONE", r=ROW)  # try again on the next tick
    return [n for n, _ in events]


def test(db, cfg):
    """Send one lens_test event to GA's validation server (it's checked, not recorded); (ok, error, messages)."""
    a = _section(cfg)
    if not a.get("measurement_id") or not isinstance(a.get("api_secret"), str) or not a.get("api_secret"):
        return False, "set the measurement id and the API secret first", []
    from . import __version__

    return send(db, cfg, [("lens_test", {"version": __version__})], debug=True)


def status(db, cfg):
    """Whether analytics is on and how the last send went, for Settings."""
    a, st = _section(cfg), _state(db)
    return {
        "enabled": bool(a.get("enabled")),
        "configured": configured(cfg),
        "measurement_id": a.get("measurement_id"),
        "client_id": st.get("client_id"),
        "last_sent_at": st.get("last_sent_at"),
        "last_sent": st.get("last_sent") or [],
        "last_error": st.get("last_error"),
        "events": [{"name": k, "description": v, "on": k in set(a.get("events") or EVENTS)} for k, v in EVENTS.items()],
    }
