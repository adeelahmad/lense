"""Automations (docs/automations.md, Automations): when something happens (triggers), if a test passes (the If), do
something (the Then, a workflow of scope event, automation_nodes.py), for one person in some namespaces.

An automation is an `automation:<id>` row:

- `name`, `description`, `enabled` (and `enabled_at`, when it was last switched on: it reacts only to what happens
  after that), `spaces` (namespace ids, or None for every one: admins only), `run_as` (the account it acts for: its
  maker, whose permissions it has), `created_by`, `created_at`, `updated_at`.
- `triggers` and `join` (automation_spec.check_triggers, check_join), `if` (a match tree over the run context, kept
  as `condition`), `workflow` (the id of its workflow, scope event) and `version` (None: the published one),
  `policy` ({action node id: ask or auto}: only the person, from a signed-in session, sets auto, with set_policy),
  `limits` ({cooldown, max_per_hour}).
- What its runs left: `last_run_at`, `last_status`, `counters` ({runs, skipped, limited, missed}).
- `removed_at`: deleted (switched off and hidden; restore brings it back).

Parts of the studio built in their own steps are called through automation_nodes.provided: schedules and times
(timers), webhooks (automation_hooks) and joins other than any (match.join_step, whose absent and batch modes wait
on timers). A trigger or join whose part isn't there is refused as coming soon.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import threading
import time

from . import (
    actions_web,
    auth,
    automation_nodes,
    automation_spec,
    budgets,
    events,
    flow,
    graph_history,
    keyring,
    match,
    store,
    telemetry,
    variables,
    workflows,
)
from . import activity as activity_mod

R = store.R


def provided(module, name):
    """automation_nodes.provided, looked up when it's called: automation_nodes imports this module (through actions),
    so its names aren't there yet while either is being imported."""
    return automation_nodes.provided(module, name)


FIELDS = (
    "record::id(id) AS id, name, description, enabled, enabled_at, spaces, run_as, triggers, join, condition, workflow, "
    "version, policy, limits, created_by, created_at, updated_at, last_run_at, last_status, last_run, counters, removed_at"
)
LIMITS = {"cooldown": "0s", "max_per_hour": 60}
MAX_PER_HOUR = 3600
COUNTERS = ("runs", "skipped", "limited", "missed")
KEYS = {"name", "description", "enabled", "spaces", "triggers", "join", "if", "graph", "workflow", "version", "policy", "limits"}
SOON = {  # what a trigger needs that comes with its own module: (module, function, what)
    "schedule": ("timers", "schedule_automation", "schedules"),
    "at": ("timers", "schedule_automation", "triggers at a time"),
    "relative": ("timers", "relative", "triggers relative to a time"),
    "webhook": ("automation_hooks", "token_for", "webhooks"),
}


# ---------- checking ----------
def action_nodes(graph, out=None):
    """The ids of every action node in a graph and the bodies inside it."""
    out = [] if out is None else out
    for n in (graph or {}).get("nodes") or []:
        if n.get("type") == "action":
            out.append(n.get("id"))
        body = (n.get("config") or {}).get("body")
        if isinstance(body, dict):
            action_nodes(body, out)
    return out


def _account(db, uid):
    a = auth.get_account(db, uid) if isinstance(uid, int) else None
    if not a or a.get("disabled") or a.get("removed_at"):
        return None
    return a


def editable(db, account):
    """The namespaces a person can edit (every one for an admin)."""
    if account.get("admin"):
        return set(store.space_names(db))
    roles = auth.roles(db, account)
    return {s for s in roles if auth.allows(roles, s, "editor")}


def _check_spaces(db, spaces, account):
    """The namespaces an automation works in, as its person may have them: ones they can edit, or every one (None)
    for an admin. Not given: every one for an admin, else each namespace they can edit."""
    can = editable(db, account)
    if spaces is None:
        if account.get("admin"):
            return None
        if not can:
            raise ValueError("you can't edit any namespace, so there's nowhere for an automation to work")
        return sorted(can)
    if not isinstance(spaces, list) or not spaces or not all(isinstance(s, int) and not isinstance(s, bool) for s in spaces):
        raise ValueError("spaces is a list of namespace ids, or none for all of them")
    known = set(store.space_names(db))
    for s in spaces:
        if s not in known:
            raise ValueError(f"no namespace {s}")
        if s not in can:
            raise ValueError(f"{account.get('name') or account.get('email')} can't edit namespace {s}, so the automation can't work there")
    return sorted(set(spaces))


def _check_limits(limits):
    if limits is None:
        return dict(LIMITS)
    if not isinstance(limits, dict):
        raise ValueError("limits are {cooldown, max_per_hour}")
    extra = sorted(set(limits) - set(LIMITS))
    if extra:
        raise ValueError(f"limits have no {extra[0]}")
    out = {**LIMITS, **{k: v for k, v in limits.items() if v is not None}}
    automation_spec.check_duration(out["cooldown"], what="the cooldown")
    n = out["max_per_hour"]
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_PER_HOUR:
        raise ValueError(f"max_per_hour is 1 to {MAX_PER_HOUR} runs")
    return out


def _soon(triggers, join):
    """Refuse what needs a part of the studio that isn't here yet."""
    for t in triggers:
        need = SOON.get(t["on"])
        if need and provided(need[0], need[1]) is None:
            raise ValueError(f"trigger {t['as']}: {need[2]} are coming soon; use a trigger on event or manual")
    if join["mode"] != "any" and provided("match", "join_step") is None:
        raise ValueError(f"joins of mode {join['mode']} are coming soon; each trigger starts a run of its own (any)")


def _graph(db, d, row):
    """(the workflow graph to save as a new version, or None; the workflow id; its version; the graph it will run)."""
    if d.get("graph") is not None and d.get("workflow") is not None:
        raise ValueError("give the Then as a graph or as a workflow, not both")
    if d.get("graph") is not None:
        graph = workflows.validate_graph(db, d["graph"], "event")
        return graph, (row or {}).get("workflow"), None, graph
    if d.get("workflow") is not None or "version" in d:
        wid = d.get("workflow", (row or {}).get("workflow"))
        version = d.get("version")
        if not isinstance(wid, int) or isinstance(wid, bool):
            raise ValueError("choose a workflow (scope event) or give the Then as a graph")
        if version is not None and (not isinstance(version, int) or isinstance(version, bool)):
            raise ValueError("version is a version number, or none to follow the published one")
        try:
            w = workflows.get(db, wid, version)
        except KeyError:
            raise ValueError(f"no workflow {wid}" + (f" version {version}" if version else "")) from None
        if w["scope"] != "event":
            raise ValueError(f"{w['name']} isn't an automation's workflow (scope event)")
        return None, wid, version, w["graph"]
    if row is None:
        raise ValueError("say what it does: a graph of action nodes (the Then)")
    return None, row["workflow"], row.get("version"), workflows.get(db, row["workflow"], row.get("version"))["graph"]


