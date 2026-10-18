"""Auto scaling (docs/scaling.md): Lens launches more capacity when work waits, and lets it go when it doesn't.

Two kinds of capacity, each a **scaling group** on one **provider** (scaling_drivers.py):

* `node`: a machine that runs `lens worker` (more workers on this machine, or over the mesh a machine at a cloud, a
  Proxmox host or any Linux box over SSH), so queued jobs run at once instead of one after another;
* `model`: an OpenAI-compatible model endpoint on a GPU (RunPod, Lambda, SageMaker, Bedrock, Hugging Face).

A group has a floor and a ceiling (`min`, `max`), a desired count, and **rules** as AWS Auto Scaling has them:

* target: keep a metric at a value (`desired = ceil(units × metric / target)`);
* step: when a metric is above (or below) a threshold for N minutes, add (or remove) units;
* schedule: at a cron time, set min, max or desired.

Rules propose; Lens takes the largest proposal (out when any rule says so, in only when all agree), clamps it to
[min, max], then waits out the group's cooldown after a change and its warm-up while a unit is still joining. Metrics
come from what Lens already keeps (the job queue, workers' heartbeats, the activity ledger): nothing new is collected.

**The person decides.** Scaling is off until an admin turns it on (`scaling.enabled`), and a group launches nothing
until a person, signed in to the web app, approves its terms (the provider, the unit, `max`, the monthly budget and
what happens at the cap). API keys, apps, MCP and the assistant create, change and propose groups, set the desired
count within the approved bounds, pause, and terminate units, but never approve: a change that widens the terms
(another provider or unit, a higher max or budget) takes the approval away until a person gives it again; running
units stay. A priced provider's group needs a budget, kept as a Lens budget (`budget:scale_group_<id>`, budgets.py) so
the Costs page shows it; at its cap, or the overall budget's hard limit, the group drains and terminates its units
(`at_cap` stop) or keeps `min` of them. Each running unit's price is accrued into the activity ledger as it runs,
labelled an estimate, so what scaling costs shows next to what the model calls cost.

One process runs the scaler (a lease on `app_service:scaling`, as the tunnel and the local model do), in `lens
worker` and the API: every `scaling.tick_seconds` it accrues cost, enforces budgets, reconciles each unit with its
provider (one that never joined within `join_minutes` is terminated, so a broken image costs one attempt; three
failures in a row pause the group), evaluates the rules, and launches or drains and terminates. Draining hands a
worker's job back (jobs.control), so nothing is lost. Every launch, termination and change is in the activity ledger
with who or what caused it, next to the audit log.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
import threading
import time

from . import activity, budgets, jobs, schedule, settings, store
from . import scaling_clouds  # noqa: F401 - registers the cloud drivers
from . import scaling_drivers as drivers
from .store import R

log = logging.getLogger("lens.scaling")
ROW = R("app_service", "scaling")
LEASE_SECONDS = 90
DEFAULTS = {"enabled": False, "tick_seconds": 30, "join_minutes": 15, "accrue_minutes": 5, "tag": "lens"}
KINDS = ("node", "model")
STATES = ("launching", "running", "draining", "terminating", "terminated", "failed")
ALIVE = ("launching", "running", "draining", "terminating")
RULE_TYPES = ("target", "step", "schedule")
TERMINATE = ("oldest", "newest")
AT_CAP = ("stop", "min")
MAX_UNITS = 100
MAX_RULES = 20
MAX_FAILURES = 3  # launches that failed in a row before the group pauses itself
WORKER_ALIVE_SECONDS = 120  # a worker whose heartbeat is older isn't counted (they register every 30 seconds)
WORKER_LOST_SECONDS = 600  # a unit's worker silent this long is lost: terminated and replaced
MODEL_WINDOW_SECONDS = 300  # model metrics look at the last five minutes
NAME_RX = re.compile(r"^[^\x00-\x1f]{1,80}$")
TAG_RX = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
METRICS = {
    "jobs_waiting": "jobs waiting in the queue (those the group's steps run, when it names steps)",
    "oldest_wait_minutes": "how many minutes the oldest waiting job has waited",
    "workers_busy_share": "the share of live workers that are busy, 0 to 1",
    "worker_load": "live workers' average load per CPU (1.0: every CPU busy)",
    "model_calls_per_minute": "model calls per minute over the last five minutes",
    "model_seconds": "how long a model call took on average over the last five minutes, in seconds",
}
PROVIDER_FIELDS = "record::id(id) AS id, name, driver, settings, sealed, price, note, created_at, updated_at, updated_by, checked_at, check"
GROUP_FIELDS = (
    "record::id(id) AS id, name, kind, provider, template, min, max, desired, cooldown_seconds, warmup_seconds, terminate, rules, "
    "rule_state, at_cap, enabled, approval, paused, paused_why, capped, created_at, updated_at, updated_by, last_out_at, last_in_at, "
    "failures, error, last_eval"
)
NODE_FIELDS = (
    "record::id(id) AS id, group, provider, driver, name, external_id, state, address, endpoint, price, launched_at, ready_at, "
    "ended_at, by, why, protected, error, accrued_at"
)


class Unapproved(PermissionError):
    """A pick only a person signed in to the web app makes."""


def conf(cfg):
    return {**DEFAULTS, **((cfg or {}).get("scaling") or {})}


def enabled(cfg):
    return bool(conf(cfg).get("enabled"))


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age(s, now):
    """Seconds since an ISO time, or None."""
    t = _parse(s)
    return (now - t).total_seconds() if t else None


def _ref(table, key):
    return activity.ref(table, key)


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24]
    return s or "group"


def _int(v, what, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what} is a whole number from {lo} to {hi}")
    return v


def _num(v, what, lo=0.0, hi=1e9):
    if isinstance(v, bool) or not isinstance(v, int | float) or not lo <= v <= hi:
        raise ValueError(f"{what} is a number from {lo:g} to {hi:g}")
    return float(v)


# ---------- providers ----------
def driver_specs():
    return [d.spec() for d in drivers.DRIVERS.values()]


def _seal(cfg, pid, key, value):
    return settings.seal(cfg, value, f"scale_provider:{pid}.{key}")


def _unseal(cfg, pid, key, sealed):
    return settings.unseal(cfg, sealed, f"scale_provider:{pid}.{key}")


def providers(db):
    return [provider_view(p) for p in db.rows(f"SELECT {PROVIDER_FIELDS} FROM scale_provider ORDER BY id")]


def _provider(db, pid):
    row = db.one(f"SELECT {PROVIDER_FIELDS} FROM $r", r=R("scale_provider", int(pid)))
    if not row:
        raise KeyError(f"there's no provider {pid}")
    return row


def provider_view(p):
    """A provider as the API shows it: which credentials are set, never their values."""
    drv = drivers.DRIVERS.get(p.get("driver"))
    return {
        "id": p["id"],
        "name": p.get("name"),
        "driver": p.get("driver"),
        "kind": drv.kind if drv else None,
        "label": drv.label if drv else p.get("driver"),
        "priced": drv.priced if drv else True,
        "settings": p.get("settings") or {},
        "secrets": {k: True for k in (p.get("sealed") or {})},
        "price": p.get("price"),
        "note": p.get("note"),
        "checked_at": p.get("checked_at"),
        "check": p.get("check"),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
        "updated_by": p.get("updated_by"),
    }


def opened(db, cfg, pid):
    """A provider with its credentials opened, for a driver's call only: never shown."""
    p = _provider(db, pid)
    secrets = {}
    for k, sealed in (p.get("sealed") or {}).items():
        try:
            secrets[k] = _unseal(cfg, p["id"], k, sealed)
        except Exception as e:  # noqa: BLE001 - the key changed: the credential has to be given again
            raise drivers.DriverError(f"the provider's {k} can't be read any more ({type(e).__name__}): save it again") from None
    return {**p, "secrets": secrets}


