"""The shapes automations are made of (docs/automations.md, Contract: Triggers, Joins, Policy, Intents, Run context),
with checks that give a ValueError naming the first problem, the way routines are checked.

An automation watches triggers (`check_trigger`): activities from the stream (`event`), the clock (`schedule`, `at`,
`relative` to a time in an activity), its webhook, or a person asking (`manual`). Its join (`check_join`) says how
several triggers make one run. Its If is a match tree over the run context (`check_if`, `context_paths`). Its Then is
a workflow whose action nodes make intents (`check_intent`), each asked about first unless the person's policy for
that node (`check_policy`) says it may run on its own. Nothing is stored here: the automations module stores them.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal, NotRequired, TypedDict

from . import events, match, schedule

ALIAS_RX = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
NODE_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")  # a workflow node's id, as flow.py has it
ACTION_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
KEY_RX = re.compile(r"^[A-Za-z0-9_.:|-]{1,200}$")
DURATION_RX = re.compile(r"^(-?)(\d{1,6})(s|m|h|d)$")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_DURATION = 366 * 86400
MAX_TRIGGERS = 10
ON = ("event", "schedule", "at", "relative", "webhook", "manual")
KEYS = {  # what each kind of trigger takes, next to as and on
    "event": {"kinds", "where", "from_automations"},
    "schedule": {"cron", "timezone"},
    "at": {"at", "timezone"},
    "relative": {"kinds", "where", "from_automations", "field", "offset"},
    "webhook": {"hook"},
    "manual": set(),
}
JOIN_MODES = ("any", "all", "count", "absent", "batch")
JOIN_KEYS = {
    "any": set(),
    "all": {"within", "key"},
    "count": {"of", "at_least", "within", "key"},
    "absent": {"after", "missing", "within", "key"},
    "batch": {"within", "of"},
}
POLICY = ("ask", "auto")
EFFECTS = ("read", "self", "lens", "out")
RISKS = ("routine", "ask")
STATUSES = ("waiting", "scheduled", "done", "failed", "declined", "expired")
CONTEXT = (  # the run context's top-level keys (section Run context), in the order the variable picker lists them
    ("event", "The event", "object", "the activity that started the run (for a join, the last one)"),
    ("events", "Events by trigger", "object", "each trigger's activity by its name (as), such as events.mail"),
    ("trigger", "Trigger", "object", "the trigger that fired: {as, on}"),
    ("automation", "Automation", "object", "{id, name, version, spaces}"),
    ("run", "Run", "object", "{id, dry}"),
    ("namespace", "Namespace", "object", "{id, name}"),
    ("user", "Person", "object", "who the automation acts for: {id, name, email}"),
    ("now", "Now", "string", "when the run started, ISO 8601 in UTC"),
    ("now_local", "Now, local", "string", "when the run started, ISO 8601 in the run's time zone"),
    ("timezone", "Time zone", "string", "the person's time zone"),
    ("vars", "Variables", "object", "the automation's and its namespace's saved variables"),
    ("steps", "Steps", "object", "what each node passed on so far, by node id, such as steps.n3"),
)


# ---------- the shapes ----------
Trigger = TypedDict(  # the functional form, since "as" is a Python keyword
    "Trigger",
    {
        "as": str,
        "on": Literal["event", "schedule", "at", "relative", "webhook", "manual"],
        "kinds": NotRequired[list[str]],
        "where": NotRequired[dict],
        "from_automations": NotRequired[bool],
        "cron": NotRequired[str],
        "timezone": NotRequired[str],
        "at": NotRequired[str],
        "field": NotRequired[str],
        "offset": NotRequired[str],
        "hook": NotRequired["int | None"],
    },
)


class Join(TypedDict):
    mode: Literal["any", "all", "count", "absent", "batch"]
    within: NotRequired[str]
    key: NotRequired[str]
    of: NotRequired[str]
    at_least: NotRequired[int]
    after: NotRequired[str]
    missing: NotRequired[str]


class Intent(TypedDict):
    key: str
    action: str
    params: dict
    effect: Literal["read", "self", "lens", "out"]
    risk: Literal["routine", "ask"]
    untrusted: bool
    spaces: list[int]
    run_as: int | None
    node: str
    automation: int | None
    run: int | None
    when: str | None
    status: Literal["waiting", "scheduled", "done", "failed", "declined", "expired"]


class RunContext(TypedDict):
    event: dict
    events: dict[str, dict]
    trigger: dict
    automation: dict
    run: dict
    namespace: dict | None
    user: dict
    now: str
    now_local: str
    timezone: str
    vars: dict
    steps: dict[str, object]


# ---------- durations ----------
def check_duration(text, back=False, what="the duration"):
    """Seconds in a duration like 30s, 30m, 2h or 7d (at most a year). A minus (-30m) only where an offset may point
    back (`back`)."""
    m = DURATION_RX.match(str(text).strip()) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"{what} is a whole number and s, m, h or d, like 30m")
    if m.group(1) and not back:
        raise ValueError(f"{what} can't point back")
    n = int(m.group(2)) * UNITS[m.group(3)]
    if n > MAX_DURATION:
        raise ValueError(f"{what} is at most 366d")
    return -n if m.group(1) else n


# ---------- triggers ----------
def _name(t, n):
    return f"trigger {t.get('as')}" if isinstance(t.get("as"), str) and t.get("as") else f"trigger {n}"


def check_trigger(t, n=1):
    """A trigger, cleaned (`n` is its place, for naming it in problems before it has a name)."""
    if not isinstance(t, dict):
        raise ValueError(f"trigger {n} is an object with on")
    what = _name(t, n)
    alias = t.get("as")
    if not isinstance(alias, str) or not ALIAS_RX.match(alias):
        raise ValueError(f"{what}: give it a name (as) of lowercase letters, digits and _, like mail")
    on = t.get("on")
    if on not in ON:
        raise ValueError(f"{what}: on is one of {', '.join(ON)}")
    extra = sorted(set(t) - KEYS[on] - {"as", "on"})
    if extra:
        raise ValueError(f"{what}: a trigger on {on} has no {extra[0]}")
    out: dict = {"as": alias, "on": on}
    if on in ("event", "relative"):
        ks = t.get("kinds")
        if not isinstance(ks, list) or not ks or not all(isinstance(k, str) for k in ks):
            raise ValueError(f"{what}: kinds lists the kinds of activity it starts on, like email.received")
        try:
            events.expand(ks)
        except ValueError as e:
            raise ValueError(f"{what}: {e}") from None
        out["kinds"] = list(dict.fromkeys(ks))
        if t.get("where") is not None:
            out["where"] = match.check(t["where"], f"{what}'s where")
        if not isinstance(t.get("from_automations", False), bool):
            raise ValueError(f"{what}: from_automations is true or false")
        out["from_automations"] = bool(t.get("from_automations", False))
    if on in ("schedule", "at"):
        out["timezone"] = t.get("timezone") or "UTC"
        try:
            schedule.zone(out["timezone"])
        except ValueError as e:
            raise ValueError(f"{what}: {e}") from None
    if on == "schedule":
        cron = (t.get("cron") or "").strip() if isinstance(t.get("cron"), str) else ""
        if not cron:
            raise ValueError(f"{what}: give the schedule (cron), like 0 7 * * 1-5")
        try:
            schedule.check(cron, out["timezone"])
        except ValueError as e:
            raise ValueError(f"{what}: {e}") from None
        out["cron"] = cron
    elif on == "at":
        try:
            when = dt.datetime.fromisoformat(str(t.get("at") or "").strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{what}: at is a time like 2026-10-16T07:30 (in its time zone)") from None
        out["at"] = when.isoformat(timespec="seconds")
    elif on == "relative":
        field = t.get("field")
        if not isinstance(field, str) or not events.PATH_RX.match(field):
            raise ValueError(f"{what}: field is the time in the activity it counts from, like object.startTime")
        out["field"] = field
        out["offset"] = t.get("offset") or "0s"
        check_duration(out["offset"], back=True, what=f"{what}'s offset")
    elif on == "webhook":
        hook = t.get("hook")
        if hook is not None and (not isinstance(hook, int) or isinstance(hook, bool)):
            raise ValueError(f"{what}: hook is given when the automation is saved")
        out["hook"] = hook
    return out


def check_triggers(ts):
    """An automation's triggers, cleaned: one to MAX_TRIGGERS, each named once."""
    if not isinstance(ts, list) or not ts:
        raise ValueError("an automation has at least one trigger")
    if len(ts) > MAX_TRIGGERS:
        raise ValueError(f"an automation has at most {MAX_TRIGGERS} triggers")
    out = [check_trigger(t, n) for n, t in enumerate(ts, 1)]
    seen: set = set()
    for t in out:
        if t["as"] in seen:
            raise ValueError(f"two triggers are named {t['as']}")
        seen.add(t["as"])
    return out


