"""Budgets (docs/budgets.md): a cap on what a routine, automation, pipeline, workflow or namespace may cost, checked before it runs.

A budget is off unless someone sets one. It caps cost in USD, tokens, or both, per run, day, week (from Monday) or
month (UTC), with what was spent read from the activity ledger (activity.py). Before a routine run or a job, Lens
estimates what the run will cost from that resource's past runs and looks at what is left:

* under budget: it runs;
* over (what was spent plus the estimate passes the cap): what `on_over` says. `ask` (the default) holds the run
  until someone picks: run it once, or skip it. `skip` skips it and says so. `assistant` lets the decision model weigh
  it (decide.py: where the budget stands, how sure the estimate is, how far over it would go); it runs only when the
  model is sure, and asks otherwise.

Doing nothing changes nothing: a held run stays held. A run someone let through runs. `warn_at` (0.8) marks a budget
`near` once that share is spent, for the periodic check (sweep) to warn about.

One budget, `all`, covers everything Lens spends (the setup wizard asks for it): its `warn_at` share is the soft limit,
which warns, and its cap the hard limit. At the hard limit every run waits as above, and calls to models that cost
money are refused (activity.Call asks `hard_stop`); models without a price (local ones) keep working, unless the cap
is in tokens.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time

from . import activity, store

log = logging.getLogger("lens")
R = store.R
PERIODS = ("run", "day", "week", "month")
ON_OVER = ("ask", "skip", "assistant")
TABLES = ("routine", "automation", "pipeline", "workflow", "space", "scale_group")
REF = re.compile(r"^(?:(routine|automation|pipeline|workflow|space|scale_group):(\d+)|all)$")
ALL = "all"  # everything Lens spends (set in setup); the meter measures all of Lens against it
HARD_SECONDS = 30  # how long hard_stop trusts what it last found
FIELDS = "resource, usd, tokens, period, on_over, warn_at, note, by, updated_at, alert"
LOOKBACK_DAYS = 30  # the past runs an estimate is drawn from
SWEEP_SECONDS = 300  # how often the scheduler looks at every budget
_swept = {"at": 0.0}


def _key(resource):
    return R("budget", resource.replace(":", "_"))


def check_ref(resource):
    if not isinstance(resource, str) or not REF.match(resource):
        raise ValueError("a budget is for all, routine:<id>, automation:<id>, pipeline:<id>, workflow:<id>, space:<id> or scale_group:<id>")
    return resource


def _num(v, what, whole=False):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 1e12 or (whole and int(v) != v):
        raise ValueError(f"{what} is a {'whole ' if whole else ''}number of at least 0")
    return int(v) if whole else float(v)


def put(db, resource, usd=None, tokens=None, period="month", on_over="ask", warn_at=0.8, note=None, by=None):
    """Set (or replace) a resource's budget. At least one of usd and tokens."""
    check_ref(resource)
    usd, tokens = _num(usd, "usd"), _num(tokens, "tokens", whole=True)
    if usd is None and tokens is None:
        raise ValueError("give the budget a cap: usd, tokens or both")
    if period not in PERIODS:
        raise ValueError(f"period is one of {', '.join(PERIODS)}")
    if on_over not in ON_OVER:
        raise ValueError(f"on_over is one of {', '.join(ON_OVER)}")
    warn_at = _num(warn_at, "warn_at")
    if warn_at is None or not 0.1 <= warn_at <= 1:
        raise ValueError("warn_at is a share of the budget from 0.1 to 1")
    row = store.clean(
        {
            "resource": resource,
            "usd": usd,
            "tokens": tokens,
            "period": period,
            "on_over": on_over,
            "warn_at": warn_at,
            "note": (note or "").strip()[:300] or None,
            "by": by,
            "updated_at": store.now(),
        }
    )
    db.q("UPSERT $r CONTENT $d", r=_key(resource), d=row)
    _hard.clear()
    return get(db, resource)


def get(db, resource):
    return db.one(f"SELECT {FIELDS} FROM $r", r=_key(resource))


def remove(db, resource):
    check_ref(resource)
    db.q("DELETE $r", r=_key(resource))
    _hard.clear()