def _check_provider_body(drv, settings_, secrets, price, note):
    settings_ = drivers.check_settings(drv, settings_)
    secrets = dict(secrets or {})
    unknown = sorted(set(secrets) - set(drv.secrets))
    if unknown:
        raise ValueError(f"a {drv.label} provider has no secret {', '.join(unknown)}; it takes {', '.join(drv.secrets) or 'none'}")
    for k, v in secrets.items():
        if v is not None and (not isinstance(v, str) or len(v) > 10_000):
            raise ValueError(f"{k} is text")
    if price is not None:
        price = _num(price, "price (USD per unit-hour)", 0, 10_000)
    if note is not None and (not isinstance(note, str) or len(note) > 300):
        raise ValueError("note is up to 300 characters")
    return settings_, secrets, price, note


def _check_call(db, cfg, p, drv):
    """Prove the credentials with the driver's read-only call; kept on the provider either way."""
    try:
        with activity.call(f"scaling.{drv.id}.check", cfg, refs=[_ref("scale_provider", p["id"])]):
            out = drv.check(opened(db, cfg, p["id"]))
        result = {"ok": True, **(out if isinstance(out, dict) else {})}
    except Exception as e:  # noqa: BLE001 - what the provider said, for the person
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500] if not isinstance(e, drivers.DriverError) else str(e)[:500]}
    db.q("UPDATE $r SET checked_at = $t, check = $c", r=R("scale_provider", p["id"]), t=store.now(), c=result)
    return result


def _fetch(cfg, drv, say=None):
    """The driver's library, fetched the first time a provider of its kind is saved (components.pip_install)."""
    if drv.ready():
        return
    from . import components

    components.pip_install(cfg, drv.extra, say or (lambda m: log.info("scaling: %s", m)))
    if not drv.ready():
        raise ValueError(f"{drv.label} needs the {drv.extra} packages, which couldn't be installed here")


def add_provider(db, cfg, name, driver, settings_=None, secrets=None, price=None, note=None, by=None, check=True):
    """Save a provider: its settings, its credentials sealed (bound to the provider's id and the field), and the
    driver's read-only check. The view (never the credentials)."""
    drv = drivers.get(driver)
    if not isinstance(name, str) or not NAME_RX.match(name.strip()):
        raise ValueError("a provider has a name of up to 80 characters")
    settings_, secrets, price, note = _check_provider_body(drv, settings_, secrets, price, note)
    missing = [k for k in drv.secrets if drv.fields[k].get("required") and not secrets.get(k)]
    if missing:
        raise ValueError(f"a {drv.label} provider needs {', '.join(missing)}")
    if drv.extra:
        _fetch(cfg, drv)
    pid = db.next_id("scale_provider")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("scale_provider", pid),
        d=store.clean(
            {
                "name": name.strip(),
                "driver": drv.id,
                "settings": settings_,
                "sealed": {k: _seal(cfg, pid, k, v) for k, v in secrets.items() if v},
                "price": price,
                "note": note,
                "created_at": store.now(),
                "updated_at": store.now(),
                "updated_by": by,
            }
        ),
    )
    activity.record("run", "scaling.provider.add", [_ref("scale_provider", pid)], cfg, db, actor=by, detail={"driver": drv.id})
    p = _provider(db, pid)
    if check:
        _check_provider(db, cfg, p, drv)
        p = _provider(db, pid)
    return provider_view(p)


def _check_provider(db, cfg, p, drv):
    return _check_call(db, cfg, p, drv)


def update_provider(db, cfg, pid, changes, by=None, check=True):
    """Change a provider's name, settings, credentials (a secret given as null is removed; one left out is kept),
    price or note."""
    p = _provider(db, pid)
    drv = drivers.get(p["driver"])
    changes = dict(changes or {})
    unknown = sorted(set(changes) - {"name", "settings", "secrets", "price", "note"})
    if unknown:
        raise ValueError(f"a provider has no {', '.join(unknown)}")
    name = changes.get("name", p.get("name"))
    if not isinstance(name, str) or not NAME_RX.match(name.strip()):
        raise ValueError("a provider has a name of up to 80 characters")
    settings_ = {**(p.get("settings") or {}), **(changes.get("settings") or {})} if "settings" in changes else p.get("settings") or {}
    settings_, secrets, price, note = _check_provider_body(
        drv, settings_, changes.get("secrets"), changes.get("price", p.get("price")), changes.get("note", p.get("note"))
    )
    sealed = dict(p.get("sealed") or {})
    for k, v in secrets.items():
        if v is None:
            sealed.pop(k, None)
        elif v:
            sealed[k] = _seal(cfg, p["id"], k, v)
    missing = [k for k in drv.secrets if drv.fields[k].get("required") and k not in sealed]
    if missing:
        raise ValueError(f"a {drv.label} provider needs {', '.join(missing)}")
    db.q(  # SET, not MERGE: a merge would keep a setting or a credential taken away
        "UPDATE $r SET name = $n, settings = $s, sealed = $k, price = $p, note = $o, updated_at = $t, updated_by = $u",
        r=R("scale_provider", p["id"]),
        n=name.strip(),
        s=settings_,
        k=sealed,
        p=price,
        o=note,
        t=store.now(),
        u=by,
    )
    activity.record(
        "run", "scaling.provider.change", [_ref("scale_provider", p["id"])], cfg, db, actor=by, detail={"fields": sorted(changes)}
    )
    out = _provider(db, p["id"])
    if check:
        _check_provider(db, cfg, out, drv)
        out = _provider(db, p["id"])
    return provider_view(out)


def check_provider(db, cfg, pid):
    p = _provider(db, pid)
    return _check_provider(db, cfg, p, drivers.get(p["driver"]))


def remove_provider(db, cfg, pid, by=None):
    p = _provider(db, pid)
    used = db.values("SELECT VALUE record::id(id) FROM scale_group WHERE provider = $p", p=p["id"])
    if used:
        raise ValueError(f"groups {', '.join(map(str, used))} use this provider: remove them first")
    db.q("DELETE $r", r=R("scale_provider", p["id"]))
    activity.record("run", "scaling.provider.remove", [_ref("scale_provider", p["id"])], cfg, db, actor=by)


# ---------- groups ----------
def _budget_ref(gid):
    return f"scale_group:{int(gid)}"