def _check(db, d, row=None, account=None):
    """An automation's settings, checked against what it has (`row`, None for a new one) and the account it acts
    for. Returns (the fields to save, a graph to save as a new version of its workflow or None)."""
    extra = sorted(set(d) - KEYS)
    if extra:
        raise ValueError(f"an automation has no {extra[0]}")
    out: dict = {}
    if "name" in d or row is None:
        name = d.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("give the automation a name")
        out["name"] = name.strip()[:80]
    if "description" in d:
        desc = d["description"]
        if desc is not None and not isinstance(desc, str):
            raise ValueError("the description is text")
        out["description"] = (desc or "").strip()[:500] or None
    if "enabled" in d:
        if not isinstance(d["enabled"], bool):
            raise ValueError("enabled is true or false")
        out["enabled"] = d["enabled"]
    if "spaces" in d or row is None:
        out["spaces"] = _check_spaces(db, d.get("spaces"), account)
    triggers = automation_spec.check_triggers(d["triggers"]) if "triggers" in d or row is None else row["triggers"]
    join = automation_spec.check_join(d.get("join"), [t["as"] for t in triggers]) if "join" in d or row is None else row["join"]
    if "triggers" in d or "join" in d or row is None:
        hooks = {t["as"]: t.get("hook") for t in (row or {}).get("triggers") or [] if t["on"] == "webhook"}
        for t in triggers:
            if t["on"] == "webhook":
                t["hook"] = hooks.get(t["as"])  # a hook is given when it is saved, never chosen
        _soon(triggers, join)
        out["triggers"], out["join"] = triggers, join
    if "if" in d:
        out["condition"] = automation_spec.check_if(d["if"])
    graph, wid, version, runs = _graph(db, d, row)
    if graph is None and (wid != (row or {}).get("workflow") or version != (row or {}).get("version")):
        out["workflow"], out["version"] = wid, version
    nodes = action_nodes(runs)
    if "policy" in d or graph is not None or "workflow" in d or "version" in d:
        had = dict((row or {}).get("policy") or {})
        want = (d["policy"] or {}) if "policy" in d else had
        if not isinstance(want, dict):
            raise ValueError("the policy is {node id: ask or auto}")
        want = {k: v for k, v in want.items() if k in nodes or "policy" in d}
        # only the person sets auto (set_policy); a node already on auto may stay so
        changed = {k: v for k, v in want.items() if not (v == "auto" and had.get(k) == "auto" and k in nodes)}
        out["policy"] = {**automation_spec.check_policy(changed, nodes), **{k: v for k, v in want.items() if k not in changed}}
    if "limits" in d or row is None:
        out["limits"] = _check_limits(d.get("limits"))
    return out, graph


# ---------- saving ----------
def _seal(db, cfg, graph, aid, spaces):
    """The graph as kept: credentials in `http_request` headers sealed with a namespace's key, bound to this
    automation and to the address they go to (actions_web.seal_headers). A value holding {{ }} is filled in when it
    runs, so it stays as written."""
    nodes = []
    for n in (graph or {}).get("nodes") or []:
        conf = n.get("config") or {}
        params = conf.get("params") or {}
        headers = params.get("headers")
        if n.get("type") == "action" and conf.get("action") == "http_request" and isinstance(headers, dict):
            fixed = {k: v for k, v in headers.items() if isinstance(v, str) and "{{" not in v}
            if any(actions_web.secret(k) and v and not keyring.is_sealed(v) for k, v in fixed.items()):
                url = params.get("url")
                if not isinstance(url, str) or "{{" in url:
                    raise ValueError(f"{n.get('id')}: a header with a credential is sealed for one address; give the url as written")
                sid = sorted(spaces)[0] if spaces else min(store.space_names(db))
                try:
                    sealed = actions_web.seal_headers(db, cfg, sid, aid, url, fixed)
                except keyring.Locked:
                    raise ValueError("the namespace's vault is locked; unlock it to save credentials") from None
                conf = {**conf, "params": {**params, "headers": {k: sealed.get(k, v) for k, v in headers.items()}}}
        if isinstance(conf.get("body"), dict):
            conf = {**conf, "body": _seal(db, cfg, conf["body"], aid, spaces)}
        nodes.append({**n, "config": conf} if n.get("config") is not None else n)
    return {**graph, "nodes": nodes}


def create(db, cfg, d, user):
    """A new automation for `user` (an account {id, email, name, admin}), who it acts for. Returns {id, hooks}: the
    URLs of webhooks it was given, shown this once."""
    account = _account(db, user.get("id")) if isinstance(user, dict) else None
    if not account:
        raise ValueError("an automation acts for a person; sign in")
    fields, graph = _check(db, dict(d), None, account)
    t = store.now()
    aid = db.next_id("automation")
    if graph is not None:
        graph = _seal(db, cfg, graph, aid, fields.get("spaces"))
        fields["workflow"] = workflows.create(db, fields["name"], graph, fields.get("description"), account["email"], scope="event")
        fields["version"] = None
    on = fields.get("enabled", True)
    row = {
        **fields,
        "enabled": on,
        "enabled_at": t if on else None,
        "run_as": account["id"],
        "policy": fields.get("policy") or {},
        "counters": dict.fromkeys(COUNTERS, 0),
        "created_by": account["email"],
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("automation", aid), d=store.clean(row))
    return {"id": aid, "hooks": _arm(db, cfg, aid)}


def update(db, cfg, aid, d, by=None):
    """Change an automation (only what `d` names). A new graph is saved as a new version of its workflow, which it
    then follows. Returns {id, hooks} like create."""
    row = _row(db, aid)
    account = _account(db, row.get("run_as"))
    if not account:
        raise ValueError("the person this automation acts for has no account any more; make it again as yourself")
    fields, graph = _check(db, dict(d), row, account)
    if "triggers" in fields:
        _revoke(db, _hooks(row["triggers"]) - _hooks(fields["triggers"]))
    if graph is not None:
        graph = _seal(db, cfg, graph, aid, fields["spaces"] if "spaces" in fields else row.get("spaces"))
        workflows.save_version(db, row["workflow"], graph, user=by, publish=True)
        fields["version"] = None
    if "name" in fields and fields["name"] != row["name"]:
        workflows.rename(db, row["workflow"], fields["name"])
    t = store.now()
    if fields.get("enabled") and not row.get("enabled"):
        fields["enabled_at"] = t
    _set(db, aid, {**fields, "updated_at": t})
    return {"id": aid, "hooks": _arm(db, cfg, aid)}


