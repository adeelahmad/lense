"""What an automation does: the nodes of a workflow of scope `event` (docs/automations.md, The event workflow scope).

The workflow's `input` node passes on the run context (automation_spec.CONTEXT: the event, each trigger's event, the
automation, the person it acts for, now, what each node passed on so far in `steps`...). Between the primitives
(flow.py) there are two nodes of its own:

- `action`: one action from the catalog (actions.py), configured `{action, params, delay?, when?}`. When it runs, it
  renders its params against the run context (`{{ event.object.name }}`; a param that is exactly one expression keeps
  its type), works out its effect and risk (`actions.resolve_risk`, with the automation's policy for the node) and
  makes an intent (`automation_spec.check_intent`). A routine intent is carried out there and then, and what it gives
  back passes on and goes in `steps.<node id>`. One that asks first waits for the person's pick, and one with a delay
  (or a time, `when`) waits for its time: such a node passes nothing on, so the nodes after it are skipped and the
  run says why.
- `decide`: asks the decision model which of its options fits, with an output port per option and `unsure`
  (match.check_decide, match.run_decide); `steps.<node id>` gets {port, choice, confidence, sure}.

The leaf rule (`check_graph`, on every path a graph is saved by): an action that reaches outside Lens or other people
(effect `out`, worked out from its params when it is saved) or that waits has nothing wired after it, since it can't
pass anything on while it waits for a yes or its time.

`Intents` collects what a run's action nodes mean to do, and is where the automations module keeps them and hands the
waiting ones on (to approvals and timers). `context` builds the run context.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re

from . import automation_spec, flow, templates

OWN_NODES = ("action", "decide")
CONFIG = {"action": {"action", "params", "delay", "when"}, "decide": {"question", "options", "input", "min_confidence"}}
NEEDS = "an automation's workflow needs an action node, or it does nothing"
NAMES_RX = re.compile(r"\{[{%](.*?)[%}]\}", re.S)
WORD_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
READS = {"event", "events", "steps", "vars"}  # context keys that can hold text from outside Lens


def _templated(v):
    return isinstance(v, str) and ("{{" in v or "{%" in v)


def _strings(value):
    """Every string in a value (dicts and lists walked)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def module(name):
    """A module of the studio built in its own step (timers, variables, approvals, automation_hooks), or None while
    it isn't there yet, so this module calls it once it lands and keeps its baseline until then."""
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"{__package__}.{name}":
            raise
        return None


def provided(name, fn):
    """`fn` of the module `name`, or None while that module (or that function) isn't there."""
    return getattr(module(name), fn, None)


def _timers():
    """The time module's `later` (docs/automations.md, Time), or None."""
    return provided("timers", "later")


def _match_fn(name):
    return provided("match", name)


# ---------- checking ----------
def check_config(db, nid, t, cfg):
    """A node's settings: ValueError naming the first problem."""
    if t == "decide":
        check_decide = _match_fn("check_decide")
        if check_decide is None:
            raise ValueError(f"decide node {nid}: deciding with the model is coming soon; use a condition or switch node")
        check_decide(nid, cfg)
        return
    name = cfg.get("action")
    try:
        spec = actions.get(name) if isinstance(name, str) else None
    except KeyError:
        spec = None
    if spec is None:
        raise ValueError(f"action node {nid}: choose an action (GET /api/v1/automations/actions lists them)")
    try:
        actions.check_params(spec, cfg.get("params"))
    except ValueError as e:
        raise ValueError(f"action node {nid}: {e}") from None
    prompts = set(actions.prompt_params(spec))
    for k, v in (cfg.get("params") or {}).items():
        try:
            if k in prompts:
                variables.check_prompt(db, v)  # its template, or the saved prompt template it names
                continue
            for s in _strings(v):
                if _templated(s):
                    templates.check(s)
        except (templates.TemplateProblem, ValueError) as e:
            raise ValueError(f"action node {nid}: {k}: {e}") from None
    delay, when = cfg.get("delay"), cfg.get("when")
    if delay is not None and when is not None:
        raise ValueError(f"action node {nid}: wait for a delay or until a time (when), not both")
    if delay is not None and automation_spec.check_duration(delay, what=f"action node {nid}'s delay") == 0:
        raise ValueError(f"action node {nid}: a delay is more than 0, like 30m")
    if when is not None:
        if not isinstance(when, str) or not when.strip():
            raise ValueError(f"action node {nid}: when is a time like 2026-10-16T07:30:00Z, or a template giving one")
        if not _templated(when):
            try:
                _iso(when)
            except ValueError:
                raise ValueError(f"action node {nid}: when is a time like 2026-10-16T07:30:00Z, or a template giving one") from None
    if (delay is not None or when is not None) and _timers() is None:
        raise ValueError(f"action node {nid}: waiting before an action is coming soon")


def ports(node):
    """A decide node's output ports: one per option, then unsure. Other nodes have the default ports."""
    if node["type"] != "decide":
        return None
    options = (node.get("config") or {}).get("options")
    names = [str(k) for k in options] if isinstance(options, dict) else []
    return ["in"], [*names, "unsure"], set()