def check_rule(rule):
    """A rule as it's kept, or ValueError."""
    if not isinstance(rule, dict):
        raise ValueError("a rule is an object")
    t = rule.get("type")
    if t not in RULE_TYPES:
        raise ValueError(f"a rule's type is one of {', '.join(RULE_TYPES)}")
    out = {"type": t}
    if t in ("target", "step"):
        m = rule.get("metric")
        if m not in METRICS:
            raise ValueError(f"a rule's metric is one of {', '.join(METRICS)}")
        out["metric"] = m
    if t == "target":
        out["target"] = _num(rule.get("target"), "target", 0.000001)
    elif t == "step":
        above, below = rule.get("above"), rule.get("below")
        if (above is None) == (below is None):
            raise ValueError("a step rule has above or below: the threshold")
        if above is not None:
            out["above"] = _num(above, "above")
        else:
            out["below"] = _num(below, "below")
        out["minutes"] = _int(rule.get("minutes", 1), "minutes", 0, 1440)
        add, remove = rule.get("add"), rule.get("remove")
        if (add is None) == (remove is None):
            raise ValueError('a step rule adds or removes units: add or remove, a count or a percentage like "50%"')
        for k, v in (("add", add), ("remove", remove)):
            if v is None:
                continue
            if isinstance(v, str) and v.strip().endswith("%") and v.strip()[:-1].isdigit() and 0 < int(v.strip()[:-1]) <= 1000:
                out[k] = v.strip()
            else:
                out[k] = _int(v, k, 1, MAX_UNITS)
    else:
        cron, tz = rule.get("cron"), rule.get("timezone") or "UTC"
        if not isinstance(cron, str):
            raise ValueError("a schedule rule has cron: when it fires, like 0 9 * * 1-5")
        try:
            schedule.check(cron.strip(), tz)
        except ValueError as e:
            raise ValueError(f"a schedule rule's cron: {e}") from None
        out["cron"], out["timezone"] = cron.strip(), tz
        if all(rule.get(k) is None for k in ("min", "max", "desired")):
            raise ValueError("a schedule rule sets min, max or desired")
        for k in ("min", "max", "desired"):
            if rule.get(k) is not None:
                out[k] = _int(rule[k], k, 0, MAX_UNITS)
    if rule.get("name") is not None:
        if not isinstance(rule["name"], str) or len(rule["name"]) > 80:
            raise ValueError("a rule's name is up to 80 characters")
        out["name"] = rule["name"]
    return out


def describe_rule(r):
    if r["type"] == "target":
        return f"keep {r['metric']} at {r['target']:g}"
    if r["type"] == "step":
        cond = f"{r['metric']} {'above' if 'above' in r else 'below'} {r.get('above', r.get('below')):g} for {r['minutes']} min"
        act = f"add {r['add']}" if "add" in r else f"remove {r['remove']}"
        return f"{act} when {cond}"
    parts = [f"{k} {r[k]}" for k in ("min", "max", "desired") if k in r]
    return f"at {schedule.describe(r['cron'])} ({r['timezone']}): {', '.join(parts)}"


def terms(g):
    """What an approval binds: what's launched, where, how many at most, and what it may cost."""
    return {
        "kind": g.get("kind"),
        "provider": g.get("provider"),
        "template": g.get("template") or {},
        "max": g.get("max"),
        "budget": (g.get("budget") or {}).get("usd") if isinstance(g.get("budget"), dict) else g.get("budget"),
        "at_cap": g.get("at_cap") or "stop",
    }


def widens(old, new):
    """Whether new terms let more be spent than the old ones a person approved."""
    if old.get("kind") != new.get("kind") or old.get("provider") != new.get("provider") or old.get("template") != new.get("template"):
        return True
    if (new.get("max") or 0) > (old.get("max") or 0):
        return True
    ob, nb = old.get("budget"), new.get("budget")
    if ob is not None and (nb is None or nb > ob):
        return True
    return old.get("at_cap", "stop") == "stop" and new.get("at_cap") == "min"


def _check_group_body(db, body, current=None):
    """A group's fields checked: the row's content (budget apart) and the budget cap."""
    body = dict(body or {})
    unknown = sorted(
        set(body)
        - {
            "name",
            "kind",
            "provider",
            "template",
            "min",
            "max",
            "desired",
            "cooldown_seconds",
            "warmup_seconds",
            "terminate",
            "rules",
            "at_cap",
            "enabled",
            "budget",
        }
    )
    if unknown:
        raise ValueError(f"a group has no {', '.join(unknown)}")
    cur = current or {}
    name = body.get("name", cur.get("name"))
    if not isinstance(name, str) or not NAME_RX.match(name.strip()):
        raise ValueError("a group has a name of up to 80 characters")
    pid = body.get("provider", cur.get("provider"))
    if pid is None:
        raise ValueError("a group has a provider")
    p = _provider(db, pid)
    drv = drivers.get(p["driver"])
    kind = body.get("kind", cur.get("kind") or drv.kind)
    if kind != drv.kind:
        raise ValueError(f"a {drv.label} provider launches {drv.kind} units, not {kind}")
    template = drivers.check_template(drv, body.get("template", cur.get("template")))
    lo = _int(body.get("min", cur.get("min", 0)), "min", 0, MAX_UNITS)
    hi = _int(body.get("max", cur.get("max", 1)), "max", 0, MAX_UNITS)
    if hi < lo:
        raise ValueError("max is at least min")
    desired = body.get("desired", cur.get("desired"))
    desired = lo if desired is None else min(hi, max(lo, _int(desired, "desired", 0, MAX_UNITS)))
    rules = [check_rule(r) for r in (body.get("rules", cur.get("rules")) or [])]
    if len(rules) > MAX_RULES:
        raise ValueError(f"a group has up to {MAX_RULES} rules")
    terminate = body.get("terminate", cur.get("terminate") or "oldest")
    if terminate not in TERMINATE:
        raise ValueError(f"terminate is one of {', '.join(TERMINATE)}")
    at_cap = body.get("at_cap", cur.get("at_cap") or "stop")
    if at_cap not in AT_CAP:
        raise ValueError(f"at_cap is one of {', '.join(AT_CAP)}: stop every unit at the budget's cap, or keep min")
    budget = body.get("budget", cur.get("budget"))
    if isinstance(budget, dict):
        budget = budget.get("usd")
    if budget is not None:
        budget = _num(budget, "budget (USD per month)", 0.01, 1e7)
    if drv.priced and budget is None:
        raise ValueError(f"a group on {drv.label} needs a budget: USD per month")
    row = {
        "name": name.strip(),
        "kind": kind,
        "provider": p["id"],
        "template": template,
        "min": lo,
        "max": hi,
        "desired": desired,
        "cooldown_seconds": _int(body.get("cooldown_seconds", cur.get("cooldown_seconds", 300)), "cooldown_seconds", 0, 86400),
        "warmup_seconds": _int(body.get("warmup_seconds", cur.get("warmup_seconds", 300)), "warmup_seconds", 0, 7200),
        "terminate": terminate,
        "rules": rules,
        "at_cap": at_cap,
        "enabled": bool(body.get("enabled", cur.get("enabled", True))),
    }
    return row, budget, p, drv


def groups(db):
    return [group_view(db, g) for g in db.rows(f"SELECT {GROUP_FIELDS} FROM scale_group ORDER BY id")]