def _hooks(triggers):
    return {t["hook"] for t in triggers or [] if t.get("on") == "webhook" and t.get("hook") is not None}


def _revoke(db, hooks):
    """Delete these webhooks: their addresses stop working at once."""
    revoke = provided("automation_hooks", "revoke")
    if revoke is not None:
        for h in sorted(hooks):
            revoke(db, h)


def _set(db, aid, fields):
    """Replace these fields (None removes one); objects are replaced whole, not merged."""
    if not fields:
        return
    sets = ", ".join(f"{k} = $v{i}" if v is not None else f"{k} = NONE" for i, (k, v) in enumerate(fields.items()))
    db.q(f"UPDATE $r SET {sets}", r=R("automation", int(aid)), **{f"v{i}": v for i, v in enumerate(fields.values()) if v is not None})


def enable(db, cfg, aid, on=True, by=None):
    """Switch it on (it reacts to what happens from now on) or off (it keeps everything)."""
    row = _row(db, aid)
    if on and not _account(db, row.get("run_as")):
        raise ValueError("the person this automation acts for has no account any more; make it again as yourself")
    if bool(on) != bool(row.get("enabled")):
        t = store.now()
        _set(db, aid, {"enabled": bool(on), "updated_at": t, **({"enabled_at": t} if on else {})})
    _arm(db, cfg, aid)


def remove(db, cfg, aid, by=None):
    """Delete it: it is switched off and hidden, and restore brings it back."""
    _row(db, aid)
    t = store.now()
    _set(db, aid, {"enabled": False, "removed_at": t, "updated_at": t})
    _arm(db, cfg, aid)


def restore(db, cfg, aid, by=None):
    """Bring a deleted automation back, switched off."""
    row = _row(db, aid, removed=True)
    if not row.get("removed_at"):
        return
    _set(db, aid, {"removed_at": None, "updated_at": store.now()})


def set_policy(db, aid, node, value, by=None):
    """Whether an action node asks first (ask) or runs on its own (auto). The one place auto is set: called for a
    person in a signed-in session (the automation's page, the approvals inbox's Always)."""
    row = _row(db, aid)
    nodes = action_nodes(workflows.get(db, row["workflow"], row.get("version"))["graph"])
    got = automation_spec.check_policy({node: value}, nodes, allow_auto=True)
    db.q("UPDATE $r SET policy = $p, updated_at = $t", r=R("automation", aid), p={**(row.get("policy") or {}), **got}, t=store.now())
    return {**(row.get("policy") or {}), **got}


def _arm(db, cfg, aid):
    """After a save, a switch or a delete: set its schedules' timers (or cancel them when it is off), and give each
    webhook trigger without one its hook. Returns the new hooks' addresses and signing secrets, shown once."""
    row = _row(db, aid, removed=True)
    urls = []
    on = bool(row.get("enabled")) and not row.get("removed_at")
    token_for = provided("automation_hooks", "token_for")
    if token_for is not None and not row.get("removed_at"):
        triggers, changed = [dict(t) for t in row["triggers"]], False
        for t in triggers:
            if t["on"] == "webhook" and t.get("hook") is None:
                got = token_for(db, cfg, aid, t["as"])
                t["hook"], changed = got["hook"], True
                urls.append({"as": t["as"], "url": got["url"], "secret": got["secret"]})
        if changed:
            db.q("UPDATE $r SET triggers = $t", r=R("automation", aid), t=triggers)
            row["triggers"] = triggers
    if on:
        arm = provided("timers", "schedule_automation")
        if arm is not None:
            arm(db, view(db, row))
    else:
        cancel = provided("timers", "cancel_for")
        if cancel is not None:
            cancel(db, aid)
    return urls


# ---------- reading ----------
def _row(db, aid, removed=False):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("automation", int(aid)))
    if not row or (row.get("removed_at") and not removed):
        raise KeyError(aid)
    return row


def view(db, row, names=None):
    names = store.space_names(db) if names is None else names
    out = {k: v for k, v in row.items() if k != "condition"}
    return {
        **out,
        "if": row.get("condition"),
        "enabled": bool(row.get("enabled")),
        "policy": row.get("policy") or {},
        "limits": {**LIMITS, **(row.get("limits") or {})},
        "counters": {**dict.fromkeys(COUNTERS, 0), **(row.get("counters") or {})},
        "namespace_names": None if row.get("spaces") is None else [names.get(s) for s in row["spaces"]],
    }


def get(db, aid, removed=False):
    """One automation, with the graph it runs (KeyError when there's none, or it was deleted)."""
    row = view(db, _row(db, aid, removed))
    try:
        w = workflows.get(db, row["workflow"], row.get("version"))
        row["graph"], row["runs_version"] = w["graph"], w["version"]
    except KeyError:
        row["graph"], row["runs_version"] = None, None
    return row


def list_automations(db, spaces=None, removed=False):
    """Every automation (deleted ones only with `removed`), or those that work only in namespaces from `spaces`."""
    names = store.space_names(db)
    out = []
    for row in db.rows(f"SELECT {FIELDS} FROM automation ORDER BY id"):
        if bool(row.get("removed_at")) != bool(removed):
            continue
        if spaces is not None and (row.get("spaces") is None or not set(row["spaces"]) <= set(spaces)):
            continue
        out.append(view(db, row, names))
    return out


def manages(row, editable_spaces, admin=False):
    """Whether someone who can edit `editable_spaces` may see and change this automation: an admin may change any,
    anyone else those that work only in namespaces they can edit."""
    if admin:
        return True
    return row.get("spaces") is not None and set(row["spaces"]) <= set(editable_spaces)


# ---------- running ----------
CONSUMER = "automations"  # the stream follower's name (events.follow)
STREAM = ("event", "relative", "webhook")  # triggers the stream feeds
DEFAULTS = {"enabled": True, "max_depth": 3, "max_runs_per_minute": 60, "keep_runs_days": 30}
LOG_MAX = 500
KEEP_LAST = 20  # runs each automation keeps however old they are
REMOVED_DAYS = 30  # a deleted automation is kept this long, to restore, then removed with its runs
SWEEP_SECONDS = 3600
FINISHED = ("done", "partly", "failed", "waiting")
RUN_FIELDS = (
    "record::id(id) AS id, automation, name, version, trigger, trigger_on, event, event_ids, status, by, started_at, finished_at, "
    "counts, cost_usd, tokens, cost_estimate, error, why, hold, cause_depth, space, dry"
)
INTENT_FIELDS = "key, action, params, effect, risk, untrusted, spaces, run_as, node, automation, run, when, status, path, result, error, created_at, updated_at"
_swept = {"at": 0.0}