def all_budgets(db):
    return db.rows(f"SELECT {FIELDS} FROM budget ORDER BY resource")


# ---------- where it stands ----------
def _spent(db, resource, since):
    """(USD, tokens, whether the USD is an estimate) since a time: one resource's calls, or every call for `all`."""
    if resource == ALL:
        p, q = (
            {},
            "SELECT math::sum(cost_usd ?? 0) AS usd, math::sum((tokens_in ?? 0) + (tokens_out ?? 0)) AS tokens, count(unpriced = true) AS unpriced FROM activity WHERE kind != 'run'",
        )
        if since:
            q += " AND at >= $since"
            p["since"] = since
        row = db.one(q + " GROUP ALL", **p) or {}
        return round(float(row.get("usd") or 0), 6), int(row.get("tokens") or 0), bool(row.get("unpriced"))
    c = activity.costs(db, [resource], since)[resource]
    return c["cost_usd"], c["tokens"], c["estimate"]


def _runs(db, resource, since):
    """How many runs this resource had since a time: a routine's or an automation's runs, or the jobs of a pipeline,
    workflow or namespace."""
    m = REF.match(resource)
    if resource == ALL:
        routine_runs = len(db.values("SELECT VALUE id FROM routine_run WHERE started_at >= $s", s=since))
        automation_runs = len(db.values("SELECT VALUE id FROM automation_run WHERE started_at >= $s", s=since))
        job_runs = db.values(
            "SELECT VALUE id FROM activity WHERE kind = 'run' AND at >= $s AND string::starts_with(action, 'job.')", s=since
        )
        return routine_runs + automation_runs + len(job_runs)
    if m and m.group(1) == "routine":
        return len(db.values("SELECT VALUE id FROM routine_run WHERE routine = $r AND started_at >= $s", r=int(m.group(2)), s=since))
    if m and m.group(1) == "automation":
        return len(db.values("SELECT VALUE id FROM automation_run WHERE automation = $r AND started_at >= $s", r=int(m.group(2)), s=since))
    return len(
        db.values(
            "SELECT VALUE id FROM activity WHERE kind = 'run' AND resources CONTAINS $res AND at >= $s AND string::starts_with(action, 'job.')",
            res=resource,
            s=since,
        )
    )