def _group(db, gid):
    row = db.one(f"SELECT {GROUP_FIELDS} FROM $r", r=R("scale_group", int(gid)))
    if not row:
        raise KeyError(f"there's no scaling group {gid}")
    return row


def get_group(db, gid):
    return group_view(db, _group(db, gid))


def _alive(db, gid):
    return db.rows(f"SELECT {NODE_FIELDS} FROM scale_node WHERE group = $g AND state IN $s ORDER BY id", g=int(gid), s=list(ALIVE))


def group_view(db, g, now=None):
    """A group as the API shows it: its rules in words, its budget's standing, its units, and whether it may launch."""
    now = now or _now()
    units = _alive(db, g["id"])
    b = budgets.get(db, _budget_ref(g["id"]))
    st = budgets.status(db, _budget_ref(g["id"]), b, now) if b else None
    approval = g.get("approval")
    t = terms({**g, "budget": b.get("usd") if b else None})  # the budget lives in the budget table, not on the row
    drv = drivers.DRIVERS.get((db.one("SELECT driver FROM $r", r=R("scale_provider", int(g["provider"]))) or {}).get("driver"))
    out = {
        "id": g["id"],
        "name": g.get("name"),
        "kind": g.get("kind"),
        "provider": g.get("provider"),
        "template": g.get("template") or {},
        "min": g.get("min") or 0,
        "max": g.get("max") or 0,
        "desired": g.get("desired") or 0,
        "cooldown_seconds": g.get("cooldown_seconds"),
        "warmup_seconds": g.get("warmup_seconds"),
        "terminate": g.get("terminate") or "oldest",
        "rules": [{**r, "says": describe_rule(r)} for r in (g.get("rules") or [])],
        "at_cap": g.get("at_cap") or "stop",
        "enabled": bool(g.get("enabled", True)),
        "paused": bool(g.get("paused")),
        "paused_why": g.get("paused_why"),
        "capped": g.get("capped"),
        "approval": approval,
        "approved": bool(approval) and not widens(approval.get("terms") or {}, t),
        "terms": t,
        "needs_join": bool(drv and drv.needs_join),
        "budget": {"usd": b.get("usd"), "period": b.get("period"), "state": st["state"], "spent": st["spent"], "left": st["left"]}
        if b
        else None,
        "units": {
            "launching": sum(u.get("state") == "launching" for u in units),
            "running": sum(u.get("state") == "running" for u in units),
            "draining": sum(u.get("state") in ("draining", "terminating") for u in units),
        },
        "last_out_at": g.get("last_out_at"),
        "last_in_at": g.get("last_in_at"),
        "last_eval": g.get("last_eval"),
        "failures": g.get("failures") or 0,
        "error": g.get("error"),
        "created_at": g.get("created_at"),
        "updated_at": g.get("updated_at"),
        "updated_by": g.get("updated_by"),
    }
    out["waiting_for"] = waiting_for(out)
    return out


def waiting_for(view, cfg=None):
    """Why a group launches nothing now, in words, or None."""
    if cfg is not None and not enabled(cfg):
        return "scaling is off: turn it on in Settings › Auto scaling"
    if not view.get("enabled"):
        return "the group is off"
    if view.get("paused"):
        return f"paused: {view.get('paused_why') or 'by hand'}"
    if not view.get("approved"):
        return "a person's approval of its terms, in Settings › Auto scaling"
    if view.get("capped"):
        return f"its budget: {view['capped']}"
    if cfg is not None and view.get("needs_join") and not can_join(cfg):
        return "the mesh's install line, which other machines join Lens with (in progress, docs/mesh.md)"
    return None


def _set_budget(db, gid, usd, by):
    ref = _budget_ref(gid)
    if usd is None:
        if budgets.get(db, ref):
            budgets.remove(db, ref)
        return
    old = budgets.get(db, ref) or {}
    budgets.put(db, ref, usd=usd, period="month", on_over="skip", warn_at=old.get("warn_at") or 0.8, note=old.get("note"), by=by)


def create_group(db, cfg, body, by=None, via="token"):
    """Save a group. It launches nothing until a person approves its terms (approve_group): a group made by a person
    signed in to the web app (`via` web) is approved as it's made, since that is their pick."""
    row, budget, p, drv = _check_group_body(db, body)
    gid = db.next_id("scale_group")
    row = {**row, "rule_state": {}, "paused": False, "failures": 0, "created_at": store.now(), "updated_at": store.now(), "updated_by": by}
    db.q("CREATE $r CONTENT $d", r=R("scale_group", gid), d=store.clean(row))
    _set_budget(db, gid, budget, by)
    activity.record(
        "run",
        "scaling.group.create",
        [_ref("scale_group", gid), _ref("scale_provider", p["id"])],
        cfg,
        db,
        actor=by,
        detail={"max": row["max"], "min": row["min"], "budget": budget, "via": via},
    )
    if via == "web":
        approve_group(db, cfg, gid, by, via)
    return get_group(db, gid)


def update_group(db, cfg, gid, changes, by=None, via="token"):
    """Change a group. A change that widens its approved terms takes the approval away (a person gives it again);
    one made by a person signed in to the web app carries their approval."""
    g = _group(db, gid)
    g = {**g, "budget": (budgets.get(db, _budget_ref(g["id"])) or {}).get("usd")}  # a budget left out is kept
    row, budget, p, drv = _check_group_body(db, changes, g)
    before, after = terms(g), terms({**row, "budget": budget})
    approval = g.get("approval")
    if approval and widens(approval.get("terms") or {}, after):
        approval = None
    row = {**row, "approval": approval, "updated_at": store.now(), "updated_by": by}
    if "rules" in changes:
        row["rule_state"] = {}
    db.q("UPDATE $r MERGE $d", r=R("scale_group", g["id"]), d=row)
    if approval is None and g.get("approval"):  # MERGE keeps a field given as None: take it off
        db.q("UPDATE $r SET approval = NONE", r=R("scale_group", g["id"]))
    _set_budget(db, g["id"], budget, by)
    activity.record(
        "run",
        "scaling.group.change",
        [_ref("scale_group", g["id"]), _ref("scale_provider", p["id"])],
        cfg,
        db,
        actor=by,
        detail={"fields": sorted(changes or {}), "widens": widens(before, after), "via": via},
    )
    if via == "web" and approval is None:
        approve_group(db, cfg, g["id"], by, via)
    return get_group(db, g["id"])


def approve_group(db, cfg, gid, by, via="web"):
    """A person's approval of a group's terms as they stand now: only from a signed-in session in the web app.
    Agents (API keys, apps, MCP, the assistant) never approve."""
    if via != "web":
        raise Unapproved("approving a scaling group's terms is the person's own pick, signed in to Lens")
    g = _group(db, gid)
    t = terms({**g, "budget": (budgets.get(db, _budget_ref(g["id"])) or {}).get("usd")})
    drv = drivers.get(_provider(db, g["provider"])["driver"])
    if drv.priced and t["budget"] is None:
        raise ValueError("give the group a budget first")
    approval = {"by": by, "at": store.now(), "terms": t}
    db.q("UPDATE $r SET approval = $a, updated_at = $t, updated_by = $u", r=R("scale_group", g["id"]), a=approval, t=store.now(), u=by)
    activity.record("run", "scaling.group.approve", [_ref("scale_group", g["id"])], cfg, db, actor=by, detail=t)
    return get_group(db, g["id"])