def section(cfg):
    """Settings → Automations (store.DEFAULTS["automations"])."""
    return {**DEFAULTS, **((cfg or {}).get("automations") or {})}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def refs(row):
    """What an automation's runs count for, for its budgets: it, its workflow and its namespaces."""
    out = [f"automation:{row['id']}"] if row.get("id") is not None else []
    if row.get("workflow") is not None:
        out.append(f"workflow:{int(row['workflow'])}")
    return out + [f"space:{s}" for s in row.get("spaces") or []]


def _lost(db, row, account):
    """Why the automation's person may no longer act where it works, or None."""
    if not account:
        return "the person it acts for has no account any more"
    who = account.get("name") or account.get("email")
    if row.get("spaces") is None:
        return None if account.get("admin") else f"{who} is no longer an admin, and it works in every namespace"
    can = editable(db, account)
    gone = [s for s in row["spaces"] if s not in can]
    if gone:
        return f"{who} can no longer edit namespace {store.space_names(db).get(gone[0]) or gone[0]}"
    return None


def _count(db, key, now):
    """One more in a counter row (made at its first count); the count."""
    got = db.one("UPSERT $r SET n += 1, at = $t RETURN AFTER", r=R("automation_limit", key), t=_iso(now))
    return int((got or {}).get("n") or 0)


def _limited(db, cfg, row, now):
    """Why this run would go over a limit (and so doesn't happen), or None: the automation's cooldown, its runs this
    hour, then every automation's runs this minute."""
    lim = {**LIMITS, **(row.get("limits") or {})}
    cool = automation_spec.check_duration(lim["cooldown"], what="the cooldown")
    if cool:
        got = db.q(
            "UPDATE $r SET fired_at = $t WHERE fired_at = NONE OR fired_at <= $c RETURN AFTER",
            r=R("automation", row["id"]),
            t=_iso(now),
            c=_iso(now - dt.timedelta(seconds=cool)),
        )
        if not got:
            return f"it waits {lim['cooldown']} between runs"
    if _count(db, f"a{row['id']}-{now:%Y%m%d%H}", now) > int(lim["max_per_hour"]):
        return f"it ran {lim['max_per_hour']} times this hour, the most it may"
    most = int(section(cfg)["max_runs_per_minute"])
    if _count(db, f"all-{now:%Y%m%d%H%M}", now) > most:
        return f"automations ran {most} times this minute, the most they may (Settings → Automations)"
    return None


def _over(db, row, why, now):
    """Count a run that went over a limit, and say so in the stream at most once an hour per automation."""
    db.q("UPDATE $r SET counters.limited += 1", r=R("automation", row["id"]))
    try:
        events.emit(
            db,
            "automation.limited",
            actor=dict(events.LENS),
            obj={"type": "Application", "id": f"lens:automation/{row['id']}", "name": row["name"], "why": why},
            space=(row.get("spaces") or [None])[0],
            summary=f"{row['name']} is over its limit: {why}",
            key=f"limited-{row['id']}-{now:%Y%m%d%H}",
        )
    except Exception:  # noqa: BLE001 - saying so is best effort; the count is kept
        pass


def _held(db, cfg, row, alias, activity, evs, by, now):
    """A run over a budget (budgets.py) is held for the person's pick, or skipped, as a run row that says why; one
    held run waits per automation, and later ones add to it. The run, or None when it may go ahead."""
    try:
        verdict = budgets.check(db, cfg, refs(row), now)
    except Exception:  # noqa: BLE001 - a budget that can't be read doesn't stop the work
        return None
    if verdict["go"]:
        return None
    held = verdict["action"] != "skip"
    status = "held" if held else "skipped"
    waiting = db.values("SELECT VALUE record::id(id) FROM automation_run WHERE automation = $a AND status = 'held'", a=row["id"])
    if held and waiting:
        db.q("UPDATE $r SET hold.missed += 1, hold.why = $w", r=R("automation_run", waiting[0]), w=verdict["why"])
        return {"run": waiting[0], "status": "held", "why": verdict["why"]}
    run_id = db.next_id("automation_run")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("automation_run", run_id),
        d=store.clean(
            {
                "automation": row["id"],
                "name": row["name"],
                "trigger": alias,
                "event": (activity or {}).get("id"),
                "event_ids": _ids(evs),
                "status": status,
                "by": by,
                "started_at": _iso(now),
                "finished_at": None if held else _iso(now),
                "why": verdict["why"],
                "hold": {
                    "why": verdict["why"],
                    "resource": verdict["resource"],
                    "action": verdict["action"],
                    "missed": 0,
                    "activity": activity,
                    "events": evs,
                },
            }
        ),
    )
    db.q("UPDATE $r SET last_status = $s, last_run = $i", r=R("automation", row["id"]), s=status, i=run_id)
    activity_mod.record(
        "run",
        f"automation.{status}",
        [f"automation:{row['id']}", f"automation_run:{run_id}"],
        cfg,
        db,
        ok=held,
        error=None if held else "OverBudget",
        detail={"budget": verdict["resource"], "trigger": alias},
    )
    return {"run": run_id, "status": status, "why": verdict["why"]}


def _ids(evs):
    out = []
    for v in (evs or {}).values():
        for a in v if isinstance(v, list) else [v]:
            if isinstance(a, dict) and isinstance(a.get("id"), str):
                out.append(a["id"])
    return list(dict.fromkeys(out))


def _keep_intent(db, intent, extra):
    t = store.now()
    row = store.clean(
        {**intent, "path": extra.get("path"), "result": extra.get("result"), "error": extra.get("error"), "created_at": t, "updated_at": t}
    )
    db.q("UPSERT $r CONTENT $d", r=R("automation_intent", intent["key"]), d=row)


def _hand_off(db, cfg, intent, depth=1, event=None):
    """A waiting intent goes to the approvals inbox, a scheduled one to timers (docs/automations.md, Approvals and
    Actions that wait); what to keep with it: its approval, or its timer and time. Without those modules they stay
    waiting and scheduled on the run."""
    if intent["status"] == "scheduled":
        later = provided("timers", "later")
        if later is not None:
            return later(db, intent, depth=depth)
    elif intent["status"] == "waiting":
        ask = provided("approvals", "ask")
        if ask is not None:
            return {"approval": ask(db, cfg, intent, event=event)}
    return None