# ---------- joins ----------
def check_join(j, aliases=None):
    """How the triggers make a run, cleaned (none: each on its own, mode any). `aliases` are the triggers' names, to
    check the ones the join names."""
    if j is None or j == {}:
        return {"mode": "any"}
    if not isinstance(j, dict):
        raise ValueError("the join is an object with a mode")
    mode = j.get("mode", "any")
    if mode not in JOIN_MODES:
        raise ValueError(f"the join's mode is one of {', '.join(JOIN_MODES)}")
    extra = sorted(set(j) - JOIN_KEYS[mode] - {"mode"})
    if extra:
        raise ValueError(f"a join of mode {mode} has no {extra[0]}")
    out: dict = {"mode": mode}
    if mode == "any":
        return out
    out["within"] = j.get("within")
    if out["within"] is None:
        raise ValueError(f"a join of mode {mode} says how long it waits (within), like 2h")
    if check_duration(out["within"], what="the join's within") == 0:
        raise ValueError("the join's within is more than 0")
    if j.get("key") is not None:
        if not isinstance(j["key"], str) or not events.PATH_RX.match(j["key"]):
            raise ValueError("the join's key is a path in the activities, like object.thread")
        out["key"] = j["key"]
    names = [k for k in ("of", "after", "missing") if k in JOIN_KEYS[mode]]
    for k in names:
        v = j.get(k)
        if v is None and (k != "of" or mode == "count"):
            raise ValueError(f"a join of mode {mode} names a trigger in {k}")
        if v is None:
            continue
        if not isinstance(v, str) or not ALIAS_RX.match(v) or (aliases is not None and v not in aliases):
            raise ValueError(f"the join's {k} is the name of one of the triggers")
        out[k] = v
    if mode == "count":
        n = j.get("at_least")
        if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= 1000:
            raise ValueError("a join of mode count says how many (at_least), 2 to 1000")
        out["at_least"] = n
    if mode == "absent" and out["after"] == out["missing"]:
        raise ValueError("a join of mode absent waits after one trigger for another one (missing)")
    if mode == "all" and aliases is not None and len(aliases) < 2:
        raise ValueError("a join of mode all needs at least two triggers")
    return out


