"""Budgets (docs/budgets.md): a cap on what a routine, pipeline, workflow or namespace may cost, checked before it runs.

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
TABLES = ("routine", "pipeline", "workflow", "space")
REF = re.compile(r"^(routine|pipeline|workflow|space):(\d+)$")
FIELDS = "resource, usd, tokens, period, on_over, warn_at, note, by, updated_at, alert"
LOOKBACK_DAYS = 30  # the past runs an estimate is drawn from
SWEEP_SECONDS = 300  # how often the scheduler looks at every budget
_swept = {"at": 0.0}


def _key(resource):
    return R("budget", resource.replace(":", "_"))


def check_ref(resource):
    if not isinstance(resource, str) or not REF.match(resource):
        raise ValueError("a budget is for routine:<id>, pipeline:<id>, workflow:<id> or space:<id>")
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
    return get(db, resource)


def get(db, resource):
    return db.one(f"SELECT {FIELDS} FROM $r", r=_key(resource))


def remove(db, resource):
    check_ref(resource)
    db.q("DELETE $r", r=_key(resource))


def all_budgets(db):
    return db.rows(f"SELECT {FIELDS} FROM budget ORDER BY resource")


# ---------- where it stands ----------
def _runs(db, resource, since):
    """How many runs this resource had since a time: a routine's runs, or the jobs of a pipeline, workflow or namespace."""
    m = REF.match(resource)
    if m and m.group(1) == "routine":
        return len(db.values("SELECT VALUE id FROM routine_run WHERE routine = $r AND started_at >= $s", r=int(m.group(2)), s=since))
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
    usd, tokens = activity.spent(db, resource, since)
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
        c = activity.costs(db, [resource], since)[resource]
        spent = {"usd": c["cost_usd"], "tokens": c["tokens"], "estimate": c["estimate"]}
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
    for res in dict.fromkeys(r for r in resources if r and REF.match(r)):
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
        made.append({"resource": res, **alert})
    return made