def _status(counts, error):
    if counts.get("failed") or error:
        return "partly" if counts.get("done") else "failed"
    if counts.get("waiting") or counts.get("scheduled"):
        return "waiting"
    return "done"


def _counts(intents):
    out = dict.fromkeys(("done", "waiting", "scheduled", "failed"), 0)
    for i in intents:
        s = i.get("status")
        if s in out:
            out[s] += 1
    return out


def fire(db, cfg, automation_id, alias, activity, events=None, dry=False, by=None, over_budget=False):
    """Run an automation for one trigger (`alias`) and the activity that fired it (`events`: each trigger's activity,
    by its name, for a join), the one way every trigger starts a run: the stream, the clock, timers, joins, webhooks
    and run now. The run's id, or None when nothing ran (it's off, over a limit, or its If was false)."""
    return run(db, cfg, automation_id, alias, activity, events, dry=dry, by=by, over_budget=over_budget)["run"]


def run(db, cfg, aid, alias, activity, evs=None, dry=False, by=None, over_budget=False, row=None, graph=None, now=None):
    """`fire`, saying what happened: {run, status, why, trace, log, intents, error, steps}. With `dry`, nothing is
    kept, sent or counted, and each action says what it would do. `row` and `graph` run a draft that isn't saved."""
    now = now or _now()
    sec = section(cfg)
    out = {"run": None, "status": None, "why": None, "trace": {}, "log": [], "intents": [], "error": None, "steps": 0}
    if row is None:
        try:
            row = _row(db, aid)
        except KeyError:
            return {**out, "status": "missing", "why": "there's no such automation"}
    if not dry and not sec["enabled"]:
        return {**out, "status": "off", "why": "automations are switched off (Settings → Automations)"}
    trig = next((t for t in row["triggers"] if t["as"] == alias), None) or {"as": alias or "manual", "on": "manual"}
    activity = activity if isinstance(activity, dict) else {}
    evs = dict(evs) if evs else {trig["as"]: activity}
    account = _account(db, row.get("run_as"))
    lost = _lost(db, row, account)
    if lost:
        if dry or row.get("id") is None:
            return {**out, "status": "failed", "why": lost}
        return {**out, **_failed(db, cfg, row, trig["as"], activity, evs, by, now, lost)}
    cause = (activity.get("lens") or {}).get("cause") or {}
    depth = int(cause.get("depth") or 0) + 1
    if graph is None:
        w = workflows.get(db, row["workflow"], row.get("version"))
        graph, version = w["graph"], w["version"]
    else:
        version = None
    run_id = None if dry else db.next_id("automation_run")
    person = auth.public(account)
    auto = {"id": row.get("id"), "name": row["name"], "version": version, "spaces": row.get("spaces")}
    trigger = {"as": trig["as"], "on": trig["on"], "timezone": trig.get("timezone")}
    ctx = automation_nodes.context(db, cfg, auto, {"id": run_id, "dry": dry}, trigger, activity, evs, person)
    env = _env(db, cfg, row, trig["as"], ctx, account, dry)
    # the If first: a run it stops uses up no limit, holds no budget and makes no row
    if row.get("condition") and not match.test(row["condition"], ctx, env):
        if not dry:
            db.q("UPDATE $r SET counters.skipped += 1", r=R("automation", row["id"]))
        return {**out, "status": "skipped", "why": "its If was false"}
    if not dry:
        why = _limited(db, cfg, row, now)
        if why:
            _over(db, row, why, now)
            return {**out, "status": "limited", "why": why}
        if not over_budget:
            held = _held(db, cfg, row, trig["as"], activity, evs, by, now)
            if held:
                return {**out, **held}
    lines: list[str] = []

    def say(msg):
        lines.append(f"{store.now()[11:19]} {msg}")
        del lines[:-LOG_MAX]

    space = (ctx.get("namespace") or {}).get("id")
    if not dry:
        db.q(
            "CREATE $r CONTENT $d",
            r=R("automation_run", run_id),
            d=store.clean(
                {
                    "automation": row["id"],
                    "name": row["name"],
                    "version": version,
                    "trigger": trig["as"],
                    "trigger_on": trig["on"],
                    "event": activity.get("id"),
                    "event_ids": _ids(evs),
                    "cause_depth": depth,
                    "cause": {"automation": row["id"], "run": run_id, "depth": depth},
                    "status": "running",
                    "by": by,
                    "space": space,
                    "started_at": _iso(now),
                }
            ),
        )
    box = automation_nodes.Intents(
        db,
        cfg,
        user=person,
        policy=row.get("policy"),
        automation=row.get("id"),
        run=run_id,
        spaces=row.get("spaces"),
        dry=dry,
        keep=lambda i, x: _keep_intent(db, i, x),
        hand_off=lambda i: _hand_off(db, cfg, i, depth, activity or None),
    )
    who = f"automation:{row['id']}" if row.get("id") is not None else None
    r = flow.Run(
        db,
        cfg,
        workflows.KITS["event"],
        ctx,
        say,
        dry=dry,
        user=who,
        origin={"automation": row.get("id"), "run": run_id},
        intents=box,
        env=env,
    )
    run_refs = [x for x in (who, f"automation_run:{run_id}" if run_id else None) if x]
    error = None
    with contextlib.ExitStack() as stack:
        stack.enter_context(activity_mod.scope(db, *run_refs, cfg=cfg))
        stack.enter_context(
            telemetry.span("automation", {"lens.automation.id": str(row.get("id")), "lens.automation.trigger": trig["as"], "lens.dry": dry})
        )
        if not dry:
            stack.enter_context(events.caused_by({"automation": row["id"], "run": run_id, "depth": depth}))
            stack.enter_context(graph_history.acting(actor=who, via=who, automation=row["id"], automation_run=run_id))
        try:
            flow.run_graph(r, graph)
        except Exception as e:  # noqa: BLE001 - recorded on the run, on the node that failed
            error = str(e)[:500] or type(e).__name__
            say(f"failed: {error}")
    counts = _counts(box.items)
    status = _status(counts, error)
    trace = automation_nodes.finish(r.trace, box)
    got = {**out, "run": run_id, "status": status, "trace": trace, "log": lines, "intents": box.items, "error": error, "steps": r.steps}
    if dry:
        return got
    usd, tokens, rough = activity_mod.run_total(db, f"automation_run:{run_id}")
    t = store.now()
    db.q(
        "UPDATE $r MERGE $d",
        r=R("automation_run", run_id),
        d=store.clean(
            {
                "status": status,
                "finished_at": t,
                "counts": counts,
                "trace": trace,
                "log": lines,
                "error": error,
                "cost_usd": usd,
                "tokens": tokens,
                "cost_estimate": rough or None,
                "steps": r.steps,
            }
        ),
    )
    db.q(
        "UPDATE $r SET last_run_at = $t, last_status = $s, last_run = $i, counters.runs += 1",
        r=R("automation", row["id"]),
        t=t,
        s=status,
        i=run_id,
    )
    activity_mod.record(
        "run",
        f"automation.{status}",
        run_refs,
        cfg,
        db,
        cost_usd=usd,
        tokens_in=tokens,
        ok=status != "failed",
        detail=store.clean({"trigger": trig["as"], "estimate": rough or None, **counts}),
    )
    telemetry.record("lens.automation.runs", 1, {"lens.outcome": status})
    events.wake()
    return got