def set_desired(db, cfg, gid, n, by=None, via="token"):
    """Ask for a number of units now, within [min, max]: anyone who may change the group, agents included, since the
    bounds are what the person approved. The rules take over again at their next proposal."""
    g = _group(db, gid)
    n = _int(n, "desired", 0, MAX_UNITS)
    if not g.get("min", 0) <= n <= g.get("max", 0):
        raise ValueError(f"desired is from {g.get('min', 0)} to {g.get('max', 0)} for this group")
    db.q("UPDATE $r SET desired = $n, updated_at = $t, updated_by = $u", r=R("scale_group", g["id"]), n=n, t=store.now(), u=by)
    activity.record("run", "scaling.group.desired", [_ref("scale_group", g["id"])], cfg, db, actor=by, detail={"desired": n, "via": via})
    return get_group(db, g["id"])


def pause_group(db, cfg, gid, paused=True, why=None, by=None):
    g = _group(db, gid)
    db.q(
        "UPDATE $r SET paused = $p, paused_why = $w, failures = 0, updated_at = $t, updated_by = $u",
        r=R("scale_group", g["id"]),
        p=bool(paused),
        w=(why or ("by hand" if by else None)) if paused else None,
        t=store.now(),
        u=by,
    )
    activity.record("run", "scaling.group.pause" if paused else "scaling.group.resume", [_ref("scale_group", g["id"])], cfg, db, actor=by)
    return get_group(db, g["id"])


def remove_group(db, cfg, gid, by=None):
    """Remove a group: its live units are terminated first (a draining one at once), then its rows and budget go."""
    g = _group(db, gid)
    p = opened(db, cfg, g["provider"])
    drv = drivers.get(p["driver"])
    for n in _alive(db, g["id"]):
        _terminate(db, cfg, drv, p, n, "the group was removed", by)
    db.q("DELETE scale_node WHERE group = $g", g=g["id"])
    db.q("DELETE $r", r=R("scale_group", g["id"]))
    _set_budget(db, g["id"], None, by)
    activity.record("run", "scaling.group.remove", [_ref("scale_group", g["id"])], cfg, db, actor=by)


def history(db, gid, limit=50, before=None):
    return activity.history(db, _budget_ref(gid), limit, before)


# ---------- units ----------
def nodes(db, gid=None, alive=False):
    where, p = [], {}
    if gid is not None:
        where.append("group = $g")
        p["g"] = int(gid)
    if alive:
        where.append("state IN $s")
        p["s"] = list(ALIVE)
    q = f"SELECT {NODE_FIELDS} FROM scale_node" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id DESC LIMIT 500"
    return [node_view(n) for n in db.rows(q, **p)]


def _node(db, nid):
    row = db.one(f"SELECT {NODE_FIELDS} FROM $r", r=R("scale_node", int(nid)))
    if not row:
        raise KeyError(f"there's no unit {nid}")
    return row


def node_view(n):
    endpoint = n.get("endpoint")
    return {
        **{k: n.get(k) for k in NODE_FIELDS.replace("record::id(id) AS id", "id").split(", ")},
        "endpoint": {k: v for k, v in endpoint.items() if k != "api_key"} | ({"api_key": True} if endpoint.get("api_key") else {})
        if isinstance(endpoint, dict)
        else None,
    }


def protect(db, cfg, nid, on=True, by=None):
    n = _node(db, nid)
    db.q("UPDATE $r SET protected = $p", r=R("scale_node", n["id"]), p=bool(on))
    activity.record(
        "run",
        "scaling.unit.protect",
        [_ref("scale_node", n["id"]), _ref("scale_group", n["group"])],
        cfg,
        db,
        actor=by,
        detail={"on": bool(on)},
    )
    return node_view(_node(db, nid))


def terminate_node(db, cfg, nid, by=None, why=None):
    """Let a unit go now: a running worker drains first (its job is handed back), anything else is terminated."""
    n = _node(db, nid)
    if n.get("state") not in ALIVE:
        raise ValueError(f"unit {nid} is {n.get('state')}")
    if n.get("state") == "running" and n.get("driver") and drivers.get(n["driver"]).kind == "node":
        _drain(db, cfg, n, why or "asked to", by)
    elif n.get("state") != "terminating":
        p = opened(db, cfg, n["provider"])
        _terminate(db, cfg, drivers.get(p["driver"]), p, n, why or "asked to", by)
    return node_view(_node(db, nid))


def _drain(db, cfg, n, why, by=None):
    try:
        jobs.control(db, n["name"], "drain", by=by or "scaling")
    except KeyError:
        pass  # never registered as a worker: nothing to hand back
    db.q("UPDATE $r SET state = 'draining', why = $w", r=R("scale_node", n["id"]), w=why)
    activity.record(
        "run", "scaling.unit.drain", [_ref("scale_node", n["id"]), _ref("scale_group", n["group"])], cfg, db, actor=by, detail={"why": why}
    )


def _terminate(db, cfg, drv, p, n, why, by=None, state="terminated", now=None):
    """Terminate a unit at its provider and close its row (accruing its last cost); a provider that fails to is tried
    again next tick (the row stays terminating)."""
    now = now or _now()
    db.q("UPDATE $r SET state = 'terminating', why = $w", r=R("scale_node", n["id"]), w=why)
    if n.get("external_id"):
        try:
            with activity.call(f"scaling.{drv.id}.terminate", cfg, refs=[_ref("scale_node", n["id"]), _ref("scale_group", n["group"])]):
                drv.terminate(p, n)
        except Exception as e:  # noqa: BLE001 - tried again next tick
            err = f"{type(e).__name__}: {e}"[:300] if not isinstance(e, drivers.DriverError) else str(e)[:300]
            db.q("UPDATE $r SET error = $e", r=R("scale_node", n["id"]), e=f"couldn't terminate: {err}")
            log.warning("scaling: couldn't terminate unit %s: %s", n["id"], err)
            return False
    accrue(db, cfg, n, now, final=True)
    db.q(
        "UPDATE $r SET state = $s, ended_at = $t, accrued_at = $t",
        r=R("scale_node", n["id"]),
        s=state,
        t=_iso(now),
    )
    db.q("DELETE $w", w=R("worker", n["name"]))
    activity.record(
        "run",
        "scaling.terminate" if state == "terminated" else "scaling.fail",
        [_ref("scale_node", n["id"]), _ref("scale_group", n["group"]), _ref("scale_provider", n["provider"])],
        cfg,
        db,
        actor=by,
        ok=state == "terminated",
        detail={"why": why, "unit": n.get("name")},
    )
    return True