# ---------- If, policy ----------
def check_if(tree):
    """An automation's If, cleaned: a match tree over the run context, whose paths start with one of its keys."""
    if tree is None:
        return None
    out = match.check(tree, "the If")
    keys = {c[0] for c in CONTEXT}
    for p in match.paths(out):
        if p.split(".")[0] not in keys:
            raise ValueError(f"the If tests {p or 'the whole context'}; paths start with {', '.join(sorted(keys))}")
    return out


def check_policy(p, nodes=None, allow_auto=False):
    """{action node id: ask or auto}, cleaned. Only a person in a signed-in session lets a node run on its own
    (`allow_auto`); everywhere else auto is refused. `nodes` are the workflow's node ids, to check the ones named."""
    if p is None:
        return {}
    if not isinstance(p, dict):
        raise ValueError("the policy is {node id: ask or auto}")
    out = {}
    for node, v in p.items():
        if not isinstance(node, str) or not NODE_RX.match(node) or (nodes is not None and node not in nodes):
            raise ValueError(f"the policy names node {node}, which isn't in the workflow")
        if v not in POLICY:
            raise ValueError(f"node {node}'s policy is ask or auto")
        if v == "auto" and not allow_auto:
            raise ValueError(f"only you can let node {node} run without asking, from the automation's page")
        out[node] = v
    return out


# ---------- intents ----------
def check_intent(i):
    """An intent (what an action node means to do), cleaned. Its status defaults to waiting."""
    if not isinstance(i, dict):
        raise ValueError("an intent is an object")
    extra = sorted(set(i) - set(Intent.__annotations__))
    if extra:
        raise ValueError(f"an intent has no {extra[0]}")
    if not isinstance(i.get("key"), str) or not KEY_RX.match(i["key"]):
        raise ValueError("an intent's key is its run and node, like 51-n7")
    if not isinstance(i.get("action"), str) or not ACTION_RX.match(i["action"]):
        raise ValueError("an intent names its action, like notify")
    if not isinstance(i.get("params", {}), dict):
        raise ValueError("an intent's params are an object")
    if i.get("effect") not in EFFECTS:
        raise ValueError(f"an intent's effect is one of {', '.join(EFFECTS)}")
    if i.get("risk") not in RISKS:
        raise ValueError(f"an intent's risk is {' or '.join(RISKS)}")
    if not isinstance(i.get("untrusted", False), bool):
        raise ValueError("an intent's untrusted is true or false")
    spaces = i.get("spaces") or []
    if not isinstance(spaces, list) or not all(isinstance(s, int) and not isinstance(s, bool) for s in spaces):
        raise ValueError("an intent's spaces are namespace ids")
    for k in ("run_as", "automation", "run"):
        v = i.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            raise ValueError(f"an intent's {k} is an id, or null")
    if not isinstance(i.get("node"), str) or not NODE_RX.match(i["node"]):
        raise ValueError("an intent names the node that made it")
    when = i.get("when")
    if when is not None:
        try:
            if not isinstance(when, str):
                raise ValueError
            dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("an intent's when is an ISO 8601 time, or null") from None
    status = i.get("status", "waiting")
    if status not in STATUSES:
        raise ValueError(f"an intent's status is one of {', '.join(STATUSES)}")
    return {
        "key": i["key"],
        "action": i["action"],
        "params": dict(i.get("params") or {}),
        "effect": i["effect"],
        "risk": i["risk"],
        "untrusted": bool(i.get("untrusted", False)),
        "spaces": sorted(set(spaces)),
        "run_as": i.get("run_as"),
        "node": i["node"],
        "automation": i.get("automation"),
        "run": i.get("run"),
        "when": when,
        "status": status,
    }


# ---------- the run context ----------
def context_paths():
    """The run context's top-level keys, for the variable picker: [{path, title, type, description}]."""
    return [{"path": p, "title": t, "type": ty, "description": d} for p, t, ty, d in CONTEXT]