def _env(db, cfg, row, alias, ctx, account, dry):
    """What a run's If and its graph's conditions test with (match.test's env): the run's clock and time zone, one
    State for `changed` (so the If and a node testing the same thing agree), and its namespaces and person for
    looking back (`seen`)."""
    return {
        "db": db,
        "cfg": cfg,
        "now": ctx.get("now"),
        "timezone": ctx.get("timezone"),
        "spaces": row.get("spaces"),
        "viewer": _viewer(db, account) if account else None,
        "state": match.State(db, row.get("id"), alias, dry=dry or row.get("id") is None),
    }


def _failed(db, cfg, row, alias, activity, evs, by, now, why):
    """A run that can't happen because its person lost access: failed, saying why, and the automation switched off."""
    run_id = db.next_id("automation_run")
    t = _iso(now)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("automation_run", run_id),
        d=store.clean(
            {
                "automation": row["id"],
                "name": row["name"],
                "trigger": alias,
                "event": activity.get("id"),
                "event_ids": _ids(evs),
                "status": "failed",
                "by": by,
                "started_at": t,
                "finished_at": t,
                "error": f"{why}, so it was switched off",
                "counts": _counts([]),
            }
        ),
    )
    _set(db, row["id"], {"enabled": False, "last_run_at": t, "last_status": "failed", "last_run": run_id, "updated_at": t})
    _arm(db, cfg, row["id"])
    activity_mod.record(
        "run", "automation.failed", [f"automation:{row['id']}", f"automation_run:{run_id}"], cfg, db, ok=False, error="NoAccess"
    )
    return {"run": run_id, "status": "failed", "why": why}


def update_intent(db, run_id, key, status, result=None):
    """What became of an intent that waited (an approval decided it, its time came): its status and what it gave
    back. The run's status follows: done, partly or failed once nothing waits."""
    if status not in automation_spec.STATUSES:
        raise ValueError(f"an intent's status is one of {', '.join(automation_spec.STATUSES)}")
    row = db.one("SELECT run, status FROM $r", r=R("automation_intent", str(key)))
    if not row or row.get("run") != int(run_id):
        raise KeyError(key)
    sets = {"status": status, "updated_at": store.now(), **({"result": result} if result is not None else {})}
    db.q("UPDATE $r MERGE $d", r=R("automation_intent", str(key)), d=sets)
    _settle(db, int(run_id))


def _settle(db, run_id):
    run_row = db.one("SELECT automation, status, error FROM $r", r=R("automation_run", run_id))
    if not run_row or run_row.get("status") not in FINISHED:
        return
    counts = _counts(db.rows("SELECT status FROM automation_intent WHERE run = $r", r=run_id))
    status = _status(counts, run_row.get("error"))
    db.q("UPDATE $r SET counts = $c, status = $s", r=R("automation_run", run_id), c=counts, s=status)
    db.q("UPDATE $r SET last_status = $s WHERE last_run = $i", r=R("automation", run_row["automation"]), s=status, i=run_id)


def decide_held(db, cfg, run_id, run_it=True, by=None):
    """The person's pick for a run held over budget: run it now (once, whatever its budgets say) or skip it."""
    row = db.one("SELECT automation, trigger, status, hold FROM $r", r=R("automation_run", int(run_id)))
    if not row:
        raise KeyError(run_id)
    if row["status"] != "held":
        raise ValueError(f"this run isn't waiting on a budget (it's {row['status']})")
    t = store.now()
    db.q(
        "UPDATE $r SET status = $s, finished_at = $t, hold.decided = $d WHERE status = 'held'",
        r=R("automation_run", int(run_id)),
        s="released" if run_it else "skipped",
        t=t,
        d={"run": run_it, "by": by, "at": t},
    )
    if run_it:
        hold = row.get("hold") or {}
        return fire(db, cfg, row["automation"], row.get("trigger"), hold.get("activity"), hold.get("events"), by=by, over_budget=True)
    return None


# ---------- following the stream ----------
def _stream_index(db, rows):
    """{kind: [(automation, trigger)]} for the triggers the stream feeds."""
    index: dict = {}
    for a in rows:
        for t in a["triggers"]:
            if t["on"] in ("event", "relative"):
                try:
                    kinds = events.expand(t.get("kinds") or [])
                except ValueError:
                    continue
            elif t["on"] == "webhook":
                kinds = {"webhook.received"} & {k["name"] for k in events.kinds()}
            else:
                continue
            for k in kinds or ():
                index.setdefault(k, []).append((a, t))
    return index


def _enabled(db):
    return [
        view(db, r)
        for r in db.rows(f"SELECT {FIELDS} FROM automation WHERE enabled = true ORDER BY id")
        if not r.get("removed_at") and any(t["on"] in STREAM for t in r.get("triggers") or [])
    ]


def _viewer(db, account):
    if account.get("admin"):
        return events.viewer(account["id"], None, admin=True)
    return events.viewer(account["id"], set(auth.roles(db, account)))


def _after(t, since):
    try:
        return events._parse(t) >= events._parse(since)
    except (TypeError, ValueError):
        return False


def _where_env(db, a, t):
    """What a trigger's where tests with: the trigger's time zone and its own State for `changed`."""
    return {"timezone": variables.timezone_of(a, t), "state": match.State(db, a["id"], t["as"])}