def accrue(db, cfg, n, now, final=False):
    """What a unit cost since its last accrual, as a ledger row counted for it, its group and its provider: its price
    per hour for the hours since, always an estimate (a list price, not a bill). Every accrue_minutes, and when it
    ends."""
    price = n.get("price")
    since = _parse(n.get("accrued_at") or n.get("launched_at"))
    if not price or since is None:
        return 0.0
    hours = max(0.0, (now - since).total_seconds() / 3600)
    if hours <= 0 or (not final and hours * 60 < conf(cfg)["accrue_minutes"]):
        return 0.0
    cost = round(price * hours, 6)
    activity.record(
        "out",
        "scaling.unit",
        [_ref("scale_node", n["id"]), _ref("scale_group", n["group"]), _ref("scale_provider", n["provider"])],
        cfg,
        db,
        cost_usd=cost,
        ms=int(hours * 3_600_000),
        detail={"estimate": True, "price_per_hour": price, "unit": n.get("name")},
    )
    db.q("UPDATE $r SET accrued_at = $t", r=R("scale_node", n["id"]), t=_iso(now))
    return cost


# ---------- metrics ----------
def _live_workers(db, now):
    out = []
    for w in jobs.workers(db, now):
        age = _age(w.get("heartbeat_at"), now)
        if age is not None and age <= WORKER_ALIVE_SECONDS:
            out.append(w)
    return out


def metrics(db, cfg, steps=None, now=None):
    """Every metric's value now: {name: value}. `steps` narrows the queue metrics to jobs those steps run."""
    now = now or _now()
    q, p = "SELECT created_at FROM job WHERE status = 'queued'", {}
    if steps:
        q += " AND next_step IN $steps"
        p["steps"] = sorted(steps)
    waiting = db.rows(q, **p)
    oldest = None
    for j in waiting:
        age = _age(j.get("created_at"), now)
        if age is not None and (oldest is None or age > oldest):
            oldest = age
    live = _live_workers(db, now)
    busy = [w for w in live if w.get("current") is not None]
    loads = [w["load"] for w in live if isinstance(w.get("load"), int | float)]
    since = _iso(now - dt.timedelta(seconds=MODEL_WINDOW_SECONDS))
    row = (
        db.one(
            "SELECT count() AS n, math::sum(ms ?? 0) AS ms FROM activity WHERE kind = 'out' AND at >= $s AND string::starts_with(action, 'model.') GROUP ALL",
            s=since,
        )
        or {}
    )
    calls = int(row.get("n") or 0)
    return {
        "jobs_waiting": len(waiting),
        "oldest_wait_minutes": round(oldest / 60, 1) if oldest is not None else 0.0,
        "workers_busy_share": round(len(busy) / len(live), 3) if live else 0.0,
        "worker_load": round(sum(loads) / len(loads), 2) if loads else 0.0,
        "model_calls_per_minute": round(calls / (MODEL_WINDOW_SECONDS / 60), 2),
        "model_seconds": round(float(row.get("ms") or 0) / calls / 1000, 2) if calls else 0.0,
        "workers_live": len(live),
    }


# ---------- rules ----------
def _count(v, units):
    if isinstance(v, str):
        return max(1, math.ceil(units * int(v[:-1]) / 100))
    return int(v)


def evaluate(g, m, units, now, state=None):
    """What the rules propose for a group with `units` running: (desired or None when no rule speaks, the new rule
    state, [what each rule said], the new min, the new max: None unless a schedule rule set them). Step rules fire once their condition has held for their minutes; schedule rules
    once per time they name."""
    state = dict(state or {})
    proposals, said = [], []
    new_min, new_max = None, None
    for i, r in enumerate(g.get("rules") or []):
        key, st = str(i), dict(state.get(str(i)) or {})
        if r["type"] == "target":
            v = m.get(r["metric"]) or 0
            want = math.ceil(max(units, 1) * v / r["target"]) if v > 0 else 0
            proposals.append(want)
            said.append(f"{describe_rule(r)}: {r['metric']} is {v:g}, so {want}")
        elif r["type"] == "step":
            v = m.get(r["metric"]) or 0
            breach = v > r["above"] if "above" in r else v < r["below"]
            if not breach:
                st.pop("since", None)
            else:
                since = _parse(st.get("since"))
                if since is None:
                    since = now
                    st["since"] = _iso(now)
                if (now - since).total_seconds() >= r["minutes"] * 60:
                    want = units + _count(r["add"], units) if "add" in r else units - _count(r["remove"], units)
                    proposals.append(max(0, want))
                    said.append(f"{describe_rule(r)}: {r['metric']} is {v:g}, so {max(0, want)}")
        else:
            last = _parse(st.get("fired_at")) or _parse(g.get("updated_at")) or _parse(g.get("created_at")) or now
            try:
                nxt = schedule.next_after(r["cron"], r.get("timezone") or "UTC", last)
            except ValueError:
                nxt = None
            if nxt is not None and nxt <= now:
                st["fired_at"] = _iso(now)
                if r.get("min") is not None:
                    new_min = r["min"]
                if r.get("max") is not None:
                    new_max = r["max"]
                if r.get("desired") is not None:
                    proposals.append(r["desired"])
                said.append(f"{describe_rule(r)}: fired")
        state[key] = st
    want = max(proposals) if proposals else None
    return want, state, said, new_min, new_max


# ---------- the scaler ----------
def mesh_module():
    """The Nebula mesh (mesh.py, docs/mesh.md) when this Lens has it, else None."""
    try:
        from . import mesh
    except ImportError:
        return None
    return mesh


def can_join(cfg):
    """Whether another machine can join Lens now: the mesh is on and has the install line machines join with."""
    mesh = mesh_module()
    if mesh is None or not (getattr(mesh, "conf", lambda c: {})(cfg) or {}).get("enabled"):
        return False
    return bool(getattr(mesh, "make_token", None) and getattr(mesh, "join_line", None))


def bootstrap(db, cfg, g, name):
    """How a unit joins Lens: a worker started here gets its name and inherits the rest; a machine elsewhere gets the
    mesh's install line (`LENS_JOIN=… LENS_JOIN_TOKEN=… sh install.sh`) with a one-time join token good for an hour,
    named for the unit, so the provider's user-data carries no long-lived secret: the database credential travels
    inside the sealed join answer. {env, line, token_id, tag} (line and token_id None without the mesh)."""
    boot = {"env": {"LENS_SCALING_UNIT": name}, "line": None, "token_id": None, "tag": conf(cfg)["tag"]}
    if not can_join(cfg):
        return boot
    mesh = mesh_module()
    make, line = mesh.make_token, mesh.join_line
    row, token = make(db, cfg, by="scaling", role="worker", name=name, hours=1, uses=1)
    boot["line"], boot["token_id"] = line(cfg, token), (row or {}).get("id")
    return boot