def estimate(db, resource, now=None):
    """What one more run will likely cost: the average of the last LOOKBACK_DAYS. Always an estimate.
    {usd, tokens, runs (it's drawn from), basis}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=LOOKBACK_DAYS)).isoformat(timespec="seconds")
    runs = _runs(db, resource, since)
    if not runs:
        return {"usd": None, "tokens": None, "runs": 0, "basis": "no runs in the last 30 days to go on"}
    usd, tokens = _spent(db, resource, since)[:2]
    return {
        "usd": round(usd / runs, 6),
        "tokens": int(tokens / runs),
        "runs": runs,
        "basis": f"the average of {runs} run{'s' if runs != 1 else ''} in the last {LOOKBACK_DAYS} days",
    }


def status(db, resource, budget=None, now=None):
    """Where a resource's budget stands: {budget, since, spent: {usd, tokens, estimate}, next: estimate, left, share,
    state: none | ok | near | over}. `share` is the largest of the spent shares of its caps."""
    budget = budget if budget is not None else get(db, resource)
    nxt = estimate(db, resource, now)
    if not budget:
        return {"resource": resource, "budget": None, "state": "none", "next": nxt}
    period = budget.get("period") or "month"
    since = None if period == "run" else activity._since(period, now)
    if period == "run":
        spent = {"usd": 0.0, "tokens": 0, "estimate": False}
    else:
        usd, tokens, est = _spent(db, resource, since)
        spent = {"usd": usd, "tokens": tokens, "estimate": est}
    left, shares, after = {}, [], []
    for cap, k in (("usd", "usd"), ("tokens", "tokens")):
        if budget.get(cap) is None:
            continue
        lim = budget[cap]
        left[k] = max(0, lim - spent[k]) if k == "tokens" else round(max(0.0, lim - spent[k]), 6)
        shares.append(spent[k] / lim if lim else (1.0 if spent[k] else 0.0))
        if nxt.get(k) is not None:
            after.append((spent[k] + nxt[k]) / lim if lim else (1.0 if spent[k] + nxt[k] else 0.0))
    share = max(shares) if shares else 0.0
    share_after = max(after) if after else None
    state = "over" if share >= 1 else "near" if share >= (budget.get("warn_at") or 0.8) else "ok"
    return {
        "resource": resource,
        "budget": budget,
        "since": since,
        "spent": spent,
        "next": nxt,
        "left": left,
        "share": round(share, 4),
        "share_after_next": round(share_after, 4) if share_after is not None else None,
        "state": state,
    }


# ---------- before a run ----------
def check(db, cfg, resources, now=None):
    """Whether a run that counts for these resources may go ahead. Returns {go, action, why, resource, status}:
    go True (run), or action "ask" (hold it for a person), "skip". The first budget that stops it decides."""
    for res in dict.fromkeys([ALL, *(r for r in resources if r and REF.match(r))]):
        b = get(db, res)
        if not b:
            continue
        st = status(db, res, b, now)
        over_now = st["state"] == "over"
        over_after = (st["share_after_next"] or 0) > 1
        if not (over_now or over_after):
            continue
        why = _why(res, st, over_now)
        how = b.get("on_over") or "ask"
        if how == "assistant":
            how = _weigh(cfg, res, st, why)
            if how == "run":
                return {"go": True, "action": "run", "why": why + " The assistant let it run.", "resource": res, "status": st}
        return {"go": False, "action": how, "why": why, "resource": res, "status": st}
    return {"go": True, "action": "run", "why": None, "resource": None, "status": None}


def used_up(db, resources, now=None):
    """Why a call for these resources must not go ahead, or None: the first of their budgets (and `all`) whose cap is
    already reached. For a single call inside a run, such as an automation asking the model, where check()'s estimate
    of a whole run doesn't apply."""
    for res in dict.fromkeys([ALL, *(r for r in resources if r and REF.match(r))]):
        b = get(db, res)
        if b:
            st = status(db, res, b, now)
            if st["state"] == "over":
                return (
                    _why(res, st, True).replace(f"{ALL} has spent", "Lens has spent") + " Raise the limit in Settings › Budgets to go on."
                )
    return None


def _money(v):
    return f"${v:,.4f}".rstrip("0").rstrip(".") if v < 1 else f"${v:,.2f}"


def _why(res, st, over_now):
    b, sp, nxt = st["budget"], st["spent"], st["next"]
    per = "per run" if b["period"] == "run" else f"this {b['period']}"
    parts = []
    if b.get("usd") is not None:
        parts.append(f"{'≈' if sp['estimate'] else ''}{_money(sp['usd'])} of {_money(b['usd'])} {per}")
    if b.get("tokens") is not None:
        parts.append(f"{sp['tokens']:,} of {b['tokens']:,} tokens {per}")
    spent = " and ".join(parts)
    if over_now:
        return f"{res} has spent {spent}: its budget is used up."
    est = []
    if nxt.get("usd") is not None and b.get("usd") is not None:
        est.append(f"≈{_money(nxt['usd'])}")
    if nxt.get("tokens") is not None and b.get("tokens") is not None:
        est.append(f"≈{nxt['tokens']:,} tokens")
    return f"{res} has spent {spent}; the next run ({' and '.join(est) or 'cost unknown'}, {nxt['basis']}) would go over."


def _weigh(cfg, res, st, why):
    """The decision model's call on a run that would go over: "run" only when it's sure; else "ask"."""
    from . import decide

    try:
        d = decide.choose(
            cfg,
            "A scheduled run would go over its budget. Should it run anyway, or wait for the person to decide? Run "
            "only when going over is small and the work is routine; anything large, or a budget already used up, waits.",
            {"run": "run it now, over budget", "ask": "hold it and ask the person"},
            {
                "why": why,
                "budget": st["budget"],
                "spent": st["spent"],
                "next_run_estimate": st["next"],
                "share_after_next": st["share_after_next"],
            },
        )
    except decide.Undecided as e:
        log.info("budgets: no decision engine for %s (%s); asking", res, e)
        return "ask"
    return d["choice"] if decide.sure(cfg, d) else "ask"


