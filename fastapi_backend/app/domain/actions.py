"""What automations can do (docs/automations.md, Contract: Actions and intents): one catalog of actions, each
declaring its parameters, its output and its effect, which the canvas, the builder and agents all read.

An action's effect says how far it reaches, and so whether it may run without asking (`resolve_risk`):

- `read` looks things up and changes nothing (a tool's own rules ask for any change it makes): routine.
- `self` tells or keeps something for the automation's own person only: routine.
- `lens` changes something inside Lens: routine on the event's own object in the automation's namespaces, else ask.
- `out` sends outside Lens, reaches other people, or deletes: ask, unless the person set that node to run on its own.

When an action node runs, it renders its params and makes an intent (automation_spec.check_intent); `perform` carries
an intent out, or with `dry` says what it would do and changes nothing. One action ships here, `log_line`; the real
ones come with the actions module.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from . import automation_spec as spec_

NAME_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
GROUPS = ("Messages", "Lens", "Assets", "Web", "AI", "Run")
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
TEMPLATE = "{{"
log = logging.getLogger("lens.automations")


@dataclass(frozen=True)
class ActionSpec:
    """One action. `params` is a JSON Schema (type object) for its parameters, `required` the ones it can't do
    without. `effect` is one of read, self, lens, out, or fn(params) giving one (a templated param that decides the
    reach counts as out). `risk`, fn(params, ctx) giving routine or ask, may only make it stricter. `subject`,
    fn(params) giving the `lens:<table>/<id>` a lens action changes, is how a change to the event's own object is
    told apart (default: the `object` param). `perform(db, cfg, intent, user, dry)` carries it out and gives a dict
    (with `dry`, what it would do, changing nothing); `describe(params)` says it in words."""

    name: str
    label: str
    group: str
    params: dict
    perform: Callable[..., dict]
    required: tuple[str, ...] = ()
    effect: str | Callable[[dict], str] = "read"
    risk: Callable[[dict, dict], str] | None = None
    output: dict | None = None
    describe: Callable[[dict], str] | None = None
    subject: Callable[[dict], str | None] | None = None
    description: str = ""

    def info(self):
        return {
            "name": self.name,
            "label": self.label,
            "group": self.group,
            "description": self.description,
            "params": self.params,
            "required": list(self.required),
            "effect": self.effect if isinstance(self.effect, str) else "depends",
            "output": self.output,
        }


_ACTIONS: dict[str, ActionSpec] = {}


def register(spec):
    """Add an action to the catalog (registering a name again replaces it); the spec."""
    if not isinstance(spec, ActionSpec):
        raise ValueError("an action is an ActionSpec")
    if not NAME_RX.match(spec.name or ""):
        raise ValueError(f"an action is named with lowercase letters, digits and _, not {spec.name}")
    if not (spec.label or "").strip():
        raise ValueError(f"action {spec.name}: give it a label")
    if spec.group not in GROUPS:
        raise ValueError(f"action {spec.name}: its group is one of {', '.join(GROUPS)}")
    if not isinstance(spec.params, dict) or spec.params.get("type") != "object":
        raise ValueError(f"action {spec.name}: params is a JSON Schema of type object")
    props = spec.params.get("properties") or {}
    missing = [k for k in spec.required if k not in props]
    if missing:
        raise ValueError(f"action {spec.name}: required param {missing[0]} isn't in its params")
    if isinstance(spec.effect, str) and spec.effect not in spec_.EFFECTS:
        raise ValueError(f"action {spec.name}: its effect is one of {', '.join(spec_.EFFECTS)}, or a function giving one")
    if not callable(spec.perform):
        raise ValueError(f"action {spec.name}: perform is a function")
    _ACTIONS[spec.name] = spec
    return spec


def catalog():
    """Every action, for the canvas palette, the builder and agents."""
    return [s.info() for s in sorted(_ACTIONS.values(), key=lambda s: (GROUPS.index(s.group), s.label))]


def get(name):
    """One action's spec; KeyError when there's none."""
    return _ACTIONS[name]


def _templated(v):
    return isinstance(v, str) and TEMPLATE in v


def check_params(spec, params):
    """An action's params, checked against its schema with defaults filled in: ValueError naming the first problem.
    A string holding {{ }} stands for any type, since it is filled in when the action runs."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(f"{spec.name}: params are an object")
    props = spec.params.get("properties") or {}
    extra = sorted(set(params) - set(props))
    if extra and spec.params.get("additionalProperties") is not True:
        raise ValueError(f"{spec.name} has no param {extra[0]}")
    out = {}
    for k, p in props.items():
        v = params.get(k, p.get("default"))
        if v is None:
            if k in spec.required:
                raise ValueError(f"{spec.name} needs {p.get('title') or k} ({k})")
            continue
        if not _templated(v):
            want = TYPES.get(p.get("type"))
            if want and (not isinstance(v, want) or (isinstance(v, bool) and p.get("type") in ("integer", "number"))):
                raise ValueError(f"{spec.name}: {k} is {'an' if p.get('type')[0] in 'aeiou' else 'a'} {p.get('type')}")
            if "enum" in p and v not in p["enum"]:
                raise ValueError(f"{spec.name}: {k} is one of {', '.join(map(str, p['enum']))}")
            if isinstance(v, str) and p.get("maxLength") and len(v) > p["maxLength"]:
                raise ValueError(f"{spec.name}: {k} is at most {p['maxLength']} characters")
        out[k] = v
    for k in extra:
        out[k] = params[k]
    return out