def _route(db, cfg, a, t, act, who, sec):
    """One activity against one trigger: the guards, a claim so it's handled once, then on to its run (or a join, or
    a timer). How many runs it started."""
    if not a.get("enabled_at") or not _after(act.get("published"), a["enabled_at"]):
        return 0
    actor = act.get("actor") or {}
    if str(actor.get("id") or "").startswith("lens:automation/") and not t.get("from_automations"):
        return 0
    cause = (act.get("lens") or {}).get("cause") or {}
    if int(cause.get("depth") or 0) >= int(sec["max_depth"]):
        return 0
    if a.get("spaces") is not None and not any(s in a["spaces"] for s in events.spaces_of(act)):
        return 0
    if who is None or not events.visible(act, who):
        return 0
    if t["on"] == "webhook" and (act.get("object") or {}).get("hook") != t.get("hook"):
        return 0
    if t.get("where") and not match.test(t["where"], act, _where_env(db, a, t)):
        return 0
    if not events.claim(db, CONSUMER, f"{a['id']}|{t['as']}|{act['id']}"):
        return 0
    if t["on"] == "relative":
        rel = provided("timers", "relative")
        if rel is not None:
            rel(db, a, t["as"], act, t["field"], t.get("offset") or "0s")
        return 0
    if a["join"]["mode"] != "any":
        step = provided("match", "join_step")
        done = step(db, cfg, a, t["as"], act) if step is not None else []
        return sum(1 for c in done if fire(db, cfg, a["id"], c.get("as") or t["as"], c.get("event") or act, c.get("events")) is not None)
    return 1 if fire(db, cfg, a["id"], t["as"], act, {t["as"]: act}) is not None else 0


def dispatch(db, cfg, log=None):
    """One look at the stream for every switched-on automation that a stream trigger feeds: each new activity runs
    each automation it matches, once. With none (or automations off), the follower only moves to now, so nothing
    that happened meanwhile replays when one is switched on. How many runs it started."""
    sec = section(cfg)
    rows = _enabled(db) if sec["enabled"] else []
    if not rows:
        events.idle(db, CONSUMER)
        return 0
    index = _stream_index(db, rows)
    if not index:
        events.idle(db, CONSUMER)
        return 0
    spaces = None if any(a.get("spaces") is None for a in rows) else {s for a in rows for s in a["spaces"]}
    acts, commit = events.follow(db, cfg, CONSUMER, kinds=sorted(index), spaces=spaces)
    viewers: dict = {}
    started = 0
    for act in acts:
        for a, t in index.get(act.get("kind"), []):
            try:
                if a["run_as"] not in viewers:
                    account = _account(db, a["run_as"])
                    viewers[a["run_as"]] = _viewer(db, account) if account else None
                started += _route(db, cfg, a, t, act, viewers[a["run_as"]], sec)
            except Exception as e:  # noqa: BLE001 - one automation failing never stops the others
                if log:
                    log(f"automation {a['id']} ({t['as']}) on {act.get('id')}: {type(e).__name__}: {e}")
    commit()
    return started


def start(db, cfg_fn, stop, log=None):
    """The automations thread, until `stop` is set: a look at the stream every events.poll_seconds, and at once when
    something is emitted or an automation is saved (events.wake); and the tidy, once an hour. Run where the
    notifier and routines run (the API's background work and `lens worker`); claims keep two processes from running
    one activity twice."""

    def loop():
        while not stop.is_set():
            g = events.generation()
            cfg = cfg_fn()
            try:
                dispatch(db, cfg, log)
                tidy(db, cfg)
            except Exception as e:  # noqa: BLE001 - keep following
                if log:
                    log(f"automations: {type(e).__name__}: {e}")
            events.wait(events.poll_seconds(cfg), stop, since=g)

    th = threading.Thread(target=loop, daemon=True, name="automations")
    th.start()
    return th


def tidy(db, cfg, now=None):
    """Runs past automations.keep_runs_days (each automation keeps its last KEEP_LAST whatever their age), automations
    deleted REMOVED_DAYS ago with their runs, and spent limit counters; at most once an hour per process. The
    cut-off for runs, or None when it didn't look."""
    t = time.monotonic()
    if now is None and _swept["at"] and t - _swept["at"] < SWEEP_SECONDS:
        return None
    _swept["at"] = t
    now = now or _now()
    cut = _iso(now - dt.timedelta(days=int(section(cfg)["keep_runs_days"] or 30)))
    gone = _iso(now - dt.timedelta(days=REMOVED_DAYS))
    for a in db.rows("SELECT record::id(id) AS id, removed_at, workflow FROM automation"):
        if a.get("removed_at") and a["removed_at"] <= gone:
            _purge(db, a)
            continue
        keep = [
            r["n"]
            for r in db.rows(
                "SELECT id, record::id(id) AS n FROM automation_run WHERE automation = $a ORDER BY id DESC LIMIT $k", a=a["id"], k=KEEP_LAST
            )
        ]
        old = db.values(
            "SELECT VALUE record::id(id) FROM automation_run WHERE automation = $a AND started_at < $c "
            "AND record::id(id) NOT IN $k AND status != 'held'",
            a=a["id"],
            c=cut,
            k=keep,
        )
        if old:
            db.q("DELETE automation_intent WHERE run IN $o", o=old)
            db.q("DELETE automation_run WHERE id IN $o", o=[R("automation_run", i) for i in old])
    db.q("DELETE automation_limit WHERE at < $c", c=_iso(now - dt.timedelta(hours=2)))
    return cut


def _purge(db, a):
    cancel = provided("timers", "cancel_for")
    if cancel is not None:
        cancel(db, a["id"])
    revoke = provided("automation_hooks", "revoke")
    row = db.one("SELECT triggers FROM $r", r=R("automation", a["id"])) or {}
    for t in row.get("triggers") or []:
        if t.get("on") == "webhook" and t.get("hook") is not None and revoke is not None:
            revoke(db, t["hook"])
    db.q("DELETE automation_intent WHERE automation = $a", a=a["id"])
    db.q("DELETE automation_run WHERE automation = $a", a=a["id"])
    db.q("DELETE $r", r=R("automation", a["id"]))
    wid = a.get("workflow")
    if wid is not None and not db.values("SELECT VALUE id FROM automation WHERE workflow = $w", w=wid):
        db.q("DELETE workflow_version WHERE workflow = $w", w=wid)
        db.q("DELETE $r", r=R("workflow", wid))


# ---------- runs ----------
def runs(db, aid, before=None, limit=20):
    """An automation's runs, newest first: `before` a run id for the page after."""
    p = {"a": int(aid), "n": max(1, min(int(limit), 100))}
    where = "automation = $a"
    if before is not None:
        where += " AND id < $b"
        p["b"] = R("automation_run", int(before))
    return [_run_row(r) for r in db.rows(f"SELECT {RUN_FIELDS} FROM automation_run WHERE {where} ORDER BY id DESC LIMIT $n", **p)]