class Scaler:
    """Holds the lease and runs each group's loop."""

    def __init__(self, db, cfg_fn, name, log_=None):
        self.db, self.cfg_fn, self.name, self.log = db, cfg_fn, name, log_ or (lambda *a: None)
        self.lock = threading.Lock()

    def _lease(self, now):
        return bool(
            self.db.rows(
                "UPSERT $r MERGE {owner: $me, at: $t} WHERE owner = NONE OR owner = $me OR at < $stale RETURN AFTER",
                r=ROW,
                me=self.name,
                t=_iso(now),
                stale=_iso(now - dt.timedelta(seconds=LEASE_SECONDS)),
            )
        )

    def tick(self, now=None, lease=True):
        """One pass over every group. Returns what it did: {launched, terminated, groups}."""
        now = now or _now()
        cfg = self.cfg_fn()
        if not enabled(cfg) and not self.db.values("SELECT VALUE id FROM scale_node WHERE state IN $s LIMIT 1", s=list(ALIVE)):
            return None  # off, with nothing left to let go: not even the lease is written
        if lease and not self._lease(now):
            return None
        did = {"launched": 0, "terminated": 0, "groups": 0}
        for g in self.db.rows(f"SELECT {GROUP_FIELDS} FROM scale_group ORDER BY id"):
            try:
                self._group(cfg, g, now, did)
                did["groups"] += 1
            except Exception as e:  # noqa: BLE001 - one group's trouble doesn't stop the rest; it shows on the group
                err = f"{type(e).__name__}: {e}"[:300]
                self.db.q("UPDATE $r SET error = $e", r=R("scale_group", g["id"]), e=err)
                self.log(f"scaling: group {g['id']}: {err}")
        self.db.q("UPDATE $r MERGE {ticked_at: $t, running: true} WHERE owner = $me", r=ROW, t=_iso(now), me=self.name)
        return did

    def _group(self, cfg, g, now, did):
        db = self.db
        p = opened(db, cfg, g["provider"])
        drv = drivers.get(p["driver"])
        units = _alive(db, g["id"])
        # 1. cost, 2. the budget
        for n in units:
            if n.get("state") in ("running", "draining") and n.get("price"):
                accrue(db, cfg, n, now)
        capped = budgets.used_up(db, [_budget_ref(g["id"])], now) if units or g.get("desired") else None
        if (capped or None) != (g.get("capped") or None):
            db.q("UPDATE $r SET capped = $c", r=R("scale_group", g["id"]), c=capped)
            if capped:
                activity.record("run", "scaling.group.capped", [_ref("scale_group", g["id"])], cfg, db, detail={"why": capped})
        # 3. each unit against its provider and Lens
        units = [n for n in units if self._reconcile(cfg, g, drv, p, n, now, did)]
        # 4. the rules, then act
        running = [n for n in units if n.get("state") == "running"]
        launching = [n for n in units if n.get("state") == "launching"]
        view = group_view(db, g, now)
        may_launch = enabled(cfg) and view["enabled"] and not view["paused"] and view["approved"] and not capped
        joinable = not drv.needs_join or can_join(cfg)
        lo, hi, desired = g.get("min") or 0, g.get("max") or 0, g.get("desired") or 0
        if may_launch and g.get("rules"):
            want, state, said, new_min, new_max = evaluate(
                g, metrics(db, cfg, (g.get("template") or {}).get("steps"), now), len(running), now, g.get("rule_state")
            )
            sets = {"rule_state": state, "last_eval": {"at": _iso(now), "said": said}}
            if new_min is not None or new_max is not None:
                lo = new_min if new_min is not None else lo
                hi = new_max if new_max is not None else hi
                if hi < lo:
                    hi = lo
                sets |= {"min": lo, "max": hi}
            if want is not None:
                desired = want
            desired = min(hi, max(lo, desired))
            sets["desired"] = desired
            db.q("UPDATE $r MERGE $d", r=R("scale_group", g["id"]), d=sets)
        target = desired
        if not enabled(cfg) or not view["enabled"]:
            target = 0
        elif capped:
            target = lo if g.get("at_cap") == "min" else 0
        elif not view["approved"] or view["paused"] or not joinable:
            target = min(target, len(running) + len(launching))  # nothing new; what runs stays
        have = len(running) + len(launching)
        if target > have:
            if any((_age(n.get("launched_at"), now) or 0) < (g.get("warmup_seconds") or 0) for n in launching):
                return  # warming up: wait for it to join before judging again
            cool = _age(g.get("last_out_at"), now)
            if cool is not None and cool < (g.get("cooldown_seconds") or 0):
                return
            for _ in range(target - have):
                if self._launch(cfg, g, drv, p, now, f"desired {target}, {have} running or joining"):
                    did["launched"] += 1
                    have += 1
                else:
                    break
            db.q("UPDATE $r SET last_out_at = $t", r=R("scale_group", g["id"]), t=_iso(now))
        elif target < have:
            cool = _age(g.get("last_in_at"), now)
            if cool is not None and cool < (g.get("cooldown_seconds") or 0) and target > 0:
                return
            gone = 0
            for n in self._for_removal(g, launching + running, have - target, now):
                why = f"desired {target}, {have} running"
                if n.get("state") == "running" and drv.kind == "node":
                    _drain(db, cfg, n, why)
                else:
                    _terminate(db, cfg, drv, p, n, why, now=now)
                gone += 1
            did["terminated"] += gone
            if gone:
                db.q("UPDATE $r SET last_in_at = $t", r=R("scale_group", g["id"]), t=_iso(now))

    def _for_removal(self, g, units, n, now):
        """Which units go when scaling in: never a protected one; idle before busy; then the oldest (or newest)."""
        workers = {w["name"]: w for w in jobs.workers(self.db, now)}
        free = [u for u in units if not u.get("protected")]
        busy = lambda u: 1 if (workers.get(u["name"]) or {}).get("current") is not None else 0  # noqa: E731
        free.sort(key=lambda u: (busy(u), u.get("launched_at") or "" if g.get("terminate") != "newest" else ""))
        if g.get("terminate") == "newest":
            free.sort(key=lambda u: (busy(u), -(_parse(u.get("launched_at")) or now).timestamp()))
        return free[:n]

    def _launch(self, cfg, g, drv, p, now, why):
        db = self.db
        nid = db.next_id("scale_node")
        name = f"{conf(cfg)['tag']}-{_slug(g.get('name'))}-{nid}"
        price = p.get("price")
        if price is None:
            try:
                price = drv.price(p, g.get("template") or {})
            except Exception:  # noqa: BLE001 - no price: nothing accrues
                price = None
        db.q(
            "CREATE $r CONTENT $d",
            r=R("scale_node", nid),
            d=store.clean(
                {
                    "group": g["id"],
                    "provider": p["id"],
                    "driver": drv.id,
                    "name": name,
                    "state": "launching",
                    "price": price,
                    "launched_at": _iso(now),
                    "accrued_at": _iso(now),
                    "by": "rule" if g.get("rules") else "desired",
                    "why": why,
                    "protected": False,
                }
            ),
        )
        refs = [_ref("scale_node", nid), _ref("scale_group", g["id"]), _ref("scale_provider", p["id"])]
        try:
            with activity.call(f"scaling.{drv.id}.launch", cfg, refs=refs):
                got = drv.launch(p, g.get("template") or {}, name, bootstrap(db, cfg, g, name)) or {}
        except Exception as e:  # noqa: BLE001 - the unit failed; three in a row pause the group
            err = f"{type(e).__name__}: {e}"[:300] if not isinstance(e, drivers.DriverError) else str(e)[:300]
            db.q("UPDATE $r SET state = 'failed', error = $e, ended_at = $t", r=R("scale_node", nid), e=err, t=_iso(now))
            fails = (g.get("failures") or 0) + 1
            db.q("UPDATE $r SET failures = $f, error = $e", r=R("scale_group", g["id"]), f=fails, e=err)
            activity.record("run", "scaling.fail", refs, cfg, db, ok=False, error=type(e).__name__, detail={"why": err, "unit": name})
            self.log(f"scaling: couldn't launch {name}: {err}")
            if fails >= MAX_FAILURES:
                pause_group(db, cfg, g["id"], True, f"{fails} launches failed in a row; the last: {err}")
            return False
        db.q(
            "UPDATE $r MERGE $d",
            r=R("scale_node", nid),
            d=store.clean(
                {
                    "external_id": str(got.get("id")) if got.get("id") is not None else None,
                    "address": got.get("address"),
                    "endpoint": got.get("endpoint"),
                    "price": got.get("price", price),
                }
            ),
        )
        db.q("UPDATE $r SET failures = 0, error = NONE", r=R("scale_group", g["id"]))
        activity.record(
            "run", "scaling.launch", refs, cfg, db, detail={"why": why, "unit": name, "price_per_hour": got.get("price", price)}
        )
        self.log(f"scaling: launched {name} on {p.get('name')} ({why})")
        return True

    def _reconcile(self, cfg, g, drv, p, n, now, did):
        """One unit against its provider and Lens: True while it's still alive."""
        db = self.db
        state = n.get("state")
        if state == "terminating":
            _terminate(db, cfg, drv, p, n, n.get("why") or "terminating", now=now)
            did["terminated"] += 1
            return False
        try:
            with activity.call(f"scaling.{drv.id}.describe", cfg, refs=[_ref("scale_node", n["id"])]):
                d = drv.describe(p, n) or {}
        except Exception as e:  # noqa: BLE001 - the provider didn't answer: judged again next tick
            db.q("UPDATE $r SET error = $e", r=R("scale_node", n["id"]), e=f"{type(e).__name__}: {e}"[:300])
            return True
        if d.get("state") == "gone":
            why = "stopped at the provider" if state != "launching" else "never came up at the provider"
            _terminate(db, cfg, drv, p, {**n, "external_id": None}, why, state="failed" if state == "launching" else "terminated", now=now)
            did["terminated"] += 1
            return False
        sets = {k: d[k] for k in ("address", "endpoint") if d.get(k) and d.get(k) != n.get(k)}
        worker = db.one("SELECT heartbeat_at, current FROM $w", w=R("worker", n["name"])) if drv.kind == "node" else None
        beat = _age((worker or {}).get("heartbeat_at"), now) if worker else None
        if state == "launching":
            joined = (beat is not None and beat <= WORKER_ALIVE_SECONDS) if drv.kind == "node" else d.get("state") == "running"
            if joined:
                sets |= {"state": "running", "ready_at": _iso(now), "error": None}
                activity.record(
                    "run", "scaling.join", [_ref("scale_node", n["id"]), _ref("scale_group", g["id"])], cfg, db, detail={"unit": n["name"]}
                )
                self.log(f"scaling: {n['name']} joined")
            elif (_age(n.get("launched_at"), now) or 0) > conf(cfg)["join_minutes"] * 60:
                _terminate(db, cfg, drv, p, n, f"didn't join within {conf(cfg)['join_minutes']} minutes", state="failed", now=now)
                fails = (g.get("failures") or 0) + 1
                db.q("UPDATE $r SET failures = $f", r=R("scale_group", g["id"]), f=fails)
                if fails >= MAX_FAILURES:
                    pause_group(db, cfg, g["id"], True, f"{fails} units in a row failed to join")
                did["terminated"] += 1
                return False
        elif state == "running" and drv.kind == "node" and beat is not None and beat > WORKER_LOST_SECONDS:
            _terminate(db, cfg, drv, p, n, f"its worker has been silent for {int(beat // 60)} minutes", state="failed", now=now)
            did["terminated"] += 1
            return False
        elif state == "draining" and (worker is None or worker.get("current") is None or beat is None or beat > WORKER_ALIVE_SECONDS):
            _terminate(db, cfg, drv, p, n, n.get("why") or "drained", now=now)
            did["terminated"] += 1
            return False
        if sets:
            db.q("UPDATE $r MERGE $d", r=R("scale_node", n["id"]), d=store.clean(sets))
            if sets.get("state") == "running":
                db.q("UPDATE $r SET error = NONE", r=R("scale_node", n["id"]))
            n.update(sets)
        return True

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the scaler alive
                self.log(f"scaling: {type(e).__name__}: {e}")
            stop.wait(max(5, int(conf(self.cfg_fn()).get("tick_seconds") or 30)))
        self.db.q("UPDATE $r SET owner = NONE, running = false WHERE owner = $me", r=ROW, me=self.name)