# ---------- the hard limit, for calls ----------
_hard: dict = {}


def hard_stop(db, cfg, model=None):
    """Why a call out must not go ahead, or None: the `all` budget is used up and the call would cost money (a model
    with a price), or its cap is in tokens (any call counts). Looked up at most every HARD_SECONDS."""
    now = time.monotonic()
    if _hard.get("db") != id(db) or now - _hard.get("at", 0) > HARD_SECONDS:
        try:
            b = get(db, ALL)
            st = status(db, ALL, b) if b and b.get("period") != "run" else None
        except Exception as e:  # noqa: BLE001 - a budget that can't be read stops nothing
            log.info("budgets: couldn't check the overall budget: %s", e)
            b = st = None
        _hard.update(
            db=id(db),
            at=now,
            budget=b,
            over=bool(st and st["state"] == "over"),
            why=_why(ALL, st, True) if st and st["state"] == "over" else None,
        )
    if not _hard.get("over"):
        return None
    b = _hard["budget"]
    if b.get("tokens") is None:  # a cap in money: only calls that cost money stop
        from . import telemetry

        p = telemetry.prices(cfg or {}).get(model) if model else None
        if not isinstance(p, dict) or (p.get("unit") or "tokens") == "off":
            return None
    return _hard["why"].replace(f"{ALL} has spent", "Lens has spent") + " Raise the limit in Settings › Budgets to go on."


# ---------- the periodic check ----------
def sweep(db, cfg, now=None, every=None):
    """Look at every budget (at most once `every` seconds, when given): one that has crossed warn_at or its cap since it was last looked at gets an alert (on the
    budget, and a ledger row `budget.near` / `budget.over` for it), once per period. Returns the alerts made."""
    if every is not None:
        if time.monotonic() - _swept["at"] < every and _swept["at"]:
            return []
        _swept["at"] = time.monotonic()
    made = []
    for b in all_budgets(db):
        res = b["resource"]
        if b.get("period") == "run":
            continue
        try:
            st = status(db, res, b, now)
        except Exception as e:  # noqa: BLE001 - one bad budget doesn't stop the rest
            log.info("budgets: couldn't check %s: %s", res, e)
            continue
        if st["state"] not in ("near", "over"):
            continue
        last = b.get("alert") or {}
        if last.get("since") == st["since"] and (last.get("state") == st["state"] or last.get("state") == "over"):
            continue
        alert = {"state": st["state"], "since": st["since"], "at": store.now(), "share": st["share"]}
        db.q("UPDATE $r SET alert = $a", r=_key(res), a=alert)
        activity.record(
            "run", f"budget.{st['state']}", [res], cfg, db, detail={"share": st["share"], "estimate": st["spent"]["estimate"] or None}
        )
        try:  # for automations: budget.warned (events_streams.py)
            from . import events_streams

            events_streams.budget_warned(db, b, alert, st)
        except Exception as e:  # noqa: BLE001 - the alert stands without it
            log.info("budgets: couldn't log the warning for %s: %s", res, e)
        made.append({"resource": res, **alert})
    return made


# ---------- the meter (top bar) and the costs page ----------
VIEW_PERIODS = ("day", "week", "month")
TOP_TABLES = ("routine", "pipeline", "workflow", "chat", "recording", "extension", "sensor", "note_page")  # what "top" lists


