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

Parts of the studio built in their own steps are called once they are there (automation_nodes.provided): schedules
and times (timers), webhooks (automation_hooks) and joins other than any (match.join_step). Until then a trigger or
join that needs one is refused as coming soon.
"""

from __future__ import annotations

from . import actions_web, auth, automation_spec, keyring, store, workflows
from .automation_nodes import provided

R = store.R
FIELDS = (
    "record::id(id) AS id, name, description, enabled, enabled_at, spaces, run_as, triggers, join, condition, workflow, "
    "version, policy, limits, created_by, created_at, updated_at, last_run_at, last_status, counters, removed_at"
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