def keeps(node):
    """An action node is what an automation's workflow does (it passes things on, so it isn't terminal)."""
    return node["type"] == "action"


def _waits(c):
    return c.get("delay") is not None or c.get("when") is not None


def effect(c):
    """The effect an action node's params give it (out when it can't be told)."""
    try:
        spec = actions.get(c.get("action"))
    except KeyError:
        return "out"
    try:
        params = actions.check_params(spec, c.get("params"))
    except ValueError:
        params = dict(c.get("params") or {})
    return actions.effect_of(spec, params)


def check_graph(nodes, edges):
    """The leaf rule: an action that reaches outside Lens or other people, or that waits, has nothing after it."""
    after = {e["source"] for e in edges}
    for n in nodes:
        if n["type"] != "action" or n["id"] not in after:
            continue
        c = n["config"]
        if _waits(c):
            why = "waits before it acts"
        elif effect(c) == "out":
            why = "reaches outside Lens or other people, so it asks you first"
        else:
            continue
        raise ValueError(f"action node {n['id']} {why}: it passes nothing on, so connect nothing after it")


# ---------- the run context ----------
def _iso(t):
    d = dt.datetime.fromisoformat(str(t).strip().replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def _z(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def context(db, cfg, automation, run, trigger, event, events, user, now=None):
    """The run context (docs/automations.md, Run context): variables.context. `automation` is {id, name, version,
    spaces} (spaces None: every namespace), `run` {id, dry}, `trigger` {as, on, timezone?}, `user` the account the
    automation acts for."""
    return variables.context(db, cfg, automation, run, trigger, event, events, user, now=now)


def untrusted(ctx):
    """Whether the run read text from outside Lens: an activity it started from marks some of its fields untrusted, a
    node that ran passed on outside text (its output says `untrusted`, like a web page's answer), or a saved variable
    was set from outside text."""
    acts = [ctx.get("event") or {}, *((ctx.get("events") or {}).values())]
    for a in acts:
        for x in a if isinstance(a, list) else [a]:
            if isinstance(x, dict) and ((x.get("lens") or {}).get("untrusted")):
                return True
    if getattr(ctx.get("vars"), "_outside", None):  # a saved variable an earlier run set from outside text
        return True
    return any(isinstance(out, dict) and out.get("untrusted") for out in (ctx.get("steps") or {}).values())


def _reads_outside(params, ctx):
    """Whether rendered params may carry outside text: a template in them names the event, the events or what nodes
    passed on (steps), and the run read outside text."""
    if not untrusted(ctx):
        return False
    for s in _strings(params):
        if _templated(s) and any(set(WORD_RX.findall(inner)) & READS for inner in NAMES_RX.findall(s)):
            return True
    return False


def render(value, ctx, where=""):
    """Params with their templates filled in from the run context (variables.render): a string that is exactly one
    expression keeps its type (a list stays a list), others render as text, sandboxed. ValueError naming `where`."""
    return variables.render(value, ctx, untrusted_paths(ctx), where)


def untrusted_paths(ctx):
    """The run context's paths that hold outside text (variables.outside_paths)."""
    return variables.outside_paths(ctx)


# ---------- running ----------
class Intents:
    """What one run's action nodes mean to do. `user` is the account the automation acts for, `policy` its policy
    ({node: ask or auto}), `automation` and `run` its ids (None in a try), `spaces` its namespaces (None: every one).
    `keep(intent, extra)` keeps each one (the automations module stores it; extra holds the node's path in the graph,
    what it gave back and any error), `hand_off(intent)` passes a waiting or scheduled one on (to approvals or
    timers) and may give back what to keep with it. A dry run keeps nothing and hands nothing on."""

    def __init__(self, db, cfg, user=None, policy=None, automation=None, run=None, spaces=None, dry=False, keep=None, hand_off=None):
        self.db, self.cfg, self.user, self.policy = db, cfg, user or {}, dict(policy or {})
        self.automation, self.run, self.spaces, self.dry = automation, run, spaces, bool(dry)
        self._keep, self._hand_off = keep, hand_off
        self.items: list[dict] = []
        self.failed: dict[str, str] = {}  # node path: why
        self._seen: dict[str, int] = {}

    def key(self, nid):
        """A key for the next intent of node `nid`: <run>-<node>, then -2, -3 for more from the same node (in a loop)."""
        n = self._seen[nid] = self._seen.get(nid, 0) + 1
        base = f"{self.run if self.run is not None else 'try'}-{nid}"
        return base if n == 1 else f"{base}-{n}"

    def add(self, intent, path, result=None, error=None):
        item = {**intent, "path": path, **({"result": result} if result is not None else {}), **({"error": error} if error else {})}
        self.items.append(item)
        if error:
            self.failed[path] = error
        if self._keep is not None and not self.dry:
            self._keep(intent, {"path": path, "result": result, "error": error})
        return item

    def hand_off(self, intent):
        if self._hand_off is None or self.dry:
            return None
        return self._hand_off(intent)

    def counts(self):
        out: dict[str, int] = {}
        for i in self.items:
            out[i["status"]] = out.get(i["status"], 0) + 1
        return out


def run_node(r, f, node, value, vals):
    """What an automation's own nodes do: {output port: value}; nothing for an action that waits."""
    if node["type"] == "decide":
        run_decide = _match_fn("run_decide")
        if run_decide is None:
            raise ValueError(f"{flow._label(node)}: deciding with the model is coming soon")
        out = run_decide(r, f, node, value)
        port, got = next(iter(out.items()), (None, None))
        d = got if isinstance(got, dict) and "sure" in got else {}
        _steps(r)[node["id"]] = {"port": port, "choice": d.get("choice", port), "confidence": d.get("confidence"), "sure": d.get("sure")}
        return out
    return _action(r, f, node)


def _steps(r):
    ctx = r.ctx if isinstance(r.ctx, dict) else {}
    return ctx.setdefault("steps", {})


def _action(r, f, node):
    c, nid, name = node["config"], node["id"], flow._label(node)
    path = f.prefix + nid
    box: Intents = r.extra["intents"]
    ctx = r.ctx if isinstance(r.ctx, dict) else {}
    spec = actions.get(c["action"])
    raw = c.get("params") or {}
    intent = {
        "key": box.key(nid),
        "action": spec.name,
        "params": dict(raw),
        "effect": "out",
        "risk": "ask",
        "untrusted": untrusted(ctx),
        "spaces": sorted(box.spaces)
        if box.spaces is not None
        else [s for s in [((ctx.get("namespace") or {}).get("id"))] if s is not None],
        "run_as": box.user.get("id") if isinstance(box.user.get("id"), int) else None,
        "node": nid,
        "automation": box.automation,
        "run": box.run,
        "when": None,
        "status": "failed",
    }
    try:
        filled = actions.check_params(spec, raw)  # defaults first, so a default that is a template is filled in too
        prompts = actions.prompt_params(spec)
        rendered = {k: v if k in prompts else render(v, ctx, k) for k, v in filled.items()}
        params = actions.check_params(spec, variables.fill_prompts(spec, rendered, ctx, r.cfg, r.db, prompts))
        intent["params"] = params
        intent["untrusted"] = _reads_outside(filled, ctx)
        intent["effect"] = actions.effect_of(spec, params)
        intent["risk"] = actions.resolve_risk(spec, params, {**ctx, "node": nid}, box.policy)
        if c.get("delay") is not None:
            seconds = automation_spec.check_duration(c["delay"])
            intent["when"] = _z(_iso(ctx.get("now") or _z(dt.datetime.now(dt.timezone.utc))) + dt.timedelta(seconds=seconds))
        elif c.get("when") is not None:
            got = render(c["when"], ctx, "when")
            try:
                intent["when"] = _z(_iso(got))
            except (TypeError, ValueError):
                raise ValueError(f"when gave {str(got)[:60]!r}, not a time") from None
        intent = automation_spec.check_intent(intent)
    except (KeyError, ValueError) as e:
        why = str(e)[:500] or type(e).__name__
        r.say(f"{name}: failed: {why}")
        box.add({**intent, "status": "failed"}, path, error=why)
        return {}
    said = actions.describe(spec.name, intent["params"])
    if intent["when"]:
        intent["status"] = "scheduled"
        kept = box.hand_off(intent)
        box.add(intent, path, result=kept)
        r.say(f"{name}: {'would wait' if box.dry else 'waits'} until {intent['when']} to {said[:1].lower()}{said[1:]}")
        r.say(f"stopped at {nid}: waiting until {intent['when']}")
        return {}
    if intent["risk"] == "ask":
        intent["status"] = "waiting"
        kept = box.hand_off(intent)
        box.add(intent, path, result=kept)
        r.say(f"{name}: {'would ask' if box.dry else 'asks'} you first: {said}")
        r.say(f"stopped at {nid}: waiting for your pick")
        return {}
    try:
        out = actions.perform(r.db, r.cfg, intent, box.user, dry=box.dry)
    except Exception as e:  # noqa: BLE001 - recorded on the intent; the rest of the graph goes on
        why = f"{type(e).__name__}: {e}"[:500]
        intent["status"] = "failed"
        box.add(intent, path, error=why)
        r.say(f"{name}: failed: {why}")
        return {}
    intent["status"] = "done"
    box.add(intent, path, result=out)
    r.say(f"{name}: {'would ' + said[:1].lower() + said[1:] if box.dry else said}")
    _steps(r)[nid] = out
    return {"out": out}


def finish(trace, box):
    """The trace with each action that failed marked failed, with why (flow.py saw it pass nothing on)."""
    for path, why in box.failed.items():
        if path in trace:
            trace[path] = {**trace[path], "status": "failed", "error": why}
    return trace


# last, because the actions (variables loads them too) load the assistant's tools, which load workflows, which reads
# this module's nodes above
from . import actions, variables  # noqa: E402