def meter(db, resource, budget_ref=None, period=None, detail=False, now=None):
    """What a namespace (`space:<id>`), or all of Lens (resource None), spent this period, against its budget when it
    has one: {resource, period, since, spent: {usd, tokens, estimate}, budget: status | None}. The period is the one
    asked for, else the budget's own (day, week or month), else month. `budget_ref` is the budget to measure against
    (the namespace's own by default). With `detail`, also by_day, by_model and top (what cost most in it)."""
    budget_ref = budget_ref if budget_ref is not None else resource
    b = get(db, budget_ref) if budget_ref else None
    st = status(db, budget_ref, b, now) if b else None
    if period not in VIEW_PERIODS:
        period = b["period"] if b and b.get("period") in VIEW_PERIODS else "month"
    since = activity._since(period, now)
    t = activity.totals(db, resource, since)
    out = {
        "resource": resource,
        "period": period,
        "since": since,
        "spent": {"usd": t["cost_usd"], "tokens": t["tokens_in"] + t["tokens_out"], "estimate": t["estimate"]},
        "calls": t["calls"] - (t["by_kind"].get("run") or {}).get("calls", 0),
        "budget": st,
    }
    if detail:
        out["by_day"] = activity.by_day(db, resource, since)
        out["by_model"] = activity.by_model(db, resource, since)
        top = activity.top(db, None, since, 40, within=resource)
        out["top"] = [r for r in top if r["resource"].split(":")[0] in TOP_TABLES][:12]
        if resource is None:
            out["by_namespace"] = activity.top(db, "space", since, 50)
    return out


# ---------- for agents: where it all stands ----------
def _stands(m):
    """A meter as an agent reads it: what was spent this period, and the budget it's measured against."""
    st = m.get("budget")
    out = {"period": m["period"], "since": m["since"], "spent": m["spent"], "calls": m["calls"], "budget": None}
    if st and st.get("budget"):
        b = st["budget"]
        out["budget"] = {
            "usd": b.get("usd"),
            "tokens": b.get("tokens"),
            "period": b.get("period"),
            "state": st["state"],
            "share": st["share"],
            "left": st.get("left"),
            "warn_at": b.get("warn_at"),
        }
    return out


def overview(db, spaces, admin, now=None, limit=20):
    """Where spending stands, for the assistant's and MCP's `budgets` tools: each namespace in `spaces` (ids the person
    can read) against its budget, and for admins all of Lens against the overall budget and every other budget with
    where it stands. Money is USD from the activity ledger; `estimate` says a figure is a floor."""
    names = store.space_names(db)
    out = {
        "namespaces": [
            {"namespace": names.get(sid), **_stands(meter(db, f"space:{sid}", now=now))}
            for sid in sorted(spaces, key=lambda s: names.get(s) or "")[:limit]
        ]
    }
    if admin:
        out["all"] = _stands(meter(db, None, ALL, now=now))
        rows = []
        for b in all_budgets(db):
            if b["resource"] == ALL or b["resource"].startswith("space:"):
                continue  # said above
            st = status(db, b["resource"], b, now)
            table, _, key = b["resource"].partition(":")
            row = db.one("SELECT name, title FROM $r", r=R(table, int(key))) if key.isdigit() else None
            rows.append(
                {
                    "resource": b["resource"],
                    "name": (row or {}).get("name") or (row or {}).get("title"),
                    "usd": b.get("usd"),
                    "tokens": b.get("tokens"),
                    "period": b.get("period"),
                    "spent": st.get("spent"),
                    "state": st["state"],
                    "share": st.get("share"),
                    "left": st.get("left"),
                }
            )
        out["budgets"] = rows
    return out


def said(view):
    """The overview in a line: all of Lens (for admins), else the namespaces, and what is near or over."""

    def one(m, who):
        b, usd = m.get("budget"), m["spent"]["usd"]
        spent = f"{'about ' if m['spent'].get('estimate') else ''}${usd:.2f}"
        if b and b.get("usd") is not None:
            return f"{who}: {spent} of ${b['usd']:g} this {m['period']} ({b['state']})"
        return f"{who}: {spent} this {m['period']}" + (f" ({b['state']})" if b else ", no budget")

    parts = [one(view["all"], "all of Lens")] if "all" in view else [one(m, m["namespace"]) for m in view["namespaces"][:3]]
    tight = [r["name"] or r["resource"] for r in view.get("budgets", []) if r["state"] in ("near", "over")]
    if tight:
        parts.append(f"near or over: {', '.join(tight[:5])}")
    return "Read budgets: " + "; ".join(parts) if parts else "Read budgets: nothing to show"