def effect_of(spec, params):
    """The action's effect for these params; out (the strictest) when its function can't tell."""
    if isinstance(spec.effect, str):
        return spec.effect
    try:
        e = spec.effect(params)
    except Exception:  # noqa: BLE001 - an effect that can't be worked out reaches as far as it could
        return "out"
    return e if e in spec_.EFFECTS else "out"


def _spaces(ctx):
    a = (ctx or {}).get("automation") or {}
    if a.get("spaces") is not None:
        return set(a["spaces"])
    ns = (ctx or {}).get("namespace") or {}
    return {ns["id"]} if ns.get("id") is not None else set()


def resolve_risk(spec, params, ctx, policy=None):
    """routine or ask, from the effect table. `ctx` is the run context (`event`, and `automation.spaces` for the
    automation's namespaces); `policy` is the node's pick (ask or auto), or the automation's whole policy with the node
    in `ctx["node"]`. Only auto lets an out action run without asking; the action's own `risk` may only be stricter."""
    from . import events

    if isinstance(policy, dict):
        policy = policy.get((ctx or {}).get("node"))
    effect = effect_of(spec, params)
    if effect in ("read", "self"):
        risk = "routine"
    elif effect == "lens":
        event = (ctx or {}).get("event") or {}
        own = ((event.get("object") or {}).get("id")) or None
        subject = (spec.subject or (lambda p: p.get("object")))(params)
        risk = "routine" if own and subject == own and set(events.spaces_of(event)) & _spaces(ctx) else "ask"
    else:
        risk = "routine" if policy == "auto" else "ask"
    if risk == "routine" and spec.risk is not None:
        try:
            if spec.risk(params, ctx) != "routine":
                risk = "ask"
        except Exception:  # noqa: BLE001 - when the action can't tell, it asks
            risk = "ask"
    return risk


def perform(db, cfg, intent, user, dry=False):
    """Carry an intent out as `user` (the person the automation acts for), or with `dry` say what it would do and
    change nothing; the action's output. ValueError for an intent or params that don't check."""
    i = spec_.check_intent(intent)
    try:
        spec = get(i["action"])
    except KeyError:
        raise ValueError(f"no action {i['action']}") from None
    i["params"] = check_params(spec, i["params"])
    out = spec.perform(db, cfg, i, user, bool(dry))
    if not isinstance(out, dict):
        raise ValueError(f"{spec.name} gave {type(out).__name__}, not an object")
    return out


def describe(name, params):
    """What an action would do, in words."""
    spec = get(name)
    if spec.describe:
        try:
            return spec.describe(params or {})
        except Exception:  # noqa: BLE001 - fall back to its label
            pass
    return spec.label


# ---------- the reference action ----------
def _log_line(db, cfg, intent, user, dry):
    text = " ".join(str(intent["params"]["text"]).split())[:500]
    if not dry:
        level = logging.WARNING if intent["params"].get("level") == "warning" else logging.INFO
        log.log(level, "automation %s run %s: %s", intent.get("automation"), intent.get("run"), text)
    return {"line": text, "level": intent["params"].get("level", "info")}


register(
    ActionSpec(
        name="log_line",
        label="Write to the run log",
        group="Run",
        description="Puts a line in the run's log; useful to see what a run saw, in tests and dry runs.",
        params={
            "type": "object",
            "properties": {
                "text": {"type": "string", "title": "Line", "maxLength": 2000},
                "level": {"type": "string", "title": "Level", "enum": ["info", "warning"], "default": "info"},
            },
        },
        required=("text",),
        effect="read",
        output={"type": "object", "properties": {"line": {"type": "string"}, "level": {"type": "string"}}},
        perform=_log_line,
        describe=lambda p: f"Write “{p.get('text', '')}” to the run log",
    )
)