def start(db, cfg_fn, stop, name, log_=None):
    s = Scaler(db, cfg_fn, name, log_)
    threading.Thread(target=s.loop, args=(stop,), daemon=True, name="scaler").start()
    return s


def state(db):
    """Which process scales, and when it last looked."""
    return db.one("SELECT owner, at, ticked_at, running FROM $r", r=ROW) or {}


def overview(db, cfg, now=None):
    """Everything at a glance: the switch, the providers, the groups with their units, the drivers on offer."""
    now = now or _now()
    gs = [group_view(db, g, now) for g in db.rows(f"SELECT {GROUP_FIELDS} FROM scale_group ORDER BY id")]
    for g in gs:
        g["waiting_for"] = waiting_for(g, cfg)
    return {
        "enabled": enabled(cfg),
        "settings": conf(cfg),
        "scaler": state(db),
        "providers": providers(db),
        "groups": gs,
        "units": nodes(db, alive=True),
        "drivers": driver_specs(),
        "metrics": metrics(db, cfg, None, now),
    }


# ---------- the assistant and MCP ----------
# the assistant's and MCP's tools that change scaling (ops_tools.SCALING_TOOLS, mcp_tools); `scaling` only reads
TOOLS = ("propose_scaling_group", "change_scaling_group", "set_scaling_desired", "pause_scaling_group", "terminate_scaling_unit")


def apply_tool(db, cfg, tool, args, user, via="agent"):
    """Carry out a scaling tool's change, for the assistant (ops_tools.apply) once approved, or at once for what
    agents may do within the approved terms. `via` web is the person's own yes in the web app, which also approves
    a group's terms."""
    by = user.get("email") if isinstance(user, dict) else user
    if tool == "propose_scaling_group":
        return {"group": create_group(db, cfg, args.get("group") or {}, by, via)}
    if tool == "change_scaling_group":
        return {"group": update_group(db, cfg, int(args["group_id"]), args.get("changes") or {}, by, via)}
    if tool == "set_scaling_desired":
        return {"group": set_desired(db, cfg, int(args["group_id"]), int(args["desired"]), by, via)}
    if tool == "pause_scaling_group":
        return {"group": pause_group(db, cfg, int(args["group_id"]), bool(args.get("paused", True)), args.get("why"), by)}
    if tool == "terminate_scaling_unit":
        return {"unit": terminate_node(db, cfg, int(args["unit_id"]), by, args.get("why"))}
    raise ValueError(f"no tool {tool}")


def time_now():
    return time.time()