def _run_row(r):
    """A run as lists show it: a held or skipped run has no counts, trace or log yet."""
    r["counts"] = r.get("counts") or _counts([])
    r["event_ids"] = r.get("event_ids") or []
    return r


def get_run(db, run_id):
    """One run with its trace, log and intents (KeyError when there's none)."""
    row = db.one(f"SELECT {RUN_FIELDS}, trace, log FROM $r", r=R("automation_run", int(run_id)))
    if not row:
        raise KeyError(run_id)
    row = _run_row(row)
    row["trace"], row["log"] = row.get("trace") or {}, row.get("log") or []
    row["intents"] = db.rows(f"SELECT {INTENT_FIELDS} FROM automation_intent WHERE run = $r ORDER BY created_at, key", r=int(run_id))
    return row


def caused(db, activity_id, limit=50):
    """The runs an activity started (it was their event, or one of a join's)."""
    return [
        _run_row(r)
        for r in db.rows(
            f"SELECT {RUN_FIELDS} FROM automation_run WHERE event_ids CONTAINS $e ORDER BY id DESC LIMIT $n",
            e=str(activity_id),
            n=int(limit),
        )
    ]


def upcoming(db, aid, n=10):
    """When it runs next, from its schedules and timers, once timers are built; [] until then."""
    row = _row(db, aid)
    fn = provided("timers", "upcoming")
    return list(fn(db, view(db, row), n)) if fn is not None else []


def sample(kinds):
    """A sample activity of the first kind that has one, to try an automation on."""
    for k in kinds:
        try:
            got = events.kind_info(k)["sample"]
        except KeyError:
            continue
        if got:
            return got
    return None


def test(db, cfg, aid=None, draft=None, activity=None, alias=None, user=None):
    """A dry run of a saved automation (`aid`) or a draft (the body a save takes) on an activity, or its first
    trigger's kind's sample: what its If said, and what each action would do. Keeps, sends and counts nothing."""
    graph = None
    if draft is not None:
        account = _account(db, (user or {}).get("id"))
        if not account:
            raise ValueError("an automation acts for a person; sign in")
        fields, graph = _check(db, dict(draft), None, account)
        if graph is None:
            graph = workflows.get(db, fields["workflow"], fields.get("version"))["graph"]
        row = {"id": None, "run_as": account["id"], "policy": {}, **fields}
    else:
        row = _row(db, aid)
    trig = next((t for t in row["triggers"] if t["as"] == alias), None) if alias else None
    trig = trig or next((t for t in row["triggers"] if t.get("kinds")), None) or row["triggers"][0]
    if activity is None:
        activity = sample(trig.get("kinds") or ["asset.added"])
        if activity is None:
            raise ValueError("there's no sample to try it on; give an activity")
    return run(db, cfg, row.get("id"), trig["as"], activity, dry=True, row=row, graph=graph)


def latest(db, cfg, row, account):
    """The newest activity its triggers listen for that its person may see, to run it now on; None when there's none."""
    kinds = sorted({k for t in row["triggers"] for k in t.get("kinds") or []})
    if not kinds or not account:
        return None
    flt = {"kinds": kinds, **({"spaces": row["spaces"]} if row.get("spaces") is not None else {})}
    got = events.page(db, cfg, _viewer(db, account), flt=flt, limit=1)["items"]
    return got[0] if got else None


def manual(row, account):
    """What a run started by hand sees as its event when no activity starts it."""
    who = account.get("name") or account.get("email")
    return {
        "id": None,
        "kind": None,
        "type": "Create",
        "published": events.stamp(_now()),
        "actor": {"type": "Person", "id": f"lens:account/{account['id']}", "name": who},
        "object": {"type": "Application", "id": f"lens:automation/{row['id']}", "name": row["name"]},
        "summary": f"{who} ran {row['name']} by hand",
        "space": (row.get("spaces") or [None])[0],
        "tag": [],
        "lens": {"source": "manual", "pos": [None, None], "untrusted": [], "cause": None},
    }


def run_now(db, cfg, aid, user, activity=None, alias=None):
    """Run it now for `user` (who must manage it): on an activity, else the newest one its triggers listen for, else
    as started by hand. Limits and budgets apply as to any run. {run, status, why}."""
    row = _row(db, aid)
    account = _account(db, (user or {}).get("id"))
    if not account:
        raise ValueError("sign in to run an automation")
    owner = _account(db, row.get("run_as"))
    if activity is not None and row.get("spaces") is not None and not set(events.spaces_of(activity)) & set(row["spaces"]):
        raise ValueError("that activity isn't in a namespace it works in")
    if activity is not None and owner and not events.visible(activity, _viewer(db, owner)):
        raise ValueError("the person it acts for can't see that activity, so it can't run on it")
    if activity is None:
        activity = latest(db, cfg, row, owner)
    if activity is None:
        trig = next((t for t in row["triggers"] if t["on"] == "manual"), None)
        if trig is None and any(t.get("kinds") for t in row["triggers"]):
            raise ValueError("nothing it listens for has happened yet; give an activity to run it on")
        activity, alias = manual(row, account), (trig or {}).get("as") or alias
    if alias is None:
        kind = activity.get("kind")
        alias = next(
            (t["as"] for t in row["triggers"] if kind and kind in (events.expand(t.get("kinds") or []) or set())),
            row["triggers"][0]["as"],
        )
    got = run(db, cfg, aid, alias, activity, by=account["email"])
    return {"run": got["run"], "status": got["status"], "why": got["why"]}


def extras(db, rows):
    """What lists show next to each automation: its intents waiting for a pick or a time, and what it cost this
    month (an estimate when some calls had no price)."""
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    waiting: dict = {}
    for i in db.rows(
        "SELECT automation, count() AS n FROM automation_intent WHERE automation IN $a AND status IN ['waiting', 'scheduled'] "
        "GROUP BY automation",
        a=ids,
    ):
        waiting[i["automation"]] = i["n"]
    month = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    costs = activity_mod.costs(db, [f"automation:{i}" for i in ids], _iso(month))
    for r in rows:
        c = costs.get(f"automation:{r['id']}") or {}
        r["waiting"] = int(waiting.get(r["id"]) or 0)
        r["cost_month"] = {"cost_usd": c.get("cost_usd") or 0.0, "estimate": bool(c.get("estimate"))}
    return rows
